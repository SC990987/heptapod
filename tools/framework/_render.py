"""
# _render.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Generate the analysis-specific half of a scaffolded framework.

The engine (processor, selections, histograms, ...) is copied from the template as
is. What depends on the analysis -- its objects, the starting menu of cuts and
histograms, the first selections, the sample list and the test notebook -- is written
here from a list of object specs (see tools.analysis.nanoaod_layout.object_spec).

Pure python: no orchestral, no coffea. Everything returns text.
"""
from __future__ import annotations

import json
import keyword
import math
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

PLACEHOLDER_PACKAGE = "analysis_pkg"


class PlanError(ValueError):
    """The requested objects cannot be turned into a consistent set of definitions."""

# Starting kinematic menus per collection: (pT thresholds, |eta| thresholds, baseline pT,
# baseline |eta|). They are starting points for the analyst to edit, not recommendations.
KINEMATIC_MENUS = {
    "Muon": ([5, 10, 20, 30], [2.4], 10, 2.4),
    "Electron": ([10, 20, 30], [2.5], 10, 2.5),
    "Photon": ([20, 30], [2.5], 20, 2.5),
    "Tau": ([20, 30], [2.3], 20, 2.3),
    "Jet": ([20, 30, 50], [2.4, 4.7], 30, 2.4),
    "FatJet": ([200, 300], [2.4], 200, 2.4),
}
DEFAULT_KINEMATIC_MENU = ([5, 10, 20], [2.4], 10, 2.4)

# Identification cuts whose name is the expression itself, so nothing is hidden in it.
# (name, field it needs, python expression in terms of ``obj``)
ID_CUTS = {
    "Muon": [("looseId", "looseId", "obj.looseId"),
             ("mediumId", "mediumId", "obj.mediumId"),
             ("tightId", "tightId", "obj.tightId")],
    "Electron": [("cutBased >= 2", "cutBased", "obj.cutBased >= 2"),
                 ("cutBased >= 4", "cutBased", "obj.cutBased >= 4")],
    "Photon": [("cutBased >= 1", "cutBased", "obj.cutBased >= 1"),
               ("electronVeto", "electronVeto", "obj.electronVeto")],
    "Jet": [("jetId >= 2", "jetId", "obj.jetId >= 2")],
    "GenPart": [("status 1", "status", "obj.status == 1"),
                ("isLastCopy", "statusFlags", 'obj.hasFlags("isLastCopy")'),
                ("isPrompt", "statusFlags", 'obj.hasFlags("isPrompt")'),
                ("|pdgId| == 11", "pdgId", "abs(obj.pdgId) == 11"),
                ("|pdgId| == 13", "pdgId", "abs(obj.pdgId) == 13")],
}

OBJECT_LABELS = {
    "Muon": "Muon", "Electron": "Electron", "Photon": "Photon", "Tau": "Tau", "Jet": "Jet",
    "FatJet": "Large-radius jet", "MET": "MET", "PuppiMET": "PUPPI MET", "PV": "PV",
    "SV": "Secondary vertex", "GenPart": "Gen particle", "GenJet": "Gen jet",
}

PT_RANGES = {"Jet": 500, "FatJet": 1000, "MET": 500, "PuppiMET": 500, "GenPart": 500}
# Multiplicity binnings (nbins, xmin, xmax) where ten objects per event is far too few.
N_RANGES = {"GenPart": (100, 0, 500)}

# Collections whose objects can be summed into a four-vector with a meaningful mass.
PAIR_MASS_COLLECTIONS = ("Muon", "Electron", "Photon", "Tau", "Jet")
EXAMPLE_OBJECT_ORDER = ("Muon", "Electron", "Jet", "Photon", "Tau")


def q(text: str) -> str:
    """A double-quoted literal valid in both python and yaml.

    Characters outside ASCII are written as they are (the files are UTF-8): escaping
    them the json way would turn a character beyond the basic plane into a surrogate
    pair, which neither python nor yaml reads back as that character.
    """
    return json.dumps(str(text), ensure_ascii=False)


def num(value) -> str:
    """Text for a number that python and yaml both read back exactly (10, not 10.0)."""
    if isinstance(value, bool):
        raise ValueError("a true/false value was given where a number is needed")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{value!r} is not a finite number")
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    text = repr(number)
    if "e" in text and "." not in text.split("e")[0]:
        mantissa, exponent = text.split("e")
        text = f"{mantissa}.0e{exponent}"   # yaml 1.1 reads 1e-05 as a string, 1.0e-05 as a float
    return text


def field_access(base: str, name: str) -> str:
    """``base.name`` when the name can be written that way, else ``base["name"]``."""
    if name.isidentifier() and not keyword.iskeyword(name):
        return f"{base}.{name}"
    return f"{base}[{q(name)}]"


