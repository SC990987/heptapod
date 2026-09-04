"""
# leptonjets.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Config-driven Lepton-Jet reconstruction.

The reconstruction *method* is fixed and generic: select a set of constituent
object collections, cross-clean overlaps, cluster them with anti-kT, categorize
each jet by its constituent content, then apply isolation and displacement
requirements. Everything that is *policy* -- which collections, their cuts and
branch names, the categorization, the displacement rules -- comes from the
config dict (see analysis_config.py). No analysis-specific values live here.

All physics is in pure functions on plain dicts, so it is testable without
coffea or the framework; coffea is used only for columnar I/O in the tool.
"""
from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional, Any, Tuple

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis.analysis_config import (
    load_config, get, delta_phi, delta_r, build_schema, apply_cutflow, MUON_MASS,
)

_OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


# ===================================================================== #
# ========================= 4-vector helpers ========================= #
# ===================================================================== #

def make_p4(pt, eta, phi, mass):
    px = pt * math.cos(phi); py = pt * math.sin(phi); pz = pt * math.sinh(eta)
    E = math.sqrt(px * px + py * py + pz * pz + mass * mass)
    return {"px": px, "py": py, "pz": pz, "E": E}


def p4_pt(p): return math.sqrt(p["px"] ** 2 + p["py"] ** 2)
def p4_phi(p): return math.atan2(p["py"], p["px"])
def p4_eta(p):
    pt = p4_pt(p)
    return math.asinh(p["pz"] / pt) if pt > 0 else (math.copysign(float("inf"), p["pz"]) if p["pz"] else 0.0)
def p4_rapidity(p):
    num, den = p["E"] + p["pz"], p["E"] - p["pz"]
    if den <= 0: den = 1e-12
    if num <= 0: num = 1e-12
    return 0.5 * math.log(num / den)
def p4_mass(p):
    m2 = p["E"] ** 2 - p["px"] ** 2 - p["py"] ** 2 - p["pz"] ** 2
    if -1e-6 < m2 < 0: m2 = 0.0
    return math.sqrt(m2) if m2 > 0 else 0.0
def p4_add(a, b):
    return {"px": a["px"] + b["px"], "py": a["py"] + b["py"], "pz": a["pz"] + b["pz"], "E": a["E"] + b["E"]}


# ===================================================================== #
# ======================= anti-kT clustering ========================= #
# ===================================================================== #

def antikt_cluster(constituents: List[dict], R: float) -> List[dict]:
    """Reference anti-kT (E-scheme, rapidity-phi metric), matching FastJet for
    small multiplicities. Returns jets {px,py,pz,E, constituents:[idx,...]}."""
    active: List[Tuple[dict, set]] = [
        ({"px": c["px"], "py": c["py"], "pz": c["pz"], "E": c["E"]}, {i})
        for i, c in enumerate(constituents)]
    R2 = R * R; jets: List[dict] = []; INF = float("inf")
    while active:
        n = len(active)
        inv_pt2 = []; raps = []; phis = []
        for p, _ in active:
            pt2 = p["px"] ** 2 + p["py"] ** 2
            inv_pt2.append(1.0 / pt2 if pt2 > 0 else INF)
            raps.append(p4_rapidity(p)); phis.append(math.atan2(p["py"], p["px"]))
        best_val = INF; best = None
        for i in range(n):
            if inv_pt2[i] < best_val:
                best_val = inv_pt2[i]; best = ("B", i)
        for i in range(n):
            for j in range(i + 1, n):
                dy = raps[i] - raps[j]; dphi = delta_phi(phis[i], phis[j])
                dij = min(inv_pt2[i], inv_pt2[j]) * (dy * dy + dphi * dphi) / R2
                if dij < best_val:
                    best_val = dij; best = ("pair", i, j)
        if best[0] == "B":
            p, idx = active.pop(best[1]); jets.append({**p, "constituents": sorted(idx)})
        else:
            _, i, j = best
            pi, idxi = active[i]; pj, idxj = active[j]
            merged = p4_add(pi, pj); active.pop(j); active.pop(i); active.append((merged, idxi | idxj))
    return jets


def cluster_fastjet_batch(consts_per_event, R: float):
    """Cluster all events in one columnar pass with fastjet+awkward (the
    coffea-native engine). Same output format as antikt_cluster."""
    import awkward as ak
    import fastjet
    import vector
    vector.register_awkward()
    recs = [[{"px": c["px"], "py": c["py"], "pz": c["pz"], "E": c["E"]} for c in ev]
            for ev in consts_per_event]
    arr = ak.with_name(ak.Array(recs), "Momentum4D")
    cs = fastjet.ClusterSequence(arr, fastjet.JetDefinition(fastjet.antikt_algorithm, float(R)))
    jets = ak.to_list(cs.inclusive_jets()); cidx = ak.to_list(cs.constituent_index())
    out = []
    for ev_i in range(len(consts_per_event)):
        out.append([{"px": jj["px"], "py": jj["py"], "pz": jj["pz"], "E": jj["E"],
                     "constituents": sorted(int(k) for k in cidx[ev_i][j_i])}
                    for j_i, jj in enumerate(jets[ev_i])])
    return out


