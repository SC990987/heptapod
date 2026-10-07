"""Lepton jets: anti-kT clusters of nearby leptons and photons.

``build_lepton_jets`` takes the selected objects of a channel, clusters the chosen
source collections with fastjet, and returns one jagged collection of lepton jets with
Lorentz-vector behaviour (``pt``, ``eta``, ``mass``, ``delta_r``, ``+`` ...) and these
extra fields:

- ``constituents``      the clustered objects of each lepton jet, as light records
                        (pt, eta, phi, mass, any carried fields, ``src``, ``idx``)
- ``n_constituents``    how many there are
- ``<source>``          the constituents that came from one source collection
- ``<source>_n``        how many there are
- ``dRSpread``          largest dR between any two constituents
- ``matched_jet``, ``lepton_fraction``, ``isolation``, ``dR_matched_jet``
                        only when a jet collection is given (see ``build_lepton_jets``)

It is registered as a derived object in definitions/objects.py, so the lepton jets of
each channel are built from the sources *after* that channel's object cuts.

A constituent only carries the fields listed in ``carry``. To get at everything the
original object has, use ``source_objects``.
"""

import awkward as ak
import fastjet
import numpy as np
from coffea.nanoevents.methods import nanoaod

from analysis_pkg.tools import utilities

_RESERVED_FIELDS = utilities.COORDINATE_NAMES | {"src", "idx"}
_LJ_FIELDS = ("constituents", "n_constituents", "dRSpread", "matched_jet", "lepton_fraction",
              "isolation", "dR_matched_jet")


def _normalise_sources(sources):
    """``["muons", ...]`` or ``{"muons": {"mass": ...}, ...}`` -> ordered ``{name: {...}}``."""
    if isinstance(sources, dict):
        normalised = {name: dict(cfg or {}) for name, cfg in sources.items()}
    else:
        normalised = {name: {} for name in sources}
    if not normalised:
        raise ValueError("lepton jets need at least one source collection")
    for name in normalised:
        if name in _RESERVED_FIELDS or name in _LJ_FIELDS or name.endswith("_n"):
            raise ValueError(f"'{name}' cannot be a lepton-jet source: the name clashes with a "
                             "field of the lepton jets")
    return normalised


def _source_records(name, coll, source_id, fixed_mass, carry):
    """Light (pt, eta, phi, mass, ...) records for one source, tagged with where they came from."""
    if coll.ndim != 2:
        raise ValueError(f"lepton-jet source '{name}' must be a jagged collection "
                         "(several objects per event)")
    pt = coll.pt
    if fixed_mass is not None:
        mass = ak.full_like(pt, fixed_mass)
    else:
        try:
            mass = coll.mass
        except Exception as exc:
            if utilities.is_io_error(exc):
                raise
            raise ValueError(
                f"lepton-jet source '{name}' has no mass: give it one in the sources "
                f"({{'{name}': {{'mass': <GeV>}}}}) or build the object with as_lorentz(..., mass=...)"
            ) from exc
    index = ak.local_index(pt, axis=1)
    columns = {
        "pt": pt, "eta": coll.eta, "phi": coll.phi, "mass": mass,
        "src": ak.zeros_like(index) + source_id,   # which source collection
        "idx": index,                              # position in that collection
    }
    for field, fill in carry.items():
        if field in coll.fields:
            if coll[field].ndim != 2:
                raise ValueError(f"cannot carry '{field}' of '{name}': it is not one value per object")
            columns[field] = coll[field]
        else:
            # in the type of the fill value, so that a flag or an integer stays one
            # when the sources are put together
            columns[field] = ak.full_like(pt, fill, dtype=np.result_type(fill))
    return ak.zip(columns, with_name="PtEtaPhiMLorentzVector", behavior=nanoaod.behavior)


def _take(array, nested_index):
    """Pick objects of each event by a per-group list of positions.

    array: events -> objects; nested_index: events -> groups -> positions in the event's
    list. Returns events -> groups -> objects. (This is how fastjet itself returns the
    constituents of its jets.)
    """
    counts = ak.num(nested_index, axis=1)
    duplicate = ak.unflatten(np.zeros(int(ak.sum(counts)), dtype=np.int64), counts)
    return array[:, np.newaxis][duplicate][nested_index]


def _no_jets(n_events):
    """What clustering returns when no event has anything to cluster."""
    per_event = np.zeros(n_events, dtype=np.int64)
    empty = ak.unflatten(np.zeros(0, dtype=np.float64), per_event)
    jets = ak.zip({"px": empty, "py": empty, "pz": empty, "E": empty})
    no_groups = ak.unflatten(np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64))
    return jets, ak.unflatten(no_groups, per_event)


def _component(jets, *names):
    """A four-momentum component of fastjet's output, whichever way it names its fields."""
    for name in names:
        if name in jets.fields:
            return jets[name]
    return getattr(jets, names[0])


