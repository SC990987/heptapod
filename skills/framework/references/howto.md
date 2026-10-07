# How to change the analysis

Sections, in this order: Add an object cut · Add an event cut · Add a channel · Add a
histogram · Add a counter · Add an object · Add a sample · Add a trigger requirement ·
Add a weight or scale factor · Data versus simulation in one selection ·
Generator-level objects · Run it · Plot it · Look inside a sample · Split a sample
over several runs. The lines up to the first section apply to every one of them.

Every recipe ends the same way: run `CheckAnalysisFramework` on a simulated sample
and on a data sample (one call takes both) and read the result. `PKG` stands for the
package directory.

The numbers in the examples are illustrations, not recommendations: thresholds,
working points, mass windows and histogram ranges are the analysis's to choose. Names
written in CAPITALS or described as "yours" are things you define; they do not exist
yet.

Two habits apply to every recipe:

- **Search first.** The scaffold wrote a starting menu (see the top of `cuts.py` and
  `hists.py`): what is asked for may exist under the name you were about to use. A
  name is defined once; the static check reports a second definition as an error.
- **Know the numbers from before.** The check has no memory. Run it before the edit,
  so that afterwards you can say what moved: cutflow rows, `counters`, and which
  histograms are filled. A new channel or sample has no "before": compare it with
  the channel it was built from, in the same run, and use the earlier answer to
  show that the others did not move.

Names follow what is there: an object cut reads like its condition (`"pT > 25 GeV"`,
`"|eta| < 2.4"`), a multiplicity cut `">=2 muons"`, a veto `"no jet above 50 GeV"`, a
channel `<what it builds on>_<what it adds>` (`baseline_2muons`), a histogram
`<object in the singular>_<what>` (`muon_pt`, `gen_muon_pt`), a quantity of a pair
as the scaffold does (`muon_muon_invmass`), a second histogram of the same quantity
by what differs (`muon_pt_wide`). Keep sample, channel and
collection names to letters, digits and `_ . + -`, never starting with `-`: they are
passed on command lines.

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

Then name the cut in a selection, under that object, in `PKG/configs/selections.yaml`.
There are two places it can go, and they mean different things:

```yaml
# 1. In the shared block: every channel that merges it gets the cut
_object_cuts: &object_cuts
  muons: &muons_base
    - "pT > 10 GeV"
    - "|eta| < 2.4"
    - "isolated"              # baseline, baseline_2muons, ... all change

# 2. In one channel: only that channel changes
signal_region:
  obj_cuts:
    <<: *object_cuts          # the shared cuts of every object
    muons:                    # replaces the shared list for muons
      - *muons_base           # ... so paste it back in
      - "isolated"
  evt_cuts:
    - *event_cuts
```

A `<<:` merge replaces a key wholesale; to *extend* an object's list in one channel,
repeat it with its anchor as the first item, as above. Nested lists are flattened
when read. A cut that tightens a shared one (25 GeV where the shared list has 10) is
added this way too: both are applied, the tighter one decides, and the channel keeps
following the shared block. Only a cut that must *loosen* or drop a shared one needs
the whole list written out without the anchor, `muons: ["pT > 5 GeV", "|eta| < 2.4"]`.
An object cut listed twice is applied once.

