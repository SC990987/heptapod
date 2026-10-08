"""
# _components.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Optional components of a scaffolded analysis framework.

A component is a set of files copied into the project plus, where needed, clearly
delimited blocks appended to files the analyst also edits (definitions and configs).
Applying one twice is safe: files and blocks that already exist are kept unless
overwrite is requested.

Applying a component either completes or changes nothing: every write goes through one
projects.Writes transaction, together with the note of it in the project's marker. The
marker records the options a component's files were written from, and is only updated
when they were written.

Pure python (no orchestral, no coffea).
"""
from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.framework import _projects as projects
from tools.framework._render import (
    PLACEHOLDER_PACKAGE, _cell, branch_name_problem, num, object_name_problem, py_literal, q,
    singular)

COMPONENTS = {
    "lepton_jets": "Lepton jets (anti-kT clusters of leptons and photons) as a derived object, "
                   "with starting cuts, histograms and a selection.",
    "scaleout": "Dask clients for local processes, an existing scheduler, and HTCondor at the "
                "LPC, with a scale-out notebook.",
    "schema": "A customised NanoAOD schema: behaviours for extra collections, cross-references "
              "between collections, hidden branches, constant fields.",
    "chain_report": "A regression report over small committed test files, with a GitHub "
                    "workflow that compares each pull request against its base.",
}

COORDINATE_NAMES = {"x", "y", "z", "t", "px", "py", "pz", "rho", "pt", "phi", "eta", "theta",
                    "tau", "E", "e", "energy", "M", "m", "mass"}
# Fields the lepton-jet builder gives every lepton jet (tools/lepton_jets.py).
LEPTON_JET_FIELDS = {"constituents", "n_constituents", "dRSpread", "matched_jet",
                     "lepton_fraction", "isolation", "dR_matched_jet"}
_NONE_WORDS = ("", "none", "null", "false", "no")


class ComponentError(ValueError):
    """A component cannot be applied as requested; the message says what to change."""

    def __init__(self, message: str, suggestion: str = ""):
        super().__init__(message)
        self.suggestion = suggestion


def _result(component: str) -> Dict[str, Any]:
    return {"component": component, "files_written": [], "files_kept": [], "blocks": {},
            "notes": [], "next_steps": []}


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _unknown_options(options: Dict[str, Any], allowed: List[str], component: str) -> None:
    unknown = sorted(str(key) for key in set(map(str, options)) - set(allowed))
    if unknown:
        raise ComponentError(f"unknown option(s) for {component}: {unknown}",
                             f"Allowed options: {allowed}" if allowed else
                             f"{component} takes no options.")


def _finite(value: Any, what: str, positive: bool = False) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError
        number = float(value)
        if not math.isfinite(number) or (positive and not number > 0):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        kind = "must be a positive number" if positive else "needs a number"
        raise ComponentError(f"{what} {kind}, got {_shown(value)}") from None
    return number


