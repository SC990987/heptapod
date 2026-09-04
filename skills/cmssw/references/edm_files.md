# EDM files: formats, inspection, reading

## The format tiers

| Tier | Size/event | Contains | coffea can read it? |
|------|-----------|----------|---------------------|
| RAW | ~1 MB | detector output | no |
| AOD | ~200 kB | full reco objects, tracks, hits | no |
| MiniAOD | ~50 kB | PAT objects, slimmed collections | no |
| NanoAOD | ~1-2 kB | flat TTree of plain numbers | **yes** |

RAW/AOD/MiniAOD are **EDM files**: ROOT files whose branches hold serialised
C++ objects. Reading them requires CMSSW's dictionaries, which is why coffea
cannot — it needs a flat tree of numbers. NanoAOD is exactly that flat tree,
which is why coffea reads it directly.

If you need a quantity that only exists in MiniAOD, the options are: read
MiniAOD with FWLite/cmsRun, or add the quantity to a custom NanoAOD (see
`custom_nanoaod.md`) and then analyse it with coffea. The second scales better.

## Inspecting a file

**What collections are in it** — the first command to run on anything unfamiliar:

```bash
edmDumpEventContent file.root
```

Prints a table of `Type | Module | Label | Process`. Those four identify a
product; the InputTag you use in a config is `module:label:process` (label and
process usually omitted).

```bash
edmDumpEventContent file.root | grep -i muon        # narrow it down
```

**File summary and branch sizes**:

```bash
edmFileUtil -P file.root       # runs, lumis, events, per-branch sizes
edmFileUtil -d /store/...      # resolve a LFN to a physical URL
```

**How the file was made** — the full processing history, including the global
tag and every module's parameters:

```bash
edmProvDump file.root | less
edmProvDump file.root | grep -A5 GlobalTag
```

**For NanoAOD** (a plain TTree), uproot is easier than any EDM tool:

```python
import uproot
f = uproot.open("nano.root")
f["Events"].keys()            # branch names
f["Events"].num_entries
```

## Reading EDM files with FWLite

FWLite gives a Python event loop over AOD/MiniAOD without writing a cmsRun
config. Verified working in this release:

```python
import ROOT
ROOT.gROOT.SetBatch(True)
from DataFormats.FWLite import Events, Handle, Runs, Lumis

events = Events("miniaod.root")          # or a list, or root:// URL
print(events.size(), "events")

muons  = Handle("std::vector<pat::Muon>")
label  = ("slimmedMuons")

for i, ev in enumerate(events):
    ev.getByLabel(label, muons)
    if not muons.isValid():
        continue
    for mu in muons.product():
        if mu.pt() > 25 and abs(mu.eta()) < 2.4:
            print(i, mu.pt(), mu.eta(), mu.phi(), mu.charge())
    if i > 10:
        break
```

The `Handle` type string must match the C++ type exactly as
`edmDumpEventContent` reports it. Common MiniAOD ones:

| Collection | Type | Label |
|------------|------|-------|
| muons | `std::vector<pat::Muon>` | `slimmedMuons` |
| electrons | `std::vector<pat::Electron>` | `slimmedElectrons` |
| jets | `std::vector<pat::Jet>` | `slimmedJets` |
| MET | `std::vector<pat::MET>` | `slimmedMETs` |
| gen particles | `std::vector<reco::GenParticle>` | `prunedGenParticles` |
| packed PF cands | `std::vector<pat::PackedCandidate>` | `packedPFCandidates` |
| vertices | `std::vector<reco::Vertex>` | `offlineSlimmedPrimaryVertices` |

FWLite is a **per-event Python loop** — orders of magnitude slower than
columnar analysis. Use it to inspect, prototype, or extract a small quantity;
do not run a full analysis through it. If you find yourself looping over
millions of events, you want NanoAOD + coffea instead.

## Running a job

```bash
cmsRun config.py                       # run it
cmsRun -n 4 config.py                  # 4 threads
cmsRun config.py maxEvents=100         # if the config uses VarParsing
```

A minimal config skeleton:

```python
import FWCore.ParameterSet.Config as cms
process = cms.Process("DEMO")
process.load("FWCore.MessageService.MessageLogger_cfi")
process.maxEvents = cms.untracked.PSet(input=cms.untracked.int32(100))
process.source = cms.Source("PoolSource",
    fileNames=cms.untracked.vstring("file:input.root"))
process.demo = cms.EDAnalyzer("MyAnalyzer", src=cms.InputTag("slimmedMuons"))
process.p = cms.Path(process.demo)
```

## Finding data

```bash
voms-proxy-init -voms cms                      # required first
dasgoclient -query="dataset=/DoubleMuon/Run2018A-*/NANOAOD"
dasgoclient -query="file dataset=/DoubleMuon/Run2018A-.../NANOAOD"
dasgoclient -query="summary dataset=/.../NANOAOD"      # event counts
dasgoclient -query="config dataset=/.../NANOAOD"       # how it was produced
```

`Certificate is expired` from `dasgoclient` means the proxy needs renewing,
even if `voms-proxy-info` prints a subject.
