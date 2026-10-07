# How to change the analysis

Every recipe ends the same way: run `CheckAnalysisFramework` (static, then with a
sample) and read the result. `PKG` stands for the package directory.

The numbers in the examples are illustrations, not recommendations: thresholds,
working points and mass windows are the analysis's to choose. Names written in
CAPITALS or described as "yours" are things you define; they do not exist yet.

## Add an object cut

In `PKG/definitions/cuts.py`, under the object's entry of `obj_cut_defs`
(`pt_above` is one of the small factories at the top of that file):

```python
from PKG.tools.utilities import dR      # dR to the nearest object of another collection

obj_cut_defs = {
    "muons": {
        "pT > 25 GeV": pt_above(25),
        "isolated": lambda objs, obj: obj.pfRelIso04_all < 0.15,
        # a cut that compares collections uses objs
        "away from jets": lambda objs, obj: dR(obj, objs["jets"]) > 0.4,
    },
}
```

`obj` is the collection being cut, already slimmed by the cuts listed before this
one. Return a mask with one entry per object. An entry that is missing (`None`)
counts as failing. A cut that compares with something an event may not have, such as
`obj.delta_r(ak.firsts(objs["jets"])) > 0.4`, has no answer at all for events without
it: all of their objects fail, and the event is left with an empty collection. If
they should pass instead, say so in the cut (`ak.fill_none(..., True)` on the
per-object result, after giving the missing partner a value that makes it so).

`objs` holds the *other* objects as they are at that moment. Object cuts are applied
object by object, in the order the objects are defined in `objects.py`:

- a primary object defined above this one has its cuts applied already;
- one defined below has not: in the example, if `jets` comes after `muons`, the muon
  cut compares with **all** jets, not the selected ones;
- derived objects do not exist yet. A cut that needs one belongs to a derived
  object's own cuts, or is an event cut.

If the order matters, reorder the entries of `primary_objs`, or move the comparison
into a derived object (see `clean_jets` below), which always sees fully cut inputs.

Then name the cut in a selection, under that object, in `PKG/configs/selections.yaml`:

```yaml
signal_region:
  obj_cuts:
    <<: *object_cuts          # the shared baseline cuts
    muons:                    # replaces the baseline list for muons
      - *muons_base           # ... so paste it back in
      - "isolated"
```

A `<<:` merge replaces a key wholesale; to *extend* an object's list, repeat it with
its anchor as the first item, as above. Nested lists are flattened when read.

## Add an event cut

```python
evt_cut_defs = {
    "opposite charge": lambda objs: objs["muons"][:, 0].charge != objs["muons"][:, 1].charge,
    "Z window": lambda objs: abs(objs["muons"][:, :2].sum().mass - 91.2) < 15,
}
```

One True/False per event. Index a fixed position (`[:, 0]`) only in a selection that
has already required enough objects; cuts run in the order the selection lists them.

```yaml
dimuon:
  obj_cuts:
    <<: *object_cuts
  evt_cuts:
    - *event_cuts
    - ">=2 muons"
    - "opposite charge"       # safe: after ">=2 muons"
    - "Z window"
```

## Add a channel

A channel is a top-level entry of `selections.yaml`, with `obj_cuts` (a mapping, per
object) and `evt_cuts` (a list, in order); both may be empty. Build it from the
shared blocks (`<<: *object_cuts`, `- *event_cuts`) plus what makes it different:

```yaml
dimuon_tight:
  obj_cuts:
    <<: *object_cuts
    muons: [*muons_base, "isolated"]
  evt_cuts:
    - *event_cuts
    - ">=2 muons"
```

Entries whose name starts with `_` are building blocks: they hold anchors and cannot
be run. An anchor must be defined above the place it is used.

Control regions are channels too: copy the signal region and invert one cut. Define
the inverted cut by name in `cuts.py` (`"fails iso"`), rather than negating in yaml.

## Add a histogram

In `PKG/definitions/hists.py`. The common case, one attribute of one object:

```python
hist_defs = {
    "muon_dxy": obj_attr("muons", "dxy", nbins=100, xmin=-0.5, xmax=0.5, label=r"Muon $d_{xy}$ (cm)"),
    "muon_abseta": obj_attr("muons", "eta", absval=True, nbins=25, xmin=0, xmax=2.5),
}
```

Anything else is a `Histogram` of `Axis` objects, each a `hist` axis and a function
`(objs, mask) -> values` (`h`, `hist` and `ak` are imported at the top of the file):

```python
"dimuon_mass": h.Histogram(
    [
        h.Axis(hist.axis.Regular(60, 60, 120, name="dimuon_mass", label="m(mu mu) (GeV)"),
               lambda objs, mask: objs["muons"][mask, :2].sum().mass),
    ],
    evt_mask=lambda objs: ak.num(objs["muons"], axis=1) > 1,
),
```

`evt_mask` restricts the events used; it is handed to each fill function as `mask`,
which must apply it before indexing (as `objs["muons"][mask, :2]` does). Without an
`evt_mask`, `mask` selects everything and can be ignored.

A fill function returns one entry per event that passes the `evt_mask`: a value, or a
list of values (one per object). Missing values (`None`) are left out. The event
weight is repeated for each value of a list.

With two axes, the entries are paired event by event:

