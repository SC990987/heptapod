"""
# test_geometry_vector.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Pin the vector-backed geometry helpers to the implementations they replaced.

The Lepton-Jet tools used to carry their own Delta phi / Delta R / Lxy /
opening-angle arithmetic. Every one of those now calls the library function
that already exists: coffea's LorentzVector.delta_r is literally
`return self.deltaR(other)` on scikit-hep `vector`, so `vector`'s deltaR,
deltaphi, deltaangle and rho ARE the functions coffea would run.

This module checks three things:
  1. the library calls agree with the arithmetic they replaced (REFERENCE_*
     below are the pre-change implementations, copied verbatim),
  2. they agree with coffea's awkward-array behaviours on the same inputs,
  3. the physics built on them -- cross-cleaning, isolation matching, the
     anti-kT reference clusterer, the cosmic veto, gen-level Lxy -- is
     unchanged, and the reference clusterer still agrees with fastjet.
"""
from __future__ import annotations

import math
import random

import numpy as np
import pytest

from tools.analysis.analysis_config import delta_phi, delta_r, MUON_MASS
from tools.analysis import leptonjets as lj


# ===================================================================== #
# ============ Pre-change implementations, copied verbatim =========== #
# ===================================================================== #

def REFERENCE_delta_phi(phi1, phi2):
    d = phi1 - phi2
    while d > math.pi:
        d -= 2.0 * math.pi
    while d <= -math.pi:
        d += 2.0 * math.pi
    return d


def REFERENCE_delta_r(eta1, phi1, eta2, phi2):
    deta = eta1 - eta2
    dphi = REFERENCE_delta_phi(phi1, phi2)
    return math.sqrt(deta * deta + dphi * dphi)


def REFERENCE_cosmic_veto(muons, angle):
    if len(muons) < 2:
        return True
    dirs = []
    for m in muons:
        p = lj.make_p4(m["pt"], m["eta"], m["phi"], 0.1056)
        mag = math.sqrt(p["px"] ** 2 + p["py"] ** 2 + p["pz"] ** 2)
        if mag > 0:
            dirs.append((p["px"] / mag, p["py"] / mag, p["pz"] / mag))
    for i in range(len(dirs)):
        for j in range(i + 1, len(dirs)):
            cos = max(-1.0, min(1.0, sum(dirs[i][k] * dirs[j][k] for k in range(3))))
            if math.acos(cos) > angle:
                return False
    return True


def REFERENCE_antikt(constituents, R):
    """The pre-change reference clusterer, with its own dy/dphi metric."""
    active = [({"px": c["px"], "py": c["py"], "pz": c["pz"], "E": c["E"]}, {i})
              for i, c in enumerate(constituents)]
    R2 = R * R
    jets = []
    INF = float("inf")
    while active:
        n = len(active)
        inv_pt2, raps, phis = [], [], []
        for p, _ in active:
            pt2 = p["px"] ** 2 + p["py"] ** 2
            inv_pt2.append(1.0 / pt2 if pt2 > 0 else INF)
            raps.append(lj.p4_rapidity(p))
            phis.append(math.atan2(p["py"], p["px"]))
        best_val = INF
        best = None
        for i in range(n):
            if inv_pt2[i] < best_val:
                best_val = inv_pt2[i]
                best = ("B", i)
        for i in range(n):
            for j in range(i + 1, n):
                dy = raps[i] - raps[j]
                dphi = REFERENCE_delta_phi(phis[i], phis[j])
                dij = min(inv_pt2[i], inv_pt2[j]) * (dy * dy + dphi * dphi) / R2
                if dij < best_val:
                    best_val = dij
                    best = ("pair", i, j)
        if best[0] == "B":
            p, idx = active.pop(best[1])
            jets.append({**p, "constituents": sorted(idx)})
        else:
            _, i, j = best
            pi, idxi = active[i]
            pj, idxj = active[j]
            merged = lj.p4_add(pi, pj)
            active.pop(j)
            active.pop(i)
            active.append((merged, idxi | idxj))
    return jets


# ===================================================================== #
# ============================== Fixtures ============================ #
# ===================================================================== #

def _rand_dirs(n, seed):
    rng = random.Random(seed)
    return [(rng.uniform(-4.0, 4.0), rng.uniform(-math.pi, math.pi)) for _ in range(n)]


