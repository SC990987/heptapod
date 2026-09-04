---
name: scaleout
bundle: coffea
description: Process many ROOT files at the Fermilab LPC — locally for development, or fanned out over HTCondor with the coffea container from cvmfs. Covers file discovery over xrootd, chunking, resumable submission, monitoring and merging with scripts/lpc_scaleout.py, plus the LPC-specific constraints that break naive submissions (worker nodes cannot see /uscms_data or the EOS FUSE mount). Use whenever the user wants to run over a whole dataset, many files, a full sample, or mentions HTCondor, condor_submit, batch jobs, dask, LPC, cmslpc, EOS, xrootd, or scaling up an analysis.
---

# Scaling out at the LPC

`scripts/lpc_scaleout.py` runs a coffea analysis over many files: locally for
development, or over HTCondor for a full sample. It carries no physics — you
supply the analysis, it handles discovery, chunking, submission, resumption and
merging.

## The four LPC facts that determine the design

All four were confirmed by probe jobs on the LPC pool. They are why a naive
submission fails.

1. **Jobs run inside an apptainer container with `--contain`.** The job's home
   is a scratch directory; the outside filesystem is not there.
2. **`/uscms_data` (nobackup) is NOT visible on worker nodes.** Your venv, your
   analysis file and your scripts must be *transferred in*, not referenced by
   path.
3. **The `/eos/uscms` FUSE mount is NOT readable on workers.** Data must be read
   over **xrootd**: `root://cmseos.fnal.gov//store/...`. Interactive nodes can
   use either.
4. **cvmfs IS available**, including
   `/cvmfs/unpacked.cern.ch/registry.hub.docker.com/coffeateam/coffea-almalinux9:2026.7.0-py3.12`
   — which is how the worker gets coffea. Request it with `+SingularityImage`.

The runner does all of this for you: it converts paths to xrootd, transfers the
runner plus your analysis module plus the chunk list, and asks for the coffea
image.

## The analysis contract

A module with one required function:

```python
def process(events, meta) -> dict:
    """Return a dict of hist objects / numbers / arrays. Merged by addition."""
```

and optionally:

```python
def open_events(path):
    """Override how one file is opened -- needed for LLPnanoAOD and other
    custom formats. See the `coffea` skill's pitfalls."""
```

Results merge by `+`, so `hist` objects, counters and numpy arrays all combine
correctly across files and chunks. `scripts/example_analysis_sidm.py` is a
worked example against a real LLPnanoAOD signal sample.

## Where the runner lives

`scripts/lpc_scaleout.py`, in the HEPTAPOD checkout. Inside a sandbox made by
`examples/sidm/launch.py` it is linked in, so `scripts/lpc_scaleout.py` works
from the sandbox too, and the sample is linked as `data/`.

Use the interpreter toolbase built for the toolkit -- it has coffea and
everything around it. Read its path from the install metadata:

```bash
PY=$(grep -h '^python_path:' ~/.toolbase/cache/heptapod/*/.install_meta.yaml \
     | head -1 | awk '{print $2}')
```

That is the **toolkit's own** interpreter, which has coffea. The `python` next
to the `toolbase` executable is a different venv -- it has toolbase but **not**
coffea, so do not use it.

## The workflow

```bash
S=scripts/lpc_scaleout.py
DIR=data                 # or an absolute /eos/... path outside a sandbox

# 1. what is there?
$PY $S discover $DIR

# 2. develop on two files, locally -- always do this first
$PY $S local --analysis my_analysis.py --directory $DIR \
     --max-files 2 --workdir work/dev

# 3. inspect the generated submit file before sending anything
$PY $S submit --analysis my_analysis.py --directory $DIR \
     --files-per-job 8 --workdir work/full --dry-run

# 4. submit for real
$PY $S submit --analysis my_analysis.py --directory $DIR \
     --files-per-job 8 --workdir work/full

# 5. watch it
$PY $S status --workdir work/full

# 6. merge when done
$PY $S merge --workdir work/full
```

`run` does 4-6 in one call, polling until finished.

Batch outputs can be large: keep `--workdir` on a filesystem with room (on the
LPC, anywhere but `$HOME`).

**Everything is resumable.** A chunk whose `parts/part_N.pkl` exists is skipped,
so re-running `submit` after a partial failure only redoes what is missing. One
bad file inside a chunk is recorded in the report rather than losing the chunk.

## Datasets by name, not by path

`configs/datasets.yaml` catalogs the sample area: name -> path, file count, and
the physics parameters parsed out of the directory name. Regenerate it after new
samples land:

```bash
python scripts/build_dataset_catalog.py <sample root> --tier LLPnanoAODv2 --era 2018
```

Then select by physics instead of pasting paths:

```bash
$PY $S datasets --mbs 500 --mdp 0.25          # list matches
$PY $S datasets --match 4Mu --link data/      # symlink matches, for sandboxed tools
$PY $S local  --dataset BsTo2DpTo2Mu2e_MBs-500_MDp-0p25_ctau-0p4 --max-files 2 --workdir work/dev
$PY $S submit --dataset BsTo2DpTo2Mu2e_MBs-500_MDp-0p25_ctau-0p4 --workdir work/full
```

`--dataset` records the name, path, era and parameters in the run's
`manifest.json` and in the merged output, so a result stays identifiable months
later. `--directory` and `--filelist` still work and record what they were given.

Note the catalog is for the **runner**, which is a plain script and can read any
path. HEPTAPOD's MCP tools still confine themselves to `base_directory`, so to
hand one of them a dataset use `datasets --link data/` first and refer to it as
`data/<name>/file.root`.

## What a run leaves behind

```
workdir/
  manifest.json     what was run: dataset, params, analysis, file/chunk counts, UTC
  chunks/           the file list per job (xrootd URLs)
  logs/             job_N.out / job_N.err / cluster.log
  parts/part_N.pkl  one accumulator per chunk -- the resume record
  merged.pkl        the combined accumulator + report + provenance
  summary.json      everything except the histograms, readable without Python
```

It is small: a completed 47-file run is **~170 kB in total**, dominated by the
condor logs. The histograms are binned, so output size is set by your binning,
not by the number of events -- 47 files and 4700 files cost the same.

`parts/` is the resume record: re-running `submit` only queues chunks whose part
is missing. **Delete `parts/` when you change the analysis**, or stale results
merge into the new ones.

For plots and a durable archive, pass `merged.pkl` to
`scripts/plot_results.py plot`, which writes PNGs, `summary.json` and a
`histograms.root` readable with plain uproot.

## Working agentically

Submit, then poll `status` rather than blocking. `status` reports completed
chunks, the condor totals and any held jobs with their hold reason. When jobs
go held, read `logs/job_N.err` — the cause is nearly always a missing input
file or an exception inside `process`.

Check the report after merging: `merged.pkl` carries `report.files_failed`,
a dict of file → error. An empty dict means every file was processed.

Sizing: ~5-10 files per job is a good default. Fewer, larger jobs waste time on
retries; many tiny jobs spend all their time in the queue.

See `references/pitfalls.md` for the failure modes and `references/dask.md`
for when a dask cluster is the better tool.
