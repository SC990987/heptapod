"""
# sidm_selection.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

SIDMEventSelectionTool -- event selection, cutflow, and the LJ-LJ invariant
mass (reconstructed chi-chibar bound-state mass) for the SIDM analysis
(CMS AN-23-107, Sec. 5).

It consumes the JSONL written by `SIDMLeptonJetTool` (one line per event, with
a reconstructed `leptonjets` collection and an `event` block carrying the
trigger / primary-vertex / cosmic-veto flags and the channel) and produces:

  * a cutflow: initial -> trigger -> PV filter -> cosmic veto -> >=2 selected
    LJs (Sec. 5.2-5.3),
  * per-channel yields (4mu, 2mu2e),
  * the LJ-LJ invariant-mass distribution per channel -- the signal-region
    observable the note bins in (Sec. 1.3, 3), saved as .npy + histograms.

All tabulation is in the pure `compute_selection` function so it is unit-
testable without the `orchestral` framework.
"""
from __future__ import annotations

import json
import os
import math
from typing import Dict, List, Optional, Any, Iterable

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField


# ===================================================================== #
# ============================ Pure logic ============================ #
# ===================================================================== #

def _invariant_mass(objs: List[dict]) -> float:
    """Invariant mass of a list of objects carrying px,py,pz,E."""
    px = sum(o["px"] for o in objs)
    py = sum(o["py"] for o in objs)
    pz = sum(o["pz"] for o in objs)
    E = sum(o["E"] for o in objs)
    m2 = E * E - px * px - py * py - pz * pz
    if -1e-6 < m2 < 0:
        m2 = 0.0
    return math.sqrt(m2) if m2 > 0 else 0.0


def leading_two_selected(leptonjets: List[dict]) -> List[dict]:
    """The two highest-pT LJs flagged `selected` by the reconstruction tool."""
    sel = [lj for lj in leptonjets if lj.get("selected")]
    sel.sort(key=lambda d: d.get("pt", 0.0), reverse=True)
    return sel[:2]


def ljlj_mass(leptonjets: List[dict]) -> Optional[float]:
    """LJ-LJ invariant mass from the two leading selected LJs (or None)."""
    lead = leading_two_selected(leptonjets)
    if len(lead) < 2:
        return None
    return _invariant_mass(lead)


def compute_selection(events: Iterable[dict], hist_bins: int = 50,
                      hist_range: Optional[List[float]] = None) -> Dict[str, Any]:
    """Tabulate the preselection cutflow, channel yields, and LJ-LJ masses.

    `events` is an iterable of parsed JSONL records with
    data.leptonjets (list) and data.event {trig_pass, pv_pass,
    cosmic_veto_pass, channel}. Returns a dict (see keys below); histograms are
    added by the caller so the pure function stays numpy-free.
    """
    n_initial = n_trig = n_pv = n_cosmic = n_2lj = 0
    n_4mu = n_2mu2e = 0
    masses_all: List[float] = []
    masses_4mu: List[float] = []
    masses_2mu2e: List[float] = []

    for rec in events:
        n_initial += 1
        data = rec.get("data", rec)
        ev = data.get("event", {})
        ljs = data.get("leptonjets", [])

        if not ev.get("trig_pass", True):
            continue
        n_trig += 1
        if not ev.get("pv_pass", True):
            continue
        n_pv += 1
        if not ev.get("cosmic_veto_pass", True):
            continue
        n_cosmic += 1

        lead = leading_two_selected(ljs)
        if len(lead) < 2:
            continue
        n_2lj += 1

        channel = ev.get("channel")
        if channel is None:
            # derive from leading two if the reco tool didn't store it
            types = sorted(lj["type"] for lj in lead)
            channel = "4mu" if types == ["mu", "mu"] else (
                "2mu2e" if types == ["eg", "mu"] else None)

        m = _invariant_mass(lead)
        masses_all.append(m)
        if channel == "4mu":
            n_4mu += 1
            masses_4mu.append(m)
        elif channel == "2mu2e":
            n_2mu2e += 1
            masses_2mu2e.append(m)

    return {
        "cutflow": [
            {"cut": "initial", "passed": n_initial},
            {"cut": "trigger", "passed": n_trig},
            {"cut": "pv_filter", "passed": n_pv},
            {"cut": "cosmic_veto", "passed": n_cosmic},
            {"cut": "two_leptonjets", "passed": n_2lj},
        ],
        "channels": {"4mu": n_4mu, "2mu2e": n_2mu2e},
        "masses": {"all": masses_all, "4mu": masses_4mu, "2mu2e": masses_2mu2e},
        "hist_bins": hist_bins,
        "hist_range": hist_range,
    }


