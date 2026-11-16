# CMS simulation example

An agent that produces CMS simulated samples for your processes, the way the official
ones are made, and then builds the analysis framework you study them with.

## What this toolkit does

Ask for a process, a run period and a number of events. The agent writes the
generator settings (Pythia 8, with generator filters if only part of the events is
wanted; or a fragment for EvtGen, a particle gun or a MadGraph/Powheg gridpack; or the
fragment of an official sample), measures the filter efficiency and cross section with
a generator-only test, and takes the events through the run period's official chain:

```
GEN-SIM -> DIGI ... HLT (premixed pileup) -> RECO (AOD) -> MiniAOD -> NanoAOD
```

with the campaign's releases, global tags, era, beamspot, HLT menu and premix library,
taken from real McM requests:

| Run period | Campaign |
|------------|----------|
| 2022, 2022EE | Run3Summer22, Run3Summer22EE |
| 2023, 2023BPix | Run3Summer23, Run3Summer23BPix |
| 2024 | RunIII2024Summer24 |

It runs here for tests and small samples, or on HTCondor at the LPC with the files
written to EOS. When the sample is there, the same session builds an analysis
framework on it (the `framework` bundle: `examples/framework/README.md`), with the
sample and its cross section configured.

## Using it

Set up the branch and the toolkit as in `examples/framework/README.md` ("Using this
branch"), installing the `mcgen` bundle as well:

```bash
tb install . --bundle mcgen --bundle framework --bundle coffea
```

Then, in a shell where `cmsenv` was never run, from the repository root:

```bash
python examples/mcgen/launch.py --harness claude-code    # or --harness codex, --harness opencode
```

and ask, for example with one of the prompts in [`task_prompt.md`](task_prompt.md); the
first one is a complete request that runs here in about an hour. For the agent to run
a framework's notebooks, and for you to open them with the toolkit's python, add
Jupyter to the toolkit's environment (`$PY` as in `examples/framework/README.md`):

```bash
$PY -m pip install nbconvert ipykernel
```

What it needs at the LPC:

- `/cvmfs` (the releases come from there) and the `cmssw-el8` container, which the
  LPC has: every Run 3 step runs in el8, as in McM's own setups.
- For premixed pileup and for batch jobs, a grid proxy:
  `voms-proxy-init -voms cms -valid 192:00`. The agent never makes one.
- For outputs on EOS, a directory of yours there:
  `root://cmseos.fnal.gov//store/user/<you>/...`. Batch jobs write only there.

The agent confirms before it submits to HTCondor or writes to EOS. Each production is
a directory in the sandbox: `fragment.py` is the generator, `production.json` the
chain, and `bin/` the scripts that ran, so a production can be rerun by hand.

## Status

New. What has been run is listed in
[tools/mcgen/README.md](../../tools/mcgen/README.md#verification).
