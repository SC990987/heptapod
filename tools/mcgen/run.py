"""
# run.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

RunMCProductionTool -- run a prepared production here, or submit it to HTCondor.
"""
import json
import os
from pathlib import Path
from typing import List, Optional

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.framework._projects import safe_path
from tools.mcgen import _jobs as jobs_lib
from tools.mcgen import _production as production


class RunMCProductionTool(BaseTool):
    """
    Run a production written by PrepareMCProduction: here, one job after another, or on
    HTCondor at the Fermilab LPC, one batch job per job of the production.

    It returns at once. A driver runs in the background: it first configures the
    production, once (sets up the CMSSW releases, builds the fragment, writes every
    step's configuration with cmsDriver.py; a few minutes), then runs the jobs here or
    submits them. Follow it with MCProductionStatus.

    where="local" runs on this machine, in an el8 container. Meant for tests and small
    samples. test_events makes each job generate only that many events, enough for a
    few to pass the generator filter (5 without one, about 5 / efficiency with one), to
    show in a few minutes per step that the whole chain works; a test keeps its files in
    the job's directory (jobs/job_NNNN) and does not count as the job. Outputs of a real
    run go to the production's output, a local directory or a root:// url.

    where="condor" submits the jobs (all, or those in `jobs`) to HTCondor, each in an
    EL8 slot, which writes its outputs to the production's root:// output. It needs a
    grid proxy valid for at least 12 hours, and an output that is a root:// url. It
    uses the batch system and writes to storage: confirm the number of jobs and the
    output with the user first.

    A production with premixed pileup needs a valid grid proxy wherever it runs.

    Args:
        production: The production's directory, as PrepareMCProduction returned it.
        where: "local" (default) or "condor".
        jobs: Job numbers to run (default all). Locally, [0] runs the first job only.
        test_events: Local runs only: events each job generates, instead of the
            production's, for a test whose files stay in the job's directory.

    Returns (JSON):
        {"status": "ok", "production", "where", "jobs", "driver_pid", "configured",
         "test_events", "log", "next_steps": [...]}

    Errors:
        A formatted error if the production does not exist, a driver of it is still
        running, a job number does not exist, the grid proxy is missing or too short,
        or a condor production has no root:// output.
    """

    # --------------------------- Runtime fields --------------------------- #
    production: str = RuntimeField(description="Directory of the production (from PrepareMCProduction)")
    where: str = RuntimeField(default="local", description="'local' or 'condor'")
    jobs: Optional[List[int]] = RuntimeField(default=None, description="Job numbers to run (default all)")
    test_events: Optional[int] = RuntimeField(
        default=None, description="Local only: events each job generates, for a test kept out of the output")
    # ---------------------------------------------------------------------- #

    # ---------------------------- State fields ---------------------------- #
    base_directory: str = StateField(default=".", description="Base directory for safe paths")
    # ---------------------------------------------------------------------- #

    def _setup(self):
        self.base_directory = os.path.abspath(self.base_directory)
        if not os.path.isdir(self.base_directory):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def _run(self) -> str:
        base = self.base_directory
        path = safe_path(base, str(self.production or ""), follow_links=True)
        if path is None or not os.path.isfile(os.path.join(path, "production.json")):
            return self.format_error(error="Production Not Found",
                                     reason="no production.json in that directory",
                                     context=f"production={self.production}",
                                     suggestion="Prepare one with PrepareMCProduction")
        directory = Path(path)
        d = json.loads((directory / "production.json").read_text())
        if self.where not in ("local", "condor"):
            return self.format_error(error="Invalid Request", reason="where must be 'local' or 'condor'",
                                     context=f"where={self.where}")
        n = len(d["jobs"])
        selected = list(range(n)) if self.jobs is None else self.jobs
        if not isinstance(selected, list) or not selected or any(
                isinstance(j, bool) or not isinstance(j, int) or not 0 <= j < n for j in selected):
            return self.format_error(error="Invalid Request",
                                     reason=f"jobs must be job numbers from 0 to {n - 1}",
                                     context=f"jobs={self.jobs}")
        selected = sorted(set(selected))
        test = self.test_events
        if test is not None:
            if self.where != "local":
                return self.format_error(error="Invalid Request", reason="test_events is for local runs",
                                         suggestion="Submit the production's own events to condor")
            if isinstance(test, bool) or not isinstance(test, int) or test < 1:
                return self.format_error(error="Invalid Request",
                                         reason="test_events must be a positive whole number")
        running = jobs_lib.driver_pid(directory)
        if running:
            return self.format_error(error="Already Running",
                                     reason=f"a driver of this production is running (pid {running})",
                                     suggestion="Follow it with MCProductionStatus")
        needs_proxy = d["pileup"] == "premix" or self.where == "condor"
        proxy = None
        if needs_proxy:
            proxy = jobs_lib.proxy(12.0 if self.where == "condor" else 2.0)
            if not proxy.get("valid"):
                return self.format_error(
                    error="No Usable Grid Proxy", reason=proxy.get("why", "no proxy"),
                    context=("premixed pileup is read over XRootD" if d["pileup"] == "premix" else "")
                            + ("; condor jobs write to the output with it" if self.where == "condor" else ""),
                    suggestion="The user makes one with: voms-proxy-init -voms cms -valid 192:00")
        if self.where == "condor":
            if not d["output"].startswith("root://"):
                return self.format_error(
                    error="Invalid Output", reason="batch jobs can only write to a root:// url",
                    context=f"output={d['output']}",
                    suggestion="Prepare the production again with output='root://cmseos.fnal.gov//store/user/<you>/...'")
            (directory / "condor" / "job.sub").write_text(
                production.condor_submit_file(directory, d, proxy["path"], selected))
        pid = jobs_lib.launch(directory, self.where, selected, test)
        relative = os.path.relpath(path, base)
        return json.dumps({
            "status": "ok", "production": relative, "where": self.where, "jobs": selected,
            "driver_pid": pid, "configured": (directory / "cfg" / ".configured").exists(),
            "test_events": test, "log": os.path.join(relative, "driver.log"),
            "proxy_hours_left": (proxy or {}).get("hours_left"),
            "next_steps": [f"MCProductionStatus(production='{relative}') in a few minutes: configuring "
                           "takes a few minutes the first time, and each step of a job some more"],
        }, ensure_ascii=False)
