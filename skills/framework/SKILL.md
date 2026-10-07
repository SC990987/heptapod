---
name: framework
bundle: framework
description: Build a coffea analysis framework for a new analysis and extend it — a python package with a processor, named cuts and histograms, selections and histogram collections chosen in yaml, samples, cutflows, normalisation and a self-check. Use whenever the user wants an analysis framework, analysis repository or analysis code set up or scaffolded, wants to start a new analysis on NanoAOD or NanoAOD-like ROOT files, or asks to add or change a cut, selection, channel, control region, histogram, object, sample, trigger or scale factor in a framework built this way, to run it, or to plot from it. Also use for its optional parts — lepton jets, scaling out with dask or HTCondor at the LPC, a custom NanoAOD schema for extra collections and cross-references, and a regression report with CI. For one-off columnar questions on a single file, use the `coffea` skill instead.
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
analysis is naming selections ("channels") and histogram collections; every
collection named in a run is filled in every channel named in that run.

So nearly every request is the same two moves, made by editing files, with no tool
in between: write the definition, then name it in a config.

| The user asks for | Define it in | Name it in |
|-------------------|--------------|------------|
| a cut on objects ("muons need pT > 30") | `definitions/cuts.py`, `obj_cut_defs[object]` | `configs/selections.yaml`, under a selection's `obj_cuts: object:` |
| a cut on events ("at least two muons") | `definitions/cuts.py`, `evt_cut_defs` | `configs/selections.yaml`, under a selection's `evt_cuts:` |
| a channel, signal or control region | (cuts it needs, if new) | `configs/selections.yaml`, a new top-level entry |
| a histogram | `definitions/hists.py`, `hist_defs` | `configs/hist_collections.yaml`, in a collection |
| a counter | `definitions/hists.py`, `counter_defs` | nowhere: counters are filled in every channel |
| an object | `definitions/objects.py` | the cuts and histograms that use it |
| a trigger requirement | `definitions/cuts.py`, an event cut | `configs/selections.yaml` |
| a weight or scale factor | `definitions/weights.py` | nowhere: weights always apply |
| a sample | `configs/samples/*.yaml` | `configs/cross_sections.yaml`, under the same name (simulation); its `year` must be a period of `run_periods.yaml` |

`references/howto.md` has the recipe for each row.

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
3. **Check.** `CheckAnalysisFramework` with `sample=` (a short run): a simulated
   sample, and a data sample too when there is one, because some warnings only show
   on one of them. One call takes both names. Its answer includes the static
   consistency checks, so a call without a sample is only needed when there is no
   sample to run on. It must come back with `ok: true` before you change anything.
   `ok: true` still comes with warnings: a missing cross section or luminosity, or
   data with no golden JSON configured, is expected at this point. Report those to
   the user; do not invent values to silence them.
4. **Express the analysis** by editing `definitions/` and `configs/` directly, one
   small step at a time, re-running the check after each change. A definition and
   the config line that names it are one change: check after both, not in between.
   `references/howto.md` has the recipe for every kind of change.
5. **Add components only when asked for**: `AddFrameworkComponent` with
   `lepton_jets`, `scaleout`, `schema` or `chain_report`. See
   `references/components.md`.
6. **Hand over.** Say how to run it (the README has the commands), which cuts and
   thresholds are still the scaffold's placeholders, what is still missing (cross
   sections, luminosity, golden JSON), and what the last check reported, warnings
   included.

**Coming back to a framework that exists.** Most requests arrive later: a histogram,
a cut, a channel, a sample. Steps 1 and 2 are then done, and this file with the one
section of `references/howto.md` that covers the change is enough reading. Before
the first edit, run the check as the framework stands (step 3). It tells you what is
there (`static` lists the objects, channels, collections and samples, and
`static.selections` the cuts each channel applies) and it gives you the numbers from
*before* your change, which are what you compare with afterwards: the check keeps no
memory of earlier calls. Then make the change as in step 4, and check again with the
same arguments (if you named channels or samples before, add the one you created).
The hand-over for one change is short: the answer, if a question was asked; what
you assumed; which files changed; what the check showed before and after; what you
could not verify. Mention placeholders and missing inputs only where
the change rests on them, and warnings that were there before in one line. When what
was asked for exists already, nothing changes, and the hand-over is that it is
there, where, and what the check shows for it.

