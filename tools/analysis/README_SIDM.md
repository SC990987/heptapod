# SIDM two-Lepton-Jet analysis tools (CMS AN-23-107)

A set of HEPTAPOD tools that reproduce results from the CMS search for
Self-Interacting Dark Matter in events with two **Lepton Jets** (AN-23-107)
starting from an **LLPNanoAOD** ROOT file. Reading is done with **coffea**
`NanoEvents`; Lepton-Jet clustering uses the **fastjet + awkward**
`ClusterSequence` (with a pure-python anti-kT reference as a validated
fallback).

The signal is a χχ̄ bound state (`Bs`) that decays to a pair of dark photons
(`Dp`, PDG id **32**), each of which decays to a displaced, collimated
lepton pair. Those pairs are reconstructed as Lepton Jets, and the χχ̄ mass is
the invariant mass of the two leading Lepton Jets.

## The four tools (run in this order)

| Tool | Module | What it does |
|------|--------|--------------|
| `SIDMInspectFileTool` | `sidm_inspect.py` | Report the file's trees / collections / branches and a **readiness check** listing any branches the pipeline needs but can't find (with the config key to fix each). **Run this first.** |
| `SIDMLeptonJetTool` | `sidm_leptonjets.py` | Object selection (Table 12) + e-γ and PF-DSA cross-cleaning + anti-kT R=0.4 clustering + eγ/µ categorization + isolation + displacement cuts (Sec. 4). Writes per-event `leptonjets` + trigger/PV/cosmic flags to JSONL. |
| `SIDMEventSelectionTool` | `sidm_selection.py` | Event selection (trigger → PV filter → cosmic veto → ≥2 selected LJs), 4µ / 2µ2e channel split, and the **LJ-LJ invariant mass** (bound-state mass) per channel, with a cutflow (Sec. 5). |
| `SIDMGenKinematicsTool` | `sidm_gen.py` | Gen-level signal kinematics (Sec. 3, Figs 2–6): dark-photon pT/η/Δφ/Lxy, leading/sub-leading lepton pT, di-lepton ΔR. |

All selection thresholds and branch names live in one place: `sidm_config.py`
(`SIDMConfig`). Every tool accepts `field_overrides` (a nested dict) so you can
adapt to a different LLPNanoAOD production without touching code.

## Quickstart

```python
from tools.analysis.sidm_inspect import SIDMInspectFileTool
from tools.analysis.sidm_leptonjets import SIDMLeptonJetTool
from tools.analysis.sidm_selection import SIDMEventSelectionTool
from tools.analysis.sidm_gen import SIDMGenKinematicsTool

BASE = "/path/to/workspace"   # base_directory; all paths are relative to it
ROOT = "CutDecayFalse_SIDM_BsTo2DpTo2Mu2e_MBs-500_MDp-0p25_ctau-0p4_v3_part-0.root"

# 1. Learn the file's schema and confirm readiness
print(SIDMInspectFileTool(base_directory=BASE, root_path=ROOT).run())

# 2. Reconstruct Lepton Jets -> JSONL
SIDMLeptonJetTool(base_directory=BASE, root_path=ROOT,
                  output_path="lj.jsonl").run()

# 3. Event selection + cutflow + LJ-LJ (bound-state) mass
SIDMEventSelectionTool(base_directory=BASE,
                       leptonjets_jsonl="lj.jsonl").run()

# 4. Gen-level signal kinematics (Figs 2-6)
SIDMGenKinematicsTool(base_directory=BASE, root_path=ROOT).run()
```

`max_events=N` on the reconstruction / gen tools caps the number of events for
quick iteration.

## Object selections (Table 12) and the branch map

Defaults are set for the standard CMS SIDM LLPNanoAOD branch names (verified
against a real 2µ2e signal file). Override any of these via
`field_overrides={"objects": {...}, "leptonjets": {...}, "fields": {...}}`.

