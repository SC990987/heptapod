# Analysis framework example

An agent that writes a python analysis framework for a new analysis, and then
extends it on request.

## What this toolkit does

Given a NanoAOD-like ROOT file and a description of the analysis, the
`framework` bundle writes a [coffea](https://coffea-hep.readthedocs.io)
analysis package:

```
my_analysis/
├── my_analysis/
│   ├── tools/          the engine: processor, selection, histogram, cutflow, utilities, check
│   ├── definitions/    what exists: objects.py, cuts.py, hists.py, weights.py
│   ├── configs/        what is used: selections.yaml, hist_collections.yaml, samples/, ...
│   ├── data/           golden JSONs, correction files
│   ├── scripts/        run_analysis, add_samples, merge_outputs
│   ├── test_notebooks/ exercising the machinery
│   └── studies/        one notebook per physics study
└── README.md, setup.py, requirements.txt
```

The engine is the same for every analysis and contains no physics. The analysis
lives in `definitions/` (named objects, cuts and histograms, as small python
functions) and `configs/` (which of them each selection and histogram collection
uses). Running the analysis is naming selections and histogram collections.

Extending it is asking for it. Once the framework exists, "I want a histogram of the
electron pT" or "add a cut on the jets, in a new channel" needs no further tool: the
agent writes the definition in `definitions/`, names it in `configs/`, and runs the
check to show what moved. Only the parts below, which bring files of their own, go
through a tool.

Parts that only some analyses need are added on request:

| Component | Adds |
|-----------|------|
| `lepton_jets` | lepton jets clustered from the selected leptons and photons, as an object with its own cuts, histograms and a selection |
| `scaleout` | dask clients for local processes, an existing scheduler, or HTCondor at the Fermilab LPC |
| `schema` | a customised NanoAOD schema: behaviours for extra collections, cross-references, hidden branches |
| `chain_report` | a regression report over small committed test files, with a GitHub workflow |

## Quick start

### 1. Install the toolkit

From a clone of this repo (see the top-level [README](../../README.md) for the
full install story):

```bash
pip install toolbase
tb install . --bundle framework --bundle coffea
```

`tb install` builds an isolated environment for the toolkit and resolves the
bundles' dependencies (coffea, awkward, uproot, hist, fastjet, ...) into it. The
framework's self-check runs in that environment by default. To run it in an
environment of your own instead (an existing analysis environment, say), either
set it for one launch of this example,

```bash
ANALYSIS_PYTHON=/path/to/env/bin/python python examples/framework/launch.py --harness claude-code
```

or for every project:

```bash
tb config set --user heptapod analysis_python /path/to/env/bin/python
```

Without `--user`, `tb config set` writes to the project it is run in. Run from this
repository that is the repository's own `.toolbase/`, which the sandboxes the
launcher creates do not read.

### 2. Launch an agent on it

```bash
ANALYSIS_DATA=/path/to/nanoaod_file_or_dir \
    python examples/framework/launch.py --harness claude-code    # or codex, opencode
```

The launcher creates a numbered sandbox under `examples/framework/`, writes the
system prompt as the file your harness reads, activates the `framework` and
`coffea` bundles, links `ANALYSIS_DATA` into the sandbox as `data/`, wires the
MCP server and starts the agent. Pass `--no-launch` to set the sandbox up and
stop.

Two things it does differently from the other examples, because these tools run
an analysis rather than a quick calculation:

- **Time per tool call.** toolbase cuts a tool call at 60 s. The launcher wires
  the server as `toolbase serve --call-timeout 600` (and tells Codex, which has a
  limit of its own). If you connect a harness by hand with `tb connect`, the 60 s
  limit applies: `CheckAnalysisFramework` then has to stay under it, which its
  default `timeout_s` of 50 respects.
- **Warm imports.** It imports coffea, awkward, uproot and hist once before the
  agent starts, with the toolkit's interpreter and with `ANALYSIS_PYTHON` if that
  is set, so that the first tool call does not pay for a cold cache. If an import
  fails, it says so: that environment then lacks the libraries, and the tools that
  run with it will fail.

The data is linked rather than referenced because every HEPTAPOD tool confines
file access to the sandbox: an absolute path outside it is refused, while
`data/x.root` passes the check and still reads the real file. The sample
configuration the agent writes records the real location, so the framework keeps
working once it is moved out of the sandbox. Links only help for reading: the
framework itself must be written inside the sandbox, and a project directory
that is a link leading out of it is refused.

`ANALYSIS_DATA` may be one file or a directory. A directory is linked as a whole,
sub-directories included; the number of ROOT files the launcher prints counts
only those directly inside it.

Without `ANALYSIS_DATA` the agent can still scaffold a framework (standard
NanoAOD is assumed), but nothing can be run until a file is there.

### 3. Ask for a framework

See `task_prompt.md` for prompts to start from. The system prompt is at
`prompts/system_prompt.md`.

## Bundles: `framework`, `coffea`

| Tool | Purpose |
|------|---------|
| InspectFile | What a NanoAOD-like file contains, and what will need care when coffea reads it |
| ScaffoldAnalysisFramework | Write the framework, matched to a representative file |
| CheckAnalysisFramework | Static consistency of configs and definitions, the cuts each channel resolves to, and a short run with cutflows, object counts, empty histograms and warnings |
| AddFrameworkComponent | Add `lepton_jets`, `scaleout`, `schema` or `chain_report` |

Two skills come with them: `framework` (how the generated package is organised
and how to change it) and `coffea` (the columnar idioms that go into cuts,
objects and histograms).

## Taking the result with you

The generated directory is self-contained: it does not import HEPTAPOD.

```bash
cp -r examples/framework/sandbox001/my_analysis ~/my_analysis && cd ~/my_analysis
git init
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
python -m my_analysis.tools.check --sample <NAME>
```

## Status

The `framework` bundle is new. It was written and statically checked in an
environment without coffea, so the generated engine has **not yet been run against
the real libraries**, and this launcher has never been started. Expect the first
session to turn up problems, and treat a failing check on a freshly scaffolded
project as a possible fault of the generator, not only of the analysis.
[tools/framework/README.md](../../tools/framework/README.md#verification-status)
lists what was and was not checked and the commands that check it.

This example has no recorded transcript yet.
