# Pitfalls catalog (sidm)

Failure modes when reproducing AN-23-107 from LLPNanoAOD, and their fixes.
Most are surfaced by the tools themselves (in `sidm_readiness` or the `warnings`
list) rather than as exceptions — read those first.

## P1. Empty selection / everything zeroes out

**Symptom:** `SIDMLeptonJetTool` runs but `two_leptonjets` ≈ 0, or a collection
comes back empty.

**Cause:** a branch name in the config doesn't match this production, so a
selection silently sees no candidates.

**Fix:** run `SIDMInspectFileTool` first. If `sidm_readiness.ready` is false,
each entry in `missing` gives the branch and the override key. Pass them, e.g.
`field_overrides={"objects": {"pfmuon_id_branch": "Muon_..."}}`. The tools also
emit a `warnings` list naming any collection they treated as empty.

## P2. LJ-LJ mass peak sits below MBs

**Symptom:** the reconstructed bound-state peak is, say, 420 GeV for an
`MBs-500` sample.

**Cause:** a constituent was dropped so an LJ 4-vector is incomplete — usually
the ΔR(e,γ)<0.025 photon-overlap veto firing on an **ultra-collimated** Zd→ee
(tiny MDp, huge boost), or a sub-leading leg below threshold. This is the
reconstruction behaving correctly, not a bug.

**Check:** compare with a heavier-MDp point (wider opening angle). If you must
keep those photons, relax `objects.photon_electron_dr_veto` — but understand
you are then re-counting energy the veto exists to remove.

## P3. Gen tool: "conflicting azimuthal coordinate representations"

**Symptom:** `SIDMGenKinematicsTool` errors on `GenParticleArray`.

**Cause:** LLPNanoAOD GenPart carries **both** px/py/pz and pt/eta/phi; coffea's
GenParticle Lorentz-vector behaviour can't pick a coordinate system.

**Fix:** already handled — `build_llpnano_schema` drops the vector behaviour for
GenPart (we read raw fields). If a *different* collection trips this, add it to
the schema's dropped-mixins the same way.

## P4. coffea read errors

- **"missing temporal coordinate: need t/E/e/energy or …/mass"** — a collection
  was given a Lorentz-vector behaviour but has no `mass` (e.g. DSA muons). The
  schema keeps DSA collections generic for this reason; don't re-add a vector
  mixin for them.
- **"missing event ID fields ['run','luminosityBlock','event']"** — the schema
  sets `error_missing_event_ids = False`, so real files are fine; only skimmed
  files without these branches would warn.
- **"lzma data error" / eager read fails** — read with the default
  `NanoEventsFactory.from_root(..., schemaclass=...)` (lazy), not
  `mode="eager"`; the tool already prefers the lazy call and only touches the
  branches it needs.

## P5. A displacement cut looks like it isn't applied

**Symptom:** `warnings` contains "electron lostHits branch missing; displacement
cut skipped" (or the PF-muon pixelHits equivalent).

**Cause:** the branch isn't in the file, so rather than reject every LJ, the
tool skips that one cut and tells you.

**Fix:** point the config at the right branch
(`leptonjets.electron_lost_hits_branch` / `pfmuon_pixel_hits_branch`) if your
production stores it under another name.

## P6. Photon ID looks too tight/loose

The default `objects.photon_id_mode = "relaxed_bitmap"` decodes
`Photon_vidNestedWPBitmap` and requires H/E + charged/neutral iso while dropping
σ_iηiη and photon iso (Sec. 4.1.2). If the bitmap branch is absent it falls back
to `Photon_cutBased ≥ 1` **with a warning** (which keeps the very cuts the
analysis drops). Prefer `"relaxed_bitmap"`; use `"cutbased_loose"` only knowingly.

## P7. Gen tool finds no dark photons

**Symptom:** `stats.dp_pt.n == 0`.

**Cause:** this sample uses a different PDG id for the dark photon.

**Fix:** `field_overrides={"fields": {"dark_photon_pdgid": <id>}}`. Default is 32
(the `Dp` in the CMS SIDM samples). Inspect `GenPart_pdgId` values to find it.

## P8. Primary-vertex numbers look off

The PV filter uses the standard CMS goodVertices values (|z|<24 cm, ρ<2 cm) via
`Flag_goodVertices` when present. The note prints "24 mm / 2 mm"; treat that as a
units typo unless you deliberately want millimetres
(`events.pv_absz_max` / `events.pv_rho_max`).