# ===================================================================== #
# ===================== Constituent selection ======================== #
# ===================================================================== #

def id_pass(o: dict, idspec: Optional[dict]) -> Tuple[bool, Optional[str]]:
    """Evaluate a constituent ID recipe against an object. Recipes:
      none | bool(field) | min(field,value,strict) | cutbased(field,min) |
      photon_vid_relaxed(field,bits_per_cut,required_cuts,min_level,[fallback]).
    """
    if not idspec or idspec.get("type", "none") == "none":
        return True, None
    t = idspec["type"]
    if t == "bool":
        return bool(o.get(idspec["field"])), None
    if t == "min":
        v = o.get(idspec["field"])
        if v is None:
            return False, None
        return (v > idspec.get("value", 0)) if idspec.get("strict", True) else (v >= idspec.get("value", 0)), None
    if t == "cutbased":
        v = o.get(idspec["field"])
        return (v is not None and v >= idspec.get("min", 1)), None
    if t == "photon_vid_relaxed":
        vid = o.get(idspec["field"])
        if vid is not None:
            bpc = idspec.get("bits_per_cut", 2); mask = (1 << bpc) - 1
            lvl = idspec.get("min_level", 1)
            ok = all(((int(vid) >> (bpc * i)) & mask) >= lvl for i in idspec.get("required_cuts", []))
            return ok, None
        fb = idspec.get("fallback_field")
        if fb and o.get(fb) is not None:
            return (o[fb] >= idspec.get("fallback_min", 1)), "photon VID bitmap missing; used fallback"
        return False, "no photon ID field available"
    return True, None


def select_constituent(objs: List[dict], cspec: dict) -> Tuple[List[dict], List[str]]:
    """Apply pt/|eta|/ID and per-object vetoes for one constituent collection."""
    warns: List[str] = []
    pt_min = cspec.get("pt_min", 0.0); eta_max = cspec.get("abseta_max", 100.0)
    veto = cspec.get("veto") or {}
    psf = veto.get("pixel_seed_field")
    out = []
    for o in objs:
        if o.get("pt", 0.0) < pt_min:
            continue
        if abs(o.get("eta", 0.0)) > eta_max:
            continue
        ok, w = id_pass(o, cspec.get("id"))
        if w and w not in warns:
            warns.append(w)
        if not ok:
            continue
        if psf and o.get(psf):
            continue
        out.append(o)
    return out, warns


def apply_crossclean(selected: Dict[str, List[dict]], rules: List[dict]) -> None:
    """Remove `target` objects that overlap a `reference` object (dR method)."""
    for cc in rules or []:
        tgt, ref = cc.get("target"), cc.get("reference")
        mdr = cc.get("max_dr", 0.1); use_outer = cc.get("use_outer", False)
        oe_f, op_f = cc.get("outer_eta_field", "outerEta"), cc.get("outer_phi_field", "outerPhi")
        refs = selected.get(ref, [])
        kept = []
        for o in selected.get(tgt, []):
            oe = o.get(oe_f, o["eta"]) if use_outer else o["eta"]
            op = o.get(op_f, o["phi"]) if use_outer else o["phi"]
            remove = False
            for r in refs:
                re_ = r.get(oe_f, r["eta"]) if use_outer else r["eta"]
                rp = r.get(op_f, r["phi"]) if use_outer else r["phi"]
                if delta_r(oe, op, re_, rp) < mdr:
                    remove = True
                    break
            if not remove:
                kept.append(o)
        selected[tgt] = kept


def build_constituents(selected: Dict[str, List[dict]], specs: List[dict]) -> List[dict]:
    """Merge selected collections into flat constituents carrying their
    4-vector, source name, is_muon flag, and the raw object (for displacement)."""
    consts = []
    for spec in specs:
        m = spec.get("mass", 0.0); is_mu = bool(spec.get("is_muon"))
        for o in selected.get(spec["name"], []):
            p = make_p4(o["pt"], o["eta"], o["phi"], m)
            p.update(cname=spec["name"], is_muon=is_mu, obj=o)
            consts.append(p)
    return consts


# ===================================================================== #
# ============= Categorization, isolation, displacement ============== #
# ===================================================================== #

