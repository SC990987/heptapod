"""
# _samples.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Samples from where their files live: a local directory or a root:// directory.

A location becomes one group of the sample config, laid out as cms-sidm/SIDM lays out
its ntuple configs: the directory as the group's ``path``, one sample per
sub-directory holding ROOT files, with the directory its files are in as the sample's
``path`` (``<sample>/LLPnanoAODv2/``, ``<dataset>/<request>/<date>/0000/``), and the
file names under that. Samples are named after their directories, without the part all
of them share (``shortened_names``). The listing and the grouping are the ones the generated
framework's ``scripts/add_samples.py`` uses, so that a group the scaffold writes looks
like one the analyst adds with that script later.

``list_files`` does the I/O (``os.walk``, or the ``xrdfs`` command for root://);
everything else is pure.
"""
from __future__ import annotations

import os
import re
import shutil
import string
import subprocess
from typing import Any, Dict, List, Optional, Tuple

LIST_TIMEOUT = 120      # seconds for listing one root:// directory


class LocationError(ValueError):
    """A sample location cannot be listed or grouped; the message says why."""

    def __init__(self, message: str, suggestion: str = ""):
        super().__init__(message)
        self.suggestion = suggestion


def is_remote(directory: str) -> bool:
    return isinstance(directory, str) and directory.startswith("root://")


def split_xrootd(url: str) -> Tuple[str, str]:
    """Split ``root://host//path`` into ``("root://host", "/path")``, with repeated
    slashes in the path made one (the server lists ``/a/b`` for ``//a//b``)."""
    rest = url[len("root://"):]
    host, _, path = rest.partition("/")
    return "root://" + host, "/" + re.sub("/+", "/", path.lstrip("/"))


