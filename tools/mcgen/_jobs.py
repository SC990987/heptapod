"""
# _jobs.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Running a production, and reading where it stands.

``launch`` starts bin/driver.sh in a session of its own and returns at once: the
driver configures the production (once), then runs its jobs one after another here,
or submits them to HTCondor. ``status`` reads what the driver and the jobs wrote
(driver.status, jobs/*/status, the summaries, and for submitted jobs HTCondor's job
log), lists the outputs, and adds up the generator's cross section and filter efficiency.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# HTCondor's job log has an event per entry, "NNN (cluster.proc.subproc) date time what";
# the events that change a job's state, and the state they leave it in
_LOG_EVENT = re.compile(r"^(\d{3}) \((\d+)\.(\d+)\.\d+\) ")
_LOG_STATES = {"000": "idle", "001": "running", "004": "idle", "005": "completed",
               "007": "idle", "009": "removed", "010": "suspended", "011": "running",
               "012": "held", "013": "idle"}
_RETURN_VALUE = re.compile(r"Normal termination \(return value (-?\d+)\)")
# GenXSecAnalyzer prints "Before Filter" for events Pythia generates, and "After matching"
# for events read from a gridpack; "After filter" is the sample's cross section in both
_XSEC_BEFORE = re.compile(r"(?:Before Filter|After matching): total cross section = "
                          r"([0-9.eE+-]+) \+- ([0-9.eE+-]+) pb")
_XSEC_FINAL = re.compile(r"After filter: final cross section = ([0-9.eE+-]+) \+- ([0-9.eE+-]+) pb")
_FILTER = re.compile(r"Filter efficiency \(event-level\)\s*=\s*\(([0-9.eE+-]+)\) / \(([0-9.eE+-]+)\)")
_TRIG = re.compile(r"^(\S+): TrigReport Events total = (\d+) passed = (\d+)")
_JOB_LINE = re.compile(r"^job (\d+) events (\d+)( test)?\s*$")


def _run(args: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, check=False)


def proxy(min_hours: float = 2.0) -> Dict[str, Any]:
    """Whether a grid proxy with the cms VO is valid for at least ``min_hours``."""
    tool = shutil.which("voms-proxy-info")
    if tool is None:
        return {"valid": False, "why": "voms-proxy-info is not on PATH"}
    try:
        left = _run([tool, "--actimeleft"])
        path = _run([tool, "--path"])
        vo = _run([tool, "--vo"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"valid": False, "why": f"voms-proxy-info failed: {exc}"}
    if left.returncode != 0 or not left.stdout.strip().isdigit():
        return {"valid": False, "why": "no grid proxy with VOMS attributes "
                                       "(voms-proxy-init -voms cms makes one)"}
    hours = int(left.stdout.strip()) / 3600
    info = {"path": path.stdout.strip() or None, "hours_left": round(hours, 1),
            "vo": vo.stdout.strip() or None}
    if info["vo"] != "cms":
        return {**info, "valid": False, "why": f"the proxy's VO is {info['vo']!r}, not cms"}
    if hours < min_hours:
        return {**info, "valid": False, "why": f"the proxy has {hours:.1f} h left, "
                                               f"less than {min_hours} h"}
    return {**info, "valid": True}


def driver_pid(directory: Path) -> Optional[int]:
    """The pid of a driver of this production that is still running, else None."""
    try:
        pid = int((directory / "driver.pid").read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    try:   # a pid reused by another program does not count
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        if "driver.sh" not in command:
            return None
    except OSError:
        pass
    return pid


def launch(directory: Path, mode: str, jobs: List[int],
           test_events: Optional[int] = None) -> int:
    """Start bin/driver.sh in the background; returns its pid."""
    env = dict(os.environ)
    env.pop("MCGEN_TEST_EVENTS", None)
    if test_events:
        env["MCGEN_TEST_EVENTS"] = str(test_events)
    args = ["bash", str(directory / "bin" / "driver.sh"), mode] + [str(job) for job in jobs]
    with open(directory / "driver.log", "ab") as log:
        process = subprocess.Popen(args, cwd=str(directory), stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    (directory / "driver.pid").write_text(f"{process.pid}\n")
    return process.pid


def _events(path: Path) -> List[List[str]]:
    try:
        return [line.split() for line in path.read_text().splitlines() if line.strip()]
    except OSError:
        return []


def _job_state(events: List[List[str]]) -> Dict[str, Any]:
    """The state of one job from its status lines (time, then words)."""
    if not events:
        return {"state": "pending"}
    words = [e[1:] for e in events]
    if words[-1][:1] == ["DONE"]:
        return {"state": "done"}
    failed = next((w for w in words if w[:1] == ["FAILED"]), None)
    if failed:
        return {"state": "failed", "error": " ".join(failed[1:])}
    steps = [w for w in words if w[:1] == ["STEP"]]
    if steps:
        last = steps[-1]
        return {"state": "running", "step": last[2] if len(last) > 2 else None,
                "step_state": last[3] if len(last) > 3 else None}
    return {"state": "running"}


def _summary(text: str) -> Dict[str, Any]:
    """Event counts per step, the generator's numbers, the outputs, from a job summary."""
    out: Dict[str, Any] = {"steps": {}, "outputs": []}
    for line in text.splitlines():
        job_line = _JOB_LINE.match(line)
        if job_line:
            out["events"] = int(job_line.group(2))
            out["test"] = bool(job_line.group(3))
        trig = _TRIG.match(line)
        if trig:
            out["steps"][trig.group(1)] = {"events_in": int(trig.group(2)),
                                           "events_out": int(trig.group(3))}
        before = _XSEC_BEFORE.search(line)
        if before:
            out["xsec_pb"] = float(before.group(1))
            out["xsec_error_pb"] = float(before.group(2))
        final = _XSEC_FINAL.search(line)
        if final:
            out["final_xsec_pb"] = float(final.group(1))
        filtered = _FILTER.search(line)
        if filtered:
            out["filter_passed"] = float(filtered.group(1))
            out["filter_total"] = float(filtered.group(2))
        if line.startswith("output "):
            parts = line.split(None, 2)
            if len(parts) == 3:
                out["outputs"].append({"tier": parts[1], "file": parts[2]})
    return out


