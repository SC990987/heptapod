# coffea's subsystems

Each of these already implements the bookkeeping. Drive it; do not reimplement
it. Verified against coffea 2026.7.0.

## Event selection and cutflows — `PackedSelection`

```python
from coffea.analysis_tools import PackedSelection

sel = PackedSelection()
sel.add("trigger",  ak.to_numpy(events.HLT.IsoMu24))
sel.add("two_muons", ak.to_numpy(ak.num(events.Muon[events.Muon.pt > 25])) >= 2)
sel.add("met50",     ak.to_numpy(events.MET.pt) > 50)

mask = sel.all("trigger", "two_muons", "met50")   # AND of all
some = sel.any("trigger", "met50")                # OR
inv  = sel.require(trigger=True, met50=False)     # explicit True/False per cut

cf = sel.cutflow("trigger", "two_muons", "met50").result()
cf.labels        # ['initial','trigger','two_muons','met50']
cf.nevonecut     # events passing each cut ALONE  (index 0 = initial)
cf.nevcutflow    # events passing cuts 1..i in ORDER (index 0 = initial)

nm = sel.nminusone("trigger", "two_muons", "met50").result()
nm.labels        # ['initial','N - trigger','N - two_muons','N - met50','N']
nm.nev
```

Every mask must be a flat per-event boolean of length `len(events)`. Reduce
jagged masks first with `ak.any` / `ak.all` / `ak.num(...) >= n`.

**`nevonecut[i]` is not a sequential efficiency** — it is that cut in
isolation. The sequential numbers are `nevcutflow`. Both include the initial
count at index 0, aligned with `labels`.

## Weights and systematics — `Weights`

```python
from coffea.analysis_tools import Weights

w = Weights(len(events))                      # or Weights(n, storeIndividual=True)
w.add("genw", ak.to_numpy(events.genWeight))
w.add("pileup", nominal, weightUp=up, weightDown=down)

w.weight()             # nominal product of every contribution
w.variations           # {'pileupUp', 'pileupDown'}
w.weight("pileupUp")   # product with that one contribution varied
```

`w.partial_weight(include=[...])` / `exclude=[...]` requires the object to have
been created with `storeIndividual=True`, otherwise it raises `ValueError`.

Weights are **per event**. To fill a per-object histogram, broadcast:
`np.repeat(w.weight(), ak.to_numpy(ak.num(collection)))`.

## Luminosity — `lumi_tools`

Data only. MC is not certified.

```python
from coffea.lumi_tools import LumiMask, LumiList, LumiData

mask = LumiMask("Cert_..._Golden.json")(events.run, events.luminosityBlock)
events = events[mask]

lumi = LumiData("lumi_brilcalc.csv").get_lumi(
    LumiList(events.run, events.luminosityBlock))   # /pb
```

## Scale factors — `correctionlib` (POG JSON)

Always list the file first; POG corrections are strict about the number, order
and type of their inputs.

```python
import correctionlib
cset = correctionlib.CorrectionSet.from_file("muon_Z.json.gz")

list(cset)                                   # correction names
c = cset["NUM_MediumID_DEN_TrackerMuons"]
[(i.name, str(i.type)) for i in c.inputs]    # declared inputs, IN ORDER

flat_eta = ak.to_numpy(ak.flatten(abs(events.Muon.eta)))   # usually |eta|
flat_pt  = ak.to_numpy(ak.flatten(events.Muon.pt))
sf = c.evaluate(flat_eta, flat_pt, "nominal")
sf = ak.unflatten(sf, ak.num(events.Muon))   # back to per-event shape
```

Membership: use `name in list(cset)`. `in` on the `CorrectionSet` itself is not
a reliable membership test, and a bad name surfaces as an opaque C++ `map::at`.

Inputs must be flat numpy arrays, not jagged — flatten, evaluate, `ak.unflatten`.

## Older correction formats — `lookup_tools`

For ROOT histograms and JEC/JER text files:

```python
from coffea.lookup_tools import extractor
ext = extractor()
ext.add_weight_sets(["* * scalefactors.root"])   # or explicit "name hist file"
ext.finalize()
ev = ext.make_evaluator()
ev.keys()
sf = ev["histogram_name"](flat_eta, flat_pt)
```

## Histograms — `hist`

```python
import hist
h = hist.Hist(
    hist.axis.Regular(50, 0, 200, name="pt", label="muon $p_T$ [GeV]"),
    hist.axis.StrCategory([], name="syst", growth=True),
    storage=hist.storage.Weight(),
)
h.fill(pt=flat_pt, syst="nominal", weight=flat_w)

h.view().value      # bin contents
h.view().variance   # sum of w^2
h.axes[0].edges
h.axes[0].centers
h.sum().value       # integral
h[{"syst": "nominal"}]        # project a category
h.project("pt")               # sum over other axes
```

`hist.axis.StrCategory(growth=True)` is the idiomatic way to keep systematic
variations, samples or regions in one object.

Other axis types: `Variable(edges)` for non-uniform bins, `Integer(lo, hi)`,
`IntCategory`.

## Other subsystems

| Module | Provides |
|--------|----------|
| `coffea.jetmet_tools` | `JetCorrectionUncertainty`, JEC/JER stacks, `CorrectedJetsFactory` |
| `coffea.btag_tools` | `BTagScaleFactor` for the legacy CSV format |
| `coffea.dataset_tools` | preprocessing and splitting file sets for a run |
| `coffea.processor` | the `ProcessorABC` batch model, for scaling out |
| `coffea.ml_tools` | ONNX / torch / xgboost inference wrappers |

Reach for `coffea.processor` + `dataset_tools` only when a run outgrows a
single script; for one file, plain columnar code is simpler and faster to
write.