def _rand_consts(n, seed):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        p = lj.make_p4(rng.uniform(0.5, 60.0), rng.uniform(-3.0, 3.0),
                       rng.uniform(-math.pi, math.pi), 0.105658)
        out.append(p)
    return out


# ===================================================================== #
# ================= 1. agreement with the old arithmetic ============= #
# ===================================================================== #

def test_delta_r_matches_reference():
    for eta1, phi1 in _rand_dirs(300, 1):
        for eta2, phi2 in _rand_dirs(3, 2):
            assert delta_r(eta1, phi1, eta2, phi2) == pytest.approx(
                REFERENCE_delta_r(eta1, phi1, eta2, phi2), abs=1e-12)


def test_delta_phi_matches_reference_away_from_the_wrap():
    for phi1, phi2 in [(0.5, -2.9), (3.0, -3.0), (-3.0, 3.0), (1.0, 1.0),
                       (-math.pi + 1e-9, math.pi - 1e-9)]:
        assert delta_phi(phi1, phi2) == pytest.approx(
            REFERENCE_delta_phi(phi1, phi2), abs=1e-12)


def test_delta_phi_wrap_boundary_differs_only_in_sign():
    """vector wraps to [-pi, pi), the old code to (-pi, pi]. At exactly
    Delta phi = pi the two disagree in sign and agree in magnitude. Every
    call site uses |Delta phi| or Delta phi**2, so this is unobservable --
    this test documents it rather than forbidding it."""
    new, old = delta_phi(math.pi, 0.0), REFERENCE_delta_phi(math.pi, 0.0)
    assert abs(new) == pytest.approx(abs(old), abs=1e-12)
    assert abs(new) == pytest.approx(math.pi, abs=1e-12)







# ===================================================================== #
# ================== 2. agreement with coffea itself ================= #
# ===================================================================== #

def test_matches_coffea_awkward_behaviour():
    """The same numbers coffea would produce from its NanoEvents behaviours."""
    awkward = pytest.importorskip("awkward")
    from coffea.nanoevents.methods import vector as cvector

    dirs = _rand_dirs(200, 6)
    a = awkward.zip(
        {"pt": np.ones(len(dirs)), "eta": np.array([e for e, _ in dirs]),
         "phi": np.array([p for _, p in dirs]), "mass": np.zeros(len(dirs))},
        with_name="PtEtaPhiMLorentzVector", behavior=cvector.behavior)
    b = awkward.zip(
        {"pt": np.ones(len(dirs)), "eta": np.zeros(len(dirs)),
         "phi": np.full(len(dirs), 0.3), "mass": np.zeros(len(dirs))},
        with_name="PtEtaPhiMLorentzVector", behavior=cvector.behavior)

    coffea_dr = np.asarray(a.delta_r(b))
    ours_dr = np.array([delta_r(e, p, 0.0, 0.3) for e, p in dirs])
    assert ours_dr == pytest.approx(coffea_dr, abs=1e-12)

    coffea_dphi = np.abs(np.asarray(a.delta_phi(b)))
    ours_dphi = np.array([abs(delta_phi(p, 0.3)) for _, p in dirs])
    assert ours_dphi == pytest.approx(coffea_dphi, abs=1e-12)


# ===================================================================== #
# ============ 3. the physics built on them is unchanged ============= #
# ===================================================================== #

def test_crossclean_unchanged():
    rng = random.Random(7)
    tgt = [{"eta": e, "phi": p, "id": i} for i, (e, p) in enumerate(_rand_dirs(30, 8))]
    ref = [{"eta": e, "phi": p} for e, p in _rand_dirs(6, 9)]
    # a few targets deliberately placed on top of references
    for k in range(3):
        tgt.append({"eta": ref[k]["eta"] + 1e-3, "phi": ref[k]["phi"] - 1e-3, "id": 100 + k})
    for max_dr in (0.05, 0.1, 0.4, 1.0):
        sel_new = {"t": list(tgt), "r": list(ref)}
        lj.apply_crossclean(sel_new, [{"name": "cc", "target": "t", "reference": "r", "max_dr": max_dr}])
        want = [o for o in tgt
                if not any(REFERENCE_delta_r(o["eta"], o["phi"], r["eta"], r["phi"]) < max_dr
                           for r in ref)]
        assert [o["id"] for o in sel_new["t"]] == [o["id"] for o in want], f"max_dr={max_dr}"
        _ = rng  # keep the seed object alive for readability


