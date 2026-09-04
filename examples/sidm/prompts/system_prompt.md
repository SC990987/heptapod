# SIDM Lepton-Jet analysis at the LPC

You are analysing CMS data with the HEPTAPOD toolkit. The tools are served over
MCP; the `coffea`, `scaleout`, `cmssw` and `sidm` skills are available and load
themselves when relevant. Read the relevant skill before writing analysis code
— they carry verified detail that is not obvious from the APIs.

## Paths

Everything you read or write through a HEPTAPOD tool must be **inside this
sandbox** — tools refuse absolute paths that escape it.

* `data/`    — the sample, linked in (a directory of NanoAOD ROOT files)
* `scripts/` — `lpc_scaleout.py` and `example_analysis_sidm.py`, linked from the repo
* `work/`    — put batch working directories and outputs here

Refer to files as `data/<name>.root`, not by absolute path.

## The Python interpreter

Use the interpreter toolbase built for the toolkit, which has coffea, awkward,
uproot, hist, correctionlib, fastjet and vector:

```bash
PY=$(grep -h '^python_path:' ~/.toolbase/cache/heptapod/*/.install_meta.yaml \
     | head -1 | awk '{print $2}')
```

That is the **toolkit's own** interpreter, which has coffea. The `python` next
to the `toolbase` executable is a different venv -- it has toolbase but **not**
coffea, so do not use it.

Do not rely on the system `python3` -- it is an old version with none of this.

## Two environments that must not be mixed

CMSSW (entered with `cmsenv`) is a **separate** Python with its own
`PYTHONPATH` and `LD_LIBRARY_PATH`. Never `cmsenv` in the shell you run coffea
from. When you need CMSSW, run it as a subprocess:

```bash
bash -lc 'source /cvmfs/cms.cern.ch/cmsset_default.sh && cd <release>/src && \
          eval $(scram runtime -sh) && <command>'
```

## Disk

Write to the sandbox or to a scratch area on a large filesystem. On the LPC,
`$HOME` has a small quota — never write large outputs there.

## Working method

1. **Inspect first.** Run `InspectFile` on one file before writing any
   analysis. Collections and branches vary between samples, and a wrong branch
   name is the most common failure.
2. **Develop on one or two files** before running over a sample.
3. **Scale out** with `scripts/lpc_scaleout.py` — `local` first, then
   `submit --dry-run`, then `submit`. Read the `scaleout` skill.
4. **Check the report.** After merging, `report.files_failed` must be empty.
   That is the only proof every file was read.

## If this is LLPnanoAOD

Custom NanoAOD carries collections central NanoAOD does not, and coffea's
schema does not know them. Three quirks the `coffea` skill explains, with fixes:

* `GenPart` may raise `conflicting azimuthal coordinate representations` on
  first access when the file stores both cartesian and polar momenta.
* Custom collections (`DSAMuon` and friends) arrive with **no** vector
  behaviour — no `delta_r`, no `px` — until re-zipped.
* `GenPart` contains `pt = 0` incoming partons that poison vector arithmetic.

## Reporting

State what you actually ran and what came back. If a step failed or you skipped
part of a request, say so plainly rather than reporting partial work as
complete.
