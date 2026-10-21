"""Add the samples of a directory to a location config under configs/samples/.

    python -m analysis_pkg.scripts.add_samples -o signal_4mu_v10.yaml -t llpNanoAOD_v2 \\
        -d root://cmseos.fnal.gov//store/group/lpcmetx/SIDM/ULSignalSamples/2018_v10/BsTo2DpTo4Mu/ \\
        -c "4mu signal samples" --name-format "4Mu_{MBs}GeV_{MDp}GeV_{ctau}mm" --year 2018
    python -m analysis_pkg.scripts.add_samples -o data.yaml -t llpNanoAOD_v2 -c "2018 data" \\
        -d root://cmseos.fnal.gov//store/group/lpcmetx/SIDM/Data/2018data/NanoAOD/DoubleMuon/ \\
        --name DoubleMuon_2018C --data --year 2018

The group is written as cms-sidm/SIDM's add_ntuples.py writes its ntuple configs: the
comment (-c) on the line above it, the group (-t) with the directory as its path, and
one sample per sub-directory holding ROOT files, with the directory its files are in as
the sample's path (``<sample>/LLPnanoAODv2/``) and the bare file names under ``files``.
Files that sit directly in DIRECTORY make one sample named after it; with --name,
DIRECTORY itself is one sample.

Samples are named after their sub-directories, leaving out the part all of them share
(``..._MBs-500_MDp-0p25_ctau-0p4_v3`` among its siblings is ``MBs-500_MDp-0p25_ctau-0p4``;
--keep-names keeps the whole names), or by --name-format: a format whose fields are the
KEY-VALUE pieces of the directory names, so that "2Mu2E_{MBs}GeV_{MDp}GeV_{ctau}mm" names
``..._MBs-500_MDp-0p25_ctau-0p4_v3`` 2Mu2E_500GeV_0p25GeV_0p4mm, as SIDM names its signal
points. ``{dir}`` is the whole directory name and ``{short}`` the shortened one.

Local directories are walked directly. ``root://`` directories are listed with the
``xrdfs`` command, which therefore has to be available (and a valid grid proxy, where
the storage asks for one).

A new group is added at the end of the file, which keeps what is there, comments
included. An existing group is replaced only with --update; the rest of the file stays
as it is. A sample name may be used in one group only, unless every use of it names its
group (tag=...) and, when two files use the same group name, its file (location_cfg=...).

Afterwards: give each simulated sample its cross section in configs/cross_sections.yaml
and make sure the run period (--year) exists in configs/run_periods.yaml.
"""

import argparse
import json
import os
import re
import string
import subprocess
import sys

import yaml

from analysis_pkg import BASE_DIR


def split_xrootd(url):
    """Split ``root://host//path`` into ``("root://host", "/path")``, with repeated
    slashes in the path made one."""
    rest = url[len("root://"):]
    host, _, path = rest.partition("/")
    return "root://" + host, "/" + re.sub("/+", "/", path.lstrip("/"))


def list_root_files(directory):
    """ROOT files below a directory, as paths relative to it, sorted."""
    if directory.startswith("root://"):
        host, path = split_xrootd(directory)
        result = subprocess.run(["xrdfs", host, "ls", "-R", path],
                                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"xrdfs could not list {directory}: {result.stderr.strip()}")
        prefix = path.rstrip("/") + "/"
        listed = (re.sub("/+", "/", line.strip()) for line in result.stdout.splitlines())
        files = [line[len(prefix):] for line in listed
                 if line.endswith(".root") and line.startswith(prefix)]
    else:
        if not os.path.isdir(directory):
            raise RuntimeError(f"{directory} is not a directory")

        def unreadable(error):
            raise RuntimeError(f"cannot list {error.filename}: {error.strerror}")

        files = []
        for root, _, names in os.walk(directory, onerror=unreadable):
            for name in names:
                if name.endswith(".root"):
                    files.append(os.path.relpath(os.path.join(root, name), directory))
    return sorted(files)


def _lifted(prefix, files):
    """``{"path", "files"}`` with the directories all the files share moved into the path."""
    parts = [path.split("/")[:-1] for path in files]
    shared = []
    for level in zip(*parts) if parts else ():
        if len(set(level)) != 1:
            break
        shared.append(level[0])
    cut = sum(len(part) + 1 for part in shared)
    return {"path": prefix + "".join(part + "/" for part in shared),
            "files": [path[cut:] for path in files]}


