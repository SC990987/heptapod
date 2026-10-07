# Recipes

Worked, runnable examples. Every block below is executed by
`tools/analysis/test_coffea_skill_docs.py` against a synthetic NanoAOD file, so
these stay true as coffea moves.

Each assumes this preamble:

```python
import awkward as ak
import numpy as np
from coffea.nanoevents import NanoEventsFactory, NanoAODSchema

events = NanoEventsFactory.from_root(
    {PATH: "Events"}, schemaclass=NanoAODSchema
).events()
```

## Open a file and look around

```python
print(len(events), "events")
print("collections:", sorted(events.fields))
print("Muon branches:", sorted(events.Muon.fields))
print("muons per event:", ak.to_list(ak.num(events.Muon)[:5]))
```

## Object selection, then an event cut

```python
good_mu = events.Muon[(events.Muon.pt > 25) & (abs(events.Muon.eta) < 2.4)]
has_two = ak.num(good_mu) >= 2
sel     = events[has_two]
sel_mu  = good_mu[has_two]
print(f"{len(sel)} / {len(events)} events keep >= 2 good muons")
```

## Leading and sub-leading object

```python
srt  = events.Muon[ak.argsort(events.Muon.pt, ascending=False)]
pad  = ak.pad_none(srt, 2)
lead, sub = pad[:, 0], pad[:, 1]
print("leading pt (first 5):", ak.to_list(lead.pt[:5]))
```

`ak.pad_none` guarantees the slots exist; entries are `None` where the event
had too few muons.

## Invariant mass of a pair

```python
two   = events.Muon[ak.num(events.Muon) >= 2]
dimu  = two[:, 0] + two[:, 1]
print("dimuon mass, first 5:", [round(m, 2) for m in ak.to_list(dimu.mass[:5])])
```

## Best opposite-sign pair in each event

```python
pairs   = ak.combinations(events.Muon, 2, fields=["a", "b"])
os_pair = pairs[pairs.a.charge != pairs.b.charge]
cand    = os_pair.a + os_pair.b
best    = cand[ak.argmax(cand.pt, axis=1, keepdims=True)]
mass    = ak.flatten(best.mass, axis=1)
print("events with an OS pair:", int(ak.sum(ak.num(cand) > 0)))
```

## Delta R matching (reco to gen)

```python
matched, dr = events.Muon.nearest(events.GenPart, return_metric=True)
is_matched  = ak.fill_none(dr < 0.1, False)
print("matched fraction:",
      round(float(ak.sum(is_matched)) / float(ak.sum(ak.num(events.Muon))), 4))
```

## Overlap removal (cross-cleaning)

Keep jets that are **not** near a selected muon. An unmatched jet has
`dr = None`, so fill with `True`:

```python
sel_mu   = events.Muon[events.Muon.pt > 15]
_, dr    = events.Jet.nearest(sel_mu, return_metric=True)
clean    = events.Jet[ak.fill_none(dr > 0.4, True)]
print("jets", int(ak.sum(ak.num(events.Jet))), "-> clean", int(ak.sum(ak.num(clean))))
```

## Full pairwise Delta R table

```python
table = events.Jet.metric_table(events.Muon)   # per event: n_jet x n_muon
print("first non-empty row:",
      next(([round(x, 3) for x in row] for ev in ak.to_list(table) for row in ev), None))
```

## Cutflow with PackedSelection

```python
from coffea.analysis_tools import PackedSelection

sel = PackedSelection()
sel.add("trigger",   ak.to_numpy(events.HLT.IsoMu24))
sel.add("two_muons", ak.to_numpy(ak.num(events.Muon[events.Muon.pt > 25])) >= 2)
sel.add("met50",     ak.to_numpy(events.MET.pt) > 50)

cf = sel.cutflow("trigger", "two_muons", "met50").result()
for label, alone, seq in zip(cf.labels, cf.nevonecut, cf.nevcutflow):
    print(f"  {label:12s} alone={int(alone):5d}  sequential={int(seq):5d}")

nm = sel.nminusone("trigger", "two_muons", "met50").result()
print("N-1:", list(zip(nm.labels, [int(x) for x in nm.nev])))
```

## Event weights with a systematic

```python
from coffea.analysis_tools import Weights

w = Weights(len(events))
w.add("genw", ak.to_numpy(events.genWeight))
pu = ak.to_numpy(events.Pileup.nTrueInt) / 40.0
w.add("pileup", pu, weightUp=pu * 1.05, weightDown=pu * 0.95)

print("sum nominal:", round(float(w.weight().sum()), 2))
print("variations :", sorted(w.variations))
print("sum pileupUp:", round(float(w.weight("pileupUp").sum()), 2))
```

