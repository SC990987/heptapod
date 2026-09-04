#!/usr/bin/env python3
"""
# lpc_scaleout.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Run a coffea analysis over many ROOT files at the LPC -- locally, or fanned out
over HTCondor.

This carries no physics. You supply an analysis module exposing

    def process(events, meta) -> dict

which returns a dict of `hist` objects, numbers, or numpy arrays. The runner
handles file discovery, chunking, submission, resumption and merging; results
are combined by addition, so partial results merge the way histograms do.

Subcommands
-----------
  discover  list ROOT files under a directory (local path or xrootd URL)
  local     process files in this process (or a pool), for development
  submit    fan out over HTCondor, one job per chunk of files
  status    progress of a submitted run
  merge     combine the per-chunk outputs into one result
  run       discover + submit + wait + merge

Every subcommand is resumable: a chunk whose output already exists is skipped,
so re-running after a partial failure only redoes what is missing.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import pickle
import shlex
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

EOS_PREFIX = "/eos/uscms"
XROOTD_REDIRECTOR = "root://cmseos.fnal.gov/"


# ===================================================================== #
# ============================ path helpers =========================== #
# ===================================================================== #

def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def to_xrootd(path: str) -> str:
    """/eos/uscms/store/... -> root://cmseos.fnal.gov//store/...

    Worker nodes cannot rely on the EOS FUSE mount; xrootd always works."""
    if path.startswith("root://"):
        return path
    if path.startswith(EOS_PREFIX + "/store/"):
        return XROOTD_REDIRECTOR + path[len(EOS_PREFIX):]
    if path.startswith("/store/"):
        return XROOTD_REDIRECTOR + path
    return path


def to_local(path: str) -> str:
    """The inverse, for interactive use where the FUSE mount is present."""
    if path.startswith(XROOTD_REDIRECTOR):
        return EOS_PREFIX + path[len(XROOTD_REDIRECTOR):]
    return path



# ===================================================================== #
# ========================== dataset catalog ========================== #
# ===================================================================== #

def load_catalog(path: Optional[str] = None) -> Dict[str, dict]:
    """Read the dataset catalog. Default: configs/datasets.yaml next to the repo."""
    import yaml
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        for cand in (os.path.join(os.getcwd(), "configs", "datasets.yaml"),
                     os.path.join(here, "..", "configs", "datasets.yaml")):
            if os.path.exists(cand):
                path = cand
                break
    if not path or not os.path.exists(path):
        raise FileNotFoundError(
            "no dataset catalog found; pass --catalog, or generate one with "
            "scripts/build_dataset_catalog.py")
    with open(path) as fh:
        return (yaml.safe_load(fh) or {}).get("datasets", {})


def resolve_dataset(name: str, catalog_path: Optional[str] = None) -> dict:
    """Name -> catalog entry, with a useful error when it is not there."""
    cat = load_catalog(catalog_path)
    if name in cat:
        return dict(cat[name], name=name)
    near = [k for k in cat if name.lower() in k.lower()]
    raise KeyError(
        f"dataset {name!r} is not in the catalog ({len(cat)} entries)."
        + (f" Did you mean: {near[:8]}" if near else
           " Run `lpc_scaleout.py datasets` to list them."))


def select_datasets(cat: Dict[str, dict], pattern=None, **params) -> Dict[str, dict]:
    """Filter by substring and/or exact physics parameters (MBs_GeV=500, ...)."""
    out = {}
    for name, entry in cat.items():
        if pattern and pattern.lower() not in name.lower():
            continue
        p = entry.get("params", {})
        if any(v is not None and p.get(k) != v for k, v in params.items()):
            continue
        out[name] = entry
    return out


# ===================================================================== #
# ============================= discovery ============================= #
# ===================================================================== #

def discover(directory: str, pattern: str = "*.root") -> List[str]:
    """List ROOT files under `directory`, via the filesystem or xrootd."""
    if directory.startswith("root://"):
        base = directory[len(XROOTD_REDIRECTOR):] if directory.startswith(
            XROOTD_REDIRECTOR) else directory
        out = subprocess.run(["xrdfs", XROOTD_REDIRECTOR.rstrip("/"), "ls", base],
                             capture_output=True, text=True, timeout=300)  # real binary
        if out.returncode != 0:
            raise RuntimeError(f"xrdfs ls failed: {out.stderr.strip()}")
        names = [l.strip() for l in out.stdout.splitlines() if l.strip().endswith(".root")]
        return sorted(XROOTD_REDIRECTOR + n.lstrip("/") for n in names)
    files = sorted(glob.glob(os.path.join(directory, pattern)))
    if not files:
        raise RuntimeError(f"no files matching {pattern} under {directory}")
    return files


def chunk(files: List[str], per_job: int) -> List[List[str]]:
    return [files[i:i + per_job] for i in range(0, len(files), per_job)]


# ===================================================================== #
# ======================== the analysis contract ====================== #
# ===================================================================== #

def load_analysis(path: str):
    """Import a user analysis module by file path and return it.

    It must define `process(events, meta) -> dict`. It may optionally define
    `open_events(path)` to override how a file is opened (LLPnanoAOD and other
    custom formats need this -- see the `scaleout` skill)."""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"analysis module not found: {path}")
    spec = importlib.util.spec_from_file_location("user_analysis", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                      # type: ignore[union-attr]
    if not hasattr(mod, "process"):
        raise AttributeError(f"{path} defines no process(events, meta) function")
    return mod


def default_open(path: str, treename: str = "Events"):
    """Open with coffea's NanoAODSchema, tolerant of skims."""
    from coffea.nanoevents import NanoEventsFactory, NanoAODSchema

    class Tolerant(NanoAODSchema):
        warn_missing_crossrefs = False
        error_missing_event_ids = False

    return NanoEventsFactory.from_root({path: treename},
                                       schemaclass=Tolerant).events()


