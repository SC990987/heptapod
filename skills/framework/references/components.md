# Optional components

Add one with `AddFrameworkComponent(project_dir, component, options)` **when the
analysis needs it**, not by default: a lean framework is easier to hand over.

A component copies files and, where needed, appends marked blocks to the definitions
and configs:

```python
# >>> component: lepton_jets >>>
...
# <<< component: lepton_jets <<<
```

Edit inside a block freely. Leave the two marker lines alone: they are how the block
is found again, and a block whose markers are damaged is refused rather than guessed
at. What is installed, and with which options, is recorded in
`.analysis_framework.json` at the project root. The record says what the tool wrote;
edits made by hand inside a block are not tracked.

A call either completes or changes nothing: if it is refused, or a write fails half
way, every file is put back as it was.

Calling the tool again:

- same options, or none: nothing is changed (missing files and blocks are restored);
- `lepton_jets` with different options: **refused** unless `overwrite=true`, because
  its blocks would be kept and the new options silently ignored. It is refused in
  the same way when its blocks are in the files but the record of their options is
  gone (the project file was deleted);
- `overwrite=true`: files and blocks are regenerated from the options (those given
  now on top of those recorded), discarding edits made inside them;
- `schema`: options add up over calls, and `tools/schema.py` itself is the record
  (see below).

Read the `notes` of the answer: they say when something was kept instead of written.
After adding any component, run `CheckAnalysisFramework`.

## lepton_jets

Lepton jets are anti-kT clusters of nearby leptons and photons: the signature of light,
boosted particles decaying to collimated leptons.

Options:

| Option | Default | Meaning |
|--------|---------|---------|
| `name` | `"ljs"` | object name of the lepton jets |
| `sources` | the analysis's muons, electrons, photons | objects to cluster: a list of names, or `{name: {"mass": GeV}}` to force a mass |
| `radius` | `0.4` | anti-kT radius parameter |
| `carry` | `{"charge": 0}` | constituent fields to keep, with the value used for sources that lack the field (a list of names means "fill with 0") |
| `isolation_jets` | `"jets"` if that is a *primary* object of the analysis and not a source, else none | jet object for the isolation; `null` to skip it |
| `invmass_max` | `1000` | upper edge (GeV) of the pair-mass histogram |

`name` must not be an object the analysis already has, and the names the component
defines (the channel `baseline_2<name>`, the collection and histograms starting with
the singular of the name, the event cuts `>=1 <name>` ...) must be free: otherwise
the call is refused and says which ones clash. A name counts as taken when the
analyst's own code or config defines it, outside the component's block.

A source or isolation jet may be a derived object, if it is defined **above** the
component's block in `objects.py` (the block is appended at the end, so it normally
is); the answer's notes say so when it applies.

What it adds:

- `tools/lepton_jets.py` with `build_lepton_jets`, `source_objects`, `leading_pair_dphi`;
- in `objects.py`, `derived_objs["ljs"]` and the constants that configure it
  (`LJS_SOURCES`, `LJS_RADIUS`, `LJS_CARRY`, `LJS_ISOLATION_JETS`);
- object cuts for `ljs` (`"pT > 10 GeV"`, `"pT > 30 GeV"`, `"|eta| < 2.4"`,
  `">=2 constituents"`, and `"isolation < 0.2"` when there is an isolation) and event
  cuts `">=1 ljs"`, `">=2 ljs"`, `"leading ljs |dphi| > 2"`;
- histograms `lj_n`, `lj_pt`, `lj_eta_phi`, `lj_n_constituents`, `lj_dRSpread`,
  `lj_isolation` (with an isolation), `lj_lj_invmass`, and the collection `lj_base`;
- the channel `baseline_2ljs`: the baseline, lepton jets with `pT > 30 GeV` and
  `|eta| < 2.4`, at least two of them;
- `fastjet` and `vector` in `requirements.txt` (install them in the analysis environment).

**Every threshold in that list is a placeholder**, like the scaffold's: 10, 30, 2.4,
0.2 and 2 are there so that something runs. The analysis chooses its own.

How it behaves:

- Lepton jets are a **derived object**, so in each channel they are built from the
  sources *after that channel's object cuts*. Tightening a source's cuts in a
  selection changes its lepton jets. Cuts on `ljs` themselves go under `obj_cuts: ljs:`.
- Each lepton jet has `pt`, `eta`, `phi`, `mass` and the usual vector methods, plus
  `constituents`, `n_constituents`, `dRSpread`, and per source `ljs.<source>` (the
  constituents from that source) and `ljs.<source>_n`.
- Constituents are light copies: `pt, eta, phi, mass`, the carried fields, `src`
  (which source) and `idx` (position in that source). For any other branch of the
  original objects use `source_objects(objs, objs["ljs"], "muons").dxy`, which has the
  layout events → lepton jets → constituents.
- With `isolation_jets`, each lepton jet is matched to the nearest jet within 0.4 and
  gets `isolation = (E_jet / E_lj) * (1 - lepton_fraction)`, with `lepton_fraction`
  the sum of the jet's `chEmEF + neEmEF + muEF`: the non-leptonic energy around the
  lepton jet. No matched jet means isolation 0. The jets used are **the channel's
  selected jets**, after its jet object cuts: a jet that the selection removes cannot
  be matched, and the lepton jet near it then counts as isolated. Keep that in mind
  when tightening jet cuts, or point `isolation_jets` at a looser jet object defined
  for the purpose. This definition suits CMS NanoAOD jets; change
  `lepton_fraction_fields`, or the function, for anything else.
- Every source needs `pt`, `eta`, `phi` and a mass. A custom collection without a mass
  branch gets one from `as_lorentz(..., mass=...)` in `objects.py` or from
  `{"mass": ...}` in the sources.
- If a source is missing from a sample, the lepton jets are unavailable there. If the
  isolation jets are missing from a sample, the lepton jets are built there without
  `isolation`, and a cut on it fails: do not point `isolation_jets` at an object
  listed in `optional_objs`.
- Lepton jets are ordered by pT, like every jagged object.
- The clustering is done in double precision, whatever the files store. The mass of
  a collimated pair is a small difference of large numbers: in single precision a
  0.25 GeV pair at a few hundred GeV comes out several per cent off. Expect small
  differences from a framework that clusters the stored single-precision values.

Typical next steps: categories as further derived objects
(`derived_objs["mu_ljs"] = lambda objs: objs["ljs"][objs["ljs"].muons_n > 0]`), matching
to generator particles with `matched(objs["ljs"], objs["gen_zd"], 0.4)`, and a
signal-region channel built on `baseline_2ljs`. Derived objects that use `ljs` must
be defined **below** the component's block in `objects.py`: objects are built top to
bottom.