- a value per event on one axis and a list on the other: the value goes with every
  entry of the list (each muon with its event's MET);
- two lists: they must have the same length in every event (two fields of the same
  objects). Lists of different objects cannot be paired; build the pairs first
  (`ak.cartesian`, `ak.combinations`) and histogram their fields;
- an entry missing on one axis is left out on both.

Axis names must be unique in a histogram and must not be `weight`, `sample`, `threads`
or `channel`.

Then list it in a collection in `PKG/configs/hist_collections.yaml`. To make an
existing collection pick it up, add the name to that collection's list.

A histogram that cannot be filled never stops a run: it is skipped with a warning
("could not be filled"), and if it stayed empty in every channel that was run it is
listed in `empty_hists`. Look at both after adding one.

## Add an object

Read from the events, in `PKG/definitions/objects.py`:

```python
primary_objs["taus"] = lambda evts: evts.Tau
primary_objs["bjets"] = lambda evts: evts.Jet[evts.Jet.btagDeepFlavB > B_TAG_CUT]   # B_TAG_CUT: yours
```

Built from the selected objects of a channel (after their object cuts):

```python
from PKG.tools.utilities import matched, unmatched

derived_objs["dimuons"] = lambda objs: ak.combinations(objs["muons"], 2, fields=["a", "b"])
derived_objs["clean_jets"] = lambda objs: unmatched(objs["jets"], objs["muons"], 0.4)
derived_objs["leading_muon_jets"] = lambda objs: matched(objs["clean_jets"], objs["muons"][:, :1], 1.0)
```

Derived objects are built in the order they appear in the file, so one may use those
above it. Where a component has added a block to the file (`# >>> component:
lepton_jets >>>`), a derived object that uses the component's object (`ljs`) goes
*below* that block; anything else can go above it. Both kinds of object may then be
given cuts in `obj_cut_defs` and used in selections.

**An object every sample must have, or not.** If the collection is missing from a
sample, a strict run stops and names the object. For objects that legitimately exist
only in some samples (generator-level objects, a collection of one production), add
the name to `optional_objs` in the same file: whatever uses it is then skipped in
samples that lack it, with a warning. The same goes for a derived object that reads
a branch only some samples have.

**Objects are ordered and cut independently.** Every jagged collection with a `pt` is
sorted by descending pT, primary and derived alike, and each takes its own cuts. Two
objects therefore do not stay aligned element by element. To keep something *with*
each object, make it a field of that object:

```python
# the track each muon points to stays with its muon through sorting and cuts
primary_objs["muons"] = lambda evts: ak.with_field(evts.Muon, MUON_TRACKS(evts), "track")   # MUON_TRACKS: yours
```

A collection coffea does not know (anything a private NanoAOD production added) has
its branches but no `delta_r`, `px`, `mass` or `+`. Wrap it:

```python
from PKG.tools.utilities import as_lorentz
primary_objs["dsaMuons"] = lambda evts: as_lorentz(evts.DSAMuon, mass=0.105658)   # no mass branch
primary_objs["tracks"] = lambda evts: as_lorentz(evts.IsoTrack2)                  # has its own mass
```

The scaffold does this for the objects it is given when it can see the file. On a
wrapped collection a position stored as `x`, `y`, `z` is available as `vx`, `vy`,
`vz`, because on a Lorentz vector `x`, `y` and `z` are momentum components.

## Add a sample

By hand, in `PKG/configs/samples/samples.yaml` (the comment at its top shows the
format), or from a directory listing:

```bash
# one sample per sub-directory that holds ROOT files
python -m PKG.scripts.add_samples -o samples.yaml -t v2 -d /path/to/dir --year 2018
# the directory itself as one sample
python -m PKG.scripts.add_samples -o samples.yaml -t v2 -d root://host//store/... --name MySample --year 2018
# data
python -m PKG.scripts.add_samples -o samples.yaml -t data18 -d /path/to/data --year 2018 --data
```

`-t` is the name of the group the samples are added as (`--update` replaces a group
that exists). The script **rewrites the config file as plain yaml**: comments in it
are lost. Use `-o other.yaml` to leave `samples.yaml` untouched; such a file is then
read with `location_cfg="other.yaml"` (python) or `--location-cfg other.yaml`
(scripts, and `sample_config` of `CheckAnalysisFramework`).

Then:

- simulation: put its cross section (pb) in `PKG/configs/cross_sections.yaml` under
  the same name. Without it, or without a `lumi` for its `year` in
  `PKG/configs/run_periods.yaml` (a value of 0 counts as "without"), the sample runs
  but is not scaled, and says so.
- data: `is_data: true` (`--data`), a `year` that exists in
  `PKG/configs/run_periods.yaml`, and that period's `golden_json` file in `PKG/data/`.
- skims: `skim_factor`, the fraction of the original events the skim kept. The sum of
  generator weights is divided by it before normalising.

`utilities.make_fileset(["A", "B"], max_files=2)` builds the fileset, looking in every
group of the config. A sample name used in two groups must be asked for with
`tag="v2"`. `replace_prefix={"root://xcache//": "root://cmseos.fnal.gov//"}` switches
storage door without editing the config.

## Add a trigger requirement

```python
evt_cut_defs["pass triggers"] = lambda objs: (
    objs["hlt"].IsoMu24
    | objs["hlt"].Mu50
)
```

Take the path names from `InspectFile` (`branch_pattern="HLT_*"`); they change between
years. A path that is missing from a sample makes the cut fail there, and a strict
run stops: that is deliberate, because a trigger silently dropped changes the
selection. Write one cut per period and use each in the channel it belongs to.

## Add a weight or scale factor

`PKG/definitions/weights.py` has three functions. `pileup_weight` and `muon_sf` below
stand for functions you write (with correctionlib, say); they are not provided.

```python
def generator_weight(evts):            # simulation only; its sum is the lumi*xs denominator
    return evts.genWeight

def event_weight(evts, is_data):       # what every histogram and cutflow is filled with
    if is_data:
        return np.ones(len(evts))
    return generator_weight(evts) * pileup_weight(evts.Pileup.nTrueInt)

def object_weight(objs, is_data):      # per channel, from the SELECTED objects; None for no change
    if is_data:
        return None
    return ak.prod(muon_sf(objs["muons"]), axis=1)
```

Never put a correction into `generator_weight`: it would be normalised away. If
`generator_weight` or `event_weight` fails, the run stops whatever the strict
setting. For correctionlib inputs (flatten, evaluate, unflatten) see the `coffea`
skill.

## Data versus simulation in one selection

Use the same channel for both. A cut, histogram or derived object that uses an object
listed in `optional_objs` (the generator-level objects, by default) is skipped on a
sample that lacks it, with a warning; a skipped cut's cutflow row is marked "not
applied".

That covers whole objects, not single branches. A cut on a simulation-only *branch*
of an object that data has too (`objs["muons"].genPartIdx`) is not skipped on data:
it fails, and a strict run stops. Give such a thing an object of its own and make
that optional:

```python
derived_objs["matched_muons"] = lambda objs: objs["muons"][objs["muons"].genPartIdx >= 0]
optional_objs.append("matched_muons")
```

If a cut must differ between data and simulation, define two cuts and two channels;
do not branch inside a lambda on something the cut cannot see.

## Generator-level objects

With a `GenPart` object the scaffold adds `pid`, `to_pid` and `from_pid` helpers to
`objects.py`:

```python
derived_objs["gen_muons"] = lambda objs: pid(objs["gens"], 13)
derived_objs["gen_zd"] = lambda objs: pid(objs["gens"], 32)
derived_objs["gen_zd_to_mu"] = lambda objs: to_pid(objs["gen_zd"], 13)
```

`to_pid` keeps particles that decay only to that species; `from_pid` looks at the
direct mother, which in a generator record is often a copy of the particle itself.
coffea's `part.distinctParent` skips the copies, but never returns on a record in
which a particle is listed as its own mother, which some skims contain: see
"Reading the file" in `pitfalls.md` before using it on files you have not checked.

`utilities.lxy(particles)` is the transverse decay length, from where a particle was
produced to where its daughters were. `matched(objs["muons"], objs["gen_muons"], 0.1)`
keeps reco objects with a generator partner within ΔR.

## Run it

```python
from coffea import processor
from PKG import TREE_NAME                       # the tree the events are in
from PKG.tools import utilities
from PKG.tools.processor import AnalysisProcessor
from PKG.tools.schema import AnalysisSchema

fileset = utilities.make_fileset(["Signal", "Data"], max_files=1)
runner = processor.Runner(executor=processor.IterativeExecutor(), schema=AnalysisSchema,
                          chunksize=100_000, metadata_cache={})
out = runner(fileset, processor_instance=AnalysisProcessor(["dimuon"], ["base"]), treename=TREE_NAME)

out["Signal"]["cutflow"]["dimuon"].print_table()
print("\n".join(utilities.cutflow_table(out, "dimuon")))            # samples side by side
h = utilities.get_hist(out, "Signal", "dimuon_mass", "dimuon")      # a plain hist.Hist
for message in sorted(out["Signal"]["warnings"]):                   # what was skipped, and why
    print(message)
```

or `python -m PKG.scripts.run_analysis --samples Signal Data --channels dimuon --hists base -o out.coffea`,
which also writes `out.meta.yaml` recording what produced the file.
`processor.FuturesExecutor(workers=8)` uses local processes; for more, add the
`scaleout` component.

Keep `metadata_cache={}` on every Runner. coffea otherwise remembers, for as long as
python runs, the `is_data`, `year` and `skim_factor` each file was first processed
with, and a sample whose settings were edited in between is run with the old ones.

`skipbadfiles=True` on the Runner carries on past input files that cannot be read.
What was left out is not recorded: compare `out[sample]["metadata"]["n_evts"]` with
what the sample holds. Mistakes in the analysis's own files (a correction file or
golden JSON that is missing or broken) still stop the run.

## Split a sample over several runs

Batch jobs that each take some of a sample's files produce one output each. Do not add
those outputs by hand: every run normalised its own slice to lumi * xs. Merge them:

```python
out = utilities.merge_outputs(["job_0.coffea", "job_1.coffea", "job_2.coffea"])
```

or `python -m PKG.scripts.merge_outputs job_*.coffea -o merged.coffea`. All pieces
must come from the same channels, histogram collections and `unweighted_hist` setting.
Outputs of *different* samples can be merged the same way.