def _flag(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise ComponentError(f"{what} must be true or false, got {_shown(value)}")
    return value


def _stored_options(marker: Dict[str, Any], component: str) -> Optional[Dict[str, Any]]:
    """Options the component was applied with before, or None if nothing records it."""
    applied = marker.get("components")
    stored = applied.get(component) if isinstance(applied, dict) else None
    return stored if isinstance(stored, dict) else None


def _block_states(project: Path, files: List[str], component: str) -> Dict[str, bool]:
    """Which of the files already hold the component's block. Refuses damaged markers."""
    states = {}
    for relative in files:
        try:
            states[relative] = projects.has_block(projects.read_text(project, relative), component)
        except projects.BlockError as exc:
            raise ComponentError(
                f"{relative}: {exc}",
                "Repair the two marker lines by hand (or delete what is left of the block "
                "and both markers), then apply the component again.") from None
    return states


def _python_keys(project: Path, relative: str, variable: str, component: str) -> List[str]:
    """Keys the analyst's own code gives a table: the component's block is left out."""
    text = projects.read_text(project, relative)
    return projects.defined_keys(text, variable, relative, projects.block_lines(text, component))


def _yaml_keys(project: Path, relative: str, component: str) -> List[str]:
    """Top-level entries of a config outside the component's block."""
    text = projects.read_text(project, relative)
    return projects.top_level_keys(text, relative, projects.block_lines(text, component))


# --------------------------------------------------------------------------- #
# lepton jets
# --------------------------------------------------------------------------- #

LEPTON_JET_OPTIONS = ["name", "sources", "radius", "carry", "isolation_jets", "invmass_max"]


def _lepton_jet_sources(available: List[str], marker: Dict[str, Any],
                        requested: Any) -> Dict[str, Dict[str, Any]]:
    shape = "sources must be a list of object names, or {name: {'mass': GeV}}"
    if requested is None:
        objects = marker.get("objects") if isinstance(marker.get("objects"), dict) else {}
        requested = [name for name, coll in objects.items()
                     if coll in ("Muon", "Electron", "Photon") and name in available]
        if not requested:
            requested = [name for name in ("muons", "electrons", "photons") if name in available]
        if not requested:
            raise ComponentError(
                "no default lepton-jet sources found in this project",
                f"Pass options={{'sources': [...]}} choosing from the defined objects: {available}")
    if isinstance(requested, str):
        requested = [requested]
    if isinstance(requested, dict):
        sources = dict(requested)
    elif isinstance(requested, (list, tuple)):
        if not all(isinstance(name, str) for name in requested):
            raise ComponentError(shape, "For example ['muons', 'electrons'] or "
                                        "{'dsaMuons': {'mass': 0.105658}}.")
        sources = {name: {} for name in requested}
    else:
        raise ComponentError(shape)
    if not sources:
        raise ComponentError("lepton jets need at least one source",
                             f"Choose from the defined objects: {available}")
    checked: Dict[str, Dict[str, Any]] = {}
    for name, cfg in sources.items():
        if not isinstance(name, str):
            raise ComponentError(shape)
        if name not in available:
            raise ComponentError(
                f"lepton-jet source '{name}' is not an object of this analysis",
                f"Defined objects: {available}. Add the object to definitions/objects.py first.")
        if name in COORDINATE_NAMES | LEPTON_JET_FIELDS | {"src", "idx"} or name.endswith("_n"):
            raise ComponentError(
                f"'{name}' cannot be a lepton-jet source: each source becomes a field of the "
                "lepton jets, and this name clashes with one they already have",
                "Rename the object in definitions/objects.py.")
        cfg = {} if cfg is None else cfg
        if not isinstance(cfg, dict) or set(map(str, cfg)) - {"mass"}:
            raise ComponentError(
                f"source '{name}': only {{'mass': GeV}} can be set, got {_shown(cfg)}")
        checked[name] = {}
        if cfg.get("mass") is not None:
            mass = _finite(cfg["mass"], f"the mass of source '{name}'")
            if mass < 0:
                raise ComponentError(f"the mass of source '{name}' cannot be negative")
            checked[name]["mass"] = mass
    return checked


def _same_particle_sources(sources: Dict[str, Any], marker: Dict[str, Any]) -> List[List[str]]:
    """Groups of sources read from collections of the same kind of particle.

    A particle stored in two of them (a muon that is both a PF and a DSA muon) is
    clustered twice. Only objects whose collection the project records are compared.
    """
    objects = marker.get("objects") if isinstance(marker.get("objects"), dict) else {}
    kinds: Dict[str, List[str]] = {}
    for name in sources:
        collection = objects.get(name)
        if not isinstance(collection, str):
            continue
        for kind in ("muon", "electron", "photon"):
            if kind in collection.lower():
                kinds.setdefault(kind, []).append(f"{name} ({collection})")
                break
    return [names for names in kinds.values() if len(names) > 1]


def _lepton_jet_carry(carry: Any) -> Dict[str, Any]:
    shape = "carry must be {field: fill value} or a list of field names"
    if isinstance(carry, str):
        carry = [carry]
    if isinstance(carry, (list, tuple)):
        if not all(isinstance(field, str) for field in carry):
            raise ComponentError(shape, "For example {'charge': 0} or ['charge', 'dxy'].")
        carry = {field: 0 for field in carry}
    if not isinstance(carry, dict):
        raise ComponentError(shape)
    checked: Dict[str, Any] = {}
    for field, fill in carry.items():
        if branch_name_problem(field):
            raise ComponentError(f"{_shown(field)} cannot be carried: it is not a field name")
        if field in COORDINATE_NAMES or field in ("src", "idx"):
            raise ComponentError(f"'{field}' cannot be carried: the name is reserved")
        if isinstance(fill, bool):
            checked[field] = fill
        elif isinstance(fill, int):
            if abs(fill) >= 2 ** 31:
                raise ComponentError(f"the fill value of carried field '{field}' is too large")
            checked[field] = fill
        else:
            checked[field] = _finite(fill, f"the fill value of carried field '{field}'")
    return checked


def _lepton_jet_options(project: Path, package: str, marker: Dict[str, Any],
                        options: Dict[str, Any]) -> Dict[str, Any]:
    """The complete, validated options of the lepton-jet component."""
    defined = projects.defined_objects(project, package, ignore_block="lepton_jets")
    available = defined["primary"] + defined["derived"]

    name = options.get("name", "ljs")
    problem = object_name_problem(name)
    if problem:
        raise ComponentError(f"{_shown(name)} cannot be the lepton-jet object name: {problem}")
    sources = _lepton_jet_sources(available, marker, options.get("sources"))
    if name in sources:
        raise ComponentError(f"'{name}' is both the lepton-jet name and one of its sources")
    if name in available:
        raise ComponentError(f"'{name}' is already an object of this analysis",
                             "Pass options={'name': ...} with a name that is not taken.")
    for other in available:
        if singular(other) == singular(name):
            raise ComponentError(
                f"'{name}' and the object '{other}' would both name their histograms "
                f"'{singular(name)}_...'", "Pass options={'name': ...} with a different name.")

    carry = _lepton_jet_carry(options.get("carry", {"charge": 0}))

    if "isolation_jets" in options:
        jets = options["isolation_jets"]
        if jets is False or (isinstance(jets, str) and jets.strip().lower() in _NONE_WORDS):
            jets = None
    else:
        # only a primary object by default: it exists whatever the order of the definitions
        jets = "jets" if "jets" in defined["primary"] and "jets" not in sources else None
    if jets is not None and (not isinstance(jets, str) or jets not in available):
        raise ComponentError(
            f"isolation_jets {_shown(jets)} is not an object of this analysis",
            f"Defined objects: {available}. Pass isolation_jets=null to skip the isolation.")
    return {
        "name": name,
        "sources": sources,
        "radius": _finite(options.get("radius", 0.4), "radius", positive=True),
        "carry": carry,
        "isolation_jets": jets,
        "invmass_max": _finite(options.get("invmass_max", 1000), "invmass_max", positive=True),
    }


def apply_lepton_jets(project: Path, package: str, marker: Dict[str, Any],
                      options: Dict[str, Any], overwrite: bool,
                      writes: projects.Writes) -> Dict[str, Any]:
    _unknown_options(options, LEPTON_JET_OPTIONS, "lepton_jets")
    result = _result("lepton_jets")
    stored = _stored_options(marker, "lepton_jets")
    files = {
        "objects": f"{package}/definitions/objects.py",
        "cuts": f"{package}/definitions/cuts.py",
        "hists": f"{package}/definitions/hists.py",
        "selections": f"{package}/configs/selections.yaml",
        "collections": f"{package}/configs/hist_collections.yaml",
        "requirements": "requirements.txt",
    }
    existing = _block_states(project, list(files.values()), "lepton_jets")
    in_place = sorted(relative for relative, has in existing.items() if has)
    if in_place and stored is None and not overwrite:
        # the blocks would be kept, and whatever got recorded could not be known to match them
        raise ComponentError(
            f"lepton_jets blocks are already in {in_place}, and the project's record "
            f"({projects.MARKER}) does not say which options they were written with: nothing "
            "was changed",
            "Pass overwrite=true to regenerate the blocks from the options given now (edits "
            "made inside them are lost), or leave them as they are.")
    # options given now are changes to the ones the component was applied with
    resolved = _lepton_jet_options(project, package, marker, {**(stored or {}), **options})
    if in_place and not overwrite and resolved != stored:
        changed = sorted(key for key in resolved if resolved[key] != (stored or {}).get(key))
        raise ComponentError(
            f"lepton_jets is already part of this framework with other options ({changed} "
            "differ), and its blocks are kept unless overwrite is set: nothing was changed",
            "Pass overwrite=true to regenerate the blocks with the new options (edits made "
            "inside them are lost), or change the constants in definitions/objects.py by hand.")

    name, sources, jets = resolved["name"], resolved["sources"], resolved["isolation_jets"]
    stem = singular(name)
    const = name.upper()
    channel = f"baseline_2{name}"
    hist_names = [f"{stem}_n", f"{stem}_pt", f"{stem}_eta_phi", f"{stem}_n_constituents",
                  f"{stem}_dRSpread"]
    if jets is not None:
        hist_names.append(f"{stem}_isolation")
    mass_hist = f"{stem}_{stem}_invmass"
    hist_names.append(mass_hist)
    evt_cut_names = [f">=1 {name}", f">=2 {name}", f"leading {name} |dphi| > 2"]

    # names this component is about to define must not already mean something else
    taken = []
    if channel in _yaml_keys(project, files["selections"], "lepton_jets"):
        taken.append(f"selection '{channel}' (configs/selections.yaml)")
    if f"{stem}_base" in _yaml_keys(project, files["collections"], "lepton_jets"):
        taken.append(f"histogram collection '{stem}_base' (configs/hist_collections.yaml)")
    own_hists = _python_keys(project, files["hists"], "hist_defs", "lepton_jets")
    taken += [f"histogram '{hist}' (definitions/hists.py)" for hist in hist_names
              if hist in own_hists]
    own_evt_cuts = _python_keys(project, files["cuts"], "evt_cut_defs", "lepton_jets")
    taken += [f"event cut '{cut}' (definitions/cuts.py)" for cut in evt_cut_names
              if cut in own_evt_cuts]
    if name in _python_keys(project, files["cuts"], "obj_cut_defs", "lepton_jets"):
        taken.append(f"object cuts for '{name}' (definitions/cuts.py)")
    if taken:
        raise ComponentError(
            "these names are already in use outside the lepton_jets block: " + "; ".join(taken),
            "Pass options={'name': ...} with another name for the lepton jets, or rename the "
            "existing entries.")

    # ---- everything is decided: write ---------------------------------------
    written, kept = projects.copy_template("components/lepton_jets", project, package, writes,
                                           overwrite=overwrite)
    result["files_written"] += written
    result["files_kept"] += kept

    source_names = ", ".join(sources)
    objects_block = f'''
# Lepton jets: anti-kT clusters of the selected {source_names}.
# They are built from the sources after each channel's object cuts, so tightening a
# source's cuts in a selection changes the lepton jets of that selection.
from {package}.tools.lepton_jets import build_lepton_jets  # noqa: E402

{const}_SOURCES = {py_literal(sources)}  # name -> {{"mass": GeV}} to force a mass
{const}_RADIUS = {num(resolved["radius"])}
{const}_CARRY = {py_literal(resolved["carry"])}  # constituent fields kept, and the fill where a source lacks one
{const}_ISOLATION_JETS = {py_literal(jets)}  # jet object used for the isolation, or None

derived_objs[{q(name)}] = lambda objs: build_lepton_jets(
    objs, {const}_SOURCES, distance_param={const}_RADIUS, carry={const}_CARRY,
    jets={const}_ISOLATION_JETS)
'''
    iso_cut = ('    "isolation < 0.2": lambda objs, obj: obj.isolation < 0.2,\n'
               if jets is not None else "")
    cuts_block = f'''
# The thresholds below are starting values written by the lepton_jets component, not
# this analysis's cuts: replace them.
import awkward as ak  # noqa: E402,F811

from {package}.tools.lepton_jets import leading_pair_dphi  # noqa: E402

obj_cut_defs[{q(name)}] = {{
    "pT > 10 GeV": lambda objs, obj: obj.pt > 10,
    "pT > 30 GeV": lambda objs, obj: obj.pt > 30,
    "|eta| < 2.4": lambda objs, obj: abs(obj.eta) < 2.4,
    ">=2 constituents": lambda objs, obj: obj.n_constituents >= 2,
{iso_cut}}}
evt_cut_defs.update({{
    {q(evt_cut_names[0])}: lambda objs: ak.num(objs[{q(name)}], axis=1) >= 1,
    {q(evt_cut_names[1])}: lambda objs: ak.num(objs[{q(name)}], axis=1) >= 2,
    {q(evt_cut_names[2])}: lambda objs: ak.fill_none(
        leading_pair_dphi(objs[{q(name)}]) > 2.0, False),
}})
'''
    iso_hist = ""
    if jets is not None:
        iso_hist = (f'    {q(stem + "_isolation")}: obj_attr({q(name)}, "isolation", nbins=50, xmin=0, '
                    f'xmax=2,\n                             label="Lepton jet isolation"),\n')
    hists_block = f'''
import awkward as ak  # noqa: E402,F811
import hist  # noqa: E402,F811

from {package}.tools import histogram as h  # noqa: E402,F811

obj_labels[{q(name)}] = "Lepton jet"
hist_defs.update({{
    {q(stem + "_n")}: obj_attr({q(name)}, "n"),
    {q(stem + "_pt")}: obj_attr({q(name)}, "pt", xmax=500),
    {q(stem + "_eta_phi")}: obj_eta_phi({q(name)}),
    {q(stem + "_n_constituents")}: obj_attr({q(name)}, "n_constituents", nbins=10, xmin=0, xmax=10,
                                 label="Lepton jet constituents"),
    {q(stem + "_dRSpread")}: obj_attr({q(name)}, "dRSpread", nbins=50, xmin=0, xmax=1,
                           label="Largest dR between lepton jet constituents"),
{iso_hist}    {q(mass_hist)}: h.Histogram(
        [
            h.Axis(hist.axis.Regular(100, 0, {num(resolved["invmass_max"])}, name={q(mass_hist)},
                                     label="Invariant mass of the two leading lepton jets (GeV)"),
                   lambda objs, mask: objs[{q(name)}][mask, :2].sum().mass),
        ],
        evt_mask=lambda objs: ak.num(objs[{q(name)}], axis=1) > 1,
    ),
}})
'''
    # anchors count only where yaml would see them: not in a comment, and not in the
    # component's own block
    own_selections = projects.strip_block(projects.read_text(project, files["selections"]),
                                          "lepton_jets")
    has_obj_anchor = bool(re.search(r"^[^#\n]*&object_cuts\b", own_selections, flags=re.M))
    has_evt_anchor = bool(re.search(r"^[^#\n]*&event_cuts\b", own_selections, flags=re.M))
    lines = [f"# Two lepton jets on top of the baseline. The sources ({source_names}) are cut",
             "# first and then clustered; the lepton jets take object cuts of their own.",
             "# The cut values are starting values: replace them.",
             f"{channel}:", "  obj_cuts:"]
    if has_obj_anchor:
        lines.append("    <<: *object_cuts")
    lines += [f"    {name}:", '      - "pT > 30 GeV"', '      - "|eta| < 2.4"', "  evt_cuts:"]
    if has_evt_anchor:
        lines.append("    - *event_cuts")
    lines.append(f"    - {q(evt_cut_names[1])}")
    selections_block = "\n".join(lines)
    collections_block = "\n".join([f"{stem}_base: &{stem}_base"] + [f"  - {q(h)}" for h in hist_names])
    requirements_block = "fastjet\nvector"

    blocks = [
        (files["objects"], objects_block, 2),
        (files["cuts"], cuts_block, 2),
        (files["hists"], hists_block, 2),
        (files["selections"], selections_block, 1),
        (files["collections"], collections_block, 1),
        (files["requirements"], requirements_block, 1),
    ]
    for relative, body, gap in blocks:
        result["blocks"][relative] = projects.edit_block(
            project, relative, "lepton_jets", body, writes, overwrite=overwrite, gap=gap)
    result["options"] = resolved
    result["channel"] = channel
    result["hist_collection"] = f"{stem}_base"
    if not has_obj_anchor or not has_evt_anchor:
        result["notes"].append(
            "configs/selections.yaml no longer has the &object_cuts / &event_cuts anchors, so "
            f"'{channel}' was written without the baseline cuts: add the cuts it should share.")
    if in_place and not overwrite:
        result["notes"].append("the component was already in place: its blocks were kept as "
                               "they are (overwrite=true regenerates them)")
    derived = projects.defined_objects(project, package, ignore_block="lepton_jets")["derived"]
    late = [obj for obj in list(sources) + ([jets] if jets else []) if obj in derived]
    if late:
        result["notes"].append(
            f"{late} are derived objects: objects are built from top to bottom, so they must "
            "be defined above the lepton_jets block in definitions/objects.py")
    for names in _same_particle_sources(sources, marker):
        result["notes"].append(
            f"the sources {', '.join(names)} can hold the same particle: one stored in both "
            "is clustered twice, which shifts the lepton jets' momenta and masses. Before "
            "relying on the lepton jets, remove from one what the other holds with an object "
            "cut that the lepton-jet channels list (the framework skill's components "
            "reference shows one)")
    result["notes"].append("the lepton-jet cut values and the selection are starting values, "
                           "not this analysis's: replace them")
    result["next_steps"] = [
        "pip install fastjet vector  (in the analysis environment)",
        f"python -m {package}.tools.check --sample <NAME> --channels {channel} --hists {stem}_base",
        f"tune the source cuts and the '{name}' cuts in configs/selections.yaml; the isolation "
        "and its inputs are described in tools/lepton_jets.py",
    ]
    return result


# --------------------------------------------------------------------------- #
# scale-out
# --------------------------------------------------------------------------- #

def render_scaleout_notebook(package: str, samples: List[str]) -> str:
    sample_list = ", ".join(q(s) for s in samples[:3]) if samples else q("SAMPLE_NAME")
    runner = """runner = processor.Runner(executor={executor},
                          schema=AnalysisSchema, chunksize=50_000, skipbadfiles=True,
                          metadata_cache={{}})
out = runner(fileset, processor_instance=p, treename=TREE_NAME)"""
    cells = [
        _cell("markdown", f"""
# Scaling out

The processor does not change with scale; the executor does. This notebook runs the
same job three ways. Workers must be able to import `{package}`: local processes share
this environment, and remote workers are sent the package directory as it is when the
client is made (make a new client, or call `scaleout.upload_package(client)`, after
editing the package).

`skipbadfiles=True` keeps a long run alive past input files that cannot be read. What
was skipped is not in the output: compare `n_evts` below with what each sample should
have. `metadata_cache={{}}` stops coffea from reusing the sample metadata (`is_data`,
`year`, `skim_factor`) it saw for a file earlier in this session.
"""),
        _cell("code", f"""
from coffea import processor
from {package} import TREE_NAME
from {package}.tools import scaleout, utilities
from {package}.tools.processor import AnalysisProcessor, list_channels
from {package}.tools.schema import AnalysisSchema

samples = [{sample_list}]
fileset = utilities.make_fileset(samples)
p = AnalysisProcessor(list_channels()[:2], ["base"])
"""),
        _cell("markdown", "## Local processes"),
        _cell("code", runner.format(executor="processor.FuturesExecutor(workers=4)")),
        _cell("markdown", "## A local dask cluster"),
        _cell("code", "client = scaleout.make_local_client(n_workers=4)\n"
              + runner.format(executor="processor.DaskExecutor(client=client)")
              + "\nclient.close()"),
        _cell("markdown", """
## HTCondor at the LPC

Needs a VOMS proxy, `pip install "htcondor<25" git+https://github.com/CoffeaTeam/lpcjobqueue.git`,
and a worker image whose coffea matches this environment (found automatically, or pass
`image=`). To watch the dashboard, forward its port: `ssh -L 8787:localhost:8787 ...`.
"""),
        _cell("code", 'cluster, client = scaleout.make_lpc_client(min_workers=1, max_workers=10, '
                      'memory="4GB")\nprint("dashboard:", cluster.dashboard_link)\n'
              + runner.format(executor="processor.DaskExecutor(client=client)")
              + "\ncluster.close()"),
        _cell("code", """
for sample in out:
    print(sample, out[sample]["metadata"]["n_evts"], "events,", len(out[sample]["warnings"]), "warning(s)")
"""),
    ]
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 4,   # 4.5 would require an id on every cell
    }
    return json.dumps(notebook, indent=1) + "\n"


