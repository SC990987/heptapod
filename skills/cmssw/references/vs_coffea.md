# CMSSW vs coffea: which one for what

## Only CMSSW can do these

| Capability | Why coffea cannot |
|------------|-------------------|
| Read RAW / AOD / MiniAOD | They hold serialised C++ objects needing CMSSW dictionaries; coffea reads flat trees |
| **Produce or extend NanoAOD** | File content is decided at production time — see `custom_nanoaod.md` |
| Full reconstruction (PF, tracking, vertexing, b-tag, tau ID) | These are CMSSW algorithms; NanoAOD ships only their outputs |
| Conditions / global tags / alignment / calibration | `conddb`, the conditions DB, `edmProvDump` |
| Provenance — how a file was made, module by module | Stored in the EDM file, read by `edmProvDump` |
| Dataset and file discovery | `dasgoclient` |
| HLT / L1 trigger configuration and re-running | `hltGetConfiguration`, the HLT menu |
| Track propagation, `TransientTrack`, kinematic vertex fits | Detector geometry and B-field live in CMSSW |
| Generation and simulation (Pythia, MadGraph, GEANT) | Run through `cmsRun` |
| Grid submission | CRAB |

## Only coffea is sensible for these

| Capability | Why not CMSSW |
|------------|---------------|
| Columnar analysis over millions of events | FWLite is a per-event Python loop, 100-1000x slower |
| `awkward` jagged manipulation, `hist`, dask scale-out | Not what the framework is for |
| Rapid iteration on selections and plots | No compile step, no job submission |

## Present in **both** — and which to use

The overlap is real, and the answers agree numerically. Prefer whichever side
of the boundary you are already on; do not cross environments just to get one
of these.

### Delta R and Delta phi

Verified identical for the same inputs:

```python
# CMSSW / ROOT
import ROOT
ROOT.gInterpreter.Declare('#include "DataFormats/Math/interface/deltaR.h"')
ROOT.reco.deltaR(eta1, phi1, eta2, phi2)                       # -> 2.69258

ROOT.Math.VectorUtil.DeltaR(v1, v2)                            # -> 2.69258
ROOT.Math.VectorUtil.DeltaPhi(v1, v2)
ROOT.TVector2.Phi_mpi_pi(phi)                                  # wrap to (-pi, pi]
```

```python
# coffea (analysing NanoAOD) -- same number
events.Muon.delta_r(events.Jet)
events.Muon.delta_phi(events.Jet)
```

Note the wrapping convention differs at exactly `pi`: `TVector2::Phi_mpi_pi`
returns `(-pi, pi]`, coffea/`vector` return `[-pi, pi)`. Irrelevant under
`abs()` or squaring, which is how both are almost always used.

### Lorentz vectors

```python
# CMSSW / ROOT
v = ROOT.Math.PtEtaPhiMVector(pt, eta, phi, mass)
(v1 + v2).M()          # invariant mass
v.Pt(), v.Eta(), v.Phi(), v.E(), v.Rapidity()
```

```python
# coffea -- the same algebra, vectorised over all events at once
(events.Muon[:, 0] + events.Muon[:, 1]).mass
```

### Jet clustering

fastjet is available in both. In CMSSW it is the actual reconstruction; in
coffea the `fastjet` Python bindings cluster awkward arrays. Same library.

### Corrections and scale factors

The modern route is shared: **correctionlib** with POG JSONs works in both
(CMSSW ships 2.7.0, a typical coffea venv 2.9.0). Older CMSSW-only paths
(`JetMETCorrections`, `.txt` JEC files, `BTagCalibration`) still exist; use
correctionlib for new work.

Jet energy corrections *applied during reconstruction* are CMSSW's job. Applying
a residual correction or an uncertainty variation at analysis level is fine in
either.

### Luminosity

`brilcalc` (outside CMSSW) computes integrated luminosity; coffea's `LumiMask`
applies a golden JSON. The golden JSON itself is the same file for both.

## The decision rule

1. Is the quantity in the NanoAOD file? → **coffea**.
2. Is it in MiniAOD but not NanoAOD? → add it to a custom NanoAOD with CMSSW,
   then use coffea. Reading MiniAOD directly for a full analysis is a trap.
3. Does it not exist at all yet (needs reconstruction, refitting, conditions)?
   → **CMSSW**, and think about what to write into NanoAOD.

The recurring mistake is doing an entire analysis in FWLite because one
variable was missing. Adding that variable to NanoAOD is usually a few lines —
see `custom_nanoaod.md` — and everything downstream then runs at columnar speed.
