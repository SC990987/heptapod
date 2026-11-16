# Analysis framework example

An agent that writes a python analysis framework for a new analysis, and then
extends it on request.

## What this toolkit does

Given your git repository, the directories your simulation and data live in, a
NanoAOD-like ROOT file and a description of the analysis, the `framework` bundle
writes a [coffea](https://coffea-hep.readthedocs.io) analysis package into the
repository (or into a new directory) and commits it:

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
│       └── obj_basics/basic_histograms.ipynb   the first: muon and electron pT, ...
└── README.md, setup.py, requirements.txt
```

The engine is the same for every analysis and contains no physics. The analysis
lives in `definitions/` (named objects, cuts and histograms, as small python
functions) and `configs/` (which of them each selection and histogram collection
uses). Running the analysis is naming selections and histogram collections.

Each directory of samples becomes a config of its own under `configs/samples/`
(`signal.yaml`: a comment, the group, the directory as its path, one sample per
sub-directory with its files and the directory they are in), with the sample names you
ask for (`ZPrime_M-500_W-5/` becomes `ZPrime_M500_W5` with the name format
`ZPrime_M{M}_W{W}`). The baseline selection applies basic placeholder cuts (muons above
5 GeV and electrons above 10 GeV with |eta| < 2.4, photons above 20 GeV, jets above
30 GeV). And `studies/obj_basics/basic_histograms.ipynb` runs the baseline over a file
of two simulated samples and one data sample and draws the muon and electron pT and
multiplicity with the samples overlaid, and the dimuon mass on a logarithmic axis. In a git repository the framework is committed
once, on the current branch, and never pushed: the answer gives the command.

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

## Using this branch

The `framework` and `coffea` bundles are on the `thrust2` branch of the fork
[SC990987/heptapod](https://github.com/SC990987/heptapod). To run them on your own
analysis:

### 1. Get the branch

On the LPC, do this in `~/nobackup` (`cd ~/nobackup` first): the launcher's sandboxes,
and everything an agent builds and runs in them, end up inside the clone, and the home
has 2 GB.

In a clone of heptapod, add the fork as a remote and check the branch out:

```bash
git remote add sc https://github.com/SC990987/heptapod.git
git fetch sc
git switch -c thrust2 --track sc/thrust2
```

Or clone the fork at that branch:

```bash
git clone -b thrust2 https://github.com/SC990987/heptapod.git
cd heptapod
```

### 2. Install

With Python 3.12 or 3.13 (on the LPC, set that up first as described below), from the
repository root:

```bash
pip install toolbase
tb install . --bundle framework --bundle coffea
```

`tb install` builds the toolkit's own environment under `~/.toolbase/`. To read
`root://` files (to inspect a file on EOS, or for the check's short run on a sample
there), add an XRootD client to it, with the toolkit's python:

```bash
PY=$(grep -h '^python_path:' ~/.toolbase/cache/heptapod/*/.install_meta.yaml | head -1 | awk '{print $2}')
$PY -m pip install fsspec-xrootd xrootd
```

`tb install .` copies the whole clone into `~/.toolbase/`, following links. Once you
have launched from a clone, its sandboxes (`examples/framework/sandbox001/`, ...) link
to your data, and installing from it again would copy that data too. To update the
toolkit later, install from a fresh clone of the branch with `--rebuild` added, then
run the `pip install` above again: the rebuild undoes it.

### On the LPC

Do all of this in a shell where `cmsenv` was never run. cmsenv's `python3` is 3.12,
but it is CMSSW's: it starts only with the `LD_LIBRARY_PATH` that cmsenv sets, so a
toolkit built on it would run in a cmsenv shell only, where CMSSW's libraries (fastjet's
among them) come before the ones the toolkit's packages bring. Without cmsenv,
`python3` is the system's 3.9, too old for toolbase. Install uv into `~/nobackup`
(nothing is added to your shell profiles), get Python 3.12 with it, and make a venv
for toolbase there too:

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL=$HOME/nobackup/uv/bin sh
export UV_PYTHON_INSTALL_DIR=$HOME/nobackup/uv/python
$HOME/nobackup/uv/bin/uv python install --no-bin 3.12
$($HOME/nobackup/uv/bin/uv python find --system 3.12) -m venv $HOME/nobackup/venvs/toolbase
source $HOME/nobackup/venvs/toolbase/bin/activate
```

The toolkit's environment takes 1.2 GB with these two bundles, and pip's cache another
0.3 GB: most of the 2 GB home. If `~/.toolbase` does not exist yet, make it a link to a
folder on nobackup, and send pip's cache there as well:

```bash
[ -e ~/.toolbase ] || { mkdir -p ~/nobackup/toolbase && ln -s ~/nobackup/toolbase ~/.toolbase; }
export PIP_CACHE_DIR=$HOME/nobackup/pip-cache
```

Then install as in step 2. In a later session,
`source $HOME/nobackup/venvs/toolbase/bin/activate` puts `tb` back on the PATH; export
`PIP_CACHE_DIR` again before a `tb install`.

Files on EOS are read over XRootD, which needs a Kerberos ticket: `klist` shows
whether you have one, `kinit $USER@FNAL.GOV` gets one. Official samples under
`/store/mc` also need a grid proxy: `voms-proxy-init -voms cms`. Until you have made
one, reading a `root://` file prints
`TLS: Unable to use cert+key file .../x509up_u<uid>; does not exist.`, because the
LPC's login profile points `X509_USER_PROXY` there; the file is read with the ticket
all the same.