def clean_text(text: Optional[str], default: str = "") -> str:
    """Make free text safe to drop into docstrings, string literals and markdown."""
    def clean(value):
        value = str(value or "").replace("\\", " ").replace('"', "'")
        value = "".join(ch if ch.isprintable() else " " for ch in value)
        return re.sub(r"\s+", " ", value).strip()

    return clean(text) or clean(default)


def text_problem(text: Any) -> Optional[str]:
    """Why a string cannot be written into a config as it is, or None if it can."""
    if not isinstance(text, str) or not text:
        return "an empty or non-text value"
    if not text.isprintable():
        return "it contains control or other non-printable characters"
    return None


# Importable names an analysis package must not take: the package directory comes first
# on the path when the analysis is checked, so "signal" or "json" would replace the
# standard-library module for everything that runs there.
_TAKEN_PACKAGE_NAMES = frozenset(getattr(sys, "stdlib_module_names", ())) | {
    PLACEHOLDER_PACKAGE, "tools", "test", "tests", "definitions", "configs", "scripts",
    # directories a project has or gets next to its package, some of which the
    # scale-out component leaves out of what it ships to workers
    "data", "docs", "studies", "test_notebooks", "condor", "build", "dist",
    "coffea", "awkward", "awkward_cpp", "hist", "boost_histogram", "uproot", "numpy", "yaml",
    "vector", "fastjet", "dask", "distributed", "numba", "llvmlite", "scipy", "pandas",
    "matplotlib", "mplhep", "correctionlib", "pyarrow", "fsspec", "pytest", "setuptools", "pip",
    "orchestral", "toolbase", "heptapod", "lpcjobqueue", "htcondor",
}

# What yaml 1.1 reads as true, false or null when it stands unquoted, in any case.
_YAML_WORDS = frozenset({"y", "n", "yes", "no", "on", "off", "true", "false", "null", "none"})


def package_name_from(project_dir: str) -> str:
    """A valid python package name derived from a directory name."""
    base = re.sub(r"[^0-9a-zA-Z_]+", "_", project_dir.rstrip("/").split("/")[-1]).strip("_").lower()
    if not base:
        base = "analysis"
    if not base[0].isalpha():
        base = "a_" + base
    if base in _TAKEN_PACKAGE_NAMES or keyword.iskeyword(base):
        base += "_analysis"
    return base


def package_name_problem(name: Any) -> Optional[str]:
    """Why a string cannot be used as the package name, or None if it can."""
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        return "use lower-case letters, digits and underscores, starting with a letter"
    if keyword.iskeyword(name):
        return "it is a python keyword"
    if name in _TAKEN_PACKAGE_NAMES:
        return "it would shadow a module of that name"
    return None


def object_name_problem(name: Any) -> Optional[str]:
    """Why a string cannot be used as an object name, or None if it can."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        return "use letters, digits and underscores, starting with a letter"
    if name in ("evt_weights", "ch"):
        return "the processor reserves this name"
    if name.lower() in _YAML_WORDS:
        return "yaml would read it as true, false or null"
    return None


def sample_name_problem(name: Any) -> Optional[str]:
    """Why a string cannot be used as a sample name, or None if it can.

    Sample names end up on command lines, in yaml keys and in notebooks, so they are
    kept to characters that mean nothing special in any of those. Run periods
    ("2018", "2016preVFP") follow the same rule.
    """
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name):
        return "use letters, digits and _ . + -, starting with a letter or digit"
    if name.lower() in _YAML_WORDS:
        return "yaml would read it as true, false or null"
    return None


def branch_name_problem(name: Any) -> Optional[str]:
    """Why a string cannot be a branch, field or collection name, or None if it can."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return "use letters, digits and underscores, not starting with a digit"
    return None


def sample_name_from(text: str) -> str:
    """A valid sample name derived from a file name."""
    name = re.sub(r"[^A-Za-z0-9_.+-]+", "_", text).strip("_.+-")
    if not name or sample_name_problem(name):
        name = "sample_" + name if name else "sample"
    return name