def process_chunk(analysis_path: str, files: List[str], out_path: str,
                  treename: str = "Events", meta: Optional[dict] = None) -> dict:
    """Process a list of files and write the accumulated result to `out_path`."""
    mod = load_analysis(analysis_path)
    opener = getattr(mod, "open_events", None) or (lambda p: default_open(p, treename))
    acc: Dict[str, Any] = {}
    report = {"files_ok": [], "files_failed": {}, "n_events": 0}
    for f in files:
        try:
            events = opener(f)
            n = len(events)
            out = mod.process(events, dict(meta or {}, file=f, n_events=n))
            acc = accumulate(acc, out)
            report["n_events"] += n
            report["files_ok"].append(f)
        except Exception as e:                                   # noqa: BLE001
            # one bad file must not lose the whole chunk
            report["files_failed"][f] = f"{type(e).__name__}: {e}"
    result = {"accumulator": acc, "report": report}
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as fh:
        pickle.dump(result, fh)
    return result


def accumulate(a: Any, b: Any) -> Any:
    """Merge two results by addition, recursing into dicts.

    Works for hist objects, numbers and numpy arrays -- anything with __add__.
    Lists are concatenated."""
    if a is None or (isinstance(a, dict) and not a):
        return b
    if b is None or (isinstance(b, dict) and not b):
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = accumulate(a[k], v) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    return a + b


# ===================================================================== #
# ============================== condor =============================== #
# ===================================================================== #

COFFEA_IMAGE = ("/cvmfs/unpacked.cern.ch/registry.hub.docker.com/"
                "coffeateam/coffea-almalinux9:2026.7.0-py3.12")

WRAPPER = """#!/bin/bash
set -e
CHUNK=$1
echo "host=$(hostname) chunk=${{CHUNK}}"
echo "python=$(which python3) $(python3 --version 2>&1)"
python3 -c "import coffea; print('coffea', coffea.__version__)"
export XRD_REQUESTTIMEOUT=600
export XRD_REDIRECTLIMIT=5
# everything below runs in the job scratch dir, which is where condor put the
# transferred inputs and from where it will take the output back
python3 lpc_scaleout.py exec-chunk \\
    --analysis {analysis_base} \\
    --chunk-file chunk_${{CHUNK}}.json \\
    --out part_${{CHUNK}}.pkl \\
    --treename {treename}
echo "chunk ${{CHUNK}} done"
"""

SUBMIT = """universe                = vanilla
executable              = {wrapper}
arguments               = $(chunk)
output                  = {logdir}/job_$(chunk).out
error                   = {logdir}/job_$(chunk).err
log                     = {logdir}/cluster.log
request_cpus            = {cpus}
request_memory          = {memory}
request_disk            = {disk}
+SingularityImage       = "{image}"
should_transfer_files   = YES
when_to_transfer_output = ON_EXIT
transfer_input_files    = {runner},{analysis},{chunkdir}/chunk_$(chunk).json
transfer_output_files   = part_$(chunk).pkl
transfer_output_remaps  = "part_$(chunk).pkl = {outdir}/part_$(chunk).pkl"
getenv                  = False
max_retries             = 2
queue chunk in ({chunks})
"""


