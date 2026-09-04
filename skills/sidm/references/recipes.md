# Recipes (sidm)

Reconstruction is a tool; everything downstream is coffea. `BASE` is the
sandbox, `ROOT` the input file relative to it, `CFG = "configs/sidm.yaml"`.

## Full chain on one file

```python
import json, awkward as ak, numpy as np
from coffea.nanoevents import NanoEventsFactory
from coffea.analysis_tools import PackedSelection
from tools.analysis.nanoaod_inspect import InspectFileTool
from tools.analysis.leptonjets import LeptonJetTool
from tools.analysis.analysis_config import build_schema, load_config

# 1. validate the config against the file BEFORE running anything
insp = json.loads(InspectFileTool(base_directory=BASE, root_path=ROOT, config=CFG).run())
assert not insp.get("config_readiness", {}).get("problems"), insp["config_readiness"]

# 2. reconstruct -> a NanoAOD-style file carrying the LeptonJet collection
lj = json.loads(LeptonJetTool(base_directory=BASE, root_path=ROOT, config=CFG).run())
print(lj["n_leptonjets"], "lepton jets;", lj["category_ids"])

# 3. everything else is coffea
cfg    = load_config(CFG)
schema = build_schema(cfg, extra_mixins={"LeptonJet": "PtEtaPhiMLorentzVector"})
ev = NanoEventsFactory.from_root(
    {f"{BASE}/{lj['output_root']}": "Events"}, schemaclass=schema).events()

sel = ev.LeptonJet[ev.LeptonJet.selected]

s = PackedSelection()
s.add("trigger",        ak.to_numpy(ev.Flag.trigger))
s.add("pv_filter",      ak.to_numpy(ev.Flag.pv_filter))
s.add("cosmic_veto",    ak.to_numpy(ev.Flag.cosmic_veto))
s.add("two_leptonjets", ak.to_numpy(ak.num(sel)) >= 2)
cuts = ("trigger", "pv_filter", "cosmic_veto", "two_leptonjets")
cf = s.cutflow(*cuts).result()
print(list(zip(cf.labels, [int(x) for x in cf.nevcutflow])))

two  = sel[s.all(*cuts) & (ak.num(sel) >= 2)]
mass = (two[:, 0] + two[:, 1]).mass          # the LJ-LJ / bound-state mass
print("LJ-LJ mass mean:", round(float(ak.mean(mass)), 2), "GeV")
```

## Plot the LJ-LJ mass

```python
import hist
h = hist.Hist(hist.axis.Regular(60, 0, 800, name="m",
                                label=r"$m_{LJ,LJ}$ [GeV]"),
              storage=hist.storage.Weight())
h.fill(m=ak.to_numpy(mass))
print("integral:", h.sum().value, "peak at:", h.axes[0].centers[h.view().value.argmax()])
```

The peak should sit at the `MBs` in the sample name. On
`..._MBs-500_MDp-0p25_ctau-0p4_...` the mean comes out at ~500 GeV.

## Split by channel

The file is self-describing: `LeptonJetTool` writes one boolean per channel, so
coffea gives you `events.Channel` and you never need an id->name legend.

```python
print(sorted(ev.Channel.fields))          # ['2mu2e', '4mu']
```

Make the channel a **histogram axis** rather than looping over masks. It
composes with every other axis, keeps all channels in one object, and lets you
project the inclusive distribution back out:

```python
import hist

# slice the events ONCE -- everything downstream is aligned by construction
keep     = s.all(*cuts)
ev_pass  = ev[keep]
sel_pass = sel[keep]
two      = sel_pass[ak.num(sel_pass) >= 2]
mass     = (two[:, 0] + two[:, 1]).mass

h = hist.Hist(
    hist.axis.Regular(40, 0, 800, name="m", label=r"$m_{LJ,LJ}$ [GeV]"),
    hist.axis.StrCategory(sorted(ev.Channel.fields), name="channel"),
    storage=hist.storage.Weight())

for name in ev.Channel.fields:
    h.fill(m=ak.to_numpy(mass[ak.to_numpy(ev_pass.Channel[name])]), channel=name)

h[{"channel": "2mu2e"}]      # one channel
h.project("m")               # inclusive, all channels summed
```

On `BsTo2DpTo2Mu2e` this gives `2mu2e` n=116 mean 509.5 GeV and `4mu` empty --
which is the right answer for that sample.

