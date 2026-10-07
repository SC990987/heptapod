# __PROJECT_TITLE__

__PROJECT_DESCRIPTION__

A columnar analysis built on [coffea](https://coffea-hep.readthedocs.io). Events are
read from NanoAOD-like ROOT files, objects and events are selected by named cuts, and
the result is a set of cutflows and histograms per sample and per channel.

> **Starting point, not an analysis.** The cuts in `analysis_pkg/definitions/cuts.py`
> and the selections in `analysis_pkg/configs/selections.yaml` were generated so that
> there is something to run. Their thresholds are placeholders: replace them with this
> analysis's own before reading anything into a yield.

## Getting started

Python 3.10 or newer is recommended: current coffea releases require it.

In a new environment:

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

In an environment that already has coffea (one you run other analyses in), install
only the package: `python -m pip install -e .`. `requirements.txt` asks for coffea
2025.7 or newer, and `pip install -r` would upgrade an environment that is pinned to
something else, a 2025.5 release candidate for instance, together with everything
coffea depends on. The engine runs on those release candidates as well.

Then check it:

```bash
python -m analysis_pkg.tools.check                 # are the configs and definitions consistent?
python -m analysis_pkg.tools.check --sample __EXAMPLE_SAMPLE__   # ... and does it run on a configured sample?
```

Install it editable (`-e`): the package finds its configs relative to its own location,
and an editable install makes your working tree the thing that is imported.

Then open `analysis_pkg/test_notebooks/test_processor.ipynb`, or run it from a shell:

```bash
python -m analysis_pkg.scripts.run_analysis --samples __EXAMPLE_SAMPLE__ --channels baseline --hists base -o output.coffea
```

## Code structure

Everything of interest is in `analysis_pkg/`:

| Directory | What lives there |
|---|---|
| `tools/` | The engine. `processor.py` defines how events are analysed; `selection.py`, `histogram.py` and `cutflow.py` are its parts; `utilities.py`, `plotting.py`, `metadata.py`, `schema.py` and `check.py` support it. Nothing here is specific to this analysis. |
| `definitions/` | What the analysis is: `objects.py` (the collections and how they are built), `cuts.py` (every available cut), `hists.py` (every available histogram), `weights.py` (event weights). |
| `configs/` | Which cuts make up a selection (`selections.yaml`), which histograms make up a collection (`hist_collections.yaml`), where the samples are (`samples/`), their cross sections and the run periods. |
| `data/` | Golden JSONs and other small inputs. |
| `scripts/` | Command-line entry points. |
| `test_notebooks/` | Notebooks that exercise the machinery. Re-run them after changing the engine: the output should not move unless you meant it to. |
| `studies/` | Where the physics happens: one notebook per study, written like a page of a lab notebook. |

Definitions say what *can* be done; configs choose what *is* done. You run the
processor by naming selections and histogram collections:

```python
from coffea import processor
from analysis_pkg import TREE_NAME                    # the tree the events are in
from analysis_pkg.tools import utilities
from analysis_pkg.tools.processor import AnalysisProcessor
from analysis_pkg.tools.schema import AnalysisSchema

fileset = utilities.make_fileset(["__EXAMPLE_SAMPLE__"], max_files=1)
runner = processor.Runner(executor=processor.IterativeExecutor(), schema=AnalysisSchema,
                          chunksize=100_000, metadata_cache={})
p = AnalysisProcessor(["baseline"], ["base"])
out = runner(fileset, processor_instance=p, treename=TREE_NAME)

out["__EXAMPLE_SAMPLE__"]["cutflow"]["baseline"].print_table()
out["__EXAMPLE_SAMPLE__"]["hists"]["__EXAMPLE_HIST__"][{"channel": "baseline"}]
```

Always give the Runner `metadata_cache={}`. Left to itself, coffea remembers for the
rest of the python session the sample metadata (`is_data`, `year`, `skim_factor`) each
file was first run with: edit a sample's settings, run again in the same notebook, and
the old values would be used without a word.

### What the processor does

For each chunk of events the primary objects are read and the jagged ones ordered by
pT. Then, for each channel:

1. object cuts slim the primary collections (they never reject an event), one object
   after another in the order the objects are defined;
2. derived objects are built from what is left, in the order they are defined, and
   their own object cuts are applied;
3. event cuts are applied one after another, and the cutflow records each step;
4. histograms and counters are filled with the surviving events.

Simulation is scaled to luminosity times cross section at the end. The output is

```
out[sample]["cutflow"][channel]     Cutflow (print_table(), rows)
out[sample]["hists"][name]          hist.Hist with a "channel" axis
out[sample]["counters"][channel]    {name: number}
out[sample]["metadata"]             n_evts, scaled_sum_weights, year, is_data, lumixs_weight, ...
out[sample]["warnings"]             everything the processor skipped, and why
```

**Read the warnings.** What goes wrong is handled in one of three ways:

- *Does not apply here.* An object listed in `optional_objs` (in
  `definitions/objects.py`) may be missing from a sample, as generator-level objects
  are from data. Cuts, histograms and derived objects that need it are skipped in
  that sample and reported in the warnings. A skipped cut keeps its cutflow row,
  marked "not applied".
- *A mistake.* Any other object that cannot be built, and any cut or object weight
  that fails (a misspelt branch, a mask of the wrong shape, an object used before it
  is built), stops the run and names the culprit. Building the processor with
  `strict=False` turns these into warnings instead, which is useful to see all of
  them at once and never the way to produce a result.
- *Never fatal.* A histogram or counter that cannot be filled is skipped with a
  warning in both modes. So a clean exit is not a clean run: look at the warnings and
  at which histograms stayed empty (`python -m analysis_pkg.tools.check --sample ...`
  lists both).

A failure in `generator_weight` or `event_weight` always stops the run, and so does a
golden JSON that is configured for a run period but cannot be read.

An input file that cannot be read is a different matter and is left to coffea: the run
stops, unless the Runner was given `skipbadfiles=True`, in which case the file (or the
chunk) is left out. Nothing in the output says what was left out, so compare
`out[sample]["metadata"]["n_evts"]` with what the sample should hold.

## How-tos

### Add a cut

1. Add an entry to `obj_cut_defs[<object>]` or `evt_cut_defs` in
   `analysis_pkg/definitions/cuts.py`. An object cut is `lambda objs, obj: <mask per object>`
   and slims a collection. An event cut is `lambda objs: <mask per event>` and rejects
   whole events; it sees the collections *after* the object cuts.
2. Add its name to a selection in `analysis_pkg/configs/selections.yaml`, under `obj_cuts:
   <object>:` or `evt_cuts:`. Selections that include that selection through a yaml
   anchor pick it up too.

Inside an object cut, `obj` is the collection being cut and `objs` holds the others:
primary objects defined above it already have their cuts applied, those below it do
not yet, and derived objects do not exist at that point (cut on those in the derived
object's own cuts).

### Add a histogram

1. Add an entry to `hist_defs` in `analysis_pkg/definitions/hists.py`. `obj_attr("muons", "pt")`
   covers the common case; for anything else build a `Histogram` from `Axis` objects,
   each a `hist` axis plus a function `(objs, mask) -> values`.
2. Add its name to a collection in `analysis_pkg/configs/hist_collections.yaml`.

A fill function returns one entry per event: a number, or a list of numbers (one per
muon, say). With two axes the entries are paired event by event: a number on one axis
goes with every number of a list on the other, two lists must be equally long, and
whatever is missing (`None`) on one axis is left out on both.

### Add an object

Add it to `primary_objs` (read from the events: `lambda evts: evts.Tau`) or to
`derived_objs` (built from selected objects: `lambda objs: ...`) in
`analysis_pkg/definitions/objects.py`. Derived objects are built in the order they appear,
so one may use those defined before it. If some samples do not have what the object
reads, add its name to `optional_objs` in the same file.

Every jagged collection is ordered by pT and cut on its own, so two objects do not
stay aligned element by element. To keep something with each object, make it a field
of that object (`ak.with_field(muons, their_tracks, "track")`) rather than a second
object.

### Add a sample

By hand, in `analysis_pkg/configs/samples/samples.yaml` (the comments at its top describe
the format), or from a directory listing:

```bash
# every sub-directory of the directory that holds ROOT files becomes one sample
python -m analysis_pkg.scripts.add_samples -o samples.yaml -t v2 -d /path/to/ntuples --year 2018
# ... or the directory itself is one sample
python -m analysis_pkg.scripts.add_samples -o samples.yaml -t v2 -d root://host//store/x --name MySignal --year 2018
# data
python -m analysis_pkg.scripts.add_samples -o samples.yaml -t data18 -d /path/to/data --year 2018 --data
```

`-t` names the group the samples are added as. The script rewrites the config as plain
yaml, so comments in it are lost; point `-o` at a new file (`-o signal.yaml`) to keep
`samples.yaml` as it is, and pass `location_cfg="signal.yaml"` to `make_fileset` (or
`--location-cfg signal.yaml` to the scripts).

Then:

- simulation: put the cross section (pb) in `analysis_pkg/configs/cross_sections.yaml`
  under the sample's name. Without it, or without a `lumi` for the sample's `year` in
  `configs/run_periods.yaml`, the sample is left unscaled and a warning says so;
- data: `is_data: true` (`--data`), a `year` that exists in `configs/run_periods.yaml`,
  and that period's golden JSON in `analysis_pkg/data/`, named there as `golden_json`.

The events are read from the tree named `TREE_NAME` in `analysis_pkg/__init__.py`
(currently `__TREE_NAME__`).

### Add a scale factor

Event-level corrections go in `event_weight` and corrections that depend on the
selected objects in `object_weight`, both in `analysis_pkg/definitions/weights.py`.

### Save and reload an output

```python
from analysis_pkg.tools import metadata, utilities
utilities.save_output(out, "study.coffea")
metadata.write_run_metadata("study.coffea", fileset=fileset, channels=["baseline"],
                            hist_collections=["base"])
out = utilities.load_output("study.coffea")
```

The `.meta.yaml` sidecar records the selections, histograms, input files, code commit
and coffea version that produced the file.

### Split a sample over several runs

Each run scales simulation to luminosity times cross section using the events *it*
processed. Outputs of separate runs over parts of one sample (batch jobs, say) must
therefore not be added by hand: the sample would be counted once per run. Merge them
instead, which takes the scaling of each piece out, adds the pieces, and scales the
total once:

```bash
python -m analysis_pkg.scripts.merge_outputs job_*.coffea -o merged.coffea
```

or `utilities.merge_outputs([...])` in python, with outputs or paths.

## Suggested workflow

1. Make a branch with a descriptive name.
2. Start a notebook in `analysis_pkg/studies/` and say in words what you are trying to learn.
3. Use the processor with existing selections and collections. When you need a new
   cut, histogram or object, add it as above and run `python -m analysis_pkg.tools.check`.
4. Commit as you go.
