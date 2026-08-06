---
name: sidm
bundle: leptonjets
description: Reproduce CMS AN-23-107 (Self-Interacting Dark Matter search with two Lepton Jets) from an LLPNanoAOD ROOT file, using the generic Lepton-Jet tools driven by configs/sidm.yaml. Use whenever the user mentions SIDM, Lepton Jets, dark photons / Zd / "Dp", displaced or DSA muons, LLPNanoAOD, the BsTo2DpTo2Mu2e / BsTo2DpTo4Mu signal samples, the LJ-LJ or bound-state mass, or the LeptonJetTool / EventSelectionTool / GenKinematicsTool / InspectFileTool tools applied to SIDM. This skill owns the SIDM-specific knowledge; the tools themselves are generic and carry no SIDM values.
---

# SIDM two-Lepton-Jet analysis (CMS AN-23-107)

The Lepton-Jet tools are **generic and config-driven** — they carry no
analysis-specific values. The entire SIDM analysis is expressed in a config,
`configs/sidm.yaml`. To reproduce the note, pass that config to every tool:
`config: configs/sidm.yaml`. To tweak a few cuts, pass `overrides: {...}` inline.

The signal: a χχ̄ bound state (`Bs`) → two dark photons (`Dp`, **PDG id 32**) →
displaced, collimated lepton pairs reconstructed as Lepton Jets; the χχ̄ mass is
the invariant mass of the two leading LJs.

```
file.root ──[InspectFileTool config=configs/sidm.yaml]──▶ readiness/validation (fix branch names here)
          ──[LeptonJetTool   config=configs/sidm.yaml]──▶ leptonjets JSONL (+ trigger/PV/cosmic + channel)
                                                            └─[EventSelectionTool]─▶ cutflow + 4µ/2µ2e + LJ-LJ mass
          ──[GenKinematicsTool config=configs/sidm.yaml]─▶ dark-photon pT/η/Δφ/Lxy, di-lepton ΔR (Figs 2–6)
```

## Three rules

### 1. Inspect + validate the config first; keep the file under base_directory
Run `InspectFileTool` with `config: configs/sidm.yaml` before anything else. Its
`config_readiness` block validates the config against the file and lists any
missing collection/branch with the **config path** to fix it. All tool paths are
relative to `base_directory` (`tb config set heptapod base_directory <dir>`), so
the `.root` and (if not given by absolute path) the config live under it.

### 2. Always pass the config; the tools do nothing SIDM-specific without it
`LeptonJetTool`, `EventSelectionTool`, and `GenKinematicsTool` all take
`config` (a YAML path or an inline dict) plus optional inline `overrides`
(deep-merged last). Without a config they fall back to neutral defaults and
reconstruct nothing meaningful. The chain is file-based: LeptonJetTool writes a
`leptonjets` JSONL that EventSelectionTool consumes.

### 3. Sanity-check against the filename
The sample name encodes the truth. The LJ-LJ mass peak should sit at `MBs`, and
the mean dark-photon Lxy should match the `ctau`↔lab mapping. A peak far from
`MBs` usually means a dropped constituent (e.g. the ΔR(e,γ)<0.025 photon veto on
an ultra-collimated pair) — not a tool bug.

## The SIDM values (all encoded in configs/sidm.yaml)

- **Constituents** (Table 12): GED electrons (pT>10, MVA v2 NoIso WPL), PF
  photons (pT>20, modified-loose = VID bitmap dropping σιηιη + photon-iso), PF
  muons (pT>5, loose), DSA muons (pT>10, displacedID>0). e–γ and PF–DSA overlaps
  cross-cleaned.
- **Lepton Jets**: anti-kT R=0.4; LJ pT>30, |η|<2.4; categories mu-type (≥1
  muon, require N_µ≥2) / eγ-type; isolation IsoLJ<0.2; displacement (e lostHits≥1,
  PF-µ trkNumPixelHits≤2).
- **Event selection**: an ordered `event_selection.cutflow` — trigger (OR of the
  four `HLT_DoubleL2Mu2{3,5}NoVtx_2Cha…` paths) → primary-vertex filter
  (`Flag_goodVertices`) → cosmic veto → `object_count` ≥ 2 selected LJs. The
  tools build the cutflow generically from this list, so a different analysis
  adds/removes cuts here without code changes.
- **Observables are config-defined and object-generic** (`observables:` list),
  built by one engine over any object collection — `leptonjets`, or the stamped
  constituents (`electron`/`photon`/`pfmuon`/`dsamuon`) and `jet`. SIDM's χχ̄
  bound state is `ljlj_mass` = invariant mass of the 2 leading selected LJs
  (per channel). Add any quantity in config alone — e.g. the invariant mass or
  Delta R of the leading `pfmuon` and leading `jet` (see the commented examples
  in `configs/sidm.yaml`). Functions: `invariant_mass`, `delta_r`, `delta_phi`,
  `pt`, `eta`, `phi`, `mass`, `sum_pt`, `count`; `rank` is 1-based by pT.
  Channels: 4µ=[mu,mu], 2µ2e=[eg,mu].
- **Gen (optional)**: `resonance_pdgid: 32` gives the truth-level dark-photon
  kinematics (Sec. 3 cross-check: pT/η/Δφ/Lxy, di-lepton ΔR grouped per dark
  photon). Not required for the LJ-LJ mass; leave `gen` out if you don't want it.

## Decode the sample name

`CutDecayFalse_SIDM_BsTo2DpTo{4Mu,2Mu2E}_MBs-<GeV>_MDp-<GeV>_ctau-<mm>_...root`

| Token | Meaning | Check |
|-------|---------|-------|
| `4Mu` / `2Mu2E` | final state → channel | yields land in that channel |
| `MBs-<GeV>` | χχ̄ bound-state mass | the LJ-LJ mass peak |
| `MDp-<GeV>` | dark-photon mass | smaller ⇒ more collimated leptons (smaller ΔR) |
| `ctau-<mm>` | Zd proper decay length | lab Lxy (Table 5): 0.4 mm ↔ ~30 cm |

## Overriding cuts (inline, no file edit)

Pass `overrides` (deep-merged onto the config) for scans, e.g.
`overrides: {leptonjet: {pt_min: 40}}` or `{leptonjet: {isolation: {max: 0.1}}}`
or `{clustering: {radius: 0.3}}`. To change the dark-photon id:
`overrides: {gen: {resonance_pdgid: <id>}}`. Fix a branch name the inspector
flagged: `overrides: {constituents: [...]}` (or edit your own copy of the YAML).

## Writing a config for a different analysis

`configs/sidm.yaml` is the worked example — copy it. The schema (see
`analysis_config.py`): `constituents` (each with an `id` recipe: bool / min /
cutbased / photon_vid_relaxed), `crossclean`, `clustering`, `leptonjet`
(categories + isolation + displacement), `event_selection` (triggers, pv,
channels, observable), `gen` (resonance_pdgid). No code changes needed for a new
lepton-jet-style search.

Recipes: `references/recipes.md`. Gotchas: `references/pitfalls.md`. Tool
reference: `tools/analysis/README_leptonjets.md`.
