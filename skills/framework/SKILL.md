---
name: framework
bundle: framework
description: Build a coffea analysis framework for a new analysis and extend it — a python package with a processor, named cuts and histograms, selections and histogram collections chosen in yaml, samples, cutflows, normalisation and a self-check. Use whenever the user wants an analysis framework, analysis repository or analysis code set up or scaffolded, wants to start a new analysis on NanoAOD or NanoAOD-like ROOT files, or asks to add a cut, selection, channel, histogram, object, sample, trigger or scale factor to a framework built this way. Also use for its optional parts — lepton jets, scaling out with dask or HTCondor at the LPC, a custom NanoAOD schema for extra collections and cross-references, and a regression report with CI. For one-off columnar questions on a single file, use the `coffea` skill instead.
---

# Building an analysis framework

`ScaffoldAnalysisFramework` writes a python package for an analysis to live in: an
engine that knows nothing about any particular analysis, and a small set of files
that say what *this* analysis is. Your job is the second part. The engine is already
written; do not rewrite it, and do not put physics in it.

```
my_analysis/
├── my_analysis/
│   ├── tools/          the engine: processor, selection, histogram, cutflow, utilities, check ...
│   ├── definitions/    WHAT EXISTS: objects.py, cuts.py, hists.py, weights.py
│   ├── configs/        WHAT IS USED: selections.yaml, hist_collections.yaml, samples/, ...
│   ├── data/           golden JSONs, correction files
│   ├── scripts/        run_analysis, add_samples, merge_outputs
│   ├── test_notebooks/ exercising the machinery
│   └── studies/        one notebook per physics study
└── README.md, setup.py, requirements.txt
```

The one idea to hold on to: **definitions say what can be done, configs choose what
is done.** A cut is a named function in `cuts.py`; it does nothing until a selection
in `selections.yaml` lists its name. Same for histograms and collections. Running the
analysis is naming selections ("channels") and histogram collections.

**The scaffold writes files; it does not run them.** Whether the framework works with
the files and the library versions at hand is what `CheckAnalysisFramework` finds
out. Trust the check, not the fact that a file was written. If the check fails on a
project you have not edited yet, the fault may be in the generated engine itself:
show the user the error in full instead of working around it in the definitions.

## The workflow

1. **Look at the file.** `InspectFile` on a representative ROOT file. Note the exact
   collection names, anything listed under `schema_notes`, and the trigger paths you
   need (`branch_pattern="HLT_*Mu*"`).
2. **Scaffold once.** `ScaffoldAnalysisFramework` with `sample_file`, the extra
   `objects` the analysis needs beyond the standard ones, `triggers`, and `year`,
   `lumi` and `golden_json` if you were given them (`lumi` and `golden_json` are only
   accepted together with `year`). Read the `notes` it returns. Once is meant
   literally: the tool refuses a directory that already holds a framework, whatever
   `overwrite` says, because scaffolding again would replace the analysis written
   into it. From here on the framework changes through its own files and through
   `AddFrameworkComponent`.
3. **Check.** `CheckAnalysisFramework` with no sample (static consistency), then with
   `sample=` (a short run). Both must come back with `ok: true` before you change
   anything. `ok: true` still comes with warnings: a missing cross section or
   luminosity, or data with no golden JSON configured, is expected at this point.
   Report those to the user; do not invent values to silence them.
4. **Express the analysis** by editing `definitions/` and `configs/` directly, one
   small step at a time, re-running the check after each. `references/howto.md` has
   the recipe for every kind of change.
5. **Add components only when asked for**: `AddFrameworkComponent` with
   `lepton_jets`, `scaleout`, `schema` or `chain_report`. See
   `references/components.md`.
6. **Hand over.** Say how to run it (the README has the commands), which cuts and
   thresholds are still the scaffold's placeholders, what is still missing (cross
   sections, luminosity, golden JSON), and what the last check reported, warnings
   included.

A check call is given 50 seconds by default, because the tool server usually cuts a
call at 60. If it times out, try once more (the first import of the libraries is the
slow part), then lower `max_events` or name fewer `channels` and `hist_collections`.

## What the processor does with your definitions

For each chunk of events the primary objects are read; then, for each channel:

```
primary objects      objects.py: primary_objs["muons"] = lambda evts: evts.Muon
   │  object cuts     cuts.py: obj_cut_defs["muons"]["pT > 10 GeV"] = lambda objs, obj: obj.pt > 10
   ▼                  object by object, in the order the objects are defined
derived objects      objects.py: derived_objs["dimuons"] = lambda objs: ...   (in order; own object cuts)
   │  event cuts      cuts.py: evt_cut_defs[">=2 muons"] = lambda objs: ak.num(objs["muons"], axis=1) >= 2
   ▼                  one cutflow row each, applied in the order the selection lists them
histograms, counters hists.py: filled with the surviving events, one "channel" axis
```

