# When something goes wrong

Start from what `CheckAnalysisFramework` returned. Read it in this order:

1. a formatted error instead of JSON: the check itself did not run (see the next
   section);
2. `static.errors`: nothing runs until these are fixed;
3. `run.error` (and `run.traceback`): the run stopped;
4. per dataset in `run.datasets`: `warnings`, `unavailable_objects`, then the
   `cutflow`, then `empty_hists`;
5. `static.warnings`: usually missing normalisation inputs.

`ok` is the verdict (`"status": "ok"` only says the checker ran). `ok: true` with
warnings is normal; the warnings still need reading.

## The check did not run

| Error | Cause and fix |
|-------|---------------|
| `Missing Dependency` | The interpreter that ran the check cannot import coffea, awkward, uproot or hist. The `python` it used is in the message. Install the framework bundle, or point the tool at an environment that has the project's requirements (`venv=...`, or the `analysis_python` setting). This is not a problem in the analysis. |
| `Timeout` | The check did not finish in `timeout_s` (50 s by default, because the tool server normally cuts calls at 60 s). Try once more, then lower `max_events` or name fewer `channels` / `hist_collections`. |
| `Check Did Not Run` | The checker crashed before writing its report; the context is its last output. `No module named '<the package>'` means the package directory is incomplete; `No module named '<a library>'` is a missing dependency. A mistake in a config or definition does not end here: it comes back as a static error. |
| `names cannot start with '-'` | A sample, channel, collection or period name beginning with a dash would be read as an option. Rename it. |
| `sample_config must be the name of a file ...` | `sample_config` names a file under `PKG/configs/samples/` (`"signal.yaml"`), never a path. |
| `... must be a whole number`, `... must be a list of names` | An argument of the wrong kind in the call. |
| `Not A Framework` | `project_dir` is not the directory the scaffold created (the one that contains the package directory). |
| `Project Not Found` | `project_dir` does not exist inside the working directory. A link that leads out of the working directory does not count as inside. |

## Static errors

| Message | Cause and fix |
|---------|---------------|
| `channel 'x' is not a selection ...` | The name is not a top-level entry of `selections.yaml`. |
| `'_x' is a building block ..., not a channel` | Entries starting with `_` hold anchors and cannot be run. |
| `[ch] muons cut 'x' is not defined ...` | The selection lists a cut that `obj_cut_defs["muons"]` does not have. Names must match exactly, spaces included. |
| `[ch] event cut 'x' is not defined ...` | Same, for `evt_cut_defs`. A cut defined under an object cannot be used as an event cut. |
| `[ch] 'x' has object cuts but is not defined ...` | The object name in the selection is not in `primary_objs` or `derived_objs`. |
| `[ch] event cuts listed more than once` | Usually an anchor pasted twice. Each cut is one cutflow row. |
| `[ch] unknown key 'x'` | A typo such as `evt_cut:`. Allowed: `obj_cuts`, `evt_cuts`, `description`. |
| `[ch] obj_cuts must give the cuts object by object` | `obj_cuts` was written as a list, the way `evt_cuts` is. It is a mapping: `obj_cuts:` then `<<: *object_cuts` or `muons: [...]`, not `- *object_cuts`. |
| `the configs cannot be read (... must hold 'name: value' entries ...)` | A config file whose top level is a list or a single value. Every config is a mapping of names. |
| `configs/samples/x.yaml cannot be read (...)` | A group or sample of the wrong shape: `tag: {path, year, samples: {name: {files: [...], ...}}}`. |
| `run period 'x' ... must be laid out as {lumi: ..., golden_json: ...}` | The period was given a bare number. |
| `the cross section of sample 'x' cannot be read` | Its entry in `cross_sections.yaml` is not a number. |
| `the static checks could not be completed (...)` | A definition has a shape the check did not expect; the traceback under the message shows which. |
| `histogram 'x' ... is not defined` | A collection lists a histogram that `hist_defs` does not have. |
| `axis name 'x' is reserved` | Rename the axis: not `weight`, `sample`, `threads`, `channel`, and not empty. |
| `the analysis cannot be imported` | Usually a python error in `definitions/`: the traceback names the file and line. If the missing module is a library, see "The check did not run". |
| `golden JSON 'x' ... is not in .../data` | Copy the file into `PKG/data/` or fix the name in `run_periods.yaml`. |

A yaml error such as `found undefined alias` means an anchor is used above its
definition, or was renamed in one place only.

Static warnings worth knowing: `has no cross section`, `has no lumi` and `is in run
period 'x', which is not in configs/run_periods.yaml` are missing inputs, to be
reported, not guessed. `is defined in several groups` means a sample name is used in
two groups of one sample config and must be asked for with its `tag`.

## The run stops