def write_condor(workdir: str, analysis: str, chunks: List[List[str]],
                 treename: str, cpus: int, memory: int, disk: int,
                 image: str, pending: List[int]) -> str:
    """Write the chunk lists, wrapper and submit file.

    Worker nodes run inside an apptainer container with --contain: they cannot
    see /uscms_data or the EOS FUSE mount. So the runner, the analysis module
    and the chunk list are transferred in, data is read over xrootd, and the
    per-chunk pickle is transferred back. The container image supplies coffea.
    """
    chunkdir = os.path.join(workdir, "chunks")
    outdir = os.path.abspath(os.path.join(workdir, "parts"))
    logdir = os.path.join(workdir, "logs")
    for d in (chunkdir, outdir, logdir):
        os.makedirs(d, exist_ok=True)
    for i, files in enumerate(chunks):
        with open(os.path.join(chunkdir, f"chunk_{i}.json"), "w") as fh:
            json.dump([to_xrootd(f) for f in files], fh)

    wrapper = os.path.join(workdir, "run_chunk.sh")
    with open(wrapper, "w") as fh:
        fh.write(WRAPPER.format(analysis_base=os.path.basename(analysis),
                                treename=treename))
    os.chmod(wrapper, 0o755)

    sub = os.path.join(workdir, "submit.sub")
    with open(sub, "w") as fh:
        fh.write(SUBMIT.format(
            wrapper=os.path.abspath(wrapper), logdir=os.path.abspath(logdir),
            cpus=cpus, memory=memory, disk=disk, image=image,
            runner=os.path.abspath(__file__), analysis=os.path.abspath(analysis),
            chunkdir=os.path.abspath(chunkdir), outdir=outdir,
            chunks=",".join(str(i) for i in pending)))
    return sub