def apply_scaleout(project: Path, package: str, marker: Dict[str, Any],
                   options: Dict[str, Any], overwrite: bool,
                   writes: projects.Writes) -> Dict[str, Any]:
    _unknown_options(options, [], "scaleout")
    result = _result("scaleout")
    _block_states(project, ["requirements.txt"], "scaleout")
    written, kept = projects.copy_template("components/scaleout", project, package, writes,
                                           overwrite=overwrite)
    result["files_written"] += written
    result["files_kept"] += kept
    notebook = f"{package}/test_notebooks/scale_out.ipynb"
    listed = marker.get("samples") if isinstance(marker.get("samples"), list) else []
    samples = [s["name"] for s in listed if isinstance(s, dict) and isinstance(s.get("name"), str)]
    if projects.write_file(project, notebook, render_scaleout_notebook(package, samples), writes,
                           overwrite=overwrite):
        result["files_written"].append(notebook)
    else:
        result["files_kept"].append(notebook)
    requirements = "\n".join([
        "coffea[dask]     # distributed and the dashboard, in the versions this coffea wants",
        "dask[dataframe]  # coffea's DaskExecutor imports dask.dataframe",
        "# HTCondor at the LPC:",
        '# htcondor<25',
        "# git+https://github.com/CoffeaTeam/lpcjobqueue.git",
    ])
    result["blocks"]["requirements.txt"] = projects.edit_block(
        project, "requirements.txt", "scaleout", requirements, writes, overwrite=overwrite, gap=1)
    result["options"] = {}
    result["next_steps"] = [
        "pip install 'coffea[dask]' 'dask[dataframe]'  (in the analysis environment)",
        f"local test: python -m {package}.scripts.run_analysis --samples <NAME> --channels "
        "baseline --executor futures --workers 4",
        f"open {notebook} for the dask and LPC HTCondor variants",
    ]
    result["notes"].append(
        "tools/scaleout.py ships the package directory to remote workers. Code that runs "
        "inside a task must therefore live in the package, not next to a notebook in studies/.")
    return result