The run's `error` is a chain, outermost first: coffea's own "Failed processing file
..." wrapper, then the line that names the cut, object or weight, then what Python
raised inside it. Read the line that starts with `caused by RuntimeError`.

- `primary object 'x' could not be built (AttributeError: no field named 'X')`: the
  collection or branch is not in this file. Check the name with
  `InspectFile(collection=...)`. If the object is right and this sample simply does
  not have it, add its name to `optional_objs` in `objects.py`.
- `... cut 'x' could not be applied/evaluated (AttributeError: no field named 'y')`:
  the cut reads a branch this file does not have. If only some samples lack it, that
  is a real difference between them: see "Data versus simulation" in `howto.md`.
- `returned a mask with 2 dimensions`: an event cut returned a per-object mask. Wrap
  it: `ak.num(objs["muons"][mask], axis=1) >= 1`, or `ak.any(mask, axis=1)`.
- `must return one True/False per object`: an object cut returned a per-event mask,
  typically from `ak.num(obj) > 0`. Object cuts compare objects, not events.
- `holds one entry per event, so it cannot take object cuts`: `MET`, `PV`, `HLT` and
  `Flag` are records. Use an event cut: `lambda objs: objs["pvs"].npvsGood >= 1`.
- `'x' has not been built yet at this point`: an object cut of a primary object used
  a derived object, or a derived object used one defined below it (objects a
  component added are defined in its block, at the end of `objects.py` unless you
  moved it). Reorder, or make the comparison a derived object or an event cut.
- `'x' is not an object`: a typo in `objs["..."]`; the message lists what is defined.
- `index out of range`: `objs["x"][:, 0]` on an event with no `x`. Put the
  multiplicity cut earlier in `evt_cuts`, or use `ak.firsts` / `ak.pad_none` and
  `ak.fill_none(..., False)`.
- `FileNotFoundError` inside a cut, weight or histogram: one of the analysis's own
  files (a correction file) is not where the code looks. Paths are best built from
  `BASE_DIR`: `os.path.join(BASE_DIR, "data", "sf.json")`.
- An `OSError` of any other kind inside a cut, weight or histogram (`Input/output
  error`, `Not a gzipped file`, `Permission denied`): the same, for a file of the
  analysis that exists and cannot be read. These stop a strict run even with
  `skipbadfiles=True`, which is about the *input* files only.
- `golden JSON for run period 'x' not found` / `could not be read`: configured in
  `run_periods.yaml` but not in `PKG/data/`, or not a valid golden JSON. Stops the run
  in both modes.
- `definitions/weights.py: the event weights could not be computed`: `event_weight` or
  `generator_weight` failed. Without weights nothing can be filled, so this stops the
  run in lenient mode too. A simulated sample without `genWeight` needs its own
  `generator_weight`.

`strict=False` (or `--no-strict`) turns the failures of objects, cuts and
`object_weight` into warnings so that one of them does not hide the rest. Fix them
all the same: a skipped cut means looser yields.

## "... is not available in this sample"

A cut, histogram, counter or derived object needs an object that is absent from the
sample. For the objects listed in `optional_objs` that is how it is meant to work:
generator-level objects on data, for instance. The cut is skipped and its cutflow row
is marked "not applied"; `unavailable_objects` lists what was absent.

If it appears for an object that should exist in that sample, read the reason in
brackets after the first such warning:
`'gens' is not available in this sample (AttributeError: no field named 'GenPart')`.
A misspelt collection name on an optional object looks exactly like this.

In lenient mode the same thing is reported as `'x' could not be built and is
unavailable`: that one is a failure, not an absence.

## The run finished, but ...

A run that finishes can still have skipped things. Histograms and counters never
stop a run:

- `histogram 'x' could not be filled and was skipped (...)`: its fill function
  failed; the reason is in brackets. If it stayed empty in every channel, the
  histogram is also in `empty_hists`.
- `counter 'x' could not be filled and was skipped (...)`: the same for a counter.
- `... (ValueError: cannot broadcast nested list)`: two axes of one histogram returned
  lists of different lengths for the same event (muons on one axis, electrons on the
  other). Only fields of the *same* objects pair up; for combinations of different
  objects build the pairs first (`ak.cartesian`) and histogram their fields.
- `... axis 'x' does not have one entry per event`: a fill function returned fewer or
  more entries than there are events passing the histogram's `evt_mask`. Usually the
  mask was applied on one axis and not on the other, or a function filtered events
  itself.

## A cutflow drops to zero

Find the first row that does. Then:

- right after the trigger cut: the trigger rarely fires in this sample, or is not
  simulated in it (a path that does not exist at all stops the run instead);
