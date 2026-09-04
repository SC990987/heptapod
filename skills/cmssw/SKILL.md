---
name: cmssw
bundle: cmssw
description: Use CMSSW directly for the things coffea cannot do — reading RAW/AOD/MiniAOD with FWLite, inspecting EDM files (edmDumpEventContent, edmFileUtil, edmProvDump), producing or extending NanoAOD with cmsRun and cmsDriver.py (including adding custom collections and variables), conditions and global tags, dataset discovery with dasgoclient, and the ROOT/CMSSW kinematics helpers (reco::deltaR, ROOT.Math.VectorUtil, PtEtaPhiMVector). Use whenever the user mentions CMSSW, cmsenv, scram, cmsRun, cmsDriver, MiniAOD, AOD, RAW, EDM, FWLite, edmDumpEventContent, global tag, conditions, DAS, dasgoclient, PAT, a custom or private NanoAOD, or a branch/collection that is missing from their NanoAOD file.
---

# Using CMSSW

**Write and run CMSSW code and commands. Do not ask for a tool to be built for
each operation.** CMSSW is a complete framework; this skill says what it
provides, how to enter it, and where the boundary with coffea lies.

## The one-sentence division of labour

**CMSSW produces and inspects CMS data; coffea analyses NanoAOD.**

If a branch is missing from a NanoAOD file, no amount of coffea will conjure
it — the file's content was decided by the CMSSW job that produced it. That is
the single most common reason to come here.

| Question | Where |
|----------|-------|
| "Why doesn't my file have `Jet_muonIdx1`?" | CMSSW — NanoAOD content is set at production |
| "Add a variable / collection to my NanoAOD" | CMSSW — `references/custom_nanoaod.md` |
| "Read a MiniAOD/AOD/RAW file" | CMSSW — coffea cannot; `references/edm_files.md` |
| "What's actually in this EDM file?" | CMSSW — `edmDumpEventContent` |
| "Which global tag / what conditions?" | CMSSW — `edmProvDump`, `conddb` |
| "Find the dataset / its files" | CMSSW — `dasgoclient` |
| "Cutflow, histograms, Delta R over NanoAOD" | coffea — use the `coffea` skill |

`references/vs_coffea.md` has the full comparison, including the functions that
exist in **both** and which to prefer.

## Entering the environment

```bash
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd /path/to/CMSSW_X_Y_Z/src
cmsenv                       # or: eval $(scram runtime -sh)
```

Verify before doing anything else:

```bash
echo $CMSSW_BASE $SCRAM_ARCH
python3 --version            # CMSSW_17_0_0_pre4 -> 3.12.4
root-config --version        # -> 6.36.13
```

**Never mix `cmsenv` with a coffea virtualenv in one shell.** They are separate
Pythons with separate `PYTHONPATH`/`LD_LIBRARY_PATH`, and neither can see the
other's packages. Use two terminals, or run one as a subprocess of the other.
Details and what each environment does ship: `references/environment.md`.

## The four things worth knowing first

1. **Data format tiers.** RAW → AOD → MiniAOD → NanoAOD, each smaller and more
   processed. coffea reads only the last (a flat TTree). Everything above needs
   CMSSW's C++ dictionaries.
2. **`edmDumpEventContent file.root`** tells you what is in an EDM file —
   type, module, label, process. This is the CMSSW equivalent of listing
   branches, and it is the first command to run on an unfamiliar file.
3. **`cmsRun config.py`** runs a job; `cmsDriver.py` generates the config.
   Producing a custom NanoAOD means writing a customisation function and
   passing it to `cmsDriver.py --customise`.
4. **`Var("expression", type, doc=...)`** is how a NanoAOD branch is declared.
   The expression is a C++ method call on the object. Adding one line there
   makes a new branch appear, which coffea then sees automatically.

## How to work

Check what you have before writing code: `edmDumpEventContent` on the file,
`edmProvDump` for how it was made, `python3 -c "import ..."` to confirm a
module exists. CMSSW error messages are long; the useful line is usually the
first `----- Begin Fatal Exception` block, not the stack trace.

A grid proxy is required for anything touching remote data (`dasgoclient`,
`root://` files):

```bash
voms-proxy-init -voms cms -valid 192:00
voms-proxy-info                      # check it has not expired
```

## Reference files

| File | Read it when |
|------|--------------|
| `references/environment.md` | Setting up, version questions, coexisting with coffea |
| `references/edm_files.md` | Inspecting EDM files, reading them with FWLite |
| `references/vs_coffea.md` | Deciding which framework to use for a given task |
| `references/custom_nanoaod.md` | Adding a variable or collection to NanoAOD |
