# SIDM / Lepton-Jet example

Launches a coding agent on the Lepton-Jet and coffea tools, wired to a NanoAOD
sample, with the `coffea`, `scaleout`, `cmssw` and `sidm` skills available.

## Run it

From the repository root:

```bash
python examples/sidm/launch.py --harness claude-code     # or codex, opencode
```

That creates a numbered sandbox under `examples/sidm/`, activates the
`coffea`, `leptonjets`, `analysis` and `cmssw` bundles, links the sample in as
`data/` and the repo's `scripts/` in, writes the system prompt as the harness's
instruction file, wires the MCP server, and starts the agent.

`--no-launch` sets the sandbox up without starting anything.

## Point it at your own sample

```bash
SIDM_DATA_DIR=/eos/uscms/store/group/.../MyNanoAOD \
    python examples/sidm/launch.py --harness claude-code
```

The default is the LPC SIDM signal sample
(`Bs -> 2 dark photons`, LLPnanoAOD, 47 files).

## Why the data is symlinked

Every HEPTAPOD tool confines file access to `base_directory` — the sandbox.
An absolute `/eos/...` path handed to a tool is refused. The launcher links the
sample in as `data/`, and because the containment check uses `os.path.abspath`
(which does not resolve symlinks), `data/x.root` passes the check and still
reads the real file on EOS.

## Codex: turn the command sandbox off for this workspace

Codex runs model-generated shell commands inside its own Linux sandbox. Under
the default `workspace-write` policy, **uproot's basket reads hang** — the
process never returns and never errors.

This was isolated by elimination. Inside the sandbox these all work: plain
Python, reading the file's bytes, threading, `import uproot`, `uproot.open()`
and `num_entries`, and an `lzma` compress/decompress round trip. What hangs is
`branch.array()`. It is not the EOS mount and not the `data/` symlink — a real
file copied into the workspace hangs identically — and
`sandbox_permissions=["disk-full-read-access"]` does not help.

The same read takes **0.7 s** unsandboxed.

Launch codex with the sandbox off:

```bash
codex --sandbox danger-full-access
```

or mark the sandbox directory trusted in `~/.codex/config.toml` (trust does not
appear to inherit from a parent directory, so the sandbox path needs its own
entry):

```toml
[projects."/path/to/heptapod/examples/sidm/sandbox005"]
trust_level = "trusted"
```

Judge that for yourself: it lets the agent run shell commands unsandboxed. The
work here is read-only against your own files, and HEPTAPOD's own tools still
confine their file access to `base_directory` regardless — but the shell is no
longer restricted.

Claude Code is unaffected; only codex's command sandbox shows this.


## Scaling over many files

Inside the sandbox:

```bash
PY=$(grep -h '^python_path:' ~/.toolbase/cache/heptapod/*/.install_meta.yaml | head -1 | awk '{print $2}')
$PY scripts/lpc_scaleout.py local   --analysis scripts/example_analysis_sidm.py \
    --directory data --max-files 2 --workdir work/dev
$PY scripts/lpc_scaleout.py submit  --analysis scripts/example_analysis_sidm.py \
    --directory data --files-per-job 8 --workdir work/full --dry-run
$PY scripts/lpc_scaleout.py submit  --analysis scripts/example_analysis_sidm.py \
    --directory data --files-per-job 8 --workdir work/full
$PY scripts/lpc_scaleout.py status  --workdir work/full
$PY scripts/lpc_scaleout.py merge   --workdir work/full
```

HTCondor worker nodes cannot see `/uscms_data` or the EOS FUSE mount, so the
runner converts inputs to `root://cmseos.fnal.gov/...`, transfers the runner and
your analysis module in, and requests the coffea container from cvmfs. A grid
proxy is required: `voms-proxy-init -voms cms -valid 192:00`.

See the `scaleout` skill for the full picture.

## Reference numbers

On the default sample (47 files, 208,564 events) the example analysis gives:

| Quantity | Value |
|----------|-------|
| dark photons | 417,128 (exactly 2.000 per event) |
| DSA muons | 612,873 |
| PAT muons | 400,894 |
| DSA matched to PAT within dR < 0.1 | 312,968 (51.1%) |
| files failed | 0 |

Six HTCondor jobs, about two minutes wall clock.