def group_by_sample(files, single_name=None, default_name=None):
    """Group relative file paths into ``{sample: {"path": dir/, "files": [...]}}``.

    Every sub-directory is a sample named after it. Files that sit directly in the
    directory make one sample named ``default_name``; next to sub-directories they are
    refused. With ``single_name`` the whole directory is one sample. The directories
    every file of a sample is in go into its ``path``.
    """
    if single_name:
        return {single_name: _lifted("", list(files))}
    direct = [path for path in files if "/" not in path]
    nested = [path for path in files if "/" in path]
    if direct and nested:
        raise ValueError(
            f"{len(direct)} file(s) sit directly in the directory, next to sub-directories "
            "of files: pass --name to make the directory one sample, or add the "
            "sub-directories one by one"
        )
    if direct:
        return {default_name or "sample": {"path": "", "files": direct}}
    found = {}
    for path in nested:
        head, _, tail = path.partition("/")
        found.setdefault(head, []).append(tail)
    return {head: _lifted(head + "/", tails) for head, tails in found.items()}


_YAML_WORDS = {"y", "n", "yes", "no", "on", "off", "true", "false", "null", "none"}


def name_problem(name):
    """Why a sample name would cause trouble, or None: names go on command lines and
    into yaml keys, so letters, digits and _ . + -, starting with a letter or digit."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name):
        return "use letters, digits and _ . + -, starting with a letter or digit"
    if name.lower() in _YAML_WORDS:
        return "yaml would read it as true, false or null"
    return None


def valid_name(text):
    """``text`` if it is a good sample name, else one made from it."""
    if name_problem(text) is None:
        return text
    name = re.sub(r"[^A-Za-z0-9_.+-]+", "_", text).strip("_.+-")
    if not name or name_problem(name):
        name = "sample_" + name if name else "sample"
    return name


def shortened_names(names):
    """Each name without what all of them share at the start and at the end, cut at an
    underscore: among its siblings ``CutDecayFalse_SIDM_BsTo2DpTo2Mu2e_MBs-500_MDp-0p25_ctau-0p4_v3``
    becomes ``MBs-500_MDp-0p25_ctau-0p4``. Unchanged when that would leave a name empty
    or make two the same."""
    names = list(names)
    unchanged = {name: name for name in names}
    if len(names) < 2:
        return unchanged
    prefix = os.path.commonprefix(names)
    prefix = prefix[: prefix.rfind("_") + 1]
    suffix = os.path.commonprefix([name[::-1] for name in names])[::-1]
    suffix = suffix[suffix.find("_"):] if "_" in suffix else ""
    short = {name: name[len(prefix): len(name) - len(suffix)] for name in names}
    if any(len(prefix) + len(suffix) >= len(name) for name in names) or \
            len(set(short.values())) != len(names):
        return unchanged
    return short


_FIELD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def name_fields(name):
    """What --name-format can take from a directory name: every ``KEY-VALUE`` piece
    between underscores (``MBs-500`` gives MBs=500), and ``dir``, the whole name."""
    fields = {"dir": name}
    for piece in name.split("_"):
        key, sep, value = piece.partition("-")
        if sep and value and _FIELD.fullmatch(key):
            fields.setdefault(key, value)
    return fields


def formatted_names(names, text, short=None):
    """``{directory name: sample name}`` from --name-format."""
    used = []
    for _, field, spec, conversion in string.Formatter().parse(text):
        if field is None:
            continue
        if spec or conversion or not _FIELD.fullmatch(field):
            raise ValueError(f"--name-format {text!r}: write each field as {{name}}, with "
                             "nothing else between the braces")
        used.append(field)
    if not used:
        raise ValueError(f"--name-format {text!r} has no {{field}}: every sample would get "
                         "the same name")
    named = {}
    for name in names:
        fields = name_fields(name)
        if short is not None:
            fields["short"] = short.get(name, name)
        missing = [field for field in used if field not in fields]
        if missing:
            raise ValueError(f"--name-format {text!r}: {name} has no {', '.join(missing)} "
                             f"(it has {', '.join(sorted(fields))})")
        named[name] = text.format(**fields)
    seen = {}
    for name, new in named.items():
        if new in seen:
            raise ValueError(f"--name-format {text!r} names both {seen[new]} and {name} {new}: "
                             "add the field that tells them apart")
        seen[new] = name
    return named


def natural_key(name):
    """A sort key that puts the numbers in a name in order, ``p`` read as the decimal
    point (0p02 < 0p2 < 2p0 < 10p0), as SIDM lists its mass points."""
    parts = re.split(r"(\d+(?:p\d+)?)", name)
    return [(float(part.replace("p", ".")), part) if index % 2 else (part.lower(), part)
            for index, part in enumerate(parts)]


def productions(files):
    """The directories a sample's files are spread over, the numbered blocks of one
    CRAB output (0000/, 0001/, ...) counted as one."""
    return sorted({re.sub(r"(^|/)0\d{3}$", "", os.path.dirname(path)) for path in files})


_PLAIN = re.compile(r"[A-Za-z0-9_/][A-Za-z0-9_./:+-]*")
_NUMBER = re.compile(r"[-+]?(\d[\d_]*\.?[\d_]*|\.\d+)([eE][-+]?\d+)?")


def yaml_text(text):
    """A string as the config shows it: plain where yaml reads it back as the same
    string, double quoted where it would read something else (2018, true, a: b)."""
    text = str(text)
    if _PLAIN.fullmatch(text) and not _NUMBER.fullmatch(text):
        try:
            if yaml.safe_load(text) == text:
                return text
        except yaml.YAMLError:
            pass
    return json.dumps(text, ensure_ascii=False)


def number_text(value):
    """A number that python and yaml read back exactly (1, 0.5, 1.0e-05)."""
    number = float(value)
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    text = repr(number)
    if "e" in text and "." not in text.split("e")[0]:
        mantissa, exponent = text.split("e")
        text = f"{mantissa}.0e{exponent}"
    return text


def render_group(tag, path, samples, comment=None, year=None):
    """One group, as SIDM's ntuple configs hold it: the comment, the tag, its path, and
    per sample its files, then is_data, path, skim_factor and year."""
    out = [f"# {comment}"] if comment else []
    out += [f"{yaml_text(tag)}:", f"  path: {yaml_text(path or '')}"]
    if year:
        out.append(f"  year: {json.dumps(str(year), ensure_ascii=False)}")
    out.append("  samples:")
    for name, entry in samples.items():
        out.append(f"    {yaml_text(name)}:")
        out.append("      files:")
        out += [f"      - {yaml_text(file)}" for file in entry["files"]]
        if entry.get("is_data"):
            out.append("      is_data: true")
        if entry.get("path"):
            out.append(f"      path: {yaml_text(entry['path'])}")
        if entry.get("skim_factor") is not None:
            out.append(f"      skim_factor: {number_text(entry['skim_factor'])}")
        if entry.get("year"):
            out.append(f"      year: {json.dumps(str(entry['year']), ensure_ascii=False)}")
    return out


def _as_entry(cfg):
    """A sample of a loaded config as render_group takes it."""
    cfg = dict(cfg or {})
    files = cfg.get("files")
    cfg["files"] = [files] if isinstance(files, str) else list(files or [])
    return cfg


def _blocks(lines):
    """``{tag: (first line, end line)}`` of the groups of a config's text, each block
    with the comment lines right above its tag. None if the text is not laid out one
    top-level key per line."""
    starts = []
    for index, line in enumerate(lines):
        if not line.strip() or line[0] in " \t#-":
            continue
        try:
            parsed = yaml.safe_load(line)
        except yaml.YAMLError:
            return None
        if not isinstance(parsed, dict) or len(parsed) != 1 or list(parsed.values()) != [None]:
            return None
        first = index
        while first > 0 and lines[first - 1].startswith("#"):
            first -= 1
        starts.append((str(next(iter(parsed))), first))
    blocks = {}
    for number, (tag, first) in enumerate(starts):
        end = starts[number + 1][1] if number + 1 < len(starts) else len(lines)
        while end > first and not lines[end - 1].strip():
            end -= 1
        blocks[tag] = (first, end)
    return blocks


def _names_elsewhere(cfg_path):
    """``{sample: config file}`` of the samples in the other configs under configs/samples/."""
    directory = os.path.dirname(cfg_path)
    found = {}
    for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
        path = os.path.join(directory, name)
        if not name.endswith((".yaml", ".yml")) or os.path.abspath(path) == os.path.abspath(cfg_path):
            continue
        try:
            with open(path, encoding="utf8") as handle:
                content = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError):
            continue
        for block in content.values() if isinstance(content, dict) else []:
            for sample in ((block or {}).get("samples") or {}) if isinstance(block, dict) else {}:
                found.setdefault(str(sample), name)
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m analysis_pkg.scripts.add_samples",
        description="Register the ROOT files below a directory as samples, laid out as "
                    "cms-sidm/SIDM's ntuple configs.")
    parser.add_argument("-o", "--output-cfg", required=True,
                        help="config under configs/samples/ (or an absolute path), e.g. "
                             "signal_2mu2e_v10.yaml")
    parser.add_argument("-t", "--tag", required=True,
                        help="name of this group of samples, e.g. llpNanoAOD_v2")
    parser.add_argument("-d", "--directory", required=True,
                        help="local directory or root://host//path holding the files")
    parser.add_argument("-c", "--comment",
                        help="one line describing the samples, written above the group")
    parser.add_argument("--name", help="treat the directory as a single sample with this name")
    parser.add_argument("--name-format",
                        help="name the samples from the KEY-VALUE pieces of their directory "
                             "names, e.g. '2Mu2E_{MBs}GeV_{MDp}GeV_{ctau}mm'")
    parser.add_argument("--rename", action="append", default=[], metavar="OLD=NEW",
                        help="rename a sample (repeatable)")
    parser.add_argument("--keep-names", action="store_true",
                        help="name the samples after their whole directory names, without "
                             "leaving out what all of them share")
    parser.add_argument("--data", action="store_true", help="the samples are data")
    parser.add_argument("--year", help="run period of the samples, e.g. 2018")
    parser.add_argument("--skim-factor", type=float, default=None,
                        help="fraction of the original events kept by a skim")
    parser.add_argument("--update", action="store_true", help="replace the group if it already exists")
    args = parser.parse_args(argv)
    if args.name and args.name_format:
        sys.exit("give --name (the directory is one sample) or --name-format, not both")
    if args.comment and "\n" in args.comment:
        sys.exit("--comment must be one line")

    cfg_path = args.output_cfg
    if not os.path.isabs(cfg_path):
        cfg_path = os.path.join(BASE_DIR, "configs", "samples", cfg_path)

    directory = args.directory
    if directory.startswith("root://"):
        host, path = split_xrootd(directory)
        directory = f"{host}/{path.rstrip('/')}/"
    else:
        # where the files really are: a path through a link stops working when the
        # link is moved or removed
        directory = os.path.realpath(directory)
    try:
        files = list_root_files(directory)
    except (RuntimeError, OSError) as exc:
        sys.exit(str(exc))
    if not files:
        sys.exit(f"no ROOT files found below {directory}")

    try:
        samples = group_by_sample(files, args.name, os.path.basename(directory.rstrip("/")))
    except ValueError as exc:
        sys.exit(f"{directory}: {exc}")

    text = ""
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf8") as handle:
            text = handle.read()
    locations = yaml.safe_load(text) or {} if text.strip() else {}
    if not isinstance(locations, dict):
        sys.exit(f"{cfg_path} does not hold groups of samples")
    if args.tag in locations and not args.update:
        sys.exit(f"'{args.tag}' already exists in {cfg_path}: pass --update to replace it")

    # named after their directories: by --name-format, or without what all of them
    # share unless a name that another group uses already would come back
    others = {name for tag, other in locations.items() if tag != args.tag
              for name in (((other or {}).get("samples") or {}) if isinstance(other, dict) else {})}
    elsewhere = _names_elsewhere(cfg_path)
    short = {} if args.name or args.keep_names else shortened_names(samples)
    if args.name_format:
        try:
            naming = formatted_names(list(samples), args.name_format, short)
        except ValueError as exc:
            sys.exit(str(exc))
    else:
        clash = (others | set(elsewhere)) & {valid_name(name) for name in short.values()}
        naming = {} if clash else short
    renames = dict(item.split("=", 1) for item in args.rename)
    for new in renames.values():
        if name_problem(new):
            sys.exit(f"--rename: '{new}' cannot be a sample name: {name_problem(new)}")
    block_samples, changed = {}, []
    for found in sorted(samples, key=natural_key):
        info = samples[found]
        name = valid_name(naming.get(found, found))
        if name != naming.get(found, found):
            changed.append(f"{naming.get(found, found)} -> {name}")
        name = renames.get(name, renames.get(found, name))
        if name in block_samples:
            sys.exit(f"two samples would both be called {name}: give --name-format or --rename")
        entry = {"files": info["files"]}
        if args.data:
            entry["is_data"] = True
        if info["path"]:
            entry["path"] = info["path"]
        if args.skim_factor is not None:
            entry["skim_factor"] = args.skim_factor
        if args.year:
            entry["year"] = str(args.year)
        block_samples[name] = entry

    kind = "data" if args.data else "simulation"
    comment = args.comment or f"{len(block_samples)} {kind} sample(s) found below {directory}"
    lines = text.splitlines()
    blocks = _blocks(lines) if lines else {}
    if args.tag not in locations:
        group = render_group(args.tag, directory.rstrip("/") + "/", block_samples, comment)
        new_lines = lines + (["", ""] if any(line.strip() for line in lines) else []) + group
        while new_lines and not new_lines[0].strip():
            new_lines.pop(0)
    elif blocks is not None and args.tag in blocks:
        first, end = blocks[args.tag]
        kept = []                    # without -c, the comment above the group stays
        while not args.comment and first + len(kept) < end and lines[first + len(kept)].startswith("#"):
            kept.append(lines[first + len(kept)])
        group = kept + render_group(args.tag, directory.rstrip("/") + "/", block_samples,
                                    None if kept else comment)
        new_lines = lines[:first] + group + lines[end:]
    else:
        print(f"note: {cfg_path} is not laid out one group per top-level key: it is written "
              "anew, and its comments are lost")
        new_lines = []
        for tag, block in locations.items():
            if new_lines:
                new_lines += ["", ""]
            if tag == args.tag:
                new_lines += render_group(args.tag, directory.rstrip("/") + "/", block_samples, comment)
            else:
                block = block if isinstance(block, dict) else {}
                new_lines += render_group(tag, block.get("path", ""),
                                          {str(k): _as_entry(v)
                                           for k, v in (block.get("samples") or {}).items()},
                                          year=block.get("year"))
    new_text = "\n".join(new_lines) + "\n"
    written = yaml.safe_load(new_text)
    if not isinstance(written, dict) or list((written.get(args.tag) or {}).get("samples") or {}) != list(block_samples):
        sys.exit(f"could not write the group into {cfg_path}: it is left as it was")

    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, "w", encoding="utf8") as handle:
        handle.write(new_text)

    print(f"wrote {len(block_samples)} sample(s) under '{args.tag}' in {cfg_path}:")
    full, brief = next(((a, b) for a, b in naming.items() if a != b), (None, None))
    if full and not args.name_format:
        print(f"(named after their directories without the part all of them share: '{full}' "
              f"is '{brief}'; --keep-names keeps the whole names)")
    if changed:
        print(f"(names changed to characters a sample name can have: {', '.join(changed)})")
    for name, entry in block_samples.items():
        print(f"  {name}: {len(entry['files'])} file(s)")
    for name, entry in block_samples.items():
        where = productions(entry["files"])
        if len(where) > 1:
            print(f"note: the files of {name} are in {len(where)} directories "
                  f"({', '.join((d or '.') + '/' for d in where)}): if these are two "
                  "productions of the same events, keep one")
    twice = sorted(name for name in block_samples if name in others)
    if twice:
        print(f"note: {twice} are also defined in another group of this config: name the "
              "group (tag=...) whenever you use them")
    twice = sorted(f"{name} ({elsewhere[name]})" for name in block_samples if name in elsewhere)
    if twice:
        print(f"note: {twice} are also defined in other configs: name the file "
              "(location_cfg=...) whenever you use them")
    if not args.data:
        print("add their cross sections (pb) to configs/cross_sections.yaml")
    if not args.year:
        print("no --year was given: simulation is only scaled, and data only finds its golden "
              "JSON, with a run period that exists in configs/run_periods.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
