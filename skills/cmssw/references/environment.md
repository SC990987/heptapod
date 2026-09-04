# The CMSSW environment

Verified on cmslpc with `CMSSW_17_0_0_pre4`, `el9_amd64_gcc13`.

## Entering

```bash
source /cvmfs/cms.cern.ch/cmsset_default.sh
cd /path/to/CMSSW_X_Y_Z/src
cmsenv
```

`cmsenv` is a shell alias for `eval $(scram runtime -sh)`. It must be run from
inside a release area (`.../CMSSW_X_Y_Z/src` or below). It sets `CMSSW_BASE`,
`SCRAM_ARCH`, `PATH`, `PYTHONPATH`, `LD_LIBRARY_PATH` and `ROOTSYS`.

## Making a release area

```bash
source /cvmfs/cms.cern.ch/cmsset_default.sh
export SCRAM_ARCH=el9_amd64_gcc13
scram list CMSSW                       # what is available for this arch
cmsrel CMSSW_17_0_0_pre4               # alias for: scram project CMSSW ...
cd CMSSW_17_0_0_pre4/src && cmsenv
git cms-init                           # only if you will modify CMSSW packages
```

Build after changing anything under `src/`:

```bash
scram b -j 8            # compile
scram b clean           # start over
scram b showlibs        # what a package links against
```

`scram b` walks everything under `src/`. A large unrelated directory there (a
Python virtualenv, a data dump) slows every build — keep those outside `src/`.

## Versions in this release

| Component | Version |
|-----------|---------|
| Python | 3.12.4 |
| ROOT | 6.36.13 |
| awkward | 2.9.0 |
| uproot | 5.7.1 |
| correctionlib | 2.7.0 |
| fastjet | present (bindings, no `__version__`) |
| coffea | **not shipped** |

So inside `cmsenv` you already have uproot, awkward, correctionlib and fastjet
— but not coffea. And a coffea virtualenv has none of ROOT, FWLite or CMSSW.

## Keeping CMSSW and coffea apart

They are two separate Python installations. Verified:

```
coffea venv (3.12.14):  import ROOT    -> ModuleNotFoundError
cmsenv      (3.12.4):   import coffea  -> ModuleNotFoundError
```

Both being Python 3.12 makes it tempting to mix them. Do not — `PYTHONPATH` and
`LD_LIBRARY_PATH` from `cmsenv` will shadow the venv's packages and produce
import errors or, worse, silently loaded wrong libraries.

**Use two terminals**, one per environment. When one job genuinely needs both,
run the CMSSW half as a subprocess:

```bash
bash -lc 'source /cvmfs/cms.cern.ch/cmsset_default.sh && cd $CMSSW/src && \
          eval $(scram runtime -sh) && python3 make_nano.py'
```

Note the version skew if you compare results across the two: awkward 2.9 vs
2.13, correctionlib 2.7 vs 2.9.

## Grid proxy

Anything reading remote data — `dasgoclient`, `root://` URLs, CRAB — needs a
valid proxy:

```bash
voms-proxy-init -voms cms -valid 192:00
voms-proxy-info                  # subject, issuer, timeleft
```

`voms-proxy-info` printing a subject does **not** mean the proxy is valid; an
expired one still shows. The symptom is a command failing with
`failed to parse X509 proxy: Certificate is expired`. Re-run
`voms-proxy-init`.

## Common environment failures

**`SCRAM_ARCH` mismatch.** A release built for one arch will not run under
another. `cat CMSSW_X_Y_Z/.SCRAM/` shows which arch the area was made with.

**`cmsenv` from the wrong directory.** It only works inside a release area;
from elsewhere you get "not a CMSSW area".

**cvmfs not mounted.** `ls /cvmfs/cms.cern.ch` should list content. If it is
empty or hangs, nothing else here will work.

**Stale build.** After changing a header, `scram b` sometimes needs
`scram b clean` to pick it up.
