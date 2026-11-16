# CMS simulation tools (`mcgen` bundle)

Four tools that produce CMS simulation for any process and a Run 3 run period, from the
generator to NanoAOD, with the official campaign's releases, conditions, HLT menu and
premixed pileup; here or on HTCondor at the Fermilab LPC:

| Tool | Module | Does |
|------|--------|------|
| `ListMCCampaignsTool` | `campaigns.py` | The run periods and, step by step, what they are made with |
| `PrepareMCProductionTool` | `prepare.py` | Writes a production for a process and a run period; runs nothing |
| `RunMCProductionTool` | `run.py` | Runs it here or submits it to HTCondor, in the background |
| `MCProductionStatusTool` | `status.py` | Where it stands; the cross section and filter efficiency; the inputs for `ScaffoldAnalysisFramework` |

The `mcgen` skill (`skills/mcgen/`) teaches an agent the workflow, from writing the
generator settings of any process to building an analysis framework on the sample.
`examples/mcgen/` launches an agent with these tools and the framework tools.

## The chain

| Run period | Campaign | GEN-SIM | DIGI-HLT, RECO | MiniAOD, NanoAOD | HLT menu |
|------------|----------|---------|----------------|------------------|----------|
| 2022 | Run3Summer22 | CMSSW_12_4_11_patch3 (LHE: 12_4_19) | 12_4_11_patch3 | 13_0_13 | 2022v12 |
| 2022EE | Run3Summer22EE | 12_4_11_patch3 (LHE: 12_4_19) | 12_4_11_patch3 | 13_0_13 | 2022v14 |
| 2023 | Run3Summer23 | 13_0_13 (LHE: 13_0_17) | 13_0_14 | 13_0_14 | 2023v12 |
| 2023BPix | Run3Summer23BPix | 13_0_13 (LHE: 13_0_17) | 13_0_14 | 13_0_14 | 2023v12 |
| 2024 | RunIII2024Summer24 | 14_0_19 | 14_0_19 | 15_0_2 | 2024v14 |

Every step's release, SCRAM_ARCH and cmsDriver options are those of a real McM request
of the campaign; `_campaigns.py` names each one, and `ListMCCampaigns` reports them.
Only options that belong to one job were left out: file names, numbers of events,
threads, seeds. Three things differ from McM's own setups, on purpose:

- **Flat NanoAOD.** McM's production writes EDM NanoAOD (`NANOEDMAODSIM`) and merges
  it later; here the NanoAOD step writes `NANOAODSIM`, the flat TTree coffea reads.
- **No pileup on request.** `pileup="none"` drops DATAMIX, the premix library and
  the `premix_stage2` modifier from the DIGI step, and writes RAWSIM.
- **Generator only on request.** `final_tier="GEN"` runs GEN (or LHE,GEN) without
  the detector simulation, to measure a filter's efficiency and the cross section.

Every Run 3 step runs in an el8 container in McM's setups, and so here:
`cmssw-el8` on an el9 machine, an EL8 slot (`+DesiredOS = "EL8"`) on the LPC's
HTCondor.

## A production

`PrepareMCProduction` writes a directory, and nothing else:

```
<name>/
  production.json      the request, the chain, every step's cmsDriver command
  fragment.py          the generator fragment
  bin/production.env   the same as shell variables
  bin/configure.sh     releases under cmssw/, the fragment built, cfg/ written by cmsDriver --no_exec
  bin/run_job.sh       one job: every step, then the kept tiers copied to the output
  bin/driver.sh        configure once, then run jobs here or condor_submit
  bin/condor_job.sh    what a batch node runs (releases set up there)
  bin/in_el8.sh        directly on el8, else in cmssw-el8
  bin/wrap_cfg.py      a step for one job: events, seeds, luminosity block, threads, GenXSecAnalyzer
```

`RunMCProduction` starts `bin/driver.sh` in a session of its own and returns: the tools
never wait for CMSSW, so a tool call stays short whatever toolbase's time limit is.
The driver configures the production once: it sets up the releases, builds the
fragment, writes every step's configuration with `cmsDriver.py --no_exec`, and, for
premixed pileup, asks DAS for the premix library a single time. Then it runs the jobs
one after another here, or submits them. A batch job receives the configurations
(cmsDriver copies the generator into them), sets the releases up in its own directory,
runs, and copies its outputs to the `root://` output with `xrdcp`.

Each job's steps get seeds of their own, drawn from the production's seed, the step and
the job; and a luminosity block of its own, so events of different jobs never repeat.
`GenXSecAnalyzer` runs after the generator, and `MCProductionStatus` adds up what it
prints over the jobs: the cross section before the filter, the filter efficiency, and
the sample's cross section.

