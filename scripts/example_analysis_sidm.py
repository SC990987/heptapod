"""
# example_analysis_sidm.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

An example analysis module for `scripts/lpc_scaleout.py`, written against a
real LLPnanoAOD signal sample (SIDM Bs -> 2 dark photons).

The contract is two functions:

    open_events(path)        optional -- how to open one file
    process(events, meta)    required -- returns a dict merged by addition

It also demonstrates the two LLPnanoAOD quirks the `coffea` skill documents:

  * `GenPart` carries BOTH cartesian (px,py,pz) and polar (pt,eta,phi) momenta.
    coffea's vector behaviour refuses the ambiguity, so the GenPart mixin has to
    be dropped and the collection re-zipped with polar coordinates only.
  * Custom collections such as `DSAMuon` get no behaviour at all -- no
    `delta_r`, no `px` -- until you re-zip them the same way.
"""
from __future__ import annotations

import awkward as ak
import numpy as np
import hist

from coffea.nanoevents import NanoEventsFactory, NanoAODSchema
from coffea.nanoevents.methods import vector as cvector

DARK_PHOTON_PDGID = 32


class LLPNanoAODSchema(NanoAODSchema):
    """Tolerant of skims, and without the GenPart mixin that LLPnanoAOD breaks."""
    warn_missing_crossrefs = False
    error_missing_event_ids = False


_mixins = dict(LLPNanoAODSchema.mixins)
_mixins.pop("GenPart", None)
LLPNanoAODSchema.mixins = _mixins


def open_events(path):
    return NanoEventsFactory.from_root(
        {path: "Events"}, schemaclass=LLPNanoAODSchema).events()


def as_vectors(coll, keep=()):
    """Give a raw collection coffea's Lorentz-vector behaviour.

    Uses polar coordinates only, which is what resolves the LLPnanoAOD
    cartesian/polar conflict."""
    fields = {
        "pt": coll.pt, "eta": coll.eta, "phi": coll.phi,
        "mass": coll.mass if "mass" in coll.fields else ak.zeros_like(coll.pt),
    }
    for f in keep:
        if f in coll.fields:
            fields[f] = coll[f]
    return ak.zip(fields, with_name="PtEtaPhiMLorentzVector",
                  behavior=cvector.behavior)


def process(events, meta):
    n = len(events)

    gen = as_vectors(events.GenPart, ("pdgId", "status", "vx", "vy", "vz"))
    dsa = as_vectors(events.DSAMuon, ("charge",))
    mu = events.Muon

    # --- gen-level dark photons -------------------------------------- #
    dp = gen[abs(gen.pdgId) == DARK_PHOTON_PDGID]
    dp_pt = ak.to_numpy(ak.flatten(dp.pt))

    # Transverse DECAY length: the resonance's own (vx, vy) is where it was
    # PRODUCED; its daughters' (vx, vy) is where it DECAYED. Lxy is the
    # distance between the two.
    #
    # `hypot(dp.vx, dp.vy)` is a different quantity -- the production vertex's
    # distance from the origin, ~0.04 cm here -- and is NOT a decay length.
    # Getting this wrong is an 800x error that looks entirely plausible.
    # GenKinematicsTool is the reference implementation of this definition.
    raw = events.GenPart
    is_lep = (abs(raw.pdgId) == 11) | (abs(raw.pdgId) == 13)
    lep = raw[is_lep & (raw.genPartIdxMother >= 0)]
    mother = raw[lep.genPartIdxMother]
    from_dp = abs(mother.pdgId) == DARK_PHOTON_PDGID
    lep, mother = lep[from_dp], mother[from_dp]
    if "vx" in raw.fields and ak.sum(ak.num(lep)):
        # one entry per lepton daughter, i.e. two per dark photon
        lxy = ak.to_numpy(ak.flatten(
            np.hypot(lep.vx - mother.vx, lep.vy - mother.vy)))
    else:
        lxy = np.empty(0)

    # --- DSA muons matched to PAT muons ------------------------------- #
    if ak.sum(ak.num(dsa)) and ak.sum(ak.num(mu)):
        _, dr = dsa.nearest(mu, return_metric=True)
        dr_flat = ak.to_numpy(ak.drop_none(ak.flatten(dr)))
    else:
        dr_flat = np.empty(0)

    # --- histograms ---------------------------------------------------- #
    h_dp_pt = hist.Hist(hist.axis.Regular(50, 0, 500, name="pt",
                                          label=r"dark photon $p_T$ [GeV]"),
                        storage=hist.storage.Weight())
    h_dp_pt.fill(pt=dp_pt)

    h_lxy = hist.Hist(hist.axis.Regular(50, 0, 200, name="lxy",
                                        label=r"$L_{xy}$ [cm]"),
                      storage=hist.storage.Weight())
    h_lxy.fill(lxy=lxy)

    h_dr = hist.Hist(hist.axis.Regular(50, 0, 1.0, name="dr",
                                       label=r"$\Delta R$(DSA, PAT muon)"),
                     storage=hist.storage.Weight())
    h_dr.fill(dr=dr_flat)

    h_nmu = hist.Hist(hist.axis.Integer(0, 12, name="n", label="muons / event"),
                      storage=hist.storage.Weight())
    h_nmu.fill(n=ak.to_numpy(ak.num(mu)))

    return {
        "n_events": n,
        "n_dark_photons": int(ak.sum(ak.num(dp))),
        "n_dsa_muons": int(ak.sum(ak.num(dsa))),
        "n_pat_muons": int(ak.sum(ak.num(mu))),
        "n_dsa_matched_dr0p1": int((dr_flat < 0.1).sum()),
        "dark_photon_pt": h_dp_pt,
        "dark_photon_lxy": h_lxy,
        "dsa_pat_dr": h_dr,
        "n_muons": h_nmu,
    }