An event cut, a histogram and a derived object see `objs`, a dict of the *selected*
objects of that channel. An object cut sees less: see the rules below. Output:
`out[sample]["cutflow"][channel]`, `["hists"][name]`, `["counters"][channel]`,
`["metadata"]`, and `["warnings"]`.

## Rules that prevent most failures

- **Object cut**: `lambda objs, obj: <mask per object>`. Same nesting as the
  collection. It slims the collection and never drops an event. `obj` is the
  collection being cut. In `objs`, primary objects defined *above* it in `objects.py`
  already have their cuts applied, those *below* it do not yet, and derived objects
  do not exist at all at that point. An object the cut has no answer for (`None`)
  does not pass; that includes every object of an event the cut cannot be evaluated
  in, as when it compares with `ak.firsts(...)` of something the event lacks.
- **Event cut**: `lambda objs: <one True/False per event>`. Reduce per-object masks
  with `ak.num(...) >= n`, `ak.any(..., axis=1)` or `ak.all(..., axis=1)`. A jagged
  mask is refused.
- **One-per-event records** (`MET`, `PV`, `HLT`, `Flag`) take event cuts only.
- **Histogram fill function**: `lambda objs, mask: <one entry per event>`, an entry
  being a value or a list of values. Several axes of one histogram are paired event by
  event: a value goes with each entry of a list on the other axis, two lists must
  have equal lengths, and what is `None` on one axis is dropped on all.
- **Event cuts are sequential.** A cut sees only events that passed the cuts before
  it, so `objs["muons"][:, 0]` is safe *after* `">=1 muons"` and an error before it.
- **Order derived objects by dependency.** They are built top to bottom; one that uses
  an object a component added (`ljs`) must be defined below that component's block.
- **Collections do not stay aligned.** Each jagged collection is ordered by pT and cut
  on its own. To keep something with each object, make it a field of that object
  (`ak.with_field`), not a second object.
- **Names are checked when the processor is built.** A misspelt channel, cut or
  histogram fails immediately with the list of problems; fix them all, then re-check.
  Entries of the yaml files whose name starts with `_` are building blocks: they
  cannot be run as channels or filled as collections.
- **Failures come in three kinds.**
  *Does not apply here*: an object listed in `optional_objs` (in `objects.py`; the
  scaffold puts generator-level objects there) may be absent from a sample. What
  needs it is skipped with the warning "`X` is not available in this sample", and a
  skipped cut's cutflow row is marked "not applied".
  *A mistake*: any other object that cannot be built, and any cut or `object_weight`
  that fails, stops the run in the default strict mode and names the culprit.
  *Never fatal*: a histogram or counter that cannot be filled is skipped with a
  warning even in strict mode.
  *Always fatal*: a failing `generator_weight` or `event_weight`, and a golden JSON
  that is configured but cannot be read.
  So a run that finished is not a run without problems: **read `warnings` and
  `empty_hists` after every run.**
- **Do not invent numbers.** Cross sections, luminosities, golden JSONs, trigger
  paths and ID working points come from the user or from their files. If one is
  missing, leave it out, say so, and the framework will warn rather than guess.
- **The generated cuts are placeholders.** The scaffold (and the lepton-jet
  component) writes generic pT and |eta| thresholds and a baseline selection so that
  something runs; they are not this analysis's cuts.
- **Keep physics out of `tools/`.** If you find yourself editing `processor.py` to
  make one analysis work, the change belongs in `definitions/`.
- **When you build a coffea `Runner` yourself** (a notebook, a script), give it
  `metadata_cache={}` as the generated code does. Otherwise coffea reuses, for the
  rest of the python session, the `is_data`, `year` and `skim_factor` a file was
  first run with.

## Reference files

| File | Read it when |
|------|--------------|
| `references/howto.md` | Adding a cut, selection, histogram, object, sample, trigger, weight or scale factor; data versus simulation; generator-level objects |
| `references/structure.md` | You need to know exactly what a file is for, what the output contains, or how normalisation works |
| `references/components.md` | Lepton jets, scale-out, custom schema or regression report were asked for |
| `references/pitfalls.md` | A check failed, a cutflow hit zero, a histogram stayed empty, or a number looks wrong |

The `coffea` skill is the reference for the columnar expressions themselves: ΔR
matching, building pairs, generator navigation, evaluating correctionlib scale
factors. Those go inside a cut, a fill function, a derived object or a weight
function here. Its other half (`PackedSelection`, `Weights`, writing a processor,
filling `hist` by hand) describes a different way of organising an analysis: this
framework already does those jobs, so do not bring them in.