# --------------------------------------------------------------------------- #
# custom schema
# --------------------------------------------------------------------------- #

SCHEMA_OPTIONS = ["mixins", "cross_references", "nested_items", "hidden_branches",
                  "constant_fields", "hide_duplicate_momenta", "reset"]
# behaviours coffea registers for NanoAOD (coffea.nanoevents.methods.nanoaod.behavior)
KNOWN_BEHAVIOURS = {
    "NanoCollection", "PtEtaPhiMCollection", "PtEtaPhiMCandidate", "PtEtaPhiMLorentzVector",
    "MissingET", "Vertex", "SecondaryVertex", "Muon", "Electron", "Photon", "Tau", "Jet",
    "FatJet", "GenParticle", "LowPtElectron", "FsrPhoton", "GenVisTau", "PFCand",
    "AssociatedPFCand", "AssociatedSV",
}
# vector behaviours that are not collections: no cross-references into them
NOT_COLLECTIONS = {"PtEtaPhiMCandidate", "PtEtaPhiMLorentzVector"}
# table in the generated tools/schema.py -> the option it holds
SCHEMA_TABLES = {
    "EXTRA_MIXINS": "mixins",
    "CROSS_REFERENCES": "cross_references",
    "NESTED_ITEMS": "nested_items",
    "CONSTANT_FIELDS": "constant_fields",
    "HIDDEN_BRANCHES": "hidden_branches",
    "HIDE_DUPLICATE_MOMENTA": "hide_duplicate_momenta",
}