def _isolation(p4: dict, pfjets: List[dict], iso: dict) -> float:
    eta, phi = p4_eta(p4), p4_phi(p4)
    best = None; best_dr = iso.get("matched_jet_dr", 0.4)
    for j in pfjets:
        dr = delta_r(eta, phi, j["eta"], j["phi"])
        if dr < best_dr:
            best_dr = dr; best = j
    if best is None or p4["E"] <= 0:
        return 0.0
    flep = sum((best.get(b) or 0.0) for b in iso.get("lepton_fraction_branches", []))
    return (best["E"] / p4["E"]) * (1.0 - flep)


def categorize_and_select(jets, constituents, pfjets, cfg) -> Tuple[List[dict], List[str]]:
    lj = cfg["leptonjet"]; cats = lj.get("categories", [])
    iso = lj.get("isolation", {}); disp = lj.get("displacement", [])
    warns: List[str] = []; out = []
    for jet in jets:
        cs = [constituents[i] for i in jet["constituents"]]
        n_muon = sum(1 for c in cs if c["is_muon"])
        counts: Dict[str, int] = {}
        for c in cs:
            counts[c["cname"]] = counts.get(c["cname"], 0) + 1
        p4 = {k: jet[k] for k in ("px", "py", "pz", "E")}
        pt, eta = p4_pt(p4), p4_eta(p4)

        cat = None; final_min_muon = None
        for cspec in cats:
            if cspec.get("default"):
                cat = cspec["name"]; break
            if n_muon >= cspec.get("min_muon", 1):
                cat = cspec["name"]; final_min_muon = cspec.get("final_min_muon"); break
        if cat is None and cats:
            cat = cats[-1]["name"]

        passes_kin = (pt > lj.get("pt_min", 0.0)) and (abs(eta) < lj.get("abseta_max", 100.0))
        passes_mult = True if final_min_muon is None else (n_muon >= final_min_muon)

        iso_val = _isolation(p4, pfjets, iso) if iso.get("enabled", True) else 0.0
        passes_iso = True if iso.get("max") is None else (iso_val < iso["max"])

        passes_disp = True
        for d in disp:
            if d.get("category") != cat:
                continue
            members = [c for c in cs if c["cname"] == d.get("constituent")]
            if not members:
                if d.get("applies_if_present", True):
                    continue
                passes_disp = False; break
            vals = [m["obj"].get(d["field"]) for m in members]
            if any(v is None for v in vals):
                w = f"displacement field '{d['field']}' missing; cut skipped"
                if w not in warns:
                    warns.append(w)
                continue
            v = min(vals) if d.get("reduce", "min") == "min" else max(vals)
            if not _OPS[d["op"]](v, d["value"]):
                passes_disp = False; break

        out.append({
            "pt": pt, "eta": eta, "phi": p4_phi(p4), "mass": p4_mass(p4),
            "px": p4["px"], "py": p4["py"], "pz": p4["pz"], "E": p4["E"],
            "category": cat, "n_muon": n_muon, "n_constituents": len(cs),
            "constituent_counts": counts, "iso": iso_val,
            "pass_kin": passes_kin, "pass_mult": passes_mult,
            "pass_iso": passes_iso, "pass_displacement": passes_disp,
            "selected": passes_kin and passes_mult and passes_iso and passes_disp,
        })
    out.sort(key=lambda d: d["pt"], reverse=True)
    return out, warns


def select_and_build_constituents(event: dict, cfg: dict):
    warns: List[str] = []
    selected: Dict[str, List[dict]] = {}
    for spec in cfg.get("constituents", []):
        sel, w = select_constituent(event.get("objects", {}).get(spec["name"], []), spec)
        selected[spec["name"]] = sel
        warns.extend(w)
    apply_crossclean(selected, cfg.get("crossclean", []))
    consts = build_constituents(selected, cfg.get("constituents", []))
    counts = {f"n_sel_{k}": len(v) for k, v in selected.items()}
    counts["n_constituents"] = len(consts)
    return consts, counts, warns


def finalize_leptonjets(consts, jets, pfjets, sel_counts, cfg) -> dict:
    ljs, w = categorize_and_select(jets, consts, pfjets, cfg)
    sel = [lj for lj in ljs if lj["selected"]]
    counts = dict(sel_counts)
    counts.update({"n_leptonjets": len(ljs), "n_leptonjets_selected": len(sel)})
    for c in {lj["category"] for lj in sel}:
        counts[f"n_{c}_selected"] = sum(1 for lj in sel if lj["category"] == c)
    return {"leptonjets": ljs, "counts": counts, "warnings": w}


def reconstruct_event(event: dict, cfg: dict) -> dict:
    """Full reconstruction for one event using the built-in anti-kT (reference
    path). `event['objects']` maps constituent name -> list of object dicts;
    `event['pfjets']` is the isolation jet collection."""
    consts, sel_counts, warns = select_and_build_constituents(event, cfg)
    jets = antikt_cluster(consts, get(cfg, "clustering.radius", 0.4))
    res = finalize_leptonjets(consts, jets, event.get("pfjets", []), sel_counts, cfg)
    res["warnings"] = warns + res["warnings"]
    return res


