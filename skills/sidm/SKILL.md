---
name: sidm
bundle: sidm
description: Reproduce CMS AN-23-107 (Self-Interacting Dark Matter search with two Lepton Jets) results from an LLPNanoAOD ROOT file. Use whenever the user mentions SIDM, Lepton Jets (LJs), dark photons / Zd / "Dp", displaced or DSA muons, LLPNanoAOD, the BsTo2DpTo2Mu2e / BsTo2DpTo4Mu signal samples, the LJ-LJ or bound-state mass, or the SIDMInspectFileTool / SIDMLeptonJetTool / SIDMEventSelectionTool / SIDMGenKinematicsTool tools. Also use proactively before running any of these on a new file — always inspect first, and several selection choices (photon ID, PF-DSA cross-clean, primary-vertex cuts) are judgment calls that need surfacing.
---

# SIDM two-Lepton-Jet analysis: from LLPNanoAOD to the bound-state peak

Reproduces the reconstruction, selection, and gen-level kinematics of the CMS
SIDM search (AN-23-107) on **LLPNanoAOD** ROOT files, using coffea `NanoEvents`
to read and the fastjet+awkward `ClusterSequence` to cluster. The signal is a
χχ̄ bound state (`Bs`) → two dark photons (`Dp`, PDG id 32) → displaced,
collimated lepton pairs reconstructed as Lepton Jets; the χχ̄ mass is the
invariant mass of the two leading LJs.

```
file.root ──[SIDMInspectFileTool]──▶ readiness check (fix branch names here)
          ──[SIDMLeptonJetTool]────▶ leptonjets JSONL (per-event LJs + flags)
                                        └─[SIDMEventSelectionTool]─▶ cutflow + 4µ/2µ2e + LJ-LJ mass
          ──[SIDMGenKinematicsTool]─▶ Figs 2-6 (dark-photon pT/η/Δφ/Lxy, dilepton ΔR)
```

## Three rules that cover most first-run failures

### 1. Inspect first; keep the ROOT file under `base_directory`

Always run `SIDMInspectFileTool` on a new file before anything else. It prints a
`sidm_readiness` block — if `ready` is false, it lists each missing branch **and
the config key to fix it**; pass those through `field_overrides` to the other
tools. All tool paths are relative to `base_directory` (set via
`tb config set heptapod base_directory <dir>`), so the `.root` must live there.

### 2. The chain is file-based through JSONL — don't skip a stage

`SIDMLeptonJetTool` writes a `leptonjets` JSONL (one line per event, each LJ
carrying its 4-vector, type, constituent counts, isolation, and a pass flag for
every cut). `SIDMEventSelectionTool` **consumes that JSONL** to build the
cutflow and the LJ-LJ mass. Run reconstruction, then selection on its output.
`SIDMGenKinematicsTool` is independent (reads GenPart directly).

### 3. Sanity-check results against the filename

Sample names encode the truth (see decode table below): the LJ-LJ mass peak
should sit at `MBs`, and the mean dark-photon Lxy should match the `ctau`↔lab
mapping. If the peak is far from `MBs`, suspect a dropped constituent (e.g. the
ΔR(e,γ)<0.025 photon veto on an ultra-collimated pair) or a wrong override —
not a bug in the tool.

## Decode the sample name before trusting a number

`CutDecayFalse_SIDM_BsTo2DpTo{4Mu,2Mu2E}_MBs-<GeV>_MDp-<GeV>_ctau-<mm>_...root`

| Token | Meaning | Use it to check |
|-------|---------|-----------------|
| `4Mu` / `2Mu2E` | final state → channel | expect yields in the 4µ or 2µ2e channel (not both) |
| `MBs-<GeV>` | χχ̄ bound-state mass | the LJ-LJ mass peak should land here |
| `MDp-<GeV>` | dark-photon mass | smaller ⇒ more collimated leptons (smaller di-lepton ΔR) |
| `ctau-<mm>` | Zd proper decay length | maps to a lab Lxy (Table 5): 0.4 mm ↔ ~30 cm for these points |

## Override quick-reference (`field_overrides`, nested by section)

| Want to… | Override |
|----------|----------|
| change an LJ cut | `{"leptonjets": {"lj_pt_min": 40, "iso_max": 0.15, "jet_radius": 0.3}}` |
| scan pT threshold / cone (Sec. 4.2.1) | loop `lj_pt_min` ∈ 30/40/50/60 or `jet_radius` ∈ 0.1–0.4 |
| change an object threshold | `{"objects": {"pfmuon_pt_min": 3.0}}` |
| different dark-photon id | `{"fields": {"dark_photon_pdgid": <id>}}` (default 32) |
| fix a branch name from inspect | `{"fields": {"dsamuon_coll": "..."}}`, `{"objects": {"pfmuon_id_branch": "..."}}` |
| stricter PF-DSA cleaning | `{"crossclean": {"method": "segment"}}` (default `"dr"`) |
| built-in clustering (no fastjet) | `{"leptonjets": {"cluster_backend": "builtin"}}` |

`max_events=N` on the reconstruction/gen tools caps events for quick iteration.

## Common asks → what to run

- "Is this file ready?" → `SIDMInspectFileTool`.
- "Reconstruct LJs and give me the cutflow / channel split" → `SIDMLeptonJetTool`
  then `SIDMEventSelectionTool`.
- "Plot the LJ-LJ / bound-state mass" → run the chain, then plot the saved
  `*_ljlj_mass_{all,4mu,2mu2e}.npy`.
- "pT-threshold / R scan" → loop the reconstruction with the override, compare
  the mass distributions. See `references/recipes.md`.
- "Dark-photon pT / Δφ / Lxy / di-lepton ΔR plots" → `SIDMGenKinematicsTool`.
- "Compare mass points" → run the chain per file, overlay the mass arrays.

Copy-paste recipes: `references/recipes.md`. Gotchas and their fixes:
`references/pitfalls.md`. Full reference: `tools/analysis/README_SIDM.md`.

## What this skill does NOT cover

Background estimation (the data-driven method is WIP in the note), systematic
uncertainties, MC scale-factor corrections (pileup / lepton / trigger), and the
statistical interpretation / limit setting. This is reconstruction, selection,
and kinematics — not the final limits.
