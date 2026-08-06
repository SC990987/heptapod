"""
# event_selection.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Config-driven event selection, cutflow, and the leading-object invariant mass.

Consumes the JSONL written by LeptonJetTool (per-event leptonjets + trigger/PV/
cosmic flags + channel) and produces the preselection cutflow, per-channel
yields, and the invariant mass of the N leading selected Lepton Jets per channel
(for SIDM: the two leading LJs = the bound-state mass). Channels and the number
of leading objects come from the config; nothing here is analysis-specific.
"""
from __future__ import annotations

import json
import os
import math
from typing import Dict, List, Optional, Any, Iterable

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis.analysis_config import load_config, get


def _invariant_mass(objs: List[dict]) -> float:
    px = sum(o["px"] for o in objs); py = sum(o["py"] for o in objs)
    pz = sum(o["pz"] for o in objs); E = sum(o["E"] for o in objs)
    m2 = E * E - px * px - py * py - pz * pz
    if -1e-6 < m2 < 0:
        m2 = 0.0
    return math.sqrt(m2) if m2 > 0 else 0.0


def _default_observables():
    """Fallback if the config declares none: invariant mass of the two leading
    selected leptonjets (the classic LJ-LJ / bound-state mass), per channel."""
    return [{"name": "mass", "function": "invariant_mass", "per_channel": True,
             "inputs": [{"object": "leptonjets", "selected": True, "rank": 1},
                        {"object": "leptonjets", "selected": True, "rank": 2}]}]


def compute_selection(events: Iterable[dict], cfg: dict) -> Dict[str, Any]:
    """Tabulate the config cutflow, channel yields, and the config-defined
    derived quantities (`observables`) for events passing the full cutflow.

    Each observable is computed by the shared quantity engine over the event's
    object collections -- `leptonjets` plus the stamped constituent/jet
    collections (muons, jets, ...). So an invariant mass of the leading muon and
    leading jet, or a Delta R between them, is expressible in config alone;
    nothing here is analysis-specific. Pure over parsed JSONL records."""
    from tools.analysis.analysis_config import apply_cutflow, get, evaluate_quantity
    cutflow = get(cfg, "event_selection.cutflow", [])
    observables = get(cfg, "observables", []) or _default_observables()
    cut_names = [c.get("name") for c in cutflow]
    counts = {"initial": 0}
    for nm in cut_names:
        counts[nm] = 0
    channels: Dict[str, int] = {}
    values: Dict[str, List[float]] = {q["name"]: [] for q in observables}
    by_channel: Dict[str, Dict[str, List[float]]] = {}
    for rec in events:
        counts["initial"] += 1
        data = rec.get("data", rec)
        passed = apply_cutflow(data, cutflow)
        for nm in passed:
            counts[nm] += 1
        if len(passed) == len(cutflow):
            ch = data.get("event", {}).get("channel")
            if ch:
                channels[ch] = channels.get(ch, 0) + 1
            collections = {"leptonjets": data.get("leptonjets", [])}
            collections.update(data.get("objects", {}))
            for q in observables:
                v = evaluate_quantity(q, collections)
                if v is not None:
                    values[q["name"]].append(v)
                    if q.get("per_channel") and ch:
                        by_channel.setdefault(q["name"], {}).setdefault(ch, []).append(v)
    return {"cutflow": [{"cut": "initial", "passed": counts["initial"]}]
                       + [{"cut": nm, "passed": counts[nm]} for nm in cut_names],
            "channels": channels, "quantities": values, "quantities_by_channel": by_channel}


