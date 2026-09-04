# Adding variables and collections to NanoAOD

This is the answer to "my NanoAOD does not have the branch I need". NanoAOD
content is decided by the CMSSW job that produced it; to get a new branch you
add it to a table and re-run NanoAOD production over MiniAOD.

Everything below was checked against `CMSSW_17_0_0_pre4`.

## How a NanoAOD branch is declared

A NanoAOD collection is a **FlatTableProducer** with a `variables` PSet. Each
entry is a `Var(...)` whose first argument is a **C++ expression evaluated on
the object**. This is the real definition of `Muon` in
`PhysicsTools/NanoAOD/python/muons_cff.py`:

```python
muonTable = simplePATMuonFlatTableProducer.clone(
    src  = cms.InputTag("linkedObjects", "muons"),
    name = cms.string("Muon"),
    doc  = cms.string("slimmedMuons after basic selection"),
    variables = cms.PSet(CandVars,
        ptErr = Var("bestTrack().ptError()", float,
                    doc="ptError of the muon track", precision=6),
        dz    = Var("dB('PVDZ')", float,
                    doc="dz (with sign) wrt first PV, in cm", precision=10),
        ...
    ),
)
```

`name = "Muon"` is what makes the branches come out as `Muon_pt`,
`Muon_ptErr`, ... — which is exactly what coffea's schema then groups into
`events.Muon`.

The helpers:

```python
from PhysicsTools.NanoAOD.common_cff import Var, CandVars, P4Vars, ExtVar
```

* `Var(expr, type, doc=..., precision=N)` — a method call on the object.
* `CandVars` — the standard `pt/eta/phi/mass/charge/pdgId` block.
* `P4Vars` — four-vector variables only.
* `ExtVar(InputTag, type, ...)` — a value from an external ValueMap.

`precision` sets float compression; `-1` or omitting it keeps full precision.
`type` is `float`, `int`, `bool`, or `"int"`/`"uint8"` style strings.

## Adding a variable to an existing collection

Write a customisation function:

```python
# MyAnalysis/NanoCustom/python/custom_cff.py
import FWCore.ParameterSet.Config as cms
from PhysicsTools.NanoAOD.common_cff import Var

def addMuonExtras(process):
    process.muonTable.variables.segmentCompatibility = Var(
        "segmentCompatibility()", float,
        doc="muon segment compatibility", precision=10)
    process.muonTable.variables.nStations = Var(
        "numberOfMatchedStations()", int,
        doc="number of matched muon stations")
    return process
```

Then hand it to `cmsDriver.py`:

```bash
cmsDriver.py step1 \
  --filein file:miniaod.root --fileout file:nano_custom.root \
  --eventcontent NANOAODSIM --datatier NANOAODSIM \
  --step NANO --mc --conditions <GLOBALTAG> --era Run3 \
  --customise MyAnalysis/NanoCustom/custom_cff.addMuonExtras \
  -n 1000 --no_exec --python_filename nano_custom_cfg.py

cmsRun nano_custom_cfg.py
```

`--no_exec` writes the config without running it, so you can read it first —
always do this the first time. Use `--data` instead of `--mc` for data, and get
the global tag from `edmProvDump` on the input or from the campaign's twiki.

## Adding a whole new collection

Clone a table producer pointing at the collection you want:

```python
from PhysicsTools.NanoAOD.simpleCandidateFlatTableProducer_cfi import \
    simpleCandidateFlatTableProducer
from PhysicsTools.NanoAOD.common_cff import Var, CandVars

def addMyCands(process):
    process.myCandTable = simpleCandidateFlatTableProducer.clone(
        src       = cms.InputTag("myProducer"),
        name      = cms.string("MyCand"),
        doc       = cms.string("my custom candidates"),
        singleton = cms.bool(False),     # True for a per-event scalar
        extension = cms.bool(False),     # True to add columns to an existing table
        variables = cms.PSet(CandVars,
            myScore = Var("userFloat('score')", float, doc="BDT score"),
        ),
    )
    process.nanoTableTaskCommon.add(process.myCandTable)
    return process
```

Branches come out as `MyCand_pt`, `MyCand_myScore`, ... and coffea sees
`events.MyCand` with no configuration — as long as `nMyCand` is written too,
which the producer does automatically for non-singleton tables.

`extension = True` is how you add columns to a table another producer already
defines (e.g. extra `Muon_*` branches from a separate producer) — the `name`
must match the existing table's.

## Cross-references and why yours may be missing

An index branch such as `Muon_jetIdx` or `Jet_muonIdx1` is an ordinary NanoAOD
variable holding the position of an object in another collection. coffea turns
those into `Muon.matched_jet` / `Jet.matched_muons`.

`Jet_muonIdx1` and `Jet_muonIdx2` are part of the **central** NanoAOD content
and hold at most two muons per jet. Custom, slimmed and privately produced
NanoAOD frequently drop them — which is why `Jet.matched_muons` raises
`no field named 'muonIdxG'` on such files.

Two ways forward:

1. Do the association geometrically in coffea — works on any file, no 2-object
   cap: `events.Jet.nearest(events.Muon, threshold=0.4)`.
2. Write the index yourself if you truly need the CMSSW-side association, using
   the same `Var` mechanism over a producer that computes it.

Option 1 is right almost always.

## Checking the result

```bash
python3 -c "
import uproot
f = uproot.open('nano_custom.root')
print([k for k in f['Events'].keys() if k.startswith('Muon_')])"
```

Then open it with coffea and confirm the field appears under the collection.

## Practical notes

* **Test on a few events first** (`-n 100`). A NanoAOD job over a full dataset
  belongs on the grid via CRAB.
* **The global tag must match the data/MC campaign.** A mismatched tag gives
  either a crash or silently wrong calibrations.
* **The `Var` expression is C++**, evaluated on the object's class. If the
  method does not exist you get a long framework exception at job start, not a
  Python error — read the first `----- Begin Fatal Exception` block.
* **`--customise` takes `Package/SubPackage/module.function`**, and the package
  must be under `$CMSSW_BASE/src` and compiled with `scram b` if it has C++.
  A pure-Python customisation still needs the directory structure
  (`MyAnalysis/NanoCustom/python/custom_cff.py`).
