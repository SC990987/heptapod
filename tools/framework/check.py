"""
# check.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

CheckAnalysisFrameworkTool -- run a scaffolded analysis framework's own self-check.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Union

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.framework import _projects as projects

MAX_LISTED = 60
NEEDED_MODULES = ("coffea", "awkward", "uproot", "hist")


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join((text or "").strip().splitlines()[-lines:])


def _capped(items, limit=MAX_LISTED):
    items = list(items)
    if len(items) <= limit:
        return items
    return items[:limit] + [f"... and {len(items) - limit} more"]


def _yield(value) -> float:
    """A weighted yield with six significant digits: small ones must not round to zero."""
    return float(f"{float(value):.6g}")


def option_like(values) -> List[str]:
    """The values that a command line would read as options instead of as names."""
    return [str(value) for value in values if value is not None and str(value).startswith("-")]


def build_command(python: str, package: str, report_path: str, *, sample: Optional[str] = None,
                  sample_file: Optional[str] = None, is_data: Optional[bool] = None,
                  year: Optional[str] = None, channels: Optional[List[str]] = None,
                  hist_collections: Optional[List[str]] = None, max_events: int = 2000,
                  strict: bool = True, tag: Optional[str] = None,
                  sample_config: Optional[str] = None) -> List[str]:
    """The command line that runs the framework's self-check.

    Names are passed on as they are, so the caller must have refused any that start
    with a dash (see option_like): the checker would take them for options.
    """
    cmd = [python, "-m", f"{package}.tools.check", "--json", report_path,
           "--max-events", str(int(max_events))]
    if sample:
        cmd += ["--sample", sample]
        if tag:
            cmd += ["--tag", str(tag)]
        if sample_config:
            cmd += ["--location-cfg", str(sample_config)]
    elif sample_file:
        dataset = Path(sample_file).name
        if dataset.endswith(".root"):
            dataset = dataset[: -len(".root")]
        cmd += ["--file", sample_file, "--dataset", dataset.lstrip("-") or "check"]
        if is_data is True:
            cmd.append("--data")
        elif is_data is False:
            cmd.append("--mc")
        if year:
            cmd += ["--year", str(year)]
    if channels:
        cmd += ["--channels", *channels]
    if hist_collections is not None:
        cmd += ["--hists", *hist_collections]
    if not strict:
        cmd.append("--no-strict")
    return cmd


def missing_modules(report: dict) -> List[str]:
    """The libraries the checker's interpreter could not import, from its report."""
    versions = report.get("versions") or {}
    return [name for name in NEEDED_MODULES if name in versions and versions[name] is None]


def condense(report: dict) -> dict:
    """Trim the framework's full report to what an agent needs to act on."""
    static = report.get("static") or {}
    inventory = static.get("inventory") or {}
    unused = inventory.get("unused") or {}
    out = {
        "ok": bool(report.get("ok")),
        "versions": report.get("versions"),
        "static": {
            "errors": _capped(static.get("errors") or []),
            "warnings": _capped(static.get("warnings") or []),
            "primary_objects": inventory.get("primary_objects"),
            "derived_objects": inventory.get("derived_objects"),
            "optional_objects": inventory.get("optional_objects"),
            "channels": inventory.get("channels"),
            "hist_collections": inventory.get("hist_collections"),
            "n_hists": inventory.get("n_hists"),
            "samples": sorted(inventory.get("samples") or {}),
            "n_unused": {key: len(value) for key, value in unused.items()},
        },
    }
    run = report.get("run")
    if run is not None:
        condensed = {"ok": bool(run.get("ok")), "seconds": run.get("seconds"), "datasets": {}}
        if run.get("error"):
            # the chain of errors, outermost first: the line naming the failing cut or
            # object is among them whichever coffea version wrapped it
            condensed["error"] = str(run["error"])[:3000]
            if run.get("traceback"):
                condensed["traceback"] = _tail(run["traceback"], 20)
        for dataset, info in (run.get("datasets") or {}).items():
            condensed["datasets"][dataset] = {
                "n_events": info.get("n_events"),
                "is_data": info.get("is_data"),
                "year": info.get("year"),
                # the inputs of the normalisation: weighted yields are scaled by
                # lumixs_weight = lumi * xs / scaled_sum_weights (null: not scaled)
                "scaled_sum_weights": info.get("scaled_sum_weights"),
                "lumixs_weight": info.get("lumixs_weight"),
                "n_removed_golden_json": info.get("n_removed_golden_json"),
                "unavailable_objects": info.get("unavailable_objects") or [],
                "cutflow": {channel: [[row["cut"], row["raw"], _yield(row["weighted"])]
                                      + (["not applied"] if row.get("not_applied") else [])
                                      for row in rows]
                            for channel, rows in (info.get("cutflow") or {}).items()},
                "counters": info.get("counters") or {},
                "n_hists": info.get("n_hists"),
                "empty_hists": _capped(info.get("empty_hists") or []),
                "warnings": _capped(info.get("warnings") or []),
            }
        out["run"] = condensed
    return out


