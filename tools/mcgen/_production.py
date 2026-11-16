"""
# _production.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

A production: one directory holding everything needed to make a simulated sample.

    <name>/
      production.json      what was asked for, the chain of steps and their commands
      fragment.py          the generator fragment
      bin/production.env   the same as shell variables, for the scripts below
      bin/configure.sh     set up the releases and write every step's configuration
      bin/run_job.sh       run every step of one job, copy the kept tiers out
      bin/driver.sh        configure once, then run the jobs here or submit them
      bin/condor_job.sh    what a batch node runs
      bin/in_el8.sh        run a command in el8 (directly, or in cmssw-el8)
      bin/wrap_cfg.py      run one step for one job: events, seeds, threads, luminosity block
      cmssw/  cfg/  jobs/  condor/  output/   made as the production runs

Writing it runs nothing and needs no CMSSW: the scripts are static, and what is
particular to the production is in production.env. Outputs go to
``<output>/<TIER>/<name>_<TIER>_<job>.root``.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.mcgen import _campaigns as campaigns

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
MAX_THREADS = 8
# Files of the premixed-pileup library a production mixes from, drawn at random once
# (with the production's seed). The whole library is hundreds of thousands of files:
# listed in a configuration, it makes one of some 80 MB that every job would receive.
PREMIX_FILES = 1000


class ProductionError(ValueError):
    """A request that cannot become a production; the message says why."""

    def __init__(self, message: str, suggestion: str = ""):
        super().__init__(message)
        self.suggestion = suggestion


def split_events(events: int, per_job: int) -> List[int]:
    """Events per job: full jobs of ``per_job`` and a last one with the rest."""
    counts = [per_job] * (events // per_job)
    if events % per_job:
        counts.append(events % per_job)
    return counts


def output_path(output: str, tier: str, name: str, job: int) -> str:
    return f"{output.rstrip('/')}/{tier}/{name}_{tier}_{job:04d}.root"


def kept_tiers(final_tier: str, keep: Optional[List[str]]) -> List[str]:
    """The tiers copied out: those asked for, else the final one. Each must be produced."""
    produced = ["GEN"] if final_tier == "GEN" else \
        ["GEN-SIM"] + [campaigns.STEP_TIER[s] for s in campaigns.TIER_STEPS[final_tier]]
    wanted = list(keep) if keep else [final_tier]
    unknown = [tier for tier in wanted if tier not in produced]
    if unknown:
        raise ProductionError(f"keep_tiers {unknown} are not produced on the way to {final_tier}",
                              f"This chain produces {produced}")
    return list(dict.fromkeys(wanted))


def create(directory: Path, *, name: str, run_period: str, events: int, events_per_job: int,
           fragment: str, pileup: str = "premix", final_tier: str = "NANOAOD",
           keep_tiers: Optional[List[str]] = None, output: Optional[str] = None,
           threads: int = 1, seed: Optional[int] = None,
           source: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Write a production into ``directory`` (which must not exist) and describe it."""
    from tools.mcgen import _fragment as fragments
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ProductionError(f"{name!r} cannot name a production",
                              "Use a letter, then letters, digits, _ and -, up to 64 characters")
    for label, value in (("events", events), ("events_per_job", events_per_job), ("threads", threads)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ProductionError(f"{label} must be a positive whole number, not {value!r}")
    if threads > MAX_THREADS:
        raise ProductionError(f"threads must be at most {MAX_THREADS}")
    if events_per_job > events:
        events_per_job = events
    campaigns.check_choice(run_period, final_tier, pileup)
    fragments.check(fragment)
    lhe = fragments.needs_lhe(fragment)
    keep = kept_tiers(final_tier, keep_tiers)
    seed = seed if seed is not None else zlib.crc32(name.encode()) % 900000 + 1
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 < seed < 900000000:
        raise ProductionError("seed must be a whole number between 1 and 899999999")
    if directory.exists():
        raise ProductionError(f"{directory.name} exists already",
                              "Choose another name, or look at that production's status")
    output = output or str(directory / "output")
    steps = campaigns.build_steps(run_period, final_tier, pileup, lhe)
    module = f"Configuration/GenProduction/python/{name.replace('-', '_')}_fragment.py"
    jobs = split_events(events, events_per_job)
    for step in steps:
        step["cmsdriver"] = campaigns.cmsdriver_args(step, module, events_per_job,
                                                     "@CFG@/" + step["cfg"])
        # the premix library: a random subset of it, listed by configure.sh (@PREMIX@)
        step["cmsdriver"] = ["@PREMIX@" if arg.startswith("dbs:") and previous == "--pileup_input"
                             else arg for previous, arg in zip([""] + step["cmsdriver"], step["cmsdriver"])]
    description = {
        "name": name, "run_period": run_period,
        "campaign": campaigns.RUN_PERIODS[run_period]["campaign"],
        "description": campaigns.DESCRIPTIONS[run_period],
        "events": events, "events_per_job": events_per_job, "jobs": jobs,
        "pileup": pileup, "premix_dataset": campaigns.premix_dataset(run_period) if pileup == "premix" else None,
        "premix_files": PREMIX_FILES if pileup == "premix" and final_tier not in ("GEN", "GEN-SIM") else None,
        "final_tier": final_tier, "keep_tiers": keep, "output": output, "threads": threads,
        "seed": seed, "lhe": lhe, "fragment_module": module, "source": source or {},
        "container": campaigns.CONTAINER, "steps": steps,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (directory / "bin").mkdir(parents=True)
    for sub in ("cfg", "jobs", "condor/logs", "condor/out"):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    (directory / "fragment.py").write_text(fragment)
    (directory / "production.json").write_text(json.dumps(description, indent=1) + "\n")
    (directory / "bin" / "production.env").write_text(render_env(description))
    for script, text in SCRIPTS.items():
        path = directory / "bin" / script
        path.write_text(text)
        path.chmod(0o755)
    return description


def render_env(d: Dict[str, Any]) -> str:
    """production.json as bash variables (sourced by the scripts)."""
    q = shlex.quote
    lines = ["# written by HEPTAPOD's PrepareMCProduction; the scripts in bin/ source it",
             f"MCGEN_NAME={q(d['name'])}", f"MCGEN_OUTPUT={q(d['output'])}",
             f"MCGEN_SEED={d['seed']}", f"MCGEN_THREADS={d['threads']}",
             f"MCGEN_KEEP={q(' '.join(d['keep_tiers']))}",
             f"MCGEN_FRAGMENT_MODULE={q(d['fragment_module'])}",
             "MCGEN_JOB_EVENTS=(" + " ".join(str(n) for n in d["jobs"]) + ")",
             f"MCGEN_PREMIX_DATASET={q(d['premix_dataset'] or '') if d['premix_files'] else ''}",
             f"MCGEN_PREMIX_FILES={d['premix_files'] or 0}",
             "# index|name|tier|release|scram_arch|cfg|input|output",
             "MCGEN_STEPS=("]
    for s in d["steps"]:
        lines.append("  " + q("|".join([str(s["index"]), s["name"], s["tier"], s["release"],
                                        s["scram_arch"], s["cfg"], s["input"] or "", s["output"]])))
    lines.append(")")
    lines.append("# the cmsDriver.py command of each step; @CFG@ is the cfg/ directory")
    for s in d["steps"]:
        lines.append(f"MCGEN_CMSDRIVER_{s['index']}={q(' '.join(q(a) for a in s['cmsdriver']))}")
    return "\n".join(lines) + "\n"


IN_EL8 = r'''#!/bin/bash
# in_el8.sh COMMAND...: run a command in el8, where every Run 3 step runs:
# directly on an el8 host (an EL8 batch slot), else in the cmssw-el8 container.
case "$(/cvmfs/cms.cern.ch/common/cmsos 2>/dev/null)" in
    el8_*) exec "$@" ;;
esac
exec /cvmfs/cms.cern.ch/common/cmssw-el8 -- "$@"
'''

CONFIGURE = r'''#!/bin/bash
# configure.sh: set up the releases of this production under cmssw/, build the fragment
# into the release of the first step, and write every step's configuration into cfg/
# with cmsDriver.py --no_exec. Run it in el8 (bin/in_el8.sh). A premixed-pileup step
# asks DAS for the premix library here, which needs a valid grid proxy.
PROD=${MCGEN_PROD:-$(cd "$(dirname "$0")/.." && pwd)}
source "$PROD/bin/production.env"
source /cvmfs/cms.cern.ch/cmsset_default.sh
mkdir -p "$PROD/cmssw" "$PROD/cfg"
rm -f "$PROD/cfg/.configured" "$PROD/cfg/.failed"
fail() { echo "$*" > "$PROD/cfg/.failed"; echo "[mcgen] configure failed: $*"; exit 1; }
if [ -n "$MCGEN_PREMIX_DATASET" ] && [ ! -s "$PROD/cfg/premix_files.txt" ]; then
    # the premix library, listed once (outside cfg/, which batch jobs receive), and a
    # random subset of it drawn with the production's seed
    echo "[mcgen] listing the premix library $MCGEN_PREMIX_DATASET"
    dasgoclient -query="file dataset=$MCGEN_PREMIX_DATASET" > "$PROD/premix_library.txt" 2> "$PROD/das.err" \
        || fail "DAS could not list $MCGEN_PREMIX_DATASET (it needs a valid grid proxy): $(head -c 300 "$PROD/das.err")"
    [ -s "$PROD/premix_library.txt" ] || fail "DAS listed no files of $MCGEN_PREMIX_DATASET"
    python3 - "$PROD/premix_library.txt" "$PROD/cfg/premix_files.txt" "$MCGEN_SEED" "$MCGEN_PREMIX_FILES" <<'PY'
import random, sys
files = sorted(set(line.strip() for line in open(sys.argv[1]) if line.strip().endswith(".root")))
random.Random(int(sys.argv[3])).shuffle(files)
open(sys.argv[2], "w").write("\n".join(sorted(files[:int(sys.argv[4])])) + "\n")
PY
    echo "[mcgen] premix: $(wc -l < "$PROD/cfg/premix_files.txt") of $(wc -l < "$PROD/premix_library.txt") files"
fi
for entry in "${MCGEN_STEPS[@]}"; do
    IFS='|' read -r index name tier release arch cfg input output <<< "$entry"
    if [ ! -d "$PROD/cmssw/$release/src" ]; then
        echo "[mcgen] setting up $release ($arch)"
        (cd "$PROD/cmssw" && export SCRAM_ARCH=$arch && scram p CMSSW "$release") \
            || fail "scram project $release failed"
    fi
    if [ "$index" = 1 ]; then
        dest="$PROD/cmssw/$release/src/$(dirname "$MCGEN_FRAGMENT_MODULE")"
        mkdir -p "$dest" && cp "$PROD/fragment.py" "$PROD/cmssw/$release/src/$MCGEN_FRAGMENT_MODULE"
        (cd "$PROD/cmssw/$release/src" && export SCRAM_ARCH=$arch && eval "$(scram runtime -sh)" \
            && scram b -j 4) || fail "building the fragment in $release failed"
    fi
    var=MCGEN_CMSDRIVER_$index
    command=${!var//@CFG@/$PROD/cfg}
    command=${command//@PREMIX@/filelist:$PROD/cfg/premix_files.txt}
    echo "[mcgen] step $index $name: $command"
    (cd "$PROD/cmssw/$release/src" && export SCRAM_ARCH=$arch && eval "$(scram runtime -sh)" \
        && cd "$PROD/cfg" && eval "$command") || fail "cmsDriver.py for step $index ($name) failed"
    [ -s "$PROD/cfg/$cfg" ] || fail "cmsDriver.py wrote no $cfg"
done
date +%s > "$PROD/cfg/.configured"
echo "[mcgen] configured"
'''

RUN_JOB = r'''#!/bin/bash
# run_job.sh JOB: run every step of one job and copy the kept tiers to the output.
# Locally the releases are those configure.sh set up under cmssw/; on a batch node
# (MCGEN_AREAS empty) they are set up afresh in the job's directory. Run it in el8.
JOB=$1
PROD=${MCGEN_PROD:-$(cd "$(dirname "$0")/.." && pwd)}
source "$PROD/bin/production.env"
source /cvmfs/cms.cern.ch/cmsset_default.sh
WORK=${MCGEN_WORK:-$PROD/jobs/job_$(printf %04d "$JOB")}
AREAS=${MCGEN_AREAS:-$PROD/cmssw}
EVENTS=${MCGEN_TEST_EVENTS:-${MCGEN_JOB_EVENTS[$JOB]}}
mkdir -p "$WORK" "$AREAS" && cd "$WORK" || exit 1
STATUS=$WORK/status
SUMMARY=$WORK/summary.txt
: > "$STATUS"                       # a job run again starts its record afresh
: > "$SUMMARY"
note() { echo "$(date +%s) $*" >> "$STATUS"; echo "[mcgen] $*"; }
fail() { note FAILED "$*"; echo "FAILED $*" >> "$SUMMARY"; exit 1; }
[ -n "$EVENTS" ] || fail "no job $JOB in this production"
note START job "$JOB" events "$EVENTS"
echo "job $JOB events $EVENTS" >> "$SUMMARY"
for entry in "${MCGEN_STEPS[@]}"; do
    IFS='|' read -r index name tier release arch cfg input output <<< "$entry"
    note STEP "$index" "$name" start
    if [ ! -d "$AREAS/$release/src" ]; then
        (cd "$AREAS" && export SCRAM_ARCH=$arch && scram p CMSSW "$release") > "$WORK/setup_$release.log" 2>&1 \
            || fail "setting up $release"
        if [ "$index" = 1 ]; then
            mkdir -p "$AREAS/$release/src/$(dirname "$MCGEN_FRAGMENT_MODULE")"
            cp "$PROD/fragment.py" "$AREAS/$release/src/$MCGEN_FRAGMENT_MODULE"
            (cd "$AREAS/$release/src" && export SCRAM_ARCH=$arch && eval "$(scram runtime -sh)" \
                && scram b -j 4) >> "$WORK/setup_$release.log" 2>&1 || fail "building the fragment"
        fi
    fi
    step_events=-1
    [ "$index" = 1 ] && step_events=$EVENTS
    (cd "$AREAS/$release/src" && export SCRAM_ARCH=$arch && eval "$(scram runtime -sh)" && cd "$WORK" \
        && MCGEN_STEP_CFG="$PROD/cfg/$cfg" MCGEN_JOB=$JOB MCGEN_EVENTS=$step_events \
           MCGEN_STEP=$index MCGEN_SEED=$MCGEN_SEED MCGEN_THREADS=$MCGEN_THREADS \
           cmsRun "$PROD/bin/wrap_cfg.py") > "$WORK/$name.log" 2>&1
    code=$?
    # TrigReport's event counts and GenXSecAnalyzer's numbers, once each (the log can
    # hold GenXSecAnalyzer's printout twice)
    grep -h -E "^TrigReport Events total|^(Before|After) (Filter|matching): total cross section|^Matching efficiency|^(HepMC f|F)ilter efficiency|^After filter: final cross section" "$WORK/$name.log" \
        | awk '!seen[$0]++' | sed "s/^/$name: /" >> "$SUMMARY"
    if [ $code -ne 0 ] || [ ! -s "$WORK/$output" ]; then
        tail -40 "$WORK/$name.log" | sed "s/^/$name log: /" >> "$SUMMARY"
        fail "$name: cmsRun exited with $code"
    fi
    if [ -n "$input" ] && [ -f "$WORK/$input" ]; then
        in_tier=$(for e in "${MCGEN_STEPS[@]}"; do IFS='|' read -r i n t r a c x o <<< "$e"; [ "$o" = "$input" ] && echo "$t"; done)
        case " $MCGEN_KEEP " in *" $in_tier "*) ;; *) rm -f "$WORK/$input" ;; esac
    fi
    note STEP "$index" "$name" done
done
for entry in "${MCGEN_STEPS[@]}"; do
    IFS='|' read -r index name tier release arch cfg input output <<< "$entry"
    case " $MCGEN_KEEP " in *" $tier "*) ;; *) continue ;; esac
    [ -s "$WORK/$output" ] || fail "no $output to copy"
    dest="${MCGEN_OUTPUT%/}/$tier/${MCGEN_NAME}_${tier}_$(printf %04d "$JOB").root"
    case "$MCGEN_OUTPUT" in
        root://*)
            rest=${dest#root://}
            xrdfs "root://${rest%%/*}" mkdir -p "$(dirname "/${rest#*/}")" > /dev/null 2>&1
            xrdcp -f -s "$WORK/$output" "$dest" || fail "xrdcp to $dest" ;;
        *) mkdir -p "$(dirname "$dest")" && cp "$WORK/$output" "$dest" || fail "copy to $dest" ;;
    esac
    echo "output $tier $dest" >> "$SUMMARY"
    note COPIED "$tier" "$dest"
done
note DONE
echo DONE >> "$SUMMARY"
'''

DRIVER = r'''#!/bin/bash
# driver.sh local|condor JOB...: configure the production once, then run the jobs one
# after another here, or submit condor/job.sub, which queues the same jobs. Started in
# the background by RunMCProduction; progress goes to driver.status, output to driver.log.
PROD=$(cd "$(dirname "$0")/.." && pwd)
MODE=$1; shift
note() { echo "$(date +%s) $*" >> "$PROD/driver.status"; echo "[mcgen] $*"; }
note DRIVER "$MODE" start $$
if [ ! -f "$PROD/cfg/.configured" ]; then
    note CONFIGURE start
    if bash "$PROD/bin/in_el8.sh" bash "$PROD/bin/configure.sh" > "$PROD/configure.log" 2>&1 \
            && [ -f "$PROD/cfg/.configured" ]; then
        note CONFIGURE done
    else
        note CONFIGURE failed; note DRIVER failed; exit 1
    fi
fi
if [ "$MODE" = local ]; then
    for job in "$@"; do
        note JOB "$job" start
        if bash "$PROD/bin/in_el8.sh" bash "$PROD/bin/run_job.sh" "$job" > "$PROD/jobs/job_$(printf %04d "$job").log" 2>&1; then
            note JOB "$job" done
        else
            note JOB "$job" failed
        fi
    done
else
    cd "$PROD/condor" || exit 1
    if condor_submit job.sub > submit.log 2>&1; then
        grep -o "submitted to cluster [0-9]*" submit.log | grep -o "[0-9]*$" > cluster
        echo "$(cat cluster) $*" >> clusters        # every submission: the cluster, its jobs
        for job in "$@"; do rm -f "out/summary_$job.txt"; done     # an earlier attempt's
        note SUBMIT done "$(cat cluster)"
    else
        note SUBMIT failed; note DRIVER failed; exit 1
    fi
fi
note DRIVER done
'''

CONDOR_JOB = r'''#!/bin/bash
# condor_job.sh JOB: what a batch node runs. The configurations, the fragment and the
# scripts were transferred into this directory; the releases are set up here.
JOB=$1
mkdir -p bin && cp run_job.sh wrap_cfg.py production.env bin/
export MCGEN_PROD=$PWD MCGEN_WORK=$PWD/job_$JOB MCGEN_AREAS=$PWD/cmssw
touch "summary_$JOB.txt"
bash bin/run_job.sh "$JOB"
code=$?
cp "$MCGEN_WORK/summary.txt" "summary_$JOB.txt" 2>/dev/null
exit $code
'''

WRAP_CFG = r'''# wrap_cfg.py: run one step's configuration (MCGEN_STEP_CFG) for one job: its number of
# events, seeds of its own for every module, a luminosity block of its own, the threads;
# and, after the generator, the GenXSecAnalyzer, which prints the cross section and the
# generator filter's efficiency at the end of the job.
import os
import random

exec(open(os.environ["MCGEN_STEP_CFG"]).read())          # defines `process` and `cms`

_events = int(os.environ.get("MCGEN_EVENTS", "-1"))
process.maxEvents.input = cms.untracked.int32(_events)
if _events > 0 and hasattr(process, "externalLHEProducer"):   # a gridpack makes this job's events
    process.externalLHEProducer.nEvents = cms.untracked.uint32(_events)
process.options.numberOfThreads = cms.untracked.uint32(int(os.environ.get("MCGEN_THREADS", "1")))
process.options.numberOfStreams = cms.untracked.uint32(0)
process.options.wantSummary = cms.untracked.bool(True)    # "TrigReport Events total = ..."
if process.source.type_() == "EmptySource":
    process.source.firstLuminosityBlock = cms.untracked.uint32(int(os.environ.get("MCGEN_JOB", "0")) + 1)
from IOMC.RandomEngine.RandomServiceHelper import RandomNumberServiceHelper
_helper = RandomNumberServiceHelper(process.RandomNumberGeneratorService)
_random = random.Random("{}:{}:{}".format(os.environ.get("MCGEN_SEED", "1"),
                                          os.environ.get("MCGEN_STEP", "1"),
                                          os.environ.get("MCGEN_JOB", "0")))
_helper.insertSeeds(*[_random.randint(1, 900000000) for _ in range(_helper.countSeeds())])
if hasattr(process, "generator"):
    process.mcgenXsec = cms.EDAnalyzer("GenXSecAnalyzer")
    process.mcgenXsecPath = cms.EndPath(process.mcgenXsec)
    if process.schedule is not None:
        process.schedule.append(process.mcgenXsecPath)
'''

SCRIPTS = {"in_el8.sh": IN_EL8, "configure.sh": CONFIGURE, "run_job.sh": RUN_JOB,
           "driver.sh": DRIVER, "condor_job.sh": CONDOR_JOB, "wrap_cfg.py": WRAP_CFG}


def condor_submit_file(directory: Path, description: Dict[str, Any], proxy: str,
                       jobs: Optional[List[int]] = None) -> str:
    """The HTCondor submit file of a production, for the LPC (an EL8 slot per job)."""
    d = str(directory)
    threads = description["threads"]
    selected = jobs if jobs is not None else list(range(len(description["jobs"])))
    queue = f"queue Job in ({', '.join(str(j) for j in selected)})"
    return "\n".join([
        "# written by HEPTAPOD's RunMCProduction",
        "universe                = vanilla",
        f"executable              = {d}/bin/condor_job.sh",
        "arguments               = $(Job)",
        f"initialdir              = {d}/condor/out",
        f"output                  = {d}/condor/logs/job_$(Job).out",
        f"error                   = {d}/condor/logs/job_$(Job).err",
        f"log                     = {d}/condor/logs/condor.log",
        "should_transfer_files   = YES",
        "when_to_transfer_output = ON_EXIT",
        f"transfer_input_files    = {d}/bin/run_job.sh,{d}/bin/wrap_cfg.py,{d}/bin/production.env,{d}/fragment.py,{d}/cfg",
        "transfer_output_files   = summary_$(Job).txt",
        f"x509userproxy           = {proxy}",
        f"request_cpus            = {threads}",
        f"request_memory          = {2000 + 1000 * threads}",
        f'+DesiredOS              = "{campaigns.DESIRED_OS}"',
        "+McgenJob               = $(Job)",
        queue,
        "",
    ])