class EventSelectionTool(BaseTool):
    """
    Apply the event selection and reconstruct the leading-object invariant mass.

    Consumes the JSONL from LeptonJetTool and reports the cutflow -- built
    generically from the config's ordered `event_selection.cutflow` list (each
    cut is a flag stamped at reconstruction or an object-count on the selected
    LJs) -- the per-channel yields, and the invariant mass of the N leading
    selected LJs per channel (saved as .npy + histograms).

    Inputs: leptonjets_jsonl, config, overrides, output_prefix, hist_bins, hist_range.
    """
    leptonjets_jsonl: str = RuntimeField(description="Sandbox-relative JSONL produced by LeptonJetTool")
    config: Optional[Any] = RuntimeField(default=None, description="Analysis config (YAML path or dict) providing event_selection.cutflow / channels / observable")
    overrides: Optional[dict] = RuntimeField(default=None, description="Inline config overrides")
    output_prefix: Optional[str] = RuntimeField(default=None, description="Prefix for saved arrays/histograms")
    hist_bins: int = RuntimeField(default=50, description="Number of mass histogram bins")
    hist_range: Optional[List[float]] = RuntimeField(default=None, description="[min, max] GeV for the mass histogram")

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

    def _hist(self, values, bins, rng):
        import numpy as np
        if not values:
            return {"bins": [], "counts": []}
        if rng is None:
            lo, hi = float(min(values)), float(max(values))
            if lo == hi:
                lo, hi = lo - 1.0, hi + 1.0
            rng = (lo, hi)
        counts, edges = np.histogram(np.asarray(values, float), bins=bins, range=tuple(rng))
        return {"bins": edges.tolist(), "counts": counts.tolist()}

    def _run(self) -> str:
        src = self._safe_path(self.leptonjets_jsonl)
        if not src:
            return self.format_error(error="Access Denied", reason="leptonjets_jsonl escapes base_directory")
        if not os.path.exists(src):
            return self.format_error(error="File Not Found", reason=f"JSONL not found: {self.leptonjets_jsonl}")
        try:
            cfg = load_config(self.config, self.base_directory, self.overrides)
        except Exception as e:
            return self.format_error(error="Invalid Config", reason=str(e))

        try:
            records = [json.loads(l) for l in open(src) if l.strip()]
        except Exception as e:
            return self.format_error(error="Read Error", reason=str(e))
        if not records:
            return self.format_error(error="Empty Input", reason=f"No events in {self.leptonjets_jsonl}")

        result = compute_selection(records, cfg)
        import numpy as np
        prefix = self.output_prefix
        if prefix is None:
            base = os.path.splitext(os.path.basename(self.leptonjets_jsonl))[0]
            d = os.path.dirname(self.leptonjets_jsonl)
            prefix = os.path.join(d, f"{base}_selection") if d else f"{base}_selection"
        abs_prefix = prefix if os.path.isabs(prefix) else os.path.join(self.base_directory, prefix)
        if os.path.dirname(abs_prefix):
            os.makedirs(os.path.dirname(abs_prefix), exist_ok=True)

        def _emit(key, vals):
            histograms[key] = self._hist(vals, self.hist_bins, self.hist_range)
            p = f"{abs_prefix}_{key}.npy"
            np.save(p, np.asarray(vals, float))
            data_paths[key] = os.path.relpath(p, self.base_directory)
            stats[key] = {"n": len(vals), "mean": float(np.mean(vals)) if vals else None,
                          "median": float(np.median(vals)) if vals else None}

        histograms, data_paths, stats = {}, {}, {}
        for qname, vals in result["quantities"].items():
            _emit(qname, vals)
            for ch, cvals in result["quantities_by_channel"].get(qname, {}).items():
                _emit(f"{qname}_{ch}", cvals)
        return json.dumps({
            "status": "ok", "leptonjets_jsonl": self.leptonjets_jsonl, "n_events": len(records),
            "cutflow": result["cutflow"], "final_passed": result["cutflow"][-1]["passed"],
            "channels": result["channels"], "quantity_histograms": histograms,
            "quantity_stats": stats, "data_paths": data_paths,
        }, separators=(",", ":"), ensure_ascii=False)


SIDMEventSelectionTool = EventSelectionTool