# ===================================================================== #
# ============================== The tool ============================= #
# ===================================================================== #

class SIDMEventSelectionTool(BaseTool):
    """
    Apply the SIDM event selection and reconstruct the LJ-LJ invariant mass.

    Consumes the JSONL from `SIDMLeptonJetTool` and reports the preselection
    cutflow (trigger -> PV filter -> cosmic veto -> >=2 selected LJs), the
    4mu / 2mu2e channel yields, and the LJ-LJ invariant-mass distribution
    (reconstructed bound-state mass) per channel. Mass arrays are saved as .npy
    and returned as histograms.

    Inputs (runtime):
      - leptonjets_jsonl: sandbox-relative JSONL from SIDMLeptonJetTool.
      - output_prefix: prefix for saved arrays/histograms (default: alongside
        the input file).
      - hist_bins: number of LJ-LJ mass histogram bins (default 50).
      - hist_range: [min, max] GeV for the histogram (default: auto).

    Output (JSON): cutflow, channels, histograms {all,4mu,2mu2e}, data_paths.
    """
    leptonjets_jsonl: str = RuntimeField(description="Sandbox-relative JSONL produced by SIDMLeptonJetTool")
    output_prefix: Optional[str] = RuntimeField(default=None, description="Prefix for saved .npy arrays / histograms (default: derived from input)")
    hist_bins: int = RuntimeField(default=50, description="Number of LJ-LJ mass histogram bins")
    hist_range: Optional[List[float]] = RuntimeField(default=None, description="[min, max] GeV for the LJ-LJ mass histogram (default: auto)")

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

    def _histogram(self, values, bins, hist_range):
        import numpy as np
        if len(values) == 0:
            return {"bins": [], "counts": []}
        if hist_range is not None:
            rng = tuple(hist_range)
        else:
            lo, hi = float(min(values)), float(max(values))
            if lo == hi:
                lo, hi = lo - 1.0, hi + 1.0
            rng = (lo, hi)
        counts, edges = np.histogram(np.asarray(values, dtype=float), bins=bins, range=rng)
        return {"bins": edges.tolist(), "counts": counts.tolist()}

    def _run(self) -> str:
        src = self._safe_path(self.leptonjets_jsonl)
        if not src:
            return self.format_error(error="Access Denied", reason="leptonjets_jsonl escapes base_directory")
        if not os.path.exists(src):
            return self.format_error(error="File Not Found", reason=f"JSONL not found: {self.leptonjets_jsonl}")

        try:
            records = []
            with open(src) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        except Exception as e:
            return self.format_error(error="Read Error", reason=str(e))

        if not records:
            return self.format_error(
                error="Empty Input",
                reason=f"No events in {self.leptonjets_jsonl}",
                suggestion="Check that SIDMLeptonJetTool produced a non-empty file.",
            )

        result = compute_selection(records, self.hist_bins, self.hist_range)

        # histograms + save mass arrays
        import numpy as np
        prefix = self.output_prefix
        if prefix is None:
            base = os.path.splitext(os.path.basename(self.leptonjets_jsonl))[0]
            d = os.path.dirname(self.leptonjets_jsonl)
            prefix = os.path.join(d, f"{base}_selection") if d else f"{base}_selection"
        abs_prefix = prefix if os.path.isabs(prefix) else os.path.join(self.base_directory, prefix)
        out_dir = os.path.dirname(abs_prefix)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        histograms = {}
        data_paths = {}
        for key, vals in result["masses"].items():
            histograms[key] = self._histogram(vals, self.hist_bins, self.hist_range)
            p = f"{abs_prefix}_ljlj_mass_{key}.npy"
            np.save(p, np.asarray(vals, dtype=float))
            data_paths[key] = os.path.relpath(p, self.base_directory)

        mass_stats = {
            key: {"n": len(vals),
                  "mean": float(np.mean(vals)) if vals else None,
                  "median": float(np.median(vals)) if vals else None}
            for key, vals in result["masses"].items()
        }
        out = {
            "status": "ok",
            "leptonjets_jsonl": self.leptonjets_jsonl,
            "n_events": len(records),
            "cutflow": result["cutflow"],
            "final_passed": result["cutflow"][-1]["passed"],
            "channels": result["channels"],
            "ljlj_mass_histograms": histograms,
            "ljlj_mass_stats": mass_stats,
            "data_paths": data_paths,
        }
        return json.dumps(out, separators=(",", ":"), ensure_ascii=False)