def cluster(parts, distance_param=0.4, min_pt=0.0):
    """Anti-kT cluster a jagged array of (pt, eta, phi, mass) records.

    Returns ``(p4, constituent_index)``: the jets as LorentzVectors, and for each jet
    the positions of its constituents in the event's list of ``parts``.
    """
    # In double precision from the start: the files store single precision, and the
    # mass of a collimated pair is a small difference of large numbers. Computed in
    # single precision, a 0.25 GeV pair at 300 GeV comes out several per cent off.
    pt, eta, phi, mass = (ak.values_astype(parts[field], np.float64)
                          for field in ("pt", "eta", "phi", "mass"))
    px = pt * np.cos(phi)
    py = pt * np.sin(phi)
    pz = pt * np.sinh(eta)
    energy = np.sqrt(px ** 2 + py ** 2 + pz ** 2 + mass ** 2)
    if int(ak.sum(ak.num(parts, axis=1))) == 0:
        jets, constituent_index = _no_jets(len(parts))
    else:
        # fastjet reads px, py, pz and E straight from the record fields
        pseudojets = ak.to_packed(ak.zip({"px": px, "py": py, "pz": pz, "E": energy}))
        jet_def = fastjet.JetDefinition(fastjet.antikt_algorithm, float(distance_param))
        sequence = fastjet.ClusterSequence(pseudojets, jet_def)
        jets = sequence.inclusive_jets(min_pt)
        constituent_index = sequence.constituent_index(min_pt)
    p4 = ak.zip(
        {
            "x": _component(jets, "px", "x"),
            "y": _component(jets, "py", "y"),
            "z": _component(jets, "pz", "z"),
            "t": _component(jets, "E", "t"),
        },
        with_name="LorentzVector",
        behavior=nanoaod.behavior,
    )
    return p4, constituent_index


def build_lepton_jets(objs, sources, distance_param=0.4, carry=None, jets=None,
                      jet_match_dr=0.4, lepton_fraction_fields=("chEmEF", "neEmEF", "muEF"),
                      min_pt=0.0):
    """Cluster the source collections of ``objs`` into lepton jets.

    objs: the selected objects of a channel
    sources: names of the collections to cluster, e.g. ``["muons", "electrons",
        "photons"]``, or ``{name: {"mass": GeV}}`` to give a source a fixed mass.
        Every source needs pt, eta, phi and a mass.
    distance_param: anti-kT radius parameter
    carry: ``{field: fill}`` of extra fields the constituents keep; ``fill`` is used for
        sources that do not have the field. Default ``{"charge": 0}``.
    jets: name of a jet collection in ``objs``. When given (and present in the sample),
        each lepton jet is matched to the nearest jet within ``jet_match_dr`` and gets
        ``isolation = (E_jet / E_lj) * (1 - lepton_fraction)``, where lepton_fraction is
        the sum of ``lepton_fraction_fields`` of that jet: the non-leptonic energy
        around the lepton jet relative to its own. Lepton jets with no matched jet get
        isolation 0.
    min_pt: drop lepton jets below this pT at clustering time

    A source that is not available in the sample makes the whole collection
    unavailable there, like any other derived object with a missing input.
    """
    sources = _normalise_sources(sources)
    carry = {"charge": 0} if carry is None else dict(carry)
    for field in carry:
        if field in _RESERVED_FIELDS:
            raise ValueError(f"'{field}' cannot be carried: the name is reserved")

    records = [
        _source_records(name, objs[name], source_id, cfg.get("mass"), carry)
        for source_id, (name, cfg) in enumerate(sources.items())
    ]
    parts = records[0] if len(records) == 1 else ak.concatenate(records, axis=1)

    p4, constituent_index = cluster(parts, distance_param, min_pt)
    constituents = _take(parts, constituent_index)

    ljs = ak.with_field(p4, constituents, "constituents")
    ljs = ak.with_field(ljs, ak.num(constituents, axis=2), "n_constituents")
    for source_id, name in enumerate(sources):
        members = constituents[constituents.src == source_id]
        ljs = ak.with_field(ljs, members, name)
        ljs = ak.with_field(ljs, ak.num(members, axis=2), f"{name}_n")

    # largest dR between any two constituents: all pairs within each lepton jet,
    # flattened per lepton jet, then the maximum (0 for a single constituent)
    pair_dr = constituents.metric_table(constituents, axis=2)
    spread = ak.max(ak.flatten(pair_dr, axis=-1), axis=-1)
    ljs = ak.with_field(ljs, ak.fill_none(spread, 0.0), "dRSpread")

    jet_collection = objs.get(jets) if jets is not None else None
    if jet_collection is not None:
        missing = [f for f in lepton_fraction_fields if f not in jet_collection.fields]
        if missing:
            raise ValueError(
                f"'{jets}' has no {missing}, which the lepton-jet isolation needs: adjust "
                "lepton_fraction_fields, or pass jets=None to skip the isolation"
            )
        matched = p4.nearest(jet_collection, threshold=jet_match_dr)
        lepton_fraction = matched[lepton_fraction_fields[0]]
        for field in lepton_fraction_fields[1:]:
            lepton_fraction = lepton_fraction + matched[field]
        isolation = (matched.energy / p4.energy) * (1 - lepton_fraction)
        ljs = ak.with_field(ljs, matched, "matched_jet")
        ljs = ak.with_field(ljs, lepton_fraction, "lepton_fraction")
        ljs = ak.with_field(ljs, ak.fill_none(isolation, 0.0), "isolation")
        ljs = ak.with_field(ljs, p4.delta_r(matched), "dR_matched_jet")
    return ljs


def source_objects(objs, ljs, source):
    """The original objects that make up each lepton jet, with all of their fields.

    ``ljs[source]`` holds light copies of the constituents. This goes back to
    ``objs[source]`` and returns the full objects in the same events -> lepton jets ->
    constituents layout, so that any branch (and the collection's own behaviour) can be
    used, e.g. ``source_objects(objs, objs["ljs"], "muons").dxy``.

    ``objs`` must be the same selection the lepton jets were built from.
    """
    return _take(objs[source], ljs[source].idx)


def leading_pair_dphi(ljs):
    """|dphi| between the two leading lepton jets (missing where there are fewer than two)."""
    padded = ak.pad_none(ljs, 2, axis=1)
    return abs(padded[:, 0].delta_phi(padded[:, 1]))
