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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.analysis import nanoaod_layout as layout_lib
from tools.framework import _components as components
from tools.framework import _projects as projects
from tools.framework import _render as render

SAMPLE_KEYS = ("name", "files", "is_data", "xsec", "year", "skim_factor")
SAMPLE_SHAPE = ("Each sample is {'name': ..., 'files': [...], 'is_data': false, 'xsec': <pb>, "
                "'year': ..., 'skim_factor': ...}.")
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
                   tree_name: str = "Events",
                   add_components: Optional[List[str]] = None,
                   overwrite: bool = False) -> Dict[str, Any]:
    """Write a new analysis framework into ``project`` and return a summary of it.

    layout: collections of a representative file (nanoaod_layout.split_collections), or
        None to assume standard NanoAOD
    objects: ``{object name: collection}`` added to (or replacing) the default objects
    golden_json: file to copy into the package's data/ directory for ``year``
    samples: ``[{"name", "files", "is_data", "xsec", "year", "skim_factor"}]``
    tree_name: name of the TTree holding the events in the input files
    add_components: optional components to apply right away. The framework is written
        first; a component that cannot be applied is reported in ``components_failed``
        and leaves the framework as it is.
    overwrite: allow a directory that already has files in it (a new repository with
        a README, say); files the framework writes replace existing ones of the same
        name. A directory that already holds a framework is never written over: its
        definitions and configs are somebody's analysis.

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
        if any(project.iterdir()) and not overwrite:
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
    title = render.clean_text(title, default=package.replace("_", " ").capitalize())
    description = render.clean_text(
        description, default="Columnar analysis of NanoAOD-like files with coffea.")
    author = render.clean_text(author)
    channels = render.channel_names(plan)
    example_sample = samples[0]["name"] if samples else "SAMPLE_NAME"
    tokens = {
        "__PROJECT_TITLE__": title,
        "__PROJECT_DESCRIPTION__": description,
        "__AUTHOR__": author,
        "__EXPERIMENT__": experiment,
        "__EXAMPLE_SAMPLE__": example_sample,
        "__EXAMPLE_HIST__": render.first_hist(plan),
        "__TREE_NAME__": tree_name,
    }
    try:
        generated = {
            f"{package}/definitions/objects.py": render.render_objects(plan, package),
            f"{package}/definitions/cuts.py": render.render_cuts(plan),
            f"{package}/definitions/hists.py": render.render_hists(plan, package),
            f"{package}/configs/selections.yaml": render.render_selections(plan),
            f"{package}/configs/hist_collections.yaml": render.render_hist_collections(plan),
            f"{package}/configs/run_periods.yaml": render.render_run_periods(year, lumi, golden_name),
            f"{package}/configs/cross_sections.yaml": render.render_cross_sections(samples),
            f"{package}/configs/samples/samples.yaml": render.render_samples(
                samples, "main", year, package),
            f"{package}/test_notebooks/test_processor.ipynb": render.render_notebook(
                package, title, [s["name"] for s in samples], channels, render.first_hist(plan)),
        }
    except ValueError as exc:
        raise ScaffoldError(f"the definitions could not be written: {exc}") from None
    marker = {
        "package": package,
        "title": title,
        "experiment": experiment,
        "tree": tree_name,
        "objects": {s["name"]: s["collection"] for s in specs},
        "samples": [{"name": s["name"], "is_data": s["is_data"]} for s in samples],
        "triggers": triggers,
        "components": {},
    }

    # ---- everything below writes to disk; a failure undoes all of it ------------
    replaced = [relative for relative in projects.template_targets("core", package)
                if (project / relative).exists()]
    with projects.Writes() as writes:
        writes.mkdir(project)
        written, _ = projects.copy_template("core", project, package, writes, tokens,
                                            overwrite=True)
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
    unscaled = [s["name"] for s in samples if not s["is_data"] and s["xsec"] is None]
    if unscaled:
        notes.append(f"no cross section for {unscaled}: add them to "
                     f"{package}/configs/cross_sections.yaml")
    no_period = [s["name"] for s in samples if s["year"] is None]
    if no_period:
        notes.append(f"no run period (year) for {no_period}: simulation needs one to be scaled "
                     "and data to find its golden JSON")
    if any(s["is_data"] for s in samples) and golden_name is None:
        notes.append("data without a golden JSON: every luminosity section is kept until one "
                     f"is configured in {package}/configs/run_periods.yaml")
    if replaced:
        notes.append(f"files that were already in the directory were replaced: {replaced}")

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
    if samples:
        with_sample = f", then with sample={samples[0]['name']!r}"
        check += f" [--sample {samples[0]['name']}]"
    return {
        "project": project.name,
        "package": package,
        "tree": tree_name,
        "n_files_written": len(written),
        "files_replaced": replaced,
        "objects": [{"name": s["name"], "collection": s["collection"], "kind": s["kind"],
                     "object_cuts": len(plan["obj_cuts"].get(s["name"], [])),
                     "wrapped_as_lorentz": bool(s.get("wrap")),
                     "optional": bool(s["mc_only"])} for s in specs],
        "channels": channels,
        "hist_collections": list(plan["collections"]) + ["base"],
        "n_hists": len(plan["hists"]),
        "n_event_cuts": len(plan["evt_cuts"]),
        "samples": [s["name"] for s in samples],
        "components": [r["component"] for r in component_results],
        "components_failed": component_failures,
        "notes": notes,
        "layout": {
            "engine": f"{package}/tools/",
            "definitions": f"{package}/definitions/ (objects.py, cuts.py, hists.py, weights.py)",
            "configs": f"{package}/configs/ (selections.yaml, hist_collections.yaml, samples/, "
                       "cross_sections.yaml, run_periods.yaml)",
            "notebook": f"{package}/test_notebooks/test_processor.ipynb",
            "readme": "README.md",
        },
        "next_steps": [
            f"check it: CheckAnalysisFramework on this project (static first{with_sample}), "
            f"or `{check}` in an environment that has the requirements",
            "the cuts, thresholds and selections that were written are placeholders: replace "
            "them in definitions/ and configs/ with the analysis's own, re-running the check "
            "after each change",
            f"to work in it by hand, in an environment that already has coffea: cd "
            f"{project.name} && pip install -e .  (in a new environment, first: python -m venv "
            ".venv && source .venv/bin/activate && pip install -r requirements.txt)",
        ],
    }