def _schema_request(given: Dict[str, Any]) -> Dict[str, Any]:
    """Check the shape of the schema options that were given (names, lists, numbers)."""
    checked: Dict[str, Any] = {}

    def mapping(key):
        value = given.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ComponentError(f"{key} must be a mapping, got {type(value).__name__}")
        return value

    def branch(name, what):
        if branch_name_problem(name):
            raise ComponentError(f"{_shown(name)} is not a {what}",
                                 "Names are made of letters, digits and underscores.")
        return name

    checked["mixins"] = {}
    for collection, behaviour in mapping("mixins").items():
        branch(collection, "collection name")
        if "_" in collection:
            raise ComponentError(
                f"'{collection}' cannot be given a behaviour: a NanoAOD collection is named by "
                "what stands before the first underscore of its branches, so its name has none",
                f"Use '{collection.split('_', 1)[0]}' if the branches are named "
                f"'{collection.split('_', 1)[0]}_...'.")
        checked["mixins"][collection] = branch(behaviour, "behaviour name")
    checked["cross_references"] = {}
    for name, target in mapping("cross_references").items():
        if branch_name_problem(name) or "_" not in name:
            raise ComponentError(f"cross-reference {_shown(name)} is not a branch name",
                                 "Use the full name of the index branch, e.g. 'Muon_jetIdx'.")
        if branch_name_problem(target):
            raise ComponentError(
                f"cross-reference '{name}' needs the name of the collection it points into")
        checked["cross_references"][name] = target
    checked["nested_items"] = {}
    for name, indices in mapping("nested_items").items():
        if branch_name_problem(name) or "_" not in name or not name.endswith("G"):
            raise ComponentError(
                f"nested item {_shown(name)} should be named <Collection>_<something>IdxG",
                "For example {'Muon_dsaIdxG': ['Muon_dsaMatch1idx', 'Muon_dsaMatch2idx']}.")
        if isinstance(indices, str):
            indices = [indices]
        if not isinstance(indices, (list, tuple)) or not indices:
            raise ComponentError(f"nested item '{name}' needs a list of index branches")
        checked["nested_items"][name] = [branch(index, "branch name") for index in indices]
    hidden = given.get("hidden_branches") or []
    if isinstance(hidden, str):
        hidden = [hidden]
    if not isinstance(hidden, (list, tuple)):
        raise ComponentError("hidden_branches must be a list of branch names")
    checked["hidden_branches"] = [branch(name, "branch name") for name in hidden]
    checked["constant_fields"] = {}
    for name, value in mapping("constant_fields").items():
        if branch_name_problem(name) or "_" not in name:
            raise ComponentError(f"constant field {_shown(name)} should be <Collection>_<field>")
        checked["constant_fields"][name] = _finite(value, f"constant field '{name}'")
    if given.get("hide_duplicate_momenta") is not None:
        checked["hide_duplicate_momenta"] = _flag(given["hide_duplicate_momenta"],
                                                  "hide_duplicate_momenta")
    return checked


