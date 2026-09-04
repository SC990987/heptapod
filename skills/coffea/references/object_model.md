# Reaching the attributes of coffea objects

Everything below was checked against coffea 2026.7.0 / awkward 2.13 with
`NanoAODSchema`.

## What an events array is

```python
events = NanoEventsFactory.from_root({"f.root": "Events"},
                                     schemaclass=NanoAODSchema).events()
events.fields        # ['HLT','Jet','MET','Muon','GenPart','event','run', ...]
events.Muon.fields   # ['pt','eta','phi','mass','charge','jetIdx', ...]
```

`events.fields` are the collections and per-event branches; a collection's
`.fields` are its real branches. **Always check these before guessing a name.**

Collections are jagged — `events.Muon.pt` is one variable-length list per
event, `ndim == 2`. Per-event quantities like `events.MET.pt` have `ndim == 1`.

## Branches vs. behaviour-provided attributes

This is the distinction that matters. `NanoAODSchema` attaches a *behaviour*
class to each collection, and that class supplies attributes computed on
demand. For `Muon` the chain is:

```
MuonArray -> Muon -> PtEtaPhiMCandidate -> Candidate
          -> PtEtaPhiMLorentzVector -> LorentzVector
```

so a muon has far more attributes than the file has branches. It is not in
`.fields`, but it works:

**Computed kinematics** (from the Lorentz-vector behaviour, all verified):

| Attribute | Meaning |
|-----------|---------|
| `px py pz` | Cartesian momentum |
| `energy` / `E` / `t` | energy |
| `mass` / `M` / `m` | invariant mass (`mass2`, `M2` squared) |
| `pt` / `rho` | transverse momentum (`pt2`, `rho2`) |
| `p` / `mag` | momentum magnitude (`p2`, `mag2`) |
| `eta` / `pseudorapidity` | pseudorapidity |
| `rapidity` | true rapidity — **not** the same as `eta` |
| `theta`, `costheta`, `cottheta` | polar angle |
| `et`, `mt` | transverse energy / mass (`et2`, `mt2`) |
| `beta`, `gamma` | relativistic factors |
| `pvec` | the 3-vector |
| `unit()` | unit vector |

**Methods** (verified): `delta_r`, `delta_phi`, `delta_r2`, `deltaR`,
`deltaR2`, `deltaeta`, `deltaphi`, `deltaangle`, `deltaRapidityPhi`,
`deltaRapidityPhi2`, `nearest`, `metric_table`, `sum`, `dot`, `cross`,
`boost`, `boostCM_of`, `rotateX/Y/Z`, `to_Vector2D/3D/4D`, `is_parallel`,
`is_timelike`, `isclose`.

`a.delta_r(b)` and `a.delta_phi(b)` are coffea's own — in
`coffea/nanoevents/methods/vector.py` they are literally
`return self.deltaR(other)` and `return self.deltaphi(other)` on scikit-hep
`vector`. Never hand-roll them.

## Arithmetic

Four-vectors add, and the sum is a four-vector:

```python
pair = mu[:, 0] + mu[:, 1]
pair.mass, pair.pt, pair.eta          # all available
events.Muon.sum()                     # vector sum of all muons in each event
```

## All pairs / combinatorics

```python
pairs = ak.combinations(events.Muon, 2, fields=["a", "b"])   # unique pairs
dimuon = pairs.a + pairs.b
mass  = dimuon.mass
opp   = pairs.a.charge != pairs.b.charge                     # opposite sign
best  = dimuon[ak.argmax(dimuon.pt, axis=1, keepdims=True)]  # highest-pT pair
```

`ak.cartesian` instead of `ak.combinations` when the two objects come from
*different* collections.

## Cross-references (navigating between collections)

The schema turns index branches into object references. **Forward** references
work whenever the index branch is present:

```python
events.Muon.matched_jet        # the Jet each muon points at (Muon_jetIdx)
events.Muon.matched_gen        # the GenPart it points at   (Muon_genPartIdx)
events.Muon.matched_fsrPhoton
```

These are **option-typed**: an unmatched object gives `None`, so guard with
`ak.is_none` or `ak.fill_none` before using the result.

Every cross-reference is just an **index branch the file either has or does
not**. `NanoAODSchema.all_cross_references` lists them; the ones that exist in
your file become navigable, the rest simply are not there. The standard set:

| Attribute | Needs branch(es) |
|-----------|------------------|
| `Muon.matched_jet` | `Muon_jetIdx` |
| `Muon.matched_gen` | `Muon_genPartIdx` |
| `Muon.matched_fsrPhoton` | `Muon_fsrPhotonIdx` |
| `Electron.matched_jet` / `matched_gen` / `matched_photon` | `Electron_jetIdx` / `_genPartIdx` / `_photonIdx` |
| `Jet.matched_gen` | `Jet_genJetIdx` |
| `Jet.matched_muons` | **`Jet_muonIdx1` and `Jet_muonIdx2`** |
| `Jet.matched_electrons` | `Jet_electronIdx1`, `Jet_electronIdx2` |
| `FatJet.subjets` | `FatJet_subJetIdx1`, `FatJet_subJetIdx2` |
| `Jet.constituents` | the `JetPFCands` collection (PFNanoAOD only) |

