"""
# sidm_inspect.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

SIDMInspectFileTool -- open an LLPNanoAOD ROOT file and report its structure.

This is the *first* tool to run against a new file. LLPNanoAOD productions
differ in the exact names of the DSA-muon collection, per-muon pixel-hit and
per-electron lost-hit branches, PF-jet energy fractions, trigger paths, etc.
The downstream reconstruction/selection tools read those by name (see
`sidm_config.FieldMap`), so knowing the real names up front avoids silent
empty-selection failures.

The tool:
  * lists every TTree and its entry count,
  * groups branches into NanoAOD-style collections (`n<Name>` + `<Name>_*`),
  * cross-checks the branches the SIDM pipeline needs against what is present,
    and reports exactly which expected branches are MISSING (with the config
    key to override), plus which trigger paths from Table 13 were found.

All parsing logic is in module-level pure functions so it is unit-testable
without uproot or a real file.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Any

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis.sidm_config import SIDMConfig


# ===================================================================== #
# ======================= Pure parsing helpers ======================= #
# ===================================================================== #

def group_collections(branch_names: List[str]) -> Dict[str, Dict[str, Any]]:
    """Group flat NanoAOD branch names into collections.

    A collection `Foo` is recognised by a counter branch `nFoo` together with
    one or more `Foo_<field>` branches. Returns a mapping::

        {"Foo": {"count_branch": "nFoo", "fields": ["pt", "eta", ...]}}

    Branches that match no collection are returned by `flat_branches` in the
    caller. This mirrors how coffea's NanoAODSchema decides what to group.
    """
    names = set(branch_names)
    counters = {n[1:] for n in names if n.startswith("n") and n[1:2].isupper()}
    collections: Dict[str, Dict[str, Any]] = {}
    for coll in sorted(counters):
        prefix = coll + "_"
        fields = sorted(n[len(prefix):] for n in names if n.startswith(prefix))
        if fields:  # only a real collection if it has member branches
            collections[coll] = {
                "count_branch": "n" + coll,
                "n_fields": len(fields),
                "fields": fields,
            }
    return collections


def flat_branches(branch_names: List[str],
                  collections: Dict[str, Dict[str, Any]]) -> List[str]:
    """Branches that are not part of any grouped collection."""
    grouped = set()
    for coll, info in collections.items():
        grouped.add(info["count_branch"])
        for f in info["fields"]:
            grouped.add(f"{coll}_{f}")
    return sorted(n for n in branch_names if n not in grouped)


def sidm_readiness(branch_names: List[str],
                   collections: Dict[str, Dict[str, Any]],
                   cfg: SIDMConfig) -> Dict[str, Any]:
    """Check the file against everything the SIDM pipeline reads.

    Returns a report of found/missing collections and branches, each missing
    entry annotated with the `SIDMConfig` key used to override it.
    """
    names = set(branch_names)
    f = cfg.fields
    o = cfg.objects
    lj = cfg.leptonjets
    ev = cfg.events
    cc = cfg.crossclean

    def coll_present(c: str) -> bool:
        return c in collections

    def field_present(coll: str, fieldname: str) -> bool:
        return f"{coll}_{fieldname}" in names or fieldname in names

    report: Dict[str, Any] = {"collections": {}, "branches": {}, "missing": [],
                              "triggers": {}}

    # ---- required collections ----
    wanted_colls = {
        "electrons": f.electron_coll,
        "photons": f.photon_coll,
        "pf_muons": f.pfmuon_coll,
        "dsa_muons": f.dsamuon_coll,
        "pf_jets": f.jet_coll,
        "gen_particles": f.genpart_coll,
    }
    for role, coll in wanted_colls.items():
        present = coll_present(coll)
        report["collections"][role] = {"name": coll, "present": present}
        if not present:
            report["missing"].append({
                "what": f"collection '{coll}' ({role})",
                "override": f"fields.{role_to_cfg_key(role)}",
            })

    # ---- specific per-object branches the selections need ----
    branch_checks = [
        ("electron_id", o.electron_id_branch, "objects.electron_id_branch"),
        ("electron_lost_hits", lj.electron_lost_hits_branch,
         "leptonjets.electron_lost_hits_branch"),
        ("pfmuon_id", o.pfmuon_id_branch, "objects.pfmuon_id_branch"),
        ("pfmuon_pixel_hits", lj.pfmuon_pixel_hits_branch,
         "leptonjets.pfmuon_pixel_hits_branch"),
        ("dsamuon_displacedID", o.dsamuon_displacedid_branch,
         "objects.dsamuon_displacedid_branch"),
        ("jet_chEmEF", lj.jet_ch_em_frac_branch, "leptonjets.jet_ch_em_frac_branch"),
        ("jet_neEmEF", lj.jet_ne_em_frac_branch, "leptonjets.jet_ne_em_frac_branch"),
        ("jet_muEF", lj.jet_mu_frac_branch, "leptonjets.jet_mu_frac_branch"),
        ("pv_flag", ev.pv_flag_branch, "events.pv_flag_branch"),
    ]
    for role, branch, override in branch_checks:
        present = branch in names
        report["branches"][role] = {"name": branch, "present": present}
        if not present:
            report["missing"].append({"what": f"branch '{branch}' ({role})",
                                       "override": override})

    # ---- triggers (Table 13): OR list ----
    n_found = 0
    for path in ev.trigger_paths:
        present = path in names
        report["triggers"][path] = present
        n_found += int(present)
    report["n_triggers_found"] = n_found
    if n_found == 0:
        report["missing"].append({
            "what": "any analysis trigger path (Table 13)",
            "override": "events.trigger_paths",
        })

    report["ready"] = len(report["missing"]) == 0
    return report


def role_to_cfg_key(role: str) -> str:
    return {
        "electrons": "electron_coll", "photons": "photon_coll",
        "pf_muons": "pfmuon_coll", "dsa_muons": "dsamuon_coll",
        "pf_jets": "jet_coll", "gen_particles": "genpart_coll",
    }.get(role, role)


# ===================================================================== #
# ============================== The tool ============================= #
# ===================================================================== #

class SIDMInspectFileTool(BaseTool):
    """
    Inspect the structure of an LLPNanoAOD ROOT file for the SIDM analysis.

    Run this FIRST on any new file. It reports the TTrees, their entry counts,
    the NanoAOD-style object collections and their fields, and -- most
    usefully -- a readiness check that lists exactly which branches the SIDM
    reconstruction pipeline expects but cannot find, together with the
    `SIDMConfig` override key for each. Feed those overrides to the
    reconstruction/selection tools via their `field_overrides` argument.

    Inputs (runtime):
      - root_path: sandbox-relative path to the .root file.
      - tree_name: TTree to inspect (default 'Events').
      - field_overrides: optional nested dict of SIDMConfig overrides, so the
        readiness check reflects the branch names you intend to use.
      - list_all_branches: include the full flat branch list in the output
        (default False; collection grouping is always returned).

    Output (JSON): trees, entries, collections, sidm_readiness report.
    """
    root_path: str = RuntimeField(description="Sandbox-relative path to the LLPNanoAOD .root file")
    tree_name: Optional[str] = RuntimeField(default=None, description="TTree name to inspect (default 'Events')")
    field_overrides: Optional[dict] = RuntimeField(
        default=None,
        description="Nested SIDMConfig overrides (e.g. {'fields':{'dsamuon_coll':'DisplacedStandAloneMuon'}}) so the readiness check matches your intended branch names."
    )
    list_all_branches: bool = RuntimeField(default=False, description="Include the full flat branch list in the output")

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
            import uproot
        except ImportError:
            return self.format_error(
                error="Missing Dependency",
                reason="uproot is required to read ROOT files.",
                suggestion="Install the sidm bundle deps: pip install uproot awkward coffea fastjet hist",
            )

        try:
            cfg = SIDMConfig.from_overrides(self.field_overrides)
        except ValueError as e:
            return self.format_error(error="Invalid Config Override", reason=str(e))

        tree_name = self.tree_name or cfg.fields.tree_name
        try:
            fh = uproot.open(src)
            tree_keys = [k.split(";")[0] for k in fh.keys(cycle=False)]
            # Identify TTrees (objects that expose .num_entries).
            trees = {}
            for key in dict.fromkeys(tree_keys):
                try:
                    obj = fh[key]
                    if hasattr(obj, "num_entries"):
                        trees[key] = int(obj.num_entries)
                except Exception:
                    continue
            if tree_name not in trees:
                return self.format_error(
                    error="Tree Not Found",
                    reason=f"TTree '{tree_name}' not in file. Trees present: {sorted(trees)}",
                    suggestion="Pass tree_name explicitly.",
                )
            tree = fh[tree_name]
            branch_names = [b.split(";")[0] for b in tree.keys()]
        except Exception as e:
            return self.format_error(error="Read Error", reason=str(e))

        collections = group_collections(branch_names)
        flats = flat_branches(branch_names, collections)
        readiness = sidm_readiness(branch_names, collections, cfg)

        out: Dict[str, Any] = {
            "status": "ok",
            "root_path": self.root_path,
            "tree_inspected": tree_name,
            "trees": trees,
            "n_entries": trees[tree_name],
            "n_branches": len(branch_names),
            "n_collections": len(collections),
            "collections": collections,
            "n_flat_branches": len(flats),
            "sidm_readiness": readiness,
        }
        if self.list_all_branches:
            out["flat_branches"] = flats
        return json.dumps(out, separators=(",", ":"), ensure_ascii=False)
