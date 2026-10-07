"""
# nanoaod_layout.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Describe a NanoAOD-like TTree from its branch names alone.

Pure python with no dependencies: the functions here take a list of branch names and
say which collections the tree holds, what shape each has, and how coffea's
NanoAODSchema will treat them. InspectFileTool reports this, and the framework
scaffold uses it to choose sensible starting objects for an analysis.

The grouping follows coffea's NanoAODSchema: a collection is everything before the
first underscore of a branch name, and it is a list (one entry per object) when a
matching ``n<Collection>`` counter branch exists. A branch whose whole name is that
of a collection takes the name for itself: coffea then reads it as a single branch,
and the ``<Collection>_*`` branches next to it are out of reach.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any, Dict, Iterable, List, Optional

# Collection -> behaviour assigned by coffea's NanoAODSchema (coffea 2026.7 and 2026.9;
# the 2025 table is the same without CorrT1METJet).
KNOWN_MIXINS: Dict[str, str] = {
    "CaloMET": "MissingET", "ChsMET": "MissingET", "GenMET": "MissingET", "MET": "MissingET",
    "METFixEE2017": "MissingET", "PuppiMET": "MissingET", "RawMET": "MissingET",
    "RawPuppiMET": "MissingET", "TkMET": "MissingET",
    "IsoTrack": "PtEtaPhiMCollection", "SoftActivityJet": "PtEtaPhiMCollection",
    "TrigObj": "PtEtaPhiMCollection", "FatJet": "FatJet",
    "GenDressedLepton": "PtEtaPhiMCollection", "GenIsolatedPhoton": "PtEtaPhiMCollection",
    "GenJet": "PtEtaPhiMCollection", "GenJetAK8": "PtEtaPhiMCollection", "Jet": "Jet",
    "LHEPart": "PtEtaPhiMCollection", "SubGenJetAK8": "PtEtaPhiMCollection",
    "SubJet": "PtEtaPhiMCollection", "CorrT1METJet": "PtEtaPhiMCollection",
    "Electron": "Electron", "LowPtElectron": "LowPtElectron", "Muon": "Muon",
    "Photon": "Photon", "FsrPhoton": "FsrPhoton", "Tau": "Tau", "GenVisTau": "GenVisTau",
    "GenPart": "GenParticle", "PV": "Vertex", "SV": "SecondaryVertex",
}

# Behaviours that are positions rather than momentum vectors: x, y and z are theirs.
VERTEX_MIXINS = ("Vertex", "SecondaryVertex")

# Field names coffea's vector behaviours read as coordinates, grouped as coffea groups
# them (coffea.nanoevents.methods.vector in 2026.7 and 2026.9). A vector collection may
# carry one name of each group and one representation of each direction; coffea 2025
# does not check.
_ALIAS_GROUPS = {
    "x component": frozenset({"x", "px"}),
    "y component": frozenset({"y", "py"}),
    "z component": frozenset({"z", "pz"}),
    "transverse momentum": frozenset({"rho", "pt"}),
    "energy or mass": frozenset({"t", "tau", "E", "e", "energy", "M", "m", "mass"}),
}
_POLAR = frozenset({"pt", "eta", "phi", "mass"})
_LONGITUDINAL = frozenset({"z", "pz", "theta", "eta"})
_TEMPORAL = _ALIAS_GROUPS["energy or mass"]

# What coffea (2026.5 and newer, with awkward 2.14) insists on finding in a collection
# before it builds it. Candidates are Lorentz vectors with a charge.
_CANDIDATE_MIXINS = frozenset({"GenVisTau", "Electron", "LowPtElectron", "Muon", "Tau", "Photon",
                               "FsrPhoton", "Jet", "FatJet"})
_LORENTZ_MIXINS = _CANDIDATE_MIXINS | {"PtEtaPhiMCollection", "GenParticle"}
# Fields NanoAODSchema supplies itself for lists of objects (as constants, or under
# another branch's name), in the coffea versions that check.
_SUPPLIED_BY_SCHEMA = {
    "Photon": {"mass", "charge"}, "Jet": {"charge"}, "FatJet": {"charge"}, "TrigObj": {"mass"},
    "FsrPhoton": {"mass", "charge"}, "CorrT1METJet": {"mass", "pt"}, "IsoTrack": {"mass"},
    "SoftActivityJet": {"mass"},
}
# Branches NanoAODSchema renames out of the way itself before the check.
_RENAMED_BY_SCHEMA = {"Electron": {"energy"}, "Photon": {"energy"}}

# The default name an analysis gives each standard collection, in the order they are
# offered. Only these are picked up automatically; anything else is opt-in.
DEFAULT_OBJECTS = [
    ("pvs", "PV"), ("muons", "Muon"), ("electrons", "Electron"), ("photons", "Photon"),
    ("jets", "Jet"), ("met", "MET"), ("hlt", "HLT"), ("flags", "Flag"), ("gens", "GenPart"),
]

# Standard collections that hold one set of values per event, for when no file is at
# hand to look at. Every collection with the MissingET behaviour is one as well;
# anything else is taken to be a list of objects.
STANDARD_RECORDS = frozenset({
    "PV", "HLT", "Flag", "L1", "Generator", "Pileup", "LHE", "LHEWeight", "BeamSpot", "Rho",
    "GenVtx", "HTXS", "L1PreFiringWeight", "L1Reco", "L1simulation", "HLTriggerFirstPath",
    "HLTriggerFinalPath", "DeepMETResolutionTune", "DeepMETResponseTune", "btagWeight",
})

# Standard branches that hold a plain list of numbers per event, and standard lists of
# objects that have no pt/eta/phi (again for when no file is at hand).
STANDARD_VALUE_LISTS = frozenset({"PSWeight", "LHEPdfWeight", "LHEScaleWeight",
                                  "LHEReweightingWeight"})
STANDARD_NOT_KINEMATIC = frozenset({"OtherPV"})

# Collections that only exist in simulation.
MC_ONLY = ("GenPart", "GenJet", "GenJetAK8", "GenMET", "GenVisTau", "GenDressedLepton",
           "GenIsolatedPhoton", "LHEPart", "Generator", "Pileup", "LHE", "SubGenJetAK8")

# A mass for a collection that stores none, from the words its name is made of
# ("DSAMuon" -> DSA + Muon). Checked in this order; whole words only, so that
# "MultiJet" is not taken for a muon nor "SelectedTrack" for an electron.
_MASS_GUESSES = [
    (lambda word: "muon" in word or word == "mu", 0.105658, "the muon mass"),
    (lambda word: word.startswith("ele"), 0.000511, "the electron mass"),
    (lambda word: word.startswith("pho") or word == "gamma", 0.0, "zero"),
    (lambda word: word.startswith("tau"), 1.77686, "the tau mass"),
    (lambda word: word.startswith(("track", "trk", "cand")) or word == "pf", 0.13957,
     "the charged-pion mass"),
]
_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")


def read_tree_layout(path: str, tree_name: str = "Events") -> Dict[str, Any]:
    """Open a ROOT file and return its trees and the branch names of one of them.

    Returns ``{"trees": {name: n_entries}, "tree": tree_name, "kind", "branches": [...],
    "unreadable": {name: reason}}``. ``kind`` is "TTree" or "RNTuple": an RNTuple has
    entries and field names too, but they are not laid out as the functions below
    assume. Raises ImportError if uproot is missing, KeyError if the tree is not in
    the file and RuntimeError if it is there but cannot be read. This is the only
    function here that needs uproot, and it imports it lazily.
    """
    import uproot

    def reason(exc):
        return f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}"

    with uproot.open(path) as handle:
        try:
            classes = {k.split(";")[0]: str(v) for k, v in handle.classnames().items()}
        except Exception:
            classes = {}
        trees: Dict[str, int] = {}
        kinds: Dict[str, str] = {}
        unreadable: Dict[str, str] = {}
        for key in dict.fromkeys(k.split(";")[0] for k in handle.keys(cycle=False)):
            try:
                obj = handle[key]
                if not hasattr(obj, "num_entries"):
                    continue
                trees[key] = int(obj.num_entries)
            except Exception as exc:
                unreadable[key] = reason(exc)
                continue
            described = classes.get(key, "") + " " + type(obj).__name__
            kinds[key] = "RNTuple" if "RNTuple" in described else "TTree"
        if tree_name not in trees:
            if tree_name in unreadable:
                raise RuntimeError(f"'{tree_name}' is in the file but could not be read "
                                   f"({unreadable[tree_name]})")
            broken = f"; could not be read: {sorted(unreadable)}" if unreadable else ""
            raise KeyError(f"TTree '{tree_name}' is not in the file (present: {sorted(trees)}"
                           f"{broken})")
        branches = [b.split(";")[0] for b in handle[tree_name].keys()]
    return {"trees": trees, "tree": tree_name, "kind": kinds[tree_name], "branches": branches,
            "unreadable": unreadable}


def split_collections(branch_names: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Group branch names into collections the way coffea's NanoAODSchema does.

    Returns ``{collection: {"kind", "counter", "fields"}}`` in alphabetical order, with
    ``kind`` one of

    - ``"jagged"``    several objects per event (``nMuon`` + ``Muon_*``),
    - ``"record"``    one set of values per event (``MET_*``, ``HLT_*``),
    - ``"value"``     a single branch per event (``run``, ``genWeight``),
    - ``"jagged_value"``  a single branch holding a list per event (``nPSWeight`` + ``PSWeight``).

    A branch named exactly like a collection (``Foo`` next to ``Foo_pt``) is what
    coffea reads under that name: the entry is then a ``"value"`` (or
    ``"jagged_value"``) and lists the branches that became unreachable as
    ``"shadowed"``.
    """
    names = sorted(set(branch_names))
    name_set = set(names)
    prefixes = {n.split("_", 1)[0] for n in names}
    counters = {p for p in prefixes if p.startswith("n") and p[1:] in prefixes}
    collections: Dict[str, Dict[str, Any]] = {}
    for coll in sorted(prefixes - counters):
        prefix = coll + "_"
        fields = [n[len(prefix):] for n in names if n.startswith(prefix)]
        counter = "n" + coll if "n" + coll in name_set else None
        if coll in name_set:
            collections[coll] = {"kind": "jagged_value" if counter else "value",
                                 "counter": counter, "fields": []}
            if fields:
                collections[coll]["shadowed"] = fields
            continue
        if fields and counter:
            kind = "jagged"
        elif fields:
            kind = "record"
        elif counter:
            kind = "jagged_value"
        else:
            kind = "value"
        collections[coll] = {"kind": kind, "counter": counter, "fields": fields}
    return collections


