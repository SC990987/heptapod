# Lepton-Jet analysis tools (config-driven)

A generic, columnar Lepton-Jet analysis toolkit for **(LLP)NanoAOD** ROOT files.
The reconstruction *method* is fixed and reusable; everything analysis-specific
comes from a **config** (a YAML file or an inline dict). Reads with **coffea**
`NanoEvents`; clusters with the **fastjet + awkward** `ClusterSequence` (with a
pure-python anti-kT reference/fallback).

A worked config for the CMS SIDM search (AN-23-107) ships as
`configs/sidm.yaml`; the `sidm` skill documents it.

## Tools

| Tool | Module | Purpose |
|------|--------|---------|
| `InspectFileTool` | `nanoaod_inspect.py` | List trees/collections/branches and **validate a config against the file** (missing collections/branches + the config path to fix each). Run first. |
| `LeptonJetTool` | `leptonjets.py` | Config-driven object selection + cross-cleaning → anti-kT clustering → categorization → isolation & displacement → per-event `leptonjets` JSONL + trigger/PV/cosmic flags + channel. |
| `GenDecayLengthTool` | `gen_decay_length.py` | Transverse decay length Lxy of the config's `gen.resonance_pdgid`, measured production-vertex-to-decay-vertex. |

Everything downstream of reconstruction — the cutflow, the LJ-LJ mass, gen
pT/η/Δφ and the di-lepton ΔR — is coffea rather than a tool; `LeptonJetTool`
writes a NanoAOD-style file so its output is `events.LeptonJet`. See the
`coffea` and `sidm` skills. The former `EventSelectionTool` and
`GenKinematicsTool` live in the `legacy` bundle (`tb activate heptapod/legacy`).

Every tool takes `config` (YAML path relative to `base_directory`, or an inline
dict) and optional inline `overrides` (deep-merged last). The old `SIDM*` class
names remain as aliases.

## Quickstart

```python
import json
from tools.analysis.leptonjets import LeptonJetTool
from coffea.analysis_tools import PackedSelection

BASE = "/path/to/workspace"; CFG = "configs/sidm.yaml"; ROOT = "signal.root"
LeptonJetTool(base_directory=BASE, root_path=ROOT, config=CFG, output_path="lj.jsonl").run()
# cutflow + LJ-LJ mass: see the sidm skill's recipes (PackedSelection + (a+b).mass)
```

## Config schema (see `analysis_config.py`)

Deep-merge precedence: built-in neutral **DEFAULTS ← your YAML/dict ← inline `overrides`**.

- `constituents`: list of collections to cluster. Each: `{name, collection,
  is_muon, mass, pt_min, abseta_max, id, veto}`. `id.type` ∈ `bool` |
  `min(field,value,strict)` | `cutbased(field,min)` | `photon_vid_relaxed(field,
  bits_per_cut,required_cuts,min_level,fallback_field,fallback_min)`.
- `crossclean`: `{name, target, reference, method: dr, max_dr, use_outer}`.
- `clustering`: `{algorithm: antikt, radius, backend: fastjet|builtin}`.
- `leptonjet`: `pt_min`, `abseta_max`, `isolation` `{max, matched_jet_dr,
  jet_collection, lepton_fraction_branches}`, `categories` (ordered; each
  `{name, min_muon, final_min_muon}` or `{name, default: true}`), `displacement`
  (`{category, constituent, field, op, value, reduce, applies_if_present}`).
- `event_selection`: `cutflow` — an **ordered list of named cuts** the tool
  applies and tabulates generically. Each cut is `{name, type, ...}` with
  `type` ∈ `trigger` | `flag` | `primary_vertex` | `cosmic_veto` |
  `event_scalar` (event-level filters, evaluated at reconstruction) or
  `object_count` (`≥ min` selected objects, optionally of a `category`). An
  analysis with no PV filter or cosmic veto simply omits those entries. Plus
  `channels` (`{name, categories:[a,b]}`).
- `observables`: **config-defined, object-generic** derived quantities computed
  for events passing the cutflow, built over any object collection
  (`leptonjets`, or the stamped `electron`/`photon`/`pfmuon`/`dsamuon`/`jet`).
  Each `{name, function, inputs:[{object, rank, [category], [selected]}],
  [per_channel]}`; functions: `invariant_mass`, `delta_r`, `delta_phi`, `pt`,
  `eta`, `phi`, `mass`, `sum_pt`, `count` (`rank` 1-based by pT). E.g. the
  invariant mass or ΔR of the leading `pfmuon` and leading `jet` — in config alone.
- `gen`: `resonance_pdgid`, `lepton_pdgids`, `mother_field`, `vertex`.

## Validation

Running `configs/sidm.yaml` on a real 2µ2e signal (`MBs-500`) recovers the
LJ-LJ mass peak at **500 GeV**, the correct 2µ2e channel, and the note's gen
kinematics; fastjet and the built-in anti-kT agree exactly. `test_sidm.py`
covers config load/validate, the ID recipes, selection/cross-clean, clustering,
categorization/isolation/displacement, the cutflow, gen kinematics, the
inspector, and an injected-mass integration + a coffea end-to-end test.

```
python tools/analysis/test_sidm.py
```

## Writing your own analysis

Copy `configs/sidm.yaml` and edit the values — no code changes for a new
lepton-jet-style search. Run `InspectFileTool` with your config to confirm every
referenced branch exists before a full run.
