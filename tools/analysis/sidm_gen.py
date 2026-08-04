"""
# sidm_gen.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

SIDMGenKinematicsTool -- gen-level signal kinematics (CMS AN-23-107, Sec. 3,
Figs 2-6). Reads GenPart from an LLPNanoAOD file with coffea and reproduces:

  * dark-photon (Z_D) pT, eta, and the Delta-phi between the two Z_D (Fig. 2),
  * dark-photon transverse decay length Lxy (Fig. 3) when gen-vertex branches
    are available,
  * leading / sub-leading gen-lepton pT and eta (Fig. 4),
  * di-lepton Delta R per Z_D and its mean vs mass (Figs 5, 6).

The per-event analysis is the pure `analyze_gen_event`; coffea is used only for
columnar reading.
"""
from __future__ import annotations

import json
import os
import math
from typing import Dict, List, Optional, Any

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis.sidm_config import SIDMConfig, delta_phi, delta_r


# ===================================================================== #
# ============================ Pure logic ============================ #
# ===================================================================== #

def analyze_gen_event(gen: List[dict], cfg: SIDMConfig) -> Dict[str, Any]:
    """Per-event gen kinematics. `gen` is a list of GenPart dicts carrying
    pdgId, pt, eta, phi, mass, status, mother_idx, and (optionally) vx, vy.
    Returns {dark_photons: [...], dphi_zdzd: float|None}."""
    dp_id = cfg.fields.dark_photon_pdgid
    e_id, mu_id = cfg.fields.electron_pdgid, cfg.fields.muon_pdgid

    # map each gen particle index -> its lepton daughters
    daughters: Dict[int, List[dict]] = {}
    for g in gen:
        if abs(g["pdgId"]) in (e_id, mu_id):
            m = g.get("mother_idx")
            if m is not None and m >= 0:
                daughters.setdefault(m, []).append(g)

    dp_all = [i for i, g in enumerate(gen) if abs(g["pdgId"]) == dp_id]
    # prefer the copies that actually decay to a lepton pair
    dp_decay = [i for i in dp_all if len(daughters.get(i, [])) >= 2]
    dp_indices = dp_decay if dp_decay else dp_all
    # if several copies remain, take the two highest-pT
    dp_indices = sorted(dp_indices, key=lambda i: gen[i]["pt"], reverse=True)[:2]

    dphotons = []
    for i in dp_indices:
        g = gen[i]
        leps = sorted(daughters.get(i, []), key=lambda d: d["pt"], reverse=True)
        lxy = None
        if leps:
            vx, vy = leps[0].get("vx"), leps[0].get("vy")
            if vx is not None and vy is not None:
                lxy = math.hypot(vx, vy)
        dilepton_dR = None
        if len(leps) >= 2:
            dilepton_dR = delta_r(leps[0]["eta"], leps[0]["phi"],
                                  leps[1]["eta"], leps[1]["phi"])
        flavor = None
        if leps:
            flavor = "ee" if abs(leps[0]["pdgId"]) == e_id else "mm"
        dphotons.append({
            "pt": g["pt"], "eta": g["eta"], "phi": g["phi"], "lxy": lxy,
            "dilepton_dR": dilepton_dR,
            "lead_lep_pt": leps[0]["pt"] if leps else None,
            "sublead_lep_pt": leps[1]["pt"] if len(leps) >= 2 else None,
            "flavor": flavor,
        })

    dphi = None
    if len(dphotons) >= 2:
        dphi = delta_phi(dphotons[0]["phi"], dphotons[1]["phi"])

    return {"dark_photons": dphotons, "dphi_zdzd": dphi}


# ===================================================================== #
# ============================== The tool ============================= #
# ===================================================================== #