def coordinate_conflicts(fields: Iterable[str]) -> List[str]:
    """Why coffea (2026 and newer) refuses a vector collection with these fields.

    Mirrors the check coffea runs the first time such a collection is used. An empty
    list means the fields describe one consistent momentum vector.
    """
    fields = set(fields)
    problems = []
    for label, aliases in _ALIAS_GROUPS.items():
        overlap = sorted(fields & aliases)
        if len(overlap) > 1:
            problems.append(f"more than one {label} ({', '.join(overlap)})")
    cartesian = fields & {"x", "px", "y", "py"}
    polar = fields & {"rho", "pt", "phi"}
    has_xy = bool(fields & {"x", "px"}) and bool(fields & {"y", "py"})
    has_rhophi = bool(fields & {"rho", "pt"}) and "phi" in fields
    if (has_xy and polar) or (has_rhophi and cartesian):
        problems.append(f"cartesian ({', '.join(sorted(cartesian))}) and polar "
                        f"({', '.join(sorted(polar))}) azimuthal coordinates")
    longitudinal = sorted(fields & {"z", "pz", "theta", "eta"})
    if sum((bool(fields & {"z", "pz"}), "theta" in fields, "eta" in fields)) > 1:
        problems.append(f"more than one longitudinal coordinate ({', '.join(longitudinal)})")
    return problems