def test_isolation_picks_the_same_jet():
    iso = {"matched_jet_dr": 0.4, "lepton_fraction_branches": ["chEmEF"]}
    pfjets = [{"eta": e, "phi": p, "E": 50.0 + 3 * i, "chEmEF": 0.1}
              for i, (e, p) in enumerate(_rand_dirs(25, 10))]
    for e, p in _rand_dirs(40, 11):
        p4 = lj.make_p4(20.0, e, p, 0.105658)
        # reference: nearest jet within matched_jet_dr, or none
        best, best_dr = None, iso["matched_jet_dr"]
        for j in pfjets:
            dr = REFERENCE_delta_r(e, p, j["eta"], j["phi"])
            if dr < best_dr:
                best_dr, best = dr, j
        want = 0.0 if (best is None or p4["E"] <= 0) else \
            (best["E"] / p4["E"]) * (1.0 - best["chEmEF"])
        assert lj._isolation(p4, pfjets, iso) == pytest.approx(want, abs=1e-12)


def test_isolation_with_no_jets():
    p4 = lj.make_p4(20.0, 0.5, 0.5, 0.105658)
    assert lj._isolation(p4, [], {"matched_jet_dr": 0.4}) == 0.0


@pytest.mark.parametrize("n,seed", [(2, 21), (5, 22), (9, 23), (14, 24)])
def test_antikt_reference_clusterer_unchanged(n, seed):
    consts = _rand_consts(n, seed)
    got = lj.antikt_cluster(consts, 0.4)
    want = REFERENCE_antikt(consts, 0.4)
    assert len(got) == len(want)
    key = lambda j: (tuple(j["constituents"]),)
    for g, w in zip(sorted(got, key=key), sorted(want, key=key)):
        assert g["constituents"] == w["constituents"]
        for c in ("px", "py", "pz", "E"):
            assert g[c] == pytest.approx(w[c], rel=1e-12, abs=1e-12)


def test_antikt_reference_clusterer_edge_cases():
    assert lj.antikt_cluster([], 0.4) == []
    one = _rand_consts(1, 30)
    assert len(lj.antikt_cluster(one, 0.4)) == 1