- at a multiplicity cut: the *object* cuts before it are too tight. Run the `all`
  channel beside it and compare the multiplicity histograms (`muon_n`, `jet_n`, ...);
- at a veto (`ak.num(objs["x"], axis=1) == 0`) that rejects everything: check that
  `x` is the object you mean; an object cut that has no answer for an event leaves it
  with zero objects, which a veto then accepts, not rejects;
- at the first row on data, with `n_removed_golden_json` equal to `n_events`: the
  golden JSON does not cover this run range, or the file is simulation marked
  `is_data`.

A run over few events can legitimately end at zero for a tight selection; raise
`max_events` before concluding anything.

## A histogram stays empty

`empty_hists` lists histograms nothing was filled into, in any channel that was run.

- its fill function failed: there is a matching `could not be filled` warning;
- it needs an object that is absent: there is a matching `not filled: 'x' is not
  available in this sample` warning;
- its `evt_mask` passes nothing in the channels that were run;
- no event survived the selection.

Values outside the axis range are not the reason: the check counts the overflow bins
too (a *plot* drawn without them can still look empty).

## Weighted yields look wrong

- `not scaled to lumi * xs (KeyError: no cross section for ...)`: add the sample to
  `cross_sections.yaml` under exactly the name it is processed as.
- `not scaled to lumi * xs (KeyError: no luminosity for run period ...)`: the sample's
  `year` is not in `run_periods.yaml`, or has no `lumi`.
- `not scaled to lumi * xs (ValueError: lumi * xs / sum of weights is 0.0 ...)`: the
  luminosity or the cross section is still 0. Zero is read as "not filled in yet".
- A change to a sample's `skim_factor`, `year` or `is_data` has no effect on a second
  run in the same notebook or python session: the Runner was built without
  `metadata_cache={}`, and coffea reused what it had cached for the file. The
  generated notebook, scripts and check pass it; code written by hand must too.
- A processor built with its own `run_periods_cfg` is normalised with that file.
  `utilities.merge_outputs` takes the same argument; without it, it uses the
  package's `configs/run_periods.yaml`.
- Off by a constant factor on a skim: `skim_factor` is the fraction of events the skim
  *kept*.
- Running over part of a sample is fine: the normalisation uses the generator weights
  of the events that were processed.
- Too large by about the number of jobs: outputs of separate runs over one sample were
  added by hand. Each was normalised on its own; combine them with
  `utilities.merge_outputs` instead.
- `unweighted_hist=True` fills histograms with weight 1 and does not scale them; the
  cutflow's weighted column is still scaled.
- A scale factor has no effect: it was put in `generator_weight`, where it cancels.

## Reading the file

- `ValueError: ... conflicting azimuthal coordinate representations`: a collection
  stores (px, py, pz) next to (pt, eta, phi). `AnalysisSchema` hides the cartesian set
  for collections coffea knows. If the error persists, the file was opened with plain
  `NanoAODSchema`, or `hide_duplicate_momenta` was switched off.
- `ValueError: ... multiple temporal aliases` or `conflicting longitudinal ...`: the
  collection has other branches coffea reads as coordinates (an `energy` next to a
  `mass`, a position stored as `x`, `y`, `z`). `InspectFile` lists these under
  `schema_notes` as `conflicting_coordinates`, with the branches to hide: pass them as
  `hidden_branches` to the `schema` component. Only coffea 2026 and newer check this.
- `LLVM IR parsing error: invalid cast opcode ...` when a generator particle's
  `children` (used by `to_pid` and `utilities.lxy`) or `distinctChildren` is used: a
  numba / llvmlite pair that miscompiles coffea's navigation kernels (seen with numba
  0.67.0 and llvmlite 0.49.0). Install `numba<0.67` in the environment that runs the
  analysis.
- `No chunks survived preprocessing`, `No chunks returned results`, or `cannot unpack
  non-iterable NoneType`: there was nothing to process. Every file was empty, or every
  chunk was skipped as unreadable (`skipbadfiles=True`); the warnings printed before
  the error say which.
- `requires the 'charge' field`, `missing temporal coordinate`, or `Vertex requires
  fields ['x', 'y', 'z']`: a collection coffea knows lacks a branch its behaviour
  needs, typically dropped by a skim. `InspectFile` lists these under `schema_notes`
  as `missing_fields` and says what to do: with the `schema` component, supply the
  field (`constant_fields={"Muon_charge": 0}`, lists of objects only) or read the
  collection as a plain one (`mixins={"PV": "NanoCollection"}`). For a custom
  collection, `as_lorentz(..., mass=...)` in `objects.py`. Only coffea 2026 and newer
  check this; the scaffold leaves such a standard collection out of the default
  objects and says so in its notes.