# ===================================================================== #
# =================== Event-level flag helpers ======================= #
# ===================================================================== #

def trigger_or(values: List[Optional[bool]]) -> Optional[bool]:
    known = [bool(v) for v in values if v is not None]
    return any(known) if known else None


def _cosmic_veto(muons: List[dict], angle: float) -> bool:
    """Veto (return False) if any muon pair is back-to-back in 3D (opening
    angle > `angle`). `muons` are objects (pt/eta/phi) from is_muon constituents."""
    if len(muons) < 2:
        return True
    import vector
    # a zero-momentum muon has no direction to compare
    dirs = [vector.obj(pt=m["pt"], eta=m["eta"], phi=m["phi"], mass=MUON_MASS)
            for m in muons if m["pt"] > 0]
    for i in range(len(dirs)):
        for j in range(i + 1, len(dirs)):
            if dirs[i].deltaangle(dirs[j]) > angle:   # vector's 3D opening angle
                return False
    return True


def event_filter_branches(cutflow: List[dict]) -> List[str]:
    """All event-level branches the cutflow's event-filter cuts reference."""
    needed = set()
    for c in cutflow:
        t = c.get("type")
        if t == "trigger":
            needed.update(c.get("paths", []))
        elif t == "flag" and c.get("branch"):
            needed.add(c["branch"])
        elif t == "primary_vertex":
            for k in ("flag", "ndof", "z", "x", "y"):
                if c.get(k):
                    needed.add(c[k])
        elif t == "event_scalar" and c.get("branch"):
            needed.add(c["branch"])
    return sorted(needed)


def compute_event_flags(cutflow: List[dict], scalars: dict, i: int, muons: List[dict]) -> Dict[str, bool]:
    """Evaluate a config cutflow's *event-filter* cuts for event `i` from the
    pre-extracted per-event `scalars` (branch -> list) and the event's muon
    objects. Recipes: trigger | flag | primary_vertex | cosmic_veto |
    event_scalar. `object_count` cuts are not event filters and are applied
    downstream on the reconstructed objects. Missing inputs pass (with a
    warning emitted at extraction), so a cut never silently rejects everything."""
    flags: Dict[str, bool] = {}
    for c in cutflow:
        t = c.get("type"); name = c.get("name")
        if t == "trigger":
            vals = [scalars[p][i] if scalars.get(p) is not None else None for p in c.get("paths", [])]
            r = trigger_or(vals); flags[name] = True if r is None else r
        elif t == "flag":
            v = scalars.get(c.get("branch")); flags[name] = True if v is None else bool(v[i])
        elif t == "primary_vertex":
            fl = scalars.get(c.get("flag"))
            if fl is not None:
                flags[name] = bool(fl[i])
            else:
                ndof = scalars.get(c.get("ndof")); z = scalars.get(c.get("z"))
                if ndof is None or z is None:
                    flags[name] = True
                else:
                    x = scalars.get(c.get("x")); y = scalars.get(c.get("y"))
                    import vector
                    rho = vector.obj(x=(x[i] if x else 0.0) or 0.0,
                                     y=(y[i] if y else 0.0) or 0.0).rho
                    flags[name] = (ndof[i] > c.get("ndof_min", 4.0) and abs(z[i]) < c.get("absz_max", 24.0)
                                   and rho < c.get("rho_max", 2.0))
        elif t == "cosmic_veto":
            flags[name] = _cosmic_veto(muons, c.get("angle", 2.9))
        elif t == "event_scalar":
            v = scalars.get(c.get("branch"))
            flags[name] = True if (v is None or v[i] is None) else _OPS[c["op"]](v[i], c["value"])
    return flags


def classify_channel(selected_ljs: List[dict], cfg: dict) -> Optional[str]:
    """Assign a channel from the two leading selected LJs using the config's
    `event_selection.channels` (each {name, categories:[a,b]})."""
    if len(selected_ljs) < 2:
        return None
    lead = sorted(selected_ljs, key=lambda d: d["pt"], reverse=True)[:2]
    got = sorted(lj["category"] for lj in lead)
    for ch in get(cfg, "event_selection.channels", []):
        if sorted(ch.get("categories", [])) == got:
            return ch["name"]
    return None


# ===================================================================== #
# ================ coffea extraction (needs awkward) ================= #
# ===================================================================== #

