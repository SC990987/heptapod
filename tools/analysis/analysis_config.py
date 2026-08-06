"""
# analysis_config.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Generic configuration for the columnar Lepton-Jet analysis tools.

These tools carry NO analysis-specific policy of their own. Everything that
defines a particular analysis -- which object collections are clustered, their
selection cuts, the branch names, the categorization, the channels, the
observable, the triggers, and the gen-level resonance -- lives in a **config**,
supplied either as a YAML file or an inline dict. The defaults here are neutral
mechanism defaults (anti-kT R=0.4, the standard CMS good-vertex values, the
NanoAOD isolation energy-fraction branches); they contain nothing specific to
any one search.

A ready-made config for the CMS SIDM two-Lepton-Jet analysis (AN-23-107) ships
as `configs/sidm.yaml` and is documented by the `sidm` skill.

Config resolution (deep-merged, later wins):
    built-in DEFAULTS  <-  the YAML file / dict you pass  <-  inline `overrides`

Nothing here imports coffea/awkward at module load; the schema builder imports
coffea lazily.
"""
from __future__ import annotations

import copy
import math
import os
from typing import Any, Dict, List, Optional

# constituent masses are declared per-constituent in the config; these are
# convenient constants a config can reference in code if desired.
ELECTRON_MASS = 0.000511
MUON_MASS = 0.105658
PHOTON_MASS = 0.0


# ===================================================================== #
# ===================== Neutral mechanism defaults ==================== #
# ===================================================================== #
# Policy sections (constituents, crossclean, categories, channels, triggers,
# gen.resonance_pdgid) are intentionally EMPTY/None -- a real analysis supplies
# them via its config. Only generic mechanism defaults are set here.

DEFAULTS: Dict[str, Any] = {
    "name": "generic",
    "tree": "Events",
    "collections": {"jet": "Jet", "genpart": "GenPart"},

    # constituent object collections that get clustered into Lepton Jets.
    # Each entry: {name, collection, is_muon, mass, pt_min, abseta_max,
    #              id:{type,...}, veto:{...}, carry:{canonical: field}}
    "constituents": [],
    # overlap removal between constituent collections:
    # {name, target, reference, method:"dr", max_dr, use_outer}
    "crossclean": [],

    "clustering": {"algorithm": "antikt", "radius": 0.4, "backend": "fastjet"},

    "leptonjet": {
        "pt_min": 0.0,
        "abseta_max": 100.0,
        "isolation": {
            "enabled": True,
            "max": None,                       # None => no isolation cut
            "matched_jet_dr": 0.4,
            "jet_collection": "Jet",
            "lepton_fraction_branches": ["chEmEF", "neEmEF", "muEF"],
        },
        # ordered category rules; first match wins, else the `default` category.
        # {name, min_muon, final_min_muon} or {name, default: true}
        "categories": [],
        # displacement requirements applied per category:
        # {category, constituent, field, op, value, reduce, applies_if_present}
        "displacement": [],
    },

    "event_selection": {
        # An ordered list of named cuts. The tools apply them in order and build
        # the cutflow generically from THIS list -- nothing is hard-coded. Cut
        # recipes: trigger | flag | primary_vertex | cosmic_veto | event_scalar
        # (event-level filters, evaluated at reconstruction and stamped as
        # per-event booleans) and object_count (>= N selected objects, optionally
        # of a category; evaluated on the reconstructed objects). An analysis
        # with no PV filter or cosmic veto simply omits those entries.
        "cutflow": [],
        # optional channel split by the categories of the leading N objects:
        # {name, categories: [catA, catB]}
        "channels": [],
        "observable": {"type": "invariant_mass", "n_leading": 2},
    },

    # Derived quantities computed for events passing the cutflow, over the
    # event's object collections (leptonjets + stamped constituents/jets). Each:
    #   {name, function, inputs:[{object, rank, [category], [selected]}], [per_channel]}
    # function in: invariant_mass | delta_r | delta_phi | pt | eta | phi | mass |
    # sum_pt | count. If empty, defaults to the invariant mass of the two leading
    # selected leptonjets (the LJ-LJ / bound-state mass).
    "observables": [],

    "gen": {
        "resonance_pdgid": None,
        "lepton_pdgids": [11, 13],
        "mother_field": "genPartIdxMother",
        "vertex": {"vx": "vx", "vy": "vy"},
    },
}


# ===================================================================== #
# ========================= Config resolution ======================== #
# ===================================================================== #

