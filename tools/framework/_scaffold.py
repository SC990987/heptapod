"""
# _scaffold.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Create a new analysis framework on disk.

Pure python (no orchestral, no coffea): the tool in scaffold.py is a thin adapter around
create_project.
"""
from __future__ import annotations

import difflib
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tools.analysis import nanoaod_layout as layout_lib
from tools.framework import _components as components
from tools.framework import _projects as projects
from tools.framework import _render as render
from tools.framework import _samples as samples_lib

SAMPLE_KEYS = ("name", "files", "is_data", "xsec", "year", "skim_factor")
SAMPLE_SHAPE = ("Each sample is {'name': ..., 'files': [...], 'is_data': false, 'xsec': <pb>, "
                "'year': ..., 'skim_factor': ...}.")
LOCATION_KEYS = ("directory", "files", "name", "tag", "config", "comment", "name_format",
                 "is_data", "year", "xsec", "skim_factor")
LOCATION_SHAPE = ("Each location is {'directory': a directory or root://host//path/, 'is_data': "
                  "false, 'year': ..., and optionally 'config': the file under configs/samples/ "
                  "to write it to, 'tag': the name of its group, 'comment': the line above it, "
                  "'name_format': e.g. '2Mu2E_{MBs}GeV_{MDp}GeV_{ctau}mm', or 'name': one "
                  "sample for the whole directory}.")
_CONFIG_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*\.ya?ml")
# a README with more lines than this is somebody's text: it is kept, and the framework's
# own README is written next to it as FRAMEWORK.md
README_KEPT_ABOVE = 3
_TRUE, _FALSE = ("true", "yes", "1"), ("false", "no", "0")


class ScaffoldError(ValueError):
    """The project cannot be created as requested; the message says what to change."""

    def __init__(self, message: str, suggestion: str = ""):
        super().__init__(message)
        self.suggestion = suggestion


def resolve_objects(layout: Optional[Dict[str, Dict[str, Any]]],
                    objects: Optional[Dict[str, str]],
                    include_default_objects: bool = True) -> List[Dict[str, Any]]:
    """Turn the requested ``{object name: collection}`` map into object specs.

    Defaults (the standard collections present in the file) come first unless
    switched off; requested objects are added to them, or replace the default of the
    same name.
    """
    if objects is not None and not isinstance(objects, dict):
        raise ScaffoldError("objects must map object names to collection names",
                            "For example objects={'muons': 'Muon', 'dsaMuons': 'DSAMuon'}.")
    chosen: Dict[str, str] = {}
    if include_default_objects:
        chosen.update(layout_lib.default_objects(layout))
    for name, collection in (objects or {}).items():
        chosen[name] = collection
    if not chosen:
        raise ScaffoldError("the analysis has no objects",
                            "Pass objects={'muons': 'Muon', ...} or keep the default objects.")
    specs = []
    for name, collection in chosen.items():
        problem = render.object_name_problem(name)
        if problem:
            raise ScaffoldError(f"'{name}' cannot be an object name: {problem}")
        if render.branch_name_problem(collection):
            raise ScaffoldError(f"object '{name}': '{collection}' is not a collection name",
                                "Give the name as it appears in the file, e.g. 'Muon'.")
        try:
            specs.append(layout_lib.object_spec(name, collection, layout))
        except KeyError:
            close = difflib.get_close_matches(collection, list(layout or {}), n=5, cutoff=0.5)
            raise ScaffoldError(
                f"object '{name}': the sample file has no collection '{collection}'",
                f"Closest names in the file: {close}" if close else
                "Inspect the file to see which collections it has.") from None
    return specs


def _number(value: Any, what: str, positive: bool = False) -> float:
    """A finite number given as a number (or as text that is one), else a ScaffoldError."""
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
        if not math.isfinite(number) or (positive and not number > 0):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        kind = "a positive number" if positive else "a number"
        shown = repr(value)
        raise ScaffoldError(f"{what} must be {kind}, got "
                            f"{shown if len(shown) < 60 else shown[:57] + '...'}") from None
    return number


