"""
# prepare.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

PrepareMCProductionTool -- write a CMS simulation production for a process and a run period.
"""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.framework._projects import safe_path
from tools.mcgen import _campaigns as campaigns
from tools.mcgen import _fragment as fragments
from tools.mcgen import _production as production

_SHELL_SAFE = re.compile(r"[A-Za-z0-9_./+:=@-]+")


class PrepareMCProductionTool(BaseTool):
    """
    Write a production of CMS simulation: a process, generated and taken through the
    official chain of a run period (GEN-SIM, DIGI to HLT with premixed pileup, RECO,
    MiniAOD, NanoAOD), as far as the tier asked for. Nothing runs yet:
    RunMCProduction runs it, here or on HTCondor, and MCProductionStatus follows it.

    The process is given in exactly one of four ways:
      - pythia8_settings: Pythia 8 settings, one string each ("HardQCD:all = on",
        "PhaseSpace:pTHatMin = 20.", "443:onMode = off", "443:onIfMatch = 13 -13").
        They are written into a fragment with the CMS common settings and the CP5 tune
        at 13.6 TeV. `filters` may add generator filters: python that defines filter
        modules after `generator` and ends with ProductionFilterSequence =
        cms.Sequence(generator * myfilter).
      - fragment: a complete CMS generator fragment, as text: any generator a fragment
        can configure (Pythia 8 with EvtGen, a particle gun, an ExternalLHEProducer
        reading a MadGraph or Powheg gridpack, whose chain then starts with LHE).
      - fragment_file: such a fragment in a file, inside the working directory or
        under /cvmfs (CMSSW's own fragments are in Configuration/Generator/python).
      - mcm_prepid: the fragment of an official McM request (e.g.
        BPH-Run3Summer23GS-00002), to make more of an existing sample.

    `events` are generated events. A generator filter keeps a fraction of them; to
    know it before a big production, prepare a short one with final_tier "GEN" (no
    detector simulation, seconds per thousand events) and run it: MCProductionStatus
    then gives the filter efficiency and the cross section.

    Pileup: "premix" mixes in the run period's premixed-pileup library, as the
    official samples do; it needs a valid grid proxy (DAS is asked for the library,
    and every job reads it over XRootD). "none" simulates the hard process alone.

    Where the files go: `output` is a directory inside the working directory (the
    default is the production's output/), or a root:// url (on EOS: root://cmseos.
    fnal.gov//store/user/<you>/...); jobs on HTCondor can only write to a root:// url.
    Files are named <output>/<TIER>/<name>_<TIER>_<job>.root.

    Args:
        name: Name of the production; it becomes the directory <working dir>/<name>.
        run_period: 2022, 2022EE, 2023, 2023BPix or 2024 (ListMCCampaigns describes them).
        events: Number of events to generate, before any generator filter.
        events_per_job: Events per job (default: all of them for "GEN", else up to 500).
        pythia8_settings, filters, fragment, fragment_file, mcm_prepid: the process (above).
        pileup: "premix" (default) or "none".
        final_tier: The last tier produced: GEN, GEN-SIM, AOD, MINIAOD or NANOAOD (default).
        keep_tiers: The tiers copied to the output (default: the final one).
        output: Directory or root:// url for the outputs.
        threads: Threads per job (1 to 8; default 1).
        seed: Base random seed (default: derived from the name). Every job and step
            gets seeds of its own from it.

    Returns (JSON):
        {"status": "ok", "production", "run_period", "campaign", "final_tier", "pileup",
         "lhe", "events", "jobs": {"n", "events_per_job", "last_job_events"},
         "steps": [{"step", "release", "conditions", "era", "cmsdriver"}], "output",
         "keep_tiers", "needs": [...], "notes": [...], "next_steps": [...]}

    Errors:
        A formatted error when the run period, tier or pileup is unknown, the process is
        not given exactly once or is not a usable fragment, a number is not a positive
        whole number, a path leaves the working directory, or the production exists.
        Nothing is written then.
    """

    # --------------------------- Runtime fields --------------------------- #
    name: str = RuntimeField(description="Name of the production (a directory of that name)")
    run_period: str = RuntimeField(description="Run period: 2022, 2022EE, 2023, 2023BPix or 2024")
    events: int = RuntimeField(description="Number of events to generate, before any generator filter")
    events_per_job: Optional[int] = RuntimeField(
        default=None, description="Events per job (default: all for GEN, else up to 500)")
    pythia8_settings: Optional[List[str]] = RuntimeField(
        default=None, description="Pythia 8 settings, one string each, e.g. 'HardQCD:all = on'")
    filters: Optional[str] = RuntimeField(
        default=None, description="Python defining generator filters and ProductionFilterSequence "
                                  "(with pythia8_settings)")
    fragment: Optional[str] = RuntimeField(default=None, description="A complete CMS generator fragment")
    fragment_file: Optional[str] = RuntimeField(
        default=None, description="A fragment file, inside the working directory or under /cvmfs")
    mcm_prepid: Optional[str] = RuntimeField(
        default=None, description="McM request whose fragment to use, e.g. BPH-Run3Summer23GS-00002")
    pileup: str = RuntimeField(default="premix", description="'premix' (official pileup) or 'none'")
    final_tier: str = RuntimeField(default="NANOAOD",
                                   description="Last tier: GEN, GEN-SIM, AOD, MINIAOD or NANOAOD")
    keep_tiers: Optional[List[str]] = RuntimeField(
        default=None, description="Tiers copied to the output (default: the final one)")
    output: Optional[str] = RuntimeField(
        default=None, description="Output directory inside the working directory, or a root:// url")
    threads: int = RuntimeField(default=1, description="Threads per job (1-8)")
    seed: Optional[int] = RuntimeField(default=None, description="Base random seed")
    # ---------------------------------------------------------------------- #

    # ---------------------------- State fields ---------------------------- #
    base_directory: str = StateField(default=".", description="Base directory for safe paths")
    # ---------------------------------------------------------------------- #

    def _setup(self):
        self.base_directory = os.path.abspath(self.base_directory)
        if not os.path.isdir(self.base_directory):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def _fragment(self, base: str) -> Dict[str, Any]:
        """The fragment text and where it came from, or {"error": ...}."""
        given = {key: value for key, value in (("pythia8_settings", self.pythia8_settings),
                                               ("fragment", self.fragment),
                                               ("fragment_file", self.fragment_file),
                                               ("mcm_prepid", self.mcm_prepid)) if value}
        if len(given) != 1:
            return {"error": self.format_error(
                error="Invalid Request",
                reason=("no process was given" if not given else
                        f"the process is given {len(given)} ways: {', '.join(given)}"),
                suggestion="Give exactly one of pythia8_settings, fragment, fragment_file, mcm_prepid")}
        if self.filters and "pythia8_settings" not in given:
            return {"error": self.format_error(
                error="Invalid Request", reason="filters go with pythia8_settings",
                suggestion="Put the filters into the fragment itself")}
        kind = next(iter(given))
        if kind == "pythia8_settings":
            text = fragments.render_pythia8(self.pythia8_settings, campaigns.COM_ENERGY_GEV,
                                            campaigns.TUNE_IMPORT, self.filters)
            return {"text": text, "source": {"kind": kind}}
        if kind == "fragment":
            fragments.check(self.fragment)
            return {"text": self.fragment, "source": {"kind": kind}}
        if kind == "mcm_prepid":
            return {"text": fragments.from_mcm(self.mcm_prepid),
                    "source": {"kind": kind, "mcm_prepid": self.mcm_prepid}}
        path = self.fragment_file
        full = os.path.abspath(path) if os.path.isabs(path) else safe_path(base, path)
        if full is None or not (full.startswith("/cvmfs/") or safe_path(base, os.path.relpath(full, base))):
            return {"error": self.format_error(
                error="Access Denied", reason="fragment_file is neither inside the working "
                                              "directory nor under /cvmfs",
                context=f"fragment_file={path}", suggestion="Copy the fragment into the working directory")}
        try:
            text = Path(full).read_text()
        except OSError as exc:
            return {"error": self.format_error(error="File Not Readable", reason=str(exc),
                                               context=f"fragment_file={path}")}
        fragments.check(text)
        return {"text": text, "source": {"kind": kind, "file": full}}

    def _run(self) -> str:
        base = self.base_directory
        if not _SHELL_SAFE.fullmatch(base):
            return self.format_error(
                error="Unsuitable Working Directory",
                reason="the production's scripts need a path of letters, digits and _ . / + - only",
                context=f"working directory={base}")
        try:
            got = self._fragment(base)
        except (fragments.FragmentError, campaigns.CampaignError) as exc:
            return self.format_error(error="Invalid Fragment", reason=str(exc),
                                     suggestion=getattr(exc, "suggestion", ""))
        if "error" in got:
            return got["error"]
        directory = safe_path(base, str(self.name or ""), follow_links=True) if self.name else None
        if directory is None or os.path.dirname(directory) != base:
            return self.format_error(error="Invalid Name", reason="name must be a plain directory name",
                                     context=f"name={self.name}")
        output = self.output
        if output and not output.startswith("root://"):
            local = safe_path(base, output, follow_links=True)
            if local is None or not _SHELL_SAFE.fullmatch(local):
                return self.format_error(
                    error="Access Denied", reason="a local output must be inside the working "
                                                  "directory, with a plain path",
                    context=f"output={output}", suggestion="Or give a root:// url")
            output = local
        elif output and not _SHELL_SAFE.fullmatch(output):
            return self.format_error(error="Invalid Output", reason="the url has unusual characters",
                                     context=f"output={output}")
        per_job = self.events_per_job
        if per_job is None and isinstance(self.events, int) and not isinstance(self.events, bool):
            per_job = self.events if self.final_tier == "GEN" else min(self.events, 500)
        try:
            described = production.create(
                Path(directory), name=self.name, run_period=self.run_period, events=self.events,
                events_per_job=per_job, fragment=got["text"], pileup=self.pileup,
                final_tier=self.final_tier, keep_tiers=self.keep_tiers, output=output,
                threads=self.threads, seed=self.seed, source=got["source"])
        except (production.ProductionError, campaigns.CampaignError,
                fragments.FragmentError) as exc:
            return self.format_error(error="Invalid Request", reason=str(exc),
                                     suggestion=getattr(exc, "suggestion", ""))
        return json.dumps(answer(described, base), ensure_ascii=False)


