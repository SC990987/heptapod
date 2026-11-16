# CMS simulation task prompts

Prompts to start a session from. The first is complete as it stands; in the others,
replace the physics, the run periods, the numbers of events and the paths with your
own: they are illustrations. The agent does not invent masses, cuts or filter
thresholds; whatever you leave out it asks for.

---

## A first run, here: 100 J/psi -> mu mu events and a framework

This one is complete as it stands. It was run at the LPC end to end, with the tools
called as an agent calls them (`tools/mcgen/README.md`, "Verification"): 47 minutes,
all of it on the interactive node, with no pileup and no grid proxy needed.

```
Produce 100 events of prompt J/psi -> mu+ mu- for the 2023 run period (not 2023BPix),
as NanoAOD, without pileup, here on this machine, with the files in the sandbox.
Generate them with Pythia 8 (Charmonium:all = on, the J/psi decaying only to mu+ mu-,
PhaseSpace:pTHatMin = 8), and keep only events with two opposite-sign muons of
pT > 5 GeV and |eta| < 2.4, with a generator filter; the 100 events are after the
filter. Measure the filter efficiency first with a generator-only test, test the whole
chain on one job, then produce. When the NanoAOD is there, build an analysis framework
on it in a new directory jpsi_analysis, check it, and run its first study notebook, so
that I can see the dimuon invariant mass and the J/psi peak.
```

The filter's cuts are those of the framework's starting muon selection, so nearly
every event produced reaches the dimuon-mass plot. Running the notebook needs Jupyter
in the toolkit's environment (see `README.md`).

To have the framework in a repository of your own, start the agent in a new git
repository (`launch.py --dir` on it) and ask for the framework "in this repository,
with the package named jpsi_analysis" instead of "in a new directory jpsi_analysis":
it is built at the repository's top and committed there.

---

## Produce a sample and analyse it

Produce 20000 events of <process, e.g. prompt J/psi -> mu+ mu-, with both muons of
pT > 3 GeV and |eta| < 2.4> for <run period, e.g. 2023> with pileup, as NanoAOD, on
condor, with the output in root://cmseos.fnal.gov//store/user/<you>/<directory>/.
Measure the filter efficiency first and tell me how many jobs you will submit before
submitting. When it is done, build an analysis framework on it in my repository
<git@github.com:you/my-analysis.git>, put the cross section in, and check it.

## Measure a process before producing it

What is the cross section and the generator filter efficiency of <process, e.g.
Drell-Yan to two muons with 60 < m(mumu) < 120 GeV, both muons with pT > 20 GeV> at
13.6 TeV? Run a generator-only test of 10000 events here and show me the settings you
used.

## More events like an official sample

Make 5000 more events of the official sample whose GEN-SIM request is
<BPH-Run3Summer23GS-00002> for <run period>, without pileup, as MiniAOD and NanoAOD,
here.

## A gridpack

Produce 2000 events from the gridpack <path on /cvmfs> for <run period> with pileup,
hadronized as in the official sample <McM request>, as NanoAOD on condor with the
output in root://cmseos.fnal.gov//store/user/<you>/<directory>/.

## A quick look without the detector

Generate 1000 events of <process> at generator level only and tell me the pT
spectrum of <particle> that passes my filter: <filter>.

## Follow up

- How far is the production? Which jobs failed, and why?
- Resubmit the failed jobs.
- The filter efficiency is too low; loosen <cut> to <value>, measure again, and
  tell me how many events I need for 10000 after the filter.
- Build the framework on the sample and plot the dimuon mass.
