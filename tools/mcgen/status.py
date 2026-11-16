"""
# status.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

MCProductionStatusTool -- where a production stands, and what to build on it.
"""
import json
import os
from pathlib import Path

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.framework._projects import safe_path
from tools.mcgen import _jobs as jobs_lib


class MCProductionStatusTool(BaseTool):
    """
    Report where a production stands: whether it is configured (or why configuring
    failed), the state of every job (the step it is at, done, failed with the end of the
    failing step's log; on HTCondor: idle, running, held, completed and the exit code),
    the events each step read and wrote, the output files, and the generator's numbers:
    the cross section before and after the generator filter and the filter efficiency,
    added up over the jobs that report them.

    Once NanoAOD files exist, `for_framework` holds what ScaffoldAnalysisFramework
    needs to build an analysis framework on them: `sample_file` (one of the files),
    `sample_locations` (the output directory as one sample, named after the
    production, in the run period, with its cross section) and `year`.

    Args:
        production: The production's directory, as PrepareMCProduction returned it.

    Returns (JSON):
        {"status": "ok", "production", "run_period", "final_tier", "output", "stage",
         "configured", "driver_running", "jobs": [{"job", "events", "state", ...}],
         "job_counts", "generator": {"generated", "passed_filter", "filter_efficiency",
         "cross_section_before_filter_pb", "cross_section_pb"}, "output_files",
         "n_output_files", "complete", "for_framework": {...}}
    """

    production: str = RuntimeField(description="Directory of the production (from PrepareMCProduction)")
    base_directory: str = StateField(default=".", description="Base directory for safe paths")

    def _setup(self):
        self.base_directory = os.path.abspath(self.base_directory)
        if not os.path.isdir(self.base_directory):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def _run(self) -> str:
        path = safe_path(self.base_directory, str(self.production or ""), follow_links=True)
        if path is None or not os.path.isfile(os.path.join(path, "production.json")):
            return self.format_error(error="Production Not Found",
                                     reason="no production.json in that directory",
                                     context=f"production={self.production}")
        try:
            report = jobs_lib.status(Path(path))
        except (OSError, ValueError) as exc:
            return self.format_error(error="Status Failed", reason=f"{type(exc).__name__}: {exc}",
                                     context=f"production={self.production}")
        return json.dumps({"status": "ok", **report}, ensure_ascii=False)