def condor(cmd: List[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Run an HTCondor command through a shell.

    The LPC `condor_*` executables are scripts with no shebang -- they rely on
    the shell's fallback to /bin/sh. Handing them straight to execve fails with
    "Exec format error", so they must be invoked via a shell.
    """
    return subprocess.run(" ".join(shlex.quote(c) for c in cmd), shell=True,
                          executable="/bin/bash", capture_output=True,
                          text=True, timeout=timeout)


def pending_chunks(workdir: str, n_chunks: int) -> List[int]:
    """Chunks with no output yet -- the basis of resumability."""
    outdir = os.path.join(workdir, "parts")
    return [i for i in range(n_chunks)
            if not os.path.exists(os.path.join(outdir, f"part_{i}.pkl"))]


# ===================================================================== #
# ============================ subcommands ============================ #
# ===================================================================== #

def cmd_discover(args):
    files = discover(args.directory, args.pattern)
    print(f"{len(files)} files under {args.directory}")
    for f in files[:args.show]:
        print("  ", f)
    if len(files) > args.show:
        print(f"   ... and {len(files) - args.show} more")
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(files, fh, indent=1)
        print("wrote", args.out)
    return 0


def _inputs(args):
    """Resolve --dataset / --directory / --filelist into (files, provenance)."""
    if getattr(args, "dataset", None):
        entry = resolve_dataset(args.dataset, getattr(args, "catalog", None))
        files = discover(entry["path"], args.pattern)
        return files, {"dataset": args.dataset, "path": entry["path"],
                       "params": entry.get("params"), "channel": entry.get("channel"),
                       "era": entry.get("era"), "tier": entry.get("tier")}
    if getattr(args, "directory", None):
        return discover(args.directory, args.pattern), {"directory": args.directory}
    if getattr(args, "filelist", None):
        return json.load(open(args.filelist)), {"filelist": os.path.abspath(args.filelist)}
    raise SystemExit("give one of --dataset, --directory or --filelist")


def cmd_datasets(args):
    cat = load_catalog(args.catalog)
    sel = select_datasets(cat, args.match,
                          MBs_GeV=args.mbs, MDp_GeV=args.mdp, ctau_mm=args.ctau)
    if not sel:
        print(f"no datasets matched (catalog has {len(cat)})")
        return 1
    print(f"{len(sel)} of {len(cat)} datasets")
    total = 0
    for name, e in sorted(sel.items()):
        p = e.get("params", {})
        n = e.get("n_files") or 0
        total += n
        print(f"  {name:52s} files={n:4d}  "
              f"MBs={p.get('MBs_GeV','?'):>6} MDp={p.get('MDp_GeV','?'):>5} "
              f"ctau={p.get('ctau_mm','?')}")
    print(f"  total files: {total}")
    if args.link:
        os.makedirs(args.link, exist_ok=True)
        for name, e in sorted(sel.items()):
            dst = os.path.join(args.link, name)
            if not os.path.exists(dst):
                os.symlink(e["path"], dst)
        print(f"  linked {len(sel)} dataset(s) under {args.link}/ "
              f"(so sandboxed tools can reach them by relative path)")
    return 0


def cmd_local(args):
    files, provenance = _inputs(args)
    if args.max_files:
        files = files[:args.max_files]
    if not args.use_xrootd:
        files = [to_local(f) for f in files]
    os.makedirs(args.workdir, exist_ok=True)
    out = os.path.join(args.workdir, "local_result.pkl")
    with open(os.path.join(args.workdir, "manifest.json"), "w") as fh:
        json.dump({"n_files": len(files), "mode": "local", "created_utc": _now(),
                   "analysis": os.path.abspath(args.analysis),
                   "provenance": provenance}, fh, indent=1)
    t0 = time.time()
    result = process_chunk(args.analysis, files, out, args.treename)
    dt = time.time() - t0
    rep = result["report"]
    print(f"processed {len(rep['files_ok'])}/{len(files)} files, "
          f"{rep['n_events']} events in {dt:.1f}s "
          f"({rep['n_events']/dt:.0f} evt/s)")
    for f, err in rep["files_failed"].items():
        print("  FAILED", os.path.basename(f), "->", err)
    print("keys:", sorted(result["accumulator"].keys()))
    print("wrote", out)
    return 0 if not rep["files_failed"] else 1


def cmd_exec_chunk(args):
    """Runs on the worker node."""
    files = json.load(open(args.chunk_file))
    result = process_chunk(args.analysis, files, args.out, args.treename)
    rep = result["report"]
    print(f"chunk: {len(rep['files_ok'])}/{len(files)} files ok, "
          f"{rep['n_events']} events")
    for f, err in rep["files_failed"].items():
        print("  FAILED", f, "->", err)
    return 0


def cmd_submit(args):
    files, provenance = _inputs(args)
    if args.max_files:
        files = files[:args.max_files]
    chunks = chunk(files, args.files_per_job)
    os.makedirs(args.workdir, exist_ok=True)
    manifest = {"n_files": len(files), "n_chunks": len(chunks),
                "files_per_job": args.files_per_job,
                "analysis": os.path.abspath(args.analysis),
                "created_utc": _now(), "provenance": provenance}
    with open(os.path.join(args.workdir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    todo = pending_chunks(args.workdir, len(chunks))
    if not todo:
        print(f"all {len(chunks)} chunks already have output -- nothing to submit")
        return 0
    sub = write_condor(args.workdir, args.analysis, chunks, args.treename,
                       args.cpus, args.memory, args.disk, args.image, todo)
    print(f"{len(files)} files -> {len(chunks)} chunks, {len(todo)} to run")
    if args.dry_run:
        print("dry run; submit file at", sub)
        return 0
    out = condor(["condor_submit", sub])
    print(out.stdout.strip() or out.stderr.strip())
    return out.returncode


def cmd_status(args):
    man = json.load(open(os.path.join(args.workdir, "manifest.json")))
    n = man["n_chunks"]
    todo = pending_chunks(args.workdir, n)
    done = n - len(todo)
    print(f"{done}/{n} chunks complete ({100*done/n:.0f}%)")
    q = condor(["condor_q", "-totals"])
    print(q.stdout.strip().splitlines()[-1] if q.stdout.strip() else "")
    held = condor(["condor_q", "-hold", "-af", "ClusterId", "HoldReason"])
    if held.stdout.strip():
        print("HELD:"); print(held.stdout.strip()[:800])
    if todo and args.show_missing:
        print("missing chunks:", todo[:40])
    return 0


def cmd_merge(args):
    parts = sorted(glob.glob(os.path.join(args.workdir, "parts", "part_*.pkl")))
    if not parts:
        print("no parts to merge"); return 1
    acc: Any = {}
    report = {"files_ok": [], "files_failed": {}, "n_events": 0}
    for p in parts:
        with open(p, "rb") as fh:
            r = pickle.load(fh)
        acc = accumulate(acc, r["accumulator"])
        report["n_events"] += r["report"]["n_events"]
        report["files_ok"] += r["report"]["files_ok"]
        report["files_failed"].update(r["report"]["files_failed"])
    out = args.out or os.path.join(args.workdir, "merged.pkl")
    manifest = {}
    mp = os.path.join(args.workdir, "manifest.json")
    if os.path.exists(mp):
        manifest = json.load(open(mp))
    payload = {"accumulator": acc, "report": report,
               "provenance": manifest.get("provenance", {}),
               "analysis": manifest.get("analysis"),
               "merged_utc": _now(), "n_parts": len(parts)}
    with open(out, "wb") as fh:
        pickle.dump(payload, fh)
    with open(os.path.join(args.workdir, "summary.json"), "w") as fh:
        json.dump({k: v for k, v in payload.items() if k != "accumulator"} |
                  {"scalars": {k: v for k, v in acc.items()
                               if isinstance(v, (int, float))}}, fh, indent=1, default=str)
    print(f"merged {len(parts)} parts: {len(report['files_ok'])} files, "
          f"{report['n_events']} events")
    if report["files_failed"]:
        print(f"  {len(report['files_failed'])} files failed:")
        for f, e in list(report["files_failed"].items())[:10]:
            print("   ", os.path.basename(f), "->", e)
    print("keys:", sorted(acc.keys()) if isinstance(acc, dict) else type(acc))
    print("wrote", out)
    return 0


def cmd_run(args):
    rc = cmd_submit(args)
    if rc != 0:
        return rc
    man = json.load(open(os.path.join(args.workdir, "manifest.json")))
    n = man["n_chunks"]
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        todo = pending_chunks(args.workdir, n)
        print(f"  {n - len(todo)}/{n} complete", flush=True)
        if not todo:
            break
        time.sleep(args.poll)
    return cmd_merge(args)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[5],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, need_analysis=True):
        sp.add_argument("--workdir", default="scaleout_work",
                        help="where chunks, logs, parts and the merged result go")
        if need_analysis:
            sp.add_argument("--analysis", required=True,
                            help="path to a module defining process(events, meta)")
        sp.add_argument("--treename", default="Events")

    d = sub.add_parser("discover"); d.add_argument("directory")
    d.add_argument("--pattern", default="*.root"); d.add_argument("--out")
    d.add_argument("--show", type=int, default=5); d.set_defaults(func=cmd_discover)

    ds = sub.add_parser("datasets"); ds.add_argument("--catalog")
    ds.add_argument("--match"); ds.add_argument("--mbs", type=float)
    ds.add_argument("--mdp", type=float); ds.add_argument("--ctau", type=float)
    ds.add_argument("--link", help="symlink the matches into this directory")
    ds.set_defaults(func=cmd_datasets)

    l = sub.add_parser("local"); common(l)
    l.add_argument("--dataset"); l.add_argument("--catalog")
    l.add_argument("--directory"); l.add_argument("--filelist")
    l.add_argument("--pattern", default="*.root")
    l.add_argument("--max-files", type=int)
    l.add_argument("--use-xrootd", action="store_true")
    l.set_defaults(func=cmd_local)

    e = sub.add_parser("exec-chunk"); e.add_argument("--analysis", required=True)
    e.add_argument("--chunk-file", required=True); e.add_argument("--out", required=True)
    e.add_argument("--treename", default="Events"); e.set_defaults(func=cmd_exec_chunk)

    for name, fn in (("submit", cmd_submit), ("run", cmd_run)):
        s = sub.add_parser(name); common(s)
        s.add_argument("--dataset"); s.add_argument("--catalog")
        s.add_argument("--directory"); s.add_argument("--filelist")
        s.add_argument("--pattern", default="*.root")
        s.add_argument("--max-files", type=int)
        s.add_argument("--files-per-job", type=int, default=5)
        s.add_argument("--cpus", type=int, default=1)
        s.add_argument("--memory", type=int, default=4000)
        s.add_argument("--disk", type=int, default=4000)
        s.add_argument("--image", default=COFFEA_IMAGE,
                       help="apptainer image supplying coffea on the worker")
        s.add_argument("--dry-run", action="store_true")
        s.add_argument("--out")
        if name == "run":
            s.add_argument("--poll", type=int, default=60)
            s.add_argument("--timeout", type=int, default=7200)
        s.set_defaults(func=fn)

    st = sub.add_parser("status"); common(st, need_analysis=False)
    st.add_argument("--show-missing", action="store_true"); st.set_defaults(func=cmd_status)

    m = sub.add_parser("merge"); common(m, need_analysis=False)
    m.add_argument("--out"); m.set_defaults(func=cmd_merge)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