def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge `over` onto a copy of `base` (dicts merge; everything
    else, including lists, is replaced)."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(config: Any = None, base_directory: Optional[str] = None,
                overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve a config into a merged dict.

    `config` may be:
      * None                -> just the neutral DEFAULTS (+ overrides),
      * a dict              -> merged onto DEFAULTS,
      * a path to a .yaml/.yml/.json file (resolved against base_directory).
    `overrides` is an inline dict merged last (so a skill can tweak a few cuts
    without editing the file).
    """
    cfg_dict: Dict[str, Any] = {}
    if isinstance(config, dict):
        cfg_dict = config
    elif isinstance(config, str) and config:
        path = config
        if base_directory and not os.path.isabs(path):
            path = os.path.join(base_directory, path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"config file not found: {config}")
        with open(path) as fh:
            text = fh.read()
        if path.endswith(".json"):
            import json
            cfg_dict = json.loads(text)
        else:
            import yaml
            cfg_dict = yaml.safe_load(text) or {}
    elif config is not None:
        raise TypeError("config must be a dict, a path string, or None")

    merged = deep_merge(DEFAULTS, cfg_dict)
    if overrides:
        merged = deep_merge(merged, overrides)
    return merged


def get(cfg: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    """Dotted-path getter: get(cfg, 'leptonjet.isolation.max')."""
    cur: Any = cfg
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def apply_cutflow(data: Dict[str, Any], cutflow: List[Dict[str, Any]]) -> List[str]:
    """Apply an ordered config cutflow to one event's data block and return the
    names of the cuts it passed, in order (stops at the first failure).

    `data` has `event.flags` (per-event booleans stamped by the reconstruction
    for the event-filter cuts) and `leptonjets` (the reconstructed objects).
    A cut of type `object_count` counts selected objects (optionally of a
    `category`); any other cut type is looked up in `event.flags` by name and
    defaults to pass if absent. Shared by the reconstruction and selection tools
    so their cutflows always agree.
    """
    ev = data.get("event", {}) if isinstance(data, dict) else {}
    flags = ev.get("flags", {})
    objs_all = data.get("leptonjets", [])
    passed: List[str] = []
    for c in cutflow:
        if c.get("type") == "object_count":
            objs = [o for o in objs_all if o.get("selected")] if c.get("selected", True) else list(objs_all)
            if c.get("category"):
                objs = [o for o in objs if o.get("category") == c["category"]]
            ok = len(objs) >= c.get("min", 1)
        else:
            ok = bool(flags.get(c.get("name"), True))
        if not ok:
            break
        passed.append(c.get("name"))
    return passed


# ===================================================================== #
# ================= Generic derived-quantity engine ================== #
# ===================================================================== #
# A quantity is computed from the leading-N members of named object
# collections. Objects are plain dicts carrying pt/eta/phi/px/py/pz/E. The same
# engine serves reconstructed objects (leptonjets, muons, jets) and gen objects,
# so any analysis can declare e.g. the invariant mass of the leading muon and
# leading jet, or Delta R between them, entirely in the config -- nothing here
# is analysis-specific.

_QUANTITY_FUNCTIONS = ("invariant_mass", "delta_r", "delta_phi", "pt", "eta",
                       "phi", "mass", "sum_pt", "count")


def _obj_mass(objs: List[dict]) -> float:
    px = sum(o["px"] for o in objs); py = sum(o["py"] for o in objs)
    pz = sum(o["pz"] for o in objs); E = sum(o["E"] for o in objs)
    m2 = E * E - px * px - py * py - pz * pz
    if -1e-6 < m2 < 0:
        m2 = 0.0
    return math.sqrt(m2) if m2 > 0 else 0.0


def _pick(collections: Dict[str, List[dict]], inp: dict) -> Optional[dict]:
    """Pick one object: the `rank`-th (1-based, by descending pt) member of a
    named collection, optionally filtered by `category` / `selected`."""
    objs = list(collections.get(inp.get("object"), []))
    if inp.get("category") is not None:
        objs = [o for o in objs if o.get("category") == inp["category"]]
    if inp.get("selected") is not None:
        objs = [o for o in objs if bool(o.get("selected")) == bool(inp["selected"])]
    objs.sort(key=lambda o: o.get("pt", 0.0), reverse=True)
    rank = int(inp.get("rank", 1))
    return objs[rank - 1] if len(objs) >= rank else None


def evaluate_quantity(spec: dict, collections: Dict[str, List[dict]]) -> Optional[float]:
    """Compute one derived quantity for an event. `spec` is
    {name, function, inputs:[{object, rank, [category], [selected]} ...]}.
    Returns the value, or None if a required input object is absent."""
    f = spec.get("function"); inputs = spec.get("inputs", [])
    if f == "invariant_mass":
        objs = [_pick(collections, i) for i in inputs]
        return None if any(o is None for o in objs) else _obj_mass(objs)
    if f in ("delta_r", "delta_phi"):
        a = _pick(collections, inputs[0]); b = _pick(collections, inputs[1])
        if a is None or b is None:
            return None
        return delta_r(a["eta"], a["phi"], b["eta"], b["phi"]) if f == "delta_r" \
            else abs(delta_phi(a["phi"], b["phi"]))
    if f in ("pt", "eta", "phi", "mass"):
        o = _pick(collections, inputs[0])
        return None if o is None else o.get(f)
    if f == "sum_pt":
        return sum(o.get("pt", 0.0) for o in collections.get(inputs[0].get("object"), []))
    if f == "count":
        return float(len(collections.get(inputs[0].get("object"), [])))
    return None


# ===================================================================== #
# ========================= Config validation ======================== #
# ===================================================================== #

def validate_config(cfg: Dict[str, Any]) -> List[str]:
    """Structural sanity checks. Returns a list of human-readable problems
    (empty => OK). Used by the inspector before a run."""
    issues: List[str] = []
    cons = cfg.get("constituents") or []
    if not cons:
        issues.append("no 'constituents' defined — nothing to cluster")
    names = set()
    for i, c in enumerate(cons):
        for key in ("name", "collection"):
            if not c.get(key):
                issues.append(f"constituent #{i} missing '{key}'")
        if c.get("name"):
            if c["name"] in names:
                issues.append(f"duplicate constituent name '{c['name']}'")
            names.add(c["name"])
        idt = (c.get("id") or {}).get("type")
        if idt not in (None, "none", "bool", "min", "cutbased", "photon_vid_relaxed"):
            issues.append(f"constituent '{c.get('name')}' has unknown id.type '{idt}'")
    for cc in (cfg.get("crossclean") or []):
        for ref in ("target", "reference"):
            if cc.get(ref) not in names:
                issues.append(f"crossclean '{cc.get('name')}' {ref} '{cc.get(ref)}' is not a constituent name")
    cats = get(cfg, "leptonjet.categories", [])
    if not cats:
        issues.append("no 'leptonjet.categories' defined")
    valid_cut_types = ("trigger", "flag", "primary_vertex", "cosmic_veto", "event_scalar", "object_count")
    for i, c in enumerate(get(cfg, "event_selection.cutflow", [])):
        if not c.get("name"):
            issues.append(f"event_selection.cutflow[{i}] missing 'name'")
        if c.get("type") not in valid_cut_types:
            issues.append(f"cutflow '{c.get('name')}' has unknown type '{c.get('type')}'; allowed: {list(valid_cut_types)}")
    for i, q in enumerate(get(cfg, "observables", [])):
        if not q.get("name"):
            issues.append(f"observables[{i}] missing 'name'")
        if q.get("function") not in _QUANTITY_FUNCTIONS:
            issues.append(f"observable '{q.get('name')}' has unknown function '{q.get('function')}'; allowed: {list(_QUANTITY_FUNCTIONS)}")
    return issues


# ===================================================================== #
# ===================== Pure geometry helpers ======================== #
# ===================================================================== #

def delta_phi(phi1: float, phi2: float) -> float:
    d = phi1 - phi2
    while d > math.pi:
        d -= 2.0 * math.pi
    while d <= -math.pi:
        d += 2.0 * math.pi
    return d


def delta_r(eta1: float, phi1: float, eta2: float, phi2: float) -> float:
    deta = eta1 - eta2
    dphi = delta_phi(phi1, phi2)
    return math.sqrt(deta * deta + dphi * dphi)


# ===================================================================== #
# ==================== coffea schema (lazy import) =================== #
# ===================================================================== #

def build_schema(cfg: Optional[Dict[str, Any]] = None,
                 extra_mixins: Optional[Dict[str, str]] = None):
    """Return a NanoAODSchema subclass tolerant of LLPNanoAOD/skim quirks:
    it does not hard-fail on missing run/lumi/event IDs, silences LLP
    cross-reference warnings, and drops the GenParticle Lorentz-vector
    behaviour (LLPNanoAOD GenPart carries both cartesian and polar momenta,
    which the vector behaviour rejects). We read gen fields raw."""
    from coffea.nanoevents import NanoAODSchema

    class AnalysisSchema(NanoAODSchema):
        warn_missing_crossrefs = False
        error_missing_event_ids = False

    mixins = dict(AnalysisSchema.mixins)
    mixins.pop("GenPart", None)
    if extra_mixins:
        mixins.update(extra_mixins)
    AnalysisSchema.mixins = mixins
    return AnalysisSchema