def _extract_collection(events, coll_name: str, fields: List[str]):
    """Per-event list of object dicts for a NanoAOD collection (fields kept by
    their member name). Missing collection/fields degrade gracefully."""
    import awkward as ak
    n = len(events)
    if not coll_name or coll_name not in events.fields:
        return [[] for _ in range(n)], False
    coll = events[coll_name]
    avail = set(coll.fields)
    data = {f: ak.to_list(coll[f]) for f in fields if f in avail}
    ref = next(iter(data.values()), None)
    if ref is None:
        return [[] for _ in range(n)], True
    result = []
    for i in range(n):
        result.append([{f: data[f][i][j] for f in data} for j in range(len(ref[i]))])
    return result, True


def _event_scalar(events, full_branch: str):
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


def _needed_fields(cspec: dict, displacement: List[dict]) -> List[str]:
    fs = {"pt", "eta", "phi"}
    idspec = cspec.get("id") or {}
    for k in ("field", "fallback_field"):
        if idspec.get(k):
            fs.add(idspec[k])
    for k in (cspec.get("veto") or {}).values():
        if k:
            fs.add(k)
    if cspec.get("crossclean_outer"):
        fs.update(cspec["crossclean_outer"])
    for d in displacement:
        if d.get("constituent") == cspec.get("name") and d.get("field"):
            fs.add(d["field"])
    return sorted(fs)


def extract_events(events, cfg: dict):
    """coffea NanoEvents -> per-event candidate records + event flag inputs."""
    warns: List[str] = []
    disp = get(cfg, "leptonjet.displacement", [])
    # constituents
    per_const = {}
    for spec in cfg.get("constituents", []):
        fields = _needed_fields(spec, disp)
        # include outer coords if any crossclean uses them
        for cc in cfg.get("crossclean", []):
            if cc.get("use_outer") and spec["name"] in (cc.get("target"), cc.get("reference")):
                fields = sorted(set(fields) | {cc.get("outer_eta_field", "outerEta"),
                                               cc.get("outer_phi_field", "outerPhi")})
        rec, ok = _extract_collection(events, spec["collection"], fields)
        if not ok:
            warns.append(f"collection '{spec['collection']}' ({spec['name']}) not found; treated as empty")
        per_const[spec["name"]] = rec
    # isolation jets
    isojet = get(cfg, "leptonjet.isolation.jet_collection", "Jet")
    jet_fields = ["pt", "eta", "phi", "mass"] + list(get(cfg, "leptonjet.isolation.lepton_fraction_branches", []))
    jet_rec, jok = _extract_collection(events, isojet, jet_fields)
    if not jok:
        warns.append(f"jet collection '{isojet}' not found; isolation will be 0")
    for evj in jet_rec:
        for j in evj:
            j["E"] = make_p4(j.get("pt", 0.0), j.get("eta", 0.0), j.get("phi", 0.0), j.get("mass") or 0.0)["E"]
    # event-level scalars referenced by the cutflow's event-filter cuts
    cutflow = get(cfg, "event_selection.cutflow", [])
    scalars = {b: _event_scalar(events, b) for b in event_filter_branches(cutflow)}
    for c in cutflow:
        if c.get("type") == "trigger" and c.get("paths") and all(scalars.get(p) is None for p in c["paths"]):
            warns.append(f"cut '{c.get('name')}': no trigger branches found; passes all")

    n = len(events)
    records = []
    for i in range(n):
        records.append({
            "objects": {name: per_const[name][i] for name in per_const},
            "pfjets": jet_rec[i],
        })
    return records, scalars, list(dict.fromkeys(warns))


def _stamp_objects(consts: List[dict], pfjets: List[dict]) -> Dict[str, List[dict]]:
    """Per-event object collections to write alongside the leptonjets, so the
    generic quantity engine can build observables over them (e.g. leading muon
    + leading jet). Keyed by constituent name (electron/photon/pfmuon/dsamuon)
    plus 'jet'. Each object carries a full 4-vector + pt/eta/phi."""
    objects: Dict[str, List[dict]] = {}
    for c in consts:
        o = {"px": c["px"], "py": c["py"], "pz": c["pz"], "E": c["E"],
             "pt": p4_pt(c), "eta": p4_eta(c), "phi": p4_phi(c)}
        objects.setdefault(c["cname"], []).append(o)
    jets = []
    for j in pfjets:
        p = make_p4(j.get("pt", 0.0), j.get("eta", 0.0), j.get("phi", 0.0), j.get("mass") or 0.0)
        p.update(pt=j.get("pt", 0.0), eta=j.get("eta", 0.0), phi=j.get("phi", 0.0))
        jets.append(p)
    objects["jet"] = jets
    return objects


def _muon_dirs(record: dict, cfg: dict) -> List[dict]:
    """Objects from all is_muon constituents, for the cosmic veto."""
    mus = []
    for spec in cfg.get("constituents", []):
        if spec.get("is_muon"):
            mus.extend(record["objects"].get(spec["name"], []))
    return mus


