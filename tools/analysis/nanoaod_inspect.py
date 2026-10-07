"""
# nanoaod_inspect.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

InspectFileTool -- open a NanoAOD-like ROOT file and report what is in it.
"""
import json
import os
from typing import Any, Dict, List, Optional

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis import nanoaod_layout


class InspectFileTool(BaseTool):
    """
    Inspect a NanoAOD-like ROOT file: its trees, its collections and branches, and how
    coffea's NanoAODSchema will read them.

    Run this FIRST on any unfamiliar file, before writing analysis code or scaffolding
    a framework. Branch names differ between NanoAOD versions, skims and private
    productions, and a wrong name is the most common cause of failure.

    What it reports:
      - every TTree and its number of entries;
      - the collections of the inspected tree, grouped the way coffea groups them
        (everything before the first underscore), with their kind: "jagged" (several
        objects per event, has an n<Collection> counter) or "record" (one set of
        values per event, like MET or HLT), and their fields;
      - `per_event_branches`: single branches such as run or genWeight;
      - `schema_notes`: things that change how the file must be read. By `issue`:
        "no_vector_behaviour" (a collection with pt/eta/phi that NanoAODSchema does
        not know: it comes back without delta_r, px or mass), "duplicate_momenta" and
        "conflicting_coordinates" (a collection that stores its momentum twice, or
        carries other fields coffea reads as a second set of coordinates),
        "missing_fields" (a collection coffea knows that lacks a field its behaviour
        needs, such as a vertex without x, y, z), and "shadowed_branches" (a branch
        named exactly like a collection, which hides the collection). The last four
        stop recent coffea versions (2026 and newer) unless dealt with; each note
        says how.

    The file must hold its events in a TTree. An RNTuple is reported as such and not
    described further: the grouping above does not apply to it.

    Field lists are truncated to `max_fields` per collection to keep the answer
    small. To see more, ask for one `collection` (all of its fields), or filter all
    branch names with `branch_pattern` (shell style, e.g. "HLT_*Mu*").
    Use `require` to verify that the branches or collections an analysis needs exist.

    Args:
        root_path: The ROOT file, relative to the working directory.
        tree_name: TTree to inspect (default "Events").
        collection: Return every field of this collection.
        branch_pattern: Return the branch names matching this shell-style pattern.
        require: Branch or collection names that must exist; reported as present or
            missing.
        max_fields: Fields listed per collection in the overview (default 12; 0 for all).
        list_all_branches: Also return the complete list of branch names.

    Returns (JSON):
        {"status": "ok", "root_path", "tree_inspected", "trees": {name: entries},
         "unreadable": {name: reason} (objects of the file that could not be opened;
         only when there are any), "n_entries", "n_branches", "n_collections",
         "collections": {name: {"kind", "n_fields", "counter", "fields"}},
         "per_event_branches": [...], "schema_notes": [{"collection", "issue", "detail"}],
         "collection_detail": {...}, "matching_branches": [...],
         "required": {"present": [...], "missing": [...]}, "all_branches": [...]}
        The last four appear only when asked for.

    Errors:
        A formatted error if the path leaves the working directory, the file or tree
        does not exist or cannot be read, the tree is an RNTuple, uproot is missing,
        or the collection asked for is not there.
    """

    # --------------------------- Runtime fields --------------------------- #
    root_path: str = RuntimeField(description="ROOT file to inspect, relative to the working directory")
    tree_name: str = RuntimeField(default="Events", description="TTree to inspect")
    collection: Optional[str] = RuntimeField(
        default=None, description="Return every field of this collection, e.g. 'Muon'")
    branch_pattern: Optional[str] = RuntimeField(
        default=None, description="Shell-style pattern of branch names to list, e.g. 'HLT_*Mu*'")
    require: Optional[List[str]] = RuntimeField(
        default=None, description="Branch or collection names that must exist")
    max_fields: int = RuntimeField(
        default=12, description="Fields listed per collection in the overview (0 for all)")
    list_all_branches: bool = RuntimeField(
        default=False, description="Also return the complete list of branch names")
    # ---------------------------------------------------------------------- #

    # ---------------------------- State fields ---------------------------- #
    base_directory: str = StateField(default=".", description="Base directory for safe paths")
    # ---------------------------------------------------------------------- #

    def _setup(self):
        self.base_directory = os.path.abspath(self.base_directory)
        if not os.path.exists(self.base_directory):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def _safe_path(self, rel: str) -> Optional[str]:
        if not rel:
            return None
        base = os.path.abspath(self.base_directory)
        full = os.path.abspath(os.path.join(base, rel))
        return full if full == base or full.startswith(base + os.sep) else None

    @staticmethod
    def describe(branches: List[str], collection: Optional[str] = None,
                 branch_pattern: Optional[str] = None, require: Optional[List[str]] = None,
                 max_fields: int = 12, list_all_branches: bool = False) -> Dict[str, Any]:
        """Build the report from a list of branch names (no file access; unit-testable)."""
        collections = nanoaod_layout.split_collections(branches)
        out: Dict[str, Any] = {
            "n_branches": len(branches),
            "n_collections": sum(1 for c in collections.values() if c["kind"] in ("jagged", "record")),
            "collections": nanoaod_layout.summarise(collections, max_fields=max(0, int(max_fields))),
            "per_event_branches": [name for name, info in collections.items()
                                   if info["kind"] in ("value", "jagged_value")],
            "schema_notes": nanoaod_layout.schema_notes(collections),
        }
        if collection:
            if collection not in collections:
                raise KeyError(collection)
            info = collections[collection]
            out["collection_detail"] = {
                "collection": collection, "kind": info["kind"], "counter": info["counter"],
                "n_fields": len(info["fields"]), "fields": info["fields"],
                # the list-valued traits are only worth showing when they say something
                **{k: v for k, v in nanoaod_layout.collection_traits(collection, info).items()
                   if v or k not in ("duplicate_momenta", "conflicts", "conflicting_fields",
                                     "missing_required")},
            }
            if info.get("shadowed"):
                out["collection_detail"]["shadowed_branches"] = [
                    f"{collection}_{field}" for field in info["shadowed"]]
        if branch_pattern:
            out["matching_branches"] = nanoaod_layout.match_branches(branches, branch_pattern)
        if require:
            out["required"] = nanoaod_layout.check_required(branches, require)
        if list_all_branches:
            out["all_branches"] = sorted(branches)
        return out

    def _run(self) -> str:
        src = self._safe_path(self.root_path)
        if not src:
            return self.format_error(
                error="Access Denied", reason="root_path escapes the working directory",
                context=f"root_path={self.root_path}",
                suggestion="Use a path relative to the working directory")
        if not os.path.exists(src):
            return self.format_error(
                error="File Not Found", reason="the ROOT file does not exist",
                context=f"root_path={self.root_path}",
                suggestion="Check the path; files outside the working directory must be copied or linked in")
        try:
            layout = nanoaod_layout.read_tree_layout(src, self.tree_name)
        except ImportError as exc:
            return self.format_error(
                error="Missing Dependency",
                reason=f"the ROOT file could not be read: {exc}",
                suggestion="Reading ROOT files needs uproot (and what it depends on); it is "
                           "part of the coffea and framework bundles")
        except KeyError as exc:
            return self.format_error(
                error="Tree Not Found", reason=str(exc.args[0]) if exc.args else str(exc),
                suggestion="Pass tree_name as one of the trees listed")
        except Exception as exc:
            return self.format_error(
                error="Read Error", reason=f"{type(exc).__name__}: {exc}",
                context=f"root_path={self.root_path}")
        if layout.get("kind") == "RNTuple":
            return self.format_error(
                error="Not A TTree",
                reason=f"'{self.tree_name}' is an RNTuple, which this tool does not describe",
                context=f"root_path={self.root_path}, trees={layout['trees']}",
                suggestion="Inspect a file that stores its events in a TTree. coffea 2026 and "
                           "newer can read NanoAOD RNTuples, but the branch grouping reported "
                           "here would not be what it does with one")

        try:
            report = self.describe(layout["branches"], self.collection, self.branch_pattern,
                                   self.require, self.max_fields, self.list_all_branches)
        except KeyError:
            names = sorted(nanoaod_layout.split_collections(layout["branches"]))
            return self.format_error(
                error="Collection Not Found",
                reason=f"the tree has no collection '{self.collection}'",
                context=f"collections: {names}",
                suggestion="Pick one of the collections listed")
        result = {
            "status": "ok", "root_path": self.root_path, "tree_inspected": layout["tree"],
            "trees": layout["trees"], "n_entries": layout["trees"][layout["tree"]], **report,
        }
        if layout.get("unreadable"):
            result["unreadable"] = layout["unreadable"]
        return json.dumps(result, separators=(",", ":"), ensure_ascii=False)
