"""
# _fragment.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

The generator fragment of a production: the python file cmsDriver.py reads the event
generation from.

It is either written here from Pythia 8 settings (the process, decays, phase-space
cuts), with the CMS common settings and the CP5 tune of the run period, and optionally
generator filters; or given whole (any generator a CMS fragment can configure: Pythia
8, Pythia 8 with EvtGen, an ExternalLHEProducer reading a MadGraph or Powheg gridpack,
particle guns); or taken from the official McM request whose fragment it is.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import List, Optional

MCM_FRAGMENT_URL = "https://cms-pdmv-prod.web.cern.ch/mcm/public/restapi/requests/get_fragment/"
_PREPID = re.compile(r"[A-Z0-9]+-[A-Za-z0-9]+-\d{5}")
_SETTING = re.compile(r"[A-Za-z0-9_:()\[\]\-+.]+(\s*=\s*.+)?")


class FragmentError(ValueError):
    """A fragment that cannot be used; the message says why."""

    def __init__(self, message: str, suggestion: str = ""):
        super().__init__(message)
        self.suggestion = suggestion


def render_pythia8(settings: List[str], com_energy: float, tune_import: str,
                   filters: Optional[str] = None) -> str:
    """A Pythia 8 fragment from a list of settings ("Key = value" or "Key:sub = value").

    ``filters`` is python that defines generator filters after ``generator`` and ends
    by defining ``ProductionFilterSequence`` (``cms.Sequence(generator * myfilter)``).
    """
    if not isinstance(settings, list) or not settings:
        raise FragmentError("pythia8_settings must be a non-empty list of strings",
                            'For example ["HardQCD:all = on", "PhaseSpace:pTHatMin = 50."]')
    lines = []
    for setting in settings:
        if not isinstance(setting, str) or not setting.strip() or "\n" in setting \
                or not setting.isprintable():
            raise FragmentError(f"{setting!r} is not a Pythia 8 setting: one printable line each")
        if not _SETTING.fullmatch(setting.strip()):
            raise FragmentError(f"{setting!r} does not look like a Pythia 8 setting",
                                'Write each as "Key = value", e.g. "443:onMode = off"')
        lines.append(f"            {json.dumps(setting.strip())},")
    tail = "ProductionFilterSequence = cms.Sequence(generator)\n"
    if filters:
        if not isinstance(filters, str):
            raise FragmentError("filters must be python code, as text")
        if "ProductionFilterSequence" not in filters:
            raise FragmentError("filters must define ProductionFilterSequence",
                                "End with e.g. ProductionFilterSequence = "
                                "cms.Sequence(generator * mufilter)")
        tail = filters.strip() + "\n"
    text = f'''import FWCore.ParameterSet.Config as cms
from Configuration.Generator.Pythia8CommonSettings_cfi import *
from {tune_import} import *

generator = cms.EDFilter("Pythia8ConcurrentGeneratorFilter",
    pythiaPylistVerbosity = cms.untracked.int32(0),
    pythiaHepMCVerbosity = cms.untracked.bool(False),
    comEnergy = cms.double({float(com_energy)!r}),
    maxEventsToPrint = cms.untracked.int32(0),
    PythiaParameters = cms.PSet(
        pythia8CommonSettingsBlock,
        pythia8CP5SettingsBlock,
        processParameters = cms.vstring(
{chr(10).join(lines)}
        ),
        parameterSets = cms.vstring("pythia8CommonSettings", "pythia8CP5Settings",
                                    "processParameters"),
    ),
)

{tail}'''
    check(text)
    return text


def check(text: str) -> None:
    """Refuse a fragment that is not python or defines no generator."""
    if not isinstance(text, str) or not text.strip():
        raise FragmentError("the fragment is empty")
    try:
        compile(text, "fragment.py", "exec")
    except SyntaxError as exc:
        raise FragmentError(f"the fragment is not valid python: line {exc.lineno}: {exc.msg}") from None
    if not re.search(r"^generator\s*=", text, re.M):
        raise FragmentError("the fragment defines no `generator`",
                            "A CMS fragment defines `generator = cms.EDFilter(...)` (or an "
                            "EDProducer for a particle gun)")


def needs_lhe(text: str) -> bool:
    """Whether the fragment reads events from a gridpack, so the chain starts with LHE."""
    return "ExternalLHEProducer" in text


def from_mcm(prepid: str, timeout: int = 60) -> str:
    """The fragment of an official McM request, from McM's public API."""
    if not isinstance(prepid, str) or not _PREPID.fullmatch(prepid):
        raise FragmentError(f"{prepid!r} is not a McM request id",
                            "Request ids look like BPH-Run3Summer23GS-00002")
    request = urllib.request.Request(MCM_FRAGMENT_URL + prepid,
                                     headers={"Accept": "*/*", "User-Agent": "heptapod-mcgen"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            text = reply.read().decode("utf8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise FragmentError(f"could not reach McM for {prepid}: {exc}",
                            "Check the network, or give the fragment itself") from None
    if text.strip().startswith('"'):        # McM can answer with the fragment as a JSON string
        try:
            text = json.loads(text.strip())
        except ValueError:
            pass
    if text.lstrip().startswith("{"):
        try:
            message = json.loads(text).get("message", text)
        except ValueError:
            message = text
        raise FragmentError(f"McM has no fragment for {prepid}: {str(message)[:200]}")
    check(text)
    return text
