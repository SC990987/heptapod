"""
# synthetic.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Write a small NanoAOD-like ROOT file with a known answer, for testing the framework.

Every event holds a resonance at rest that decays into two light particles going back
to back, one to a collimated muon pair and one to a collimated electron pair. So:

- there are two muons and two electrons above any reasonable threshold;
- clustered with R = 0.4 they form exactly two lepton jets, back to back;
- the invariant mass of those two lepton jets is the resonance mass.

Around that sit the things real files have and that trip code up: soft extra muons and
photons below the usual thresholds, jets (two of them overlapping the lepton pairs),
MET, a PV record with a few events failing a vertex filter, trigger and filter flags, a
custom collection without a mass branch (DSAMuon) that the muons point into through
index branches stored as floats (as LLPNanoAOD stores them), and a GenPart that stores
its momentum both as (pt, eta, phi) and as (px, py, pz). Data files have no generator
information, come in two runs, the first of which is the one a test golden JSON
rejects, and carry a collection whose counter has an underscore in its name
(nProton_multiRP), as CMS data does.

Needs awkward, numpy and uproot.
"""
import json

import awkward as ak
import numpy as np
import uproot

MUON_MASS = 0.105658
ELECTRON_MASS = 0.000511
PAIR_OPENING = 0.02          # separation of the two leptons of a pair in eta and in phi


def write_tree(handle, name, data):
    """Write ``{branch: array}`` as a TTree into an open uproot file.

    ``handle[name] = data`` is not it any more: since uproot 5.7 that writes an
    RNTuple. mktree with explicit types writes a TTree in every version; jagged record
    arrays become ``nMuon`` + ``Muon_*`` branches as before.
    """
    types = {key: value.type if isinstance(value, ak.Array) else np.asarray(value).dtype
             for key, value in data.items()}
    handle.mktree(name, types).extend(data)


def _wrap(phi):
    return (phi + np.pi) % (2 * np.pi) - np.pi


def _jagged(columns, counts):
    """{field: flat array} -> jagged record array with the given counts per event."""
    return ak.zip({name: ak.unflatten(values, counts) for name, values in columns.items()})


def _regular(columns):
    """{field: (n_events, k) array} -> jagged record array with k objects per event."""
    return ak.zip({name: ak.from_regular(ak.Array(values), axis=1) for name, values in columns.items()})