def _flag(value: Any, what: str) -> bool:
    """A real true/false. The text "false" must not count as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in _TRUE + _FALSE:
        return value.strip().lower() in _TRUE
    raise ScaffoldError(f"{what} must be true or false, got {value!r}")


def _period(value: Any, what: str) -> Optional[str]:
    """A run period as text ("2018"), or None when it is not given."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ScaffoldError(f"{what} must be a name such as '2018', got {value!r}")
    period = str(value)
    problem = render.sample_name_problem(period)
    if problem:
        raise ScaffoldError(f"'{period}' cannot be used as {what}: {problem}")
    return period


def normalise_samples(samples: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Check the sample list and bring every entry to the same shape."""
    if samples is not None and not isinstance(samples, (list, tuple)):
        raise ScaffoldError("samples must be a list", SAMPLE_SHAPE)
    normalised = []
    seen = set()
    for index, sample in enumerate(samples or []):
        if not isinstance(sample, dict) or sample.get("name") in (None, ""):
            raise ScaffoldError(f"sample #{index} needs a 'name'", SAMPLE_SHAPE)
        name = sample["name"]
        if isinstance(name, int) and not isinstance(name, bool):
            name = str(name)            # a sample called 2018 is the text "2018"
        problem = render.sample_name_problem(name)
        if problem:
            raise ScaffoldError(
                f"'{name}' cannot be a sample name: {problem}",
                f"For example '{render.sample_name_from(str(name))}'. The name is also what "
                "the cross section is looked up under.")
        if name in seen:
            raise ScaffoldError(f"sample '{name}' is listed twice")
        seen.add(name)
        unknown = set(sample) - set(SAMPLE_KEYS)
        if unknown:
            raise ScaffoldError(f"sample '{name}': unknown key(s) {sorted(unknown)}",
                                f"Allowed keys: {list(SAMPLE_KEYS)}")
        files = sample.get("files") or []
        if isinstance(files, str):
            files = [files]
        if not isinstance(files, (list, tuple)) or not files:
            raise ScaffoldError(f"sample '{name}' has no files",
                                "Give 'files' as a list of paths or root:// urls.")
        for path in files:
            problem = render.text_problem(path)
            if problem:
                raise ScaffoldError(f"sample '{name}': {path!r} cannot be a file name: {problem}")
        xsec = sample.get("xsec")
        skim_factor = sample.get("skim_factor")
        normalised.append({
            "name": name,
            "files": list(files),
            "is_data": _flag(sample.get("is_data", False), f"sample '{name}': is_data"),
            "xsec": None if xsec is None else _number(xsec, f"sample '{name}': xsec"),
            "year": _period(sample.get("year"), f"the year of sample '{name}'"),
            "skim_factor": None if skim_factor is None else _number(
                skim_factor, f"sample '{name}': skim_factor", positive=True),
        })
    return normalised


def normalise_locations(locations: Optional[List[Dict[str, Any]]], year: Optional[str],
                        taken: set, reserved: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Turn listed locations into groups of samples, as the sample configs hold them.

    Each location carries the ``files`` found below its ``directory`` (relative paths,
    as samples_lib.list_files returns them) and becomes one group, written to a config
    file of its own under configs/samples/ as cms-sidm/SIDM keeps one ntuple config per
    production. ``taken`` holds the sample names already used; the names of the new
    samples are added to it. ``reserved`` are group names of samples.yaml that are taken.
    """
    if locations is not None and not isinstance(locations, (list, tuple)):
        raise ScaffoldError("sample_locations must be a list", LOCATION_SHAPE)
    groups: List[Dict[str, Any]] = []
    in_config: Dict[str, set] = {"samples.yaml": set(reserved)}     # config -> its tags
    default_configs: set = set()
    for index, location in enumerate(locations or []):
        if not isinstance(location, dict) or not location.get("directory"):
            raise ScaffoldError(f"sample location #{index} needs a 'directory'", LOCATION_SHAPE)
        directory = location["directory"]
        unknown = set(location) - set(LOCATION_KEYS)
        if unknown:
            raise ScaffoldError(f"location {directory}: unknown key(s) {sorted(unknown)}",
                                LOCATION_SHAPE)
        files = location.get("files") or []
        if not files:
            raise ScaffoldError(f"no ROOT files were found below {directory}",
                                "Check the directory, or give the files of the sample one by one")
        for path in files:
            problem = render.text_problem(path)
            if problem:
                raise ScaffoldError(f"{directory}: {path!r} cannot be a file name: {problem}")
        name = location.get("name")
        if isinstance(name, int) and not isinstance(name, bool):
            name = str(name)
        if name is not None:
            problem = render.sample_name_problem(name)
            if problem:
                raise ScaffoldError(f"'{name}' cannot be a sample name: {problem}")
        name_format = location.get("name_format")
        if name_format is not None:
            if not isinstance(name_format, str) or render.text_problem(name_format):
                raise ScaffoldError(f"location {directory}: name_format must be text such as "
                                    "'2Mu2E_{MBs}GeV_{MDp}GeV_{ctau}mm'")
            if name is not None:
                raise ScaffoldError(f"location {directory}: give 'name' (the directory is one "
                                    "sample) or 'name_format' (one sample per sub-directory), "
                                    "not both")
        comment = location.get("comment")
        if comment is not None:
            if not isinstance(comment, str) or render.text_problem(comment) or "\n" in comment:
                raise ScaffoldError(f"location {directory}: comment must be one line of text")
            comment = " ".join(comment.split())
        basename = samples_lib.name_from_directory(directory)
        try:
            grouped = samples_lib.group_by_sample(files, name, render.sample_name_from(basename))
        except samples_lib.LocationError as exc:
            raise ScaffoldError(f"{directory}: {exc}", exc.suggestion) from None
        is_data = _flag(location.get("is_data", False), f"location {directory}: is_data")
        period = _period(location.get("year"), f"the year of location {directory}") or year
        xsec = location.get("xsec")
        if xsec is not None:
            if len(grouped) != 1:
                raise ScaffoldError(
                    f"location {directory}: an 'xsec' is for one sample, and this directory "
                    f"holds {len(grouped)}",
                    "Give the cross sections in configs/cross_sections.yaml afterwards, or "
                    "give 'name' to make the directory one sample")
            xsec = _number(xsec, f"location {directory}: xsec")
        skim_factor = location.get("skim_factor")
        if skim_factor is not None:
            skim_factor = _number(skim_factor, f"location {directory}: skim_factor", positive=True)

        # the file it goes to, and its group in that file
        stem = render.sample_name_from(basename or "samples")
        config = location.get("config")
        if config is not None:
            config = str(config)
            if not _CONFIG_NAME.fullmatch(config):
                raise ScaffoldError(f"location {directory}: config {config!r} must be the name of "
                                    "a .yaml file under configs/samples/, such as "
                                    "'signal_2mu2e_v10.yaml' (no directories)")
        else:
            config, number = f"{stem}.yaml", 2
            while config in default_configs or config == "samples.yaml":
                config, number = f"{stem}_{number}.yaml", number + 1
            default_configs.add(config)
        tags = in_config.setdefault(config, set())
        tag = location.get("tag")
        if tag is not None:
            tag = str(tag)
            problem = render.sample_name_problem(tag)
            if problem:
                raise ScaffoldError(f"'{tag}' cannot be the name of a group of samples: {problem}")
            if config == "samples.yaml" and tag in reserved:
                raise ScaffoldError(f"'{tag}' is the group of the samples given file by file",
                                    "Give the location another 'tag'")
            if tag in tags:
                raise ScaffoldError(f"two locations both write the group '{tag}' of {config}",
                                    "Give each its own 'tag' or 'config'")
        else:
            tag, number = stem, 2
            while tag in tags:
                tag, number = f"{stem}_{number}", number + 1
        tags.add(tag)

        def valid(text):
            return text if render.sample_name_problem(text) is None else render.sample_name_from(text)

        # named after their directories: by name_format, or without what all of them
        # share; but never so that a name another location took comes back
        short = samples_lib.shortened_names(list(grouped)) if name is None else {}
        if name_format is not None:
            try:
                naming = samples_lib.formatted_names(list(grouped), name_format, short)
            except samples_lib.LocationError as exc:
                raise ScaffoldError(f"{directory}: {exc}", exc.suggestion) from None
        else:
            naming = short if not any(valid(value) in taken for value in short.values()) else {}
        shortened = next(((found, naming[found]) for found in sorted(naming, key=samples_lib.natural_key)
                          if found != naming[found]), None)
        samples, renamed, spread = [], [], []
        for found in sorted(grouped, key=samples_lib.natural_key):
            info = grouped[found]
            final = valid(naming.get(found, found))
            if final != naming.get(found, found):
                renamed.append(f"{naming.get(found, found)} -> {final}")
            if final in taken:
                raise ScaffoldError(
                    f"sample '{final}' is defined twice (found again below {directory})",
                    "Register one of the two locations with a 'name' or a 'name_format', or "
                    "leave it out")
            taken.add(final)
            where = samples_lib.productions(info["files"])
            if len(where) > 1:
                spread.append(f"{final} ({', '.join((d or '.') + '/' for d in where[:3])}"
                              f"{', ...' if len(where) > 3 else ''})")
            samples.append({"name": final, "path": info["path"], "files": list(info["files"]),
                            "is_data": is_data, "xsec": xsec, "year": period,
                            "skim_factor": skim_factor,
                            "group_path": directory.rstrip("/") + "/"})
        kind = "data" if is_data else "simulation"
        groups.append({"tag": tag, "config": config, "path": directory.rstrip("/") + "/",
                       "year": period, "samples": samples, "renamed": renamed,
                       "spread": spread, "shortened": shortened,
                       "named_by": "name_format" if name_format is not None else "directory",
                       "comment": comment or f"{len(samples)} {kind} sample(s) found below "
                                             f"{directory.rstrip('/')}/"})
    return groups


def _same_file(one: str, other: str) -> bool:
    """Whether two spellings name the same file (``root://host//a/b`` and
    ``root://host///a//b`` do; a local path and a url never do)."""
    if samples_lib.is_remote(one) != samples_lib.is_remote(other):
        return False
    if samples_lib.is_remote(one):
        (host, path), (host2, path2) = samples_lib.split_xrootd(one), samples_lib.split_xrootd(other)
        return host == host2 and re.sub("/+", "/", path) == re.sub("/+", "/", path2)
    return os.path.realpath(one) == os.path.realpath(other)


def _holds(sample: Dict[str, Any], target: Optional[str]) -> bool:
    """Whether ``target`` is one of the sample's files (``group path + path + file``)."""
    if not target:
        return False
    base = (sample.get("group_path") or "") + (sample.get("path") or "")
    return any(_same_file(name if "://" in name or name.startswith("/") else base + name, target)
               for name in sample["files"])


def _merged_gitignore(existing: str, ours: str) -> Optional[str]:
    """The repository's .gitignore with the framework's lines it lacks, or None if none."""
    have = {line.strip() for line in existing.splitlines()}
    missing = [line for line in ours.splitlines()
               if line.strip() and not line.startswith("#") and line.strip() not in have]
    if not missing:
        return None
    text = existing if existing.endswith("\n") or not existing else existing + "\n"
    return text + "\n# added with the analysis framework\n" + "\n".join(missing) + "\n"


def _is_own_readme(path: Path) -> bool:
    """A README with more than a title and a line or two is the repository owner's text."""
    try:
        text = path.read_text(encoding="utf8", errors="replace")
    except OSError:
        return False
    return sum(1 for line in text.splitlines() if line.strip()) > README_KEPT_ABOVE


def _sample_configs(samples: List[Dict[str, Any]],
                    groups: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """``{config file: {tag: number of samples}}``, for the answer."""
    configs: Dict[str, Dict[str, int]] = {}
    if samples or not groups:
        configs["samples.yaml"] = {"main": len(samples)}
    for group in groups:
        configs.setdefault(group["config"], {})[group["tag"]] = len(group["samples"])
    return configs


def _shortened(names: List[str], limit: int = 10) -> str:
    """A list of names for a note, cut short when there are many."""
    if len(names) <= limit:
        return str(list(names))
    return f"{list(names[:limit])} and {len(names) - limit} more"


def _triggers(triggers: Optional[List[str]]) -> List[str]:
    if triggers is None:
        return []
    if isinstance(triggers, str):
        triggers = [triggers]
    if not isinstance(triggers, (list, tuple)):
        raise ScaffoldError("triggers must be a list of HLT path names",
                            "For example triggers=['HLT_IsoMu24'].")
    for trigger in triggers:
        if not isinstance(trigger, str) or not re.fullmatch(r"[A-Za-z0-9_]+", trigger):
            raise ScaffoldError(f"{trigger!r} is not a trigger path",
                                "Give HLT paths by name, e.g. 'HLT_IsoMu24' or 'IsoMu24'.")
    return list(triggers)


def create_project(project: Path, *, package: Optional[str] = None, title: Optional[str] = None,
                   description: Optional[str] = None, author: Optional[str] = None,
                   experiment: str = "CMS",
                   layout: Optional[Dict[str, Dict[str, Any]]] = None,
                   objects: Optional[Dict[str, str]] = None,
                   include_default_objects: bool = True,
                   triggers: Optional[List[str]] = None,
                   year: Optional[str] = None, lumi: Optional[float] = None,
                   golden_json: Optional[Path] = None,
                   samples: Optional[List[Dict[str, Any]]] = None,
                   locations: Optional[List[Dict[str, Any]]] = None,
                   tree_name: str = "Events",
                   add_components: Optional[List[str]] = None,
                   overwrite: bool = False, repo: bool = False,
                   representative: Optional[str] = None) -> Dict[str, Any]:
    """Write a new analysis framework into ``project`` and return a summary of it.

    layout: collections of a representative file (nanoaod_layout.split_collections), or
        None to assume standard NanoAOD
    objects: ``{object name: collection}`` added to (or replacing) the default objects
    golden_json: file to copy into the package's data/ directory for ``year``
    samples: ``[{"name", "files", "is_data", "xsec", "year", "skim_factor"}]``
    locations: directories the samples live in, each with the ``files`` found below it:
        ``[{"directory", "files", "name", "tag", "is_data", "year", "xsec", "skim_factor"}]``.
        Each becomes a group of the sample config (see normalise_locations).
    tree_name: name of the TTree holding the events in the input files
    add_components: optional components to apply right away. The framework is written
        first; a component that cannot be applied is reported in ``components_failed``
        and leaves the framework as it is.
    representative: the file the layout was read from. The sample it belongs to comes
        first in the notebooks and in the commands the README and the answer show.
    overwrite: allow a directory that already has files in it (a new repository with
        a README, say); files the framework writes replace existing ones of the same
        name. A directory that already holds a framework is never written over: its
        definitions and configs are somebody's analysis.
    repo: the directory is a git repository: it may hold files already, its .gitignore
        gets the framework's lines added rather than replaced, and a README with more
        than a title is kept (the framework's goes to FRAMEWORK.md). Committing is the
        caller's business.

    Everything is checked and rendered before anything is written, and the writing is
    undone if it fails part-way: the directory is then exactly as it was.
    """
    project = Path(project)
    package = package or render.package_name_from(project.name)
    problem = render.package_name_problem(package)
    if problem:
        raise ScaffoldError(f"'{package}' cannot be the package name: {problem}",
                            "Pass package_name explicitly, e.g. 'dimuon_analysis'.")
    if project.exists():
        if not project.is_dir():
            raise ScaffoldError(f"{project.name} exists and is not a directory")
        existing = projects.find_package(project)
        if existing is not None or (project / projects.MARKER).exists() \
                or (project / package).exists():
            holds = f"the analysis framework '{existing}'" if existing else \
                f"a framework marker or a '{package}' directory"
            raise ScaffoldError(
                f"directory '{project.name}' already holds {holds}: scaffolding again would "
                "replace its definitions and configs",
                "To change an existing framework, edit its definitions/ and configs/ or use "
                "AddFrameworkComponent. To start over, choose another project_dir or remove "
                "this one first." + ("" if not overwrite else
                                     " overwrite=true does not write over a framework."))
        if any(project.iterdir()) and not (overwrite or repo):
            raise ScaffoldError(
                f"directory '{project.name}' already exists and is not empty",
                "Choose another project_dir, or pass overwrite=true to write the framework "
                "into it: files of the same name (README.md, setup.py, requirements.txt, "
                ".gitignore, MANIFEST.in) are then replaced.")
    add_components = list(add_components or [])
    for name in add_components:
        if name not in components.COMPONENTS:
            raise ScaffoldError(f"unknown component '{name}'",
                                f"Available components: {sorted(components.COMPONENTS)}")
    problem = render.tree_name_problem(tree_name)
    if problem:
        raise ScaffoldError(f"'{tree_name}' cannot be used as the tree name: {problem}")
    year = _period(year, "the run period (year)")
    if lumi is not None:
        lumi = _number(lumi, "lumi", positive=True)
        if not year:
            raise ScaffoldError("a luminosity needs the run period it belongs to",
                                "Pass year as well, e.g. year='2018'.")
    golden_name = None
    if golden_json is not None:
        if not year:
            raise ScaffoldError("a golden JSON needs the run period it belongs to",
                                "Pass year as well, e.g. year='2018'.")
        if not Path(golden_json).is_file():
            raise ScaffoldError(f"golden JSON not found: {golden_json}")
        golden_name = Path(golden_json).name
        if render.text_problem(golden_name):
            raise ScaffoldError(f"{golden_name!r} cannot be used as a file name in data/")
    experiment = render.clean_text(experiment, default="CMS")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", experiment):
        raise ScaffoldError(f"'{experiment}' is not an experiment name",
                            "Use a name mplhep has a style for: CMS, ATLAS, LHCb, ALICE, ...")

    specs = resolve_objects(layout, objects, include_default_objects)
    triggers = _triggers(triggers)
    try:
        plan = render.build_plan(specs, triggers)
    except render.PlanError as exc:
        raise ScaffoldError(str(exc), "Give the objects names that cannot be confused: "
                                      "differing by more than a final 's', and not made "
                                      "of another object's name plus a field name.") from None
    samples = normalise_samples(samples)
    for sample in samples:
        if sample["year"] is None:
            sample["year"] = year
    groups = normalise_locations(locations, year, {s["name"] for s in samples},
                                 reserved={"main"} if samples else ())
    every_sample = samples + [s for group in groups for s in group["samples"]]
    # the notebooks and examples start from the sample the representative file is in
    shown = sorted(every_sample, key=lambda s: not _holds(s, representative))
    title = render.clean_text(title, default=package.replace("_", " ").capitalize())
    description = render.clean_text(
        description, default="Columnar analysis of NanoAOD-like files with coffea.")
    author = render.clean_text(author)
    channels = render.channel_names(plan)
    example_sample = shown[0]["name"] if shown else "SAMPLE_NAME"
    tokens = {
        "__PROJECT_TITLE__": title,
        "__PROJECT_DESCRIPTION__": description,
        "__AUTHOR__": author,
        "__EXPERIMENT__": experiment,
        "__EXAMPLE_SAMPLE__": example_sample,
        "__EXAMPLE_HIST__": render.first_hist(plan),
        "__TREE_NAME__": tree_name,
        "__YEAR__": year or "",
    }
    try:
        generated = {
            f"{package}/definitions/objects.py": render.render_objects(plan, package),
            f"{package}/definitions/cuts.py": render.render_cuts(plan),
            f"{package}/definitions/hists.py": render.render_hists(plan, package),
            f"{package}/configs/selections.yaml": render.render_selections(plan),
            f"{package}/configs/hist_collections.yaml": render.render_hist_collections(plan),
            f"{package}/configs/run_periods.yaml": render.render_run_periods(year, lumi, golden_name),
            f"{package}/configs/cross_sections.yaml": render.render_cross_sections(every_sample),
            f"{package}/configs/samples/README.md": render.render_samples_readme(package),
            **{f"{package}/configs/samples/{config}": text for config, text in
               render.render_sample_configs(samples, groups, package, year).items()},
            f"{package}/test_notebooks/test_processor.ipynb": render.render_notebook(
                package, title, [s["name"] for s in shown], channels,
                render.first_hist(plan)),
            f"{package}/{render.STUDY_NOTEBOOK}": render.render_study_notebook(
                package, title, plan, [s["name"] for s in shown if not s["is_data"]],
                [s["name"] for s in shown if s["is_data"]]),
        }
    except ValueError as exc:
        raise ScaffoldError(f"the definitions could not be written: {exc}") from None
    marker = {
        "package": package,
        "title": title,
        "experiment": experiment,
        "tree": tree_name,
        "objects": {s["name"]: s["collection"] for s in specs},
        "samples": [{"name": s["name"], "is_data": s["is_data"]} for s in every_sample],
        "triggers": triggers,
        "components": {},
    }

    # the repository's own .gitignore and README are not replaced
    keep: List[str] = []
    if repo:
        if (project / ".gitignore").is_file():
            keep.append(".gitignore")
        if (project / "README.md").is_file() and _is_own_readme(project / "README.md"):
            keep.append("README.md")

    targets = dict.fromkeys([*projects.template_targets("core", package), *generated])
    if "README.md" in keep:
        targets["FRAMEWORK.md"] = None
    replaced = [relative for relative in targets
                if (project / relative).exists() and relative not in keep]
    # In a repository, a file of the analyst's is not replaced unasked; a README of a
    # line or two is (a new repository's), and a longer one was kept above.
    clashes = [relative for relative in replaced if relative != "README.md"]
    if repo and clashes and not overwrite:
        raise ScaffoldError(
            f"the repository already has {_shortened(clashes)}, which the framework would "
            "replace",
            "Pass overwrite=true to replace them (they are then listed in files_replaced, and "
            "the commit shows what changed), or build in a repository without them")

    # ---- everything below writes to disk; a failure undoes all of it ------------
    merged: List[str] = []
    with projects.Writes(root=project) as writes:
        writes.mkdir(project)
        written, _ = projects.copy_template("core", project, package, writes, tokens,
                                            overwrite=True, never_overwrite=keep)
        if ".gitignore" in keep:
            ours = projects.render_text(
                (projects.TEMPLATE_DIR / "core" / "gitignore").read_text(encoding="utf8"),
                package, tokens)
            text = _merged_gitignore(projects.read_text(project, ".gitignore"), ours)
            if text is not None:
                writes.write_text(project / ".gitignore", text)
                written.append(".gitignore")
                merged.append(".gitignore")
        if "README.md" in keep:
            readme = projects.render_text(
                (projects.TEMPLATE_DIR / "core" / "README.md").read_text(encoding="utf8"),
                package, tokens)
            projects.write_file(project, "FRAMEWORK.md", readme, writes, overwrite=True)
            written.append("FRAMEWORK.md")
        if golden_json is not None:
            writes.copy(Path(golden_json), project / package / "data" / golden_name)
            written.append(f"{package}/data/{golden_name}")
        for relative, text in generated.items():
            projects.write_file(project, relative, text, writes, overwrite=True)
            written.append(relative)
        projects.write_marker(project, marker, writes)

    notes = list(plan["notes"])
    for spec in specs:
        notes += spec["notes"]
    if layout is not None:
        if include_default_objects:
            notes += [why for name, why in layout_lib.skipped_defaults(layout).items()
                      if name not in (objects or {})]
        used = [s["collection"] for s in specs]
        others = layout_lib.other_kinematic_collections(layout, used)
        if others:
            notes.append("other collections with pt/eta/phi in the file, not used yet: "
                         + ", ".join(others))
    else:
        notes.append("no sample file was given, so standard NanoAOD collections were assumed. "
                     "A collection the files do not have stops the run with its name: remove "
                     "the object from definitions/objects.py, or list it in optional_objs there")
    if lumi is None:
        notes.append("no luminosity was given: simulation stays unscaled until lumi is set in "
                     f"{package}/configs/run_periods.yaml")
    for group in groups:
        kinds = "data" if group["samples"] and group["samples"][0]["is_data"] else "simulation"
        notes.append(f"{len(group['samples'])} {kinds} sample(s) found below {group['path']} "
                     f"were registered as the group '{group['tag']}' of "
                     f"{package}/configs/samples/{group['config']}")
        if group["shortened"]:
            full, short = group["shortened"]
            how = "by name_format" if group["named_by"] == "name_format" else \
                "after their directories without the part all of them share"
            notes.append(f"the samples of {group['config']} are named {how} ('{full}' is "
                         f"'{short}'); the directory stays in each sample's path")
        if group["renamed"]:
            notes.append(f"sample names changed to characters a sample name can have: "
                         f"{_shortened(group['renamed'])}")
        if group["spread"]:
            notes.append(f"the files of a sample are in more than one directory, which can be "
                         f"two productions of the same events (a resubmission, an older "
                         f"version): {_shortened(group['spread'], 5)}. Check them, and keep one "
                         f"in {package}/configs/samples/{group['config']}")
    unscaled = [s["name"] for s in every_sample if not s["is_data"] and s["xsec"] is None]
    if unscaled:
        notes.append(f"no cross section for {_shortened(unscaled)}: add them to "
                     f"{package}/configs/cross_sections.yaml")
    no_period = [s["name"] for s in every_sample if s["year"] is None]
    if no_period:
        notes.append(f"no run period (year) for {_shortened(no_period)}: simulation needs one "
                     "to be scaled and data to find its golden JSON")
    if any(s["is_data"] for s in every_sample) and golden_name is None:
        notes.append("data without a golden JSON: every luminosity section is kept until one "
                     f"is configured in {package}/configs/run_periods.yaml")
    if replaced:
        notes.append(f"files that were already in the directory were replaced: {replaced}")
    if merged:
        notes.append("the repository's .gitignore was kept, with the framework's entries added")
    if "README.md" in keep:
        notes.append("the repository's README.md was kept; the framework's README is "
                     "FRAMEWORK.md")

    component_results, component_failures = [], []
    for name in dict.fromkeys(add_components):
        try:
            component_results.append(components.apply_component(project, name, {}))
        except Exception as exc:
            known = isinstance(exc, components.ComponentError)
            component_failures.append({
                "component": name,
                "error": str(exc) if known else f"{type(exc).__name__}: {exc}",
                "suggestion": getattr(exc, "suggestion", "") or
                "Add it afterwards with AddFrameworkComponent, giving its options."})
    if component_failures:
        notes.append("the framework was written, but not every component could be added: "
                     "see components_failed")
    notes += [f"[{result['component']}] {note}" for result in component_results
              for note in result["notes"]]

    check = f"python -m {package}.tools.check"
    with_sample = ""
    if shown:
        with_sample = f", then with sample={shown[0]['name']!r}"
        check += f" [--sample {shown[0]['name']}]"
    every_written = list(written) + [projects.MARKER]
    for result in component_results:
        every_written += list(result.get("files_written") or []) + list(result.get("blocks") or {})
    return {
        "project": project.name,
        "package": package,
        "tree": tree_name,
        "n_files_written": len(written),
        # every path written or edited, for the caller that commits them (not for display)
        "written": sorted(set(every_written)),
        "files_replaced": replaced,
        "objects": [{"name": s["name"], "collection": s["collection"], "kind": s["kind"],
                     "object_cuts": len(plan["obj_cuts"].get(s["name"], [])),
                     "wrapped_as_lorentz": bool(s.get("wrap")),
                     "optional": bool(s["mc_only"])} for s in specs],
        "channels": channels,
        "hist_collections": list(plan["collections"]) + ["base"],
        "n_hists": len(plan["hists"]),
        "n_event_cuts": len(plan["evt_cuts"]),
        "samples": [s["name"] for s in every_sample],
        "sample_configs": _sample_configs(samples, groups),
        "components": [r["component"] for r in component_results],
        "components_failed": component_failures,
        "notes": notes,
        "layout": {
            "engine": f"{package}/tools/",
            "definitions": f"{package}/definitions/ (objects.py, cuts.py, hists.py, weights.py)",
            "configs": f"{package}/configs/ (selections.yaml, hist_collections.yaml, samples/, "
                       "cross_sections.yaml, run_periods.yaml)",
            "notebook": f"{package}/test_notebooks/test_processor.ipynb",
            "study": f"{package}/{render.STUDY_NOTEBOOK}",
            "readme": "README.md",
        },
        "next_steps": [
            f"check it: CheckAnalysisFramework on this project (static first{with_sample}), "
            f"or `{check}` in an environment that has the requirements",
            "the cuts, thresholds and selections that were written are placeholders: replace "
            "them in definitions/ and configs/ with the analysis's own, re-running the check "
            "after each change",
            f"the first study, {package}/{render.STUDY_NOTEBOOK}, processes a file of two "
            "simulated samples and one data sample and plots the basic lepton histograms: "
            "open it in Jupyter with an environment that has coffea (it brings mplhep)",
            f"to work in it by hand, in an environment that already has coffea: cd "
            f"{project.name} && pip install -e .  (in a new environment, first: python -m venv "
            ".venv && source .venv/bin/activate && pip install -r requirements.txt)",
        ],
    }