Outputs: `<output>/<TIER>/<name>_<TIER>_<job>.root`.

## How the code here is organised

| File | Role |
|------|------|
| `_campaigns.py` | The run-period table (generated from McM) and the chains built from it |
| `_fragment.py` | Pythia 8 fragments from settings, checks of given fragments, fragments of McM requests |
| `_production.py` | Writing a production: the scripts (static), `production.env`, the condor submit file |
| `_jobs.py` | Starting the driver; the grid proxy; reading status, HTCondor, outputs, GenXSecAnalyzer |
| `campaigns.py`, `prepare.py`, `run.py`, `status.py` | The tools: thin adapters that confine paths to `base_directory` and turn exceptions into formatted errors |
| `tests/test_mcgen.py` | Everything on this side, with no CMSSW and no job submitted |

## Verification

Run at the Fermilab LPC on 15 and 16 November 2026, on an el9 interactive node, with
toolbase 0.16.0 and the toolkit's environment of the `framework` bundle (Python
3.12.14, coffea 2026.11.0). The process was prompt J/psi -> mu+ mu- (Pythia 8
`Charmonium:all`, the J/psi decaying to two muons only, `PhaseSpace:pTHatMin = 8.`, a
`MCParticlePairFilter` for two opposite-sign muons), chosen only as an example. The
tools were called from python, in the order the skill gives an agent.

| What | Result |
|------|--------|
| `tests/test_mcgen.py --no-skips` | 10 passed |
| `tb validate` with the bundle | valid, 64 tools |
| `tb install -e . --bundle mcgen --bundle framework --bundle coffea` in a new toolbase home, then `launch.py --harness claude-code --no-launch` | installed; a sandbox with the three bundles and a 600 s call timeout |
| Configuring 2022, 2022EE, 2023 and 2023BPix without pileup, 2024 with premixed pileup, and an LHE production for 2023 from an official gridpack fragment | every step's configuration written by cmsDriver.py with the McM options, in the el8 container; for 2024, DAS listed the premix library (499,432 files) and 1000 were drawn |
| Generator only, 2023, 2000 events, muons of pT > 2 GeV and abs(eta) < 2.5 | filter efficiency 293/2000 = 14.65%; 3.0e6 pb before the filter, 4.39e5 pb after |
| Here, 2023, no pileup, 700 events with the same filter, to NanoAOD | 98 events through every step; `ScaffoldAnalysisFramework` and `CheckAnalysisFramework` `ok: true` on the sample |
| HTCondor, 2024, premixed pileup, one job of 70 events, output on EOS | 6 events passed the filter and went through every step in an EL8 slot; NanoAOD written to EOS with `xrdcp`; `MCProductionStatus` followed the job in HTCondor's job log and read its summary; a framework built on the file on EOS and checked, `ok: true` |
| The first prompt of `examples/mcgen/task_prompt.md`: 100 events after the filter (muons of pT > 5 GeV and abs(eta) < 2.4), 2023, no pileup, here, then the framework and its first study | 47 minutes in all. The generator-only test: 43/2000 = 2.15% (4 min). A one-job test of 233 generated events: 3 events through every step, kept out of the output, `test done` (10 min). The production: 5400 events generated, 153 (2.83%) passed the filter and went through every step (31 min; 3.05e6 pb before the filter, 8.6e4 pb after). The framework from `for_framework`: `CheckAnalysisFramework` `ok: true`, 145 of the 153 events with two muons after the starting cuts; its first study ran in 50 s, and its dimuon mass shows the J/psi peak |

Found and fixed while running: the analyzer is `GenXSecAnalyzer`; cmsDriver
configurations print no `TrigReport` unless `wantSummary` is set; `xrdcp` 5 has no
`-p` (the job makes the directory with `xrdfs mkdir -p`); the whole premix library
made an 80 MB configuration (now a random subset of 1000 files); McM can send a
fragment as a JSON string; the LPC's `condor_history` must be pointed at one schedd,
so a job that had left the queue was lost (the status now reads HTCondor's job log);
a step's log can hold GenXSecAnalyzer's printout twice (the summary keeps each line
once); with a 2% filter, a test of 5 generated events ran the chain on nothing, and a
one-job production counted as complete after its test (a test now generates about
5 / efficiency events and stays out of the output); on a hundred events the first
study's pair mass, drawn as a density with error bars, hid the peak (now events per
bin, without error bars).

Not run: Run 2 (not supported), LHE productions beyond configuring them, productions
of more than one job on HTCondor, and agent sessions (`--harness claude-code`, `codex`
or `opencode`) driving these tools: the tools were called from python, as an agent
calls them.
