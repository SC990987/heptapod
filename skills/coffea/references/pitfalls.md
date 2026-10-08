# Pitfalls

Each of these was hit and confirmed against coffea 2026.7.0 / awkward 2.13, apart
from the last section, which was read off coffea's source and says so.

## Object mask vs. event mask

```python
events.Muon[events.Muon.pt > 25]   # filters muons INSIDE each event
events[ak.num(good_muons) >= 2]    # drops whole events
```

`events[jagged_mask]` is not meaningful. Reduce to per-event first with
`ak.any`, `ak.all`, or `ak.num(...) >= n`.

**`ak.any` is the default meaning of a bare object cut.** "Muon pt > 25" as an
event cut means *at least one* muon passes — not two, and not all of them.
Decide explicitly which you meant.

## `nevonecut` is not a sequential efficiency

coffea's `CutflowResult` gives `nevonecut` (each cut alone) and `nevcutflow`
(cuts applied in order). Both put the pre-selection count at index 0, aligned
with `labels`. Quoting `nevonecut` as a cutflow overstates every step after the
first.

## Option types return `None`, and `to_numpy` masks them

`nearest`, `matched_jet`, `matched_gen`, `ak.firsts`, `ak.pad_none` are all
option-typed. Unmatched entries are `None`.

```python
ak.to_numpy(option_typed)             # -> numpy.ma.MaskedArray
np.save("x.npy", masked)              # NotImplementedError
np.quantile(masked, 0.5)              # silently IGNORES the mask
```

Drop the option before converting: `ak.to_numpy(ak.drop_none(x))`, or
`ak.fill_none(x, sentinel)` when you want to keep the length.

## MET has no eta

`events.MET` is a `MissingETArray` — 2-D polar. `delta_phi` works;
`delta_r` and `muon + MET` both raise `TypeError`. MET is per-event, so
broadcast with `events.MET[:, None]` to combine with a jagged collection.

## Schema behaviours require fields the skim may have dropped

`NanoAODSchema` gives `Muon` the Candidate behaviour, which **requires
`charge`**; the Lorentz-vector behaviours require `pt/eta/phi/mass`. A skim
missing one fails on *first access*, not at open time, and raises `ValueError`
("MuonArray requires the 'charge' field") rather than a `KeyError`.

Read such a file with `BaseSchema`, or subclass the schema and drop the
offending mixin. With `BaseSchema` you get raw branches and no `delta_r`.

## A cross-reference exists only if its index branch does

Cross-references are index branches, not magic. `Muon.matched_jet` needs
`Muon_jetIdx`; `Jet.matched_muons` needs `Jet_muonIdx1` **and**
`Jet_muonIdx2`. Custom, slimmed and privately-produced NanoAOD routinely omit
them, and the attribute then raises `no field named 'muonIdxG'`.

Worse than missing: `Jet.matched_muons` is capped at **two** muons per jet by
construction, padded with `None`. It is not "all muons in this jet".

```python
"muonIdxG" in events.Jet.fields          # can I use matched_muons at all?
events.Jet.nearest(events.Muon, threshold=0.4)   # works on any file
```

Geometric matching with `nearest` has no such dependency and no 2-object cap.

## A cyclic GenPart tree hangs

`distinctParent` / `distinctChildrenDeep` walk the mother chain until the pdgId
changes. Real NanoAOD always has `genPartIdxMother < ` the particle's own
index, so the walk terminates. A hand-built or corrupted file with cyclic
indices makes these loop forever — the symptom is a hang, not an error.

This is not only a hand-built-file problem: the cms-sidm/SIDM analysis reports
skimmed ntuples with a particle listed as its own mother, and replaces coffea's
kernel with a copy that gives up after a fixed number of steps
(`sidm/tools/gen.py` there). `parent` and `children` do not walk a chain and
are not affected.

## `nearest` is asymmetric

`a.nearest(b)` gives, for **each object in `a`**, the closest object in `b`.
Swapping the operands is a different question. For overlap removal you want the
objects with *no* partner:

```python
_, dr = jets.nearest(muons, return_metric=True)
clean = jets[ak.fill_none(dr > 0.4, True)]     # note: fill None with True
```

`ak.fill_none(dr < 0.4, False)` for "matched", `dr > 0.4` filled with `True`
for "keep" — an unmatched jet has `dr = None` and must be kept.

## `partial_weight` needs `storeIndividual=True`

`Weights(n).partial_weight(include=[...])` raises `ValueError` unless the
`Weights` object was constructed with `storeIndividual=True`.

## Weights are per event, fills may be per object

Filling a per-object histogram with a per-event weight array raises a length
mismatch. Broadcast first:

```python
w_obj = np.repeat(w.weight(), ak.to_numpy(ak.num(events.Muon)))
```

## `rapidity` is not `eta`

Both exist on the same object. Jet algorithms (anti-kT, FastJet) use rapidity;
detector acceptance cuts use pseudorapidity. `delta_r` uses `eta`;
`deltaRapidityPhi` uses rapidity.

## Delta phi wrapping convention