def tree_name_problem(name: Any) -> Optional[str]:
    """Why a string cannot be used as the name of the events tree, or None if it can."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./+-]*", name):
        return "use letters, digits and _ . / + -, not starting with a dash"
    return None


def py_literal(value: Any) -> str:
    """Python source for a plain value (None, bool, number, string, list or dict).

    json.dumps is not it: it writes true, false and null, which python does not read.
    Strings are double-quoted, numbers must be finite.
    """
    if value is None or isinstance(value, bool):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{value!r} is not a finite number")
        return repr(value)
    if isinstance(value, str):
        return q(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(py_literal(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{py_literal(str(key))}: {py_literal(item)}"
                               for key, item in value.items()) + "}"
    raise ValueError(f"{type(value).__name__} values cannot be written into the definitions")


def singular(name: str) -> str:
    """muons -> muon, pvs -> pv: the prefix used for histogram names."""
    if len(name) > 2 and name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _has(spec: Dict[str, Any], field: str, standard: bool = True) -> bool:
    """Does the object have this field? Unknown layouts fall back on ``standard``."""
    if spec.get("fields") is None:
        return standard
    return field in spec["fields"]


def is_countable(spec: Dict[str, Any]) -> bool:
    """A jagged collection with kinematics: gets kinematic cuts and multiplicity cuts."""
    return spec["kind"] == "jagged" and spec["kinematic"]


# --------------------------------------------------------------------------- #
# the plan: one consistent description that every file is rendered from
# --------------------------------------------------------------------------- #

def build_plan(specs: List[Dict[str, Any]], triggers: Optional[List[str]] = None) -> Dict[str, Any]:
    """Decide the starting cuts, histograms, selections and collections for the objects."""
    triggers = [t[4:] if t.startswith("HLT_") else t for t in (triggers or [])]
    by_collection: Dict[str, Dict[str, Any]] = {}
    stems: Dict[str, str] = {}
    for spec in specs:
        by_collection.setdefault(spec["collection"], spec)
        stem = singular(spec["name"])
        if stem in stems:
            raise PlanError(
                f"objects '{stems[stem]}' and '{spec['name']}' would both name their "
                f"histograms '{stem}_...': rename one of them")
        stems[stem] = spec["name"]
    notes: List[str] = []

    obj_cuts: Dict[str, List[Tuple[str, str]]] = {}
    base_obj_cuts: Dict[str, List[str]] = {}
    evt_cuts: List[Tuple[str, str]] = []
    base_evt_cuts: List[str] = []
    hists: List[Tuple[str, str]] = []
    collections: Dict[str, List[str]] = {}
    counters: List[Tuple[str, str]] = []
    labels: Dict[str, str] = {}

    for spec in specs:
        name, coll = spec["name"], spec["collection"]
        stem = singular(name)
        labels[name] = OBJECT_LABELS.get(coll, coll)
        own: List[str] = []

        if is_countable(spec):
            pts, etas, base_pt, base_eta = KINEMATIC_MENUS.get(coll, DEFAULT_KINEMATIC_MENU)
            menu = [(f"pT > {num(t)} GeV", f"pt_above({num(t)})") for t in pts]
            menu += [(f"|eta| < {num(t)}", f"abs_eta_below({num(t)})") for t in etas]
            for cut_name, field, expr in ID_CUTS.get(coll, []):
                if _has(spec, field):
                    menu.append((cut_name, f"lambda objs, obj: {expr}"))
            obj_cuts[name] = menu
            if not spec["mc_only"]:
                base_obj_cuts[name] = [f"pT > {num(base_pt)} GeV", f"|eta| < {num(base_eta)}"]
                evt_cuts += [(f">={n} {name}", f"at_least({q(name)}, {n})") for n in (1, 2)]
                counters.append((f"Selected {name}",
                                 f"lambda objs: ak.sum(ak.num(objs[{q(name)}], axis=1))"))
            xmax = PT_RANGES.get(coll)
            pt_args = f", xmax={xmax}" if xmax else ""
            n_args = ""
            if coll in N_RANGES:
                n_args = ", nbins={}, xmin={}, xmax={}".format(*N_RANGES[coll])
            hists += [
                (f"{stem}_n", f"obj_attr({q(name)}, \"n\"{n_args})"),
                (f"{stem}_pt", f"obj_attr({q(name)}, \"pt\"{pt_args})"),
                (f"{stem}_eta_phi", f"obj_eta_phi({q(name)})"),
            ]
            own = [f"{stem}_n", f"{stem}_pt", f"{stem}_eta_phi"]
        elif spec["kind"] == "jagged":
            hists.append((f"{stem}_n", f"obj_attr({q(name)}, \"n\")"))
            own = [f"{stem}_n"]
        elif spec["kind"] == "record":
            is_met = spec.get("mixin") == "MissingET"
            if _has(spec, "pt", standard=is_met):
                xmax = PT_RANGES.get(coll, 500)
                hists.append((f"{stem}_pt", f"obj_attr({q(name)}, \"pt\", xmax={xmax})"))
                own.append(f"{stem}_pt")
            if _has(spec, "phi", standard=is_met):
                hists.append((f"{stem}_phi", f"obj_attr({q(name)}, \"phi\")"))
                own.append(f"{stem}_phi")
            if coll == "PV":
                if _has(spec, "npvs"):
                    hists.append((f"{stem}_n", f"obj_attr({q(name)}, \"npvs\", nbins=100, xmin=0, "
                                               f"xmax=100, label=\"Number of PVs\")"))
                    own.append(f"{stem}_n")
                if _has(spec, "z"):
                    hists.append((f"{stem}_z", f"obj_attr({q(name)}, \"z\", nbins=100, xmin=-25, "
                                               f"xmax=25, label=\"PV z (cm)\")"))
                    own.append(f"{stem}_z")
                if _has(spec, "npvsGood") and "PV filter" not in base_evt_cuts:
                    evt_cuts.insert(0, ("PV filter", f"lambda objs: objs[{q(name)}].npvsGood >= 1"))
                    base_evt_cuts.append("PV filter")
            if (coll == "Flag" and _has(spec, "METFilters")
                    and not any(cut == "MET filters" for cut, _ in evt_cuts)):
                evt_cuts.append(("MET filters", f"lambda objs: objs[{q(name)}].METFilters"))
        if own:
            collections[f"{stem}_base"] = own

    # triggers
    if triggers:
        hlt = by_collection.get("HLT")
        if hlt is None:
            notes.append("triggers were given but no object reads the HLT collection: "
                         "the 'pass triggers' cut was not written")
        else:
            missing = [t for t in triggers if not _has(hlt, t)]
            if missing:
                notes.append(f"trigger path(s) not found in the sample file: {missing}")
            paths = [t for t in triggers if t not in missing]
            if paths:
                joined = "\n        | ".join(field_access(f"objs[{q(hlt['name'])}]", t)
                                                for t in paths)
                evt_cuts.append(("pass triggers", f"lambda objs: (\n        {joined}\n    )"))
                base_evt_cuts.insert(0, "pass triggers")

    # one worked example on top of the baseline: a pair requirement and a pair mass
    example = None
    candidates = [by_collection[c] for c in EXAMPLE_OBJECT_ORDER if c in by_collection]
    candidates += [s for s in specs if is_countable(s) and not s["mc_only"]]
    for spec in candidates:
        if is_countable(spec) and not spec["mc_only"]:
            example = spec
            break
    if example is not None:
        name, stem = example["name"], singular(example["name"])
        if example["collection"] in PAIR_MASS_COLLECTIONS or example.get("wrap"):
            hist_name = f"{stem}_{stem}_invmass"
            label = f"Invariant mass of the two leading {name} (GeV)"
            hists.append((hist_name, (
                "h.Histogram(\n"
                "        [\n"
                f"            h.Axis(hist.axis.Regular(*default_binnings[\"mass\"], name={q(hist_name)},\n"
                f"                                     label={q(label)}),\n"
                f"                   lambda objs, mask: objs[{q(name)}][mask, :2].sum().mass),\n"
                "        ],\n"
                f"        evt_mask=lambda objs: ak.num(objs[{q(name)}], axis=1) > 1,\n"
                "    )")))
            collections[f"{stem}_base"].append(hist_name)

    for what, names in (("histogram", [name for name, _ in hists]),
                        ("event cut", [name for name, _ in evt_cuts]),
                        ("counter", [name for name, _ in counters])):
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise PlanError(
                f"the object names chosen would define the {what} {repeated} twice: rename "
                "one of the objects involved")

    return {
        "specs": specs,
        "obj_cuts": obj_cuts,
        "base_obj_cuts": base_obj_cuts,
        "evt_cuts": evt_cuts,
        "base_evt_cuts": base_evt_cuts,
        "hists": hists,
        "collections": collections,
        "counters": counters,
        "labels": labels,
        "example": example["name"] if example is not None else None,
        "notes": notes,
    }


def channel_names(plan: Dict[str, Any]) -> List[str]:
    names = ["all", "baseline"]
    if plan["example"]:
        names.append(f"baseline_2{plan['example']}")
    return names


def first_hist(plan: Dict[str, Any]) -> str:
    return plan["hists"][0][0] if plan["hists"] else "HIST"


# --------------------------------------------------------------------------- #
# definitions/
# --------------------------------------------------------------------------- #

def render_objects(plan: Dict[str, Any], package: str = PLACEHOLDER_PACKAGE) -> str:
    specs = plan["specs"]
    has_gen = any(s["collection"] == "GenPart" for s in specs)
    needs_wrap = any(s.get("wrap") for s in specs)
    out = ['"""Object definitions: what the analysis calls things, and where each comes from.',
           "",
           "primary_objs  name -> lambda evts: ...   read straight from the events.",
           "derived_objs  name -> lambda objs: ...   built from the *selected* objects of a",
           "              channel, in the order they are defined here, so a derived object may",
           "              use any primary object and any derived object defined before it.",
           "",
           "Jagged collections (several objects per event) are ordered by pT by the processor,",
           "primary and derived alike, and can take object cuts. One-per-event records (MET,",
           "PV, HLT, Flag) take event cuts only. Because every collection is ordered and cut",
           "on its own, two of them do not stay aligned object by object: to keep something",
           "with each object (the track a muon points to, say), attach it as a field of that",
           "object with ak.with_field instead of defining it as a second object.",
           '"""',
           ""]
    out += ["import awkward as ak  # noqa: F401", ""]
    if needs_wrap:
        out += [f"from {package}.tools.utilities import as_lorentz", ""]
    if has_gen:
        out += [
            "",
            "# helpers for generator-level objects",
            "def pid(part, val):",
            '    """Particles whose |pdgId| is val."""',
            "    return part[abs(part.pdgId) == val]",
            "",
            "",
            "def to_pid(part, val):",
            '    """Particles that decay, with every daughter of |pdgId| val."""',
            "    daughters = abs(part.children.pdgId)",
            "    return part[(ak.num(daughters, axis=-1) > 0) & ak.all(daughters == val, axis=-1)]",
            "",
            "",
            "def from_pid(part, val):",
            '    """Particles whose mother has |pdgId| val."""',
            "    return part[ak.fill_none(abs(part.parent.pdgId) == val, False)]",
            "",
        ]
    out += ["", "# Objects read straight from the events.", "primary_objs = {}"]
    width = max((len(s["name"]) for s in specs), default=0)
    for spec in specs:
        key = f"primary_objs[{q(spec['name'])}]".ljust(len("primary_objs[]") + width + 2)
        source = field_access("evts", spec["collection"])
        comment = ""
        if spec.get("wrap"):
            mass = spec["wrap"].get("mass")
            if mass is None:
                source = f"as_lorentz({source})"
            else:
                source = f"as_lorentz({source}, mass={num(mass)})"
                comment = "  # mass in GeV: an assumption, the file stores none"
        out.append(f"{key} = lambda evts: {source}{comment}")
    optional = [s["name"] for s in specs if s["mc_only"]]
    out += [
        "",
        "# Objects a sample is allowed not to have: generator-level objects on data, a",
        "# collection only some productions carry. Whatever needs one of them is skipped",
        "# there, with a warning. A derived object built from an optional object needs no",
        "# entry of its own: it is absent wherever its input is. List a derived object only",
        "# if its own definition reads a branch some samples lack (a simulation-only field",
        "# of the muons, say). Every other object must build in every sample: if one does",
        "# not, a strict run stops and names it.",
        "optional_objs = [" + ", ".join(q(name) for name in optional) + "]",
        "",
        "# Objects built from the selected objects of a channel, evaluated in this order.",
        "derived_objs = {}",
    ]
    if plan["example"]:
        ex = plan["example"]
        out += [
            "# For example, the two leading " + ex + " of each event:",
            f"# derived_objs[{q('leading_' + ex)}] = lambda objs: objs[{q(ex)}][:, :2]",
        ]
    return "\n".join(out) + "\n"