def list_files(directory: str, timeout: int = LIST_TIMEOUT) -> List[str]:
    """ROOT files below a directory, as paths relative to it, sorted.

    Local directories are walked (links to sub-directories are not followed, as in
    add_samples.py); root:// directories are listed with ``xrdfs ls -R``.
    """
    if is_remote(directory):
        if shutil.which("xrdfs") is None:
            raise LocationError(
                f"cannot list {directory}: the xrdfs command (the XRootD client) is not "
                "on PATH here",
                "List the files yourself and give them as a sample's 'files', or run "
                "where xrdfs exists (the LPC has it)")
        host, path = split_xrootd(directory)
        env = dict(os.environ)
        env.setdefault("XRD_REQUESTTIMEOUT", str(timeout))
        try:
            result = subprocess.run(["xrdfs", host, "ls", "-R", path], capture_output=True,
                                    text=True, timeout=timeout, env=env,
                                    stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired:
            raise LocationError(f"listing {directory} took longer than {timeout} s",
                                "Give a deeper directory, or fewer locations per call") from None
        if result.returncode != 0:
            message = " ".join((result.stderr or result.stdout or "").split())[:300]
            raise LocationError(
                f"xrdfs could not list {directory}: {message or 'exit code %d' % result.returncode}",
                "Check the url (root://host//path/), and that the storage can be read from "
                "here (some ask for a grid proxy)")
        prefix = path.rstrip("/") + "/"
        listed = (re.sub("/+", "/", line.strip()) for line in result.stdout.splitlines())
        files = [line[len(prefix):] for line in listed
                 if line.endswith(".root") and line.startswith(prefix)]
    else:
        if not os.path.isdir(directory):
            raise LocationError(f"{directory} is not a directory")

        def unreadable(error: OSError):
            raise LocationError(f"cannot list {error.filename}: {error.strerror}",
                                "Check that the directory can be read from here")

        files = []
        for root, _, names in os.walk(directory, onerror=unreadable):
            for name in names:
                if name.endswith(".root"):
                    files.append(os.path.relpath(os.path.join(root, name), directory))
    return sorted(files)


def _lifted(prefix: str, files: List[str]) -> Dict[str, object]:
    """``{"path", "files"}`` with the directories all the files share moved into the
    path, as cms-sidm/SIDM writes a sample: ``path: <sample>/LLPnanoAODv2/`` and the
    bare file names under it."""
    parts = [path.split("/")[:-1] for path in files]
    shared: List[str] = []
    for level in zip(*parts) if parts else ():
        if len(set(level)) != 1:
            break
        shared.append(level[0])
    cut = sum(len(part) + 1 for part in shared)
    return {"path": prefix + "".join(part + "/" for part in shared),
            "files": [path[cut:] for path in files]}


def group_by_sample(files: List[str], single_name: Optional[str] = None,
                    default_name: Optional[str] = None) -> Dict[str, Dict[str, object]]:
    """Group relative file paths into ``{sample: {"path": subdir/, "files": [...]}}``.

    With ``single_name`` the directory is one sample. Otherwise every sub-directory is
    a sample named after it; files that sit directly in the directory make one sample
    named ``default_name``, provided there are no sub-directories next to them. The
    directories every file of a sample is in go into its ``path``.
    """
    if single_name:
        return {single_name: _lifted("", list(files))}
    direct = [path for path in files if "/" not in path]
    nested = [path for path in files if "/" in path]
    if direct and nested:
        raise LocationError(
            f"{len(direct)} file(s) sit directly in the directory, next to sub-directories "
            "of files: it is not clear which samples are meant",
            "Give 'name' to make the whole directory one sample, or point at the "
            "sub-directories one by one")
    if direct:
        return {default_name or "sample": {"path": "", "files": direct}}
    found: Dict[str, List[str]] = {}
    for path in nested:
        head, _, tail = path.partition("/")
        found.setdefault(head, []).append(tail)
    return {head: _lifted(head + "/", tails) for head, tails in found.items()}


def shortened_names(names: List[str]) -> Dict[str, str]:
    """Each name without what all of them share at the start and at the end, cut at an
    underscore, the way cms-sidm/SIDM shortens its sample names for tables and legends:
    among its siblings, ``CutDecayFalse_SIDM_BsTo2DpTo2Mu2e_MBs-500_MDp-0p25_ctau-0p4_v3``
    becomes ``MBs-500_MDp-0p25_ctau-0p4``. With fewer than two names, or when a name
    would be left empty or two would become the same, every name stays as it is."""
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


def name_fields(name: str) -> Dict[str, str]:
    """What a name format can take from a directory name: every ``KEY-VALUE`` piece
    between underscores (``MBs-500`` gives MBs=500, ``ctau-0p4`` gives ctau=0p4), and
    ``dir``, the whole name."""
    fields = {"dir": name}
    for piece in name.split("_"):
        key, sep, value = piece.partition("-")
        if sep and value and _FIELD.fullmatch(key):
            fields.setdefault(key, value)
    return fields


def format_fields(text: str) -> List[str]:
    """The fields a name format uses. ValueError unless every one is a plain ``{name}``."""
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError as exc:
        raise ValueError(f"name_format {text!r}: {exc}") from None
    fields = []
    for _, field, spec, conversion in parsed:
        if field is None:
            continue
        if spec or conversion or not _FIELD.fullmatch(field):
            raise ValueError(f"name_format {text!r}: write each field as {{name}}, with "
                             "nothing else between the braces")
        fields.append(field)
    if not fields:
        raise ValueError(f"name_format {text!r} has no {{field}}: every sample would get "
                         "the same name")
    return fields


def formatted_names(names: List[str], text: str,
                    short: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """``{directory name: sample name}`` from a format such as
    ``2Mu2E_{MBs}GeV_{MDp}GeV_{ctau}mm``, which names cms-sidm/SIDM's signal points
    (``..._MBs-500_MDp-0p25_ctau-0p4_v3`` becomes ``2Mu2E_500GeV_0p25GeV_0p4mm``).
    ``short`` adds the field ``short``: the name without what all the names share."""
    try:
        used = format_fields(text)
    except ValueError as exc:
        raise LocationError(str(exc), "For example {'name_format': "
                                      "'2Mu2E_{MBs}GeV_{MDp}GeV_{ctau}mm'}") from None
    named: Dict[str, str] = {}
    for name in names:
        fields = name_fields(name)
        if short is not None:
            fields["short"] = short.get(name, name)
        missing = [field for field in used if field not in fields]
        if missing:
            raise LocationError(
                f"name_format {text!r}: {name} has no {', '.join(missing)} (it has "
                f"{', '.join(sorted(fields))})",
                "Fields are the KEY-VALUE pieces of the directory names, plus dir and short")
        named[name] = text.format(**fields)
    seen: Dict[str, str] = {}
    for name, new in named.items():
        if new in seen:
            raise LocationError(f"name_format {text!r} names both {seen[new]} and {name} {new}",
                                "Add the field that tells them apart")
        seen[new] = name
    return named


def natural_key(name: str) -> List[Tuple[Any, ...]]:
    """A sort key that puts the numbers in a name in order, ``p`` read as the decimal
    point (0p02 < 0p2 < 2p0 < 10p0, MBs-150 < MBs-1000), as cms-sidm/SIDM lists its
    mass points."""
    parts = re.split(r"(\d+(?:p\d+)?)", name)
    return [(float(part.replace("p", ".")), part) if index % 2 else (part.lower(), part)
            for index, part in enumerate(parts)]


_CRAB_BLOCK = re.compile(r"(^|/)0\d{3}$")       # 0000, 0001, ...: not a year


def productions(files: List[str]) -> List[str]:
    """The directories a sample's files are spread over, the numbered blocks of one
    CRAB output (0000/, 0001/, ...) counted as one. More than one can mean two
    productions of the same events (a resubmission, an older version) side by side."""
    return sorted({_CRAB_BLOCK.sub("", os.path.dirname(path)) for path in files})


def name_from_directory(directory: str) -> str:
    """The last component of a directory or url, for naming a group or a sample."""
    stripped = directory.rstrip("/")
    if is_remote(stripped):
        stripped = split_xrootd(stripped)[1]
    return re.split(r"[/\\]", stripped)[-1] if stripped else ""
