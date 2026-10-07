"""
# components.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

AddFrameworkComponentTool -- add an optional part to a scaffolded analysis framework.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.framework import _components as components
from tools.framework import _projects as projects


class AddFrameworkComponentTool(BaseTool):
    """
    Add an optional component to an analysis framework made by ScaffoldAnalysisFramework.

    The scaffold is deliberately lean. Use this when the analysis needs one of:

      lepton_jets   Lepton jets: anti-kT clusters of leptons and photons, built per
                    channel from the selected objects and exposed as a derived object
                    with its own cuts, histograms and a two-lepton-jet selection.
                    options: name (default "ljs"), sources (list of object names, or
                    {name: {"mass": GeV}}; default the muons/electrons/photons of the
                    analysis), radius (0.4), carry ({field: fill} kept on constituents;
                    default {"charge": 0}), isolation_jets (name of the jet object used
                    for the isolation; default "jets" if that is a primary object of
                    the analysis, null for no isolation), invmass_max (upper edge of
                    the pair-mass histogram, GeV; default 1000).
                    Needs `fastjet` and `vector` in the analysis environment. The cut
                    values it writes are placeholders, like the scaffold's.

      scaleout      tools/scaleout.py with dask clients for local processes, an
                    existing scheduler, and HTCondor at the LPC (lpcjobqueue), the LPC
                    condor configuration, and a scale-out notebook. No options.

      schema        A customised tools/schema.py. options: mixins ({collection:
                    behaviour}, e.g. {"MyTrack": "PtEtaPhiMCollection"}),
                    cross_references ({index branch: target collection}), nested_items
                    ({new field: [index branches]}), hidden_branches ([branch]),
                    constant_fields ({"Coll_field": value}; coffea 2025.12 or newer,
                    lists of objects only), hide_duplicate_momenta (bool), reset
                    (true: forget the options in effect and start from these). Without
                    reset, options add to the ones already in the file's tables. The
                    file also gets `follow(objects, "<index>G", "<Collection>")`, which
                    goes through a cross-reference from a cut or an object definition.

      chain_report  tests/chain_report.py, tests/make_fixture.py and a GitHub workflow:
                    a regression report over small committed test files that compares
                    each pull request with its base. No options.

    It copies the component's files and, where the definitions and configs are
    involved, appends a block between the lines "# >>> component: NAME >>>" and
    "# <<< component: NAME <<<". Edit inside a block freely, but leave those two
    lines alone: they are how the block is found again.

    Calling it again:
      - with the same options (or none): nothing is changed; files and blocks that
        exist are kept, missing ones are restored.
      - lepton_jets with different options: refused unless overwrite=true, because
        the existing blocks would be kept and the new options silently ignored. The
        same goes for blocks that are in place with no record of their options.
      - overwrite=true regenerates the component's files and blocks from the options
        (those given now on top of those recorded before) and discards edits made
        inside them. tests/fixtures.yaml is never replaced.
      - schema: the tables at the top of tools/schema.py may be edited by hand and
        are what later calls add to. If the file was edited anywhere else it is kept
        unless overwrite=true; the answer then has "applied": false and the options
        given are not in effect.

    Args:
        project_dir: The framework's directory, relative to the working directory.
        component: One of lepton_jets, scaleout, schema, chain_report.
        options: Component options (see above).
        overwrite: Regenerate the component's existing files and blocks.

    Returns (JSON):
        {"status": "ok", "component", "package", "files_written": [...],
         "files_kept": [...], "blocks": {file: "added"|"replaced"|"kept"},
         "options": {...the options the component's files and blocks were written
         from...}, "notes": [...], "next_steps": [...]}
        schema adds "applied": true or false. Read `notes`: they say when something
        was kept instead of written. The tool does not let the recorded options and
        what it wrote drift apart; edits made by hand inside a block are not tracked.

    Errors:
        A formatted error if project_dir is not a scaffolded framework, the component
        or an option is unknown or has a value of the wrong kind, an option refers to
        an object the analysis does not define, a name the component would define is
        already in use, a block's marker lines are damaged, or a definitions or
        config file that has to be read does not parse. Nothing is changed in those
        cases, nor when writing fails part-way.
    """

    # --------------------------- Runtime fields --------------------------- #
    project_dir: str = RuntimeField(
        description="Directory of the analysis framework, relative to the working directory")
    component: str = RuntimeField(
        description="Component to add: lepton_jets, scaleout, schema or chain_report")
    options: Optional[Dict[str, Any]] = RuntimeField(
        default=None, description="Options of the component (see the tool description)")
    overwrite: bool = RuntimeField(
        default=False, description="Regenerate the component's existing files and blocks")
    # ---------------------------------------------------------------------- #

    # ---------------------------- State fields ---------------------------- #
    base_directory: str = StateField(default=".", description="Base directory for safe paths")
    # ---------------------------------------------------------------------- #

    def _setup(self):
        self.base_directory = os.path.abspath(self.base_directory)
        if not os.path.isdir(self.base_directory):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def _run(self) -> str:
        base = os.path.abspath(self.base_directory)
        project = projects.safe_path(base, self.project_dir, follow_links=True)
        if project is None:
            return self.format_error(
                error="Access Denied",
                reason="project_dir escapes the working directory (also when a link leads "
                       "out of it)",
                context=f"project_dir={self.project_dir}",
                suggestion="Use the relative path of the framework directory")
        if not os.path.isdir(project):
            return self.format_error(
                error="Project Not Found", reason="project_dir does not exist",
                context=f"project_dir={self.project_dir}",
                suggestion="Create the framework with ScaffoldAnalysisFramework first")
        try:
            result = components.apply_component(Path(project), self.component,
                                                self.options, self.overwrite)
        except components.ComponentError as exc:
            return self.format_error(
                error="Invalid Request", reason=str(exc),
                context=f"component={self.component}",
                suggestion=exc.suggestion or f"Components: {sorted(components.COMPONENTS)}")
        except Exception as exc:
            return self.format_error(
                error="Component Failed", reason=f"{type(exc).__name__}: {exc}",
                context=f"component={self.component}, project_dir={self.project_dir}")
        return json.dumps({"status": "ok", **result}, ensure_ascii=False)