def _default_schema_options() -> Dict[str, Any]:
    return {**_schema_request({}), "hide_duplicate_momenta": True}


def _merge_schema_options(base: Dict[str, Any], given: Dict[str, Any]) -> Dict[str, Any]:
    """Validated options ``base``, updated with the (raw) ones given now."""
    merged = {key: (dict(value) if isinstance(value, dict) else list(value)
                    if isinstance(value, list) else value) for key, value in base.items()}
    merged.setdefault("hide_duplicate_momenta", True)
    given = _schema_request(given)
    for key in ("mixins", "cross_references", "constant_fields", "nested_items"):
        merged[key].update(given[key])
    for name in given["hidden_branches"]:
        if name not in merged["hidden_branches"]:
            merged["hidden_branches"].append(name)
    if "hide_duplicate_momenta" in given:
        merged["hide_duplicate_momenta"] = given["hide_duplicate_momenta"]
    return merged


def _resolve_nested_items(options: Dict[str, Any]) -> None:
    """Bring the index branches of the nested items to their plain names, in place.

    A nested item combines global indices, which only cross-references have. An index
    may be given by the name of its branch or by that of its global index (the same
    with a "G" appended); it is stored as the former.
    """
    references = options["cross_references"]
    for name, indices in options["nested_items"].items():
        if name[:-1] in references:
            raise ComponentError(
                f"nested item '{name}' has the very name the cross-reference '{name[:-1]}' "
                "gets as its global index",
                "Give the nested item another name, e.g. '<Collection>_<something>IdxG'.")
        resolved = []
        for index in indices:
            if index not in references and index.endswith("G") and index[:-1] in references:
                index = index[:-1]
            if index not in references:
                raise ComponentError(
                    f"nested item '{name}' uses '{index}', which is not a cross-reference",
                    f"Add it to cross_references too, e.g. {{'{index}': '<TargetCollection>'}} "
                    "(also when it is one coffea already knows: repeating it does no harm).")
            resolved.append(index)
        options["nested_items"][name] = resolved


def schema_options_in(text: str) -> Optional[Dict[str, Any]]:
    """The options a generated tools/schema.py holds in its tables.

    None for a file without (all of) the tables, such as the plain schema the scaffold
    writes, and for one whose tables cannot be read as plain values.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    found: Dict[str, Any] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in SCHEMA_TABLES):
            try:
                found[SCHEMA_TABLES[node.targets[0].id]] = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return None
    if set(found) != set(SCHEMA_TABLES.values()):
        return None
    try:
        if isinstance(found["nested_items"], dict):
            # the file lists global indices: the index branch plus "G"
            found["nested_items"] = {
                name: [index[:-1] if isinstance(index, str) and index.endswith("G") else index
                       for index in indices] if isinstance(indices, (list, tuple)) else indices
                for name, indices in found["nested_items"].items()}
        options = _schema_request(found)
    except ComponentError:
        return None
    options.setdefault("hide_duplicate_momenta", True)
    return options


def _dict_lines(mapping: Dict[str, Any], indent: str = "    ") -> str:
    return "".join(f"{indent}{q(key)}: {py_literal(value)},\n" for key, value in mapping.items())


_SCHEMA_TEMPLATE = '''"""The NanoEvents schema used to read the input files.

AnalysisSchema is coffea's NanoAODSchema, made tolerant of skims and private
productions and extended for this analysis through the tables below:

EXTRA_MIXINS       collection -> behaviour, for collections coffea does not know.
                   "PtEtaPhiMCollection" gives Lorentz-vector behaviour and needs pt,
                   eta, phi and mass.
CROSS_REFERENCES   index branch -> collection it points into. Each becomes a global
                   index (branch name + "G") that survives slicing the events.
NESTED_ITEMS       new field -> global indices of several fixed slots (up to five
                   matches, say), combined into one list per object.
CONSTANT_FIELDS    branch to synthesise -> value, e.g. a fixed mass for a jagged
                   collection stored without one. Needs coffea 2025.12 or newer; on
                   older versions use utilities.as_lorentz(..., mass=...) in
                   definitions/objects.py.
HIDDEN_BRANCHES    branches that are never read.

When a collection stores its momentum both as (pt, eta, phi) and as (px, py, pz),
only the polar set is read (HIDE_DUPLICATE_MOMENTA): coffea accepts one
representation per vector collection, and px, py, pz stay available as computed
attributes.

Index branches stored as floating-point numbers, as some private productions do, are
read as integers (see _accept_any_index_type).

Use ``follow`` to go through a cross-reference or nested item from the definitions:

    follow(objs["muons"], "dsaIdxG", "DSAMuon")   # per muon, the DSA muons it points to

