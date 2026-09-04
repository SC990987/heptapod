# LPC scale-out pitfalls

Every item was hit and confirmed on the LPC pool.

## Worker nodes see almost nothing of your machine

Probe job output, verbatim:

```
--- can the worker see /uscms_data/d3/<user>? ---   NO
--- the toolkit venv?                            ---   NO
--- /eos/uscms via FUSE?                         ---   NO
--- xrootd to cmseos?                            ---   YES
--- cvmfs?                                       ---   YES
```

So: **never** put a `/uscms_data` path in a submit file and expect the job to
read it. Transfer inputs with `transfer_input_files`, read data over xrootd,
and get software from cvmfs.

## `condor_submit` has no shebang

The LPC `condor_*` executables begin with

```
exec env LD_LIBRARY_PATH='' /usr/bin/python3 -Ex "$0" "$@"
```

and no `#!` line. They work from a shell, which falls back to `/bin/sh`, but
`subprocess.run(["condor_submit", ...])` fails with

```
OSError: [Errno 8] Exec format error: 'condor_submit'
```

Invoke them through a shell (`shell=True`, or `bash -lc`).

## Getting coffea onto the worker

CMSSW on cvmfs ships uproot, awkward, hist, vector and correctionlib — but
**not coffea**. The clean route is the container:

```
+SingularityImage = "/cvmfs/unpacked.cern.ch/registry.hub.docker.com/coffeateam/coffea-almalinux9:2026.7.0-py3.12"
```

Match the image version to your interactive environment so results are
reproducible. `ls /cvmfs/unpacked.cern.ch/registry.hub.docker.com/coffeateam/`
lists what is available.

Do **not** try to ship a virtualenv: venvs are not relocatable, and a coffea
env is ~1 GB, far past a sensible `transfer_input_files`.

## EOS paths must become xrootd URLs

```
/eos/uscms/store/group/...      ->  root://cmseos.fnal.gov//store/group/...
```

Note the **double slash** after the redirector. The runner's `to_xrootd()`
handles the conversion; if you write submit files by hand, do it explicitly.

Set generous xrootd timeouts in the job — remote reads occasionally stall:

```bash
export XRD_REQUESTTIMEOUT=600
export XRD_REDIRECTLIMIT=5
```

## A grid proxy is needed for remote reads

`voms-proxy-init -voms cms -valid 192:00`. An expired proxy still shows a
subject under `voms-proxy-info`, so check for `Certificate is expired` in the
job's stderr rather than trusting that output.

## One bad file should not lose a chunk

A single corrupt or unreachable file in an 8-file chunk would otherwise waste
the whole job. The runner catches per-file exceptions and records them in
`report.files_failed`, so the rest of the chunk still produces output. **Check
that dict after merging** — an empty `files_failed` is the only proof that
everything was read.

## Held jobs

```bash
condor_q -hold -af ClusterId HoldReason
```

Common reasons: the output file was never created (an exception in `process` —
see `logs/job_N.err`), memory exceeded (raise `--memory`), or the image path
was wrong.

## Resumption is by output file, not by job id

The runner treats `parts/part_N.pkl` as the record of a finished chunk. If you
change your analysis, **delete the parts directory** or you will merge stale
results with new ones.

## Sizing

`request_memory = 4000` is enough for typical NanoAOD columnar work; a wide
collection or a large `ak.combinations` can exceed it. If jobs are held for
memory, raise it rather than shrinking the chunk — memory is per event batch,
not per file.