A CMSSW environment on `LD_LIBRARY_PATH` breaks the pip `fastjet` wheel (see the
`coffea` skill's pitfalls): run the analysis in a shell where `cmsenv` was never sourced.

## scaleout

No options. Adds `tools/scaleout.py`, `condor/lpc_condor_config`, a `scale_out.ipynb`
test notebook, and `coffea[dask]` and `dask[dataframe]` in `requirements.txt`
(`distributed` and the dashboard come with the first; coffea's `DaskExecutor` imports
`dask.dataframe`, which needs the second).

The processor does not change with scale; the executor does:

```python
processor.IterativeExecutor()                 # one process: debugging
processor.FuturesExecutor(workers=8)          # local processes: needs nothing extra
processor.DaskExecutor(client=client)         # a dask cluster, from one of:

client = scaleout.make_local_client(n_workers=8)
client = scaleout.make_dask_client("tls://scheduler:8786")          # an existing scheduler
cluster, client = scaleout.make_lpc_client(max_workers=50)          # HTCondor at the LPC
```

Things to know:

- Remote workers must import the analysis package. `make_dask_client` and
  `make_lpc_client` ship the package directory (`tools/`, `definitions/`, `scripts/`,
  `configs/`, `data/`) to them, including uncommitted edits. Notebooks, `studies/`,
  `test_notebooks/` and saved `.coffea` outputs are not shipped (a `.coffea` file
  under `data/` is: that is where a lookup table in that format belongs).
  **Code that runs in a task must live in the package**, not beside a notebook.
- The package is shipped as it is when the client is made. After editing it while a
  cluster is up, ship it again and restart the workers, since python does not
  re-import what it has loaded: `scaleout.upload_package(client, restart=True)`.
- Workers and client need the same coffea version. `make_lpc_client` looks on cvmfs
  for a worker image with this environment's coffea and python
  (`coffea-dak-almalinux9:<coffea>-py<python>`; `coffea-dask-...` is the earlier name
  of the same images) and stops with the list of available ones if there is none;
  pass `image=` to choose.
- The LPC path needs a VOMS proxy (`check_voms_proxy` says how to renew it),
  `pip install "htcondor<25" git+https://github.com/CoffeaTeam/lpcjobqueue.git`, and
  the `condor/lpc_condor_config` file next to the package: work from the repository
  with the package installed editable (`pip install -e .`), or pass `condor_config=`.
- `skipbadfiles=True` on the Runner keeps a long run alive past unreadable input
  files. What it skips is not recorded in the output: compare
  `out[sample]["metadata"]["n_evts"]` with what the sample should have. Which read
  failures it skips depends on the coffea version (`pitfalls.md`, "Scaling out"). It
  never skips a failure of the analysis itself: a cut that cannot be evaluated, or a
  correction file or golden JSON that cannot be opened, stops a strict run.
- Give every Runner `metadata_cache={}`, as the notebook does.
- Save the result with `utilities.save_output` and `metadata.write_run_metadata`.
- Several submissions over parts of one sample are combined with
  `utilities.merge_outputs`, never by adding the outputs by hand.

`make_lpc_client` is specific to the Fermilab LPC. For another site, start a dask
cluster the way that site documents and hand its scheduler address to
`make_dask_client`.

## schema

Options (added to those already in effect):

| Option | Example | Effect |
|--------|---------|--------|
| `mixins` | `{"MyTrack": "PtEtaPhiMCollection"}` | give a collection a coffea behaviour |
| `cross_references` | `{"Muon_dsaIdx": "DSAMuon"}` | an index branch becomes a global index `Muon.dsaIdxG` |
| `nested_items` | `{"Muon_dsaMatchIdxG": ["Muon_dsaMatch1idx", "Muon_dsaMatch2idx"]}` | several index slots combined into one list per object |
| `hidden_branches` | `["Jet_brokenBranch"]` | never read these |
| `constant_fields` | `{"DSAMuon_mass": 0.105658}` | synthesise a constant branch (coffea 2025.12 or newer; lists of objects only) |
| `hide_duplicate_momenta` | `true` | read (pt, eta, phi) only when (px, py, pz) is stored too |
| `reset` | `true` | start from an empty schema instead of adding to what is in effect |

It rewrites `tools/schema.py` with these options as tables at the top of the file
(`EXTRA_MIXINS`, `CROSS_REFERENCES`, `NESTED_ITEMS`, `CONSTANT_FIELDS`,
`HIDDEN_BRANCHES`, `HIDE_DUPLICATE_MOMENTA`) and a `follow` helper.

**The file is its own record.** The tool reads the tables back and adds the options
of the call to them, so the tables can be edited by hand and the tool keeps up. To
take an entry out, delete it from its table, or call with `reset=true` and everything
that should remain. Edits anywhere else in the file make the tool leave the file
alone: the answer then says `"applied": false`, and the options of that call are
**not in effect**. `overwrite=true` regenerates the file from its tables and the
options given, discarding those other edits.

What the names must look like:

- a mixin is given to a collection, named by what stands before the first underscore
  of its branches: `"MyTrack"`, never `"My_Track"`;
- a cross-reference is the full name of the index branch, pointing into a collection
  that is a list of objects with a `n<Target>` counter. Its target needs a
  *collection* behaviour: `PtEtaPhiMCollection`, not `PtEtaPhiMCandidate` or
  `PtEtaPhiMLorentzVector`, which have the vector methods only;
- a nested item is named `<Collection>_<something>IdxG`, lists index branches that
  are all cross-references (given with or without the trailing `G`), and may not take
  the name a cross-reference already gets (`Muon_dsaIdxG` when `Muon_dsaIdx` is one);
- an index branch stored as floating-point numbers is fine: the schema reads it as
  integers.

`follow(collection, index_field, target)` returns, per object of `collection`, what
it points to in `target`: one object for a cross-reference (missing where the index
is negative), a list with one slot per index branch for a nested item (missing where
a slot is empty). `collection` may come straight from the events or from `objs`,
after any cuts and ordering; `target` is the collection's name in the file:

```python
from PKG.tools.schema import follow

# in a cut, a fill function or a derived object
follow(objs["muons"], "dsaIdxG", "DSAMuon").pt

# or once, as a field of the object
import awkward as ak
primary_objs["muons"] = lambda evts: ak.with_field(
    evts.Muon, follow(evts.Muon, "dsaIdxG", "DSAMuon"), "dsa")
# then:  objs["muons"].dsa.pt
```

What comes back are objects of the collection **as stored in the file**: the object
cuts a selection applies to an analysis object made from the same collection
(`objs["dsa_muons"]`) are not applied to them. Cut on their fields where it matters.
And do not use a second object in place of `follow`: every object is ordered by pT
and cut separately, so `objs["dsa_muons"]` does not line up with `objs["muons"]`
element by element.

When to use it rather than `as_lorentz`:

- you need **cross-references** between collections (that is a schema matter);
- you want a collection to have behaviour everywhere, including in interactive use;
- a branch must be hidden.

For "this custom collection needs `delta_r` and has no mass", `as_lorentz(...,
mass=...)` in `objects.py` is simpler and does not depend on the coffea version
(`constant_fields` needs coffea 2025.12 or newer and is ignored with a warning
before). Behaviours that validate their fields (coffea 2026 and newer) refuse a
Lorentz-vector collection without a mass, and a candidate collection without a
charge: give the missing field with `constant_fields`, or use `as_lorentz`.
`constant_fields` works for collections that are lists of objects, not for
one-per-event records.

A cross-reference or nested item whose index branch, or whose target's counter, is
not in a file is simply absent there, without a message at file open: `no field
'dsaIdxG'` at first use is how it shows.

## chain_report

No options. Adds `tests/chain_report.py`, `tests/make_fixture.py`, `tests/fixtures.yaml`,
`tests/README.md` and `.github/workflows/chain-report.yml`.

```bash
python tests/make_fixture.py /path/to/file.root --dataset NAME --events 200 --year 2018 --skim-factor 0.5
python tests/chain_report.py compute state.json
python tests/chain_report.py render base.json new.json
```

`compute` runs every channel and collection over the fixtures (in lenient mode, so
that every failure is seen) and records static errors, cutflows (raw and weighted),
empty histograms and all warnings. `render` compares two such states. The workflow
does this for each pull request against its base and writes the comparison to the job
summary.

`render` exits with status 1, and the check turns red, when the newer state has *new*
errors (a new crash, a new inconsistency, or a cut, object, weight, histogram or
counter that newly fails), **and also** when the chain could not be run on a fixture
at all, even if the base fails the same way. That includes having no fixture yet: the
check is red until the first fixture is registered. Changed cutflows, new "not
available in this sample" warnings and newly empty histograms are shown and do not
fail it.

`overwrite=true` regenerates the scripts and the workflow but never
`tests/fixtures.yaml`, which is the analyst's list.

`make_fixture.py` rewrites the slice with uproot as a tree named like the analysis's
`TREE_NAME`, regrouping the branches of each collection under its counter. Counters
whose name has an underscore (`nProton_multiRP` in CMS data) are handled. Branches
that cannot be read or regrouped are left out and listed when the script ends: check
that list for anything the analysis reads.

Choosing fixtures matters more than their size (a few hundred events):

- simulation: a slice in which the main selections keep a few events, and a
  `--skim-factor` other than 1, so a mistake in its handling shows;
- data: a slice crossing a golden-JSON boundary, so that one chunk comes back empty;
- the dataset name needs a cross section for the weighted columns to be scaled.

Say plainly what the report is: it shows that the chain executes and how its numbers
move. It does not show that the physics is right.