class CheckAnalysisFrameworkTool(BaseTool):
    """
    Check an analysis framework made by ScaffoldAnalysisFramework: are its configs and
    definitions consistent, and does it run?

    Runs the framework's own checker (`python -m <package>.tools.check`) and returns
    its report. Two levels:

      static  (always) every channel in selections.yaml uses objects and cuts that
              exist, every histogram collection lists histograms that exist, axis and
              object names are legal, samples have files and cross sections, golden
              JSONs are in place. No event data is read.
      run     (when `sample` or `sample_file` is given) processes one chunk of
              about `max_events` events from the start of the file through the chosen
              channels and histogram collections, and reports each cutflow, the
              histograms left empty, and every warning the processor recorded.

    Call it after scaffolding, and again after EVERY edit to definitions/ or configs/.

    How to read the answer:
      - "status": "ok" only says the checker ran. The verdict is `ok` (true: no error
        found). `ok: true` is not "nothing to report": read the warnings.
      - `static.errors` first: nothing runs until they are fixed.
      - `static.warnings` about a missing cross section or luminosity are expected
        until those are configured, and so is the run warning that data has no
        golden JSON configured. Report them; they are not bugs.
      - `run.error` (with `run.traceback`) when the run stopped: the chain of
        errors, outermost first. One of its lines names the cut, object or weight
        that failed and the file to fix.
      - per dataset, `warnings`: "... is not available in this sample" means a cut,
        histogram or derived object was skipped because an object it needs is absent
        there: one listed in `optional_objs` (generator-level quantities on data)
        or, with strict=false, one that failed to build. Anything else deserves a
        look. `unavailable_objects` lists the objects that could not be built.
      - a cutflow row ending in "not applied" was skipped. An unexpected zero in a
        cutflow, or a histogram in `empty_hists` that should have entries, usually
        means a cut is tighter than meant.
      - weighted yields are scaled to lumi * cross section when `lumixs_weight` is
        a number; when it is null they are sums of generator weights.
      - lists are cut off after 60 entries ("... and N more").

    With strict=true (default) the run stops, naming the culprit, when an object
    that is not optional cannot be built, or when a cut or `object_weight` fails for
    any reason other than an optional object being absent. strict=false turns those
    into warnings so that one failure does not hide the rest. In both modes a
    histogram or counter that cannot be filled is skipped with a warning, and a
    failing `generator_weight` or `event_weight`, or a golden JSON that is configured
    but cannot be read, stops the run.

    The check needs coffea, awkward, uproot and hist. It runs with, in this order:
    `venv` if given; the interpreter configured as `analysis_python` (set it with
    `tb config set --user heptapod analysis_python /path/to/python`); the toolkit's
    own python, for which the framework bundle installs them.

    Time: by default the check is given 50 s, because the tool server (toolbase)
    cuts every call at 60 s unless it was started with `tb serve --call-timeout`.
    If it does not finish, lower `max_events` or name fewer `channels` and
    `hist_collections`. The first run in a new environment is the slowest (python
    is still caching the libraries): trying once more is worthwhile.

    Args:
        project_dir: The framework's directory, relative to the working directory.
        sample: Name of a configured sample to run on (its first file).
        tag: Group of the sample config the sample belongs to. Only needed when the
            same sample name is used in two groups.
        sample_config: Name of the file under configs/samples/ that defines the
            sample (default "samples.yaml"): a file name, not a path.
        sample_file: Or a ROOT file to run on, relative to the working directory.
        is_data: Whether sample_file is data (default: decided from the file).
        year: Run period of sample_file.
        channels: Channels to run (default: all).
        hist_collections: Histogram collections to fill (default: all; [] for none).
        max_events: Roughly how many events to process (default 2000). coffea evens
            out chunk sizes, so the one chunk that is read can be up to half as large
            again.
        strict: Stop at the first failure (default true).
        venv: Virtual environment to run with, relative to the working directory,
            e.g. "my_analysis/.venv".
        timeout_s: Give up after this many seconds (default 50; more only helps if
            the tool server allows longer calls).

    Returns (JSON):
        {"status": "ok", "ok": <bool: no errors found>, "command": "...",
         "python": "<interpreter used>", "versions": {"coffea", "awkward", ...},
         "static": {"errors": [...], "warnings": [...], "channels": [...],
                    "primary_objects", "derived_objects", "optional_objects", ...},
         "run": {"ok": <bool>, "error", "traceback",
                 "datasets": {name: {"n_events", "is_data", "year",
                 "scaled_sum_weights", "lumixs_weight", "cutflow": {channel:
                 [[cut, raw, weighted], ...]}, "counters", "empty_hists": [...],
                 "unavailable_objects": [...], "warnings": [...]}}}}
        "run" is present only when a sample or file was given.

    Errors:
        A formatted error if project_dir is not a framework, a path leaves the
        working directory, a name starts with "-" or a value is of the wrong kind, the
        interpreter lacks the libraries, or the check times out.
    """

    # --------------------------- Runtime fields --------------------------- #
    project_dir: str = RuntimeField(
        description="Directory of the analysis framework, relative to the working directory")
    sample: Optional[str] = RuntimeField(
        default=None, description="Configured sample to run on (static checks only if omitted)")
    tag: Optional[str] = RuntimeField(
        default=None, description="Group of the sample config the sample belongs to (rarely needed)")
    sample_config: Optional[str] = RuntimeField(
        default=None, description="File under configs/samples/ defining the sample (default samples.yaml)")
    sample_file: Optional[str] = RuntimeField(
        default=None, description="ROOT file to run on instead, relative to the working directory")
    is_data: Optional[bool] = RuntimeField(
        default=None, description="Whether sample_file is data (default: decided from the file)")
    year: Optional[Union[str, int]] = RuntimeField(default=None, description="Run period of sample_file")
    channels: Optional[List[str]] = RuntimeField(
        default=None, description="Channels to run (default: all)")
    hist_collections: Optional[List[str]] = RuntimeField(
        default=None, description="Histogram collections to fill (default: all)")
    max_events: int = RuntimeField(default=2000, description="Events to process in the run")
    strict: bool = RuntimeField(
        default=True, description="Stop at the first failure (false: warn and carry on)")
    venv: Optional[str] = RuntimeField(
        default=None,
        description="Virtual environment to run with, relative to the working directory")
    timeout_s: int = RuntimeField(
        default=50, description="Timeout in seconds (the tool server may cut calls at 60 s)")
    # ---------------------------------------------------------------------- #

    # ---------------------------- State fields ---------------------------- #
    base_directory: str = StateField(default=".", description="Base directory for safe paths")
    analysis_python: Optional[str] = StateField(
        default=None,
        description="Python interpreter with coffea installed, used to run analysis frameworks")
    # ---------------------------------------------------------------------- #

    def _setup(self):
        self.base_directory = os.path.abspath(self.base_directory)
        if not os.path.isdir(self.base_directory):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def _python(self, base: str):
        """Interpreter to run the check with: venv, then the configured one, then our own."""
        if self.venv:
            venv = projects.safe_path(base, self.venv)
            if venv is None:
                return None, "venv escapes the working directory"
            for candidate in (os.path.join(venv, "bin", "python"),
                              os.path.join(venv, "Scripts", "python.exe")):
                if os.path.isfile(candidate):
                    return candidate, None
            return None, f"no python interpreter found in {self.venv}"
        if self.analysis_python:
            # absolute: the check runs with the project as its working directory
            configured = os.path.abspath(os.path.expanduser(str(self.analysis_python)))
            if not os.path.isfile(configured):
                return None, f"analysis_python does not exist: {self.analysis_python}"
            return configured, None
        return sys.executable, None

    def _request(self):
        """The arguments in the types the checker needs, or (None, what is wrong)."""
        def names(value, what):
            if value is None:
                return None
            if isinstance(value, str):
                value = [value]         # one name is a list of one, not of its letters
            if not isinstance(value, (list, tuple)) or \
                    not all(isinstance(item, str) and item for item in value):
                raise ValueError(f"{what} must be a list of names")
            return list(value)

        def text(value, what):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{what} must be a name")
            return value

        def whole(value, what, least):
            try:
                if isinstance(value, bool):
                    raise ValueError
                number = int(value)
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f"{what} must be a whole number") from None
            if number < least:
                raise ValueError(f"{what} must be at least {least}")
            return number

        try:
            year = self.year
            if year is not None and (isinstance(year, bool) or not isinstance(year, (str, int))):
                raise ValueError("year must be a name such as '2018'")
            for flag, what in ((self.strict, "strict"), (self.is_data, "is_data")):
                if flag is not None and not isinstance(flag, bool):
                    raise ValueError(f"{what} must be true or false")
            sample_config = text(self.sample_config, "sample_config")
            if sample_config is not None and (
                    os.path.basename(sample_config) != sample_config
                    or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*", sample_config)):
                raise ValueError("sample_config must be the name of a file under "
                                 "configs/samples/ of the framework, not a path")
            return {
                "sample": text(self.sample, "sample"),
                "tag": text(self.tag, "tag"),
                "sample_config": sample_config,
                "sample_file": text(self.sample_file, "sample_file"),
                "venv": text(self.venv, "venv"),
                "year": None if year is None or year == "" else str(year),
                "channels": names(self.channels, "channels"),
                "hist_collections": names(self.hist_collections, "hist_collections"),
                "max_events": whole(self.max_events, "max_events", 1),
                "timeout": max(10, whole(self.timeout_s, "timeout_s", 1)),
            }, None
        except ValueError as exc:
            return None, str(exc)

    def _run(self) -> str:
        try:
            return self._check()
        except Exception as exc:    # a tool must answer, not raise
            return self.format_error(
                error="Check Failed", reason=f"{type(exc).__name__}: {exc}",
                context=f"project_dir={self.project_dir}",
                suggestion="This is a problem in the tool rather than in the framework: "
                           f"run `python -m <package>.tools.check` in {self.project_dir} by hand")

    def _check(self) -> str:
        base = os.path.abspath(self.base_directory)
        project = projects.safe_path(base, self.project_dir, follow_links=True)
        if project is None or not os.path.isdir(project):
            return self.format_error(
                error="Project Not Found",
                reason="project_dir is not a directory inside the working directory",
                context=f"project_dir={self.project_dir}",
                suggestion="Create the framework with ScaffoldAnalysisFramework first")
        package = projects.find_package(Path(project))
        if package is None:
            return self.format_error(
                error="Not A Framework",
                reason="no analysis package (tools/processor.py + definitions/objects.py) found",
                context=f"project_dir={self.project_dir}",
                suggestion="Point project_dir at the directory ScaffoldAnalysisFramework created")
        request, problem = self._request()
        if request is None:
            return self.format_error(
                error="Invalid Request", reason=problem,
                suggestion="Correct the value named above and call the tool again")
        if request["sample"] and request["sample_file"]:
            return self.format_error(
                error="Invalid Request", reason="give either sample or sample_file, not both")
        named = [request["sample"], request["tag"], request["sample_config"], request["year"],
                 *(request["channels"] or []), *(request["hist_collections"] or [])]
        dashed = option_like(named)
        if dashed:
            return self.format_error(
                error="Invalid Request",
                reason=f"names cannot start with '-': {dashed}",
                suggestion="Sample, channel, collection and period names are passed to the "
                           "checker's command line, where a leading dash means an option")
        sample_file = None
        if request["sample_file"]:
            sample_file = projects.safe_path(base, request["sample_file"])
            if sample_file is None or not os.path.isfile(sample_file):
                return self.format_error(
                    error="File Not Found",
                    reason="sample_file does not exist inside the working directory",
                    context=f"sample_file={self.sample_file}",
                    suggestion="Check the path, or pass sample=<configured sample name>")
        python, problem = self._python(base)
        if python is None:
            return self.format_error(
                error="Interpreter Not Found", reason=problem,
                suggestion="Create the environment first (python -m venv <dir> && pip install "
                           "-r requirements.txt), or omit venv to use the toolkit's python")

        timeout = request["timeout"]
        env = dict(os.environ)
        env["PYTHONPATH"] = project + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["MPLBACKEND"] = "Agg"
        handle, report_path = tempfile.mkstemp(prefix="framework_check_", suffix=".json")
        os.close(handle)
        report, shown = None, ""
        try:
            cmd = build_command(python, package, report_path, sample=request["sample"],
                                sample_file=sample_file, is_data=self.is_data,
                                year=request["year"], channels=request["channels"],
                                hist_collections=request["hist_collections"],
                                max_events=request["max_events"], strict=self.strict,
                                tag=request["tag"], sample_config=request["sample_config"])
            shown = " ".join(["python"] + [c for c in cmd[1:] if c != report_path and c != "--json"])
            # no stdin: the tool server talks to its client over this process's own
            proc = subprocess.run(cmd, cwd=project, env=env, capture_output=True, text=True,
                                  timeout=timeout, stdin=subprocess.DEVNULL)
            try:
                with open(report_path, encoding="utf8") as stream:
                    text = stream.read()
                report = json.loads(text) if text.strip() else None
            except (OSError, ValueError):
                report = None
        except subprocess.TimeoutExpired:
            return self.format_error(
                error="Timeout", reason=f"the check did not finish in {timeout} s",
                context=shown,
                suggestion="Try once more if this was the first run in this environment "
                           "(the libraries are cached after it). Otherwise lower max_events or "
                           "name fewer channels and hist_collections. A larger timeout_s only "
                           "helps if the tool server allows calls that long (toolbase: 60 s "
                           "unless started with `tb serve --call-timeout SECONDS`)")
        except OSError as exc:
            return self.format_error(error="Execution Failed", reason=str(exc), context=shown)
        finally:
            try:
                os.remove(report_path)
            except OSError:
                pass

        if not isinstance(report, dict):
            stderr = _tail(proc.stderr, 25)
            suggestion = "Read the traceback above and fix the reported file"
            if "No module named" in stderr:
                suggestion = ("If the missing module is a library, the interpreter lacks a "
                              "dependency: install the project's requirements into it (pip "
                              "install -r requirements.txt), or pass venv=<environment that "
                              "has them>. If it is the analysis package itself, a file of the "
                              "package is missing or misnamed")
            return self.format_error(
                error="Check Did Not Run",
                reason=f"the checker exited with status {proc.returncode} before writing a report",
                context=stderr or _tail(proc.stdout, 25), suggestion=suggestion)

        absent = missing_modules(report)
        if absent:
            return self.format_error(
                error="Missing Dependency",
                reason=f"{python} cannot import {', '.join(absent)}, which the framework needs",
                context=shown,
                suggestion="Install the framework bundle's dependencies into the toolkit "
                           "(tb install heptapod --bundle framework), or use an environment "
                           "that has the project's requirements: venv=<dir> for this call, or "
                           "`tb config set --user heptapod analysis_python <python>` for all")

        result = {"status": "ok", "command": shown, "python": python, **condense(report)}
        return json.dumps(result, ensure_ascii=False)
