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

To be filled in from the runs at the LPC.