coffea/`vector` wrap Delta phi to `[-pi, pi)`, so exactly `pi` comes back as
`-pi`. Immaterial for `abs()` or `**2`, which is how it is almost always used,
but do not compare a raw signed Delta phi to `+pi`.

## A custom collection gets no behaviour at all

The schema only attaches behaviours to collections it knows. A custom one --
`DSAMuon`, `PatMuonVertex`, anything a private NanoAOD producer added -- comes
back as a plain record: its branches are there, but `delta_r`, `px`, `mass`
and the rest are **not**.

```python
hasattr(events.Muon, "delta_r")      # True
hasattr(events.DSAMuon, "delta_r")   # False -- custom collection
```

Re-zip it with the Lorentz-vector behaviour to get them:

```python
from coffea.nanoevents.methods import vector as cvector

def as_vectors(coll, keep=()):
    fields = {"pt": coll.pt, "eta": coll.eta, "phi": coll.phi,
              "mass": coll.mass if "mass" in coll.fields else ak.zeros_like(coll.pt)}
    for f in keep:
        if f in coll.fields:
            fields[f] = coll[f]
    return ak.zip(fields, with_name="PtEtaPhiMLorentzVector",
                  behavior=cvector.behavior)

dsa = as_vectors(events.DSAMuon, keep=("charge", "dxy"))
dsa.nearest(events.Muon, return_metric=True)      # now works
```

## The same particle can be in two collections

Collections are not exclusive. In LLP NanoAOD most muons are reconstructed twice, as a
PF muon (`Muon`) and as a displaced standalone muon (`DSAMuon`): in a cms-sidm signal
file, 82% of the PF muons with pT > 10 GeV and |eta| < 2.4 had a DSA muon with the same
cuts within Delta R < 0.05 (median 0.011). Anything that combines the two collections
(objects clustered from both, a mass or a count over both) takes such a muon twice:
lepton jets clustered from PF and DSA muons, electrons and photons put the mass of the
two leading lepton jets at 705 GeV for a 500 GeV resonance, against 505 GeV without
the DSA muons. Take out of one collection what the other already holds, first:

```python
# dsa: the DSA muons as vectors (above); sel_mu: the PF muons the analysis keeps
_, dr = dsa.nearest(sel_mu, return_metric=True)
dsa_only = dsa[ak.fill_none(dr > 0.05, True)]      # no kept PF muon within 0.05
```

The radius is a choice, not a convention: too large a one also takes out a different
muon next to a PF muon (in a collimated pair, its partner). Where the production
stores the muon-system segments the two share (LLP NanoAOD: `DSAMuon_muonMatch1..5`,
the number shared with the PF muon at `DSAMuon_muonMatch1..5idx`), those say directly
which DSA muon is a PF muon again; cms-sidm/SIDM cleans with them. Electrons and
photons overlap the same way: an electron's deposit can also be stored as a photon
(`Photon_electronIdx`, `Photon_pixelSeed`).

## Cartesian and polar in the same collection breaks the vector behaviour

If a collection carries **both** `px,py,pz` and `pt,eta,phi`, coffea refuses it:

```
ValueError: GenParticleArray: conflicting azimuthal coordinate representations
present: cartesian=['px','py'], polar=['phi','pt']
```

LLPnanoAOD's `GenPart` does exactly this. The failure appears on **first
access** (`events.GenPart.pdgId`), not at open time.

The cleanest fix is to not read the duplicate: a schema that hides
`GenPart_px/py/pz` leaves a normal `GenPart` with all of its navigation intact.
The `framework` skill's `AnalysisSchema` does this for every collection that has a
vector behaviour and stores both sets; it has done so on an LLPnanoAOD file with
coffea 2026.9.0. The other route, also confirmed, is to drop that collection's mixin
and re-zip with polar coordinates only:

```python
class LLPNanoAODSchema(NanoAODSchema):
    warn_missing_crossrefs = False
    error_missing_event_ids = False

mixins = dict(LLPNanoAODSchema.mixins)
mixins.pop("GenPart", None)
LLPNanoAODSchema.mixins = mixins
# then: gen = as_vectors(events.GenPart, keep=("pdgId", "status", "vx", "vy"))
```

Dropping the mixin costs you `parent`, `children` and `distinctParent`, which
are built by the schema's global-index machinery. Navigate with the raw
`genPartIdxMother` index if you need them.

## GenPart contains pt = 0 entries

The incoming partons have `pt = 0`, which makes `eta` infinite and any
`delta_r` or `.mass` involving them `nan` or nonsense. Filter before doing
vector arithmetic on gen particles -- by `status`, by `hasFlags("isLastCopy")`,
or simply `gen = gen[gen.pt > 0]`.

## Decay length: which vertex is which

For a long-lived particle, `GenPart_vx/vy/vz` on the particle itself is where it
was **produced**, not where it decayed. Its decay vertex is the production
vertex of its **daughters**.

```python
# WRONG -- the production vertex's distance from the origin
lxy = np.hypot(dp.vx, dp.vy)                       # ~0.04 cm

# RIGHT -- production vertex to decay vertex
lep    = raw[is_lepton & (raw.genPartIdxMother >= 0)]
mother = raw[lep.genPartIdxMother]
lxy    = np.hypot(lep.vx - mother.vx, lep.vy - mother.vy)   # ~34 cm
```

