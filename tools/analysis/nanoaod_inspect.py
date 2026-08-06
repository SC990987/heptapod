"""
# nanoaod_inspect.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

InspectFileTool -- open a (LLP)NanoAOD ROOT file and report its structure, and
(optionally) validate an analysis config against it.

Run this FIRST on any new file. It lists TTrees, NanoAOD-style collections, and
branches, and -- given a config -- reports exactly which collections/branches
the config references but the file does not have, together with the config path
to fix each. This makes user-supplied configs safe to run. Nothing here is
analysis-specific; the SIDM readiness check is just "validate configs/sidm.yaml".
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Any

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis.analysis_config import load_config, get, validate_config


# ---------------------------- pure parsing ---------------------------- #

def group_collections(branch_names: List[str]) -> Dict[str, Dict[str, Any]]:
    names = set(branch_names)
    counters = {n[1:] for n in names if n.startswith("n") and n[1:2].isupper()}
    collections: Dict[str, Dict[str, Any]] = {}
    for coll in sorted(counters):
        prefix = coll + "_"
        fields = sorted(n[len(prefix):] for n in names if n.startswith(prefix))
        if fields:
            collections[coll] = {"count_branch": "n" + coll, "n_fields": len(fields), "fields": fields}
    return collections


def flat_branches(branch_names, collections):
    grouped = set()
    for coll, info in collections.items():
        grouped.add(info["count_branch"])
        for f in info["fields"]:
            grouped.add(f"{coll}_{f}")
    return sorted(n for n in branch_names if n not in grouped)


def config_readiness(branch_names: List[str], collections: Dict[str, Any], cfg: dict) -> Dict[str, Any]:
    """Check every collection/branch the config references against the file."""
    names = set(branch_names)
    missing: List[dict] = []

    def check_coll(coll, where):
        if coll and coll not in collections:
            missing.append({"what": f"collection '{coll}'", "config": where})

    def check_branch(full, where):
        if full and full not in names:
            missing.append({"what": f"branch '{full}'", "config": where})

    for i, c in enumerate(cfg.get("constituents", [])):
        coll = c.get("collection"); nm = c.get("name", f"#{i}")
        check_coll(coll, f"constituents[{nm}].collection")
        idspec = c.get("id") or {}
        for k in ("field", "fallback_field"):
            if idspec.get(k):
                check_branch(f"{coll}_{idspec[k]}", f"constituents[{nm}].id.{k}")
        for vk, vv in (c.get("veto") or {}).items():
            check_branch(f"{coll}_{vv}", f"constituents[{nm}].veto.{vk}")
    for d in get(cfg, "leptonjet.displacement", []):
        for c in cfg.get("constituents", []):
            if c.get("name") == d.get("constituent"):
                check_branch(f"{c.get('collection')}_{d.get('field')}",
                             f"leptonjet.displacement[{d.get('constituent')}].field")
    jetc = get(cfg, "leptonjet.isolation.jet_collection", "Jet")
    check_coll(jetc, "leptonjet.isolation.jet_collection")
    for b in get(cfg, "leptonjet.isolation.lepton_fraction_branches", []):
        check_branch(f"{jetc}_{b}", "leptonjet.isolation.lepton_fraction_branches")
    n_trig = 0
    for c in get(cfg, "event_selection.cutflow", []):
        t = c.get("type"); nm = c.get("name")
        if t == "trigger":
            found = sum(1 for p in c.get("paths", []) if p in names)
            n_trig += found
            if c.get("paths") and found == 0:
                missing.append({"what": f"any trigger path for cut '{nm}'",
                                "config": f"event_selection.cutflow[{nm}].paths"})
        elif t == "flag" and c.get("branch") and c["branch"] not in names:
            missing.append({"what": f"branch '{c['branch']}'", "config": f"event_selection.cutflow[{nm}].branch"})
        elif t == "event_scalar" and c.get("branch") and c["branch"] not in names:
            missing.append({"what": f"branch '{c['branch']}'", "config": f"event_selection.cutflow[{nm}].branch"})
        elif t == "primary_vertex":
            fl = c.get("flag")
            has_flag = fl and fl in names
            has_pv = all(c.get(k) in names for k in ("ndof", "z") if c.get(k))
            if not has_flag and not (c.get("ndof") and has_pv):
                missing.append({"what": f"PV branches for cut '{nm}' (flag '{fl}' or PV_ndof/PV_z)",
                                "config": f"event_selection.cutflow[{nm}]"})
    if get(cfg, "gen.resonance_pdgid") is not None:
        check_coll(get(cfg, "collections.genpart", "GenPart"), "collections.genpart")

    return {"structural_issues": validate_config(cfg), "missing_in_file": missing,
            "n_triggers_found": n_trig, "ready": (len(missing) == 0)}


# ------------------------------- the tool ----------------------------- #

class InspectFileTool(BaseTool):
    """
    Inspect a (LLP)NanoAOD ROOT file and optionally validate an analysis config
    against it. Reports trees, collections, branch counts, and -- if `config` is
    given -- exactly which referenced collections/branches are missing (with the
    config path to fix each) plus any structural config issues. Run first.

    Inputs: root_path, config, overrides, tree_name, list_all_branches.
    """
    root_path: str = RuntimeField(description="Sandbox-relative path to the .root file")
    config: Optional[Any] = RuntimeField(default=None, description="Analysis config (YAML path or dict) to validate against the file")
    overrides: Optional[dict] = RuntimeField(default=None, description="Inline config overrides")
    tree_name: Optional[str] = RuntimeField(default=None, description="TTree to inspect (default 'Events')")
    list_all_branches: bool = RuntimeField(default=False, description="Include the full flat branch list in the output")

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

    def _run(self) -> str:
        src = self._safe_path(self.root_path)
        if not src:
            return self.format_error(error="Access Denied", reason="root_path escapes base_directory")
        if not os.path.exists(src):
            return self.format_error(error="File Not Found", reason=f"ROOT file not found: {self.root_path}")
        try:
            import uproot
        except ImportError:
            return self.format_error(error="Missing Dependency", reason="uproot is required", suggestion="pip install uproot")

        cfg = None
        if self.config is not None:
            try:
                cfg = load_config(self.config, self.base_directory, self.overrides)
            except Exception as e:
                return self.format_error(error="Invalid Config", reason=str(e))

        tree_name = self.tree_name or (cfg.get("tree", "Events") if cfg else "Events")
        try:
            fh = uproot.open(src)
            trees = {}
            for key in dict.fromkeys(k.split(";")[0] for k in fh.keys(cycle=False)):
                try:
                    obj = fh[key]
                    if hasattr(obj, "num_entries"):
                        trees[key] = int(obj.num_entries)
                except Exception:
                    continue
            if tree_name not in trees:
                return self.format_error(error="Tree Not Found",
                    reason=f"TTree '{tree_name}' not in file. Present: {sorted(trees)}")
            branch_names = [b.split(";")[0] for b in fh[tree_name].keys()]
        except Exception as e:
            return self.format_error(error="Read Error", reason=str(e))

        collections = group_collections(branch_names)
        flats = flat_branches(branch_names, collections)
        out: Dict[str, Any] = {
            "status": "ok", "root_path": self.root_path, "tree_inspected": tree_name,
            "trees": trees, "n_entries": trees[tree_name], "n_branches": len(branch_names),
            "n_collections": len(collections), "collections": collections,
            "n_flat_branches": len(flats),
        }
        if cfg is not None:
            out["config_readiness"] = config_readiness(branch_names, collections, cfg)
        if self.list_all_branches:
            out["flat_branches"] = flats
        return json.dumps(out, separators=(",", ":"), ensure_ascii=False)


SIDMInspectFileTool = InspectFileTool
