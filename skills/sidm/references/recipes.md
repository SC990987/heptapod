# Recipes (sidm)

Copy-paste patterns. All paths are relative to `base_directory`; keep the
`.root` there so outputs land alongside it. `BASE` below is that directory.

## Full chain on one file

```python
import json
from tools.analysis.sidm_inspect import SIDMInspectFileTool
from tools.analysis.sidm_leptonjets import SIDMLeptonJetTool
from tools.analysis.sidm_selection import SIDMEventSelectionTool
from tools.analysis.sidm_gen import SIDMGenKinematicsTool

ROOT = "CutDecayFalse_SIDM_BsTo2DpTo2Mu2e_MBs-500_MDp-0p25_ctau-0p4_v3_part-0.root"

rep = json.loads(SIDMInspectFileTool(base_directory=BASE, root_path=ROOT).run())
assert rep["sidm_readiness"]["ready"], rep["sidm_readiness"]["missing"]

reco = json.loads(SIDMLeptonJetTool(base_directory=BASE, root_path=ROOT,
                                    output_path="lj.jsonl").run())
print(reco["preselection_cutflow"], reco["channels"])

sel = json.loads(SIDMEventSelectionTool(base_directory=BASE,
                 leptonjets_jsonl="lj.jsonl",
                 hist_bins=40, hist_range=[0, 1000]).run())
print(sel["ljlj_mass_stats"])          # median should be ~ MBs

gen = json.loads(SIDMGenKinematicsTool(base_directory=BASE, root_path=ROOT).run())
print(gen["mean_dilepton_dR"], gen["stats"]["lxy"]["mean"])
```

## Plot the LJ-LJ (bound-state) mass

The selection tool saves one array per channel:
`<jsonl_base>_selection_ljlj_mass_{all,4mu,2mu2e}.npy`.

```python
import os, numpy as np, matplotlib.pyplot as plt
import mplhep as hep; plt.style.use(hep.style.CMS)

m = np.load(os.path.join(BASE, "lj_selection_ljlj_mass_2mu2e.npy"))
plt.hist(m, bins=40, range=(0, 1000), histtype="step", label="2µ2e")
hep.cms.label("Simulation", data=False, rlabel="(13 TeV)")
plt.xlabel(r"$m_{\mathrm{LJ,LJ}}$ [GeV]"); plt.ylabel("Events")
plt.legend(); plt.savefig(os.path.join(BASE, "ljlj_mass.png"), dpi=150)
```

## LJ pT-threshold scan (Sec. 4.2.1)

```python
import json, numpy as np, os
from tools.analysis.sidm_leptonjets import SIDMLeptonJetTool
from tools.analysis.sidm_selection import SIDMEventSelectionTool

for pt in (30, 40, 50, 60):
    SIDMLeptonJetTool(base_directory=BASE, root_path=ROOT,
        output_path=f"lj_pt{pt}.jsonl",
        field_overrides={"leptonjets": {"lj_pt_min": pt}}).run()
    s = json.loads(SIDMEventSelectionTool(base_directory=BASE,
        leptonjets_jsonl=f"lj_pt{pt}.jsonl",
        output_prefix=f"scan_pt{pt}").run())
    st = s["ljlj_mass_stats"]["2mu2e"]
    print(f"pT>{pt}: N={st['n']}  median m(LJ,LJ)={st['median']:.1f}")
```

Swap the override for the cone scan: `{"leptonjets": {"jet_radius": r}}` with
`r` in 0.1/0.2/0.3/0.4.

## Isolation working-point scan

```python
import json
for iso in (0.1, 0.15, 0.2, 0.3, 0.5):
    r = json.loads(SIDMLeptonJetTool(base_directory=BASE, root_path=ROOT,
        output_path=f"lj_iso{iso}.jsonl",
        field_overrides={"leptonjets": {"iso_max": iso}}).run())
    print(iso, r["preselection_cutflow"]["two_leptonjets"], r["channels"])
```

## Overlay several mass points

```python
import glob, os, json, numpy as np, matplotlib.pyplot as plt
import mplhep as hep; plt.style.use(hep.style.CMS)
from tools.analysis.sidm_leptonjets import SIDMLeptonJetTool
from tools.analysis.sidm_selection import SIDMEventSelectionTool

for root in glob.glob(os.path.join(BASE, "*BsTo2DpTo2Mu2e*MBs-*.root")):
    tag = os.path.basename(root).split("MBs-")[1].split("_")[0]
    SIDMLeptonJetTool(base_directory=BASE, root_path=os.path.basename(root),
                      output_path=f"lj_{tag}.jsonl").run()
    SIDMEventSelectionTool(base_directory=BASE, leptonjets_jsonl=f"lj_{tag}.jsonl",
                           output_prefix=f"sel_{tag}").run()
    m = np.load(os.path.join(BASE, f"sel_{tag}_ljlj_mass_2mu2e.npy"))
    plt.hist(m, bins=50, range=(0, 1200), histtype="step",
             density=True, label=f"MBs {tag}")
hep.cms.label("Simulation", data=False, rlabel="(13 TeV)")
plt.xlabel(r"$m_{\mathrm{LJ,LJ}}$ [GeV]"); plt.legend()
plt.savefig(os.path.join(BASE, "ljlj_masspoints.png"), dpi=150)
```

## Gen-level kinematics arrays

`SIDMGenKinematicsTool` saves `<root_base>_genkin_<name>.npy` for
`dp_pt, dp_eta, dphi, lxy, dR_ee, dR_mm, lead_e_pt, sublead_e_pt,
lead_mu_pt, sublead_mu_pt`. Load and plot the same way as the mass arrays;
`stats[...]["mean"]` gives the summary values (e.g. mean di-lepton ΔR of Fig. 6).
