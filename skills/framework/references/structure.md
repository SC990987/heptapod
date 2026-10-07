# What each part is for

`PKG` stands for the analysis package.

## The engine: `PKG/tools/`

Nothing here is specific to an analysis. Read it to understand behaviour; change it
only to change how *every* analysis built this way behaves.

| File | Holds |
|------|-------|
| `processor.py` | `AnalysisProcessor`, the coffea processor. Also `list_channels()` and `list_hist_collections()`. |
| `selection.py` | `JaggedSelection` (object cuts) and `Selection` (event cuts, with the cutflow). |
| `histogram.py` | `Histogram` and `Axis`: a `hist.Hist` bundled with the functions that fill it. |
| `cutflow.py` | `Cutflow`: rows of raw and weighted counts; `print_table()`, `yields()`, `efficiency()`. |
| `utilities.py` | Configs and filesets (`make_fileset`, `load_samples`, `get_xs`, `get_lumi`), output helpers (`get_hist`, `sum_hists`, `cutflow_table`, `save_output`, `load_output`, `merge_outputs`, `scale_to_lumi_xs`), columnar idioms (`dR`, `matched`, `unmatched`, `as_lorentz`, `rho`, `lxy`, `check_bit`, `leading`, `pairs`). No matplotlib. |
| `plotting.py` | `set_plot_style`, `plot`, `plot_ratio`, `get_eff_hist`, `get_ratio_hist`, `plot_samples` (mplhep). |
| `schema.py` | `AnalysisSchema`: NanoAODSchema, tolerant of skims, reading one momentum representation per collection, and index branches stored as floats as the integers they are. |
| `metadata.py` | `write_run_metadata`, `write_merge_metadata`, `load_run_metadata`: the `.meta.yaml` sidecar of a saved output. |
| `check.py` | `python -m PKG.tools.check`: static consistency checks and an optional short run. |

`PKG/__init__.py` holds `BASE_DIR` (where the package is, for finding `configs/` and
`data/`) and `TREE_NAME` (the tree the events are read from).

`.analysis_framework.json`, next to the package, records what the framework was
scaffolded from (the objects and their collections, the tree, the trigger paths, the
sample names) and the components added since, with their options. The tools read it
for defaults and to update a component; edits made by hand to the definitions and
configs do not update it, and nothing depends on its list of objects or samples
being current.

## The analysis: `PKG/definitions/`

| File | Holds | Shape |
|------|-------|-------|
| `objects.py` | `primary_objs`, `derived_objs`, `optional_objs` | `name -> lambda evts: ...`, `name -> lambda objs: ...`, a list of names |
| `cuts.py` | `obj_cut_defs`, `evt_cut_defs` | `obj -> name -> lambda objs, obj: mask` and `name -> lambda objs: mask` |
| `hists.py` | `hist_defs`, `counter_defs`, and the `obj_attr` / `obj_eta_phi` / `make_2d` helpers | `name -> Histogram`, `name -> lambda objs: number` |
| `weights.py` | `generator_weight`, `event_weight`, `object_weight` | functions |

Object names are the vocabulary of the analysis: `objs["muons"]` in an event cut is
whatever `primary_objs["muons"]` built, after the channel's object cuts. `evt_weights`
and `ch` are reserved names.

`optional_objs` lists the objects a sample is allowed not to have. Every other object
must build in every sample.

## The choices: `PKG/configs/`

`selections.yaml`: each top-level entry is a channel.

```yaml
_object_cuts: &object_cuts        # leading underscore: a building block, not a channel
  muons: &muons_base
    - "pT > 10 GeV"
_event_cuts: &event_cuts
  - "PV filter"

baseline:
  obj_cuts:                       # per object; slims collections
    <<: *object_cuts
  evt_cuts:                       # in order; one cutflow row each
    - *event_cuts
  description: optional free text
```

`hist_collections.yaml`: each top-level entry is a list of histogram names; lists may
include other lists by anchor.

