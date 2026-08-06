"""
# gen_kinematics.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Config-driven gen-level resonance kinematics.

Given a resonance PDG id and its lepton daughters (from the config's `gen`
block), reconstruct, per resonance: pT, eta, transverse decay length Lxy (from
the daughter production vertex), di-lepton Delta R, and leading/sub-leading
lepton pT; plus the Delta-phi between the two resonances. For the SIDM config
this is the dark-photon (Z_D) kinematics of AN-23-107 Sec. 3 (Figs 2-6). No
analysis-specific values are hard-coded.
"""
from __future__ import annotations

import json
import os
import math
from typing import Dict, List, Optional, Any

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis.analysis_config import load_config, get, delta_phi, delta_r, build_schema
from tools.analysis.leptonjets import _extract_collection


def analyze_gen_event(gen: List[dict], cfg: dict) -> Dict[str, Any]:
    """Per-event resonance kinematics. `gen` is a list of GenPart dicts with
    pdgId, pt, eta, phi, mass, status, mother_idx, vx, vy."""
    res_id = get(cfg, "gen.resonance_pdgid")
    lep_ids = [abs(x) for x in get(cfg, "gen.lepton_pdgids", [11, 13])]
    daughters: Dict[int, List[dict]] = {}
    for g in gen:
        if abs(g["pdgId"]) in lep_ids:
            m = g.get("mother_idx")
            if m is not None and m >= 0:
                daughters.setdefault(m, []).append(g)
    res_all = [i for i, g in enumerate(gen) if res_id is not None and abs(g["pdgId"]) == res_id]
    res_dec = [i for i in res_all if len(daughters.get(i, [])) >= 2]
    idx = sorted(res_dec if res_dec else res_all, key=lambda i: gen[i]["pt"], reverse=True)[:2]

    out = []
    for i in idx:
        g = gen[i]
        leps = sorted(daughters.get(i, []), key=lambda d: d["pt"], reverse=True)
        lxy = None
        if leps and leps[0].get("vx") is not None and leps[0].get("vy") is not None:
            lxy = math.hypot(leps[0]["vx"], leps[0]["vy"])
        dR = delta_r(leps[0]["eta"], leps[0]["phi"], leps[1]["eta"], leps[1]["phi"]) if len(leps) >= 2 else None
        flavor = None
        if leps:
            flavor = "ee" if abs(leps[0]["pdgId"]) == 11 else ("mm" if abs(leps[0]["pdgId"]) == 13 else str(leps[0]["pdgId"]))
        out.append({"pt": g["pt"], "eta": g["eta"], "phi": g["phi"], "lxy": lxy, "dR": dR,
                    "lead_pt": leps[0]["pt"] if leps else None,
                    "sublead_pt": leps[1]["pt"] if len(leps) >= 2 else None, "flavor": flavor})
    dphi = delta_phi(out[0]["phi"], out[1]["phi"]) if len(out) >= 2 else None
    return {"resonances": out, "dphi": dphi}