def render_cuts(plan: Dict[str, Any]) -> str:
    out = ['"""Every available cut, by name.',
           "",
           "obj_cut_defs[object][name]  lambda objs, obj: <one True/False per object>",
           "    Object cuts slim a collection; they never reject an event. ``obj`` is the",
           "    collection being cut, after the cuts listed before this one. ``objs`` holds the",
           "    other objects as they are at that point, for cuts that compare collections:",
           "    primary objects defined above this one in objects.py after their cuts, those",
           "    below it before theirs. Derived objects do not exist yet when primary objects",
           "    are cut; the cuts of a derived object see the derived objects built before it.",
           "",
           "evt_cut_defs[name]          lambda objs: <one True/False per event>",
           "    Event cuts reject whole events. They are applied after the object cuts, one",
           "    after another, and each becomes a row of the cutflow.",
           "",
           "Defining a cut here does nothing by itself: a selection in configs/selections.yaml",
           "has to list its name.",
           "",
           "The thresholds below were written by the scaffold so that there is something to",
           "run. They are starting values, not this analysis's cuts: replace them.",
           '"""',
           "",
           "import awkward as ak",
           "",
           "",
           "# small factories, so that each entry below reads as what it does",
           "def pt_above(threshold):",
           "    return lambda objs, obj: obj.pt > threshold",
           "",
           "",
           "def abs_eta_below(threshold):",
           "    return lambda objs, obj: abs(obj.eta) < threshold",
           "",
           "",
           "def at_least(name, n):",
           "    return lambda objs: ak.num(objs[name], axis=1) >= n",
           "",
           "",
           "obj_cut_defs = {"]
    for name, menu in plan["obj_cuts"].items():
        out.append(f"    {q(name)}: {{")
        out += [f"        {q(cut)}: {expr}," for cut, expr in menu]
        out.append("    },")
    out += ["}", "", "evt_cut_defs = {"]
    out += [f"    {q(cut)}: {expr}," for cut, expr in plan["evt_cuts"]]
    out.append("}")
    return "\n".join(out) + "\n"