`samples/*.yaml`: `{tag: {path, year, samples: {name: {path, files, is_data, year, skim_factor}}}}`.
A tag names a group of samples; a file may hold several groups.
`cross_sections.yaml`: `{sample name: pb}`. `run_periods.yaml`: `{year: {lumi: /pb, golden_json: file in data/}}`.

Every config is a mapping of names to values, and the names are read as text: a run
period written `2018:` and one written `"2018":` are the same entry, and so is the
`year: 2018` of a sample. A luminosity or cross section of 0 is read as "not filled in
yet": the sample is left unscaled with a warning rather than scaled to nothing.

Yaml anchors (`&name`, `*name`, `<<: *name`) are how configs avoid repeating
themselves. Lists pasted inside lists are flattened when read. An anchor must be
defined above the place it is used. Entries whose name starts with an underscore
cannot be run.

## What a chunk goes through

1. Metadata of the dataset: `is_data`, `year`, `skim_factor` (from the sample config).
2. Data only: events outside the period's golden JSON are dropped. With none
   configured, everything is kept and a warning says so. A golden JSON that is
   configured but missing, or that cannot be read, stops the run in every mode.
3. Weights: `sum of generator_weight / skim_factor` is recorded for normalisation
   (summed in double precision, so that it does not depend on how the events are
   split into chunks); `event_weight` is what gets filled. A failure here stops the
   run in every mode.
4. Primary objects are built, in the order they are defined. One whose collection is
   not in the file is skipped with a warning if it is listed in `optional_objs`, and
   stops a strict run otherwise. Jagged collections with a `pt` are ordered by
   descending pT.
5. Per channel, starting again from the primary objects:
   - object cuts on the primary objects, one object after another in definition
     order. While an object is being cut, the objects defined before it are already
     cut, those after it are not, and no derived object exists. An object a cut has
     no answer for (`None`) does not pass. If the cut has no answer for a whole event
     (it compared with the leading jet of an event that has none), that event keeps
     an empty list of the object;
   - derived objects in definition order, each ordered by pT if it is jagged with a
     `pt`, each followed by its own object cuts;
   - `object_weight`, which multiplies the channel's event weights from here on: the
     cutflow, its first row included, and the histograms;
   - event cuts in the order the selection lists them, each seeing only the events
     that passed the ones before;
   - histograms and counters, with the events that are left. Each axis of a
     histogram gives one entry per event (a value or a list); the axes of one
     histogram are paired event by event, and the weight of an event goes with every
     entry of its list.

After all chunks are merged, `postprocess` multiplies the weighted cutflow columns and
the histograms of each simulated sample by `lumi * xs / (sum of generator weights /
skim_factor)`. Data is untouched. A sample with no cross section or luminosity is left
unscaled and says so in its warnings: its weighted numbers are then sums of generator
weights.

That sum runs over the events of *one run*. When a sample is processed in several
runs (batch jobs over slices of its files), each output is scaled as if its slice
were the whole sample, so adding the outputs by hand counts the sample once per run.
`utilities.merge_outputs([...])` (or `python -m PKG.scripts.merge_outputs`) takes the
scaling of each piece out, adds the pieces and scales the total once. A single run
over a dask cluster needs none of this.

The luminosity comes from the processor's `run_periods_cfg` (`configs/run_periods.yaml`
unless another file was named when the processor was built). `merge_outputs` and
`scale_to_lumi_xs` take the same `run_periods_cfg` argument, for outputs produced
with a file of their own.

`is_data`, `year` and `skim_factor` reach the processor as metadata of the fileset.
coffea keeps what it learnt about a file for the rest of the python session, this
metadata included, so a Runner built without `metadata_cache={}` processes a file
with the values it was *first* run with. The generated notebook, scripts and check
all pass `metadata_cache={}`; a Runner written by hand must too.

## The output

```python
out[sample] = {
    "cutflow":  {channel: Cutflow},          # .rows[cut] = {"raw", "weighted", "not_applied"}
    "hists":    {name: hist.Hist},           # axes: channel, then the histogram's own
    "counters": {channel: {name: number}},
    "metadata": {"n_evts", "scaled_sum_weights", "n_removed_golden_json",
                 "year": {...}, "is_data": {...}, "unweighted_hist": {...},
                 "unavailable_objects": {...},
                 "lumixs_weight"},           # the last only once a sample was scaled
    "warnings": {message, ...},
}
```