What in a scaffolded framework is a placeholder: the pT and |eta| thresholds, the
`baseline` selection and the two-object channel, the histogram binnings, and any mass
marked "an assumption" in `objects.py`. What is not: the scaffold never invents a
cross section, a luminosity, a golden JSON or a trigger path, so a value found in
`cross_sections.yaml` or `run_periods.yaml`, or a path in the "pass triggers" cut, was
given by someone. `.analysis_framework.json` records what the scaffold was given
(objects and their collections, tree, trigger paths, sample names) and the components
added since. Edits made by hand do not update it, and nothing breaks when it lags
behind.

A check call is given 50 seconds by default, because the tool server usually cuts a
call at 60. If it times out, try once more (the first import of the libraries is the
slow part), then lower `max_events`, name fewer `channels` and `hist_collections`,
or give it one sample per call.

## What the processor does with your definitions

For each chunk of events the primary objects are read; then, for each channel:

```
primary objects      objects.py: primary_objs["muons"] = lambda evts: evts.Muon
   │  object cuts     cuts.py: obj_cut_defs["muons"]["pT > 10 GeV"] = lambda objs, obj: obj.pt > 10
   ▼                  object by object, in the order the objects are defined
derived objects      objects.py: derived_objs["dimuons"] = lambda objs: ...   (in order; own object cuts)
   │  object weight   weights.py: object_weight(objs, is_data), times the event weight
   │  event cuts      cuts.py: evt_cut_defs[">=2 muons"] = lambda objs: ak.num(objs["muons"], axis=1) >= 2
   ▼                  one cutflow row each, applied in the order the selection lists them
histograms, counters hists.py: filled with the surviving events, one "channel" axis
```

An event cut, a histogram and a derived object see `objs`, a dict of the *selected*
objects of that channel. An object cut sees less: see the rules below. Output:
`out[sample]["cutflow"][channel]`, `["hists"][name]`, `["counters"][channel]`,
`["metadata"]`, and `["warnings"]`.

## What the expressions are written with

`tools/utilities.py` of the package has the columnar helpers a definition usually
needs: `dR`, `matched` and `unmatched` (ΔR matching and overlap removal),
`as_lorentz`, `lxy`, `check_bit`. Look there before writing the same thing by hand.

The `coffea` skill is the reference for the expressions themselves: building pairs,
generator navigation, evaluating correctionlib scale factors. Those go inside a cut,
a fill function, a derived object or a weight function here. Its other half
(`PackedSelection`, `Weights`, writing a processor or a script of your own, filling
`hist` by hand) describes a different way of organising an analysis: this framework
already does those jobs, so do not bring them in.

## Rules that prevent most failures

- **Look before you add.** The scaffold already wrote a menu for each of its objects:
  pT and |eta| cuts at several thresholds, the ID flags it found, `">=1 muons"` and
  `">=2 muons"` event cuts, and multiplicity, pT and eta-phi histograms (`muon_n`,
  `muon_pt`, `muon_eta_phi`). Search `cuts.py` and `hists.py` for what is asked
  before writing it. A cut that is defined but unused is simply named in the
  selection. A histogram that is defined and listed in a collection is filled
  already: say so. When the user has said how theirs differs (a range, a
  threshold), follow their verb: "add" or "another" is a second one beside the
  first, named for what differs (`muon_pt_wide`); "change", "extend" or "make it"
  edits the one that is there. Say which you did. A name can be defined once: the
  static check reports a name defined twice as an error, because the later
  definition would silently replace the earlier.
- **Object or event?** "Electrons need pT above 25 GeV" is an object cut: it removes
  electrons, not events, and has no cutflow row of its own. It still changes event
  yields wherever a later event cut counts that object (`">=2 electrons"`). "At
  least two electrons" is an event cut. Tell the user which one you made and where
  its effect shows.
