---
name: mcgen
bundle: mcgen
description: Produce CMS simulation (NanoAOD, or GEN, GEN-SIM, AOD or MiniAOD) for any physics process and a Run 3 run period (2022, 2022EE, 2023, 2023BPix, 2024) with the official campaign's releases, global tags, HLT menu and premixed pileup - from Pythia 8 settings, a generator fragment, a gridpack fragment or an official McM request - locally or on HTCondor at the LPC with outputs on EOS, and then build an analysis framework on the result. Use whenever the user asks to generate, simulate or produce MC events or samples for CMS, to make NanoAOD for a process, to measure a generator filter efficiency or cross section, or to make more events like an official sample.
---

# Producing CMS simulation

Four tools take a process to analysis-ready files:

| Tool | Does |
|------|------|
| `ListMCCampaigns` | The run periods and, step by step, the release, global tag, era, beamspot, HLT menu and premix library of each, from real McM requests |
| `PrepareMCProduction` | Writes a production: the generator fragment, every step's cmsDriver command, the job scripts. Runs nothing |
| `RunMCProduction` | Runs it here (el8 container) or submits it to HTCondor; returns at once while a background driver configures and runs it |
| `MCProductionStatus` | Configuration, every job's state, events per step, outputs, the generator's cross section and filter efficiency; and once NanoAOD exists, the inputs for `ScaffoldAnalysisFramework` |

The chain is the official one of the run period: GEN-SIM (LHE-GEN-SIM when the
generator reads a gridpack), DIGI to HLT with premixed pileup, RECO, MiniAOD, NanoAOD.
A production stops at the tier asked for. Nothing here is specific to one process:
the process is whatever the generator settings say.

## The workflow

1. **Find out what is wanted.** The process (final state, decays, phase-space cuts),
   the run period (if the user names a year, ask which part when it is split: 2022 or
   2022EE, 2023 or 2023BPix), the number of events *after* any generator filter, the
   tier (NanoAOD unless said otherwise), pileup (premixed, like the official samples,
   unless the user wants the hard process alone), where it runs (here for tests and a
   few hundred events, HTCondor for more), and where the files go (a directory here,
   or EOS: `root://cmseos.fnal.gov//store/user/<username>/...`). Ask for what you
   cannot infer; do not pick a run period or an output area on EOS for the user.
2. **Write the generator.** Choose one way (`references/generators.md` has the
   settings, filters and gridpack fragments):
   - `pythia8_settings` for anything Pythia 8 generates itself: QCD, quarkonia, W, Z,
     top, Higgs, Z'. Add `filters` when only part of the events is wanted: a filter
     makes the production efficient, and an unfiltered sample of a rare final state
     is mostly empty.
   - `mcm_prepid` to make more events of an official sample, with its own fragment.
   - `fragment` or `fragment_file` for anything else: EvtGen decays, particle guns,
     MadGraph or Powheg gridpacks. A fragment with an `ExternalLHEProducer` makes the
     chain start with LHE in the campaign's LHE release.
3. **Measure before producing.** Prepare a short production with `final_tier="GEN"`
   (no detector simulation) and a few thousand events, run it here, and read
   `generator` from `MCProductionStatus`: the filter efficiency and the cross section.
   `events` are generated events, so a production that must end with N events needs
   about N / efficiency. A filter efficiency of zero means the settings or the filter
   are wrong: fix them before going on.
4. **Test the whole chain.** Prepare the real production, then
   `RunMCProduction(where="local", jobs=[0], test_events=5)`: every step runs on five
   events in a few minutes each, and a failure shows at the step it happens. A
   premixed production needs a valid grid proxy for this already.
5. **Produce.** Small samples run here (`where="local"`). For more, `where="condor"`:
   confirm with the user first how many jobs, how many events each, and the output
   on EOS; it needs a grid proxy valid for 12 hours and a `root://` output. Choose
   `events_per_job` so that a job takes a few hours: the full chain costs of the order
   of 10 to 30 s per event per thread, more with pileup.
6. **Follow it.** `MCProductionStatus` until `complete`. A failed job reports the
   step, the error and the end of its log. Fix the cause, then run just the failed
   jobs again (`jobs=[...]`). A held condor job usually means the proxy expired or a
   job ran out of memory.
7. **Build the framework on it**, when the user wants to analyse the sample (with the
   `framework` skill): `ScaffoldAnalysisFramework` with the `sample_file`,
   `sample_locations` and `year` of `for_framework` (and the user's `repo` if they
   gave one). The location carries the sample's cross section, so the framework is
   normalised from the start once the user gives the run period's luminosity. Then
   run `CheckAnalysisFramework` on the sample.
   Reading `root://` files needs `fsspec-xrootd` in the toolkit's environment.
8. **Hand over**: what was produced (process, run period, events after the filter,
   pileup, tiers), where the files are, the cross section and filter efficiency with
   how they were obtained, what was tested and what was not, and the framework.

## Rules

- **Never invent physics numbers.** Masses, widths, couplings, branching fractions,
  cuts and filter thresholds come from the user or from an official fragment. Where a
  setting is a choice (a pTHat cut that makes the generation efficient), say what you
  chose and why.
- **Show the fragment.** Before a long production, show the user the settings and
  filters (`fragment.py` in the production): a wrong decay or cut is cheap to fix
  before the batch jobs and expensive after.
- **Batch jobs and EOS are the user's resources.** Confirm before every submission to
  HTCondor and before writing to EOS. Never delete anything on EOS.
- **The grid proxy is the user's.** If a step needs one and none is valid, say so
  and give the command (`voms-proxy-init -voms cms -valid 192:00`); never create one.
- **One production per directory.** `PrepareMCProduction` refuses a name that exists;
  a changed production gets a new name, so outputs never mix.
- **Results come from the tools.** Report the filter efficiency, cross section and
  event counts that `MCProductionStatus` gives, not estimates.

## Pileup

`pileup="premix"` mixes in the run period's premixed-pileup library, exactly as the
official samples do: DAS is asked for the library once, when the production is
configured, and every job reads it over XRootD. Both need a valid grid proxy.
`pileup="none"` simulates the hard process alone: faster, no proxy, fine for
generator studies and quick looks, but not comparable to data in anything that pileup
affects (isolation, jet energies, vertex counts).

## What the files are

`<output>/<TIER>/<name>_<TIER>_<job>.root`, one file per job and tier. NanoAOD is flat
(a TTree `Events`), as coffea reads it; MiniAOD and AOD are EDM files. Events of
different jobs have different luminosity blocks, so they never repeat.
`MCProductionStatus.generator` adds up the GenXsecAnalyzer reports of the jobs: the
cross section before the filter, the filter efficiency (events kept over events
generated) and their product, the cross section of the sample.