def _idx(lst, i): return lst[i] if lst is not None else None


# ===================================================================== #
# =========================== The tool =============================== #
# ===================================================================== #


# ===================================================================== #
# ============ LeptonJet collection as a coffea-readable file ========= #
# ===================================================================== #

# Fields written per Lepton Jet. Names follow NanoAOD convention
# (`LeptonJet_pt`, ...) so coffea's schema groups them into `events.LeptonJet`.
LJ_FLOAT_FIELDS = ("pt", "eta", "phi", "mass", "iso")
LJ_INT_FIELDS = ("nMuon", "nConstituents", "categoryId")
LJ_BOOL_FIELDS = ("selected", "passKin", "passMult", "passIso", "passDisplacement")

_LJ_SRC = {"nMuon": "n_muon", "nConstituents": "n_constituents",
           "passKin": "pass_kin", "passMult": "pass_mult",
           "passIso": "pass_iso", "passDisplacement": "pass_displacement"}


def _fastjet_fallback_reason(exc: Exception) -> str:
    """Explain why fastjet could not be used, actionably.

    The usual cause on an LPC-style setup is a CMSSW environment leaking into
    the shell: `cmsenv` puts /cvmfs/.../libfastjet*.so on LD_LIBRARY_PATH, which
    shadows the libfastjet that the pip `fastjet` wheel ships, and its SWIG
    extension then fails to resolve a symbol. The built-in anti-kT that takes
    over agrees with fastjet to ~1e-9 (see test_antikt_agrees_with_fastjet_
    backend), so results stay correct -- but it is much slower.
    """
    msg = str(exc)
    if "undefined symbol" in msg and "fastjet" in msg.lower():
        cmssw = [p for p in os.environ.get("LD_LIBRARY_PATH", "").split(":")
                 if "/cvmfs/" in p or "CMSSW" in p]
        if cmssw:
            return ("fastjet shadowed by a CMSSW environment on LD_LIBRARY_PATH "
                    f"({cmssw[0]}); used the built-in anti-kT instead (same "
                    "numbers, slower). Run this from a shell where `cmsenv` has "
                    "NOT been sourced.")
        return ("fastjet's SWIG extension failed to load (undefined symbol) -- a "
                "libfastjet ABI mismatch; used the built-in anti-kT instead "
                "(same numbers, slower).")
    return (f"fastjet unavailable ({type(exc).__name__}: {msg[:120]}); used the "
            "built-in anti-kT instead (same numbers, slower)")


def write_leptonjet_root(path, per_event, categories, channels, event_ids, cut_names):
    """Write the reconstructed Lepton Jets as a NanoAOD-style ROOT file.

    Read it back with coffea and the collection behaves like any other:

        from tools.analysis.analysis_config import build_schema
        schema = build_schema(cfg, extra_mixins={"LeptonJet": "PtEtaPhiMLorentzVector"})
        events = NanoEventsFactory.from_root({path: "Events"}, schemaclass=schema).events()
        events.LeptonJet.pt
        (events.LeptonJet[:, 0] + events.LeptonJet[:, 1]).mass
        events.LeptonJet.delta_r(events.LeptonJet)

    Categories and channels are strings, which do not fit in a flat branch, so
    they are written as integer ids; the legends come back in the tool's JSON.
    """
    import awkward as ak
    import numpy as np
    import uproot

    cat_id = {c: i for i, c in enumerate(categories)}
    chan_id = {c: i for i, c in enumerate(channels)}
    counts = np.array([len(e["leptonjets"]) for e in per_event], dtype=np.int64)

    def jag(name, cast):
        src = _LJ_SRC.get(name, name)
        flat = [lj.get(src) for e in per_event for lj in e["leptonjets"]]
        if name == "categoryId":
            flat = [cat_id.get(lj.get("category"), -1)
                    for e in per_event for lj in e["leptonjets"]]
        flat = [(cast(0) if v is None else cast(v)) for v in flat]
        return ak.unflatten(np.array(flat, dtype=cast), counts)

    data = {"nLeptonJet": counts.astype(np.uint32)}
    for f in LJ_FLOAT_FIELDS:
        data[f"LeptonJet_{f}"] = jag(f, np.float64)
    for f in LJ_INT_FIELDS:
        data[f"LeptonJet_{f}"] = jag(f, np.int32)
    for f in LJ_BOOL_FIELDS:
        data[f"LeptonJet_{f}"] = jag(f, bool)

    # per-event: the cutflow flags, the channel, and ids for joining back
    for name in cut_names:
        data[f"Flag_{name}"] = np.array(
            [bool(e["event"]["flags"].get(name, True)) for e in per_event], dtype=bool)
    # channelId keeps the compact form; the per-channel booleans make the file
    # self-describing -- coffea groups `Channel_*` into `events.Channel`, so
    # `events.Channel["4mu"]` works without carrying an id->name legend around.
    data["channelId"] = np.array(
        [chan_id.get(e["event"]["channel"], -1) for e in per_event], dtype=np.int32)
    for name in channels:
        data[f"Channel_{name}"] = np.array(
            [e["event"]["channel"] == name for e in per_event], dtype=bool)
    for key in ("run", "luminosityBlock", "event"):
        if event_ids.get(key) is not None:
            data[key] = np.asarray(event_ids[key])

    with uproot.recreate(path) as fh:
        fh["Events"] = data
    return {"n_events": int(counts.size), "n_leptonjets": int(counts.sum()),
            "category_ids": cat_id, "channel_ids": chan_id}