`Jet.matched_muons` deserves a warning: it is **not** a general reverse lookup.
It reads the two fixed slots `Jet_muonIdx1`/`Jet_muonIdx2`, so it returns at
most two muons per jet, padded with `None`. A jet with three nearby muons
silently loses one. Those branches are part of the central NanoAOD content and
are frequently absent from custom, slimmed or privately-produced NanoAOD --
in which case the attribute raises `no field named 'muonIdxG'`.

Test before relying on any of them:

```python
"muonIdxG" in events.Jet.fields     # False -> Jet.matched_muons unavailable
"jetIdx"   in events.Muon.fields    # the forward reference
```

When the branch is missing -- or when you need *all* nearby muons rather than
two -- do the association geometrically instead, which works on any file:

```python
matched, dr = events.Jet.nearest(events.Muon, return_metric=True)
```

If a collection you need is missing from the file altogether, that is a
question for CMSSW, not coffea: NanoAOD content is decided when the file is
produced. See the `cmssw` skill for adding a collection to a custom NanoAOD.

## Gen-particle navigation

`GenPart` gets its own behaviour (all verified):

```python
events.GenPart.parent                # direct mother (None at the top)
events.GenPart.children              # direct daughters
events.GenPart.distinctParent        # first ancestor with a different pdgId
events.GenPart.distinctChildren
events.GenPart.distinctChildrenDeep
events.GenPart.hasFlags("isPrompt")  # statusFlags bit test
```

`hasFlags` takes the CMS `statusFlags` names — `isPrompt`,
`isLastCopy`, `isFirstCopy`, `fromHardProcess`, `isDirectHardProcessTauDecayProduct`
and so on — and accepts several at once (AND).

These require `GenPart_genPartIdxMother` to be a valid tree: every mother index
is strictly below the daughter's own index. A file with cyclic indices makes
`distinctParent` loop forever.

## MET is not a four-vector

`events.MET` is a `MissingETArray` — a two-dimensional polar vector with
`pt` and `phi` and **no `eta`**. Consequences (verified):

```python
lead.delta_phi(events.MET)     # works — only needs phi
lead.delta_r(events.MET)       # TypeError — there is no eta
lead + events.MET              # TypeError — cannot add a 4-vector to a 2-vector
```

MET is per-event (`ndim == 1`), so to combine it with a jagged collection
broadcast it: `events.Muon.delta_phi(events.MET[:, None])`.

Transverse mass has no built-in, and is the one place a formula is unavoidable:

```python
mt = np.sqrt(2 * lead.pt * events.MET.pt *
             (1 - np.cos(lead.delta_phi(events.MET))))
```

## Selecting and reshaping

```python
good   = events.Muon[events.Muon.pt > 25]        # filter objects
events = events[ak.num(good) >= 2]               # filter events
srt    = events.Muon[ak.argsort(events.Muon.pt, ascending=False)]
lead   = ak.firsts(srt)                          # leading muon, None if empty
padded = ak.pad_none(events.Muon, 2)             # guarantee 2 slots
ak.num(events.Muon)                              # per-event count
ak.sum(ak.num(events.Muon))                      # total objects
ak.any(events.Muon.pt > 25, axis=1)              # per-event: any muon passes
ak.all(events.Muon.pt > 25, axis=1)              # per-event: all muons pass
```

`ak.firsts` and `ak.pad_none` return option types — `None` where the event had
too few objects. Use `ak.fill_none(x, value)` before feeding them to numpy or a
histogram.

## Converting out

```python
ak.to_numpy(flat_array)             # 1-D arrays only
ak.to_numpy(ak.flatten(jagged))     # flatten first
ak.to_list(x)                       # plain Python, for printing
```

`ak.to_numpy` on an **option-typed** array returns a `numpy.ma.MaskedArray`.
`np.save` cannot write one and `np.quantile` silently ignores its mask. Drop
the option first: `ak.to_numpy(ak.drop_none(x))`.

## Other schemas

`schemaclass=` also takes `PFNanoAODSchema`, `ScoutingNanoAODSchema`,
`DelphesSchema`, `TreeMakerSchema`, `PHYSLITESchema`, `FCCSchema`,
`EDM4HEPSchema`, and `BaseSchema` (no behaviours at all — raw branches, useful
when a skim is missing a field some behaviour requires).

For skims that trip the schema's checks:

```python
class Tolerant(NanoAODSchema):
    warn_missing_crossrefs = False
    error_missing_event_ids = False
```