## Weighted histogram with systematic variations

```python
import hist

h = hist.Hist(
    hist.axis.Regular(20, 0, 200, name="pt", label="muon $p_T$ [GeV]"),
    hist.axis.StrCategory([], name="syst", growth=True),
    storage=hist.storage.Weight(),
)

counts  = ak.to_numpy(ak.num(events.Muon))
flat_pt = ak.to_numpy(ak.flatten(events.Muon.pt))
for variation in ["nominal"] + sorted(w.variations):
    ev_w  = w.weight() if variation == "nominal" else w.weight(variation)
    obj_w = np.repeat(ev_w, counts)          # per-event -> per-object
    h.fill(pt=flat_pt, syst=variation, weight=obj_w)

nominal = h[{"syst": "nominal"}]
print("integral:", round(float(nominal.sum().value), 2))
print("first edges:", nominal.axes[0].edges[:4].tolist())
```

## Gen-particle navigation

```python
gen = events.GenPart
muons_gen = gen[abs(gen.pdgId) == 13]
print("gen muons:", int(ak.sum(ak.num(muons_gen))))
print("their mothers' pdgId:",
      ak.to_list(ak.flatten(muons_gen.distinctParent.pdgId))[:8])
print("prompt flags:", ak.to_list(ak.flatten(gen.hasFlags('isPrompt')))[:8])
```

## Gen-level resonance daughters, paired correctly

Two traps here, both silent. Taking `lep[:, 0]` and `lep[:, 1]` pairs leptons
**across** resonances and drops half your statistics; ignoring last copies
collapses a radiating channel onto a non-radiating one. Pair by the ancestor's
index, and take last copies:

```python
g = events.GenPart

# ancestry still works when the GenPart mixin has been dropped -- the global
# index is there, it is just not exposed as a property
parent = g._apply_global_index(g.distinctParentIdxG)

is_lep = (abs(g.pdgId) == 11) | (abs(g.pdgId) == 13)
is_last = (g.statusFlags & (1 << 13)) != 0            # bit 13 = isLastCopy
keep = is_lep & is_last & (abs(ak.fill_none(parent.pdgId, 0)) == RESONANCE_PDGID)

lep = g[keep]
pidx = g.distinctParentIdxG[keep]                     # which resonance each came from

# group by resonance: sort on the ancestor index, then split on run lengths
order = ak.argsort(pidx, axis=1)
lep, pidx = lep[order], pidx[order]
vec = ak.zip({"pt": lep.pt, "eta": lep.eta, "phi": lep.phi,
              "mass": lep.mass, "pdgId": lep.pdgId},
             with_name="PtEtaPhiMLorentzVector", behavior=cvector.behavior)
groups = ak.unflatten(ak.flatten(vec), ak.flatten(ak.run_lengths(pidx)))

pair = groups[ak.num(groups) >= 2]
print("resonances with two daughters:", len(pair))
if len(pair):
    dr = pair[:, 0].delta_r(pair[:, 1])
    print("mean di-lepton dR:", round(float(ak.mean(dr)), 6))
```

`ak.run_lengths` on the sorted ancestor index is what turns a flat list of
daughters into one group per resonance. A quick check that the pairing is
right: a sample with two resonances per event must give two pairs per event,
not one.

Bit 13 of `statusFlags` is `isLastCopy`; the branch's own docstring lists every
bit, so check it rather than trusting a remembered number.

## Transverse mass with MET

MET has no `eta`, so `delta_phi` — never `delta_r`:

```python
srt  = events.Muon[ak.argsort(events.Muon.pt, ascending=False)]
lead = ak.firsts(srt)
dphi = ak.fill_none(lead.delta_phi(events.MET), 0.0)
lpt  = ak.fill_none(lead.pt, 0.0)
mt   = np.sqrt(2 * lpt * events.MET.pt * (1 - np.cos(dphi)))
print("mean mT:", round(float(ak.mean(mt)), 3))
```

## Save arrays for a later step

```python
import os
flat = ak.to_numpy(ak.flatten(events.Muon.pt))
np.save(os.path.join(OUTDIR, "muon_pt.npy"), flat)
print("saved", flat.size, "values")
```

For anything option-typed, `ak.drop_none` first — `np.save` cannot write a
`MaskedArray`.