def answer(d: Dict[str, Any], base: str) -> Dict[str, Any]:
    """What PrepareMCProduction reports about a production it wrote."""
    jobs = d["jobs"]
    needs, notes = [], []
    if d["pileup"] == "premix":
        needs.append(f"a valid grid proxy (voms-proxy-init -voms cms): premixed pileup comes from "
                     f"{d['premix_dataset']}, listed through DAS once and read by every job")
    if d["output"].startswith("root://"):
        needs.append("a Kerberos ticket or a grid proxy to write to " + d["output"])
    if d["lhe"]:
        notes.append("the fragment reads a gridpack (ExternalLHEProducer): the chain starts with "
                     f"LHE,GEN,SIM in {d['steps'][0]['release']}, as the campaign's LHE samples do")
    if d["source"].get("kind") != "fragment":
        notes.append("the fragment is in fragment.py in the production: look at it before a long run")
    notes.append("events are generated events; a generator filter keeps only a fraction, which "
                 "MCProductionStatus reports once a job has run")
    if d["pileup"] == "none":
        notes.append("no pileup: the events contain the hard process alone, unlike the official samples")
    if d.get("premix_files"):
        notes.append(f"premixed pileup is mixed from {d['premix_files']} files of the library, drawn at "
                     "random with the production's seed when it is configured "
                     "(cfg/premix_files.txt): the whole library would make a configuration of tens of "
                     "MB for every job")
    steps = [{"step": s["name"], "release": s["release"], "conditions": s["options"]["conditions"],
              "era": s["options"]["era"],
              "cmsdriver": " ".join(s["cmsdriver"]).replace("@CFG@/", "cfg/").replace(
                  "@PREMIX@", "filelist:cfg/premix_files.txt")} for s in d["steps"]]
    relative = os.path.relpath(os.path.join(base, d["name"]), base)
    return {
        "status": "ok", "production": relative, "run_period": d["run_period"],
        "campaign": d["campaign"], "final_tier": d["final_tier"], "pileup": d["pileup"],
        "lhe": d["lhe"], "events": d["events"],
        "jobs": {"n": len(jobs), "events_per_job": d["events_per_job"], "last_job_events": jobs[-1]},
        "steps": steps, "output": d["output"], "keep_tiers": d["keep_tiers"],
        "threads": d["threads"], "seed": d["seed"], "needs": needs, "notes": notes,
        "next_steps": [
            f"RunMCProduction(production='{relative}', where='local', test_events=5) for a quick "
            "test of the whole chain here",
            f"RunMCProduction(production='{relative}', where='condor') for all {len(jobs)} job(s) "
            "on HTCondor (needs a grid proxy and a root:// output); confirm with the user first",
            f"MCProductionStatus(production='{relative}') to follow it",
        ],
    }
