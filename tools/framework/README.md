# Analysis framework tools (`framework` bundle)

Three tools that write, extend and check a coffea analysis framework for a new
analysis, plus the file-inspection tool they rely on:

| Tool | Module | Does |
|------|--------|------|
| `InspectFileTool` | `tools/analysis/nanoaod_inspect.py` | Reports the collections of a NanoAOD-like file and what will need care when coffea reads it |
| `ScaffoldAnalysisFrameworkTool` | `scaffold.py` | Writes the framework, matched to a representative file |
| `AddFrameworkComponentTool` | `components.py` | Adds `lepton_jets`, `scaleout`, `schema` or `chain_report` |
| `CheckAnalysisFrameworkTool` | `check.py` | Runs the framework's own self-check and returns its report |

The `framework` skill (`skills/framework/`) teaches an agent how to use them and how
to work inside the generated package. `examples/framework/` launches an agent on them.

**Read [Verification status](#verification-status) before relying on any of it.**

## What gets generated

```
my_analysis/
├── my_analysis/
│   ├── tools/          copied from template/core: the engine, identical for every analysis
│   ├── definitions/    rendered: objects.py, cuts.py, hists.py   (weights.py is copied)
│   ├── configs/        rendered: selections, hist collections, samples, cross sections, run periods
│   ├── scripts/        copied: run_analysis, add_samples, merge_outputs
│   ├── test_notebooks/ rendered: test_processor.ipynb
│   └── data/, studies/
├── .analysis_framework.json     what the project was built from (objects, samples, tree, components)
└── README.md, setup.py, requirements.txt, MANIFEST.in, .gitignore
```

The generated project does not import HEPTAPOD. It is the user's code from then on.

## How the code here is organised

| File | Role |
|------|------|
| `template/core/` | The project skeleton. The package is called `analysis_pkg` here and renamed when copied; `__TOKEN__` placeholders are filled in. |
| `template/components/<name>/` | Files of each optional component. |
| `_render.py` | Renders the analysis-specific half (definitions, configs, notebook) from a list of object specs, and holds the rules for names and for writing values as python and yaml. Pure python, returns text. |
| `_projects.py` | Copies templates, keeps the marker file, inserts and replaces marked blocks in files the analyst also edits, and reads names out of those files without importing them. All writing goes through `Writes`, which undoes every write of an operation if one of them fails. |
| `_scaffold.py` | `create_project(...)`: the whole scaffold as one function. Validates and renders everything before it writes anything. |
| `_components.py` | `apply_component(...)`: the four components. |
| `scaffold.py`, `components.py`, `check.py` | The tools: thin adapters that confine paths to `base_directory` and turn exceptions into formatted errors. |
| `../analysis/nanoaod_layout.py` | Describes a tree from its branch names: collections, kinds, what coffea's schema will do with them. |
| `tests/synthetic.py` | Writes a small NanoAOD-like file with a known answer. |
| `tests/test_framework.py` | Tests of everything on this side. Needs no analysis library. |
| `tests/test_framework_run.py` | Tests that run generated frameworks for real. Needs coffea, awkward, uproot, hist; fastjet and dask for two of them. |

Nothing below `template/` is imported by HEPTAPOD (`conftest.py` keeps pytest out of
it). To change what every generated framework contains, edit the template; to change
what is chosen per analysis, edit `_render.py`.

## How a framework is extended

There is deliberately no tool per kind of change. A cut, a histogram, a channel, an
object, a sample: the agent writes the definition in `definitions/` and names it in
`configs/` itself, following the `framework` skill, and `CheckAnalysisFrameworkTool`
says whether the result holds together. What the check returns is shaped for that
loop:

- a name defined twice in a definitions file is a static error (python would let
  the later definition replace the earlier one without a word);
- `static.selections` holds the cuts each channel applies once the yaml anchors and
  merges are resolved, which is the proof that an edit landed where it was meant to;
- `static.unused_hists` names histograms that no collection lists, and per dataset
  `empty_hists` and `empty_in_channels` say where a histogram received nothing;
- `counters` and the cutflow rows are the numbers to compare before and after;
- `static.samples` and `static.run_periods` show what the configs say about each
  sample and period, and `files_in_sample` that the run read one file of several;
- `sample` takes several names, so one call covers a simulated and a data sample.

A component adds files and, where the analyst's own files are involved, appends a block
between `# >>> component: NAME >>>` and `# <<< component: NAME <<<`. Applying one
again with the same options changes nothing; `overwrite=true` regenerates files and
blocks.

Three rules keep what is on disk and what is recorded about it in step:

- **All or nothing.** A scaffold, and each application of a component, either writes
  everything (the marker last) or leaves the directory as it found it, also when a
  write fails half-way.
- **The marker records what was written, when it was written.** A request that would
  leave existing blocks in place under other options is refused, as is one that
  finds blocks with no record of their options. For the schema, the tables in
  `tools/schema.py` are themselves what later calls build on, so they can be edited
  by hand; a file edited anywhere else is kept and nothing is recorded. Edits made by
  hand inside a block are not tracked.
- **A framework is never scaffolded over.** `overwrite=true` lets the scaffold write
  into a directory that already has files (a fresh repository), never into one that
  holds a framework: its definitions and configs are somebody's analysis.

Before a component defines a name (a channel, a histogram, a cut, an object), the
analyst's files are read to see whether it is taken: python with `ast`, yaml with a
yaml parser. A block whose marker lines are damaged is not touched.

## Design: what was kept from cms-sidm/SIDM and what changed

Kept: the split into an engine (`tools/`), named definitions (`definitions/`) and
configs that choose among them (`configs/`); cuts and histograms as small functions of
`objs`; selections and histogram collections assembled with yaml anchors; one
`channel` axis per histogram; the `Cutflow` accumulator; `test_notebooks/` and
`studies/`; the metadata sidecar; the dask and LPC scale-out helpers; the chain report.

Changed, to make it independent of one analysis:

- **No lepton jets in the processor.** Objects are either *primary* (read from the
  events) or *derived* (built from the selected objects of a channel, in order, each
  followed by its own object cuts). Lepton jets are one derived object, added by a
  component.
- **Names are checked when the processor is built.** A channel, cut or histogram that
  does not exist stops at construction with the full list of problems, and
  `python -m PKG.tools.check` reports the same without reading a file.
- **Strict by default, with absence declared.** An object listed in `optional_objs`
  may be missing from a sample (generator-level objects on data); what needs it is
  skipped there with a warning, and a skipped cut keeps its cutflow row, marked as not
  applied. Any other object that cannot be built, and any cut that fails, stops the
  run and is named. `strict=False` gives the lenient behaviour. Histograms and
  counters that cannot be filled are skipped with a warning in both modes.
- **Mask shapes are checked.** An event cut must return one value per event and an
  object cut one per object; the other way round is refused instead of being applied.
  An object cut that has no answer for whole events (it compared with something those
  events lack) leaves them with no objects, not with a missing list.
- **Histogram axes are paired event by event.** The weight and all axes are broadcast
  to one structure before filling, so a per-event axis can sit next to a per-object
  one, and an entry missing on one axis is dropped on all.
- **Only jagged collections are ordered by pT.** One-per-event records (`MET`, `PV`)
  are left alone.
- **Weights have their own module**, with the generator weight (the normalisation
  denominator) kept apart from event and object weights, and summed in double
  precision so that the normalisation does not depend on the chunking.
- **Outputs of separate runs can be merged** (`utilities.merge_outputs`): each run
  normalises its own slice, so the pieces are un-scaled, added and scaled once.
- **The schema reads one momentum representation** per vector collection, which coffea
  2026 requires, and collections it does not know are wrapped with `as_lorentz`.
- **An error of the analysis is not a bad input file.** With `skipbadfiles`, coffea
  drops a chunk whose failure looks like an I/O error (which errors count differs
  between coffea versions, see `skills/framework/references/pitfalls.md`). The engine
  tells the two apart by where an error was raised: in uproot, fsspec or XRootD
  underneath coffea's column loader, it is the input's and is passed on unchanged;
  anywhere else (a missing correction file, a golden JSON that cannot be read) it is
  the analysis's and is re-raised as a plain `RuntimeError` without the original as
  its cause, so the run stops instead of coming back short.
- **Sample metadata is never taken from an earlier run.** coffea caches, per python
  session, the metadata a file was first run with. Every Runner here is built with
  `metadata_cache={}`.
- **Index branches may be stored as floats.** coffea's `local2global` refuses them;
  `tools/schema.py` wraps it with a conversion (cms-sidm/SIDM replaces the function
  for the same reason).
- **The tree name is a property of the project** (`TREE_NAME`), not a literal
  repeated in every script and notebook.

## Verification status

**Nothing in this bundle has been run against coffea, awkward, uproot, hist, fastjet,
dask, orchestral or toolbase.** It was written in an environment where none of them
could be installed. What follows separates what was checked from what was not.

### Checked

| What | How |
|------|-----|
| Every python file here and every file a scaffold produces compiles | byte-compiled on Python 3.11, 3.12 and 3.13; generated files also parse as Python 3.9 syntax; `ruff` rules E, F and W with the line-length rule (E501) switched off |
| `toolkit.yaml` is well-formed and every module file it names exists | toolbase's `validate_toolkit`, called from the toolbase 0.16.0 source. It does not import the tool modules. |
| Scaffolding, components, block editing, undoing of failed writes, detection of names in use, validation of names and values, request handling of the three tools, the launcher's editing of harness configs | `tests/test_framework.py` (33 tests) and `tools/analysis/test_nanoaod_inspect.py` (15 tests, one of which needs uproot and was skipped), with a stand-in for `orchestral.tools.base` |
| Generated projects: no placeholder left, configs parse as yaml, every name used in a config is defined, notebooks are valid JSON whose code cells compile | same tests |
| The scaffold on the layout of a real LLP-NanoAOD file: 2009 branches, forming 56 collections (32 lists of objects, 24 one-per-event) and 23 single branches | scaffolded from the branch list of `CutDecayFalse_SIDM_BsTo2DpTo2Mu2e_MBs-500_MDp-0p25_ctau-0p4_v3_part-0.root`, extracted without uproot. Only the branch *names* were used. |
| The fixture writer's grouping of branches into collections | run on the branch lists of that file and of a 2018 data file (which has `nProton_multiRP`), and compared with the counter each branch names in its leaf title: no disagreement in 836 and 773 jagged branches |
| Engine control flow: channels, derived objects, optional and required objects, strict and lenient failures, empty chunks, accumulation, normalisation arithmetic, merging of separate runs, error classification by origin, the metadata cache, run periods, pairing of histogram axes, config mistakes, sample groups, the command-line scripts, the sidecar, the chain report's comparison, the index wrapper surviving a module reload | driven end to end against small stand-ins for awkward, hist and coffea. This shows the python logic is coherent. It says nothing about how the real libraries behave: the stand-in for awkward is a few hundred lines written from a reading of awkward's rules, with missing values one level deep only. |
| An agent extending a scaffolded framework with nothing but the system prompt, the two skills and the check | twenty simulated sessions in four rounds, each a fresh agent given one request in a user's words ("i want a new histogram to plot electron PT", a cut for one channel, a jet veto, a tighter muon ID with the change in yields, a changed threshold, a control region, generator-level and generator-matched muons, a new sample, a new data period with its golden JSON, a flat scale factor, jets cleaned from muons, ...). All ended with the check passing and with edits confined to `definitions/` and `configs/`, or with no edit where what was asked for existed. What they stumbled over after each round was fed back into the skill, the system prompt and the check's answer. The check ran against the same stand-ins and made-up events, so this tests the instructions and the workflow, not coffea, and not one of the run or plot commands the agents handed over was executed. |
| API usage | read against the sources of coffea 2025.5.0rc2, 2025.7.0, 2025.7.3, 2025.9.0, 2025.10.2, 2025.11.0, 2025.12.0, 2026.4.0, 2026.5.0, 2026.7.0 and 2026.9.0, awkward 2.8.7 and 2.14.0, uproot 5.7.5, distributed 2025.3.0 and 2026.8.0, fastjet (scikit-hep), hist, boost-histogram, mplhep 1.2.0, vector, fsspec, fsspec-xrootd, lpcjobqueue and toolbase 0.16.0 |

Three reviews of the code against those sources found defects that no test here could
have shown, and they were fixed without being run: the metadata cache, the
classification of errors under `skipbadfiles`, float index branches, `follow` on
collections that were cut or re-ordered, the fixture writer on data, and the pairing of
histogram axes among them. Treat every one of those fixes as unverified until the
tests below have passed.

### Never executed

- **All of `tests/test_framework_run.py`** (22 tests). In particular:
  - any real `Runner` call, with any executor, and `metadata_cache={}` on it;
  - `utilities.as_lorentz`, `dR`, `matched`, `lxy` on real arrays;
  - `AnalysisSchema` hiding `GenPart_px/py/pz` on a real file, and its wrapper
    around coffea's `local2global` (float and integer index branches);
  - `histogram.Histogram.fill`: `ak.broadcast_arrays` over the weight and the axes,
    on real jagged and option-type arrays;
  - `selection.JaggedSelection` when a cut returns None for whole events;
  - `Cutflow` and the output dictionary going through coffea's accumulation;
  - `LumiMask` on data, and a golden JSON that cannot be read;
  - `utilities.is_io_error` on an error that really came from uproot or XRootD (the
    test builds one with the right call stack by hand), and how `skipbadfiles`
    treats the engine's errors in each coffea version;
  - the lepton-jet component: fastjet clustering, `_take`, `source_objects`, isolation;
  - the schema component: `follow` (from cut and re-ordered objects, by name, through
    nested items), `constant_fields`;
  - `tests/make_fixture.py` rewriting a file with uproot, and the chain report on it;
  - the scale-out helpers, locally (`build_upload_plugin`, `make_local_client`).
- `nanoaod_layout.read_tree_layout`: **no ROOT file has been opened** by
  `InspectFileTool` or by the scaffold. `tests/synthetic.py` has never written one.
  How the function tells an RNTuple from a TTree is taken from uproot's source.
- The three tools under real Orchestral. The field declarations (for instance
  `Optional[Union[str, int]]` for `year`, and `Optional[Union[str, List[str]]]` for the
  check's `sample`) have not been through its schema generation, and its own
  validation of argument types runs before the checks written here.
- `tools/plotting.py` and the generated notebooks.
- `pip install -e .` of a generated project, and `python -m PKG.scripts.*` from an
  installed one.
- `scripts/add_samples.py` on `root://` directories (it calls `xrdfs`).
- `scaleout.make_dask_client`, `check_voms_proxy` and `make_lpc_client`, which can
  only be tried with a scheduler or at the LPC. `find_lpc_image` is tested against a
  directory of made-up image names; whether the images carry the names it looks for
  (`coffea-dak-almalinux*`, formerly `coffea-dask-almalinux*`) on cvmfs was not checked.
- The GitHub workflow of the chain report.
- `tools/analysis/test_coffea_skill_docs.py` in this branch (5 tests). It was ported
  from the SIDMRepo branch; here its input file is written with `mktree` (see below).
- `tb validate`, `tb install` and serving the tools from a real toolbase install.
- `examples/framework/launch.py`, and the additions to
  `examples/shared/harness_launch.py` it uses: the `--call-timeout` wiring, the Codex
  timeouts and the warm import. Their file edits are unit-tested against the formats
  `tb connect` writes according to the toolbase source; the Codex keys
  `startup_timeout_sec` and `tool_timeout_sec` were not checked against a Codex
  installation.

Two things found while reading sources are worth knowing in any case. Since uproot
5.7, `file["Events"] = {...}` writes an **RNTuple**, not a TTree: everything here that
writes a test file therefore uses `file.mktree(name, types).extend(data)` (coffea 2026
requires uproot 5.7 or newer). And coffea keeps the metadata of a fileset in a cache
shared by every `Runner` of a python session, so a sample whose `skim_factor`, `year`
or `is_data` was edited is processed with the old values until python is restarted,
unless the Runner is given `metadata_cache={}`.

### How to verify

From the repository root, with the bundle installed
(`tb install . --bundle framework --bundle coffea`) and `PY` the toolkit's interpreter:

```bash
tb validate
PY=$(grep -h '^python_path:' ~/.toolbase/cache/heptapod/*/.install_meta.yaml | head -1 | awk '{print $2}')
$PY -c "import coffea, awkward, uproot, hist, fastjet, numba; print(coffea.__version__, awkward.__version__, uproot.__version__, numba.__version__)"

$PY tools/analysis/test_nanoaod_inspect.py --no-skips         # 15 tests
$PY tools/framework/tests/test_framework.py --no-skips        # 33 tests
$PY tools/framework/tests/test_framework_run.py               # 22 tests
```

In an environment of your own instead (an existing analysis environment with coffea),
the same three commands work with its `python` from the repository root. The first two
then skip the few tests that need `orchestral`, so leave `--no-skips` off for them.

Use the scripts with `--no-skips` rather than `test_runner.py` for this: a test whose
library is missing is skipped, the script still exits 0, and the runner then reports
PASS for a suite that tested nothing. `--no-skips` turns a skip into a failure.

`test_framework_run.py` skips its dask test unless `distributed` and `dask.dataframe`
are importable, which the bundle does not install
(`$PY -m pip install "coffea[dask]" "dask[dataframe]"`); once they are, run it with
`--no-skips` too. `tools/analysis/test_coffea_skill_docs.py` needs pytest
(`$PY -m pip install pytest`, then `$PY -m pytest tools/analysis/test_coffea_skill_docs.py -q -rs`).

`test_framework_run.py` writes its own input (`tests/synthetic.py`): a file in which
two muons and two electrons per event come from a 500 GeV resonance, so a pass means
the engine counted, normalised, merged and clustered what the file was built to
contain, not merely that it ran. The tests share one engine, so start with the first
failure; `$PY -m pytest tools/framework/tests/test_framework_run.py -x -q -rs` stops
there.

Then on a real file, in a scratch directory (run with `$PY` from the repository root):

```python
import json
from tools.framework.scaffold import ScaffoldAnalysisFrameworkTool
from tools.framework.components import AddFrameworkComponentTool
from tools.framework.check import CheckAnalysisFrameworkTool

base = "/path/to/scratch"          # the file must be inside it (a symlink is fine)
print(ScaffoldAnalysisFrameworkTool(base_directory=base, project_dir="llp", sample_file="data/part-0.root",
                                    objects={"dsaMuons": "DSAMuon"})._run())
print(AddFrameworkComponentTool(base_directory=base, project_dir="llp", component="lepton_jets",
                                options={"sources": ["muons", "dsaMuons", "electrons", "photons"]})._run())
report = json.loads(CheckAnalysisFrameworkTool(
    base_directory=base, project_dir="llp", sample="part-0", channels=["baseline_2ljs"],
    hist_collections=["lj_base"], timeout_s=600)._run())
print(json.dumps(report, indent=1))
```

The check reports the cuts each channel applies, cutflows, object counts, warnings
and which histograms stayed empty; it does not return histogram contents. For the file named above, the physics cross-check is the
invariant mass of the two leading lepton jets, which should peak near the 500 GeV of
the sample. To look at it, run the generated code with the same interpreter from
inside `llp/`:

```python
from coffea import processor
from llp import TREE_NAME
from llp.tools import utilities
from llp.tools.processor import AnalysisProcessor
from llp.tools.schema import AnalysisSchema

runner = processor.Runner(executor=processor.IterativeExecutor(), schema=AnalysisSchema,
                          chunksize=50_000, metadata_cache={})
out = runner(utilities.make_fileset(["part-0"]), treename=TREE_NAME,
             processor_instance=AnalysisProcessor(["all", "baseline_2ljs"], ["lj_base"]))
for message in sorted(out["part-0"]["warnings"]):
    print(message)
out["part-0"]["cutflow"]["baseline_2ljs"].print_table()
h = utilities.get_hist(out, "part-0", "lj_lj_invmass", "baseline_2ljs")
print(h.axes[0].centers[h.values().argmax()], h.values().sum())
```

The thresholds of `baseline_2ljs` are the generator's placeholders, and it has no
trigger requirement unless `triggers` were given to the scaffold: an empty histogram
there says something about those cuts before it says anything about the engine. The
`all` channel has no cuts.

### Where to look first

| Symptom | Likely place |
|---------|--------------|
| A tool fails to load, or rejects an argument that looks right | The tools were only run against a stand-in for Orchestral's `BaseTool`. Check the field declarations at the top of `scaffold.py`, `components.py` and `check.py`. |
| `InspectFile` or the scaffold cannot read a file | `nanoaod_layout.read_tree_layout`, the only place a file is opened. |
| `test_framework_run.py` fails before any analysis runs | `tests/synthetic.py` (`write_tree`): the file is written with `mktree`. `_project` asserts that `nMuon` is among the branches. |
| `LLVM IR parsing error: invalid cast opcode for cast from 'i64' to 'ptr'` in anything touching `GenPart.children` | numba 0.67.0 with llvmlite 0.49.0 miscompiles coffea's kernel (found in cms-sidm/SIDM, August 2026). `toolkit.yaml` bounds `numba<0.67` for this reason; check what is installed. |
| `ValueError: ... conflicting ... coordinate representations` | coffea 2026 validates vector fields on first use. `tools/schema.py` (`_build_collections`) and `utilities.as_lorentz` are where one representation is chosen. |
| `requires the 'charge' field`, `missing temporal coordinate` | A behaviour was given to a collection that lacks the field: `as_lorentz` picks `PtEtaPhiMCandidate` only when `charge` is present. |
| An object that exists is reported as "could not be built", or one that is absent is not recognised as absent | `utilities.is_missing_field` recognises absence by awkward's `AttributeError("no field named ...")` (attribute access) and `FieldNotFoundError` (item access); `processor.process` decides from it. |
| A histogram is skipped with `cannot broadcast`, or fills the wrong number of entries | `tools/histogram.py`, `fill`: the weight and every axis go through `ak.broadcast_arrays` together and are then flattened with `ak.flatten(axis=None)`, which drops missing values. |
| An object cut leaves `None` in a collection, or `ak.num` of it is `None` | `tools/selection.py`, `apply_obj_cuts`: the branch for masks that are None for whole events. |
| A bare `RuntimeError` with no message from inside coffea when a cross-reference (`...IdxG`) is first used | coffea's `local2global` got an index that did not come out as int64. `tools/schema.py`, `_accept_any_index_type`, is meant to prevent exactly that. |
| `follow` returns the wrong objects, or fails with `no field named '_apply_global_index'` | `tools/schema.py` (schema component), `follow`: it must take the target collection from the original events (`attrs["@original_array"]` or `attrs["@events_factory"].events()`), never from cut or re-zipped objects. |
| A sample's `skim_factor`, `year` or `is_data` seems not to take effect on a second run in one session | A Runner built without `metadata_cache={}`. |
| Accumulation errors (`Cannot add accumulators of incompatible type`) | `tools/processor.py`, `_output`: the output must hold only numbers, sets, `hist.Hist` and `Cutflow`. |
| Lepton jets: fastjet rejects the input, or constituents do not line up | `tools/lepton_jets.py`, `cluster` and `_take`. The input is a packed jagged array of plain `px, py, pz, E` records; `_take` is fastjet's own indexing pattern. |
| The output differs between coffea 2025 and 2026 | `postprocess` returns the accumulator *and* modifies it in place because 2025 ignores the return value and 2026 uses it. |
| A run with `skipbadfiles=True` comes back short, or stops where it should have skipped | `utilities.is_io_error` decides by the modules in the traceback (`coffea.nanoevents.mapping`, then uproot, fsspec or XRootD) what is passed on to coffea as it is; `utilities.cause` keeps an OSError out of the causes of everything else. |
| Yields off by the number of jobs | Outputs of separate runs were added by hand instead of with `utilities.merge_outputs`. |
| `CheckAnalysisFramework` times out | toolbase cuts a call at 60 s unless served with `--call-timeout`; the tool's own default is 50 s. See `examples/framework/README.md`. |

Two environments matter and differ: the toolkit's own (what `CheckAnalysisFramework`
uses unless `analysis_python` or `venv` says otherwise), and whatever environment an
analysis already lives in. The code is written for coffea 2025.7 and newer and for the
2025.5 release candidates; cms-sidm/SIDM pins `coffea==2025.5.0rc2`, which has no
field validation and no `full_like_items`, so the `schema` component's
`constant_fields` is ignored there with a warning. The bundle's own requirement is
`coffea>=2025.7.0`, which today resolves to a 2026 release: the two differ in what
they validate, in how `skipbadfiles` decides, and in their dependencies (2026 no
longer brings dask with it). Do not run `pip install -r requirements.txt` of a
generated project inside a pinned environment such as that one: it would upgrade
coffea. `pip install -e .` alone is enough there.