def test_antikt_agrees_with_fastjet_backend():
    """The metric now comes from vector; it must still reproduce FastJet."""
    pytest.importorskip("fastjet")
    consts = _rand_consts(12, 40)
    ours = sorted(lj.antikt_cluster(consts, 0.4), key=lambda j: tuple(j["constituents"]))
    fj = sorted(lj.cluster_fastjet_batch([consts], 0.4)[0], key=lambda j: tuple(j["constituents"]))
    assert [j["constituents"] for j in ours] == [j["constituents"] for j in fj]
    for a, b in zip(ours, fj):
        for c in ("px", "py", "pz", "E"):
            assert a[c] == pytest.approx(b[c], rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("angle", [2.0, 2.9, 3.1])
def test_cosmic_veto_unchanged(angle):
    for seed in (50, 51, 52, 53):
        dirs = _rand_dirs(6, seed)
        muons = [{"pt": 10.0 + i, "eta": e, "phi": p} for i, (e, p) in enumerate(dirs)]
        assert lj._cosmic_veto(muons, angle) == REFERENCE_cosmic_veto(muons, angle)
    # a genuinely back-to-back pair must be vetoed
    bb = [{"pt": 20.0, "eta": 0.4, "phi": 0.2}, {"pt": 20.0, "eta": -0.4, "phi": 0.2 - math.pi}]
    assert lj._cosmic_veto(bb, 2.9) is False
    assert REFERENCE_cosmic_veto(bb, 2.9) is False


def test_cosmic_veto_degenerate_inputs():
    assert lj._cosmic_veto([], 2.9) is True
    assert lj._cosmic_veto([{"pt": 10.0, "eta": 0.0, "phi": 0.0}], 2.9) is True
    # zero-momentum muons carry no direction and must not veto
    assert lj._cosmic_veto([{"pt": 0.0, "eta": 0.0, "phi": 0.0},
                            {"pt": 0.0, "eta": 0.0, "phi": 3.0}], 2.9) is True


def test_gen_lxy_matches_hypot():
    """Lxy is the transverse magnitude of (decay vertex - production vertex)."""
    from tools.analysis.gen_kinematics import analyze_gen_event
    cfg = {"gen": {"resonance_pdgid": 999999, "lepton_pdgids": [13],
                   "mother_field": "genPartIdxMother", "vertex": {"vx": "vx", "vy": "vy"}}}
    gen = [
        {"pdgId": 999999, "pt": 40.0, "eta": 0.3, "phi": 0.9, "mass": 5.0, "status": 62,
         "mother_idx": -1, "vx": 0.10, "vy": -0.20},
        {"pdgId": 13, "pt": 22.0, "eta": 0.35, "phi": 0.95, "mass": 0.105, "status": 1,
         "mother_idx": 0, "vx": 3.10, "vy": 3.80},
        {"pdgId": -13, "pt": 18.0, "eta": 0.25, "phi": 0.85, "mass": 0.105, "status": 1,
         "mother_idx": 0, "vx": 3.10, "vy": 3.80},
    ]
    res = analyze_gen_event(gen, cfg)["resonances"]
    assert len(res) == 1
    # production (0.10, -0.20) -> decay (3.10, 3.80) is a 3-4-5 triangle
    assert res[0]["lxy"] == pytest.approx(math.hypot(3.10 - 0.10, 3.80 - (-0.20)), abs=1e-12)
    assert res[0]["lxy"] == pytest.approx(5.0, abs=1e-12)
    assert res[0]["dR"] == pytest.approx(REFERENCE_delta_r(0.35, 0.95, 0.25, 0.85), abs=1e-12)


# ===================================================================== #
# ===================== 4. Lxy is a pinned convention ================= #
# ===================================================================== #

def test_decay_length_matches_the_reference_implementation():
    """The columnar Lxy must agree with the per-event reference in
    gen_kinematics.analyze_gen_event -- they are the same definition."""
    pytest.importorskip("coffea")
    from tools.analysis.gen_decay_length import decay_lengths

    # a resonance at (0.1, -0.2) decaying to two leptons at (3.1, 3.8):
    # a 3-4-5 triangle, so Lxy = 5 exactly
    import awkward as ak
    from coffea.nanoevents.methods import vector as cvector

    gen = ak.zip({
        "pdgId": ak.Array([[32, 13, -13]]),
        "genPartIdxMother": ak.Array([[-1, 0, 0]]),
        "vx": ak.Array([[0.10, 3.10, 3.10]]),
        "vy": ak.Array([[-0.20, 3.80, 3.80]]),
    })

    class FakeEvents:
        GenPart = gen

    out = decay_lengths(FakeEvents(), 32, [11, 13], "genPartIdxMother", "vx", "vy")
    assert out.size == 1, "one entry per resonance"
    assert out[0] == pytest.approx(5.0, abs=1e-12)


def test_decay_length_one_per_daughter_option():
    pytest.importorskip("coffea")
    from tools.analysis.gen_decay_length import decay_lengths
    import awkward as ak
    gen = ak.zip({
        "pdgId": ak.Array([[32, 13, -13]]),
        "genPartIdxMother": ak.Array([[-1, 0, 0]]),
        "vx": ak.Array([[0.10, 3.10, 3.10]]),
        "vy": ak.Array([[-0.20, 3.80, 3.80]]),
    })

    class FakeEvents:
        GenPart = gen

    per_daughter = decay_lengths(FakeEvents(), 32, [11, 13], "genPartIdxMother",
                                 "vx", "vy", one_per_resonance=False)
    assert per_daughter.size == 2, "two daughters -> two entries"
    assert per_daughter == pytest.approx([5.0, 5.0], abs=1e-12)


def test_decay_length_is_not_the_production_vertex():
    """The trap this tool exists to prevent: hypot(resonance.vx, resonance.vy)
    measures the production point, not the decay length."""
    pytest.importorskip("coffea")
    from tools.analysis.gen_decay_length import decay_lengths
    import awkward as ak
    import math
    gen = ak.zip({
        "pdgId": ak.Array([[32, 13, -13]]),
        "genPartIdxMother": ak.Array([[-1, 0, 0]]),
        "vx": ak.Array([[0.10, 3.10, 3.10]]),
        "vy": ak.Array([[-0.20, 3.80, 3.80]]),
    })

    class FakeEvents:
        GenPart = gen

    lxy = decay_lengths(FakeEvents(), 32, [11, 13], "genPartIdxMother", "vx", "vy")[0]
    wrong = math.hypot(0.10, -0.20)          # the production vertex from the origin
    assert lxy == pytest.approx(5.0, abs=1e-12)
    assert wrong < 0.25
    assert lxy / wrong > 20, "the two definitions must not be confusable"