class SIDMGenKinematicsTool(BaseTool):
    """
    Reproduce the SIDM gen-level signal kinematics of AN-23-107 Sec. 3
    (Figs 2-6) from an LLPNanoAOD file: dark-photon pT / eta / Delta-phi,
    dark-photon Lxy, leading/sub-leading lepton pT, and di-lepton Delta R.

    Set `field_overrides={'fields':{'dark_photon_pdgid': <id>}}` if your sample
    uses a different PDG id for the dark photon, and point the gen-vertex fields
    (`genpart_vx_field`, `genpart_vy_field`) at the right branches for Lxy.

    Inputs (runtime): root_path, tree_name, max_events, field_overrides,
    output_prefix, hist_bins.
    Output (JSON): distributions (histograms) + summary stats (e.g. mean
    di-lepton Delta R), with arrays saved to .npy.
    """
    root_path: str = RuntimeField(description="Sandbox-relative path to the LLPNanoAOD .root file")
    tree_name: Optional[str] = RuntimeField(default=None, description="TTree name (default 'Events')")
    max_events: Optional[int] = RuntimeField(default=None, description="Process at most this many events")
    output_prefix: Optional[str] = RuntimeField(default=None, description="Prefix for saved arrays/histograms")
    hist_bins: int = RuntimeField(default=50, description="Number of histogram bins")
    field_overrides: Optional[dict] = RuntimeField(default=None, description="Nested SIDMConfig overrides")

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
        # read GenPart via coffea, reusing the reconstruction tool's helpers
        try:
            from tools.analysis.sidm_leptonjets import (
                SIDMLeptonJetTool, _extract_collection,
            )
            reader = SIDMLeptonJetTool(base_directory=self.base_directory,
                                       root_path=self.root_path)
            reader._setup()
            events = reader._read_events(src, tree_name, cfg)
        except ImportError as e:
            return self.format_error(error="Missing Dependency", reason=str(e),
                                     suggestion="pip install coffea awkward uproot")
        except Exception as e:
            return self.format_error(error="Read Error", reason=str(e))

        if self.max_events is not None:
            events = events[: int(self.max_events)]

        f = cfg.fields
        gen_map = {
            "pdgId": "pdgId", "pt": "pt", "eta": "eta", "phi": "phi",
            "mass": "mass", "status": "status",
            "mother_idx": f.genpart_mother_field,
            "vx": f.genpart_vx_field, "vy": f.genpart_vy_field,
        }
        try:
            gen_rec, ok = _extract_collection(events, f.genpart_coll, gen_map)
        except Exception as e:
            return self.format_error(error="Extraction Error", reason=str(e))
        warns = []
        if not ok:
            return self.format_error(
                error="No GenPart",
                reason=f"GenPart collection '{f.genpart_coll}' not found.",
                suggestion="Override fields.genpart_coll or run on a signal MC file.",
            )

        acc: Dict[str, List[float]] = {
            k: [] for k in ("dp_pt", "dp_eta", "dphi", "lxy",
                            "dR_ee", "dR_mm", "lead_e_pt", "sublead_e_pt",
                            "lead_mu_pt", "sublead_mu_pt")
        }
        lxy_missing = True
        for gen in gen_rec:
            r = analyze_gen_event(gen, cfg)
            if r["dphi_zdzd"] is not None:
                acc["dphi"].append(abs(r["dphi_zdzd"]))
            for dp in r["dark_photons"]:
                acc["dp_pt"].append(dp["pt"])
                acc["dp_eta"].append(dp["eta"])
                if dp["lxy"] is not None:
                    acc["lxy"].append(dp["lxy"]); lxy_missing = False
                if dp["dilepton_dR"] is not None:
                    if dp["flavor"] == "ee":
                        acc["dR_ee"].append(dp["dilepton_dR"])
                        if dp["lead_lep_pt"]: acc["lead_e_pt"].append(dp["lead_lep_pt"])
                        if dp["sublead_lep_pt"]: acc["sublead_e_pt"].append(dp["sublead_lep_pt"])
                    elif dp["flavor"] == "mm":
                        acc["dR_mm"].append(dp["dilepton_dR"])
                        if dp["lead_lep_pt"]: acc["lead_mu_pt"].append(dp["lead_lep_pt"])
                        if dp["sublead_lep_pt"]: acc["sublead_mu_pt"].append(dp["sublead_lep_pt"])
        if lxy_missing:
            warns.append("gen-vertex fields absent; dark-photon Lxy not computed")

        import numpy as np
        prefix = self.output_prefix
        if prefix is None:
            base = os.path.splitext(os.path.basename(self.root_path))[0]
            d = os.path.dirname(self.root_path)
            prefix = os.path.join(d, f"{base}_genkin") if d else f"{base}_genkin"
        abs_prefix = prefix if os.path.isabs(prefix) else os.path.join(self.base_directory, prefix)
        out_dir = os.path.dirname(abs_prefix)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        histograms, data_paths, stats = {}, {}, {}
        for key, vals in acc.items():
            if vals:
                counts, edges = np.histogram(np.asarray(vals, dtype=float), bins=self.hist_bins)
                histograms[key] = {"bins": edges.tolist(), "counts": counts.tolist()}
                p = f"{abs_prefix}_{key}.npy"
                np.save(p, np.asarray(vals, dtype=float))
                data_paths[key] = os.path.relpath(p, self.base_directory)
                stats[key] = {"n": len(vals), "mean": float(np.mean(vals))}
            else:
                stats[key] = {"n": 0, "mean": None}

        out = {
            "status": "ok",
            "root_path": self.root_path,
            "n_events": len(gen_rec),
            "mean_dilepton_dR": {"ee": stats["dR_ee"]["mean"], "mm": stats["dR_mm"]["mean"]},
            "stats": stats,
            "histograms": histograms,
            "data_paths": data_paths,
            "warnings": warns,
        }
        return json.dumps(out, separators=(",", ":"), ensure_ascii=False)
