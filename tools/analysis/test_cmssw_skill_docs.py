"""
# test_cmssw_skill_docs.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Check the `cmssw` skill against a real CMSSW release.

The skill claims specific commands exist, specific modules import, and specific
APIs work. Those claims are checked here by running them inside `cmsenv` as a
subprocess -- the test suite itself runs in the coffea virtualenv, which by
design cannot see CMSSW at all.

Everything is skipped when no CMSSW release is reachable, so the suite still
passes off-site. Set CMSSW_TEST_BASE to point at a release explicitly.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILL_DIR = os.path.join(REPO, "skills", "cmssw")
CMSSET = "/cvmfs/cms.cern.ch/cmsset_default.sh"


def find_release():
    """A CMSSW release area to test against, or None."""
    explicit = os.environ.get("CMSSW_TEST_BASE")
    if explicit and os.path.isdir(os.path.join(explicit, "src")):
        return explicit
    # the repo commonly lives inside one: .../CMSSW_X_Y_Z/src/heptapod
    here = REPO
    for _ in range(4):
        here = os.path.dirname(here)
        if os.path.basename(here).startswith("CMSSW_") and \
                os.path.isdir(os.path.join(here, "src")):
            return here
    return None


RELEASE = find_release()
needs_cmssw = pytest.mark.skipif(
    not (os.path.exists(CMSSET) and RELEASE),
    reason="no CMSSW release / cvmfs available")


def in_cmsenv(script: str, timeout: int = 300):
    """Run a Python snippet inside cmsenv; return (rc, stdout, stderr)."""
    cmd = (f"source {CMSSET} >/dev/null 2>&1 && cd {RELEASE}/src && "
           f"eval $(scram runtime -sh) && python3 -")
    p = subprocess.run(["bash", "-lc", cmd], input=script, text=True,
                       capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def shell_in_cmsenv(command: str, timeout: int = 300):
    cmd = (f"source {CMSSET} >/dev/null 2>&1 && cd {RELEASE}/src && "
           f"eval $(scram runtime -sh) && {command}")
    p = subprocess.run(["bash", "-lc", cmd], text=True, capture_output=True,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr


# ===================================================================== #
# ===================== structure (always runs) ======================= #
# ===================================================================== #

def test_skill_frontmatter_is_valid():
    yaml = pytest.importorskip("yaml")
    text = open(os.path.join(SKILL_DIR, "SKILL.md")).read()
    assert text.startswith("---")
    meta = yaml.safe_load(text.split("---")[1])
    assert meta["name"] == "cmssw"
    assert meta["bundle"] == "cmssw"
    assert len(meta["description"]) > 200


def test_reference_files_exist():
    for name in ("environment.md", "edm_files.md", "vs_coffea.md",
                 "custom_nanoaod.md"):
        p = os.path.join(SKILL_DIR, "references", name)
        assert os.path.exists(p), f"missing reference: {name}"
        assert os.path.getsize(p) > 1000, f"too small to be useful: {name}"


def test_bundle_is_declared_with_no_tools():
    yaml = pytest.importorskip("yaml")
    d = yaml.safe_load(open(os.path.join(REPO, "toolkit.yaml")))
    assert "cmssw" in d["bundles"], "cmssw bundle must exist for the skill"
    assert not [t for t in d["tools"] if t.get("bundle") == "cmssw"], \
        "the cmssw bundle is skill-only by design"


# ===================================================================== #
# ================== claims checked against CMSSW ===================== #
# ===================================================================== #

@needs_cmssw
def test_environment_reports_the_documented_versions():
    rc, out, err = in_cmsenv(
        "import sys, ROOT\n"
        "print('PY', sys.version.split()[0])\n"
        "print('ROOT', ROOT.gROOT.GetVersion())\n")
    assert rc == 0, err[-2000:]
    assert "PY 3.12" in out, f"skill documents Python 3.12: {out}"
    assert "ROOT 6." in out, out


@needs_cmssw
def test_kinematics_helpers_agree():
    """reco::deltaR and ROOT.Math.VectorUtil.DeltaR must give the same value,
    as vs_coffea.md claims."""
    rc, out, err = in_cmsenv(
        'import ROOT\n'
        'ROOT.gROOT.SetBatch(True)\n'
        'ROOT.gInterpreter.Declare(\'#include "DataFormats/Math/interface/deltaR.h"\')\n'
        'a = ROOT.Math.PtEtaPhiMVector(10, 0.5, 0.3, 0.105)\n'
        'b = ROOT.Math.PtEtaPhiMVector(8, -0.2, 2.9, 0.105)\n'
        'print("RECO", round(ROOT.reco.deltaR(0.5, 0.3, -0.2, 2.9), 6))\n'
        'print("VUTIL", round(ROOT.Math.VectorUtil.DeltaR(a, b), 6))\n'
        'print("MASS", round((a + b).M(), 4))\n'
        'print("WRAP", round(ROOT.TVector2.Phi_mpi_pi(7.0), 6))\n')
    assert rc == 0, err[-2000:]
    vals = dict(l.split()[:2] for l in out.strip().splitlines() if " " in l)
    assert vals["RECO"] == vals["VUTIL"], f"skill claims these agree: {vals}"


@needs_cmssw
def test_fwlite_imports():
    rc, out, err = in_cmsenv(
        "import ROOT; ROOT.gROOT.SetBatch(True)\n"
        "from DataFormats.FWLite import Events, Handle, Runs, Lumis\n"
        "print('OK', Handle('std::vector<reco::GenParticle>').__class__.__name__)\n")
    assert rc == 0, err[-2000:]
    assert "OK Handle" in out


@needs_cmssw
def test_nanoaod_customisation_api_exists():
    """custom_nanoaod.md tells the agent to use these names."""
    rc, out, err = in_cmsenv(
        "from PhysicsTools.NanoAOD.common_cff import Var, CandVars, P4Vars, ExtVar\n"
        "from PhysicsTools.NanoAOD.simpleCandidateFlatTableProducer_cfi import "
        "simpleCandidateFlatTableProducer as P\n"
        "import PhysicsTools.NanoAOD.muons_cff as mu\n"
        "print('TYPE', P.type_())\n"
        "print('MUONTABLE', mu.muonTable.name.value())\n"
        "print('VAR', type(Var('pt', float, doc='d', precision=6)).__name__)\n")
    assert rc == 0, err[-2000:]
    assert "TYPE SimpleCandidateFlatTableProducer" in out, out
    assert "MUONTABLE Muon" in out, out
    assert "VAR PSet" in out, out


@needs_cmssw
def test_documented_executables_are_on_path():
    """Every command the skill tells the agent to run must exist."""
    text = "\n".join(
        open(os.path.join(SKILL_DIR, "references", n)).read()
        for n in ("environment.md", "edm_files.md", "custom_nanoaod.md"))
    text += open(os.path.join(SKILL_DIR, "SKILL.md")).read()
    documented = {"cmsRun", "cmsDriver.py", "edmDumpEventContent", "edmFileUtil",
                  "edmProvDump", "dasgoclient", "scram", "conddb"}
    referenced = {c for c in documented if c in text}
    assert referenced, "no executables referenced?"
    rc, out, err = shell_in_cmsenv(
        "for c in " + " ".join(sorted(referenced)) + "; do "
        "command -v $c >/dev/null && echo \"HAVE $c\" || echo \"MISSING $c\"; done")
    assert rc == 0, err[-2000:]
    missing = [l.split()[1] for l in out.splitlines() if l.startswith("MISSING")]
    assert not missing, f"skill documents commands that do not exist: {missing}"


@needs_cmssw
def test_cmssw_ships_what_environment_md_claims():
    """environment.md lists uproot/awkward/correctionlib as present and coffea
    as absent -- an agent will rely on that when choosing where to run."""
    rc, out, err = in_cmsenv(
        "for m in ('uproot', 'awkward', 'correctionlib', 'coffea'):\n"
        "    try:\n"
        "        __import__(m); print('HAVE', m)\n"
        "    except ImportError:\n"
        "        print('MISSING', m)\n")
    assert rc == 0, err[-2000:]
    assert "HAVE uproot" in out and "HAVE awkward" in out
    assert "HAVE correctionlib" in out
    assert "MISSING coffea" in out, \
        "environment.md says coffea is not shipped with CMSSW"