- **Where an edit to a selection lands.** `baseline` and the channels built like it
  merge the shared blocks `_object_cuts` and `_event_cuts`. A cut added to a shared
  block applies in every channel that merges it. A key written next to `<<:` in one
  channel replaces that object's whole list for that channel alone. When the user
  names no channel, the shared block is meant. Tell the user which channels changed.
- **Verify with the check, and leave nothing temporary behind.** After an edit,
  `static.selections` shows the cuts each channel now applies; the cutflow and
  `counters` show their effect against the numbers from before; `empty_hists`,
  `empty_in_channels` and `static.unused_hists` say whether a histogram is filled.
  Nothing goes into the user's files only to convince yourself: no counter, print,
  histogram, channel or reordered list that you mean to take out again. To look at
  something the check does not show, run the framework from outside the package (a
  one-off script or `python -c` with the interpreter the check reports); if that is
  not possible, say what you could not verify.
- **A run or a plot is not a change to the framework.** "Run the baseline on ttbar"
  and "plot the MET" add nothing when what they need is defined. A yield the check
  has already read in full (`n_events`, `files_in_sample`) is answered from its
  cutflow. Otherwise run `scripts/run_analysis` and plot its output
  (`references/howto.md`, "Run it" and "Plot it"): one file per sample unless told
  how much, into `output/` in the project directory unless told where, and say what
  was read. If the environment cannot run or plot, give the exact commands and say
  that they were not run.
- **Object cut**: `lambda objs, obj: <mask per object>`. Same nesting as the
  collection. It slims the collection and never drops an event. `obj` is the
  collection being cut. In `objs`, primary objects defined *above* it in `objects.py`
  already have their cuts applied, those *below* it do not yet, and derived objects
  do not exist at all at that point. An object the cut has no answer for (`None`)
  does not pass; that includes every object of an event the cut cannot be evaluated
  in, as when it compares with `ak.firsts(...)` of something the event lacks.
- **Event cut**: `lambda objs: <one True/False per event>`. Reduce per-object masks
  with `ak.num(...) >= n`, `ak.any(..., axis=1)` or `ak.all(..., axis=1)`. A jagged
  mask is refused. An event the cut has no answer for (`None`) fails it, and
  `ak.max`, `ak.min` and `ak.firsts` have no answer for an empty list: a veto
  written with them also rejects the events that have none of the object.
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
- **Failures come in four kinds.**
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
  missing, leave it out, say so, and the framework will warn rather than guess. A
  histogram's range and binning are the user's as well: when they give none, use the
  defaults in `hists.py` and say that you did.
- **The generated cuts are placeholders.** The scaffold (and the lepton-jet
  component) writes generic pT and |eta| thresholds and a baseline selection so that
  something runs; they are not this analysis's cuts.
- **Keep physics out of `tools/`.** If you find yourself editing `processor.py` to
  make one analysis work, the change belongs in `definitions/`.
- **Notebooks are the user's.** Leave `test_notebooks/` and `studies/` as they are
  unless asked: a new channel does not need to be added to the test notebook.
- **When you build a coffea `Runner` yourself** (a notebook, a script), give it
  `metadata_cache={}` as the generated code does. Otherwise coffea reuses, for the
  rest of the python session, the `is_data`, `year` and `skim_factor` a file was
  first run with.

## Reference files

| File | Read it when |
|------|--------------|
| `references/howto.md` | Adding or changing a cut, selection, histogram, counter, object, sample, trigger, weight or scale factor; data versus simulation; generator-level objects; running, plotting, looking inside a sample. Its first lines list the sections |
| `references/structure.md` | You need to know exactly what a file is for, what the output contains, or how normalisation works |
| `references/components.md` | Lepton jets, scale-out, custom schema or regression report were asked for |
| `references/pitfalls.md` | You need to know what a field of the check's answer holds (its first section); a check failed, a cutflow hit zero, a histogram stayed empty, or a number looks wrong |