class GenKinematicsTool(BaseTool):
    """
    Reproduce gen-level resonance kinematics from a (LLP)NanoAOD file, driven by
    the config's `gen` block (resonance_pdgid, lepton_pdgids, mother/vertex
    fields). For the SIDM config: dark-photon pT / eta / Delta-phi / Lxy and
    di-lepton Delta R (Figs 2-6). Saves arrays + histograms + summary stats.

    Inputs: root_path, config, overrides, tree_name, max_events, output_prefix, hist_bins.
    """
    root_path: str = RuntimeField(description="Sandbox-relative path to the (LLP)NanoAOD .root file")
    config: Optional[Any] = RuntimeField(default=None, description="Analysis config (YAML path or dict) with a 'gen' block")
    overrides: Optional[dict] = RuntimeField(default=None, description="Inline config overrides")
    tree_name: Optional[str] = RuntimeField(default=None, description="TTree name (default: config 'tree' or 'Events')")
    max_events: Optional[int] = RuntimeField(default=None, description="Process at most this many events")
    output_prefix: Optional[str] = RuntimeField(default=None, description="Prefix for saved arrays/histograms")
    hist_bins: int = RuntimeField(default=50, description="Number of histogram bins")

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
                ev = NanoEventsFactory.from_root({src: tree_name}, **kwargs).events()
                _ = ev.fields
                return ev
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
        if get(cfg, "gen.resonance_pdgid") is None:
            return self.format_error(error="No Resonance", reason="config gen.resonance_pdgid is not set")

        tree_name = self.tree_name or cfg.get("tree", "Events")
        try:
            events = self._read_events(src, tree_name, cfg)
        except ImportError as e:
            return self.format_error(error="Missing Dependency", reason=str(e))
        except Exception as e:
            return self.format_error(error="Read Error", reason=str(e))
        if self.max_events is not None:
            events = events[: int(self.max_events)]

        coll = get(cfg, "collections.genpart", "GenPart")
        mother_f = get(cfg, "gen.mother_field", "genPartIdxMother")
        vx_f = get(cfg, "gen.vertex.vx", "vx"); vy_f = get(cfg, "gen.vertex.vy", "vy")
        fields = ["pdgId", "pt", "eta", "phi", "mass", "status", mother_f, vx_f, vy_f]
        try:
            gen_rec, ok = _extract_collection(events, coll, fields)
        except Exception as e:
            return self.format_error(error="Extraction Error", reason=str(e))
        if not ok:
            return self.format_error(error="No GenPart", reason=f"collection '{coll}' not found")

        # normalize mother/vertex field names -> canonical keys
        for ev in gen_rec:
            for o in ev:
                o["mother_idx"] = o.get(mother_f)
                o["vx"] = o.get(vx_f); o["vy"] = o.get(vy_f)

        warns = []
        acc: Dict[str, List[float]] = {k: [] for k in
            ("res_pt", "res_eta", "dphi", "lxy", "dR_ee", "dR_mm",
             "lead_ee_pt", "sublead_ee_pt", "lead_mm_pt", "sublead_mm_pt")}
        lxy_seen = False
        for gen in gen_rec:
            r = analyze_gen_event(gen, cfg)
            if r["dphi"] is not None:
                acc["dphi"].append(abs(r["dphi"]))
            for res in r["resonances"]:
                acc["res_pt"].append(res["pt"]); acc["res_eta"].append(res["eta"])
                if res["lxy"] is not None:
                    acc["lxy"].append(res["lxy"]); lxy_seen = True
                if res["dR"] is not None and res["flavor"] in ("ee", "mm"):
                    acc[f"dR_{res['flavor']}"].append(res["dR"])
                    if res["lead_pt"]:
                        acc[f"lead_{res['flavor']}_pt"].append(res["lead_pt"])
                    if res["sublead_pt"]:
                        acc[f"sublead_{res['flavor']}_pt"].append(res["sublead_pt"])
        if not lxy_seen:
            warns.append("gen-vertex fields absent; Lxy not computed")

        import numpy as np
        prefix = self.output_prefix
        if prefix is None:
            base = os.path.splitext(os.path.basename(self.root_path))[0]
            d = os.path.dirname(self.root_path)
            prefix = os.path.join(d, f"{base}_genkin") if d else f"{base}_genkin"
        abs_prefix = prefix if os.path.isabs(prefix) else os.path.join(self.base_directory, prefix)
        if os.path.dirname(abs_prefix):
            os.makedirs(os.path.dirname(abs_prefix), exist_ok=True)

        histograms, data_paths, stats = {}, {}, {}
        for key, vals in acc.items():
            if vals:
                counts, edges = np.histogram(np.asarray(vals, float), bins=self.hist_bins)
                histograms[key] = {"bins": edges.tolist(), "counts": counts.tolist()}
                p = f"{abs_prefix}_{key}.npy"
                np.save(p, np.asarray(vals, float))
                data_paths[key] = os.path.relpath(p, self.base_directory)
                stats[key] = {"n": len(vals), "mean": float(np.mean(vals))}
            else:
                stats[key] = {"n": 0, "mean": None}
        return json.dumps({
            "status": "ok", "root_path": self.root_path, "n_events": len(gen_rec),
            "mean_dilepton_dR": {"ee": stats["dR_ee"]["mean"], "mm": stats["dR_mm"]["mean"]},
            "stats": stats, "histograms": histograms, "data_paths": data_paths, "warnings": warns,
        }, separators=(",", ":"), ensure_ascii=False)


SIDMGenKinematicsTool = GenKinematicsTool