| Object | Cuts | Key branches (config key) |
|--------|------|---------------------------|
| GED electron | pT>10, |η|<2.4, MVA v2 NoIso WPL | `Electron_mvaFall17V2noIso_WPL` (`objects.electron_id_branch`), `Electron_lostHits` (`leptonjets.electron_lost_hits_branch`) |
| PF photon | pT>20, |η|<2.4, modified-loose ID | `Photon_vidNestedWPBitmap` (`objects.photon_vid_bitmap_branch`), `Photon_pixelSeed`, `Photon_cutBased` |
| PF muon | pT>5, |η|<2.4, loose ID | `Muon_looseId` (`objects.pfmuon_id_branch`), `Muon_trkNumPixelHits` (`leptonjets.pfmuon_pixel_hits_branch`) |
| DSA muon | pT>10, |η|<2.4, displacedID>0 | `DSAMuon_displacedID` (`objects.dsamuon_displacedid_branch`) |
| PF jet (for iso) | closest within ΔR<0.4 | `Jet_chEmEF`, `Jet_neEmEF`, `Jet_muEF` |

**Lepton Jets** (Sec. 4.2–4.4): anti-kT R=0.4; LJ pT>30, |η|<2.4;
µ-type requires N_µ^LJ ≥ 2; isolation `IsoLJ = (E_MJ/E_LJ)(1 − f_lepton) < 0.2`;
displacement — eγ-type electrons need lostHits ≥ 1, µ-type PF muons need
pixelHits ≤ 2.

**Event selection** (Sec. 5): OR of the four `HLT_DoubleL2Mu2{3,5}NoVtx_2Cha…`
triggers; `Flag_goodVertices`; a back-to-back-muon cosmic veto; ≥2 selected LJs;
channel = 4µ (both µ-type) or 2µ2e (one µ-type + one eγ-type).

## Choices worth knowing (and how to change them)

- **Photon "modified loose" ID (Sec. 4.1.2).** The note drops the σ_iηiη and
  photon-isolation cuts from the loose cut-based ID. Stock NanoAOD doesn't store
  the absolute ρ-corrected isolations, so the default (`photon_id_mode =
  "relaxed_bitmap"`) decodes `Photon_vidNestedWPBitmap` (2 bits × 7 cuts) and
  requires the loose decisions for **MinPt, SCEta, H/E, chIso, nhIso** while
  **skipping σ_iηiη (cut 3) and photon iso (cut 6)** — a faithful reproduction
  from the standard bitmap. Alternatives: `"cutbased_loose"` (`Photon_cutBased≥1`)
  or `"table7"` (needs absolute-iso branches).
- **PF-DSA cross-clean (Sec. 4.1.4 / Table 9).** Default is a robust ΔR match
  (`crossclean.method = "dr"`, remove a DSA within ΔR<0.1 of a selected PF muon).
  The full shared-segment logic of Table 9 (`method = "segment"`) can use the
  `DSAMuon_nSegments` / `DSAMuon_muonMatch*` branches present in the file.
- **Primary-vertex filter (Table 15).** Uses `Flag_goodVertices` when present.
  The note prints "24 mm / 2 mm"; the code uses the standard CMS goodVertices
  values (|z|<24 cm, ρ<2 cm) — override `events.pv_absz_max` / `events.pv_rho_max`
  if you really mean millimetres.
- **Dark-photon PDG id** defaults to **32** (`fields.dark_photon_pdgid`);
  change it if your sample uses a different id.
- **Clustering engine** is fastjet by default (`leptonjets.cluster_backend =
  "fastjet"`); it falls back to the built-in anti-kT if fastjet is unavailable.
  The two agree exactly (verified on real data, 0/800 events differ).

## Validation

On a real 2µ2e signal file (`…MBs-500_MDp-0p25_ctau-0p4…`):

- `extract_events` resolves every branch with **zero** warnings.
- fastjet and built-in anti-kT give **identical** jets (0/800 events differ).
- Channel assignment: **118 2µ2e, 0 4µ** (correct for a 2µ2e sample).
- **LJ-LJ mass peaks at 450–500 GeV (median 500.4)** — recovers M_Bs = 500.
- Gen: Δφ(ZdZd) ≈ 2.98 (≈π), mean Lxy ≈ 33 cm (consistent with the ctau-0.4 mm ↔
  ~30 cm lab decay-length mapping of Table 5), di-lepton ΔR ≈ 0.003 for the
  0.25 GeV dark photon.

## Tests

`test_sidm.py` covers object selection, the photon bitmap logic, anti-kT
clustering, categorization / isolation / displacement, the selection cutflow,
gen kinematics, and an injected-signal integration test that recovers the
bound-state mass (median LJ-LJ mass = injected mass). The pure-function tests
need no external packages; the integration test needs `awkward` + `fastjet`; an
optional end-to-end test exercises the tool classes when `coffea` is installed.

```
python tools/analysis/test_sidm.py
```