def render_hists(plan: Dict[str, Any], package: str = PLACEHOLDER_PACKAGE) -> str:
    out = ['"""Every available histogram and counter, by name.',
           "",
           "Histograms are Histogram objects: a list of Axis objects, each bundling a hist axis",
           "with the function that fills it, ``lambda objs, mask: values``. ``objs`` are the",
           "selected objects of one channel. An optional ``evt_mask`` restricts the events",
           "used (for example to events with two muons) and is passed to the fill functions",
           "as ``mask`` so they can apply it before indexing. Storage is weighted by default.",
           "",
           "Defining a histogram here does nothing by itself: a collection in",
           "configs/hist_collections.yaml has to list its name. Every collection named at",
           "run time is filled in every channel named at run time, so a fill function that",
           "picks a position (``objs[\"muons\"][mask, 0]``) needs an ``evt_mask`` for the",
           "events that have none; a slice (``objs[\"muons\"][:, :1]``) does not. Counters",
           "are different: all of them are filled, in every channel.",
           '"""',
           "",
           "import math",
           "",
           "import awkward as ak  # noqa: F401",
           "import hist  # noqa: F401",
           "",
           f"from {package}.tools import histogram as h",
            "",
            "",
            "# counters: one plain (unweighted) number per channel and sample, summed over",
            "# the events that pass the channel's event cuts",
            "counter_defs = {"]
    out += [f"    {q(name)}: {expr}," for name, expr in plan["counters"]]
    out += ["}", "", "",
            "# Default labels and binnings. The binnings are the scaffold's defaults, not",
            "# choices of this analysis: change them here, or per histogram with nbins, xmin",
            "# and xmax. A new object gets its axis label from an entry in obj_labels.",
            "obj_labels = {"]
    out += [f"    {q(name)}: {q(label)}," for name, label in plan["labels"].items()]
    out += [
        "}",
        "attr_labels = {",
        '    "pt": r"$p_T$ (GeV)",',
        '    "eta": r"$\\eta$",',
        '    "phi": r"$\\phi$",',
        '    "mass": "Mass (GeV)",',
        "}",
        "default_binnings = {",
        '    "n": (10, 0, 10),',
        '    "pt": (100, 0, 200),',
        '    "eta": (50, -3, 3),',
        '    "phi": (50, -math.pi, math.pi),',
        '    "mass": (100, 0, 200),',
        "}",
        "",
        "",
        "# convenience functions for the common cases",
        "def make_label(obj, attr, absval=False):",
        "    obj_label = obj_labels.get(obj, obj)",
        '    if attr == "n":',
        '        return f"Number of {obj_label}s"',
        "    attr_label = attr_labels.get(attr, attr)",
        "    if absval:",
        '        attr_label = f"|{attr_label}|"',
        '    return f"{obj_label} {attr_label}"',
        "",
        "",
        "def obj_attr(obj, attr, absval=False, nbins=None, xmin=None, xmax=None, label=None):",
        '    """One-axis histogram of objs[obj].attr; attr "n" counts the objects per event.',
        "",
        "    Binning: nbins, xmin and xmax where given, else default_binnings[attr], else",
        '    (100, 0, 100). The axis is named "<obj>_<attr>"; absval histograms |attr|.',
        '    """',
        "    default_nbins, default_xmin, default_xmax = default_binnings.get(attr, (100, 0, 100))",
        "    nbins = default_nbins if nbins is None else nbins",
        "    xmin = default_xmin if xmin is None else xmin",
        "    xmax = default_xmax if xmax is None else xmax",
        "    label = make_label(obj, attr, absval) if label is None else label",
        "    return h.Histogram.simple_hist(obj, attr, absval, nbins, xmin, xmax, label)",
        "",
        "",
        "def make_2d(h1, h2):",
        '    """Two-axis histogram from the (last) axes of two one-axis histograms."""',
        "    return h.Histogram([h1.axes[-1], h2.axes[-1]])",
        "",
        "",
        "def obj_eta_phi(obj):",
        '    return make_2d(obj_attr(obj, "eta"), obj_attr(obj, "phi"))',
        "",
        "",
        "hist_defs = {",
    ]
    out += [f"    {q(name)}: {expr}," for name, expr in plan["hists"]]
    out.append("}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# configs/
# --------------------------------------------------------------------------- #

def render_selections(plan: Dict[str, Any]) -> str:
    out = [
        "# Selections: which of the cuts defined in definitions/cuts.py are applied.",
        "#",
        "# A selection has object cuts (obj_cuts, per object: they slim collections) and event",
        "# cuts (evt_cuts: they reject events, in the order listed, one cutflow row each).",
        "# Every top-level entry is a channel that can be run by name, except entries whose",
        "# name starts with an underscore: those only exist to be reused.",
        "#",
        "# Reuse works through yaml anchors: '&name' labels a block, '*name' pastes it and",
        "# '<<: *name' merges a mapping. Lists pasted inside lists are flattened when read.",
        "# A key written next to '<<: *name' replaces the merged key wholesale: to give one",
        "# selection its own cuts for an object, write that object's whole list there",
        "# (starting with its anchor, '- *muons_base', to keep the shared ones). Editing a",
        "# shared block (_object_cuts, _event_cuts) changes every selection that merges it.",
        "#",
        "# The selections below were written by the scaffold so that there is something to",
        "# run. They are a starting point, not this analysis's selection: replace them.",
        "",
        "# Object cuts shared by the selections below",
    ]
    if plan["base_obj_cuts"]:
        out.append("_object_cuts: &object_cuts")
        for name, cuts in plan["base_obj_cuts"].items():
            out.append(f"  {name}: &{name}_base")
            out += [f"    - {q(cut)}" for cut in cuts]
    else:
        out += ["# (none yet. To add some, replace the {} by indented lines of the form",
                "#    <object>:",
                '#      - "<cut name>"  )',
                "_object_cuts: &object_cuts {}"]
    out += ["", "# Event cuts shared by the selections below"]
    if plan["base_evt_cuts"]:
        out.append("_event_cuts: &event_cuts")
        out += [f"  - {q(cut)}" for cut in plan["base_evt_cuts"]]
    else:
        out += ['# (none yet. To add some, replace the [] by indented lines of the form  - "<cut name>")',
                "_event_cuts: &event_cuts []"]
    out += [
        "",
        "# Every event and every object: the reference point for efficiencies",
        "all:",
        "  obj_cuts: {}",
        "  evt_cuts: []",
        "",
        "baseline:",
        "  obj_cuts:",
        "    <<: *object_cuts",
        "  evt_cuts:",
        "    - *event_cuts",
    ]
    if plan["example"]:
        ex = plan["example"]
        out += [
            "",
            "# The baseline's cuts plus one more event cut: how one selection extends another",
            f"baseline_2{ex}:",
            "  obj_cuts:",
            "    <<: *object_cuts",
            "  evt_cuts:",
            "    - *event_cuts",
            f"    - {q('>=2 ' + ex)}",
        ]
    return "\n".join(out) + "\n"


def render_hist_collections(plan: Dict[str, Any]) -> str:
    out = [
        "# Histogram collections: which of the histograms defined in definitions/hists.py",
        "# are filled. The processor is given collection names; a collection may include",
        "# others through yaml anchors ('&name' labels a list, '- *name' includes it).",
        "# Entries whose name starts with an underscore are not offered as collections.",
        "",
    ]
    for name, hists in plan["collections"].items():
        out.append(f"{name}: &{name}")
        out += [f"  - {q(hist_name)}" for hist_name in hists]
        out.append("")
    out.append("# everything above")
    if plan["collections"]:
        out.append("base:")
        out += [f"  - *{name}" for name in plan["collections"]]
    else:
        out.append("base: []")
    return "\n".join(out) + "\n"


def render_run_periods(year: Optional[str], lumi: Optional[float], golden_json: Optional[str]) -> str:
    out = [
        "# Run periods. lumi is the integrated luminosity in /pb, used to scale simulation.",
        "# golden_json names a file in data/ and is applied to data of that period; leave it",
        "# out to keep every luminosity section (the processor warns when it does).",
        "# Period names are read as text: 2018 and \"2018\" name the same period.",
        "",
    ]
    if not year:
        out += ["# None yet. Uncomment and fill in:",
                '# "2018":', "#   lumi: 0.0   # integrated luminosity in /pb",
                '#   golden_json: "Cert_..._JSON.txt"']
        return "\n".join(out) + "\n"
    out.append(f"{q(year)}:")
    if lumi is not None:
        out.append(f"  lumi: {num(lumi)}")
    else:
        out.append("  # lumi: 0.0   # /pb: fill in before trusting any normalised yield")
    if golden_json:
        out.append(f"  golden_json: {q(golden_json)}")
    else:
        out.append('  # golden_json: "Cert_..._JSON.txt"   # a file in data/')
    return "\n".join(out) + "\n"


def render_cross_sections(samples: List[Dict[str, Any]]) -> str:
    out = [
        "# Cross sections in pb, by sample name. A simulated sample that is not listed here",
        "# is left unscaled, and the processor says so in its warnings.",
        "",
    ]
    simulated = [sample for sample in samples if not sample.get("is_data")]
    for sample in simulated:
        if sample.get("xsec") is not None:
            out.append(f"{q(sample['name'])}: {num(sample['xsec'])}")
        else:
            out.append(f"# {q(sample['name'])}: 1.0   # pb: fill in")
    if not simulated:
        out.append('# "MySample": 1.0')
    return "\n".join(out) + "\n"


def render_samples(samples: List[Dict[str, Any]], tag: str, year: Optional[str],
                   package: str = PLACEHOLDER_PACKAGE) -> str:
    out = [
        "# Where the samples are. Each top-level entry is a group ('tag') of samples:",
        "#",
        "#   <tag>:",
        "#     path: common prefix of every file in the group (may be empty)",
        "#     year: default run period of the samples",
        "#     samples:",
        "#       <sample name>:",
        "#         path: sub-directory of this sample (optional)",
        "#         files: [...]         relative to path + sample path, or complete paths",
        "#         is_data: false       true for data",
        "#         year: \"2018\"         overrides the group's year",
        "#         skim_factor: 1.0     fraction of the original events kept by a skim",
        "#",
        f"# python -m {package}.scripts.add_samples writes a group from a directory listing",
        "# (it rewrites the file it is pointed at as plain yaml, without these comments).",
        "",
        f"{q(tag)}:",
        "  path: \"\"",
    ]
    if year:
        out.append(f"  year: {q(year)}")
    out.append("  samples:")
    if samples:
        for sample in samples:
            out.append(f"    {q(sample['name'])}:")
            out.append("      files:")
            out += [f"        - {q(path)}" for path in sample["files"]]
            if sample.get("is_data"):
                out.append("      is_data: true")
            if sample.get("year") and str(sample["year"]) != str(year or ""):
                out.append(f"      year: {q(sample['year'])}")
            if sample.get("skim_factor") is not None:
                out.append(f"      skim_factor: {num(sample['skim_factor'])}")
    else:
        out += ["    # None yet. For example:",
                '    # "MySample":',
                "    #   files:",
                '    #     - "/complete/path/to/file.root"']
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# the test notebook
# --------------------------------------------------------------------------- #

def _cell(kind: str, source: str) -> Dict[str, Any]:
    lines = source.strip("\n").split("\n")
    cell: Dict[str, Any] = {
        "cell_type": kind,
        "metadata": {},
        "source": [line + "\n" for line in lines[:-1]] + [lines[-1]],
    }
    if kind == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def render_notebook(package: str, title: str, samples: List[str], channels: List[str],
                    hist_name: str) -> str:
    """The test notebook: run the processor on one file and look at what comes out."""
    sample_list = ", ".join(q(s) for s in samples[:2]) if samples else q("SAMPLE_NAME")
    first_sample = "samples[0]"
    cells = [
        _cell("markdown", f"""
# {title}: processor test

Runs the processor on one file per sample, then looks at the cutflows, a histogram and
the warnings. Re-run it after changing anything in `{package}/tools/`: the numbers
should only move when you meant them to.
"""),
        _cell("code", f"""
# python
import importlib
# columnar analysis and plotting
import matplotlib.pyplot as plt
from coffea import processor
# local
from {package} import TREE_NAME
from {package}.tools import utilities, plotting, selection, histogram, cutflow, schema
from {package}.tools import processor as analysis_processor
from {package}.definitions import objects, cuts, hists, weights
# Always reload the local modules to pick up changes made during development. The
# order matters: a module is reloaded after everything it imports from this package.
# (A module added later, like tools/lepton_jets.py, goes in before `objects`.)
for module in (utilities, cutflow, histogram, schema, objects, cuts, hists, weights,
               selection, analysis_processor, plotting):
    importlib.reload(module)
AnalysisSchema = schema.AnalysisSchema
plotting.set_plot_style()
%matplotlib inline
"""),
        _cell("code", f"""
samples = [{sample_list}]
fileset = utilities.make_fileset(samples, max_files=1)
fileset
"""),
        _cell("code", f"""
channels = {json.dumps(channels)}
hist_collections = ["base"]

runner = processor.Runner(
    executor=processor.IterativeExecutor(),
    # executor=processor.FuturesExecutor(workers=4),
    schema=AnalysisSchema,
    chunksize=100_000,
    # maxchunks=1,
    skipbadfiles=False,   # True carries on past unreadable files; in a test one should stop the run
    metadata_cache={{}},    # else coffea reuses the sample metadata (is_data, year, skim_factor)
                          # a file was first run with in this session, whatever the fileset says
)
p = analysis_processor.AnalysisProcessor(channels, hist_collections)
out = runner(fileset, processor_instance=p, treename=TREE_NAME)
"""),
        _cell("markdown", """
## Cutflows

One row per event-level cut. Object cuts slim collections and do not appear here.
"""),
        _cell("code", f"""
for channel in channels:
    print(channel)
    out[{first_sample}]["cutflow"][channel].print_table()
    print()
"""),
        _cell("markdown", """
## Warnings

Everything the processor skipped, and why. A generator-level cut on data belongs here;
anything you did not expect is worth understanding before looking at plots.
"""),
        _cell("code", """
for sample in samples:
    for message in sorted(out[sample]["warnings"]):
        print(f"{sample}: {message}")
"""),
        _cell("markdown", """
## Histograms

Every histogram has a `channel` axis; pick a channel to get the plain histogram.
"""),
        _cell("code", f"""
for channel in channels:
    plotting.plot(utilities.get_hist(out, {first_sample}, {q(hist_name)}, channel), label=channel,
                  skip_label=True)
plotting.add_label(data=True in out[{first_sample}]["metadata"]["is_data"])
plt.legend()
plt.yscale("log")
"""),
        _cell("code", f"""
out[{first_sample}]["metadata"]
"""),
    ]
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        # 4.4, not 4.5: from 4.5 on every cell needs an id, and these have none
        "nbformat_minor": 4,
    }
    return json.dumps(notebook, indent=1) + "\n"
