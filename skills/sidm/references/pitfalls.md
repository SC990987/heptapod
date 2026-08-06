# Pitfalls catalog (sidm)

Failure modes when reproducing AN-23-107 with the config-driven Lepton-Jet
tools, and their fixes. Most are surfaced by the tools (in `config_readiness` or
the `warnings` list) rather than as exceptions — read those first.

## P1. Empty selection / a collection is empty
**Symptom:** `min_leptonjets` ≈ 0, or a constituent comes back empty.
**Cause:** a branch/collection name in the config doesn't match this production.
**Fix:** run `InspectFileTool` with `config: configs/sidm.yaml`. Its
`config_readiness.missing_in_file` lists each missing item with the **config
path** (e.g. `constituents[dsamuon].collection`). Correct it via inline
`overrides` or by editing your copy of the YAML.

## P2. LJ-LJ mass peak below MBs
**Symptom:** peak at ~420 GeV for an `MBs-500` sample.
**Cause:** a constituent was dropped so an LJ 4-vector is incomplete — usually
the ΔR(e,γ)<0.025 photon veto firing on an ultra-collimated Zd→ee, or a
sub-leading leg below threshold. Correct reconstruction, not a bug.
**Check:** compare with a heavier-MDp point. To keep those photons, relax the
`crossclean` rule `photon_electron.max_dr`.

## P3. Config validation / readiness fails
**Symptom:** `InspectFileTool` returns `config_readiness.ready: false` or
`structural_issues`.
**Fix:** `structural_issues` flag config mistakes (missing constituent name,
unknown id.type, crossclean referencing an unknown constituent).
`missing_in_file` flag branches the file lacks. Fix both before running.

## P4. coffea read errors
- **"missing temporal coordinate"** — a collection was given a Lorentz-vector
  behaviour but has no `mass`. The schema keeps DSA/LLP collections generic; we
  build 4-vectors ourselves from pt/eta/phi + the config `mass`.
- **"conflicting azimuthal coordinate"** on GenPart — LLPNanoAOD GenPart carries
  both px/py/pz and pt/eta/phi. Handled: `build_schema` drops GenPart's vector
  behaviour.
- **"missing event ID fields"** — the schema sets `error_missing_event_ids =
  False`, so skimmed files are fine.
- **"lzma data error" / eager read** — the tool reads with the default (lazy)
  `NanoEventsFactory.from_root`, touching only the branches the config needs.

## P5. A displacement cut looks skipped
**Symptom:** `warnings` contains "displacement field '<x>' missing; cut skipped".
**Cause:** that branch isn't in the file, so the tool skips only that cut (rather
than rejecting everything) and tells you.
**Fix:** point the config displacement rule at the right member field.

## P6. Photon ID too tight/loose
The photon `id.type: photon_vid_relaxed` decodes `vidNestedWPBitmap` and requires
the loose per-cut decisions for `required_cuts: [0,1,2,4,5]` (drops σιηιη=3 and
photon-iso=6). If the bitmap is absent it falls back to `cutBased ≥
fallback_min` with a warning. Adjust `required_cuts` to change which cuts apply.

## P7. Gen tool finds no resonances
**Symptom:** `stats.res_pt.n == 0`.
**Fix:** set `gen.resonance_pdgid` to your sample's id (32 for the SIDM dark
photon); inspect `GenPart_pdgId` to confirm.

## P8. Primary-vertex numbers look off
The PV filter is the `pv_filter` entry in `event_selection.cutflow` (CMS
goodVertices values: |z|<24 cm, ρ<2 cm). The note prints "24 mm"; treat as a
units typo unless you mean mm. To change it, edit that cut in your config
(inline `overrides` replace whole lists, so to tweak one value re-supply the
`event_selection.cutflow` list).

## P9. Making the event selection fit a different analysis
The cutflow is fully config-defined — an ordered list of named cuts the tools
apply in order. Cut recipes: `trigger` (OR of HLT paths), `flag` (require a
boolean branch), `primary_vertex`, `cosmic_veto`, `event_scalar` (`{branch, op,
value}`, e.g. a MET cut), and `object_count` (`≥ min` selected LJs, optionally of
a `category`). Drop the cuts you don't have; add the ones you do. The
reconstructed resonance is the `event_selection.observable` (invariant mass of
the N leading selected LJs) — for SIDM that's the LJ-LJ bound-state mass.