On a real long-lived-particle sample those differed by a factor of ~800, and
**both look plausible** -- nothing errors, nothing warns. Sanity-check against
the physics: `<Lxy> ~ gamma * c*tau` with `gamma = E/m`. A 0.25 GeV particle at
250 GeV has `gamma ~ 1000`, so `c*tau = 0.4 mm` implies tens of cm, not microns.

## Two LLPnanoAOD warnings that are noise

```
RuntimeWarning: Branch Photon_mass already exists but its values will be replaced with 0.0
RuntimeWarning: Branch Photon_charge already exists but its values will be replaced with 0.0
```

Harmless. The file stores those branches, coffea's Photon behaviour defines
them as identically zero, and it overwrites. The stored values are ~1e-5 --
numerically zero already -- so nothing is lost, and photons really are massless
and neutral. Do not spend time on these.

## Reading one file is fast -- if it seems to hang, it already finished

Opening a 25 MB LLPnanoAOD file with `NanoEventsFactory` and flattening a
GenPart column takes **under a second**:

```
from_root + events() : 0.7 s
len(events)          : 0.0 s   (4733 events)
flatten GenPart.pdgId: 0.1 s   (116499 entries)
```

coffea reads lazily, so having 2000 branches in the file costs nothing until
you touch them. If such a command appears to run for minutes, the work is not
the problem -- check whether the process is still alive
(`ps -p <pid>`) before concluding it is slow. An empty `ps` means it exited and
something upstream is still waiting on it.

**Run short reads in the foreground.** Backgrounding sub-second work and then
polling for it costs far more than it saves, and a completed process that the
poller misses looks exactly like a hang.

## A CMSSW environment on LD_LIBRARY_PATH breaks fastjet

If `cmsenv` has been sourced in the shell (or `LD_LIBRARY_PATH` otherwise
carries `/cvmfs/.../CMSSW_*/external/*/lib`), importing the pip `fastjet`
wheel fails:

```
ImportError: .../fastjet/_swig/_fastjet_swig...so:
  undefined symbol: _ZNK7fastjet9PseudoJet21_ensure_valid_rap_phiEv
```

CMSSW ships its own `libfastjet*.so`, which shadows the one the wheel bundles.
Reproduced exactly: a clean environment imports fine,
`LD_LIBRARY_PATH=<CMSSW>` gives the error above.

Check, and use a shell where `cmsenv` was never run:

```bash
echo $LD_LIBRARY_PATH | tr ':' '\n' | grep -E 'cvmfs|CMSSW'   # should print nothing
```

## Python loops over events

If you find yourself writing `for event in events:` — stop. Columnar operations
on the whole array are typically 100–1000x faster, and every operation in these
notes is already vectorised.

## From coffea's source, not from running it

The entries below were read off the source of coffea 2025.5 to 2026.9. Nobody
has triggered them here: treat them as where to look, and confirm before
relying on one.

**A cross-reference also needs the counter of its target.** The schema builds
`Muon.matched_jet` only when the file has both `Muon_jetIdx` and `nJet`. With
`warn_missing_crossrefs = False` nothing is said at open time; the attribute
is just not there.

**An index branch stored as floats.** Some private productions write index
branches as floating-point numbers (`-1.0, 0.0, 1.0`). coffea turns the index
into a global one in `coffea.nanoevents.transforms.local2global`, which raises
a bare `RuntimeError`, with no message, unless the result is 64-bit integers.
The error appears at first use of the reference, not at open time. The
`framework` skill's `AnalysisSchema` converts such an index before coffea sees
it.

**A bare branch named like a collection.** A branch `Foo` next to `Foo_pt`,
`Foo_eta` makes `events.Foo` that single branch, and the `Foo_*` branches
cannot be reached through the schema. Hide the bare branch in a schema
subclass, or read with `BaseSchema`.

**The `Runner` remembers file metadata for the whole session.** With the
processor `Runner`, what coffea learnt about a file when it was first
processed is cached for the rest of the python session, the fileset's
`metadata` for that file included. Run the same file again with different
metadata (an `is_data` flag, a year, a normalisation factor) and the processor
still sees the first values. Up to coffea 2025.7 the cache is keyed by file
and tree name alone, so the same file under two dataset names also shares one
entry. Pass a fresh cache:

```python
runner = processor.Runner(executor=..., schema=NanoAODSchema, metadata_cache={})
```

**What `skipbadfiles=True` skips depends on the version.** The 2025.5 release
candidates skip a file or tree that is not there, and errors whose text says
"Invalid redirect URL", "Operation expired" or "Socket timeout". From 2025.7
any `OSError` raised while a chunk is processed is skipped — including one from
your own code, such as a correction file that cannot be opened, which then
silently costs you the chunk. From 2025.11 a failing chunk is also tried
again three times when skipping. Skipped chunks are announced in a Python
warning and nowhere in the output: compare the number of events processed
with what the sample should have.