The first cutflow row, `"None"`, is the count before any event cut. Object cuts have
no row in the cutflow: their effect is in the object counts (`counters`, the
`<object>_n` histograms) and in the rows of the event cuts that count the object, and
one that was skipped leaves a warning and nothing else.
Counters are not chosen by a config: every entry of `counter_defs` is filled in every
channel, as a plain (unweighted, unscaled) number over the events that pass the
channel's event cuts.
`"not_applied"` counts the chunks in which an event cut was skipped; such a row
repeats the counts of the row before it. `unavailable_objects` are the objects that
could not be built in at least one chunk.

`warnings` holds each distinct message once, however many chunks produced it.

Things in the output must add up across chunks: numbers add, sets take the union,
histograms and cutflows add. Strings, lists and `None` do not merge sensibly, which is
why per-sample facts are kept in sets.

`CheckAnalysisFramework` returns a summary of this, not the output itself: cutflow
rows as `[cut, events, weighted events]`, the counters, and the names of the
histograms that stayed empty. It does not return histogram contents. Its fields are
listed at the top of `pitfalls.md`.

## Strict and lenient

`AnalysisProcessor(..., strict=True)` is the default. What happens when something in
the definitions fails:

| What fails | strict | `strict=False` |
|------------|--------|----------------|
| an object listed in `optional_objs` reads something the sample does not have | warning, object absent | the same |
| any other object cannot be built | run stops, object named | warning, object absent |
| a cut, histogram, counter, derived object or `object_weight` uses an absent object | skipped with a warning ("... is not available in this sample"); a skipped event cut's cutflow row is marked "not applied" | the same |
| a cut fails for another reason (missing branch, wrong mask shape, an object used before it is built) | run stops, cut and channel named | warning, cut skipped; for an event cut the cutflow row is marked "not applied" |
| an object does not hold one entry per event when the events are cut | run stops | warning, object left uncut |
| `object_weight` fails | run stops | warning, weight not applied |
| a histogram or counter cannot be filled | warning, skipped | the same |
| `generator_weight` or `event_weight` fails | run stops | run stops |
| the golden JSON of the run period is missing or cannot be read | run stops | run stops |
| the *input file* cannot be read while any of the above is being evaluated | the error goes to coffea as it is | the same |

"Reads something the sample does not have" means exactly that: the collection or
branch is absent. An object whose definition is wrong in some other way is a mistake
whether it is optional or not.

The last row is about lazily read columns: a branch is only read when a cut first
uses it, so an unreadable input file can surface in the middle of a cut. Such an
error is recognised by where it was raised (uproot, fsspec or XRootD, underneath
coffea's column loader) and passed on unchanged, so that the Runner's
`skipbadfiles` can act on it. An error of the same type from the analysis's own
code (a correction file that is missing) is reported as a failing cut or weight, and
stops a strict run with `skipbadfiles` too: were it passed on, coffea would drop
chunk after chunk as if the input were bad.

Two consequences. A run that finishes can still have skipped things: the warnings and
the list of empty histograms are part of the result. And `strict=False` is a way to
see every problem at once (the chain report uses it), not a way to produce numbers: a
skipped cut means looser yields.

## Versions

The engine needs virtual arrays together with the processor `Runner`: coffea 2025.7
and newer, or one of the 2025.5 release candidates. It is written to avoid what
differs between those versions: the notebook and scripts call `runner(fileset,
processor_instance=..., treename=...)`, which returns the accumulated output
directly, and `postprocess` both modifies its argument and returns it. coffea 2026
checks the fields of vector collections when they are first used, which 2025 does
not: a collection that reads fine with one can raise with the other (see
`pitfalls.md`, "Reading the file"). What `skipbadfiles=True` skips, and how often a
failing chunk is tried again, also differs between versions (`pitfalls.md`, "Scaling
out"). Workers of a distributed run must have the same coffea as the client.