def hidden_duplicates(fields: Iterable[str]) -> List[str]:
    """The cartesian momentum fields the framework's AnalysisSchema leaves unread."""
    fields = set(fields)
    hidden = []
    if {"pt", "phi"} <= fields:
        hidden += [f for f in ("px", "py") if f in fields]
    if {"pt", "eta"} <= fields and "pz" in fields:
        hidden.append("pz")
    return hidden


def missing_required(name: str, info: Dict[str, Any]) -> List[str]:
    """Fields without which coffea (2026.5 and newer) refuses to build the collection.

    Vertices need a position, Lorentz vectors a transverse momentum with its angle, a
    longitudinal coordinate and a mass or energy, candidates a charge as well, and a
    missing-energy vector its magnitude and angle. Empty for collections coffea does
    not give a behaviour, and on older coffea nothing is checked at all.
    """
    mixin = KNOWN_MIXINS.get(name)
    if mixin is None or info.get("kind") not in ("jagged", "record"):
        return []
    have = set(info.get("fields") or [])
    if info["kind"] == "jagged":
        have |= _SUPPLIED_BY_SCHEMA.get(name, set())
    missing: List[str] = []
    if mixin in VERTEX_MIXINS:
        missing += sorted({"x", "y", "z"} - have)
        if mixin == "SecondaryVertex":
            missing += [f for f in ("pt", "eta", "phi", "mass") if f not in have]
        return missing
    azimuthal = (bool(have & {"pt", "rho"}) and "phi" in have) or \
        (bool(have & {"x", "px"}) and bool(have & {"y", "py"}))
    if not azimuthal:
        missing += [f for f in ("pt", "phi") if f not in have]
    if mixin in _LORENTZ_MIXINS:
        if not have & _LONGITUDINAL:
            missing.append("eta")
        if not have & _TEMPORAL:
            missing.append("mass")
        if mixin in _CANDIDATE_MIXINS and "charge" not in have:
            missing.append("charge")
    return missing