class LeptonJetTool(BaseTool):
    """
    Reconstruct Lepton Jets from a (LLP)NanoAOD ROOT file, driven entirely by a
    config. Object selection + cross-cleaning of the configured constituent
    collections, anti-kT clustering, categorization, isolation and displacement
    cuts -> a per-event JSONL of leptonjets plus trigger/PV/cosmic flags and the
    channel.

    Provide the analysis via `config` (a YAML path relative to base_directory,
    or an inline dict) plus optional inline `overrides`. A ready-made SIDM config
    ships as configs/sidm.yaml (see the sidm skill).

    Inputs: root_path, config, overrides, output_path, tree_name, max_events.
    """
    root_path: str = RuntimeField(description="Sandbox-relative path to the (LLP)NanoAOD .root file")
    config: Optional[Any] = RuntimeField(default=None, description="Analysis config: a YAML path (relative to base_directory) or an inline dict. Defines constituents, cuts, categories, channels, triggers, etc.")
    overrides: Optional[dict] = RuntimeField(default=None, description="Inline config overrides merged last (deep-merged onto the config).")
    output_path: Optional[str] = RuntimeField(default=None, description="Sandbox-relative output path (default: <input>_leptonjets.root)")
    output_format: str = RuntimeField(default="root", description="'root' writes a NanoAOD-style LeptonJet collection coffea can open; 'jsonl' the legacy per-event records")
    tree_name: Optional[str] = RuntimeField(default=None, description="TTree name (default: config 'tree' or 'Events')")
    max_events: Optional[int] = RuntimeField(default=None, description="Process at most this many events")

    base_directory: str = StateField(default=".", description="Base directory for safe paths")

    def _setup(self):
        self.base_directory = os.path.abspath(self.base_directory)
        if not os.path.exists(self.base_directory):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def _safe_path(self, rel):
        if not rel:
            return None
        full = os.path.abspath(os.path.join(self.base_directory, rel))
        return full if full.startswith(self.base_directory) else None

    def _read_events(self, src, tree_name, cfg):
        from coffea.nanoevents import NanoEventsFactory
        schema = build_schema(cfg)
        for kwargs in ({"schemaclass": schema}, {"schemaclass": schema, "delayed": False},
                       {"schemaclass": schema, "mode": "eager"}):
            try:
                events = NanoEventsFactory.from_root({src: tree_name}, **kwargs).events()
                _ = events.fields
                return events
            except Exception:
                continue
        import uproot
        return NanoEventsFactory.from_root(uproot.open(src)[tree_name], schemaclass=schema).events()

    def _run(self) -> str:
        src = self._safe_path(self.root_path)
        if not src:
            return self.format_error(error="Access Denied", reason="root_path escapes base_directory")
        if not os.path.exists(src):
            return self.format_error(error="File Not Found", reason=f"ROOT file not found: {self.root_path}")
        try:
            cfg = load_config(self.config, self.base_directory, self.overrides)
        except Exception as e:
            return self.format_error(error="Invalid Config", reason=str(e))

        tree_name = self.tree_name or cfg.get("tree", "Events")
        try:
            events = self._read_events(src, tree_name, cfg)
        except ImportError as e:
            return self.format_error(error="Missing Dependency", reason=str(e), suggestion="pip install coffea awkward uproot fastjet")
        except Exception as e:
            return self.format_error(error="Read Error", reason=str(e))
        if self.max_events is not None:
            events = events[: int(self.max_events)]
        try:
            records, flags_scalars, warns = extract_events(events, cfg)
        except Exception as e:
            return self.format_error(error="Extraction Error", reason=str(e))

        out_rel = self.output_path or self._default_output()
        dst = self._safe_path(out_rel)
        if not dst:
            return self.format_error(error="Access Denied", reason="output_path escapes base_directory")
        if os.path.dirname(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)

        if self.output_format not in ("root", "jsonl"):
            return self.format_error(
                error="Invalid Output Format", reason=f"output_format={self.output_format!r}",
                suggestion="Use 'root' (a coffea-readable LeptonJet collection) or 'jsonl'")
        n = len(records); all_warns = set(warns)
        # Phase 1: constituents
        consts_all, selcounts_all = [], []
        for i in range(n):
            c, sc, w = select_and_build_constituents(records[i], cfg)
            consts_all.append(c); selcounts_all.append(sc); all_warns.update(w)
        # Phase 2: clustering
        R = get(cfg, "clustering.radius", 0.4)
        jets_all = None
        if get(cfg, "clustering.backend", "fastjet") == "fastjet":
            try:
                jets_all = cluster_fastjet_batch(consts_all, R)
            except Exception as e:
                all_warns.add(_fastjet_fallback_reason(e))
        if jets_all is None:
            jets_all = [antikt_cluster(c, R) for c in consts_all]
        # Phase 3: finalize + evaluate the config cutflow + write JSONL
        cutflow = get(cfg, "event_selection.cutflow", [])
        cut_names = [c.get("name") for c in cutflow]
        cutflow_counts = {name: 0 for name in cut_names}
        n_fullpass = 0; chan_counts = {}
        per_event = []
        for i in range(n):
            reco = finalize_leptonjets(consts_all[i], jets_all[i], records[i]["pfjets"], selcounts_all[i], cfg)
            all_warns.update(reco["warnings"])
            muons = _muon_dirs(records[i], cfg)
            ev_flags = compute_event_flags(cutflow, flags_scalars, i, muons)
            sel = [lj for lj in reco["leptonjets"] if lj["selected"]]
            channel = classify_channel(sel, cfg)
            data = {"leptonjets": reco["leptonjets"],
                    "objects": _stamp_objects(consts_all[i], records[i]["pfjets"]),
                    "event": {"flags": ev_flags, "channel": channel, "counts": reco["counts"]}}
            passed = apply_cutflow(data, cutflow)
            for name in passed:
                cutflow_counts[name] += 1
            if len(passed) == len(cutflow):
                n_fullpass += 1
                if channel:
                    chan_counts[channel] = chan_counts.get(channel, 0) + 1
            per_event.append(data)

        result = {
            "status": "ok", "config": cfg.get("name", "generic"), "root_path": self.root_path,
            "n_events": n,
            "cutflow": [{"cut": "initial", "passed": n}]
                       + [{"cut": nm, "passed": cutflow_counts[nm]} for nm in cut_names],
            "passed_all": n_fullpass, "channels": chan_counts, "warnings": sorted(all_warns),
        }

        if self.output_format == "jsonl":
            with open(dst, "w") as fh:
                for i, data in enumerate(per_event):
                    fh.write(json.dumps({"event_id": i, "schema_version": "evtjsonl-1.0",
                                         "data": data},
                                        separators=(",", ":"), ensure_ascii=False) + "\n")
            result["output_jsonl"] = out_rel
        else:
            cats = [c.get("name") for c in get(cfg, "leptonjet.categories", []) if c.get("name")]
            for d in per_event:
                for lj in d["leptonjets"]:
                    if lj.get("category") and lj["category"] not in cats:
                        cats.append(lj["category"])
            chans = [c.get("name") for c in get(cfg, "event_selection.channels", []) if c.get("name")]
            ids = {}
            for key in ("run", "luminosityBlock", "event"):
                try:
                    import awkward as ak
                    ids[key] = ak.to_numpy(events[key]) if key in events.fields else None
                except Exception:                                # noqa: BLE001
                    ids[key] = None
            try:
                info = write_leptonjet_root(dst, per_event, cats, chans, ids, cut_names)
            except Exception as e:                               # noqa: BLE001
                return self.format_error(error="Write Failed", reason=str(e),
                                         suggestion="uproot is required to write the LeptonJet ROOT file")
            result["output_root"] = out_rel
            result["n_leptonjets"] = info["n_leptonjets"]
            result["category_ids"] = info["category_ids"]
            result["channel_ids"] = info["channel_ids"]
            result["read_back"] = (
                "build_schema(cfg, extra_mixins={'LeptonJet': 'PtEtaPhiMLorentzVector'}) "
                "then NanoEventsFactory.from_root({path: 'Events'}) -> events.LeptonJet")
        return json.dumps(result, separators=(",", ":"), ensure_ascii=False)

    def _default_output(self):
        base = os.path.splitext(os.path.basename(self.root_path))[0]
        d = os.path.dirname(self.root_path)
        ext = "jsonl" if getattr(self, "output_format", "root") == "jsonl" else "root"
        name = f"{base}_leptonjets.{ext}"
        return os.path.join(d, name) if d else name


# Back-compat alias (the analysis-specific name kept for existing callers).
SIDMLeptonJetTool = LeptonJetTool