Two things worth doing this way round:

* **Slice once.** Re-deriving the mask at fill time
  (`ak.to_numpy(ev.channelId)[mask] == cid`) is how the channel array and the
  mass array drift out of alignment. Apply `keep` to the events, then work from
  the sliced objects.
* **Declare the categories up front**, `StrCategory(sorted(ev.Channel.fields))`,
  not `growth=True`. With growth, a channel that happens to have zero events
  never appears in the axis at all, so an empty channel and a missing channel
  look identical.

`ev.channelId` (an int, legend in the tool's JSON under `channel_ids`) is still
written for compactness, but prefer `ev.Channel` -- it needs no legend.

## Dark-photon Lxy

A tool, because the vertex convention is easy to get wrong:

```python
from tools.analysis.gen_decay_length import GenDecayLengthTool
r = json.loads(GenDecayLengthTool(base_directory=BASE, root_path=ROOT,
                                  config=CFG, resonance_pdgid=32).run())
print(r["definition"], r["statistics"])
```

Cross-check: `<Lxy> ~ gamma * c*tau` with `gamma = E/m`. A 0.25 GeV dark photon
at ~250 GeV has `gamma ~ 1000`, so `ctau = 0.4 mm` means tens of cm. On the
signal sample the answer is ~34 cm — if you get ~0.04 cm you measured the
production vertex instead.

## Dark-photon pT / eta / dphi and the di-lepton dR

Coffea, not a tool. Use the `coffea` skill's "Gen-level resonance daughters,
paired correctly" recipe: pair by the ancestor index (never by position) and
take last copies (`statusFlags` bit 13). Done right, 500 events give 1000
resonance pairs with `dR_ee = 0.003966` and `dR_mm = 0.001575`.

```python
g  = ev_gen.GenPart                       # ev_gen = the ORIGINAL nanoAOD file
dp = g[abs(g.pdgId) == 32]
print("dark photons per event:", float(ak.mean(ak.num(dp))))   # 2.0
dphi = abs(dp[ak.num(dp) >= 2][:, 0].delta_phi(dp[ak.num(dp) >= 2][:, 1]))
print("mean |dphi| between the two:", round(float(ak.mean(dphi)), 3))  # ~2.98, back-to-back
```

Note `dp` needs the Lorentz-vector behaviour: LLPnanoAOD's `GenPart` carries
both cartesian and polar momenta, so the mixin is dropped and you must re-zip.
See the `coffea` skill's pitfalls.

## Scans via inline overrides (no file edit)

```python
for pt_min in (20, 30, 40):
    out = json.loads(LeptonJetTool(
        base_directory=BASE, root_path=ROOT, config=CFG,
        overrides={"leptonjet": {"pt_min": pt_min}},
        output_path=f"lj_pt{pt_min}.root").run())
    print(pt_min, out["n_leptonjets"], out["cutflow"][-1])
```

## Many files

Do not loop in Python — use `scripts/lpc_scaleout.py` with an analysis module
that calls `LeptonJetTool` per file and returns `hist` objects. See the
`scaleout` skill.

```bash
PY=$(grep -h '^python_path:' ~/.toolbase/cache/heptapod/*/.install_meta.yaml | head -1 | awk '{print $2}')
$PY scripts/lpc_scaleout.py datasets --mbs 500                       # pick a sample
$PY scripts/lpc_scaleout.py local  --analysis scripts/example_analysis_sidm.py \
    --dataset <name> --max-files 2 --workdir work/dev                # always first
$PY scripts/lpc_scaleout.py submit --analysis scripts/example_analysis_sidm.py \
    --dataset <name> --workdir work/full
$PY scripts/lpc_scaleout.py status --workdir work/full               # poll, do not block
$PY scripts/lpc_scaleout.py merge  --workdir work/full
```

## Plots and a durable archive

```bash
$PY scripts/plot_results.py plot work/full/merged.pkl \
    --outdir work/plots_full --label "MBs-500 ctau-0p4"
```

Writes one PNG per histogram, plus `summary.json` and `histograms.root` — the
pair to keep for later comparison, since both are readable without this repo.
Overlay two runs with `plot_results.py compare a.pkl b.pkl --labels a b`.

Always report `report.files_failed` from the merge; an empty dict is the only
proof every file was read.