- A bare `RuntimeError` with no message when a cross-reference is first used
  (`obj.matched_jet`, an `...IdxG` field, `follow(...)`): coffea refuses an index
  branch that is not stored as integers. `AnalysisSchema` converts such branches;
  the error means the events were read with another schema.
- `no field 'xIdxG'` for a cross-reference set up with the `schema` component: coffea
  only makes one when the file has both the index branch and the `n<Target>` counter
  of the collection it points into (and a nested item only when all of its index
  branches are there). Nothing is said at file open, because the schema is told not
  to complain about missing cross-references.
- A collection has only one field, or its fields are missing, although the branches
  are in the file: there is a branch named exactly like the collection (`Foo` next to
  `Foo_pt`), and coffea reads `Foo` as that branch. `InspectFile` reports it as
  `shadowed_branches`; hide the branch with the `schema` component
  (`hidden_branches=["Foo"]`).
- A custom collection has no `delta_r`: it was not wrapped. See "Add an object" in
  `howto.md`.
- The tree is not called `Events`: the scaffold's `tree_name` sets `TREE_NAME` in
  `PKG/__init__.py`, which the scripts, the notebooks and the check all use.
- `GenPart` has entries with `pt = 0` (the incoming partons): cut them before doing
  kinematics, `obj.pt > 0` or a `statusFlags` requirement.
- A run hangs, with one core busy, in something that uses `part.distinctParent`: the
  file has a generator particle listed as its own mother (seen in skimmed ntuples),
  and coffea follows the chain of mothers without a limit. `part.parent` and
  `part.children` are not affected. cms-sidm/SIDM replaces coffea's kernel with a
  capped one for this reason (`sidm/tools/gen.py`); do the same, or stay with
  `parent`, if the files are not known to be clean.
- Using a raw index branch after cuts (`objs["jets"][objs["muons"].jetIdx]`) picks the
  wrong objects: `jetIdx` counts positions in the *original* collection. Use the
  cross-reference attributes (`objs["muons"].matched_jet`), which keep pointing at the
  right object.

## Scaling out

- `ModuleNotFoundError` for the analysis package on a worker: the package was not
  shipped. Use `scaleout.make_dask_client` / `make_lpc_client`, or install the package
  on the workers.
- `No module named 'dask.dataframe'` (or pandas, pyarrow) from `DaskExecutor`:
  install `dask[dataframe]`; coffea's dask executor imports it.
- Works iteratively, fails on workers with a pickling error: something in the
  *output* is not picklable. Keep outputs to numbers, sets, histograms and cutflows.
- Different numbers from the same job: a worker has a different coffea. Pin versions.
- `ValueError: Empty list provided to reduction`, or "Operation expired" in worker
  logs: the storage is timing out, not the code. `skipbadfiles=True` only helps when
  some files are readable.
- `skipbadfiles=True` skips a chunk when reading the *input* fails, and says so in a
  Python warning, not in the output: a long run can come back short. Compare
  `out[sample]["metadata"]["n_evts"]` with what the sample should have. Which read
  failures count depends on the coffea version:

  | coffea | skipped with `skipbadfiles=True` | a failing chunk is tried |
  |--------|----------------------------------|--------------------------|
  | 2025.5 release candidates | a file or tree that is not there; "Invalid redirect URL", "Operation expired" and "Socket timeout" errors | once |
  | 2025.7 to 2025.10 | any `OSError`, and a missing tree | once |
  | 2025.11 to 2026.7 | any `OSError` | four times when skipping, once otherwise |
  | 2026.9 | any `OSError` | four times |

  The framework's own check sets the retries to zero, so a failure there is reported
  once.
- An edit to the package does not reach workers that are already running: the
  package is shipped as it was when the client was made. Ship it again with
  `scaleout.upload_package(client, restart=True)`.
- `no worker image for coffea X / python Y`: `find_lpc_image` found no image on cvmfs
  for this environment's versions. The message lists the images it saw; pass one as
  `image=`, or use an environment that matches one. The images are named
  `coffea-dak-almalinux9:<coffea version>-py<python version>` (earlier
  `coffea-dask-...`).
- `HTCondor configuration not found`: `make_lpc_client` reads
  `condor/lpc_condor_config` next to the package. Work from the repository with the
  package installed editable, or pass `condor_config=`.

## Changing the engine

If the fix seems to be in `tools/`, look again: nearly everything an analysis needs
is expressible in `definitions/`. Two cases are legitimate. One is a genuine engine
change (a new kind of output, a systematics loop): make it deliberately, keep it
analysis-independent, and re-run the whole check afterwards. The other is a fault in
the generated engine itself, which is new code: if a freshly scaffolded project fails
its first check, or an error points into `tools/` with nothing of yours involved, say
so to the user with the full error, and say what you changed if you fix it.
