"""
# gen_decay_length.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

GenDecayLengthTool -- transverse decay length Lxy of a long-lived resonance,
read straight from a (LLP)NanoAOD file.

Lxy is a *convention*, not an expression, which is why it is a tool rather than
something to re-derive per analysis:

    Lxy = | (decay vertex) - (production vertex) |_T

  * the **production** vertex is the resonance's own (vx, vy);
  * the **decay** vertex is where its daughters are produced, i.e. the
    daughters' (vx, vy);
  * when the resonance carries no vertex of its own, the origin is used.

The trap this exists to prevent: `hypot(resonance.vx, resonance.vy)` is the
*production* vertex's distance from the origin and is NOT a decay length. On a
SIDM signal sample the two differ by a factor of ~800 (0.04 cm vs 34 cm) and
both look entirely plausible.

Sanity check on any answer: <Lxy> ~ gamma * c*tau with gamma = E/m.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Any, Dict, Optional

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis.analysis_config import load_config, get, build_schema

SCHEMA_VERSION = "gen_decay_length/1.0"


def decay_lengths(events, resonance_pdgid: int, daughter_pdgids, mother_field: str,
                  vx_field: str, vy_field: str, one_per_resonance: bool = True):
    """Columnar Lxy for every resonance that decayed to the given daughters.

    Returns a flat numpy array, one entry per resonance (or per daughter when
    `one_per_resonance` is False). Matches the per-event reference
    implementation in `gen_kinematics.analyze_gen_event` to ~1e-6 cm.
    """
    import awkward as ak
    import numpy as np

    gen = events.GenPart
    for f in (mother_field, vx_field, vy_field, "pdgId"):
        if f not in gen.fields:
            raise KeyError(f"GenPart has no field {f!r}; present: {sorted(gen.fields)[:25]}")

    want = np.abs(np.asarray(list(daughter_pdgids), dtype=int))
    pdg = abs(gen.pdgId)
    is_daughter = ak.zeros_like(pdg, dtype=bool)
    for d in want:
        is_daughter = is_daughter | (pdg == int(d))

    moth_idx = gen[mother_field]
    cand = gen[is_daughter & (moth_idx >= 0)]
    if ak.sum(ak.num(cand)) == 0:
        return np.empty(0, dtype=float)

    mother = gen[cand[mother_field]]                       # per-event index lookup
    from_res = abs(mother.pdgId) == int(resonance_pdgid)
    cand, mother = cand[from_res], mother[from_res]
    if ak.sum(ak.num(cand)) == 0:
        return np.empty(0, dtype=float)

    # production vertex -> decay vertex
    dx = cand[vx_field] - mother[vx_field]
    dy = cand[vy_field] - mother[vy_field]
    lxy = np.sqrt(dx * dx + dy * dy)

    if one_per_resonance:
        # every daughter of one resonance shares its decay vertex, so keep the
        # first daughter of each distinct mother
        midx = cand[mother_field]
        order = ak.argsort(midx, axis=1)
        m_sorted, l_sorted = midx[order], lxy[order]
        head = ak.ones_like(m_sorted[:, :1], dtype=bool)
        rest = m_sorted[:, 1:] != m_sorted[:, :-1]
        first = ak.concatenate([head, rest], axis=1)
        lxy = l_sorted[first]

    return np.asarray(ak.to_numpy(ak.drop_none(ak.flatten(lxy))), dtype=float)


class GenDecayLengthTool(BaseTool):
    """
    Transverse decay length Lxy of a long-lived resonance, from a (LLP)NanoAOD
    file. Reads the ROOT file directly -- no intermediate arrays.

    Lxy is measured from the resonance's own production vertex to its decay
    vertex (where its daughters are produced). Using the resonance's `(vx, vy)`
    alone measures the *production* point instead, which is not a decay length;
    on a displaced signal the two can differ by orders of magnitude.

    Inputs:
      root_path   sandbox-relative path to the NanoAOD/LLPnanoAOD file
      config      analysis config (YAML path or dict) supplying the `gen` block:
                    gen.resonance_pdgid   e.g. 32 for a dark photon
                    gen.lepton_pdgids     daughters that mark a decay, default [11, 13]
                    gen.mother_field      default "genPartIdxMother"
                    gen.vertex.vx / .vy   default "vx" / "vy"
      resonance_pdgid  overrides the config, for a quick one-off

    Returns the Lxy histogram and statistics (n, mean, median, min, max, and the
    quantiles) as JSON. Set `output_path` to also write the raw values as .npy.

    Cross-check the answer against the physics: <Lxy> ~ gamma * c*tau, with
    gamma = E/m. A 0.25 GeV resonance at 250 GeV has gamma ~ 1000, so a c*tau of
    0.4 mm means tens of cm -- not microns.
    """

    root_path: str = RuntimeField(description="Sandbox-relative path to the (LLP)NanoAOD .root file")
    config: Optional[Any] = RuntimeField(default=None, description="Analysis config (YAML path or dict) with a 'gen' block")
    resonance_pdgid: Optional[int] = RuntimeField(default=None, description="Resonance PDG id; overrides the config")
    overrides: Optional[dict] = RuntimeField(default=None, description="Inline config overrides")
    tree_name: Optional[str] = RuntimeField(default=None, description="TTree name (default: config 'tree' or 'Events')")
    max_events: Optional[int] = RuntimeField(default=None, description="Process at most this many events")
    hist_bins: int = RuntimeField(default=50, description="Number of histogram bins")
    hist_max: Optional[float] = RuntimeField(default=None, description="Upper edge of the histogram; default is the 99th percentile")
    one_per_resonance: bool = RuntimeField(default=True, description="One entry per resonance (True) or per daughter (False)")
    output_path: Optional[str] = RuntimeField(default=None, description="Optional .npy path for the raw Lxy values")

    base_directory: str = StateField(default=".", description="Base directory for safe paths")

    def _setup(self):
        self.base_directory = os.path.abspath(self.base_directory)
        if not os.path.isdir(self.base_directory):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def _safe(self, rel):
        if not rel:
            return None
        full = os.path.abspath(os.path.join(self.base_directory, rel))
        return full if (full == self.base_directory or
                        full.startswith(self.base_directory + os.sep)) else None

    def _run(self) -> str:
        src = self._safe(self.root_path)
        if src is None:
            return self.format_error(error="Access Denied",
                                     reason="root_path escapes base_directory")
        if not os.path.exists(src):
            return self.format_error(error="File Not Found",
                                     reason=f"ROOT file not found: {self.root_path}",
                                     suggestion="Run InspectFileTool to confirm the path")
        try:
            cfg = load_config(self.config, self.base_directory, self.overrides)
        except Exception as e:                                   # noqa: BLE001
            return self.format_error(error="Invalid Config", reason=str(e))

        res_id = self.resonance_pdgid if self.resonance_pdgid is not None \
            else get(cfg, "gen.resonance_pdgid")
        if res_id is None:
            return self.format_error(
                error="No Resonance",
                reason="neither resonance_pdgid nor config gen.resonance_pdgid is set",
                suggestion="Pass resonance_pdgid=32 for a dark photon, or a config with a gen block")

        tree = self.tree_name or cfg.get("tree", "Events")
        try:
            from coffea.nanoevents import NanoEventsFactory
            events = NanoEventsFactory.from_root(
                {src: tree}, schemaclass=build_schema(cfg)).events()
            if self.max_events is not None:
                events = events[: int(self.max_events)]
        except ImportError as e:
            return self.format_error(error="Missing Dependency", reason=str(e))
        except Exception as e:                                   # noqa: BLE001
            return self.format_error(error="Read Error", reason=str(e), context=self.root_path)

        try:
            vals = decay_lengths(
                events, int(res_id), get(cfg, "gen.lepton_pdgids", [11, 13]),
                get(cfg, "gen.mother_field", "genPartIdxMother"),
                get(cfg, "gen.vertex.vx", "vx"), get(cfg, "gen.vertex.vy", "vy"),
                one_per_resonance=self.one_per_resonance)
        except KeyError as e:
            return self.format_error(
                error="Missing Gen Vertex", reason=str(e),
                suggestion="The file has no gen vertex branches; Lxy cannot be computed from it")
        except Exception as e:                                   # noqa: BLE001
            return self.format_error(error="Computation Failed", reason=str(e))

        import numpy as np
        if vals.size == 0:
            return self.format_error(
                error="No Decays Found",
                reason=f"no resonance with |pdgId|={res_id} had the configured daughters",
                context=f"daughters={get(cfg, 'gen.lepton_pdgids', [11, 13])}",
                suggestion="Check the PDG id, or run InspectFileTool to see what the file holds")

        hi = self.hist_max if self.hist_max is not None else float(np.quantile(vals, 0.99))
        counts, edges = np.histogram(vals, bins=self.hist_bins, range=(0.0, max(hi, 1e-6)))
        q = np.quantile(vals, [0.5, 0.9, 0.99])
        out: Dict[str, Any] = {
            "schema": SCHEMA_VERSION, "status": "ok",
            "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "inputs": {"root_path": self.root_path, "resonance_pdgid": int(res_id),
                       "daughter_pdgids": get(cfg, "gen.lepton_pdgids", [11, 13]),
                       "one_per_resonance": self.one_per_resonance,
                       "max_events": self.max_events},
            "n_events": len(events),
            "definition": "|decay vertex - production vertex|_T  (resonance vx,vy -> daughter vx,vy)",
            "units": "cm",
            "statistics": {"n": int(vals.size), "mean": float(vals.mean()),
                           "median": float(q[0]), "q90": float(q[1]), "q99": float(q[2]),
                           "min": float(vals.min()), "max": float(vals.max())},
            "histogram": {"bins": edges.tolist(), "counts": counts.tolist()},
            "tool": "GenDecayLengthTool",
        }
        if self.output_path:
            dst = self._safe(self.output_path)
            if dst is None:
                return self.format_error(error="Access Denied",
                                         reason="output_path escapes base_directory")
            os.makedirs(os.path.dirname(dst) or self.base_directory, exist_ok=True)
            np.save(dst, vals)
            out["values_path"] = os.path.relpath(dst, self.base_directory)
        return json.dumps(out, separators=(",", ":"), ensure_ascii=False)
