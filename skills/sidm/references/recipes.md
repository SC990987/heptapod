# Recipes (sidm)

Copy-paste patterns. Paths are relative to `base_directory`; `BASE` is that dir,
`CFG = "configs/sidm.yaml"` (a path under BASE, or pass an absolute path / inline
dict). Every tool takes `config` + optional inline `overrides`.

## Full chain on one file

```python
import json
from tools.analysis.nanoaod_inspect import InspectFileTool
from tools.analysis.leptonjets import LeptonJetTool
from tools.analysis.event_selection import EventSelectionTool
from tools.analysis.gen_kinematics import GenKinematicsTool

ROOT = "CutDecayFalse_SIDM_BsTo2DpTo2Mu2e_MBs-500_MDp-0p25_ctau-0p4_v3_part-0.root"

rep = json.loads(InspectFileTool(base_directory=BASE, root_path=ROOT, config=CFG).run())
assert rep["config_readiness"]["ready"], rep["config_readiness"]["missing_in_file"]

reco = json.loads(LeptonJetTool(base_directory=BASE, root_path=ROOT,
                                config=CFG, output_path="lj.jsonl").run())
print(reco["preselection_cutflow"], reco["channels"])

sel = json.loads(EventSelectionTool(base_directory=BASE, leptonjets_jsonl="lj.jsonl",
                 config=CFG, hist_bins=40, hist_range=[0, 1000]).run())
print(sel["mass_stats"])          # median should be ~ MBs

gen = json.loads(GenKinematicsTool(base_directory=BASE, root_path=ROOT, config=CFG).run())
print(gen["mean_dilepton_dR"], gen["stats"]["lxy"]["mean"])
```

## Plot the LJ-LJ (bound-state) mass

EventSelectionTool saves one array per channel: `<jsonl_base>_selection_mass_{all,4mu,2mu2e}.npy`.

```python
import os, numpy as np, matplotlib.pyplot as plt
import mplhep as hep; plt.style.use(hep.style.CMS)
m = np.load(os.path.join(BASE, "lj_selection_mass_2mu2e.npy"))
plt.hist(m, bins=40, range=(0, 1000), histtype="step", label="2µ2e")
hep.cms.label("Simulation", data=False, rlabel="(13 TeV)")
plt.xlabel(r"$m_{\mathrm{LJ,LJ}}$ [GeV]"); plt.ylabel("Events"); plt.legend()
plt.savefig(os.path.join(BASE, "ljlj_mass.png"), dpi=150)
```

## Scans via inline overrides (no file edit)

```python
import json
# LJ pT-threshold scan (Sec. 4.2.1)
for pt in (30, 40, 50, 60):
    LeptonJetTool(base_directory=BASE, root_path=ROOT, config=CFG,
        overrides={"leptonjet": {"pt_min": pt}}, output_path=f"lj_pt{pt}.jsonl").run()
    s = json.loads(EventSelectionTool(base_directory=BASE, config=CFG,
        leptonjets_jsonl=f"lj_pt{pt}.jsonl", output_prefix=f"scan_pt{pt}").run())
    st = s["mass_stats"]["2mu2e"]
    print(f"pT>{pt}: N={st['n']}  median={st['median']:.1f}")
```

Cone scan: `overrides={"clustering": {"radius": r}}`.
Isolation scan: `overrides={"leptonjet": {"isolation": {"max": iso}}}`.

## Overlay several mass points

```python
import glob, os, json, numpy as np, matplotlib.pyplot as plt
import mplhep as hep; plt.style.use(hep.style.CMS)
from tools.analysis.leptonjets import LeptonJetTool
from tools.analysis.event_selection import EventSelectionTool

for root in glob.glob(os.path.join(BASE, "*BsTo2DpTo2Mu2e*MBs-*.root")):
    tag = os.path.basename(root).split("MBs-")[1].split("_")[0]
    LeptonJetTool(base_directory=BASE, root_path=os.path.basename(root),
                  config=CFG, output_path=f"lj_{tag}.jsonl").run()
    EventSelectionTool(base_directory=BASE, config=CFG,
                       leptonjets_jsonl=f"lj_{tag}.jsonl", output_prefix=f"sel_{tag}").run()
    m = np.load(os.path.join(BASE, f"sel_{tag}_mass_2mu2e.npy"))
    plt.hist(m, bins=50, range=(0, 1200), histtype="step", density=True, label=f"MBs {tag}")
hep.cms.label("Simulation", data=False, rlabel="(13 TeV)")
plt.xlabel(r"$m_{\mathrm{LJ,LJ}}$ [GeV]"); plt.legend(); plt.savefig(os.path.join(BASE, "masspoints.png"), dpi=150)
```

## Gen-level arrays

GenKinematicsTool saves `<root_base>_genkin_<name>.npy` for `res_pt, res_eta,
dphi, lxy, dR_ee, dR_mm, lead_ee_pt, sublead_ee_pt, lead_mm_pt, sublead_mm_pt`.
`stats[name]["mean"]` gives the summary values (e.g. mean di-lepton ΔR).
