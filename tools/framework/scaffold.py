"""
# scaffold.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

ScaffoldAnalysisFrameworkTool -- write a coffea analysis framework for a new analysis.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis import nanoaod_layout
from tools.framework import _projects as projects
from tools.framework import _render as render
from tools.framework import _scaffold as scaffold
from tools.framework._components import ComponentError


class ScaffoldAnalysisFrameworkTool(BaseTool):
    """
    Write a coffea analysis framework for a new analysis: a python package to build on.

    The package has an analysis-independent engine (processor, selections, histograms,
    cutflows, utilities, plotting, a self-check) and a starting set of definitions and
    configs for the objects you name. Nothing in the engine is specific to one
    analysis: the physics goes into definitions/ (objects.py, cuts.py, hists.py,
    weights.py) and configs/ (selections.yaml, hist_collections.yaml, samples), which
    you then edit directly.

    What is written has not been run: this tool reads at most the branch names of
    `sample_file`. Whether the framework runs on your files is what
    CheckAnalysisFramework finds out, so call it next.

    Call this ONCE, at the start of a new analysis. Give it `sample_file` whenever you
    have a representative ROOT file: the objects, cuts and histograms are then matched
    to what the file really contains (including collections coffea does not know).
    Unless `samples` is given, that file also becomes the first sample, named after
    it. Without `sample_file`, standard NanoAOD is assumed.

    After it returns:
      1. run CheckAnalysisFramework on the project (static checks, then a short run);
      2. edit definitions/ and configs/ to express the analysis, re-checking as you go;
      3. add optional parts with AddFrameworkComponent only when they are asked for
         (lepton_jets, scaleout, schema, chain_report).

    The cuts, thresholds and selections it writes are placeholders so that there is
    something to run (pT and |eta| thresholds per object type, a baseline, one
    two-object channel). They are not the analysis's selection: replace them.

    Args:
        project_dir: Directory to create, relative to the working directory.
        package_name: Python package name (default: derived from project_dir).
        project_title, project_description, author: Free text for the README and
            package metadata.
        experiment: Experiment whose plot style and label to use (default "CMS").
        sample_file: A representative NanoAOD-like ROOT file, relative to the working
            directory. Strongly recommended.
        tree_name: TTree holding the events (default "Events"). Recorded in the
            package and used by its scripts, notebooks and self-check.
        objects: {object name: collection name} to add to, or override in, the default
            objects, e.g. {"dsaMuons": "DSAMuon", "taus": "Tau"}. The object name is
            what cuts and histograms will call it.
        include_default_objects: Start from the standard objects present in the file
            (pvs, muons, electrons, photons, jets, met, hlt, flags, gens). Default true.
        triggers: HLT paths ORed into a "pass triggers" event cut of the baseline
            selection, with or without the "HLT_" prefix.
        year: Run period of the samples, e.g. "2018".
        lumi: Integrated luminosity of that period in /pb. Needs `year`.
        golden_json: Golden JSON for that period, relative to the working directory;
            copied into the package's data/ directory. Needs `year`.
        samples: Samples to register: [{"name", "files", "is_data", "xsec" (pb),
            "year", "skim_factor"}]. `files` are complete paths, root:// urls, or
            paths relative to the working directory, one per file: directories and
            wildcards are not expanded. Names use letters, digits and _ . + - only;
            the cross section is looked up under the name.
        components: Optional components to add right away, with their default
            options (see AddFrameworkComponent).
        overwrite: Allow project_dir to be a directory that already has files in it
            (a new repository with a README, say). Files the framework writes then
            replace existing ones of the same name, and are listed in
            `files_replaced`. A directory that already holds a framework is never
            written over: change that one through its definitions/ and configs/.

    Returns (JSON):
        {"status": "ok", "project", "package", "tree", "n_files_written",
         "files_replaced": [...], "objects": [{"name", "collection", "kind",
         "object_cuts", "wrapped_as_lorentz", "optional"}], "channels": [...],
         "hist_collections": [...], "n_hists", "n_event_cuts", "samples": [...],
         "components": [...], "components_failed": [{"component", "error",
         "suggestion"}], "notes": [...], "layout": {...}, "next_steps": [...]}
        `notes` lists everything worth knowing about the result: collections that were
        re-zipped to get vector behaviour, assumed masses, standard collections that
        were left out and why, missing normalisation inputs, other collections in the
        file, and what each added component had to say (prefixed "[component]").
        Read them. `components_failed` lists requested components that could not be
        added (the framework itself is written all the same), each with the reason.

    Errors:
        A formatted error if a path leaves the working directory, the directory is
        not empty or already holds a framework, a name or value is invalid, or a
        requested collection or file is missing or cannot be read. Nothing is
        written in that case: the working directory is left as it was.
    """

    # --------------------------- Runtime fields --------------------------- #
    project_dir: str = RuntimeField(
        description="Directory to create for the analysis, relative to the working directory")
    package_name: Optional[str] = RuntimeField(
        default=None, description="Python package name (default: derived from project_dir)")
    project_title: Optional[str] = RuntimeField(
        default=None, description="Title of the analysis")
    project_description: Optional[str] = RuntimeField(
        default=None, description="One-sentence description of the analysis")
    author: Optional[str] = RuntimeField(default=None, description="Author name")
    experiment: str = RuntimeField(
        default="CMS", description="Experiment for plot style and label (CMS, ATLAS, LHCb, ...)")
    sample_file: Optional[str] = RuntimeField(
        default=None,
        description="Representative NanoAOD-like ROOT file, relative to the working directory")
    tree_name: str = RuntimeField(default="Events", description="TTree holding the events")
    objects: Optional[Dict[str, str]] = RuntimeField(
        default=None,
        description="{object name: collection} to add to or override in the default objects, "
                    "e.g. {'dsaMuons': 'DSAMuon'}")
    include_default_objects: bool = RuntimeField(
        default=True,
        description="Start from the standard objects found in the file (muons, electrons, ...)")
    triggers: Optional[List[str]] = RuntimeField(
        default=None, description="HLT paths ORed into the baseline 'pass triggers' cut")
    year: Optional[Union[str, int]] = RuntimeField(
        default=None, description="Run period of the samples, e.g. '2018'")
    lumi: Optional[float] = RuntimeField(
        default=None, description="Integrated luminosity of the run period in /pb (needs year)")
    golden_json: Optional[str] = RuntimeField(
        default=None, description="Golden JSON file for the run period, relative to the working directory")
    samples: Optional[List[Dict[str, Any]]] = RuntimeField(
        default=None,
        description="Samples to register: [{'name', 'files', 'is_data', 'xsec', 'year', "
                    "'skim_factor'}]; files are complete paths, root:// urls, or relative "
                    "to the working directory")
    components: Optional[List[str]] = RuntimeField(
        default=None,
        description="Optional components to add now: lepton_jets, scaleout, schema, chain_report")
    overwrite: bool = RuntimeField(
        default=False,
        description="Allow a directory that already has files (never one that holds a framework)")
    # ---------------------------------------------------------------------- #

    # ---------------------------- State fields ---------------------------- #
    base_directory: str = StateField(default=".", description="Base directory for safe paths")
    # ---------------------------------------------------------------------- #

    def _setup(self):
        self.base_directory = os.path.abspath(self.base_directory)
        if not os.path.isdir(self.base_directory):
            raise ValueError(f"Base directory does not exist: {self.base_directory}")

    def _sample_files(self, base: str, samples: Any):
        """Make relative sample files absolute. Returns (samples, notes, error)."""
        notes: List[str] = []
        if not isinstance(samples, list):
            return samples, notes, None          # create_project says what is wrong with it
        resolved = []
        for sample in samples:
            files = sample.get("files") if isinstance(sample, dict) else None
            if isinstance(files, str):
                files = [files]
            if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
                resolved.append(sample)
                continue
            paths = []
            for name in files:
                if "://" in name or not name:
                    paths.append(name)
                    continue
                if name.startswith("~"):
                    name = os.path.expanduser(name)
                full = name if os.path.isabs(name) else projects.safe_path(base, name)
                if full is None:
                    return None, notes, self.format_error(
                        error="Access Denied",
                        reason="a relative sample file escapes the working directory",
                        context=f"sample={sample.get('name')!r}, file={name}",
                        suggestion="Give files outside the working directory by their "
                                   "complete path or root:// url")
                if os.path.isdir(full):
                    return None, notes, self.format_error(
                        error="Invalid Request",
                        reason=f"sample '{sample.get('name')}': {name} is a directory, not a file",
                        suggestion="List the ROOT files of the sample one by one. Once the "
                                   "framework exists, `python -m <package>.scripts.add_samples` "
                                   "writes a group of samples from a directory listing")
                if os.path.isabs(name):
                    paths.append(name)          # a complete path is registered as given
                    continue
                if os.path.exists(full):
                    # where the file really is, so that the config survives a move
                    full = os.path.realpath(full)
                else:
                    hint = ""
                    if any(char in name for char in "*?["):
                        hint = " Wildcards are not expanded: list the files."
                    elif "$" in name:
                        hint = " Environment variables are not expanded: give the path itself."
                    notes.append(f"sample '{sample.get('name')}': {name} was not found in the "
                                 f"working directory; it is registered as {full}.{hint}")
                paths.append(full)
            resolved.append({**sample, "files": paths})
        return resolved, notes, None

    def _run(self) -> str:
        try:
            return self._scaffold()
        except Exception as exc:    # a tool must answer, not raise
            return self.format_error(
                error="Scaffold Failed", reason=f"{type(exc).__name__}: {exc}",
                context=f"project_dir={self.project_dir}",
                suggestion="This is a problem in the tool rather than in the request. "
                           "Nothing is left half-written: a failed write is undone")

    def _scaffold(self) -> str:
        base = os.path.abspath(self.base_directory)
        # the framework is written there: where the path really leads must be inside too
        project = projects.safe_path(base, self.project_dir, follow_links=True)
        if project is None or project == base:
            return self.format_error(
                error="Access Denied",
                reason="project_dir must be a directory inside the working directory "
                       "(a link that leads out of it does not count)",
                context=f"project_dir={self.project_dir}",
                suggestion="Use a relative path such as 'my_analysis'")

        layout = None
        samples, notes, error = self._sample_files(
            base, [] if self.samples is None else self.samples)
        if error:
            return error
        if self.sample_file:
            sample_path = projects.safe_path(base, self.sample_file)
            if sample_path is None:
                return self.format_error(
                    error="Access Denied", reason="sample_file escapes the working directory",
                    context=f"sample_file={self.sample_file}",
                    suggestion="Copy or link the file into the working directory")
            if not os.path.isfile(sample_path):
                return self.format_error(
                    error="File Not Found", reason="sample_file does not exist",
                    context=f"sample_file={self.sample_file}",
                    suggestion="Check the path, or omit sample_file to assume standard NanoAOD")
            try:
                tree = nanoaod_layout.read_tree_layout(sample_path, self.tree_name)
            except ImportError as exc:
                return self.format_error(
                    error="Missing Dependency",
                    reason=f"sample_file could not be read: {exc}",
                    suggestion="Install the framework bundle's dependencies (uproot and what "
                               "it needs), or omit sample_file to assume standard NanoAOD")
            except KeyError as exc:
                return self.format_error(
                    error="Tree Not Found", reason=str(exc.args[0]) if exc.args else str(exc),
                    suggestion="Pass tree_name as one of the trees listed")
            except Exception as exc:
                return self.format_error(
                    error="Read Error", reason=f"{type(exc).__name__}: {exc}",
                    context=f"sample_file={self.sample_file}")
            if tree.get("kind") == "RNTuple":
                return self.format_error(
                    error="Not A TTree",
                    reason=f"'{self.tree_name}' in sample_file is an RNTuple: its layout cannot "
                           "be read off the way a TTree's can",
                    context=f"sample_file={self.sample_file}",
                    suggestion="Use a file that stores the events in a TTree, or omit "
                               "sample_file to assume standard NanoAOD and name the objects "
                               "yourself")
            layout = nanoaod_layout.split_collections(tree["branches"])
            if not samples:
                is_data = "GenPart" not in layout and "genWeight" not in layout
                name = Path(sample_path).name
                if name.endswith(".root"):
                    name = name[: -len(".root")]
                # Record where the file really is: a link inside the working directory
                # would stop working once the framework is moved somewhere else.
                samples = [{"name": render.sample_name_from(name),
                            "files": [os.path.realpath(sample_path)], "is_data": is_data}]

        golden = None
        if self.golden_json:
            golden_path = projects.safe_path(base, self.golden_json)
            if golden_path is None or not os.path.isfile(golden_path):
                return self.format_error(
                    error="File Not Found",
                    reason="golden_json does not exist inside the working directory",
                    context=f"golden_json={self.golden_json}",
                    suggestion="Copy the JSON into the working directory first")
            golden = Path(golden_path)

        try:
            result = scaffold.create_project(
                Path(project), package=self.package_name, title=self.project_title,
                description=self.project_description, author=self.author,
                experiment=self.experiment or "CMS", layout=layout, objects=self.objects,
                include_default_objects=self.include_default_objects,
                triggers=self.triggers, year=self.year, lumi=self.lumi, golden_json=golden,
                samples=samples, tree_name=self.tree_name, add_components=self.components,
                overwrite=self.overwrite)
        except (scaffold.ScaffoldError, ComponentError) as exc:
            return self.format_error(
                error="Invalid Request", reason=str(exc),
                context=f"project_dir={self.project_dir}",
                suggestion=getattr(exc, "suggestion", "") or
                "Correct the value named above and call the tool again")
        except OSError as exc:
            return self.format_error(
                error="Scaffold Failed", reason=f"{type(exc).__name__}: {exc}",
                context=f"project_dir={self.project_dir}",
                suggestion="A file could not be written. What had been written was removed "
                           "again: the directory is as it was")

        result["project"] = self.project_dir
        result["notes"] = notes + result["notes"]
        return json.dumps({"status": "ok", **result}, ensure_ascii=False)
