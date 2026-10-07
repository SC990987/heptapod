"""Add samples to a location config under configs/samples/.

    python -m analysis_pkg.scripts.add_samples -o samples.yaml -t v1 -d /data/ntuples/v1
    python -m analysis_pkg.scripts.add_samples -o samples.yaml -t v1 \\
        -d root://some.host//store/group/me/v1 --name MySignal --year 2018

Every sub-directory of DIRECTORY that holds ROOT files becomes one sample, named after
the sub-directory. With --name, DIRECTORY itself is a single sample.

Local directories are walked directly. ``root://`` directories are listed with the
``xrdfs`` command, which therefore has to be available (and a valid grid proxy, where
the storage asks for one).

The config is rewritten as plain yaml: comments in an existing file are not kept. An
existing group of samples is only replaced when --update is given. A sample name may
be used in one group of a config only, unless every use of it names its group (--tag).

Afterwards: give each simulated sample its cross section in configs/cross_sections.yaml
and make sure the run period (--year) exists in configs/run_periods.yaml.
"""

import argparse
import os
import subprocess
import sys

import yaml

from analysis_pkg import BASE_DIR


def split_xrootd(url):
    """Split ``root://host//path`` into ``("root://host", "/path")``."""
    rest = url[len("root://"):]
    host, _, path = rest.partition("/")
    return "root://" + host, "/" + path.lstrip("/")


def list_root_files(directory):
    """ROOT files below a directory, as paths relative to it, sorted."""
    if directory.startswith("root://"):
        host, path = split_xrootd(directory)
        result = subprocess.run(["xrdfs", host, "ls", "-R", path],
                                capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"xrdfs could not list {directory}: {result.stderr.strip()}")
        prefix = path.rstrip("/") + "/"
        files = [line.strip()[len(prefix):] for line in result.stdout.splitlines()
                 if line.strip().endswith(".root") and line.strip().startswith(prefix)]
    else:
        files = []
        for root, _, names in os.walk(directory):
            for name in names:
                if name.endswith(".root"):
                    files.append(os.path.relpath(os.path.join(root, name), directory))
    return sorted(files)


def group_by_sample(files, single_name=None):
    """Group relative file paths into ``{sample: {"path": subdir, "files": [...]}}``."""
    if single_name:
        return {single_name: {"path": "", "files": list(files)}}
    samples = {}
    for path in files:
        head, _, tail = path.partition("/")
        if not tail:
            raise ValueError(
                f"'{path}' sits directly in the directory: pass --name to treat the "
                "directory as one sample"
            )
        samples.setdefault(head, {"path": head + "/", "files": []})["files"].append(tail)
    return samples


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m analysis_pkg.scripts.add_samples",
        description="Register the ROOT files below a directory as samples.")
    parser.add_argument("-o", "--output-cfg", required=True,
                        help="config under configs/samples/ (or an absolute path), e.g. samples.yaml")
    parser.add_argument("-t", "--tag", required=True, help="name for this group of samples, e.g. v1")
    parser.add_argument("-d", "--directory", required=True,
                        help="local directory or root://host//path holding the files")
    parser.add_argument("--name", help="treat the directory as a single sample with this name")
    parser.add_argument("--rename", action="append", default=[], metavar="OLD=NEW",
                        help="rename a sample (repeatable)")
    parser.add_argument("--data", action="store_true", help="the samples are data")
    parser.add_argument("--year", help="run period of the samples, e.g. 2018")
    parser.add_argument("--skim-factor", type=float, default=None,
                        help="fraction of the original events kept by a skim")
    parser.add_argument("--update", action="store_true", help="replace the group if it already exists")
    args = parser.parse_args(argv)

    cfg_path = args.output_cfg
    if not os.path.isabs(cfg_path):
        cfg_path = os.path.join(BASE_DIR, "configs", "samples", cfg_path)

    directory = args.directory
    if not directory.startswith("root://"):
        # where the files really are: a path through a link stops working when the
        # link is moved or removed
        directory = os.path.realpath(directory)
    files = list_root_files(directory)
    if not files:
        sys.exit(f"no ROOT files found below {directory}")

    samples = group_by_sample(files, args.name)
    renames = dict(item.split("=", 1) for item in args.rename)
    block_samples = {}
    for name, info in samples.items():
        entry = {}
        if info["path"]:
            entry["path"] = info["path"]
        entry["files"] = info["files"]
        if args.data:
            entry["is_data"] = True
        if args.skim_factor is not None:
            entry["skim_factor"] = args.skim_factor
        block_samples[renames.get(name, name)] = entry

    locations = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf8") as handle:
            locations = yaml.safe_load(handle) or {}
    if args.tag in locations and not args.update:
        sys.exit(f"'{args.tag}' already exists in {cfg_path}: pass --update to replace it")

    block = {"path": directory.rstrip("/") + "/"}
    if args.year:
        block["year"] = str(args.year)
    block["samples"] = block_samples
    locations[args.tag] = block

    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, "w", encoding="utf8") as handle:
        yaml.safe_dump(locations, handle, sort_keys=False, default_flow_style=False, width=200)

    print(f"wrote {len(block_samples)} sample(s) under '{args.tag}' in {cfg_path}:")
    for name, entry in block_samples.items():
        print(f"  {name}: {len(entry['files'])} file(s)")
    elsewhere = sorted(name for name in block_samples for tag, other in locations.items()
                       if tag != args.tag and name in ((other or {}).get("samples") or {}))
    if elsewhere:
        print(f"note: {elsewhere} are also defined in another group of this config: name the "
              "group (--tag / tag=...) whenever you use them")
    if not args.data:
        print("add their cross sections (pb) to configs/cross_sections.yaml")
    if not args.year:
        print("no --year was given: simulation is only scaled, and data only finds its golden "
              "JSON, with a run period that exists in configs/run_periods.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
