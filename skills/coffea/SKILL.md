---
name: coffea
bundle: coffea
description: Write columnar HEP analysis code directly against coffea on NanoAOD-like ROOT files — how to open a file, how to reach the attributes of coffea objects (which are branches, which are computed by behaviours, and how cross-references like matched_jet / GenPart.parent work), and how to drive coffea's subsystems (PackedSelection cutflows, Weights and systematics, LumiMask, correctionlib scale factors, hist). Use whenever the user mentions coffea, NanoAOD, awkward arrays, cutflows, N-1, Delta R matching, cross-cleaning, invariant mass, gen matching, scale factors, correctionlib, POG JSON, golden JSON, pileup reweighting, b-tag SFs, or wants histograms with systematic variations from a ROOT file.
---

# Analysing NanoAOD with coffea

**Write and run coffea code. Do not ask for a tool to be built for each
quantity.** coffea already implements the physics — Lorentz-vector algebra,
Delta R, gen-particle navigation, cutflow bookkeeping, weights, corrections,
luminosity masking. The job is to reach for the right attribute or method, not
to reimplement it.

The one tool worth calling first is `InspectFileTool`, which lists a file's
trees, collections and branches. Everything after that is a short Python
script.

## The 30-second version

```python
from coffea.nanoevents import NanoEventsFactory, NanoAODSchema

events = NanoEventsFactory.from_root(
    {"file.root": "Events"}, schemaclass=NanoAODSchema
).events()

mu = events.Muon[events.Muon.pt > 25]        # cut objects -> still per-event lists
events = events[ak.num(mu) >= 2]             # cut events
mass = (mu[:, 0] + mu[:, 1]).mass            # coffea does the 4-vector algebra
```

Three ideas carry almost everything:

1. **`events.Collection.field`** — `events.Muon.pt`. Collections are jagged:
   one variable-length list per event.
2. **Behaviours add attributes that are not in the file.** `pt/eta/phi/mass`
   are branches; `px`, `energy`, `rapidity`, `delta_r(...)`, `matched_jet`
   are computed on demand. See `references/object_model.md`.
3. **Masking is either per-object or per-event.** `events.Muon[mask]` filters
   objects inside each event; `events[mask]` drops whole events. Mixing them up
   is the most common bug.

## Reading the reference files

| File | Read it when |
|------|--------------|
| `references/object_model.md` | You need an attribute — which are branches, which are computed, what cross-references exist, how MET differs |
| `references/subsystems.md` | Cutflows, weights/systematics, scale factors, luminosity masking, histograms |
| `references/recipes.md` | You want a worked, runnable example of a common task |
| `references/pitfalls.md` | Something returned `None`, the wrong count, or an odd error |

## How to work

Write a script, run it, print what you got. Check `events.fields` and
`events.Muon.fields` before guessing a branch name — every schema and skim
differs, and a wrong name is the usual cause of failure.

Prefer coffea's own function over arithmetic you write:

| Want | Use | Not |
|------|-----|-----|
| Delta R | `a.delta_r(b)` | `sqrt(deta**2 + dphi**2)` |
| Delta phi | `a.delta_phi(b)` | a hand-rolled `while` wrap |
| Invariant mass | `(a + b).mass` | `sqrt(E**2 - p**2)` |
| Nearest partner | `a.nearest(b, threshold=0.4)` | a double loop |
| All pairs | `ak.combinations(coll, 2)` | index arithmetic |
| Cutflow | `PackedSelection` | counters you increment |
| Weighted histogram | `hist` + `Weights` | manual binning |

Work columnar: operate on whole arrays. A Python loop over events is typically
100–1000x slower and is almost never necessary.

## Work on the NanoAOD file, not on array dumps

The analysis operates on the NanoAOD ROOT file directly: open it with coffea,
select and compute columnar, and histogram with `hist`. Do **not** flatten
arrays out to `.npy` / `.jsonl` and work on those.

The `analysis` bundle has tools that work on such dumps (they exist for
generator-level LHE and Pythia output). For a NanoAOD file each of them is a
coffea one-liner, and going through a dump means flattening a columnar array to
disk and back -- slower, lossier, and a second implementation of the same
physics. If one of them is being served and you are tempted, write the coffea
instead:

| Tool you may see served | Use instead |
|-------------------------|-------------|
| `CalculateDeltaR` | `a.delta_r(b)` |
| `CalculateInvariantMass` | `(a + b).mass` |
| `CalculateTransverseMomentum` | `obj.pt` |
| `FilterByDeltaR` | `a.nearest(b, threshold=0.4)` |
| `SortByPt` | `obj[ak.argsort(obj.pt, ascending=False)]` |
| `GetHardestN`, `GetHardestNJets` | the same, then `[:, :N]` |
| `ApplyCuts` | `obj[mask]` |
| `FilterByPDGID` | `gen[abs(gen.pdgId) == pid]` |
| `MergeObjectCollections` | `ak.concatenate([a, b], axis=1)` |
| `Cutflow` | `PackedSelection(...).cutflow(...)` |

**Tools that are still the right call**, because they do something a one-liner
does not:

* `InspectFileTool` -- run it first on any unfamiliar file.
* `NormalizeYieldTool` -- cross-section / luminosity normalisation, with
  plausibility checks on the result. It belongs to the `analysis` bundle, so
  it is only there when that bundle is active as well.

The rule: **wraps a library call -> write the coffea; pins a convention or
checks a result -> call the tool.**

## One script or a whole analysis?

Everything in this skill is about writing coffea directly: one file, one
question, one short script. When the work is an *analysis* -- several samples,
named selections, cutflows, many histograms, normalisation, something other
people will run again -- do not grow the script. Use the `framework` skill:
`ScaffoldAnalysisFramework` writes a processor-based analysis package in which
cuts, histograms and objects are defined once and combined by name, and this
skill is then how you write the expressions that go into it.

## Sanity checks worth running

* `len(events)` before and after each event-level cut.
* `ak.num(coll)` to confirm per-event multiplicities are what you expect.
* `ak.sum(ak.num(coll))` for the total object count.
* For anything option-typed (`nearest`, `matched_*`), count the `None`s:
  `ak.sum(ak.is_none(x, axis=1))`.