def collection_traits(name: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """What a collection can do: kinematics, mass, charge, and how the schema sees it.

    ``duplicate_momenta`` are the px/py/pz fields stored next to pt/eta/phi (which
    AnalysisSchema hides for collections with a vector behaviour). ``conflicts`` is
    what would still stop coffea 2026+ from building the collection after that,
    ``conflicting_fields`` the fields to hide to resolve it, and ``missing_required``
    the fields it would refuse the collection for lacking.
    """
    fields = set(info.get("fields") or [])
    mixin = KNOWN_MIXINS.get(name)
    duplicate = hidden_duplicates(fields)
    conflicts: List[str] = []
    conflicting: List[str] = []
    if mixin is not None and mixin not in VERTEX_MIXINS:
        seen = fields - set(duplicate) - _RENAMED_BY_SCHEMA.get(name, set())
        conflicts = coordinate_conflicts(seen)
        keep = _POLAR
        if mixin == "MissingET":
            keep = frozenset({"pt", "phi"})
            extra = sorted(seen & (_LONGITUDINAL | _TEMPORAL))
            if extra:
                conflicts.append("longitudinal or temporal coordinates on a two-dimensional "
                                 f"vector ({', '.join(extra)})")
        if conflicts:
            reserved = set().union(*_ALIAS_GROUPS.values()) | {"theta", "eta", "phi"}
            conflicting = sorted((seen & reserved) - keep)
    return {
        "has_kinematics": {"pt", "eta", "phi"} <= fields,
        "has_mass": "mass" in fields,
        "has_charge": "charge" in fields,
        "mixin": mixin,
        "duplicate_momenta": duplicate,
        "conflicts": conflicts,
        "conflicting_fields": conflicting,
        "missing_required": missing_required(name, info),
    }


def missing_fields_fix(name: str, missing: List[str]) -> str:
    """How to make such a collection readable, in terms of the schema component."""
    constants = [f for f in missing if f in ("mass", "charge")]
    if constants and len(constants) == len(missing):
        values = ", ".join(f"'{name}_{f}': <value>" for f in constants)
        return (f"give it the missing field with the schema component "
                f"(constant_fields={{{values}}}), or read it as a plain collection "
                f"(mixins={{'{name}': 'NanoCollection'}})")
    return (f"read it as a plain collection with the schema component "
            f"(mixins={{'{name}': 'NanoCollection'}}): its branches stay available")


def guess_mass(collection: str):
    """A plausible fixed mass (GeV) for a collection with no mass branch, with its reason."""
    words = [word.lower() for word in _WORDS.findall(collection)]
    for matches, mass, reason in _MASS_GUESSES:
        if any(matches(word) for word in words):
            return mass, reason
    return 0.0, "zero"


def schema_notes(collections: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Things about this tree that change how it has to be read with coffea.

    Each note is ``{"collection", "issue", "detail"}`` with ``issue`` one of

    - ``"no_vector_behaviour"``  a collection with pt/eta/phi that NanoAODSchema does
      not know: its branches are readable but delta_r, px, mass, ``+`` are not there;
    - ``"duplicate_momenta"``    a collection with a vector behaviour that stores both
      (pt, eta, phi) and (px, py, pz): recent coffea refuses to build it;
    - ``"conflicting_coordinates"``  a collection with a vector behaviour and other
      fields coffea reads as a second set of coordinates (a position stored as x, y, z,
      an energy next to a mass): recent coffea refuses that too;
    - ``"missing_fields"``       a collection coffea knows that lacks a field its
      behaviour needs (a vertex without x, y, z; a candidate without charge): recent
      coffea refuses to build it;
    - ``"shadowed_branches"``    a branch named exactly like a collection, which makes
      the collection's other branches unreachable.
    """
    notes: List[Dict[str, Any]] = []
    for name, info in collections.items():
        if info.get("shadowed"):
            hidden = ", ".join(f"{name}_{f}" for f in info["shadowed"][:6])
            more = "" if len(info["shadowed"]) <= 6 else f" and {len(info['shadowed']) - 6} more"
            notes.append({
                "collection": name, "issue": "shadowed_branches",
                "detail": (f"the file has a branch called {name} itself, so NanoAODSchema "
                           f"reads '{name}' as that branch and {hidden}{more} cannot be reached "
                           f"through it. Hide the branch {name} with a customised schema "
                           f"(hidden_branches=['{name}']) to get the collection instead."),
            })
        if info["kind"] not in ("jagged", "record"):
            continue
        traits = collection_traits(name, info)
        if traits["missing_required"]:
            notes.append({
                "collection": name, "issue": "missing_fields",
                "detail": (f"{name} has no {', '.join(traits['missing_required'])}, which the "
                           f"behaviour coffea gives it ({traits['mixin']}) needs: coffea 2026 "
                           "and newer raise a ValueError the first time it is used. To use "
                           f"it, {missing_fields_fix(name, traits['missing_required'])}."),
            })
        if traits["duplicate_momenta"] and traits["mixin"]:
            notes.append({
                "collection": name, "issue": "duplicate_momenta",
                "detail": (f"{name} stores {', '.join(traits['duplicate_momenta'])} next to "
                           "pt/eta/phi. coffea accepts one momentum representation per "
                           "vector collection: with plain NanoAODSchema recent coffea "
                           "versions raise a ValueError the first time it is used. Read only "
                           "the polar set (the framework's AnalysisSchema does this)."),
            })
        if traits["conflicts"]:
            branches = ", ".join(f"{name}_{f}" for f in traits["conflicting_fields"])
            notes.append({
                "collection": name, "issue": "conflicting_coordinates",
                "detail": (f"{name} has {'; '.join(traits['conflicts'])}. coffea 2026 and "
                           "newer raise a ValueError the first time it is used, because on "
                           "a momentum vector these names all mean coordinates. Hide the "
                           f"extra branches ({branches}) with a customised schema, or read "
                           "them separately."),
            })
        if traits["has_kinematics"] and traits["mixin"] is None:
            mass = "" if traits["has_mass"] else " It has no mass branch either."
            notes.append({
                "collection": name, "issue": "no_vector_behaviour",
                "detail": (f"{name} is not a collection NanoAODSchema knows, so it comes back "
                           "as a plain record without delta_r, px or mass." + mass +
                           " Re-zip it as Lorentz vectors before using it kinematically."),
            })
    return notes


def match_branches(branch_names: Iterable[str], pattern: str) -> List[str]:
    """Branch names matching a shell-style pattern (``HLT_DoubleL2Mu*``), sorted."""
    return sorted(n for n in set(branch_names) if fnmatch.fnmatchcase(n, pattern))


def check_required(branch_names: Iterable[str], required: Iterable[str]) -> Dict[str, List[str]]:
    """Which of the required names exist, as a branch or as a collection."""
    names = set(branch_names)
    collections = split_collections(names)
    present, missing = [], []
    for item in required:
        (present if item in names or item in collections else missing).append(item)
    return {"present": present, "missing": missing}


def object_spec(name: str, collection: str,
                collections: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Describe one analysis object: where it comes from and what it supports.

    ``collections`` is the output of split_collections for a representative file. If it
    is None the collection is assumed to follow standard NanoAOD.

    Returns a dict with ``name``, ``collection``, ``kind`` ("jagged", "record" or
    "value"), ``fields`` (None when unknown), ``kinematic`` (has pt/eta/phi), ``mixin``
    (the behaviour NanoAODSchema gives it, or None), ``wrap`` (None, or
    ``{"mass": value-or-None}`` when the collection must be re-zipped as Lorentz
    vectors), ``mc_only``, and ``notes`` (list of strings worth telling the user).
    """
    notes: List[str] = []
    mixin = KNOWN_MIXINS.get(collection)
    if collections is None:
        is_record = collection in STANDARD_RECORDS or mixin == "MissingET"
        kind = "record" if is_record else "jagged"
        if collection in STANDARD_VALUE_LISTS:
            kind = "value"
        fields = None
        kinematic = (kind == "jagged" and collection not in STANDARD_NOT_KINEMATIC) \
            or mixin == "MissingET"
        wrap = None
        if collection not in KNOWN_MIXINS and kind == "jagged":
            notes.append(f"{collection} is not a standard NanoAOD collection: if it needs "
                         "vector behaviour wrap it with as_lorentz in definitions/objects.py")
    else:
        if collection not in collections:
            raise KeyError(collection)
        info = collections[collection]
        kind = {"jagged": "jagged", "record": "record"}.get(info["kind"], "value")
        fields = list(info["fields"])
        traits = collection_traits(collection, info)
        kinematic = traits["has_kinematics"]
        wrap = None
        if kind == "jagged" and kinematic and traits["mixin"] is None:
            if traits["has_mass"]:
                wrap = {"mass": None}
                notes.append(f"{collection} is unknown to NanoAODSchema: it is re-zipped as "
                             "Lorentz vectors so that delta_r, px and mass work")
            else:
                mass, reason = guess_mass(collection)
                wrap = {"mass": mass}
                notes.append(f"{collection} is unknown to NanoAODSchema and has no mass "
                             f"branch: it is re-zipped as Lorentz vectors with {reason} "
                             f"({mass} GeV); change it in definitions/objects.py if that is wrong")
        if traits["duplicate_momenta"] and traits["mixin"]:
            notes.append(f"{collection} stores px/py/pz as well as pt/eta/phi: AnalysisSchema "
                         "reads the polar set only")
        if traits["conflicts"]:
            branches = [f"{collection}_{f}" for f in traits["conflicting_fields"]]
            notes.append(f"{collection} has {'; '.join(traits['conflicts'])}: coffea 2026 and "
                         "newer refuse to build it. Hide the extra branches with the schema "
                         f"component (hidden_branches={branches})")
        if traits["missing_required"]:
            notes.append(f"{collection} has no {', '.join(traits['missing_required'])}: coffea "
                         "2026 and newer refuse to build it. To use it there, "
                         + missing_fields_fix(collection, traits["missing_required"]))
        if info.get("shadowed"):
            notes.append(f"{collection} is a single branch in this file, which keeps the "
                         f"{len(info['shadowed'])} branches named {collection}_... out of "
                         f"reach: hide it with the schema component "
                         f"(hidden_branches=['{collection}']) to read them as a collection")
        positions = [f for f in ("x", "y", "z") if f in fields]
        if wrap is not None and positions:
            notes.append(f"{collection} stores a position as {', '.join(positions)}: on the "
                         "re-zipped collection these are "
                         f"{', '.join('v' + f for f in positions)}, because x, y and z of a "
                         "Lorentz vector are its momentum components")
    return {
        "name": name, "collection": collection, "kind": kind, "fields": fields,
        "kinematic": bool(kinematic), "mixin": mixin, "wrap": wrap,
        "mc_only": collection in MC_ONLY or collection.startswith("Gen"), "notes": notes,
    }


def _default_candidates(collections: Dict[str, Dict[str, Any]]) -> List[tuple]:
    """(object name, collection) for the standard collections the file has."""
    found = []
    for name, collection in DEFAULT_OBJECTS:
        if collection in collections:
            found.append((name, collection))
        elif collection == "MET" and "PuppiMET" in collections:
            found.append((name, "PuppiMET"))
    return found


def _usable_by_default(collection: str, info: Dict[str, Any]) -> bool:
    return info["kind"] in ("jagged", "record") and not missing_required(collection, info)


def default_objects(collections: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, str]:
    """``{object name: collection}`` for the standard collections worth starting with.

    With a file layout only the collections that exist are returned (PuppiMET stands in
    for a missing MET), and those that recent coffea would refuse to build are left
    out (see skipped_defaults). Without one, the standard NanoAOD set is assumed.
    """
    if collections is None:
        return dict(DEFAULT_OBJECTS)
    return {name: collection for name, collection in _default_candidates(collections)
            if _usable_by_default(collection, collections[collection])}


def skipped_defaults(collections: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, str]:
    """``{object name: why}`` for standard collections of the file not offered by default."""
    notes: Dict[str, str] = {}
    for name, collection in _default_candidates(collections or {}):
        info = collections[collection]
        if _usable_by_default(collection, info):
            continue
        if info["kind"] not in ("jagged", "record"):
            notes[name] = (f"{collection} is a single branch in this file, not a collection: "
                           f"no '{name}' object was defined")
            continue
        missing = missing_required(collection, info)
        notes[name] = (f"{collection} has no {', '.join(missing)}, so coffea 2026 and newer "
                       f"refuse to build it: no '{name}' object was defined. To use it, "
                       + missing_fields_fix(collection, missing)
                       + ", then add the object to definitions/objects.py")
    return notes


def other_kinematic_collections(collections: Dict[str, Dict[str, Any]],
                                used: Iterable[str]) -> List[str]:
    """Jagged collections with pt/eta/phi that the analysis is not using yet."""
    used = set(used)
    return [name for name, info in collections.items()
            if info["kind"] == "jagged" and name not in used
            and collection_traits(name, info)["has_kinematics"]]


def summarise(collections: Dict[str, Dict[str, Any]], max_fields: int = 12) -> Dict[str, Any]:
    """Compact per-collection summary for tool output (field lists truncated)."""
    summary: Dict[str, Any] = {}
    for name, info in collections.items():
        if info["kind"] in ("value", "jagged_value"):
            continue
        fields = info["fields"]
        entry: Dict[str, Any] = {"kind": info["kind"], "n_fields": len(fields)}
        if info["counter"]:
            entry["counter"] = info["counter"]
        if max_fields and len(fields) > max_fields:
            entry["fields"] = fields[:max_fields]
            entry["fields_truncated"] = True
        else:
            entry["fields"] = fields
        summary[name] = entry
    return summary