### 3. Launch an agent on your analysis

From the repository root, with toolbase's venv active (so that `tb` is on the PATH) and
Claude Code installed:

```bash
python examples/framework/launch.py --harness claude-code
```

It makes a sandbox under `examples/framework/`, wires the tools into it and starts
Claude Code there (`--no-launch` stops after the sandbox). Point it at your data in
either of two ways:

- `ANALYSIS_DATA=/path/to/file_or_directory` in front of the command links a local
  ROOT file or directory into the sandbox as `data/`;
- or name `root://` directories and files in your request: the tools list and read
  them where they are.

Then ask for the framework as you would ask a colleague: where it goes (your git
repository's url), where the simulation and the data are, how the samples should be
named, and the analysis's objects, triggers and cuts. [`task_prompt.md`](task_prompt.md)
has requests to start from; "Start in your repository, from where your samples are" is
the one for a new analysis.

### 4. Afterwards

The framework is committed in your repository, in a clone inside the sandbox, and
never pushed: look at the commit, then push it with the command the agent hands over,
run from the sandbox: `git -C <clone> push -u origin HEAD`, where `<clone>` is the
clone's directory there. The package does not need HEPTAPOD: install it into any
environment that has coffea with `pip install -e .` from the repository. To have the
tools check frameworks with that environment rather than the toolkit's, set
`ANALYSIS_PYTHON=/path/to/env/bin/python` in front of the launch command, or for every
launch:

```bash
tb config set --user heptapod analysis_python /path/to/env/bin/python
```

The quick start below says more about each step.

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

Samples on EOS need no link. A `root://` directory given as a sample location is
listed with the `xrdfs` command, which the LPC has. Reading a `root://` file (to
inspect it, or for the check's short run) needs an XRootD client in the toolkit's
environment, which `tb install` does not add:

```bash
PY=$(grep -h '^python_path:' ~/.toolbase/cache/heptapod/*/.install_meta.yaml | head -1 | awk '{print $2}')
$PY -m pip install fsspec-xrootd xrootd
```

To build in a git repository the tool server needs `git`, a way to clone it (an
https url for a public repository, an ssh key for a private one: git is run
without a terminal, so nothing can ask for a password), and your git identity for
the commit. Without an identity the framework is written and staged, and the
answer says how to commit it.

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

The generated directory is self-contained: it does not import HEPTAPOD. Built in your
repository, it is a clone in the sandbox with one new commit: look at it, then push
(`git -C <clone> push -u origin HEAD`, the command the scaffold's answer gives). Built
in a new directory, copy it out:

```bash
cp -r examples/framework/sandbox001/my_analysis ~/my_analysis && cd ~/my_analysis
git init
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
python -m my_analysis.tools.check --sample <NAME>
```

The first study runs in any environment that has coffea (which brings matplotlib
and mplhep): open it in Jupyter, or run it from a shell and look at the figures in
`output/studies/`:

```bash
pip install nbconvert ipykernel
jupyter nbconvert --to html --execute my_analysis/studies/obj_basics/basic_histograms.ipynb
```

## Status

The `framework` bundle is new. It was written and statically checked in an
environment without coffea, and run at the Fermilab LPC in October and November 2026:
its tests and a generated framework against coffea 2025.5.0rc2, 2026.9.0 and
2026.11.0, on an LLP NanoAOD signal file and on synthetic files; the first study on
that file; this launcher with `--no-launch`; the tools served over MCP from a sandbox
it made; one headless agent session in such a sandbox (`claude -p`, not started by the
launcher); and the steps of "Using this branch", in a new home. No data file and no
central NanoAOD file has been run, and the launcher has not been tried with
`--harness codex` or `opencode`. Treat a failing check on a freshly scaffolded project
as a possible fault of the generator, not only of the analysis.
[tools/framework/README.md](../../tools/framework/README.md#verification-status)
lists what was and was not checked and the commands that check it.

This example has no recorded transcript yet.