When the user names no channel ("tighten the muon selection", "apply it in the
baseline"), the shared block is meant, since `baseline` is nothing but the shared
blocks: put the cut there and say which channels changed with it.

**Changing a threshold** ("make the jet pT cut 40 GeV"). A cut is named for its
condition, so a new threshold is a new entry of the menu (`"pT > 40 GeV":
pt_above(40)`), and the selection names it *in place of* the old one. Never change
what an existing name does: `"pT > 30 GeV": pt_above(40)` passes every check and
lies. The old entry stays in the menu, unused. "Everywhere" means every channel that
applies the cut; `all` applies none by design (it is the reference for
efficiencies) and stays that way unless the user says otherwise. The same number
may also sit inside other definitions (a veto, an `evt_mask`, a derived object),
where no check can see it: search `definitions/` for it.

Afterwards, `static.selections` in the check's answer shows the list each channel
ends up with. An object cut has no cutflow row. Its
effect is in `counters` (`"Selected muons"`, per channel), in `muon_n`, and in the
rows of the event cuts that count the object: `">=2 muons"` passes fewer events once
muons are tighter. Where that happens every counter of the channel drops, the
electrons' too, because fewer events are left: the effect of the cut on its own
object is read in a channel whose event cuts do not count it (`baseline`).

## Add an event cut

```python
evt_cut_defs = {
    "opposite charge": lambda objs: objs["muons"][:, 0].charge != objs["muons"][:, 1].charge,
    "Z window": lambda objs: abs(objs["muons"][:, :2].sum().mass - 91.2) < 15,
}
```

One True/False per event. Index a fixed position (`[:, 0]`) only in a selection that
has already required enough objects; cuts run in the order the selection lists them.
For "at least N of an object" there is a factory at the top of `cuts.py`:
`">=2 taus": at_least("taus", 2)` (the scaffold wrote `>=1` and `>=2` for its
objects).

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

An event cut sees the channel's *selected* objects. A veto on jets counts the jets
that passed the channel's jet cuts: one outside them cannot veto. To veto on other
jets, define them as an object of their own ("Add an object") and count that.

An event the cut has no answer for (`None`) fails it. `ak.max`, `ak.min` and
`ak.firsts` give `None` for an event whose list is empty, so a veto written as
`ak.max(objs["jets"].pt, axis=1) < 30` also rejects every event with no jet at all.
Count instead, or say what an empty event should do:

```python
"no jet above 30 GeV": lambda objs: ak.num(objs["jets"][objs["jets"].pt > 30], axis=1) == 0,
"leading jet below 30 GeV": lambda objs: ak.fill_none(ak.max(objs["jets"].pt, axis=1) < 30, True),
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
be run. An anchor must be defined above the place it is used. A channel may carry a
`description:` (free text; it is copied into the `.meta.yaml` of saved outputs). A
channel written without `evt_cuts` has no event cuts at all, not the shared ones.

When the user names one cut and asks for "a channel that uses it", build the channel
on the shared blocks plus that cut, as `baseline_2muons` is built, and say that you
did: those blocks are still the scaffold's placeholders unless they were changed.

Control regions are channels too: copy the region they go with and change what makes
them different (invert a cut, raise a threshold, add a requirement). Define an
inverted cut under a name of its own in `cuts.py` (`"pfRelIso04_all > 0.15"`, or
`"fails iso"`), rather than negating in yaml. Two things to look at and to say:

- whether the parent region applies the cut being inverted (`static.selections`). If
  it does not, the new region is a subset of the parent, not orthogonal to it. Do
  not add the cut to the parent unasked;
- an inverted *object* cut keeps the objects that fail and ignores the others: an
  event with two non-isolated muons and a third, isolated one is in the region. If
  it should be vetoed, that is an event cut.

Histograms are not attached to a channel. "Fill the muon histograms in the new
region" is a choice made when running: name the channel and the collections
(`channels=["cr"]`, `hist_collections=["muon_base", "jet_base"]`); leaving either out
in the check means all of them.

## Add a histogram

Look in `hist_defs` first. For each list of objects the scaffold wrote a
multiplicity, a pT and an eta-phi histogram, named in the singular (`muon_n`,
`muon_pt`, `muon_eta_phi`) and listed in a collection of the object (`muon_base`)
that `base` includes. One-per-event records got what they have (`met_pt`, `met_phi`,
`pv_n`, `pv_z`), and the first object an invariant mass of its two leading entries
(`muon_muon_invmass`). If the histogram asked for is one of those, it exists and is
filled: say so. If the user said how theirs differs, see "Look before you add" in
the skill: a second one under its own name for "add", the existing one edited for
"change".

In `PKG/definitions/hists.py`. The common case, one attribute of one object:

```python
hist_defs = {
    "muon_dxy": obj_attr("muons", "dxy", nbins=100, xmin=-0.5, xmax=0.5, label=r"Muon $d_{xy}$ (cm)"),
    "muon_abseta": obj_attr("muons", "eta", absval=True, nbins=25, xmin=0, xmax=2.5),
}
```

`obj_attr(object, attribute)` fills one entry per object; the attribute `"n"` counts
the objects per event instead. Binning that is not given comes from
`default_binnings` in the same file (by attribute name; 100 bins from 0 to 100 for an
attribute it does not list), and those defaults are the scaffold's, not the
analysis's: a range the user gave goes in `xmin`/`xmax`, and a bin count they did not
give stays the default, which you mention. The axis is named `<object>_<attribute>`.
`obj_eta_phi(object)` is the two-axis eta-phi histogram. A new object gets its axis
label from an entry in `obj_labels` (`obj_labels["taus"] = "Tau"`). When the user
gave no binning at all, a histogram that will be compared with another takes that
one's: `clean_jet_pt` is binned like `jet_pt`, whatever the default for pT is.

Anything else is a `Histogram` of `Axis` objects, each a `hist` axis and a function
`(objs, mask) -> values` (`h`, `hist` and `ak` are imported at the top of the file).
A hand-built axis takes the default binning the same way `obj_attr` does, when the
user gave none: `hist.axis.Regular(*default_binnings["pt"], name=..., label=...)`.

A histogram of *the leading* object: objects are already ordered by pT, so the
leading one is the first. Slice it out rather than indexing it:

```python
"leading_muon_pt": h.Histogram([
    h.Axis(hist.axis.Regular(*default_binnings["pt"], name="leading_muon_pt",
                             label=r"Leading muon $p_T$ (GeV)"),
           lambda objs, mask: objs["muons"][:, :1].pt),      # a list of one, or of none
]),
```

A quantity that needs a fixed number of objects, such as the mass of the two leading
muons, restricts the events with an `evt_mask` (the scaffold wrote one such
histogram, `muon_muon_invmass`; the window below is an illustration):

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
`evt_mask`, `mask` selects everything and can be ignored. Indexing a position
(`objs["muons"][mask, 0]`) needs the mask even when the channel you have in mind
requires the object: collections are filled in every channel that is run, `all`
included, and there some events have no muon.

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

Then list it in a collection in `PKG/configs/hist_collections.yaml`. A histogram of
an object goes into that object's collection (`muon_base`), so that `base` picks it
up; a new object gets a collection of its own, included in `base` the same way. A
histogram that no collection lists is never filled, and nothing fails: the check's
`static.unused_hists` is where it shows.

A histogram that cannot be filled never stops a run: it is skipped with a warning
("could not be filled"), and if it stayed empty in every channel that was run it is
listed in `empty_hists`. One that is filled in some channels and empty in others is
under `empty_in_channels`, with the channels. Look at all three after adding one. The
check does not return the axis or the contents of a histogram, and entries in the
overflow count as filled: a range is verified by reading the definition, or by
running and plotting (below).

## Add a counter

A counter is one plain (unweighted) number per channel and sample, taken over the
events that pass the channel's event cuts:

```python
counter_defs["Events with a b jet"] = lambda objs: ak.sum(ak.num(objs["bjets"], axis=1) > 0)
counter_defs["Selected clean_jets"] = lambda objs: ak.sum(ak.num(objs["clean_jets"], axis=1))
```

Counters are not chosen by a config: every counter is filled in every channel. The
scaffold's `"Selected muons"` and its siblings are how an object cut shows its
effect, and a new list of objects is given one for the same reason. One that
depends on generator-level objects gets a counter only when the count is what was
asked for: on data it adds a "not filled" warning per channel. Because they
count within the surviving events, a tighter *event* cut lowers every counter of the
channel: read a change in one against the cutflow.

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

`unmatched(a, b, r)` keeps the `a` with no `b` within ΔR < `r` (an event with no `b`
keeps all of its `a`); `matched(a, b, r)` keeps those that have one. Both compare
with the `b` the channel selected, placeholder cuts included: say which.

**A new object or a tighter old one?** Something that should exist *beside* the
original (cleaned jets next to all jets, b jets next to jets) is an object of its
own. A tighter definition of the object itself, for the channels that ask for it, is
an object cut: no new event cut or histogram is needed then, because `">=2 jets"`,
`jet_n` and `jet_pt` follow the cut.

**What makes a new object visible.** By itself it only appears in the check's list
of objects. Give a list of objects the same things the scaffold gave its own: a
counter (`"Selected clean_jets"`, see "Add a counter"), which is where the check
shows its size per channel; an axis label in `obj_labels`; and the histograms that
were asked for, in a collection of its own. The object with these is one change:
check after the lot. If the object comes out empty everywhere, see "A derived object
is empty" in `pitfalls.md` before reporting a number.

**An object every sample must have, or not.** If the collection is missing from a
sample, a strict run stops and names the object. For objects that legitimately exist
only in some samples (generator-level objects, a collection of one production), add
the name to `optional_objs` in the same file: whatever uses it is then skipped in
samples that lack it, with a warning.

A derived object needs an entry of its own in only one case: when its own definition
reads a branch some samples lack (`objs["muons"].genPartIdx`). A derived object
*built from* an optional object (`gen_muons` from `gens`) needs none: where its input
is absent it is absent too, and what uses it is skipped the same way. Listing it
anyway would do harm: a misspelt branch in its definition would then pass as "not
available" instead of stopping the run.

**Objects are ordered and cut independently.** Every jagged collection with a `pt` is
sorted by descending pT, primary and derived alike, and each takes its own cuts. Two
objects therefore do not stay aligned element by element. To keep something *with*
each object, make it a field of that object, by changing the line that defines the
object (a second `primary_objs["muons"] = ...` further down is a name defined twice):

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
format): that is the way for a list of named files. The paths are wherever the
analysis reads the files from (an absolute path, a `root://` URL); the rule that a
tool's arguments stay inside the working directory is not about them. Or from a
directory listing:

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
  A new period is a new entry of that file, `"2017": {lumi: ..., golden_json: ...}`
  (the comment at its top shows the layout). Its `lumi` scales the *simulation* of
  that period: with data alone nothing uses it yet, which is worth saying.
- skims: `skim_factor`, the fraction of the original events the skim kept. The sum of
  generator weights is divided by it before normalising.

To see that a sample is in: `static.samples` in the check's answer lists it with what
the configs say (data or not, run period, number of files), `static.run_periods` the
luminosity and golden JSON of each period, and a run with `sample=` the `is_data`,
`year` and `lumixs_weight` it was processed with. For data, `n_removed_golden_json`
is the number of events outside the golden JSON: they are removed before anything is
counted, so the first cutflow row is `n_events` minus that. The check
opens a sample's *first* file only. The others are not opened before a full run, so
a mistyped path further down the list passes every check: say that they were not
looked at. A sample from another production can name collections or trigger paths
differently; the strict run on it names what is missing.

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
selection. Write one cut per period and use each in a channel of its own: channels
are not tied to samples, so it is the run that pairs a period's channel with that
period's samples. A sample of a new period goes through the trigger cut that is
there; say so when you add one.

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

`muon_sf` returns one factor per muon, nested like the collection. A flat stand-in
until the measured ones exist is `ak.full_like(muons.pt, 0.95)` (0.95: the user's).
`ak.prod(..., axis=1)` is 1 for an event without muons.

Never put a correction into `generator_weight`: it would be normalised away. If
`generator_weight` or `event_weight` fails, the run stops whatever the strict
setting. For correctionlib inputs (flatten, evaluate, unflatten) see the `coffea`
skill.

Weights are not chosen by a config and not per channel. `object_weight` is called in
every channel, with that channel's selected objects, and is not told which channel
it is in: in `all`, which has no object cuts, every object counts. Say that when a
per-object factor is asked for.

Afterwards, in the check on simulation: the weighted column of the cutflow moves in
every row. That includes the first row, "None": the object weight is applied before
the event cuts, so that row is no longer lumi × cross section and differs between
channels. Histograms are filled with the same weight. What must *not* move: the
event counts, the `counters`, `scaled_sum_weights` and `lumixs_weight` (the
normalisation), and everything on data. The check shows that yields moved, not the
size of the factor per object: that is read from the definition.

## Data versus simulation in one selection

Use the same channel for both. A cut, histogram or derived object that uses an object
listed in `optional_objs` (the generator-level objects, by default), or a derived
object built from one, is skipped on a sample that lacks it, with a warning; a
skipped event cut's cutflow row is marked "not applied". Tell the user what that
means for the channel: on data it then selects what it would without that cut.

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

`pid` takes every entry of that species. A generator record usually holds several
copies of one particle (before and after radiation), so counting the result counts
copies: for "the muons" in the sense of one entry each, require the last copy as
well, with the cuts the scaffold wrote for `gens` when the file has the branches
(`"isLastCopy"`, `"status 1"`) or directly, `gens[gens.hasFlags("isLastCopy")]`. Say
which definition you used; it changes a multiplicity cut.

`to_pid` keeps particles that decay only to that species; `from_pid` looks at the
direct mother, which in a generator record is often a copy of the particle itself.
coffea's `part.distinctParent` skips the copies, but never returns on a record in
which a particle is listed as its own mother, which some skims contain: see
"Reading the file" in `pitfalls.md` before using it on files you have not checked.

`utilities.lxy(particles)` is the transverse decay length, from where a particle was
produced to where its daughters were. `matched(objs["muons"], objs["gen_muons"], 0.1)`
keeps reco objects with a generator partner within ΔR.

## Run it

The check is a look at one chunk of one file. A run over the samples is the script,
from the project directory, with the interpreter the check reports as `python`
(`Signal`, `Data`, `baseline_2muons` and `base` are the scaffold's names):

```bash
python -m PKG.scripts.run_analysis --samples Signal Data --channels baseline_2muons \
    --hists base --max-files 1 -o output/baseline_2muons.coffea
```

It prints each cutflow and every warning, and writes the output with a `.meta.yaml`
beside it recording what produced it. Two differences from the check: without
`--hists` no histogram is filled (the check fills all of them by default), and
without `--max-files` every file of every sample is read.

When the user asks for a run ("run the baseline on ttbar"), make it. Unless they say
how much, start with `--max-files 1`, and tell them what was read (files and events)
and what was not. Outputs and figures made along the way go where the user says, and
otherwise into `output/` in the project directory, which git ignores. A comparison of
yields *before and after* an edit on more than the check's one chunk needs the
"before" run made before the edit.

The same from python (a notebook, a study):

```python
from coffea import processor
from PKG import TREE_NAME                       # the tree the events are in
from PKG.tools import utilities
from PKG.tools.processor import AnalysisProcessor
from PKG.tools.schema import AnalysisSchema

fileset = utilities.make_fileset(["Signal", "Data"], max_files=1)
runner = processor.Runner(executor=processor.IterativeExecutor(), schema=AnalysisSchema,
                          chunksize=100_000, metadata_cache={})
out = runner(fileset, processor_instance=AnalysisProcessor(["baseline_2muons"], ["base"]),
             treename=TREE_NAME)

out["Signal"]["cutflow"]["baseline_2muons"].print_table()
print("\n".join(utilities.cutflow_table(out, "baseline_2muons")))                    # samples side by side
h = utilities.get_hist(out, "Signal", "muon_muon_invmass", "baseline_2muons")      # a plain hist.Hist
for message in sorted(out["Signal"]["warnings"]):                                  # what was skipped, and why
    print(message)
```

`processor.FuturesExecutor(workers=8)` (`--executor futures --workers 8`) uses local
processes; for more, add the `scaleout` component.

Keep `metadata_cache={}` on every Runner. coffea otherwise remembers, for as long as
python runs, the `is_data`, `year` and `skim_factor` each file was first processed
with, and a sample whose settings were edited in between is run with the old ones.

`skipbadfiles=True` on the Runner carries on past input files that cannot be read.
What was left out is not recorded: compare `out[sample]["metadata"]["n_evts"]` with
what the sample holds. Mistakes in the analysis's own files (a correction file or
golden JSON that is missing or broken) still stop the run.

## Plot it

The check says whether a histogram was filled, not what is in it. To look at one, or
when the user asks for a plot, run the analysis as above and plot the output:

```bash
python -m PKG.scripts.run_analysis --samples Signal Data --channels baseline \
    --hists electron_base --max-files 1 -o output/baseline.coffea
```

```python
import matplotlib.pyplot as plt
from PKG.tools import plotting, utilities

out = utilities.load_output("output/baseline.coffea")
plotting.set_plot_style()
plotting.plot_samples(out, "electron_pt", "baseline")          # one line per sample
plt.savefig("output/electron_pt_baseline.png")

# one sample, two channels: the effect of what the second channel adds
plt.figure()
for channel in ("baseline", "baseline_2muons"):
    plotting.plot(utilities.get_hist(out, "Signal", "muon_pt", channel), label=channel, skip_label=True)
plotting.add_label()
plt.legend()

# the same as a ratio, numerator first. The error bars assume the numerator's events
# are a subset of the denominator's; pass efficiency=False when they are not
plotting.plot_ratio(utilities.get_hist(out, "Signal", "muon_pt", "baseline_2muons"),
                    utilities.get_hist(out, "Signal", "muon_pt", "baseline"),
                    labels=["baseline", "baseline_2muons"])    # labels: denominator first
```

`plot` adds the overflow to the last bin by default (`flow="sum"`); pass `flow="none"`
to draw the axis range alone. Simulation is scaled to lumi * cross section in the
output and data is not: the two compare in normalisation only when both numbers are
configured *and* the data that was run over is the data of that luminosity. On part
of the files, compare shapes (`plot_samples(..., density=True)`) and say so.
Plotting needs matplotlib and mplhep, which the processor does not: if the
environment lacks them, say so and give the user these lines instead of a picture.
A plot of a histogram that exists needs no new definition: do not ask what should
differ, plot it.

## Look inside a sample

When a number from the check needs explaining (an object that comes out empty, a cut
that removes everything) and nothing in its answer does, look at the events
themselves, from a script or a prompt of your own outside the package, with the
interpreter the check reports:

```python
import awkward as ak
from coffea.nanoevents import NanoEventsFactory
from PKG import TREE_NAME
from PKG.definitions.objects import primary_objs
from PKG.tools import utilities
from PKG.tools.schema import AnalysisSchema

path = utilities.make_fileset(["Signal"], max_files=1)["Signal"]["files"][0]
events = NanoEventsFactory.from_root({path: TREE_NAME}, schemaclass=AnalysisSchema,
                                     entry_stop=2000).events()
muons, gens = primary_objs["muons"](events), primary_objs["gens"](events)
gen_muons = gens[abs(gens.pdgId) == 13]
print("gen muons:", ak.sum(ak.num(gen_muons, axis=1)))
print("smallest dR(muon, gen muon):", ak.min(utilities.dR(muons, gen_muons), axis=None))
```

These are the objects as read, before any cut and before the processor orders them
by pT. Nothing of this goes into the package.

## Split a sample over several runs

Batch jobs that each take some of a sample's files produce one output each. Do not add
those outputs by hand: every run normalised its own slice to lumi * xs. Merge them:

```python
out = utilities.merge_outputs(["job_0.coffea", "job_1.coffea", "job_2.coffea"])
```

or `python -m PKG.scripts.merge_outputs job_*.coffea -o merged.coffea`. All pieces
must come from the same channels, histogram collections and `unweighted_hist` setting.
Outputs of *different* samples can be merged the same way.