This file is generated from its tables. Edit the tables freely: the schema component
of the framework tools reads them back and adds to them. Other edits to this file make
that tool leave it alone until it is told to overwrite it.
"""

import warnings

import awkward as ak
import numpy as np
from coffea.nanoevents import NanoAODSchema, transforms

EXTRA_MIXINS = {
@@MIXINS@@}

CROSS_REFERENCES = {
@@CROSS_REFERENCES@@}

NESTED_ITEMS = {
@@NESTED_ITEMS@@}

CONSTANT_FIELDS = {
@@CONSTANT_FIELDS@@}

HIDDEN_BRANCHES = @@HIDDEN_BRANCHES@@

HIDE_DUPLICATE_MOMENTA = @@HIDE_DUPLICATE_MOMENTA@@


def _accept_any_index_type(local2global):
    """Wrap coffea's local2global so that it takes index branches of any numeric type.

    coffea turns the per-event index of a cross-reference into a global one and
    insists on the result being 64-bit integers, which it is not when the branch is
    stored as floats (-1.0, 0.0, 1.0, ...). The index is converted first, and coffea's
    own function does the rest.
    """
    def int_index_local2global(stack):
        target_offsets = stack.pop()
        index = stack.pop()
        stack.append(ak.values_astype(index, np.int64))
        stack.append(target_offsets)
        local2global(stack)

    int_index_local2global.original = local2global
    return int_index_local2global


# coffea looks its transforms up by name in that module each time one is needed. The
# replacement is installed once per process, wherever this module is first imported
# (dask workers import it when they receive the schema), and applies to every schema.
# Reloading this module leaves the one that is installed in place.
if not hasattr(transforms.local2global, "original"):
    transforms.local2global = _accept_any_index_type(transforms.local2global)


def _constant_items():
    """CONSTANT_FIELDS in the form coffea expects (empty on versions without the feature)."""
    make_form = getattr(transforms, "full_like_from_offsets_form", None)
    base = dict(getattr(NanoAODSchema, "full_like_items", {}))
    if not CONSTANT_FIELDS:
        return base
    if make_form is None:
        warnings.warn(
            "CONSTANT_FIELDS needs coffea 2025.12 or newer and is ignored by this version: "
            "use utilities.as_lorentz(..., mass=...) in definitions/objects.py instead",
            RuntimeWarning,
        )
        return base
    for name, value in CONSTANT_FIELDS.items():
        base[name] = (make_form, ("o" + name.split("_", 1)[0], float(value)))
    return base


class AnalysisSchema(NanoAODSchema):
    """NanoAODSchema for the files of this analysis."""

    warn_missing_crossrefs = False
    error_missing_event_ids = False

    mixins = {**NanoAODSchema.mixins, **EXTRA_MIXINS}
    all_cross_references = {**NanoAODSchema.all_cross_references, **CROSS_REFERENCES}
    nested_items = {**NanoAODSchema.nested_items, **NESTED_ITEMS}
    full_like_items = _constant_items()

    hidden_branches = tuple(HIDDEN_BRANCHES)
    hide_duplicate_momenta = HIDE_DUPLICATE_MOMENTA

    def _build_collections(self, field_names, input_contents):
        hidden = set(self.hidden_branches)
        names = set(field_names)
        if self.hide_duplicate_momenta:
            for name in field_names:
                collection, _, field = name.partition("_")
                # only collections that get a vector behaviour are affected: for the
                # others the branches are plain columns and nothing would recompute them
                if field != "pt" or collection not in self.mixins:
                    continue
                if f"{collection}_phi" in names:
                    hidden.update((f"{collection}_px", f"{collection}_py"))
                if f"{collection}_eta" in names:
                    hidden.add(f"{collection}_pz")
        hidden &= names
        if hidden:
            kept = [(n, c) for n, c in zip(field_names, input_contents) if n not in hidden]
            field_names = [n for n, _ in kept]
            input_contents = [c for _, c in kept]
        return super()._build_collections(field_names, input_contents)


def follow(collection, index_field, target):
    """The objects of ``target`` that the objects of ``collection`` point to.

    collection: objects read through this schema, straight from the events
        (``evts.Muon``) or after any cuts and ordering (``objs["muons"]``)
    index_field: one of their global-index fields: a cross-reference (``"jetIdxG"``:
        one object each, missing where the index is negative) or a nested item
        (``"dsaIdxG"``: a list each, with one slot per index branch, missing where a
        slot is empty)
    target: the collection pointed into, by its name in the file (``"DSAMuon"``) or as
        read from the events (``evts.DSAMuon``)

    What comes back are objects of the collection as it is in the file. Object cuts
    that a selection applies to an analysis object made from the same collection are
    not applied to them: cut on their fields where that matters.
    """
    if isinstance(target, str):
        name = target
    else:
        name = target.layout.purelist_parameter("collection_name")
        if name is None:
            raise ValueError(
                "follow: the target is no longer a collection as read from the events "
                '(it was rebuilt, by as_lorentz for example): pass its name instead, as in '
                'follow(objs["muons"], "dsaIdxG", "DSAMuon")')
    attrs = getattr(collection, "attrs", None) or {}
    if "@original_array" in attrs:
        events = attrs["@original_array"]
    elif "@events_factory" in attrs:
        events = attrs["@events_factory"].events()
    else:
        raise ValueError("follow: the collection must be one read through the schema; this one "
                         "does not know the events it came from")
    # the global index counts in the collection as it was read, whatever was cut since
    return events[name]._apply_global_index(collection[index_field])
'''


def render_schema(options: Dict[str, Any]) -> str:
    """The customised tools/schema.py."""
    nested = {name: [index + "G" for index in indices]
              for name, indices in options["nested_items"].items()}
    tables = {
        "@@MIXINS@@": _dict_lines(options["mixins"]),
        "@@CROSS_REFERENCES@@": _dict_lines(options["cross_references"]),
        "@@NESTED_ITEMS@@": _dict_lines(nested),
        "@@CONSTANT_FIELDS@@": _dict_lines(options["constant_fields"]),
        "@@HIDDEN_BRANCHES@@": py_literal(list(options["hidden_branches"])),
        "@@HIDE_DUPLICATE_MOMENTA@@": py_literal(bool(options["hide_duplicate_momenta"])),
    }
    return re.sub("|".join(re.escape(token) for token in tables),
                  lambda match: tables[match.group(0)], _SCHEMA_TEMPLATE)


def apply_schema(project: Path, package: str, marker: Dict[str, Any],
                 options: Dict[str, Any], overwrite: bool,
                 writes: projects.Writes) -> Dict[str, Any]:
    _unknown_options(options, SCHEMA_OPTIONS, "schema")
    result = _result("schema")
    options = dict(options)
    reset = _flag(options.pop("reset", False) or False, "reset")
    _schema_request(options)              # refuse a bad request before looking at the file

    relative = f"{package}/tools/schema.py"
    target = project / relative
    pristine = projects.render_text(
        (projects.TEMPLATE_DIR / "core" / PLACEHOLDER_PACKAGE / "tools" / "schema.py")
        .read_text(encoding="utf8"), package)
    current = projects.read_text(project, relative) if target.is_file() else None
    on_disk = schema_options_in(current) if current is not None else None
    # a generated file whose tables may have been edited, but nothing else
    generated = on_disk is not None and render_schema(on_disk) == current
    if current is not None and current != pristine and not generated and not overwrite:
        result["files_kept"].append(relative)
        result["applied"] = False
        result["record"] = False          # the marker keeps describing what is on disk
        result["options"] = on_disk if on_disk is not None else (_stored_options(marker, "schema")
                                                                 or {})
        result["notes"].append(
            f"NOT APPLIED: {relative} has been edited by hand outside its tables and was kept, "
            "so the options given now are not in effect. Pass overwrite=true to regenerate "
            "the file from its tables and the options given now (the other hand edits are "
            "lost), or put the options into its tables directly.")
        result["next_steps"] = [f"python -m {package}.tools.check   (after editing the file)"]
        return result

    stored = _stored_options(marker, "schema")
    if reset:
        base = _default_schema_options()
    elif on_disk is not None:
        base = on_disk                    # the file itself says what is in effect
    elif current is None and stored is not None:
        try:                              # the file is gone: write it again as recorded
            base = _merge_schema_options(_default_schema_options(), stored)
        except ComponentError:
            base = _default_schema_options()
    else:
        base = _default_schema_options()
    merged = _merge_schema_options(base, options)
    _resolve_nested_items(merged)

    for collection, behaviour in merged["mixins"].items():
        if behaviour not in KNOWN_BEHAVIOURS:
            result["notes"].append(
                f"mixin '{behaviour}' for {collection} is not one of coffea's NanoAOD behaviours "
                f"({sorted(KNOWN_BEHAVIOURS)}): it must be registered in nanoaod.behavior before "
                "the schema is used")
        elif behaviour in NOT_COLLECTIONS:
            result["notes"].append(
                f"mixin '{behaviour}' gives {collection} vector methods but not those of a "
                "collection: cross-references into it, and follow(), will not work. "
                "'PtEtaPhiMCollection' has both")
    behaviours = {**merged["mixins"]}
    for name, target_collection in merged["cross_references"].items():
        if behaviours.get(target_collection) in NOT_COLLECTIONS:
            result["notes"].append(
                f"cross-reference '{name}' points into {target_collection}, whose behaviour "
                f"'{behaviours[target_collection]}' is not a collection: give it "
                "'PtEtaPhiMCollection' instead")

    new_text = render_schema(merged)
    if current == new_text:
        result["files_kept"].append(relative)   # already up to date
    else:
        writes.write_text(target, new_text)
        result["files_written"].append(relative)
        if current is not None and current != pristine and not generated:
            lost = "its hand edits outside the tables are gone"
            if on_disk is None:
                lost = ("it had no readable tables, so only the options "
                        + ("recorded for the project and " if stored and not reset else "")
                        + "given now are in effect")
            result["notes"].append(f"{relative} was regenerated: {lost}")
    result["options"] = merged
    result["applied"] = True
    if merged["constant_fields"]:
        result["notes"].append(
            "constant_fields need coffea 2025.12 or newer (older versions ignore them with a "
            "warning) and work for jagged collections only: several objects per event")
    if merged["cross_references"]:
        result["notes"].append(
            "follow(objs[...], '<index>G', '<Collection>') returns objects of the collection "
            "as stored in the file, not after the selection's object cuts")
    result["next_steps"] = [
        f"python -m {package}.tools.check --sample <NAME>   (the schema is used on every file open)",
        "collections given a Lorentz behaviour here no longer need as_lorentz in "
        "definitions/objects.py",
    ]
    return result


# --------------------------------------------------------------------------- #
# chain report
# --------------------------------------------------------------------------- #

def apply_chain_report(project: Path, package: str, marker: Dict[str, Any],
                       options: Dict[str, Any], overwrite: bool,
                       writes: projects.Writes) -> Dict[str, Any]:
    _unknown_options(options, [], "chain_report")
    result = _result("chain_report")
    # the list of fixtures is the analyst's: never replaced, whatever overwrite says
    written, kept = projects.copy_template("components/chain_report", project, package, writes,
                                           overwrite=overwrite,
                                           never_overwrite=("tests/fixtures.yaml",))
    result["files_written"] += written
    result["files_kept"] += kept
    result["options"] = {}
    result["next_steps"] = [
        "make a fixture: python tests/make_fixture.py <file.root> --dataset <NAME> --events 200 "
        "--year <YEAR> --skim-factor 0.5   (for data: --data --year <YEAR>, no skim factor)",
        "python tests/chain_report.py compute state.json   and read tests/README.md",
        "commit tests/ and .github/workflows/chain-report.yml; the report then runs on every "
        "pull request",
    ]
    result["notes"].append(
        "The report shows that the chain executes and how its numbers move between two "
        "versions. It does not check that the physics is right.")
    return result


APPLY = {
    "lepton_jets": apply_lepton_jets,
    "scaleout": apply_scaleout,
    "schema": apply_schema,
    "chain_report": apply_chain_report,
}


def apply_component(project: Path, name: str, options: Optional[Dict[str, Any]] = None,
                    overwrite: bool = False) -> Dict[str, Any]:
    """Apply one component to a project and record it in the project's marker.

    Either everything is written, the record included, or the project is left exactly
    as it was and a ComponentError (or the OSError of a failed write) says why.
    """
    if not isinstance(name, str) or name not in APPLY:
        raise ComponentError(f"unknown component {_shown(name)}",
                             f"Available components: {sorted(APPLY)}")
    if options is not None and not isinstance(options, dict):
        raise ComponentError("options must be a mapping of option names to values",
                             "For example options={'radius': 0.4}.")
    package = projects.find_package(project)
    if package is None:
        raise ComponentError(
            f"{project.name} is not a scaffolded analysis framework",
            "Create one with ScaffoldAnalysisFramework first, or point project_dir at the "
            "directory that contains the analysis package.")
    marker = projects.read_marker(project) or {}
    try:
        with projects.Writes() as writes:
            result = APPLY[name](project, package, marker, dict(options or {}), overwrite, writes)
            if result.pop("record", True):
                applied = marker.get("components")
                applied = dict(applied) if isinstance(applied, dict) else {}
                applied[name] = result.get("options", {})
                marker["components"] = applied
                marker["package"] = package
                projects.write_marker(project, marker, writes)
    except projects.BlockError as exc:
        raise ComponentError(str(exc), "Repair the marker lines by hand, then apply the "
                                       "component again.") from None
    except projects.SourceError as exc:
        raise ComponentError(
            f"{exc}: nothing was changed",
            f"Fix that file first (python -m {package}.tools.check shows such errors), then "
            "apply the component again.") from None
    result["package"] = package
    return result
