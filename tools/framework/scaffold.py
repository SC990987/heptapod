"""
# scaffold.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

ScaffoldAnalysisFrameworkTool -- write a coffea analysis framework for a new analysis.
"""
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from orchestral.tools.base.tool import BaseTool
from orchestral.tools.base.field_utils import RuntimeField, StateField

from tools.analysis import nanoaod_layout
from tools.framework import _git as git
from tools.framework import _projects as projects
from tools.framework import _render as render
from tools.framework import _samples as samples_lib
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
    Unless samples are given, that file also becomes the first sample, named after
    it. Without `sample_file`, standard NanoAOD is assumed.

    Where it goes: `repo`, the analyst's git repository (a url, which is cloned into
    the working directory, or a clone already there), or else a new `project_dir`.
    In a repository the framework is committed, in one commit on the current branch,
    with nothing else that was staged; it is never pushed: the answer gives the
    command for that. A file of the repository's that the framework would replace is
    refused unless `overwrite` is given (a README of a line or two is replaced, a
    longer one kept; a .gitignore gets the framework's entries added).

    Where the events are: `sample_locations` names the directories that hold the
    simulation and the data (local, or root:// on EOS). Their files are listed and
    written as cms-sidm/SIDM writes its ntuple configs: a file per directory under
    configs/samples/ (signal_2mu2e_v10.yaml, data.yaml), holding a comment line, the
    group (llpNanoAOD_v2) with the directory as its path, and one sample per
    sub-directory with its files and the directory they are in. Samples are named by
    `name_format` (2Mu2E_{MBs}GeV_{MDp}GeV_{ctau}mm gives SIDM's names), or else after
    their directories.

    What it starts with: objects and a menu of cuts for them, a baseline selection
    (muons pT > 5 GeV, electrons pT > 10 GeV, |eta| < 2.4, as cms-sidm/SIDM's lepton
    sources), basic histograms of every object (pT, multiplicity, eta-phi), and a
    first study, studies/obj_basics/basic_histograms.ipynb, that processes a file of
    two simulated samples and one data sample and plots the muon and electron
    histograms with the samples overlaid.

    After it returns:
      1. run CheckAnalysisFramework on the project (static checks, then a short run);
      2. edit definitions/ and configs/ to express the analysis, re-checking as you go;
      3. add optional parts with AddFrameworkComponent only when they are asked for
         (lepton_jets, scaleout, schema, chain_report).

    The cuts, thresholds and selections it writes are placeholders so that there is
    something to run (pT and |eta| thresholds per object type, a baseline, one
    two-object channel). They are not the analysis's selection: replace them.

    Args:
        project_dir: Directory to create, relative to the working directory. With a
            repo url: where it is cloned (default: the repository's name).
        repo: The git repository to build the framework in: a url to clone
            (https://..., git@github.com:owner/name.git), or a clone inside the working
            directory. The framework is committed there and never pushed.
        package_name: Python package name (default: derived from project_dir).
        project_title, project_description, author: Free text for the README and
            package metadata.
        experiment: Experiment whose plot style and label to use (default "CMS").
        sample_file: A representative NanoAOD-like ROOT file, relative to the working
            directory, or a root:// url (reading one needs fsspec-xrootd and xrootd in
            the toolkit's environment). Strongly recommended.
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
        samples: Samples to register file by file: [{"name", "files", "is_data",
            "xsec" (pb), "year", "skim_factor"}]. `files` are complete paths, root://
            urls, or paths relative to the working directory, one per file: directories
            and wildcards are not expanded (use sample_locations for directories).
            Names use letters, digits and _ . + - only; the cross section is looked up
            under the name.
        sample_locations: Directories that hold samples: [{"directory", "is_data",
            "year", "config", "tag", "comment", "name_format", "name", "skim_factor",
            "xsec"}]. `directory` is a root:// url (listed with xrdfs) or a directory
            inside the working directory. Each location becomes the group `tag`
            (default: the directory's name) of the file `config` under
            configs/samples/ (default: <directory's name>.yaml), with `comment` on the
            line above it. Every sub-directory holding ROOT files becomes a sample,
            named by `name_format`, a format whose fields are the KEY-VALUE pieces of
            the directory names ("2Mu2E_{MBs}GeV_{MDp}GeV_{ctau}mm" names
            ..._MBs-500_MDp-0p25_ctau-0p4_v3 2Mu2E_500GeV_0p25GeV_0p4mm; {dir} is the
            whole name), or else after the
            directory without the part all of them share. With `name`, the whole
            directory is one sample (files sitting directly in the directory are one
            sample, named after it). `xsec` only for a location that holds a single
            sample.
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
         "suggestion"}], "sample_configs": {file: {tag: n_samples}}, "notes": [...],
         "layout": {...}, "next_steps": [...],
         "git": {"repository", "cloned_from", "branch", "remote", "commit", "push",
                 "note"}}   (git only with repo)
        `notes` lists everything worth knowing about the result: collections that were
        re-zipped to get vector behaviour, assumed masses, standard collections that
        were left out and why, missing normalisation inputs, other collections in the
        file, and what each added component had to say (prefixed "[component]").
        Read them. `components_failed` lists requested components that could not be
        added (the framework itself is written all the same), each with the reason.

    Errors:
        A formatted error if a path leaves the working directory, the directory is
        not empty or already holds a framework, a name or value is invalid, a
        requested collection or file is missing or cannot be read, a sample location
        cannot be listed, or the repository cannot be cloned. Nothing is written in
        that case: the working directory is left as it was (a clone made for the call
        is removed again).
    """

    # --------------------------- Runtime fields --------------------------- #
    project_dir: Optional[str] = RuntimeField(
        default=None,
        description="Directory to create for the analysis, relative to the working directory "
                    "(with a repo url: where it is cloned, default its name)")
    repo: Optional[str] = RuntimeField(
        default=None,
        description="Git repository to build the framework in: a url to clone, or a clone "
                    "inside the working directory. Committed there, never pushed")
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
        description="Representative NanoAOD-like ROOT file, relative to the working directory, "
                    "or a root:// url")
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
        description="Samples to register file by file: [{'name', 'files', 'is_data', 'xsec', "
                    "'year', 'skim_factor'}]; files are complete paths, root:// urls, or "
                    "relative to the working directory")
    sample_locations: Optional[List[Dict[str, Any]]] = RuntimeField(
        default=None,
        description="Directories holding the simulation and the data: [{'directory' (root:// "
                    "url or inside the working directory), 'is_data', 'year', 'config' (file "
                    "under configs/samples/), 'tag', 'comment', 'name_format' (e.g. "
                    "'2Mu2E_{MBs}GeV_{MDp}GeV_{ctau}mm'), 'name' (whole directory as one "
                    "sample)}]; one sample per sub-directory")
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
                        suggestion="Give the directory as a sample location "
                                   "(sample_locations=[{'directory': ...}]) or list its ROOT "
                                   "files one by one. Once the framework exists, `python -m "
                                   "<package>.scripts.add_samples` adds a directory's samples too")
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

    def _where(self, base: str):
        """Where the framework goes: (relative directory, url to clone or None, error)."""
        if not self.repo:
            if not self.project_dir:
                return None, None, self.format_error(
                    error="Invalid Request", reason="give project_dir (a new directory) or repo "
                                                    "(the git repository to build in)")
            return self.project_dir, None, None
        if not git.available():
            return None, None, self.format_error(
                error="Git Not Found", reason="building in a repository needs git, which is not "
                                              "on PATH here",
                suggestion="Install git, or give project_dir and commit the framework yourself")
        if git.is_url(self.repo) or "://" in self.repo:
            problem = git.url_problem(self.repo)
            if problem:
                return None, None, self.format_error(
                    error="Invalid Request", reason=f"repo {self.repo!r}: {problem}")
            source = git.local_path(self.repo)
            if source is not None and projects.safe_path(base, source) is None:
                return None, None, self.format_error(
                    error="Access Denied",
                    reason="a file:// repository must be inside the working directory",
                    context=f"repo={self.repo}",
                    suggestion="Give the repository's remote url (https:// or ssh), or its "
                               "clone inside the working directory")
            return self.project_dir or git.repo_name(self.repo), self.repo, None
        local = projects.safe_path(base, self.repo, follow_links=True)
        if local is None:
            return None, None, self.format_error(
                error="Access Denied",
                reason="repo is neither a url nor a directory inside the working directory",
                context=f"repo={self.repo}",
                suggestion="Give the repository's url (it is cloned here), or clone it into "
                           "the working directory first")
        root = git.work_tree_root(Path(local)) if os.path.isdir(local) else None
        if root is None or os.path.realpath(str(root)) != os.path.realpath(local):
            return None, None, self.format_error(
                error="Not A Repository",
                reason=f"{self.repo} is not the top directory of a git clone",
                suggestion="Give the repository's url, or the directory its clone is in")
        if self.project_dir and projects.safe_path(base, self.project_dir) != local:
            return None, None, self.format_error(
                error="Invalid Request",
                reason="repo and project_dir name two different directories",
                suggestion="Give repo alone: the framework is built at the top of the clone")
        return self.repo, None, None

    def _locations(self, base: str):
        """List every sample location. Returns (locations, error)."""
        if self.sample_locations is None:
            return [], None
        if not isinstance(self.sample_locations, list):
            return None, self.format_error(error="Invalid Request",
                                           reason="sample_locations must be a list",
                                           suggestion=scaffold.LOCATION_SHAPE)
        listed = []
        for index, entry in enumerate(self.sample_locations):
            directory = entry.get("directory") if isinstance(entry, dict) else None
            if not isinstance(directory, str) or not directory:
                return None, self.format_error(
                    error="Invalid Request", reason=f"sample location #{index} needs a 'directory'",
                    suggestion=scaffold.LOCATION_SHAPE)
            if "files" in entry:
                return None, self.format_error(
                    error="Invalid Request",
                    reason=f"location {directory}: the files of a location are found by listing it",
                    suggestion="Give files one by one as a sample of `samples` instead")
            if samples_lib.is_remote(directory):
                host, path = samples_lib.split_xrootd(directory)
                directory = f"{host}/{path.rstrip('/')}/"     # root://host//one/slash/each/
            else:
                full = projects.safe_path(base, directory)
                if full is None:
                    return None, self.format_error(
                        error="Access Denied",
                        reason=f"sample location {directory} is outside the working directory",
                        suggestion="Give its root:// url, or link it into the working directory "
                                   "(the launcher's ANALYSIS_DATA does) and give that path")
                if not os.path.isdir(full):
                    return None, self.format_error(
                        error="File Not Found",
                        reason=f"sample location {directory} is not a directory",
                        suggestion="A single file is a sample: give it in `samples`")
                directory = os.path.realpath(full)     # where the files really are
            try:
                files = samples_lib.list_files(directory)
            except samples_lib.LocationError as exc:
                return None, self.format_error(error="Listing Failed", reason=str(exc),
                                               suggestion=exc.suggestion)
            listed.append({**entry, "directory": directory, "files": files})
        return listed, None

    def _layout(self, base: str):
        """Branch layout of sample_file. Returns (layout, path read, error)."""
        if samples_lib.is_remote(self.sample_file):
            sample_path = self.sample_file
        else:
            sample_path = projects.safe_path(base, self.sample_file)
            if sample_path is None:
                return None, None, self.format_error(
                    error="Access Denied", reason="sample_file escapes the working directory",
                    context=f"sample_file={self.sample_file}",
                    suggestion="Copy or link the file into the working directory, or give its "
                               "root:// url")
            if not os.path.isfile(sample_path):
                return None, None, self.format_error(
                    error="File Not Found", reason="sample_file does not exist",
                    context=f"sample_file={self.sample_file}",
                    suggestion="Check the path, or omit sample_file to assume standard NanoAOD")
        try:
            tree = nanoaod_layout.read_tree_layout(sample_path, self.tree_name)
        except ImportError as exc:
            remote = samples_lib.is_remote(sample_path)
            return None, None, self.format_error(
                error="Missing Dependency",
                reason=f"sample_file could not be read: {exc}",
                suggestion=("Reading root:// needs fsspec-xrootd and xrootd in the toolkit's "
                            "environment: install them there, or give a local copy of the file"
                            if remote else
                            "Install the framework bundle's dependencies (uproot and what it "
                            "needs), or omit sample_file to assume standard NanoAOD"))
        except KeyError as exc:
            return None, None, self.format_error(
                error="Tree Not Found", reason=str(exc.args[0]) if exc.args else str(exc),
                suggestion="Pass tree_name as one of the trees listed")
        except Exception as exc:
            return None, None, self.format_error(
                error="Read Error", reason=f"{type(exc).__name__}: {exc}",
                context=f"sample_file={self.sample_file}")
        if tree.get("kind") == "RNTuple":
            return None, None, self.format_error(
                error="Not A TTree",
                reason=f"'{self.tree_name}' in sample_file is an RNTuple: its layout cannot "
                       "be read off the way a TTree's can",
                context=f"sample_file={self.sample_file}",
                suggestion="Use a file that stores the events in a TTree, or omit "
                           "sample_file to assume standard NanoAOD and name the objects "
                           "yourself")
        return nanoaod_layout.split_collections(tree["branches"]), sample_path, None

    def _commit(self, project: Path, relative: str, written: List[str],
                cloned_from: Optional[str], result: Dict[str, Any]) -> Dict[str, Any]:
        """Commit what was written into the repository; never push."""
        title = render.clean_text(self.project_title) or result["package"]
        message = (f"Add the {title} analysis framework\n\n"
                   f"Written by HEPTAPOD's ScaffoldAnalysisFramework: the package "
                   f"{result['package']}, {len(result['objects'])} objects, the channels "
                   f"{', '.join(result['channels'])}, {len(result['samples'])} samples and a "
                   "first study. Its cuts and thresholds are starting values.")
        paths = sorted({path for path in written if (project / path).exists()})
        try:
            commit, problem, ignored = git.commit(project, paths, message)
            described = git.describe(project)
        except git.GitError as exc:            # a hook or a signing step that hangs
            commit, problem, ignored = None, str(exc), []
            described = {"branch": None, "remote": None}
        info = {"repository": relative, "cloned_from": cloned_from,
                "branch": described["branch"], "remote": described["remote"],
                "commit": commit, "push": None, "note": None}
        if commit is not None and described["remote"]:
            info["push"] = f"git -C {relative} push -u origin HEAD"
            info["note"] = ("committed, not pushed: look at it, then push with the command "
                            "above from a shell that has your git credentials")
        elif commit is not None:
            info["note"] = "committed; the repository has no remote 'origin' to push to"
        else:
            info["note"] = (f"written, not committed ({problem}). What could be staged is "
                            f"staged: commit it with git -C {relative} commit -m 'Add the "
                            "analysis framework'")
        if ignored:
            info["note"] += (f". Left out because the repository's .gitignore excludes them: "
                             f"{ignored[:10]}{' and more' if len(ignored) > 10 else ''} "
                             "(add them with git add -f if they belong in it)")
        return info

    def _scaffold(self) -> str:
        base = os.path.abspath(self.base_directory)
        relative, clone_url, error = self._where(base)
        if error:
            return error
        # the framework is written there: where the path really leads must be inside too
        project = projects.safe_path(base, relative, follow_links=True)
        if project is None or project == base:
            return self.format_error(
                error="Access Denied",
                reason="the framework must go into a directory inside the working directory "
                       "(a link that leads out of it does not count)",
                context=f"project_dir={relative}",
                suggestion="Use a relative path such as 'my_analysis'")
        notes: List[str] = []
        if clone_url and os.path.isdir(project) and os.listdir(project):
            root = git.work_tree_root(Path(project))
            if root is None or os.path.realpath(str(root)) != os.path.realpath(project):
                return self.format_error(
                    error="Invalid Request",
                    reason=f"'{relative}' already exists and is not a clone to build in",
                    suggestion="Pass project_dir with a new directory name for the clone")
            origin = git.describe(Path(project))["remote"]
            if origin != clone_url:
                where = origin or "a repository without an origin"
                return self.format_error(
                    error="Invalid Request",
                    reason=f"'{relative}' is a clone of {where}, not of {clone_url}",
                    suggestion="Pass project_dir with a new directory name for the clone")
            if git.half_made(Path(project)):
                return self.format_error(
                    error="Invalid Request",
                    reason=f"'{relative}' is a clone that was not finished (no branch is "
                           "checked out)",
                    suggestion="Remove it and call again, or pass project_dir with another "
                               "directory name")
            notes.append(f"'{relative}' is already a clone: the framework was built in it "
                         "rather than in a new clone")
            clone_url = None

        layout, sample_path = None, None
        samples, sample_notes, error = self._sample_files(
            base, [] if self.samples is None else self.samples)
        if error:
            return error
        notes += sample_notes
        locations, error = self._locations(base)
        if error:
            return error
        if self.sample_file:
            layout, sample_path, error = self._layout(base)
            if error:
                return error
            if not samples and not locations:
                is_data = "GenPart" not in layout and "genWeight" not in layout
                name = samples_lib.name_from_directory(sample_path)
                if name.endswith(".root"):
                    name = name[: -len(".root")]
                # Record where the file really is: a link inside the working directory
                # would stop working once the framework is moved somewhere else.
                where = sample_path if samples_lib.is_remote(sample_path) else \
                    os.path.realpath(sample_path)
                samples = [{"name": render.sample_name_from(name), "files": [where],
                            "is_data": is_data}]

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

        cloned = False
        existed = os.path.isdir(project)            # git clones into an empty directory only
        if clone_url:
            try:
                git.clone(clone_url, Path(project))
            except git.GitError as exc:
                self._forget_clone(project, True, existed)
                return self.format_error(
                    error="Clone Failed", reason=str(exc), context=f"repo={clone_url}",
                    suggestion="Check the url and that this machine can read it: an https url "
                               "works for a public repository, ssh needs a key here")
            cloned = True
        try:
            result = scaffold.create_project(
                Path(project), package=self.package_name, title=self.project_title,
                description=self.project_description, author=self.author,
                experiment=self.experiment or "CMS", layout=layout, objects=self.objects,
                include_default_objects=self.include_default_objects,
                triggers=self.triggers, year=self.year, lumi=self.lumi, golden_json=golden,
                samples=samples, locations=locations, tree_name=self.tree_name,
                add_components=self.components, overwrite=self.overwrite,
                repo=bool(self.repo), representative=sample_path)
        except (scaffold.ScaffoldError, ComponentError) as exc:
            self._forget_clone(project, cloned, existed)
            return self.format_error(
                error="Invalid Request", reason=str(exc),
                context=f"project_dir={relative}",
                suggestion=getattr(exc, "suggestion", "") or
                "Correct the value named above and call the tool again")
        except OSError as exc:
            self._forget_clone(project, cloned, existed)
            return self.format_error(
                error="Scaffold Failed", reason=f"{type(exc).__name__}: {exc}",
                context=f"project_dir={relative}",
                suggestion="A file could not be written. What had been written was removed "
                           "again: the directory is as it was")
        except BaseException:
            self._forget_clone(project, cloned, existed)
            raise

        written = result.pop("written")
        result["project"] = relative
        result["notes"] = notes + result["notes"]
        if self.repo:
            result["git"] = self._commit(Path(project), relative, written,
                                         self.repo if cloned else None, result)
        return json.dumps({"status": "ok", **result}, ensure_ascii=False)

    @staticmethod
    def _forget_clone(project: str, cloned: bool, keep_directory: bool = False) -> None:
        """Remove a clone this call made (or began), so that a failed call leaves nothing
        behind. ``keep_directory``: the directory was there, empty, before the call."""
        if not cloned or not os.path.isdir(project):
            return
        if not keep_directory:
            shutil.rmtree(project, ignore_errors=True)
            return
        for entry in os.listdir(project):
            path = os.path.join(project, entry)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.unlink(path)
                except OSError:
                    pass