def generator_numbers(summaries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The cross section and filter efficiency over all jobs that report them."""
    reporting = [s for s in summaries if "filter_total" in s and s["filter_total"] > 0]
    if not reporting:
        return None
    passed = sum(s["filter_passed"] for s in reporting)
    total = sum(s["filter_total"] for s in reporting)
    out: Dict[str, Any] = {"generated": int(total), "passed_filter": int(passed),
                           "filter_efficiency": passed / total, "jobs": len(reporting)}
    for key, name in (("xsec_pb", "cross_section_before_filter_pb"),
                      ("final_xsec_pb", "cross_section_pb")):
        with_xsec = [s for s in reporting if key in s]
        if with_xsec:   # each job's estimate, weighted by the events it generated
            weight = sum(s["filter_total"] for s in with_xsec)
            out[name] = sum(s[key] * s["filter_total"] for s in with_xsec) / weight
    return out


def _submissions(directory: Path) -> Dict[int, List[int]]:
    """{cluster: its jobs, in the order of its procs}, for every submission, in order."""
    condor = directory / "condor"
    out: Dict[int, List[int]] = {}
    try:        # the driver's record: a line per submission, the cluster then its jobs
        for line in (condor / "clusters").read_text().splitlines():
            parts = line.split()
            if parts and all(part.isdigit() for part in parts):
                out[int(parts[0])] = [int(part) for part in parts[1:]]
    except OSError:
        pass
    if not out:     # no record: the last submission, from the cluster and the submit file
        try:
            cluster = (condor / "cluster").read_text().strip()
            queue = re.search(r"^queue Job in \(([^)]*)\)", (condor / "job.sub").read_text(), re.M)
        except OSError:
            return out
        if cluster.isdigit() and queue:
            out[int(cluster)] = [int(job) for job in queue.group(1).replace(",", " ").split()]
    return out


def _job_log(path: Path) -> Dict[Tuple[int, int], Dict[str, Any]]:
    """{(cluster, proc): {"state", and "exit_code" or "hold_reason"}} from HTCondor's job log."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return {}
    states: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for i, line in enumerate(lines):
        event = _LOG_EVENT.match(line)
        if not event or event.group(1) not in _LOG_STATES:
            continue
        entry: Dict[str, Any] = {"state": _LOG_STATES[event.group(1)]}
        following = lines[i + 1] if i + 1 < len(lines) else ""
        if event.group(1) == "005":
            code = _RETURN_VALUE.search(following)
            entry["exit_code"] = int(code.group(1)) if code else None
        elif event.group(1) == "012":
            entry["hold_reason"] = following.strip()[:300]
        states[(int(event.group(2)), int(event.group(3)))] = entry
    return states


def _condor(directory: Path) -> Tuple[Optional[int], Dict[int, Dict[str, Any]], Optional[str]]:
    """The last cluster, {job: {"state", ...}}, and a problem in reading, if any.

    HTCondor writes what happens to every job of every submission to the job log,
    condor/logs/condor.log, so no schedd is asked (at the LPC, condor_history must be
    pointed at the schedd that ran the job). A job submitted again takes the state of
    its last submission.
    """
    submissions = _submissions(directory)
    if not submissions:
        return None, {}, None
    log = directory / "condor" / "logs" / "condor.log"
    events = _job_log(log)
    states: Dict[int, Dict[str, Any]] = {}
    for cluster, jobs in submissions.items():
        for proc, job in enumerate(jobs):
            states[job] = events.get((cluster, proc), {"state": "submitted"})
    problem = None if log.exists() else "HTCondor's job log, condor/logs/condor.log, is missing"
    return list(submissions)[-1], states, problem


def list_outputs(output: str, tier: str) -> Tuple[List[str], Optional[str]]:
    """The ROOT files of one tier in the output, and a problem in listing, if any."""
    where = f"{output.rstrip('/')}/{tier}"
    if where.startswith("root://"):
        rest = where[len("root://"):]
        host, _, path = rest.partition("/")
        path = "/" + re.sub("/+", "/", path.lstrip("/"))
        if shutil.which("xrdfs") is None:
            return [], "xrdfs is not on PATH"
        try:
            result = _run(["xrdfs", "root://" + host, "ls", path], timeout=60)
        except subprocess.TimeoutExpired:
            return [], "listing the output took too long"
        if result.returncode != 0:
            message = " ".join((result.stderr or "").split())[:200]
            return [], None if "No such file" in message else message or None
        return sorted(f"root://{host}/{line.strip()}" for line in result.stdout.splitlines()
                      if line.strip().endswith(".root")), None
    try:
        return sorted(str(Path(where) / name) for name in os.listdir(where) if name.endswith(".root")), None
    except FileNotFoundError:
        return [], None
    except OSError as exc:
        return [], str(exc)


def status(directory: Path) -> Dict[str, Any]:
    """Where a production stands: the driver, every job, the outputs, the generator."""
    d = json.loads((directory / "production.json").read_text())
    driver = [e[1:] for e in _events(directory / "driver.status")]
    pid = driver_pid(directory)
    configured = (directory / "cfg" / ".configured").exists()
    stage = "prepared"
    for words in driver:
        if words[:2] == ["CONFIGURE", "start"]:
            stage = "configuring"
        elif words[:2] == ["CONFIGURE", "failed"]:
            stage = "configure failed"
        elif words[:2] == ["CONFIGURE", "done"] or (words[:1] == ["DRIVER"] and configured
                                                     and stage == "prepared"):
            stage = "configured"
        elif words[:2] == ["SUBMIT", "done"]:
            stage = "submitted"
        elif words[:2] == ["SUBMIT", "failed"]:
            stage = "submit failed"
    report: Dict[str, Any] = {"production": d["name"], "run_period": d["run_period"],
                              "final_tier": d["final_tier"], "output": d["output"],
                              "configured": configured, "stage": stage,
                              "driver_running": pid is not None}
    if stage == "configure failed":
        try:
            report["configure_error"] = (directory / "cfg" / ".failed").read_text().strip()
        except OSError:
            pass
        log = directory / "configure.log"
        if log.exists():
            report["configure_log_tail"] = log.read_text(errors="replace").splitlines()[-25:]
    cluster, condor_states, condor_problem = _condor(directory)
    if cluster:
        report["condor_cluster"] = cluster
    if condor_problem:
        report["condor_problem"] = condor_problem
    jobs, summaries = [], []
    for job in range(len(d["jobs"])):
        local = directory / "jobs" / f"job_{job:04d}"
        entry: Dict[str, Any] = {"job": job, "events": d["jobs"][job]}
        summary_path = directory / "condor" / "out" / f"summary_{job}.txt"
        if job in condor_states:
            entry.update(condor_states[job])
        elif local.exists():
            entry.update(_job_state(_events(local / "status")))
            summary_path = local / "summary.txt"
        else:
            entry["state"] = "not submitted" if cluster else "not started"
        if summary_path.exists():
            text = summary_path.read_text(errors="replace")
            parsed = _summary(text)
            entry["events_per_step"] = parsed["steps"]
            if parsed.get("test"):      # a test run is not the job: its files are not in the output
                entry["test_events"] = parsed.get("events")
                if entry.get("state") == "done":
                    entry["state"] = "test done"
            else:
                summaries.append(parsed)
                if "DONE" in text.split():
                    entry["finished"] = True
            failure = [line for line in text.splitlines() if line.startswith("FAILED")]
            if failure:
                entry["error"] = failure[0][len("FAILED "):]
                entry["log_tail"] = [line for line in text.splitlines() if " log: " in line][-15:]
        jobs.append(entry)
    report["jobs"] = jobs
    counts: Dict[str, int] = {}
    for entry in jobs:
        key = "done" if entry.get("finished") else entry.get("state", "?")
        counts[key] = counts.get(key, 0) + 1
    report["job_counts"] = counts
    generator = generator_numbers(summaries)
    if generator:
        report["generator"] = generator
    files, listing_problem = list_outputs(d["output"], d["final_tier"])
    report["output_files"] = files[:20] + ([f"... and {len(files) - 20} more"] if len(files) > 20 else [])
    report["n_output_files"] = len(files)
    if listing_problem:
        report["output_listing_problem"] = listing_problem
    finished = sum(1 for entry in jobs if entry.get("finished"))
    report["complete"] = finished == len(jobs) and len(files) >= len(jobs)
    if files and d["final_tier"] == "NANOAOD":
        xsec = (generator or {}).get("cross_section_pb")
        location = {"directory": f"{d['output'].rstrip('/')}/NANOAOD/", "name": d["name"],
                    "year": d["run_period"], "is_data": False, "config": f"{d['name']}.yaml",
                    "tag": "mcgen", "comment": f"{d['name']}: {d['campaign']} simulation made with "
                                               f"HEPTAPOD's mcgen tools, pileup {d['pileup']}"}
        if xsec:
            location["xsec"] = float(f"{xsec:.4g}")       # the sample's cross section, in pb
        report["for_framework"] = {
            "sample_file": files[0],
            "sample_locations": [location],
            "year": d["run_period"],
            "cross_section_pb": xsec,
            "note": ("pass sample_file, sample_locations and year to ScaffoldAnalysisFramework: the "
                     "sample comes with its cross section (generator level, after the filter); the "
                     "run period's luminosity is still the user's to give"
                     + ("" if report["complete"] else "; not every job has finished yet")),
        }
    return report