def write_nanoaod(path, n=400, seed=7, data=False, resonance_mass=500.0, tree_name="Events"):
    """Write the file and return the numbers a test can check against."""
    rng = np.random.default_rng(seed)
    f32, i32 = np.float32, np.int32

    # the two lepton pairs: back to back, each carrying half of the resonance mass
    eta1 = rng.uniform(-1.5, 1.5, n)
    phi1 = rng.uniform(-np.pi, np.pi, n)
    eta2, phi2 = -eta1, _wrap(phi1 + np.pi)
    pair_pt = (resonance_mass / 2) / np.cosh(eta1)
    share_mu = rng.uniform(0.3, 0.7, n)
    share_el = rng.uniform(0.3, 0.7, n)
    half = PAIR_OPENING / 2

    def pair(pt, share, eta, phi):
        return (np.stack([pt * share, pt * (1 - share)], axis=1),
                np.stack([eta + half, eta - half], axis=1),
                np.stack([_wrap(phi + half), _wrap(phi - half)], axis=1))

    n_dsa = rng.integers(0, 3, n)                   # objects of the custom collection, built below
    mu_pt, mu_eta, mu_phi = pair(pair_pt, share_mu, eta1, phi1)
    el_pt, el_eta, el_phi = pair(pair_pt, share_el, eta2, phi2)
    charges = np.tile(np.array([1, -1], dtype=i32), (n, 1))
    true2 = np.ones((n, 2), dtype=bool)

    signal_muons = _regular({
        "pt": mu_pt.astype(f32), "eta": mu_eta.astype(f32), "phi": mu_phi.astype(f32),
        "mass": np.full((n, 2), MUON_MASS, dtype=f32), "charge": charges,
        "looseId": true2, "mediumId": true2, "tightId": true2,
        "jetIdx": np.zeros((n, 2), dtype=i32), "genPartIdx": np.tile(np.array([3, 4], dtype=i32), (n, 1)),
        # The first signal muon points at the first DSAMuon of its event, and at the
        # second one too, where there are any. Stored as floats, -1.0 for "none".
        "dsaMatch1idx": np.stack([np.where(n_dsa > 0, 0, -1), np.full(n, -1)], axis=1).astype(f32),
        "dsaMatch2idx": np.stack([np.where(n_dsa > 1, 1, -1), np.full(n, -1)], axis=1).astype(f32),
    })
    # soft extra muons, below any sensible threshold
    n_soft = rng.integers(0, 2, n)
    total = int(n_soft.sum())
    soft_muons = _jagged({
        "pt": rng.uniform(3, 8, total).astype(f32), "eta": rng.uniform(-2.4, 2.4, total).astype(f32),
        "phi": rng.uniform(-np.pi, np.pi, total).astype(f32),
        "mass": np.full(total, MUON_MASS, dtype=f32),
        "charge": rng.choice(np.array([-1, 1], dtype=i32), total),
        "looseId": rng.random(total) < 0.5, "mediumId": np.zeros(total, dtype=bool),
        "tightId": np.zeros(total, dtype=bool), "jetIdx": np.full(total, -1, dtype=i32),
        "genPartIdx": np.full(total, -1, dtype=i32),
        "dsaMatch1idx": np.full(total, -1, dtype=f32), "dsaMatch2idx": np.full(total, -1, dtype=f32),
    }, n_soft)
    muons = ak.to_packed(ak.concatenate([signal_muons, soft_muons], axis=1))

    electrons = _regular({
        "pt": el_pt.astype(f32), "eta": el_eta.astype(f32), "phi": el_phi.astype(f32),
        "mass": np.full((n, 2), ELECTRON_MASS, dtype=f32), "charge": charges,
        "cutBased": np.full((n, 2), 4, dtype=i32),
        "jetIdx": np.ones((n, 2), dtype=i32), "genPartIdx": np.tile(np.array([5, 6], dtype=i32), (n, 1)),
    })

    n_pho = rng.integers(0, 2, n)
    total = int(n_pho.sum())
    photons = _jagged({
        "pt": rng.uniform(5, 15, total).astype(f32), "eta": rng.uniform(-2.4, 2.4, total).astype(f32),
        "phi": rng.uniform(-np.pi, np.pi, total).astype(f32),
        "mass": np.zeros(total, dtype=f32), "charge": np.zeros(total, dtype=i32),
        "cutBased": rng.integers(0, 4, total).astype(i32), "electronVeto": rng.random(total) < 0.9,
    }, n_pho)

    # jets: one on top of each lepton pair (mostly leptonic energy), plus a few others
    lead_jets = _regular({
        "pt": (1.05 * np.stack([pair_pt, pair_pt], axis=1)).astype(f32),
        "eta": np.stack([eta1, eta2], axis=1).astype(f32),
        "phi": np.stack([phi1, phi2], axis=1).astype(f32),
        "mass": np.full((n, 2), 5.0, dtype=f32), "jetId": np.full((n, 2), 6, dtype=i32),
        "chEmEF": np.full((n, 2), 0.45, dtype=f32), "neEmEF": np.full((n, 2), 0.05, dtype=f32),
        "muEF": np.full((n, 2), 0.45, dtype=f32),
    })
    n_other = rng.integers(0, 3, n)
    total = int(n_other.sum())
    other_jets = _jagged({
        "pt": rng.uniform(30, 100, total).astype(f32), "eta": rng.uniform(-2.4, 2.4, total).astype(f32),
        "phi": rng.uniform(-np.pi, np.pi, total).astype(f32),
        "mass": rng.uniform(3, 15, total).astype(f32), "jetId": np.full(total, 6, dtype=i32),
        "chEmEF": np.full(total, 0.1, dtype=f32), "neEmEF": np.full(total, 0.1, dtype=f32),
        "muEF": np.zeros(total, dtype=f32),
    }, n_other)
    jets = ak.to_packed(ak.concatenate([lead_jets, other_jets], axis=1))

    # a collection coffea does not know, and with no mass branch
    total = int(n_dsa.sum())
    dsa_muons = _jagged({
        "pt": rng.uniform(5, 60, total).astype(f32), "eta": rng.uniform(-2.4, 2.4, total).astype(f32),
        "phi": rng.uniform(-np.pi, np.pi, total).astype(f32),
        "charge": rng.choice(np.array([-1, 1], dtype=i32), total),
        "dxy": rng.normal(0, 5, total).astype(f32),
    }, n_dsa)

    npvs_good = rng.integers(1, 40, n)
    npvs_good[rng.random(n) < 0.05] = 0            # a few events fail the vertex filter
    passes_trigger = rng.random(n) < 0.9
    tree = {
        "run": np.where(np.arange(n) < n // 2, 2, 1).astype(np.uint32),   # first half: run 2
        "luminosityBlock": rng.integers(1, 50, n).astype(np.uint32),
        "event": np.arange(n, dtype=np.uint64),
        "Muon": muons, "Electron": electrons, "Photon": photons, "Jet": jets, "DSAMuon": dsa_muons,
        "MET_pt": rng.uniform(0, 80, n).astype(f32), "MET_phi": rng.uniform(-np.pi, np.pi, n).astype(f32),
        "PV_npvs": (npvs_good + rng.integers(0, 10, n)).astype(i32), "PV_npvsGood": npvs_good.astype(i32),
        "PV_x": rng.normal(0, 0.01, n).astype(f32), "PV_y": rng.normal(0, 0.01, n).astype(f32),
        "PV_z": rng.normal(0, 4, n).astype(f32), "PV_ndof": rng.uniform(5, 100, n).astype(f32),
        "HLT_IsoMu24": passes_trigger, "HLT_Mu50": rng.random(n) < 0.5,
        "Flag_METFilters": rng.random(n) < 0.98,
    }

    truth = {
        "n_events": n, "resonance_mass": float(resonance_mass), "is_data": bool(data),
        "n_pass_trigger": int(passes_trigger.sum()),
        "n_pass_trigger_and_pv": int((passes_trigger & (npvs_good >= 1)).sum()),
        "n_first_run": int(n // 2),
        "n_muons_with_dsa": int((n_dsa > 0).sum()),
        "n_muons_with_two_dsa": int((n_dsa > 1).sum()),
    }
    if data:
        n_protons = rng.integers(0, 3, n)
        tree["Proton_multiRP"] = _jagged(
            {"xi": rng.uniform(0.02, 0.2, int(n_protons.sum())).astype(f32)}, n_protons)

    if not data:
        weights = rng.normal(1.0, 0.05, n)
        tree["genWeight"] = weights.astype(f32)
        truth["sum_gen_weights"] = float(weights.astype(f32).astype(np.float64).sum())
        zeros = np.zeros(n)
        # resonance, its two daughters, then the four leptons
        gen_pt = np.stack([zeros, pair_pt, pair_pt, mu_pt[:, 0], mu_pt[:, 1], el_pt[:, 0], el_pt[:, 1]], axis=1)
        gen_eta = np.stack([zeros, eta1, eta2, mu_eta[:, 0], mu_eta[:, 1], el_eta[:, 0], el_eta[:, 1]], axis=1)
        gen_phi = np.stack([zeros, phi1, phi2, mu_phi[:, 0], mu_phi[:, 1], el_phi[:, 0], el_phi[:, 1]], axis=1)
        flight = rng.exponential(5.0, (n, 2))                       # transverse decay lengths in cm
        vx = np.zeros((n, 7))
        vy = np.zeros((n, 7))
        for lepton, parent, phi in ((3, 0, phi1), (4, 0, phi1), (5, 1, phi2), (6, 1, phi2)):
            vx[:, lepton] = flight[:, parent] * np.cos(phi)
            vy[:, lepton] = flight[:, parent] * np.sin(phi)
        flags = (1 << 13) | (1 << 8) | (1 << 0)                     # last copy, from hard process, prompt
        tree["GenPart"] = _regular({
            "pt": gen_pt.astype(f32), "eta": gen_eta.astype(f32), "phi": gen_phi.astype(f32),
            "mass": np.tile(np.array([resonance_mass, 0.25, 0.25, MUON_MASS, MUON_MASS,
                                      ELECTRON_MASS, ELECTRON_MASS], dtype=f32), (n, 1)),
            # the same momentum a second time, as some private productions store it
            "px": (gen_pt * np.cos(gen_phi)).astype(f32), "py": (gen_pt * np.sin(gen_phi)).astype(f32),
            "pz": (gen_pt * np.sinh(gen_eta)).astype(f32),
            "pdgId": np.tile(np.array([35, 32, 32, 13, -13, 11, -11], dtype=i32), (n, 1)),
            "status": np.tile(np.array([22, 2, 2, 1, 1, 1, 1], dtype=i32), (n, 1)),
            "statusFlags": np.full((n, 7), flags, dtype=i32),
            "genPartIdxMother": np.tile(np.array([-1, 0, 0, 1, 1, 2, 2], dtype=i32), (n, 1)),
            "vx": vx.astype(f32), "vy": vy.astype(f32), "vz": np.zeros((n, 7), dtype=f32),
        })
        truth["mean_lxy"] = float(flight.mean())

    with uproot.recreate(path) as handle:
        write_tree(handle, tree_name, tree)
    return truth


def write_golden_json(path, runs=(1,)):
    """A golden JSON that certifies every luminosity section of the given runs."""
    with open(path, "w", encoding="utf8") as handle:
        json.dump({str(run): [[1, 100000]] for run in runs}, handle)
    return path
