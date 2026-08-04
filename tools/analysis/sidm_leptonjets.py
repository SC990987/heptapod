"""
# sidm_leptonjets.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Lepton-Jet reconstruction for the SIDM "two Lepton Jets" analysis
(CMS AN-23-107, Sec. 4). This is the core of the pipeline.

Chain (all faithful to the note):
  1. object selection of the four LJ-constituent collections (Sec. 4.1, Table 12)
       - GED electrons, PF photons, PF muons, DSA muons
       - e-gamma cross-clean (PixelSeedVeto + dR(e,gamma) < 0.025)
       - PF-DSA muon cross-clean (Sec. 4.1.4 / Table 9; dR fallback)
  2. anti-kT clustering with cone R = 0.4 (Sec. 4.2)
  3. categorization into eg-type / mu-type and N_mu^LJ >= 2 (Table 10)
  4. LJ isolation IsoLJ = (E_MJ/E_LJ)(1 - f_lepton) (Sec. 4.3)
  5. displacement cuts: e lost-hits >= 1, PF-mu pixel-hits <= 2 (Sec. 4.4, Table 11)

DESIGN: every physics step below is a pure function operating on plain Python
dicts/lists (canonical per-object records). They need neither coffea nor the
`orchestral` framework, so they are unit-testable in any environment. coffea is
used only by `SIDMLeptonJetTool` to read the ROOT file and hand these functions
per-event records. The built-in `antikt_cluster` is the reference clustering;
it can be validated against the `fastjet` package (see tests).

The tool writes one JSONL line per event (schema `evtjsonl-1.0`) carrying the
reconstructed `leptonjets` collection plus an `event` block with the trigger /
primary-vertex / cosmic-veto flags, so the downstream selection+cutflow tool
consumes a single file.
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional, Any, Tuple

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis.sidm_config import (
    SIDMConfig, delta_phi, delta_r, photon_nh_iso_cut,
    ELECTRON_MASS, MUON_MASS, PHOTON_MASS,
)

# constituent-kind codes
KIND_E, KIND_GAMMA, KIND_PFMU, KIND_DSAMU = 0, 1, 2, 3
_KIND_NAME = {0: "electron", 1: "photon", 2: "pf_muon", 3: "dsa_muon"}


# ===================================================================== #
# ========================= 4-vector helpers ========================= #
# ===================================================================== #

def make_p4(pt: float, eta: float, phi: float, mass: float) -> Dict[str, float]:
    """Build a Cartesian 4-vector dict from (pt, eta, phi, mass)."""
    px = pt * math.cos(phi)
    py = pt * math.sin(phi)
    pz = pt * math.sinh(eta)
    E = math.sqrt(px * px + py * py + pz * pz + mass * mass)
    return {"px": px, "py": py, "pz": pz, "E": E}


def p4_pt(p: Dict[str, float]) -> float:
    return math.sqrt(p["px"] ** 2 + p["py"] ** 2)


def p4_eta(p: Dict[str, float]) -> float:
    pt = p4_pt(p)
    if pt == 0.0:
        return math.copysign(float("inf"), p["pz"]) if p["pz"] else 0.0
    return math.asinh(p["pz"] / pt)


def p4_phi(p: Dict[str, float]) -> float:
    return math.atan2(p["py"], p["px"])


def p4_rapidity(p: Dict[str, float]) -> float:
    """Rapidity y = 0.5 ln((E+pz)/(E-pz)); used by anti-kT to match fastjet."""
    num, den = p["E"] + p["pz"], p["E"] - p["pz"]
    if den <= 0:
        den = 1e-12
    if num <= 0:
        num = 1e-12
    return 0.5 * math.log(num / den)


def p4_mass(p: Dict[str, float]) -> float:
    m2 = p["E"] ** 2 - p["px"] ** 2 - p["py"] ** 2 - p["pz"] ** 2
    if -1e-6 < m2 < 0:
        m2 = 0.0
    return math.sqrt(m2) if m2 > 0 else 0.0


def p4_add(a: Dict[str, float], b: Dict[str, float]) -> Dict[str, float]:
    return {"px": a["px"] + b["px"], "py": a["py"] + b["py"],
            "pz": a["pz"] + b["pz"], "E": a["E"] + b["E"]}


# ===================================================================== #
# ======================= Object selection =========================== #
# ===================================================================== #

def select_electrons(electrons: List[dict], cfg: SIDMConfig) -> List[dict]:
    """GED electrons: pt>10, |eta|<2.4, MVA v2 NoIso WPL (Sec. 4.1.1)."""
    o = cfg.objects
    out = []
    for e in electrons:
        if e["pt"] < o.electron_pt_min:
            continue
        if abs(e["eta"]) > o.electron_abseta_max:
            continue
        if not e.get("id_pass", False):
            continue
        out.append(e)
    return out


def photon_passes_id(g: dict, cfg: SIDMConfig) -> Tuple[bool, Optional[str]]:
    """Modified cut-based loose photon ID (Sec. 4.1.2, Table 7): the standard
    loose cut-based decisions EXCEPT sigma_ietaieta and photon isolation, which
    are dropped. Returns (passes, warning).

    Three modes (cfg.objects.photon_id_mode):
      relaxed_bitmap  decode Photon_vidNestedWPBitmap and require the loose
                      per-cut decisions for the requested cuts only (drops
                      sieie + photon iso) -- the faithful default.
      table7          explicit H/E + rho-corrected iso cuts (needs abs-iso).
      cutbased_loose  Photon_cutBased >= 1.
    Each mode falls back to cutBased>=1 (with a warning) if its inputs are
    absent.
    """
    o = cfg.objects
    mode = o.photon_id_mode

    if mode == "relaxed_bitmap":
        vid = g.get("vid_bitmap")
        if vid is not None:
            bpc = o.photon_vid_bits_per_cut
            mask = (1 << bpc) - 1
            ok = all(((int(vid) >> (bpc * idx)) & mask) >= o.photon_vid_min_level
                     for idx in o.photon_vid_required_cuts)
            return ok, None
        cb = g.get("cutbased")
        if cb is None:
            return False, "photon vidNestedWPBitmap and cutBased both missing"
        return (cb >= 1), "photon vidNestedWPBitmap missing; fell back to cutBased>=1"

    if mode == "table7":
        have_table7 = all(g.get(k) is not None for k in ("hoe", "ch_iso", "nh_iso"))
        if have_table7:
            is_eb = g.get("is_eb")
            if is_eb is None:
                sceta = g.get("sc_eta", g["eta"])
                is_eb = abs(sceta) <= o.photon_sceta_barrel_max
            hoe_max = o.photon_hoe_max_eb if is_eb else o.photon_hoe_max_ee
            ch_max = o.photon_chIso_max_eb if is_eb else o.photon_chIso_max_ee
            nh_par = o.photon_nhIso_eb if is_eb else o.photon_nhIso_ee
            ok = (g["hoe"] < hoe_max and g["ch_iso"] < ch_max
                  and g["nh_iso"] < photon_nh_iso_cut(g["pt"], nh_par))
            return ok, None
        cb = g.get("cutbased")
        if cb is None:
            return False, "photon Table-7 inputs and cutBased both missing"
        return (cb >= 1), "photon Table-7 ID inputs missing; fell back to cutBased>=1"

    # cutbased_loose
    cb = g.get("cutbased")
    if cb is None:
        return False, "no photon ID branch available"
    return (cb >= 1), None


def select_photons(photons: List[dict], selected_electrons: List[dict],
                   cfg: SIDMConfig) -> Tuple[List[dict], List[str]]:
    """PF photons: pt>20, |eta|<2.4, modified-loose ID, then e-gamma
    cross-clean: reject if PixelSeed set or within dR<0.025 of a selected e."""
    o = cfg.objects
    warns: List[str] = []
    out = []
    for g in photons:
        if g["pt"] < o.photon_pt_min:
            continue
        if abs(g["eta"]) > o.photon_abseta_max:
            continue
        ok, w = photon_passes_id(g, cfg)
        if w and w not in warns:
            warns.append(w)
        if not ok:
            continue
        # PixelSeedVeto
        if o.photon_apply_pixel_seed_veto and g.get("pixel_seed"):
            continue
        # dR(e, gamma) < 0.025 overlap removal
        overlap = False
        for e in selected_electrons:
            if delta_r(g["eta"], g["phi"], e["eta"], e["phi"]) < o.photon_electron_dr_veto:
                overlap = True
                break
        if overlap:
            continue
        out.append(g)
    return out, warns


def select_pfmuons(pfmuons: List[dict], cfg: SIDMConfig) -> List[dict]:
    """PF muons: pt>5, |eta|<2.4, loose ID (Sec. 4.1.3)."""
    o = cfg.objects
    out = []
    for m in pfmuons:
        if m["pt"] < o.pfmuon_pt_min:
            continue
        if abs(m["eta"]) > o.pfmuon_abseta_max:
            continue
        if not m.get("loose_id", False):
            continue
        out.append(m)
    return out


def _dsa_passes_quality(m: dict, cfg: SIDMConfig) -> bool:
    """DSA displacedID>0 if present, else the explicit Table-8 requirements."""
    o = cfg.objects
    did = m.get("displaced_id")
    if did is not None:
        return did > o.dsamuon_displacedid_min
    # Table-8 fallback (needs the individual hit/chi2/pterr branches)
    n_hits = m.get("n_muon_hits")
    csc = m.get("n_csc_hits")
    dt = m.get("n_dt_hits")
    chi2 = m.get("norm_chi2")
    pterr = m.get("pt_rel_err")
    if n_hits is None or chi2 is None:
        return False
    if n_hits <= o.dsamuon_min_muon_hits:
        return False
    if csc is not None and dt is not None:
        if csc == 0 and dt <= o.dsamuon_dt_only_hits:
            return False
    if chi2 >= o.dsamuon_norm_chi2_max:
        return False
    if pterr is not None and pterr >= o.dsamuon_pt_rel_err_max:
        return False
    return True


def crossclean_dsamuons(dsamuons: List[dict], selected_pfmuons: List[dict],
                        cfg: SIDMConfig) -> Tuple[List[dict], List[str]]:
    """Select DSA muons (pt>10,|eta|<2.4, displacedID>0) and remove those that
    overlap a selected PF muon (Sec. 4.1.4).

    Two methods:
      "segment" (Table 9): full shared-segment + dR(outer) logic. Needs the
                 per-DSA segment-matching branches.
      "dr"      (default, robust): remove a DSA if a selected PF muon lies
                 within dR < fallback_dr (using outer-track coords if present).
    """
    o = cfg.objects
    cc = cfg.crossclean
    warns: List[str] = []
    # 1) quality selection
    cand = []
    for m in dsamuons:
        if m["pt"] < o.dsamuon_pt_min:
            continue
        if abs(m["eta"]) > o.dsamuon_abseta_max:
            continue
        if not _dsa_passes_quality(m, cfg):
            continue
        cand.append(m)

    # 2) cross-clean against selected PF muons
    kept = []
    for m in cand:
        remove = False
        if cc.method == "segment" and m.get("n_segments"):
            nseg = m.get("n_segments") or 0
            nmatch = m.get("n_matched_segments") or 0
            frac = (nmatch / nseg) if nseg else 0.0
            oeta = m.get("outer_eta", m["eta"])
            ophi = m.get("outer_phi", m["phi"])
            for pfm in selected_pfmuons:
                if nmatch < cc.min_shared_segments:
                    continue
                # Overlap Condition 2: full segment overlap -> always remove
                if frac >= 1.0:
                    remove = True
                    break
                # Overlap Condition 1: partial overlap AND close outer tracks
                dro = delta_r(oeta, ophi, pfm.get("outer_eta", pfm["eta"]),
                              pfm.get("outer_phi", pfm["phi"]))
                if cc.shared_frac_min <= frac < 1.0 and dro <= cc.delta_r_outer_max:
                    remove = True
                    break
        else:
            if cc.method == "segment":
                w = "PF-DSA segment branches missing; used dR cross-clean"
                if w not in warns:
                    warns.append(w)
            oeta = m.get("outer_eta", m["eta"])
            ophi = m.get("outer_phi", m["phi"])
            for pfm in selected_pfmuons:
                dro = delta_r(oeta, ophi, pfm.get("outer_eta", pfm["eta"]),
                              pfm.get("outer_phi", pfm["phi"]))
                if dro < cc.fallback_dr:
                    remove = True
                    break
        if not remove:
            kept.append(m)
    return kept, warns


# ===================================================================== #
# ==================== Constituent construction ====================== #
# ===================================================================== #

def build_constituents(sel_e, sel_g, sel_pfmu, sel_dsamu) -> List[dict]:
    """Merge the four selected collections into a flat constituent list, each
    carrying its 4-vector plus the fields needed downstream (kind, charge,
    displacement variables)."""
    consts: List[dict] = []
    for e in sel_e:
        c = make_p4(e["pt"], e["eta"], e["phi"], ELECTRON_MASS)
        c.update({"kind": KIND_E, "charge": e.get("charge"),
                  "lost_hits": e.get("lost_hits")})
        consts.append(c)
    for g in sel_g:
        c = make_p4(g["pt"], g["eta"], g["phi"], PHOTON_MASS)
        c.update({"kind": KIND_GAMMA, "charge": 0})
        consts.append(c)
    for m in sel_pfmu:
        c = make_p4(m["pt"], m["eta"], m["phi"], MUON_MASS)
        c.update({"kind": KIND_PFMU, "charge": m.get("charge"),
                  "pixel_hits": m.get("pixel_hits")})
        consts.append(c)
    for m in sel_dsamu:
        c = make_p4(m["pt"], m["eta"], m["phi"], MUON_MASS)
        c.update({"kind": KIND_DSAMU, "charge": m.get("charge")})
        consts.append(c)
    return consts


# ===================================================================== #
# ===================== anti-kT clustering =========================== #
# ===================================================================== #

def antikt_cluster(constituents: List[dict], R: float) -> List[dict]:
    """Reference anti-kT implementation (E-scheme recombination, rapidity-phi
    metric) matching FastJet for small multiplicities.

    Returns a list of jets, each a dict {px,py,pz,E, constituents:[idx,...]}
    where idx points into `constituents`.
    """
    # active pseudojets: (p4dict, frozenset-of-original-indices)
    active: List[Tuple[dict, set]] = [
        ({"px": c["px"], "py": c["py"], "pz": c["pz"], "E": c["E"]}, {i})
        for i, c in enumerate(constituents)
    ]
    R2 = R * R
    jets: List[dict] = []
    INF = float("inf")

    while active:
        n = len(active)
        # precompute pt^-2, rapidity, phi
        inv_pt2 = []
        raps = []
        phis = []
        for p, _ in active:
            pt2 = p["px"] ** 2 + p["py"] ** 2
            inv_pt2.append(1.0 / pt2 if pt2 > 0 else INF)
            raps.append(p4_rapidity(p))
            phis.append(math.atan2(p["py"], p["px"]))

        # find global minimum among d_iB and d_ij
        best_val = INF
        best = None  # ("B", i) or ("pair", i, j)
        for i in range(n):
            if inv_pt2[i] < best_val:
                best_val = inv_pt2[i]
                best = ("B", i)
        for i in range(n):
            for j in range(i + 1, n):
                dy = raps[i] - raps[j]
                dphi = delta_phi(phis[i], phis[j])
                dR2 = dy * dy + dphi * dphi
                dij = min(inv_pt2[i], inv_pt2[j]) * dR2 / R2
                if dij < best_val:
                    best_val = dij
                    best = ("pair", i, j)

        if best[0] == "B":
            i = best[1]
            p, idx = active.pop(i)
            jets.append({**p, "constituents": sorted(idx)})
        else:
            _, i, j = best
            pi, idxi = active[i]
            pj, idxj = active[j]
            merged = p4_add(pi, pj)
            # remove higher index first
            active.pop(j)
            active.pop(i)
            active.append((merged, idxi | idxj))
    return jets


# ===================================================================== #
# ============= Categorization, isolation, displacement ============== #
# ===================================================================== #

def _matched_jet_iso(lj_p4: dict, pfjets: List[dict], cfg: SIDMConfig) -> float:
    """IsoLJ = (E_MJ/E_LJ)*(1 - f_lepton), matched jet = closest PF jet within
    dR < matched_jet_dr; 0 if none (Sec. 4.3)."""
    lj = cfg.leptonjets
    eta, phi = p4_eta(lj_p4), p4_phi(lj_p4)
    best = None
    best_dr = lj.matched_jet_dr
    for j in pfjets:
        dr = delta_r(eta, phi, j["eta"], j["phi"])
        if dr < best_dr:
            best_dr = dr
            best = j
    if best is None:
        return 0.0
    E_lj = lj_p4["E"]
    if E_lj <= 0:
        return 0.0
    f_lepton = (best.get("ch_em_frac", 0.0) + best.get("ne_em_frac", 0.0)
                + best.get("mu_frac", 0.0))
    return (best["E"] / E_lj) * (1.0 - f_lepton)


def categorize_and_select_lj(jets: List[dict], constituents: List[dict],
                             pfjets: List[dict], cfg: SIDMConfig) -> Tuple[List[dict], List[str]]:
    """Turn clustered jets into LJ records with type, multiplicity, isolation
    and displacement flags, applying the Table 11 / Table 12 requirements."""
    lj = cfg.leptonjets
    warns: List[str] = []
    out: List[dict] = []

    for jet in jets:
        cons = [constituents[i] for i in jet["constituents"]]
        n_e = sum(1 for c in cons if c["kind"] == KIND_E)
        n_g = sum(1 for c in cons if c["kind"] == KIND_GAMMA)
        n_pfmu = sum(1 for c in cons if c["kind"] == KIND_PFMU)
        n_dsamu = sum(1 for c in cons if c["kind"] == KIND_DSAMU)
        n_mu = n_pfmu + n_dsamu

        p4 = {"px": jet["px"], "py": jet["py"], "pz": jet["pz"], "E": jet["E"]}
        pt, eta = p4_pt(p4), p4_eta(p4)
        lj_type = "mu" if n_mu > 0 else "eg"

        # kinematics
        passes_kin = (pt > lj.lj_pt_min) and (abs(eta) < lj.lj_abseta_max)

        # mu-type multiplicity N_mu^LJ >= 2
        passes_mult = True
        if lj_type == "mu":
            passes_mult = n_mu >= lj.mu_type_nmu_min

        # isolation
        iso = _matched_jet_iso(p4, pfjets, cfg)
        passes_iso = iso < lj.iso_max

        # displacement (Table 11). Applied only to LJs with e's or PF mu's.
        passes_disp = True
        if lj_type == "eg" and n_e > 0:
            lost = [c.get("lost_hits") for c in cons if c["kind"] == KIND_E]
            if any(v is None for v in lost):
                w = "electron lostHits branch missing; displacement cut skipped"
                if w not in warns:
                    warns.append(w)
            else:
                passes_disp = min(lost) >= lj.electron_lost_hits_min
        elif lj_type == "mu" and n_pfmu > 0:
            pix = [c.get("pixel_hits") for c in cons if c["kind"] == KIND_PFMU]
            if any(v is None for v in pix):
                w = "PF-muon pixelHits branch missing; displacement cut skipped"
                if w not in warns:
                    warns.append(w)
            else:
                passes_disp = max(pix) <= lj.pfmuon_pixel_hits_max

        selected = passes_kin and passes_mult and passes_iso and passes_disp

        out.append({
            "pt": pt, "eta": eta, "phi": p4_phi(p4), "mass": p4_mass(p4),
            "px": p4["px"], "py": p4["py"], "pz": p4["pz"], "E": p4["E"],
            "type": lj_type,
            "n_e": n_e, "n_gamma": n_g, "n_pf_mu": n_pfmu, "n_dsa_mu": n_dsamu,
            "n_mu": n_mu, "n_constituents": len(cons),
            "iso": iso,
            "pass_kin": passes_kin, "pass_mult": passes_mult,
            "pass_iso": passes_iso, "pass_displacement": passes_disp,
            "selected": selected,
        })
    # sort by pt descending (leading first) -- needed for channel assignment
    out.sort(key=lambda d: d["pt"], reverse=True)
    return out, warns


# ===================================================================== #
# ===================== Per-event driver ============================= #
# ===================================================================== #

def select_and_build_constituents(event: dict, cfg: SIDMConfig):
    """Object selection + cross-cleaning + constituent construction for one
    event. Returns (constituents, selection_counts, warnings)."""
    warnings: List[str] = []
    sel_e = select_electrons(event.get("electrons", []), cfg)
    sel_g, w1 = select_photons(event.get("photons", []), sel_e, cfg)
    sel_pfmu = select_pfmuons(event.get("pfmuons", []), cfg)
    sel_dsamu, w2 = crossclean_dsamuons(event.get("dsamuons", []), sel_pfmu, cfg)
    warnings.extend(w1)
    warnings.extend(w2)
    consts = build_constituents(sel_e, sel_g, sel_pfmu, sel_dsamu)
    sel_counts = {
        "n_sel_electrons": len(sel_e), "n_sel_photons": len(sel_g),
        "n_sel_pfmuons": len(sel_pfmu), "n_sel_dsamuons": len(sel_dsamu),
        "n_constituents": len(consts),
    }
    return consts, sel_counts, warnings


def finalize_leptonjets(consts, jets, pfjets, sel_counts, cfg: SIDMConfig) -> dict:
    """Categorize/isolate/displacement-cut pre-clustered jets and assemble the
    per-event LJ record. `jets` is the clustering output (builtin or fastjet)."""
    ljs, w3 = categorize_and_select_lj(jets, consts, pfjets, cfg)
    selected_ljs = [lj for lj in ljs if lj["selected"]]
    counts = dict(sel_counts)
    counts.update({
        "n_leptonjets": len(ljs),
        "n_leptonjets_selected": len(selected_ljs),
        "n_eg_selected": sum(1 for lj in selected_ljs if lj["type"] == "eg"),
        "n_mu_selected": sum(1 for lj in selected_ljs if lj["type"] == "mu"),
    })
    return {"leptonjets": ljs, "counts": counts, "warnings": w3}


def reconstruct_event(event: dict, cfg: SIDMConfig) -> dict:
    """Full LJ reconstruction for one event using the built-in anti-kT (the
    reference path). `event` provides candidate collections as lists of
    canonical dicts: electrons, photons, pfmuons, dsamuons, pfjets."""
    consts, sel_counts, warnings = select_and_build_constituents(event, cfg)
    jets = antikt_cluster(consts, cfg.leptonjets.jet_radius)
    res = finalize_leptonjets(consts, jets, event.get("pfjets", []), sel_counts, cfg)
    res["warnings"] = warnings + res["warnings"]
    return res


def cluster_fastjet_batch(consts_per_event, R: float):
    """Cluster every event's constituents in one columnar pass with the
    fastjet+awkward ClusterSequence (the coffea-native engine). Returns a
    per-event list of jets in the same dict format as `antikt_cluster`."""
    import awkward as ak
    import fastjet
    import vector
    vector.register_awkward()

    recs = [[{"px": c["px"], "py": c["py"], "pz": c["pz"], "E": c["E"]}
             for c in ev] for ev in consts_per_event]
    arr = ak.Array(recs)
    arr = ak.with_name(arr, "Momentum4D")
    jetdef = fastjet.JetDefinition(fastjet.antikt_algorithm, float(R))
    cs = fastjet.ClusterSequence(arr, jetdef)
    jets = ak.to_list(cs.inclusive_jets())
    cidx = ak.to_list(cs.constituent_index())

    out = []
    for ev_i in range(len(consts_per_event)):
        ev_jets = []
        for j_i, jj in enumerate(jets[ev_i]):
            ev_jets.append({
                "px": jj["px"], "py": jj["py"], "pz": jj["pz"], "E": jj["E"],
                "constituents": sorted(int(k) for k in cidx[ev_i][j_i]),
            })
        out.append(ev_jets)
    return out


# ===================================================================== #
# ================= Event-level flag helpers (pure) ================== #
# ===================================================================== #

def trigger_or(trigger_values: List[Optional[bool]]) -> Optional[bool]:
    """Logical OR of the analysis trigger bits (Table 13). Returns None if no
    trigger information is available (so the caller can warn rather than reject
    every event)."""
    known = [bool(v) for v in trigger_values if v is not None]
    if not known:
        return None
    return any(known)


def pv_pass(ndof, z, x, y, flag, cfg: SIDMConfig) -> Optional[bool]:
    """Primary-vertex filter (Table 15). Prefers the precomputed Flag; else
    computes from PV_ndof / PV_z / PV_{x,y}. None if nothing is available."""
    ev = cfg.events
    if flag is not None:
        return bool(flag)
    if ndof is None or z is None:
        return None
    rho = math.hypot(x if x is not None else 0.0, y if y is not None else 0.0)
    return (ndof > ev.pv_ndof_min and abs(z) < ev.pv_absz_max
            and rho < ev.pv_rho_max)


def cosmic_veto_pass(muons: List[dict], cfg: SIDMConfig) -> bool:
    """Cosmic-ray muon veto (Sec. 5.2.2). Veto (return False) if any muon pair
    is back-to-back in 3D (opening angle > threshold). `muons` carry pt/eta/phi.
    """
    ev = cfg.events
    if not ev.cosmic_veto_enabled or len(muons) < 2:
        return True
    dirs = []
    for m in muons:
        p = make_p4(m["pt"], m["eta"], m["phi"], MUON_MASS)
        mag = math.sqrt(p["px"] ** 2 + p["py"] ** 2 + p["pz"] ** 2)
        if mag > 0:
            dirs.append((p["px"] / mag, p["py"] / mag, p["pz"] / mag))
    for i in range(len(dirs)):
        for j in range(i + 1, len(dirs)):
            cos = (dirs[i][0] * dirs[j][0] + dirs[i][1] * dirs[j][1]
                   + dirs[i][2] * dirs[j][2])
            cos = max(-1.0, min(1.0, cos))
            if math.acos(cos) > ev.cosmic_veto_3d_angle:
                return False
    return True


# ===================================================================== #
# ================ coffea extraction (needs awkward) ================= #
# ===================================================================== #

def _strip_prefix(coll_name: str, branch: str) -> str:
    """'Electron_mvaFall17V2noIso_WPL' -> 'mvaFall17V2noIso_WPL'."""
    pre = coll_name + "_"
    return branch[len(pre):] if branch.startswith(pre) else branch


def _extract_collection(events, coll_name: str, field_map: Dict[str, Optional[str]]):
    """Return a per-event list of object dicts for a NanoAOD collection.

    field_map maps canonical keys -> collection member field names (post-prefix)
    or None. Missing collections/fields yield [] / None gracefully.
    """
    import awkward as ak
    n = len(events)
    if coll_name not in events.fields:
        return [[] for _ in range(n)], False
    coll = events[coll_name]
    avail = set(coll.fields)
    data: Dict[str, Any] = {}
    for canon, fname in field_map.items():
        if fname is not None and fname in avail:
            data[canon] = ak.to_list(coll[fname])
        else:
            data[canon] = None
    # per-event object multiplicity = list length of any available field
    ref = next((v for v in data.values() if v is not None), None)
    if ref is None:
        return [[] for _ in range(n)], True
    result = []
    for i in range(n):
        objs = []
        for j in range(len(ref[i])):
            d = {}
            for canon, lst in data.items():
                d[canon] = lst[i][j] if lst is not None else None
            objs.append(d)
        result.append(objs)
    return result, True


def _event_scalar(events, full_branch: str):
    """Read a per-event scalar branch, handling both flat ('HLT_x') and
    NanoAODSchema-grouped ('HLT'.'x') layouts. Returns a python list or None."""
    import awkward as ak
    if full_branch in events.fields:
        try:
            return ak.to_list(events[full_branch])
        except Exception:
            return None
    if "_" in full_branch:
        group, member = full_branch.split("_", 1)
        if group in events.fields:
            sub = events[group]
            if hasattr(sub, "fields") and member in sub.fields:
                try:
                    return ak.to_list(sub[member])
                except Exception:
                    return None
    return None


def extract_events(events, cfg: SIDMConfig):
    """Convert a coffea NanoEvents array into per-event candidate records +
    event-level flag inputs. Returns (records, flag_inputs, warnings)."""
    f = cfg.fields
    o = cfg.objects
    lj = cfg.leptonjets
    warns: List[str] = []

    e_map = {"pt": "pt", "eta": "eta", "phi": "phi", "charge": "charge",
             "id_pass": _strip_prefix(f.electron_coll, o.electron_id_branch),
             "lost_hits": _strip_prefix(f.electron_coll, lj.electron_lost_hits_branch)}
    g_map = {"pt": "pt", "eta": "eta", "phi": "phi",
             "sc_eta": _strip_prefix(f.photon_coll, o.photon_sceta_branch),
             "is_eb": "isScEtaEB",
             "hoe": _strip_prefix(f.photon_coll, o.photon_hoe_branch),
             "ch_iso": _strip_prefix(f.photon_coll, o.photon_chiso_branch),
             "nh_iso": _strip_prefix(f.photon_coll, o.photon_nhiso_branch),
             "pixel_seed": _strip_prefix(f.photon_coll, o.photon_pixelseed_branch),
             "cutbased": _strip_prefix(f.photon_coll, o.photon_cutbased_branch),
             "vid_bitmap": _strip_prefix(f.photon_coll, o.photon_vid_bitmap_branch)}
    pfmu_map = {"pt": "pt", "eta": "eta", "phi": "phi", "charge": "charge",
                "loose_id": _strip_prefix(f.pfmuon_coll, o.pfmuon_id_branch),
                "pixel_hits": _strip_prefix(f.pfmuon_coll, lj.pfmuon_pixel_hits_branch)}
    dsa_map = {"pt": "pt", "eta": "eta", "phi": "phi", "charge": "charge",
               "displaced_id": _strip_prefix(f.dsamuon_coll, o.dsamuon_displacedid_branch),
               "n_matched_segments": "nMatchedSegments", "n_segments": "nSegments",
               "outer_eta": "outerEta", "outer_phi": "outerPhi"}
    jet_map = {"pt": "pt", "eta": "eta", "phi": "phi", "mass": "mass",
               "ch_em_frac": _strip_prefix(f.jet_coll, lj.jet_ch_em_frac_branch),
               "ne_em_frac": _strip_prefix(f.jet_coll, lj.jet_ne_em_frac_branch),
               "mu_frac": _strip_prefix(f.jet_coll, lj.jet_mu_frac_branch)}

    e_rec, e_ok = _extract_collection(events, f.electron_coll, e_map)
    g_rec, g_ok = _extract_collection(events, f.photon_coll, g_map)
    pfmu_rec, pfmu_ok = _extract_collection(events, f.pfmuon_coll, pfmu_map)
    dsa_rec, dsa_ok = _extract_collection(events, f.dsamuon_coll, dsa_map)
    jet_rec, jet_ok = _extract_collection(events, f.jet_coll, jet_map)
    for ok, name in [(e_ok, f.electron_coll), (g_ok, f.photon_coll),
                     (pfmu_ok, f.pfmuon_coll), (dsa_ok, f.dsamuon_coll),
                     (jet_ok, f.jet_coll)]:
        if not ok:
            warns.append(f"collection '{name}' not found; treated as empty")

    # add E to pf jets
    for ev_jets in jet_rec:
        for j in ev_jets:
            p = make_p4(j["pt"], j["eta"], j["phi"], j.get("mass") or 0.0)
            j["E"] = p["E"]

    # event-level flag inputs
    trig_arrays = {p: _event_scalar(events, p) for p in cfg.events.trigger_paths}
    if all(v is None for v in trig_arrays.values()):
        warns.append("no analysis trigger branches found; trigger cut passes all")
    pv_flag = _event_scalar(events, cfg.events.pv_flag_branch)
    pv_ndof = _event_scalar(events, cfg.events.pv_ndof_branch)
    pv_z = _event_scalar(events, cfg.events.pv_z_branch)
    pv_x = _event_scalar(events, cfg.events.pv_x_branch)
    pv_y = _event_scalar(events, cfg.events.pv_y_branch)
    if pv_flag is None and pv_ndof is None:
        warns.append("no primary-vertex branches found; PV cut passes all")

    n = len(events)
    records = []
    for i in range(n):
        records.append({
            "electrons": e_rec[i], "photons": g_rec[i],
            "pfmuons": pfmu_rec[i], "dsamuons": dsa_rec[i], "pfjets": jet_rec[i],
        })
    flag_inputs = {
        "triggers": trig_arrays, "pv_flag": pv_flag, "pv_ndof": pv_ndof,
        "pv_z": pv_z, "pv_x": pv_x, "pv_y": pv_y,
    }
    return records, flag_inputs, list(dict.fromkeys(warns))


# ===================================================================== #
# =========================== The tool =============================== #
# ===================================================================== #

class SIDMLeptonJetTool(BaseTool):
    """
    Reconstruct SIDM Lepton Jets from an LLPNanoAOD ROOT file (CMS AN-23-107,
    Sec. 4) and write them to a JSONL event file for downstream selection.

    Pipeline: coffea reads the file -> object selection of GED electrons, PF
    photons, PF muons, DSA muons (Table 12) with e-gamma and PF-DSA
    cross-cleaning -> anti-kT (R=0.4) clustering -> eg/mu categorization and
    N_mu^LJ>=2 -> LJ isolation (Sec. 4.3) -> displacement cuts (Table 11).

    Run `SIDMInspectFileTool` first, and pass any branch-name corrections via
    `field_overrides` (see `sidm_config.SIDMConfig`).

    Inputs (runtime):
      - root_path: sandbox-relative path to the LLPNanoAOD .root file.
      - output_path: sandbox-relative JSONL to write (default: alongside input).
      - tree_name: TTree to read (default 'Events').
      - max_events: cap events processed (default: all).
      - field_overrides: nested SIDMConfig overrides.

    Output JSONL (schema evtjsonl-1.0), one line per event:
      {"event_id": i, "schema_version": "evtjsonl-1.0",
       "data": {"leptonjets": [...], "event": {"trig_pass":b,"pv_pass":b,
                 "cosmic_veto_pass":b, "counts": {...}}}}
    The tool returns a JSON summary (yields, category breakdown, warnings).
    """
    root_path: str = RuntimeField(description="Sandbox-relative path to the LLPNanoAOD .root file")
    output_path: Optional[str] = RuntimeField(default=None, description="Sandbox-relative output JSONL path (default: <input>_leptonjets.jsonl)")
    tree_name: Optional[str] = RuntimeField(default=None, description="TTree name (default 'Events')")
    max_events: Optional[int] = RuntimeField(default=None, description="Process at most this many events (default: all)")
    field_overrides: Optional[dict] = RuntimeField(default=None, description="Nested SIDMConfig overrides, e.g. {'fields':{'dsamuon_coll':'DisplacedStandAloneMuon'}}")

    base_directory: str = StateField(default=".", description="Base directory for safe paths")

    def _setup(self):
        self.base_directory = os.path.abspath(self.base_directory)
        if not os.path.exists(self.base_directory):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def _safe_path(self, rel: str) -> Optional[str]:
        if not rel:
            return None
        full = os.path.abspath(os.path.join(self.base_directory, rel))
        return full if full.startswith(self.base_directory) else None

    def _read_events(self, src: str, tree_name: str, cfg: SIDMConfig):
        """Read the ROOT file into a coffea NanoEvents array, tolerant of
        coffea API differences across versions."""
        from coffea.nanoevents import NanoEventsFactory
        from tools.analysis.sidm_config import build_llpnano_schema
        schema = build_llpnano_schema()
        last_err = None
        # Try the API variants across coffea versions. The bare call is the
        # default on coffea 2025/2026 and reads branches lazily on access
        # (so it only touches the branches we actually need). `delayed=False`
        # / `mode="eager"` are older/eager variants that materialise every
        # branch up front -- kept as fallbacks. We catch broadly so a variant
        # that raises (unknown kwarg, or an eager read hitting an unrelated
        # compressed branch) falls through to the next.
        for kwargs in ({"schemaclass": schema},
                       {"schemaclass": schema, "delayed": False},
                       {"schemaclass": schema, "mode": "eager"}):
            try:
                events = NanoEventsFactory.from_root({src: tree_name}, **kwargs).events()
                _ = events.fields  # surface obvious construction errors here
                return events
            except Exception as e:  # noqa: BLE001 - deliberate fall-through
                last_err = e
                continue
        # legacy coffea (0.7): open the tree directly
        import uproot
        tree = uproot.open(src)[tree_name]
        return NanoEventsFactory.from_root(tree, schemaclass=schema).events()

    def _run(self) -> str:
        src = self._safe_path(self.root_path)
        if not src:
            return self.format_error(error="Access Denied", reason="root_path escapes base_directory")
        if not os.path.exists(src):
            return self.format_error(error="File Not Found", reason=f"ROOT file not found: {self.root_path}")
        try:
            cfg = SIDMConfig.from_overrides(self.field_overrides)
        except ValueError as e:
            return self.format_error(error="Invalid Config Override", reason=str(e))

        tree_name = self.tree_name or cfg.fields.tree_name
        try:
            events = self._read_events(src, tree_name, cfg)
        except ImportError as e:
            return self.format_error(error="Missing Dependency", reason=str(e),
                                     suggestion="pip install coffea awkward uproot")
        except Exception as e:
            return self.format_error(error="Read Error", reason=str(e))

        if self.max_events is not None:
            events = events[: int(self.max_events)]

        try:
            records, flags, warns = extract_events(events, cfg)
        except Exception as e:
            return self.format_error(error="Extraction Error", reason=str(e))

        out_rel = self.output_path or self._default_output()
        dst = self._safe_path(out_rel)
        if not dst:
            return self.format_error(error="Access Denied", reason="output_path escapes base_directory")
        out_dir = os.path.dirname(dst)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        n_events = len(records)
        all_warns = set(warns)
        totals = {"n_leptonjets": 0, "n_leptonjets_selected": 0,
                  "n_eg_selected": 0, "n_mu_selected": 0}
        n_trig = n_pv = n_cosmic = n_ge2lj = 0
        n_4mu = n_2mu2e = 0

        # Phase 1: object selection + constituent building (per event).
        consts_all, selcounts_all = [], []
        for i in range(n_events):
            c, sc, w = select_and_build_constituents(records[i], cfg)
            consts_all.append(c)
            selcounts_all.append(sc)
            all_warns.update(w)

        # Phase 2: clustering. Default is the fastjet+awkward ClusterSequence
        # (columnar, coffea-native), batched over all events; fall back to the
        # built-in reference anti-kT if fastjet is unavailable.
        R = cfg.leptonjets.jet_radius
        jets_all = None
        if cfg.leptonjets.cluster_backend == "fastjet":
            try:
                jets_all = cluster_fastjet_batch(consts_all, R)
            except Exception as e:
                all_warns.add(f"fastjet unavailable ({type(e).__name__}); used built-in anti-kT")
        if jets_all is None:
            jets_all = [antikt_cluster(c, R) for c in consts_all]

        # Phase 3: finalize LJs + event flags + write JSONL.
        with open(dst, "w") as fh:
            for i in range(n_events):
                reco = finalize_leptonjets(consts_all[i], jets_all[i],
                                           records[i]["pfjets"], selcounts_all[i], cfg)
                all_warns.update(reco["warnings"])
                # event flags
                trig_vals = [flags["triggers"][p][i] if flags["triggers"][p] is not None else None
                             for p in cfg.events.trigger_paths]
                t = trigger_or(trig_vals)
                trig_ok = True if t is None else t
                pv = pv_pass(_idx(flags["pv_ndof"], i), _idx(flags["pv_z"], i),
                             _idx(flags["pv_x"], i), _idx(flags["pv_y"], i),
                             _idx(flags["pv_flag"], i), cfg)
                pv_ok = True if pv is None else pv
                muons = records[i]["pfmuons"] + records[i]["dsamuons"]
                cosmic_ok = cosmic_veto_pass(muons, cfg)

                selected = [lj for lj in reco["leptonjets"] if lj["selected"]]
                channel = classify_channel(selected)

                totals["n_leptonjets"] += reco["counts"]["n_leptonjets"]
                totals["n_leptonjets_selected"] += reco["counts"]["n_leptonjets_selected"]
                totals["n_eg_selected"] += reco["counts"]["n_eg_selected"]
                totals["n_mu_selected"] += reco["counts"]["n_mu_selected"]
                n_trig += int(trig_ok)
                n_pv += int(trig_ok and pv_ok)
                n_cosmic += int(trig_ok and pv_ok and cosmic_ok)
                if trig_ok and pv_ok and cosmic_ok and len(selected) >= 2:
                    n_ge2lj += 1
                    if channel == "4mu":
                        n_4mu += 1
                    elif channel == "2mu2e":
                        n_2mu2e += 1

                line = {
                    "event_id": i, "schema_version": "evtjsonl-1.0",
                    "data": {
                        "leptonjets": reco["leptonjets"],
                        "event": {
                            "trig_pass": bool(trig_ok), "pv_pass": bool(pv_ok),
                            "cosmic_veto_pass": bool(cosmic_ok),
                            "channel": channel, "counts": reco["counts"],
                        },
                    },
                }
                fh.write(json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n")

        summary = {
            "status": "ok",
            "root_path": self.root_path,
            "output_jsonl": out_rel,
            "n_events": n_events,
            "totals": totals,
            "preselection_cutflow": {
                "initial": n_events, "trigger": n_trig, "pv_filter": n_pv,
                "cosmic_veto": n_cosmic, "two_leptonjets": n_ge2lj,
            },
            "channels": {"4mu": n_4mu, "2mu2e": n_2mu2e},
            "warnings": sorted(all_warns),
        }
        return json.dumps(summary, separators=(",", ":"), ensure_ascii=False)

    def _default_output(self) -> str:
        base = os.path.splitext(os.path.basename(self.root_path))[0]
        d = os.path.dirname(self.root_path)
        name = f"{base}_leptonjets.jsonl"
        return os.path.join(d, name) if d else name


def _idx(lst, i):
    return lst[i] if lst is not None else None


def classify_channel(selected_ljs: List[dict]) -> Optional[str]:
    """Channel from the two leading selected LJs (Sec. 5.3): both mu-type ->
    '4mu'; one mu-type + one eg-type -> '2mu2e'; else None."""
    if len(selected_ljs) < 2:
        return None
    lead = sorted(selected_ljs, key=lambda d: d["pt"], reverse=True)[:2]
    types = sorted(lj["type"] for lj in lead)
    if types == ["mu", "mu"]:
        return "4mu"
    if types == ["eg", "mu"]:
        return "2mu2e"
    return None
