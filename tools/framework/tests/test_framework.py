"""Tests for the framework tools that need no analysis library.

They cover everything that happens on the heptapod side: rendering the starting
definitions and configs, writing a project, adding components, and the three tools'
handling of requests. Nothing here imports coffea or awkward; the generated code is
checked by compiling it and by cross-checking names between files.

Whether a generated framework actually runs is tested in test_framework_run.py.
"""
import ast
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.analysis import nanoaod_layout  # noqa: E402
from tools.analysis.test_nanoaod_inspect import BRANCHES  # noqa: E402
from tools.framework import _components as components  # noqa: E402
from tools.framework import _projects as projects  # noqa: E402
from tools.framework import _render as render  # noqa: E402
from tools.framework import _scaffold as scaffold  # noqa: E402

LAYOUT = nanoaod_layout.split_collections(BRANCHES)
TOKENS = ("analysis_pkg", "__PROJECT_TITLE__", "__PROJECT_DESCRIPTION__", "__AUTHOR__",
          "__EXPERIMENT__", "__EXAMPLE_SAMPLE__", "__EXAMPLE_HIST__", "__TREE_NAME__")


class _Skipped(Exception):
    pass


def _skip(reason):
    """Skip properly under pytest; say so when run as a script."""
    if "pytest" in sys.modules:
        import pytest
        pytest.skip(reason)
    raise _Skipped(reason)


def _create(tmp, name="demo_analysis", **kwargs):
    kwargs.setdefault("layout", LAYOUT)
    kwargs.setdefault("objects", {"dsaMuons": "DSAMuon"})
    return scaffold.create_project(Path(tmp) / name, **kwargs)


def _refused(error, call, *args, **kwargs):
    """Run a call that must be refused and return the message it was refused with."""
    try:
        call(*args, **kwargs)
    except error as exc:
        return f"{exc} | {getattr(exc, 'suggestion', '')}"
    raise AssertionError(f"{getattr(call, '__name__', call)}{args} {kwargs} should have been refused")


def _yaml():
    try:
        import yaml
    except ImportError:
        _skip("PyYAML is not installed")
    return yaml


def _tree(root):
    """Every directory and every file (with its content) under a directory."""
    root = Path(root)
    return {str(path.relative_to(root)): (path.read_bytes() if path.is_file() else None)
            for path in sorted(root.rglob("*"))}


def _text_files(project):
    for path in Path(project).rglob("*"):
        if path.is_file() and path.suffix in (".py", ".md", ".yaml", ".yml", ".txt", ".in",
                                              ".ipynb", ".json", ""):
            yield path


def _compile_all(project):
    """Compile every python file and every notebook code cell of a project."""
    n = 0
    for path in Path(project).rglob("*.py"):
        compile(path.read_text(encoding="utf8"), str(path), "exec")
        n += 1
    for path in Path(project).rglob("*.ipynb"):
        notebook = json.loads(path.read_text(encoding="utf8"))
        assert notebook["nbformat"] == 4
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                source = "".join(line for line in cell["source"] if not line.lstrip().startswith("%"))
                compile(source, str(path), "exec")
    return n


def _quoted_keys(text):
    """Every string used as a dict key in a piece of generated python."""
    return set(re.findall(r'^\s*"((?:[^"\\]|\\.)*)":', text, flags=re.M))


def _names_in_yaml_lists(text):
    return re.findall(r'^\s*-\s*"((?:[^"\\]|\\.)*)"\s*$', text, flags=re.M)


# --------------------------------------------------------------------------- #
# names
# --------------------------------------------------------------------------- #

def test_names_are_derived_and_validated():
    assert render.package_name_from("My Analysis-2024") == "my_analysis_2024"
    assert render.package_name_from("2mu2e") == "a_2mu2e"
    assert render.package_name_problem("dimuon") is None
    for bad in ("Dimuon", "2mu", "my-pkg", "class", "coffea", "analysis_pkg", ""):
        assert render.package_name_problem(bad), bad
    assert render.object_name_problem("dsaMuons") is None
    for bad in ("1jets", "my jets", "evt_weights", "ch", ""):
        assert render.object_name_problem(bad), bad
    assert [render.singular(n) for n in ("muons", "pvs", "met", "ljs", "dsaMuons", "ss")] == [
        "muon", "pv", "met", "lj", "dsaMuon", "ss"]
    assert render.clean_text('a "quoted"\n back\\slash') == "a 'quoted' back slash"
    assert render.clean_text("nul\x00 and\u2028 line") == "nul and line"
    # a package must not replace a module everything else imports
    for taken in ("json", "signal", "test", "numpy", "tools"):
        assert render.package_name_problem(taken), taken
        assert render.package_name_from(taken) == taken + "_analysis"
    # names that yaml would read as something else, or a shell as an option
    for bad in ("yes", "Null", "on"):
        assert render.object_name_problem(bad) and render.sample_name_problem(bad), bad
    assert render.sample_name_problem("TTTo2L2Nu_13TeV-powheg+v1.2") is None
    for bad in ("-rf", "my sample", "a/b", "", "caf\u00e9", 7, None):
        assert render.sample_name_problem(bad), bad
    assert render.sample_name_from("-odd name (1)") == "odd_name_1"
    assert render.sample_name_from("no") == "sample_no" and render.sample_name_from("()") == "sample"
    assert render.tree_name_problem("Events") is None and render.tree_name_problem("dir/Events") is None
    for bad in ("-t", "my tree", 'a"b', ""):
        assert render.tree_name_problem(bad), bad


def test_values_are_written_so_that_python_and_yaml_read_them_back():
    assert [render.num(v) for v in (10, 10.0, 0.5, "2.4", 59830.0)] == ["10", "10", "0.5", "2.4", "59830"]
    assert render.num(1e-5) == "1.0e-05" and render.num(1e22) == "1.0e+22"
    for bad in (True, float("nan"), float("inf"), "heavy", None):
        try:
            render.num(bad)
        except (TypeError, ValueError):
            continue
        raise AssertionError(f"num({bad!r}) must be refused")
    awkward_text = ['plain', 'with "quotes"', "back\\slash", "caf\u00e9", "\U0001F600 emoji",
                    "tab\tand\nnewline", "a: b #c", "{x}"]
    for text in awkward_text:
        assert ast.literal_eval(render.q(text)) == text, text
    value = {"flag": True, "none": None, "n": 3, "x": 0.25, "s": 'a "b"', "l": [1, False, "z"]}
    assert ast.literal_eval(render.py_literal(value)) == value
    assert "true" not in render.py_literal(value) and "null" not in render.py_literal(value)
    for bad in (float("nan"), object(), {1: {2: float("inf")}}):
        try:
            render.py_literal(bad)
        except ValueError:
            continue
        raise AssertionError(f"py_literal({bad!r}) must be refused")
    # text put in for a placeholder is not scanned for placeholders again
    text = projects.render_text("t=__TITLE__ a=__AUTHOR__ p=analysis_pkg x=my_analysis_pkg_x", "pkg",
                                {"__TITLE__": "__AUTHOR__ analysis_pkg", "__AUTHOR__": "me"})
    assert text == "t=__AUTHOR__ analysis_pkg a=me p=pkg x=my_analysis_pkg_x"
    yaml = _yaml()
    for text in awkward_text:
        assert yaml.safe_load(f"k: {render.q(text)}") == {"k": text}, text
    for number in (1e-5, 1e22, 0.105658, 3):
        loaded = yaml.safe_load(f"k: {render.num(number)}")["k"]
        assert isinstance(loaded, (int, float)) and loaded == number, (number, loaded)


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def test_plan_matches_the_file_layout():
    specs = scaffold.resolve_objects(LAYOUT, {"dsaMuons": "DSAMuon"})
    plan = render.build_plan(specs, triggers=["HLT_IsoMu24", "Mu50", "HLT_NotInTheFile"])
    # kinematic menus for jagged objects only; records get event cuts
    assert set(plan["obj_cuts"]) == {"muons", "electrons", "photons", "jets", "gens", "dsaMuons"}
    assert "pvs" not in plan["obj_cuts"] and "met" not in plan["obj_cuts"]
    # ID cuts only where the branch exists: the reduced file has no Muon_tightId
    muon_cuts = [name for name, _ in plan["obj_cuts"]["muons"]]
    assert "looseId" in muon_cuts and "tightId" not in muon_cuts
    # generator objects are not part of the baseline
    assert "gens" not in plan["base_obj_cuts"] and "muons" in plan["base_obj_cuts"]
    evt = dict(plan["evt_cuts"])
    assert "PV filter" in evt and ">=2 muons" in evt and ">=1 gens" not in evt
    assert "IsoMu24" in evt["pass triggers"] and "Mu50" in evt["pass triggers"]
    assert "NotInTheFile" not in evt["pass triggers"]
    assert any("NotInTheFile" in note for note in plan["notes"])
    assert plan["base_evt_cuts"] == ["pass triggers", "PV filter"]
    assert plan["example"] == "muons"
    assert render.channel_names(plan) == ["all", "baseline", "baseline_2muons"]


def test_rendered_definitions_compile_and_agree_with_the_configs():
    specs = scaffold.resolve_objects(LAYOUT, {"dsaMuons": "DSAMuon"})
    plan = render.build_plan(specs, triggers=["IsoMu24"])
    objects, cuts, hists = (render.render_objects(plan), render.render_cuts(plan),
                            render.render_hists(plan))
    for name, text in (("objects", objects), ("cuts", cuts), ("hists", hists)):
        compile(text, name, "exec")
    assert 'primary_objs["dsaMuons"]  = lambda evts: as_lorentz(evts.DSAMuon, mass=0.105658)' in objects
    assert 'primary_objs["muons"]     = lambda evts: evts.Muon' in objects
    # every cut a selection names is defined, and every histogram a collection names too
    defined = _quoted_keys(cuts)
    used = set(_names_in_yaml_lists(render.render_selections(plan)))
    assert used and used <= defined, used - defined
    hist_names = _quoted_keys(hists)
    listed = set(_names_in_yaml_lists(render.render_hist_collections(plan)))
    assert listed and listed <= hist_names, listed - hist_names


def test_yaml_configs_parse():
    yaml = _yaml()
    specs = scaffold.resolve_objects(LAYOUT, {"dsaMuons": "DSAMuon"})
    plan = render.build_plan(specs, triggers=["IsoMu24"])
    selections = yaml.safe_load(render.render_selections(plan))
    assert selections["all"] == {"obj_cuts": {}, "evt_cuts": []}
    assert selections["baseline"]["obj_cuts"]["muons"] == ["pT > 10 GeV", "|eta| < 2.4"]
    assert selections["baseline_2muons"]["evt_cuts"] == [["pass triggers", "PV filter"], ">=2 muons"]
    collections = yaml.safe_load(render.render_hist_collections(plan))
    assert collections["muon_base"][-1] == "muon_muon_invmass"
    assert collections["base"][0] == collections["pv_base"]
    assert yaml.safe_load(render.render_run_periods("2018", 59830.0, "golden.txt")) == {
        "2018": {"lumi": 59830, "golden_json": "golden.txt"}}
    # nothing that a line added or uncommented underneath would turn into invalid yaml
    assert yaml.safe_load(render.render_run_periods("2018", None, None)) == {"2018": None}
    uncommented = render.render_run_periods("2018", None, None).replace("  # lumi: 0.0", "  lumi: 0.0")
    assert yaml.safe_load(uncommented) == {"2018": {"lumi": 0.0}}
    assert yaml.safe_load(render.render_run_periods(None, None, None)) is None
    assert yaml.safe_load(render.render_cross_sections([])) is None
    assert yaml.safe_load(render.render_samples([], "main", None)) == {"main": {"path": "", "samples": None}}
    assert "analysis_pkg" not in render.render_samples([], "main", None, "mypkg")
    samples = [{"name": "Sig-1", "files": ["/a/b.root"], "is_data": False, "xsec": 0.5, "year": "2018",
                "skim_factor": 0.25},
               {"name": "Data", "files": ["x.root", "y.root"], "is_data": True, "xsec": None,
                "year": "2017", "skim_factor": None}]
    assert yaml.safe_load(render.render_cross_sections(samples)) == {"Sig-1": 0.5}
    block = yaml.safe_load(render.render_samples(samples, "main", "2018"))["main"]
    assert block["year"] == "2018" and block["samples"]["Sig-1"]["skim_factor"] == 0.25
    assert block["samples"]["Data"] == {"files": ["x.root", "y.root"], "is_data": True, "year": "2017"}
    # no countable objects at all: the files must still be valid
    lonely = render.build_plan([nanoaod_layout.object_spec("met", "MET", LAYOUT)])
    assert yaml.safe_load(render.render_selections(lonely))["baseline"] == {"obj_cuts": {}, "evt_cuts": [[]]}
    assert yaml.safe_load(render.render_hist_collections(lonely))["base"] == [["met_pt", "met_phi"]]


# --------------------------------------------------------------------------- #
# writing a project
# --------------------------------------------------------------------------- #

def test_project_is_complete_and_has_no_placeholders():
    with tempfile.TemporaryDirectory() as tmp:
        result = _create(tmp, title='The "X" search', triggers=["IsoMu24"], year="2018", lumi=1000.0,
                         samples=[{"name": "Signal", "files": ["/data/s.root"], "xsec": 1.5}])
        project, package = Path(tmp) / "demo_analysis", result["package"]
        assert package == "demo_analysis"
        for relative in ("README.md", "setup.py", "requirements.txt", ".gitignore", "MANIFEST.in",
                         "tools/processor.py", "tools/selection.py", "tools/histogram.py",
                         "tools/cutflow.py", "tools/utilities.py", "tools/plotting.py",
                         "tools/metadata.py", "tools/schema.py", "tools/check.py",
                         "definitions/objects.py", "definitions/cuts.py", "definitions/hists.py",
                         "definitions/weights.py", "configs/selections.yaml",
                         "configs/hist_collections.yaml", "configs/cross_sections.yaml",
                         "configs/run_periods.yaml", "configs/samples/samples.yaml",
                         "scripts/run_analysis.py", "scripts/add_samples.py",
                         "test_notebooks/test_processor.ipynb", "studies/README.md", "data/README.md"):
            top_level = relative in ("README.md", "setup.py", "requirements.txt", ".gitignore", "MANIFEST.in")
            path = project / relative if top_level else project / package / relative
            assert path.is_file(), relative
        for path in _text_files(project):
            text = path.read_text(encoding="utf8")
            for token in TOKENS:
                assert token not in text, f"{token} left in {path.relative_to(project)}"
        assert _compile_all(project) >= 16
        assert "The 'X' search" in (project / "README.md").read_text(encoding="utf8")
        assert 'EXPERIMENT = "CMS"' in (project / package / "tools" / "plotting.py").read_text(encoding="utf8")
        marker = projects.read_marker(project)
        assert marker["package"] == package and marker["objects"]["dsaMuons"] == "DSAMuon"
        assert projects.find_package(project) == package
        assert projects.defined_objects(project, package)["primary"][-1] == "dsaMuons"
        # what the caller is told
        assert result["channels"] == ["all", "baseline", "baseline_2muons"]
        assert result["samples"] == ["Signal"] and "--sample Signal" in result["next_steps"][0]
        wrapped = [o["name"] for o in result["objects"] if o["wrapped_as_lorentz"]]
        assert wrapped == ["dsaMuons"]
        notes = " ".join(result["notes"])
        assert "muon mass" in notes and "polar set" in notes and "boostedTau" in notes


def test_project_without_a_sample_file_assumes_standard_nanoaod():
    with tempfile.TemporaryDirectory() as tmp:
        result = _create(tmp, layout=None, objects=None)
        names = [o["name"] for o in result["objects"]]
        assert names == ["pvs", "muons", "electrons", "photons", "jets", "met", "hlt", "flags", "gens"]
        assert any("standard NanoAOD" in note for note in result["notes"])
        assert any("luminosity" in note for note in result["notes"])
        _compile_all(Path(tmp) / "demo_analysis")


def test_objects_can_replace_the_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        result = _create(tmp, objects={"jets": "Jet", "met": "MET"}, include_default_objects=False)
        assert [o["name"] for o in result["objects"]] == ["jets", "met"]
        assert result["channels"] == ["all", "baseline", "baseline_2jets"]


def test_bad_requests_are_refused_with_a_way_forward():
    with tempfile.TemporaryDirectory() as tmp:
        cases = [
            (dict(package="Bad-Name"), "package name"),
            (dict(objects={"taus": "Tauu"}), "no collection 'Tauu'"),
            (dict(objects={"my jets": "Jet"}), "object name"),
            (dict(add_components=["nope"]), "unknown component"),
            (dict(golden_json=Path(tmp) / "g.json"), "run period"),
            (dict(samples=[{"files": ["a.root"]}]), "needs a 'name'"),
            (dict(samples=[{"name": "A", "files": []}]), "no files"),
            (dict(samples=[{"name": "A", "files": ["a"], "cross_section": 1}]), "unknown key"),
            (dict(objects={}, include_default_objects=False), "no objects"),
        ]
        for index, (kwargs, expected) in enumerate(cases):
            message = _refused(scaffold.ScaffoldError, _create, tmp, name=f"case{index}", **kwargs)
            assert expected in message, (expected, message)
        # a misspelt collection gets the closest names as a suggestion
        message = _refused(scaffold.ScaffoldError, _create, tmp, name="typo",
                           objects={"dsa": "DSAMuons"})
        assert "Closest names" in message and "'DSAMuon'" in message


def test_a_directory_in_use_is_written_into_only_on_request_and_a_framework_never():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        (repo / "README.md").write_text("mine\n")
        (repo / "notes.txt").write_text("kept\n")
        message = _refused(scaffold.ScaffoldError, _create, tmp, name="repo")
        assert "not empty" in message and "overwrite=true" in message
        assert (repo / "README.md").read_text() == "mine\n"
        # on request the framework goes in, and the answer says which files it replaced
        result = _create(tmp, name="repo", overwrite=True)
        assert result["files_replaced"] == ["README.md"]
        assert any("README.md" in note and "replaced" in note for note in result["notes"])
        assert (repo / "notes.txt").read_text() == "kept\n"
        assert (repo / "README.md").read_text() != "mine\n"
        # a framework is someone's analysis: it is not written over, whatever is asked
        (repo / "repo" / "definitions" / "cuts.py").write_text("# my cuts\n")
        before = _tree(repo)
        for kwargs in ({}, {"overwrite": True}, {"overwrite": True, "package": "other_pkg"},
                       {"overwrite": True, "add_components": ["scaleout"]}):
            message = _refused(scaffold.ScaffoldError, _create, tmp, name="repo", **kwargs)
            assert "already holds the analysis framework 'repo'" in message, message
            assert "AddFrameworkComponent" in message
        assert _tree(repo) == before
        # what is left of one counts as well: its marker, or a directory of the package's name
        for leftover in (projects.MARKER, "leftover"):
            path = Path(tmp) / "leftover"
            path.mkdir(exist_ok=True)
            for child in path.iterdir():
                child.unlink() if child.is_file() else child.rmdir()
            (path / leftover).mkdir() if leftover == "leftover" else (path / leftover).write_text("{}")
            message = _refused(scaffold.ScaffoldError, _create, tmp, name="leftover", overwrite=True)
            assert "already holds" in message


def test_user_text_is_written_as_given():
    yaml = _yaml()
    with tempfile.TemporaryDirectory() as tmp:
        # a path and a title that contain the template's own placeholders
        path = "/store/analysis_pkg/__AUTHOR__/café file.root"
        result = _create(tmp, title="Search with __AUTHOR__ in analysis_pkg", author="A. Author",
                         tree_name="Friends/Events", year=2018, lumi="59830",
                         samples=[{"name": "Sig_M-500", "files": path, "is_data": "false",
                                   "xsec": "1e-05", "skim_factor": 0.5},
                                  {"name": "Data", "files": ["d.root"], "is_data": "TRUE"}])
        project, package = Path(tmp) / "demo_analysis", result["package"]
        samples = yaml.safe_load((project / package / "configs/samples/samples.yaml").read_text("utf8"))
        block = samples["main"]
        assert block["year"] == "2018" and block["samples"]["Sig_M-500"]["files"] == [path]
        assert "is_data" not in block["samples"]["Sig_M-500"]       # the text "false" is not true
        assert block["samples"]["Data"]["is_data"] is True
        xsecs = yaml.safe_load((project / package / "configs/cross_sections.yaml").read_text())
        assert xsecs == {"Sig_M-500": 1e-5} and isinstance(xsecs["Sig_M-500"], float)
        periods = yaml.safe_load((project / package / "configs/run_periods.yaml").read_text())
        assert periods == {"2018": {"lumi": 59830}}
        readme = (project / "README.md").read_text("utf8")
        assert readme.startswith("# Search with __AUTHOR__ in analysis_pkg\n")
        init = (project / package / "__init__.py").read_text()
        assert 'TREE_NAME = "Friends/Events"' in init and result["tree"] == "Friends/Events"
        assert projects.read_marker(project)["tree"] == "Friends/Events"
        notebook = json.loads((project / package / "test_notebooks/test_processor.ipynb").read_text())
        assert notebook["nbformat_minor"] == 4 and all("id" not in cell for cell in notebook["cells"])
        code = "".join("".join(cell["source"]) for cell in notebook["cells"])
        assert "treename=TREE_NAME" in code and '"Events"' not in code
        assert 'optional_objs = ["gens"]' in (project / package / "definitions/objects.py").read_text()
        assert [o["name"] for o in result["objects"] if o["optional"]] == ["gens"]


def test_bad_values_are_refused_before_anything_is_written():
    with tempfile.TemporaryDirectory() as tmp:
        sample = {"name": "S", "files": ["a.root"]}
        cases = [
            (dict(samples=[{**sample, "name": "my sample"}]), "sample name"),
            (dict(samples=[{**sample, "name": "no"}]), "yaml would read"),
            (dict(samples=[{**sample, "is_data": "maybe"}]), "true or false"),
            (dict(samples=[{**sample, "xsec": "big"}]), "xsec must be a number"),
            (dict(samples=[{**sample, "xsec": True}]), "xsec must be a number"),
            (dict(samples=[{**sample, "skim_factor": 0}]), "positive number"),
            (dict(samples=[{**sample, "files": ["ok.root", "bad\nname.root"]}]), "file name"),
            (dict(samples=[{**sample, "year": "20 18"}]), "year of sample"),
            (dict(samples="S"), "must be a list"),
            (dict(lumi=100.0), "run period"),
            (dict(lumi=-1, year="2018"), "positive number"),
            (dict(lumi=float("nan"), year="2018"), "positive number"),
            (dict(year="-2018"), "run period"),
            (dict(tree_name="my tree"), "tree name"),
            (dict(experiment="C M S"), "experiment"),
            (dict(triggers=["IsoMu24; rm -rf"]), "trigger path"),
            (dict(package="json"), "shadow"),
            (dict(objects={"muon": "Muon"}), "muon_..."),          # histogram names would collide
            (dict(objects={"jets2": "No-Such"}), "collection name"),
            (dict(objects=["Muon"]), "must map"),
        ]
        for index, (kwargs, expected) in enumerate(cases):
            message = _refused(scaffold.ScaffoldError, _create, tmp, name=f"case{index}", **kwargs)
            assert expected in message, (expected, message)
            assert not (Path(tmp) / f"case{index}").exists(), f"case {index} left files behind"


def test_writes_are_undone_together():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "kept.txt").write_bytes(b"old\r\n")
        try:
            with projects.Writes() as writes:
                writes.write_text(root / "kept.txt", "new\n")
                writes.write_text(root / "a" / "b" / "new.txt", "x")
                writes.write_text(root / "kept.txt", "newer\n")
                writes.copy(root / "kept.txt", root / "a" / "copy.txt")
                assert (root / "a" / "b" / "new.txt").read_text() == "x"
                assert (root / "a" / "copy.txt").read_text() == "newer\n"
                raise RuntimeError("stop")
        except RuntimeError:
            pass
        # the content from before the first write is back, and nothing new is left
        assert _tree(root) == {"kept.txt": b"old\r\n"}
        with projects.Writes() as writes:
            writes.write_text(root / "a" / "new.txt", "x\r\n")
            writes.copy(root / "kept.txt", root / "kept.txt")       # onto itself: nothing to do
        assert _tree(root) == {"a": None, "a/new.txt": b"x\r\n", "kept.txt": b"old\r\n"}


def test_a_failed_write_leaves_nothing_behind():
    original = projects.Writes.write_bytes
    fail_on = ["hists.py"]

    def failing(self, path, data):
        if str(path).endswith(fail_on[0]):
            raise OSError("disk full")
        return original(self, path, data)

    def must_fail(call, *args, **kwargs):
        try:
            call(*args, **kwargs)
        except OSError:
            return
        raise AssertionError("the write was meant to fail")

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "empty").mkdir()
        (Path(tmp) / "used").mkdir()
        (Path(tmp) / "used" / "README.md").write_text("mine\n")
        _create(tmp, name="ana", triggers=["IsoMu24"])
        ana = Path(tmp) / "ana"
        projects.Writes.write_bytes = failing
        try:
            # a new directory whose parents do not exist either, an empty one, and one
            # with files in it that the framework had already replaced when it failed
            for name, overwrite in (("deep/er/fresh", False), ("empty", False), ("used", True)):
                before = _tree(tmp)
                must_fail(_create, tmp, name=name, overwrite=overwrite)
                assert _tree(tmp) == before, name
            # a component that fails after it has written blocks into three files
            fail_on[0] = "selections.yaml"
            before = _tree(tmp)
            must_fail(components.apply_component, ana, "lepton_jets")
            assert _tree(tmp) == before
            # ... or at the very end, when recording what it did
            fail_on[0] = projects.MARKER
            for name in ("lepton_jets", "scaleout", "schema", "chain_report"):
                must_fail(components.apply_component, ana, name)
                assert _tree(tmp) == before, name
        finally:
            projects.Writes.write_bytes = original
        # and it all works afterwards
        _create(tmp, name="deep/er/fresh")
        components.apply_component(ana, "lepton_jets")
        _compile_all(ana)


def test_components_asked_for_with_the_scaffold():
    with tempfile.TemporaryDirectory() as tmp:
        # lepton jets need lepton sources; the framework is still written
        result = _create(tmp, objects={"jets": "Jet", "met": "MET"}, include_default_objects=False,
                         add_components=["lepton_jets", "scaleout"])
        assert result["components"] == ["scaleout"]
        failed = result["components_failed"]
        assert [f["component"] for f in failed] == ["lepton_jets"] and "sources" in failed[0]["error"]
        assert any("components_failed" in note for note in result["notes"])
        project = Path(tmp) / "demo_analysis"
        assert set(projects.read_marker(project)["components"]) == {"scaleout"}
        _compile_all(project)


def test_collections_newer_coffea_would_refuse_are_left_out_with_a_note():
    layout = nanoaod_layout.split_collections(
        [b for b in BRANCHES if b not in ("PV_x", "PV_y", "PV_z")])
    with tempfile.TemporaryDirectory() as tmp:
        result = _create(tmp, layout=layout)
        assert "pvs" not in [o["name"] for o in result["objects"]]
        assert any(note.startswith("PV has no x, y, z") and "NanoCollection" in note
                   for note in result["notes"])
        package = Path(tmp) / "demo_analysis" / "demo_analysis"
        assert "PV filter" not in (package / "definitions" / "cuts.py").read_text()
        assert "PV filter" not in (package / "configs" / "selections.yaml").read_text()
        _compile_all(Path(tmp) / "demo_analysis")
        # asked for by name it is defined, and told about once
        result = _create(tmp, name="named", layout=layout,
                         objects={"pvs": "PV", "dsaMuons": "DSAMuon"})
        assert "pvs" in [o["name"] for o in result["objects"]]
        notes = [note for note in result["notes"] if note.startswith("PV has no x, y, z")]
        assert len(notes) == 1 and "refuse to build" in notes[0]


def test_golden_json_is_copied_and_referenced():
    with tempfile.TemporaryDirectory() as tmp:
        golden = Path(tmp) / "Cert_test.json"
        golden.write_text("{}", encoding="utf8")
        _create(tmp, year="2018", lumi=10.0, golden_json=golden,
                samples=[{"name": "Data", "files": ["d.root"], "is_data": True}])
        package = Path(tmp) / "demo_analysis" / "demo_analysis"
        assert (package / "data" / "Cert_test.json").is_file()
        assert 'golden_json: "Cert_test.json"' in (package / "configs" / "run_periods.yaml").read_text()


# --------------------------------------------------------------------------- #
# blocks and components
# --------------------------------------------------------------------------- #

def test_blocks_are_added_once_and_replaced_only_on_request():
    text, action = projects.upsert_block("a = 1\n", "demo", "b = 2")
    assert action == "added" and text.count(">>> component: demo >>>") == 1
    assert text.startswith("a = 1\n\n\n# >>> component: demo >>>\nb = 2\n# <<< component: demo <<<\n")
    same, action = projects.upsert_block(text, "demo", "b = 3")
    assert action == "kept" and same == text
    new, action = projects.upsert_block(text + "c = 4\n", "demo", "b = 3", overwrite=True)
    assert action == "replaced" and "b = 3" in new and "b = 2" not in new and new.endswith("c = 4\n")
    assert new.count(">>> component: demo >>>") == 1
    other, _ = projects.upsert_block(new, "other", "d = 5")
    assert projects.has_block(other, "demo") and projects.has_block(other, "other")
    empty, action = projects.upsert_block("", "demo", "x")
    assert action == "added" and empty.startswith("# >>> component: demo >>>")


def test_blocks_are_whole_lines_and_files_keep_their_line_endings():
    # a marker that is indented, or part of another line, is not a block boundary:
    # replacing "the block" there could take the analyst's lines with it
    for damaged in ("if x:\n    # >>> component: demo >>>\n    b = 2\n    # <<< component: demo <<<\n",
                    'text = "# >>> component: demo >>>"\n',
                    "# >>> component: demo >>> and more\nb = 2\n# <<< component: demo <<<\n"):
        message = _refused(projects.BlockError, projects.upsert_block, damaged, "demo", "c", True)
        assert "damaged" in message
        _refused(projects.BlockError, projects.has_block, damaged, "demo")
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "f.py").write_bytes(b"a = 1\r\nb = 2\r\n")
        with projects.Writes() as writes:
            assert projects.edit_block(project, "f.py", "demo", "c = 3", writes) == "added"
        data = (project / "f.py").read_bytes()
        assert data == (b"a = 1\r\nb = 2\r\n\r\n\r\n# >>> component: demo >>>\r\nc = 3\r\n"
                        b"# <<< component: demo <<<\r\n")
        text = projects.read_text(project, "f.py")
        assert "\r" not in text and projects.block_lines(text, "demo") == (5, 7)
        with projects.Writes() as writes:
            assert projects.edit_block(project, "f.py", "demo", "c = 4", writes) == "kept"
            assert projects.edit_block(project, "f.py", "demo", "c = 4", writes,
                                       overwrite=True) == "replaced"
        data = (project / "f.py").read_bytes()
        assert b"c = 4\r\n" in data and b"c = 3" not in data
        assert b"\n" not in data.replace(b"\r\n", b"")


def test_names_in_use_are_read_off_the_source():
    source = '''"""hist_defs["in_a_docstring"] = 0"""
hist_defs = {"a": 1,
             "b": 2}   # hist_defs["in_a_comment"] = 3
hist_defs.update({"c": 4})
hist_defs.update(d=5)
hist_defs.setdefault("e", 6)
obj_cut_defs.setdefault("ljs", {})["nested"] = 1
hist_defs[
    "f"] = 7
hist_defs["g"]["inner"] = 8
other = {"not_ours": 0}
for name in ("x", "y"):
    hist_defs[name] = 9
'''
    assert projects.defined_keys(source, "hist_defs") == ["a", "b", "c", "d", "e", "f", "g"]
    assert projects.defined_keys(source, "obj_cut_defs") == ["ljs"]
    assert projects.defined_keys(source, "hist_defs", outside=(2, 4)) == ["d", "e", "f", "g"]
    message = _refused(projects.SourceError, projects.defined_keys, "def broken(:\n", "x", "cuts.py")
    assert "cuts.py" in message and "syntax error on line 1" in message
    yaml = _yaml()
    text = "a: &a\n  - x\n# b: in a comment\n? c\n: - *a\n2018: {}\n<<: {d: 1}\n"
    assert set(yaml.safe_load(text)) >= {"a", "c", 2018}
    assert projects.top_level_keys(text) == ["a", "c", "2018"]
    assert projects.top_level_keys(text, outside=(1, 2)) == ["c", "2018"]
    assert projects.top_level_keys("") == [] and projects.top_level_keys("# nothing\n") == []
    for broken in ("- a\n- b\n", "a: [1\n", "a: *undefined\n"):
        message = _refused(projects.SourceError, projects.top_level_keys, broken, "sel.yaml")
        assert "sel.yaml" in message


def test_every_component_applies_cleanly_and_twice():
    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp, triggers=["IsoMu24"], samples=[{"name": "Signal", "files": ["/s.root"]}])
        project = Path(tmp) / "demo_analysis"
        for name in components.COMPONENTS:
            first = components.apply_component(project, name)
            assert first["component"] == name and first["package"] == "demo_analysis"
            assert first["files_written"] or first["blocks"], name
            again = components.apply_component(project, name)
            assert not again["files_written"], (name, again["files_written"])
            assert all(action == "kept" for action in again["blocks"].values()), name
        assert set(projects.read_marker(project)["components"]) == set(components.COMPONENTS)
        assert _compile_all(project) >= 20
        for path in _text_files(project):
            assert "analysis_pkg" not in path.read_text(encoding="utf8"), path
        package = project / "demo_analysis"
        for relative in ("definitions/objects.py", "definitions/cuts.py", "definitions/hists.py",
                         "configs/selections.yaml", "configs/hist_collections.yaml"):
            assert (package / relative).read_text().count(">>> component: lepton_jets >>>") == 1
        assert (project / ".github" / "workflows" / "chain-report.yml").is_file()
        assert (project / "condor" / "lpc_condor_config").is_file()
        assert (project / "tests" / "chain_report.py").is_file()
        requirements = (project / "requirements.txt").read_text()
        assert "fastjet" in requirements and "coffea[dask]" in requirements
        assert "dask[dataframe]" in requirements
        # regenerating on request replaces blocks instead of stacking them
        redo = components.apply_component(project, "lepton_jets", {"radius": 0.2}, overwrite=True)
        assert set(redo["blocks"].values()) == {"replaced"}
        objects = (package / "definitions" / "objects.py").read_text()
        assert "LJS_RADIUS = 0.2" in objects and objects.count("LJS_RADIUS") == 2


def test_lepton_jet_blocks_agree_with_each_other():
    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp, triggers=["IsoMu24"])
        project = Path(tmp) / "demo_analysis"
        result = components.apply_component(
            project, "lepton_jets",
            {"sources": {"muons": {}, "dsaMuons": {"mass": 0.105658}, "electrons": {}},
             "isolation_jets": None, "name": "ljs"})
        assert result["options"]["sources"]["dsaMuons"] == {"mass": 0.105658}
        assert result["channel"] == "baseline_2ljs" and result["hist_collection"] == "lj_base"
        package = project / "demo_analysis"
        objects = (package / "definitions" / "objects.py").read_text()
        assert 'LJS_SOURCES = {"muons": {}, "dsaMuons": {"mass": 0.105658}, "electrons": {}}' in objects
        assert "LJS_ISOLATION_JETS = None" in objects
        cuts = (package / "definitions" / "cuts.py").read_text()
        hists = (package / "definitions" / "hists.py").read_text()
        assert "isolation" not in cuts.split(">>> component: lepton_jets >>>")[1]
        assert "lj_isolation" not in hists
        used = set(_names_in_yaml_lists((package / "configs" / "selections.yaml").read_text()))
        assert used <= _quoted_keys(cuts), used - _quoted_keys(cuts)
        listed = set(_names_in_yaml_lists((package / "configs" / "hist_collections.yaml").read_text()))
        assert listed <= _quoted_keys(hists), listed - _quoted_keys(hists)
        try:
            import yaml
        except ImportError:
            return
        selections = yaml.safe_load((package / "configs" / "selections.yaml").read_text())
        assert selections["baseline_2ljs"]["obj_cuts"]["ljs"] == ["pT > 30 GeV", "|eta| < 2.4"]
        assert selections["baseline_2ljs"]["obj_cuts"]["muons"] == ["pT > 10 GeV", "|eta| < 2.4"]
        assert selections["baseline_2ljs"]["evt_cuts"][-1] == ">=2 ljs"


def test_component_requests_are_validated():
    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp)
        project = Path(tmp) / "demo_analysis"
        cases = [
            ("nope", {}, "unknown component"),
            ("lepton_jets", {"sources": ["muons", "taus"]}, "not an object"),
            ("lepton_jets", {"sorces": ["muons"]}, "unknown option"),
            ("lepton_jets", {"isolation_jets": "fatjets"}, "isolation_jets"),
            ("lepton_jets", {"name": "muons", "sources": ["muons"]}, "both"),
            ("lepton_jets", {"carry": {"pt": 0}}, "reserved"),
            ("schema", {"nested_items": {"Muon_dsaIdxG": ["Muon_dsaMatch1idx"]}}, "not a cross-reference"),
            ("schema", {"cross_references": {"dsaIdx": "DSAMuon"}}, "not a branch name"),
            ("schema", {"constant_fields": {"DSAMuon_mass": "heavy"}}, "needs a number"),
            ("scaleout", {"workers": 3}, "unknown option"),
        ]
        cases += [
            ("lepton_jets", ["muons"], "options must be a mapping"),
            ("lepton_jets", {"sources": [{"muons": {}}]}, "list of object names"),
            ("lepton_jets", {"sources": {7: {}}}, "list of object names"),
            ("lepton_jets", {"carry": [["charge"]]}, "carry must be"),
            ("lepton_jets", {"carry": {"charge": 2 ** 40}}, "too large"),
            ("lepton_jets", {"radius": 10 ** 400}, "positive number"),
            ("schema", {"mixins": {"My_Track": "PtEtaPhiMCollection"}}, "first underscore"),
            ("schema", {"reset": "yes"}, "true or false"),
            (["scaleout"], {}, "unknown component"),
        ]
        before = _tree(project)
        for name, options, expected in cases:
            message = _refused(components.ComponentError, components.apply_component, project,
                               name, options)
            assert expected in message, (expected, message)
        assert _tree(project) == before
        message = _refused(components.ComponentError, components.apply_component, Path(tmp),
                           "scaleout")
        assert "not a scaffolded" in message


def test_schema_options_accumulate_and_hand_edits_survive():
    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp)
        project = Path(tmp) / "demo_analysis"
        schema = project / "demo_analysis" / "tools" / "schema.py"
        marker = project / projects.MARKER
        pristine = schema.read_text()
        # the plain schema reads float index branches too, and has no tables to read back
        assert "transforms.local2global = _accept_any_index_type(transforms.local2global)" in pristine
        assert components.schema_options_in(pristine) is None
        first = components.apply_component(project, "schema", {"mixins": {"boostedTau": "PtEtaPhiMCollection"}})
        assert first["files_written"] and schema.read_text() != pristine
        second = components.apply_component(project, "schema", {
            "cross_references": {"Muon_dsaMatch1idx": "DSAMuon", "Muon_dsaMatch2idx": "DSAMuon"},
            "nested_items": {"Muon_dsaIdxG": ["Muon_dsaMatch1idx", "Muon_dsaMatch2idxG"]},
            "hidden_branches": ["Jet_muEF"], "constant_fields": {"DSAMuon_mass": 0.105658},
            "hide_duplicate_momenta": False})
        text = schema.read_text()
        compile(text, "schema.py", "exec")
        assert second["files_written"] and second["applied"] is True
        assert '"boostedTau": "PtEtaPhiMCollection"' in text           # kept from the first call
        assert '"Muon_dsaIdxG": ["Muon_dsaMatch1idxG", "Muon_dsaMatch2idxG"]' in text
        assert 'HIDDEN_BRANCHES = ["Jet_muEF"]' in text and '"DSAMuon_mass": 0.105658' in text
        assert "HIDE_DUPLICATE_MOMENTA = False" in text
        assert any("2025.12" in note for note in second["notes"])
        assert "def follow(collection, index_field, target):" in text
        assert "transforms.local2global = _accept_any_index_type(transforms.local2global)" in text
        # index branches are recorded by their own name, however they were given
        assert second["options"]["nested_items"] == {
            "Muon_dsaIdxG": ["Muon_dsaMatch1idx", "Muon_dsaMatch2idx"]}
        # the file is its own record, and the project's record agrees with it
        assert components.schema_options_in(text) == second["options"]
        assert projects.read_marker(project)["components"]["schema"] == second["options"]
        assert components.render_schema(second["options"]) == text

        # the tables may be edited by hand: the next call builds on what they say now
        schema.write_text(text.replace('HIDDEN_BRANCHES = ["Jet_muEF"]', 'HIDDEN_BRANCHES = ["Jet_chEmEF"]'))
        third = components.apply_component(project, "schema", {"hidden_branches": ["Jet_neEmEF"]})
        text = schema.read_text()
        assert third["applied"] is True and 'HIDDEN_BRANCHES = ["Jet_chEmEF", "Jet_neEmEF"]' in text
        assert "Jet_muEF" not in text and '"boostedTau": "PtEtaPhiMCollection"' in text
        # the same request again changes nothing
        again = components.apply_component(project, "schema", {"hidden_branches": ["Jet_neEmEF"]})
        assert again["files_kept"] and not again["files_written"] and schema.read_text() == text

        # an edit anywhere else: the file is left alone unless overwriting is asked for,
        # and what was asked for is then neither in the file nor recorded as if it were
        schema.write_text(text + "\n# my own change\n")
        recorded = marker.read_bytes()
        fourth = components.apply_component(project, "schema", {"hidden_branches": ["Jet_muEF"]})
        assert fourth["files_kept"] and fourth["applied"] is False
        assert any("NOT APPLIED" in note for note in fourth["notes"])
        assert schema.read_text() == text + "\n# my own change\n" and marker.read_bytes() == recorded
        assert fourth["options"]["hidden_branches"] == ["Jet_chEmEF", "Jet_neEmEF"]
        fifth = components.apply_component(project, "schema", {"hidden_branches": ["Jet_muEF"]},
                                           overwrite=True)
        text = schema.read_text()
        assert fifth["files_written"] and "# my own change" not in text
        assert 'HIDDEN_BRANCHES = ["Jet_chEmEF", "Jet_neEmEF", "Jet_muEF"]' in text
        assert '"boostedTau": "PtEtaPhiMCollection"' in text           # the tables survive
        assert any("regenerated" in note for note in fifth["notes"])

        # the project's record is lost: the file still says what is in effect
        marker.unlink()
        sixth = components.apply_component(project, "schema", {"mixins": {"MyTrack": "PtEtaPhiMCandidate"}})
        text = schema.read_text()
        assert '"boostedTau": "PtEtaPhiMCollection"' in text and '"MyTrack": "PtEtaPhiMCandidate"' in text
        assert '"Muon_dsaMatch1idx": "DSAMuon"' in text
        assert any("MyTrack" in note and "PtEtaPhiMCollection" in note for note in sixth["notes"])
        assert projects.read_marker(project)["components"]["schema"] == sixth["options"]
        unknown = components.apply_component(project, "schema", {"mixins": {"Other": "MyBehaviour"}})
        assert any(note.startswith("mixin 'MyBehaviour'") and "registered" in note
                   for note in unknown["notes"])
        known = components.apply_component(project, "schema", {"mixins": {"Cands": "PFCand"}})
        assert not any(note.startswith("mixin 'PFCand'") for note in known["notes"])

        # reset starts again from nothing but what is given with it
        seventh = components.apply_component(project, "schema", {"reset": True,
                                                                 "hidden_branches": ["Jet_muEF"]})
        text = schema.read_text()
        assert "boostedTau" not in text and 'HIDDEN_BRANCHES = ["Jet_muEF"]' in text
        assert seventh["options"]["mixins"] == {} and seventh["options"]["hide_duplicate_momenta"] is True
        assert "reset" not in seventh["options"]

        # the file is gone: it is written again as recorded
        schema.unlink()
        eighth = components.apply_component(project, "schema")
        assert eighth["files_written"] and schema.read_text() == text

        # values that would not be valid python, or not what was meant, are refused
        for options, expected in (
                ({"mixins": {"My Track": "PtEtaPhiMCollection"}}, "collection name"),
                ({"mixins": ["MyTrack"]}, "must be a mapping"),
                ({"hidden_branches": ["Jet_pt; import os"]}, "branch name"),
                ({"constant_fields": {"DSAMuon_mass": float("inf")}}, "needs a number"),
                ({"constant_fields": {"DSAMuon_mass": True}}, "needs a number"),
                ({"hide_duplicate_momenta": "false"}, "true or false"),
                ({"nested_items": {"Muon_dsaIdxG": []}}, "list of index branches")):
            message = _refused(components.ComponentError, components.apply_component, project,
                               "schema", options)
            assert expected in message, (expected, message)
        assert schema.read_text() == text


def test_lepton_jet_options_are_recorded_as_applied():
    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp, triggers=["IsoMu24"])
        project = Path(tmp) / "demo_analysis"
        package = project / "demo_analysis"
        first = components.apply_component(project, "lepton_jets", {
            "sources": ["muons", "electrons"], "carry": {"charge": 0, "tightId": False, "dxy": -99.5},
            "isolation_jets": "none"})
        objects = (package / "definitions" / "objects.py").read_text()
        # python literals, not json: False and None must be readable by python
        assert 'LJS_CARRY = {"charge": 0, "tightId": False, "dxy": -99.5}' in objects
        assert "LJS_ISOLATION_JETS = None" in objects
        compile(objects, "objects.py", "exec")
        assert first["options"]["isolation_jets"] is None
        recorded = projects.read_marker(project)["components"]["lepton_jets"]
        assert recorded == first["options"]

        # the same request again is a no-op; a different one is refused, not half-recorded
        again = components.apply_component(project, "lepton_jets")
        assert set(again["blocks"].values()) == {"kept"} and again["options"] == recorded
        message = _refused(components.ComponentError, components.apply_component, project,
                           "lepton_jets", {"radius": 0.2})
        assert "already part of this framework" in message and "overwrite=true" in message
        assert projects.read_marker(project)["components"]["lepton_jets"] == recorded
        assert "LJS_RADIUS = 0.4" in (package / "definitions" / "objects.py").read_text()

        # with overwrite the new options are changes to the recorded ones
        redo = components.apply_component(project, "lepton_jets", {"radius": 0.2}, overwrite=True)
        assert redo["options"]["sources"] == {"muons": {}, "electrons": {}}
        assert redo["options"]["radius"] == 0.2 and redo["options"]["isolation_jets"] is None
        assert "LJS_RADIUS = 0.2" in (package / "definitions" / "objects.py").read_text()

        # a derived object as a source: built before the lepton jets only if defined above them
        objects_file = package / "definitions" / "objects.py"
        text = objects_file.read_text()
        marker_line = "# >>> component: lepton_jets >>>"
        objects_file.write_text(text.replace(
            marker_line, 'derived_objs.update({"tight_muons": lambda objs: objs["muons"]})\n\n\n'
            + marker_line))
        derived = components.apply_component(project, "lepton_jets", {"sources": ["tight_muons"]},
                                             overwrite=True)
        assert derived["options"]["sources"] == {"tight_muons": {}}
        assert any("tight_muons" in note and "above" in note for note in derived["notes"])
        assert 'LJS_SOURCES = {"tight_muons": {}}' in objects_file.read_text()

        # the record is lost while the blocks are there: nothing can say what they were
        # written with, so they are neither kept under new options nor silently adopted
        marker = project / projects.MARKER
        marker.unlink()
        before = _tree(project)
        for options in ({}, {"radius": 0.25}):
            message = _refused(components.ComponentError, components.apply_component, project,
                               "lepton_jets", options)
            assert "does not say which options" in message and "overwrite=true" in message
        assert _tree(project) == before
        redo = components.apply_component(project, "lepton_jets",
                                          {"sources": ["muons"], "radius": 0.25}, overwrite=True)
        assert redo["options"]["radius"] == 0.25 and redo["options"]["sources"] == {"muons": {}}
        assert "LJS_RADIUS = 0.25" in objects_file.read_text()
        assert projects.read_marker(project)["components"]["lepton_jets"] == redo["options"]

        for options, expected in (
                ({"sources": []}, "at least one source"),
                ({"sources": {"muons": {"mass": -1}}}, "cannot be negative"),
                ({"sources": {"muons": {"pt": 3}}}, "only {'mass': GeV}"),
                ({"radius": 0}, "positive number"),
                ({"radius": "wide"}, "positive number"),
                ({"invmass_max": float("inf")}, "positive number"),
                ({"carry": {"my field": 0}}, "not a field name"),
                ({"carry": {"charge": "zero"}}, "needs a number"),
                ({"name": "muons"}, "both"),
                ({"name": "jets"}, "already an object"),
                ({"name": "electron"}, "electron_..."),
                ({"name": "yes"}, "yaml")):
            message = _refused(components.ComponentError, components.apply_component, project,
                               "lepton_jets", options, True)
            assert expected in message, (expected, message)
        _compile_all(project)


def test_lepton_jets_do_not_take_over_names_that_are_in_use():
    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp, triggers=["IsoMu24"])
        project = Path(tmp) / "demo_analysis"
        package = project / "demo_analysis"
        selections = package / "configs" / "selections.yaml"
        hists = package / "definitions" / "hists.py"
        original = selections.read_text()
        selections.write_text(original + "\nbaseline_2ljs:\n  obj_cuts: {}\n  evt_cuts: []\n")
        hists.write_text(hists.read_text() + '\nhist_defs["lj_pt"] = obj_attr("muons", "pt")\n')
        before = (package / "definitions" / "objects.py").read_text()
        message = _refused(components.ComponentError, components.apply_component, project, "lepton_jets")
        assert "selection 'baseline_2ljs'" in message and "histogram 'lj_pt'" in message
        assert (package / "definitions" / "objects.py").read_text() == before      # nothing written
        assert not (package / "tools" / "lepton_jets.py").exists()
        # another name for the lepton jets avoids both
        result = components.apply_component(project, "lepton_jets", {"name": "darkjets"})
        assert result["channel"] == "baseline_2darkjets" and result["hist_collection"] == "darkjet_base"
        # what the analyst builds on the block does not get in the way of applying it again
        collections = package / "configs" / "hist_collections.yaml"
        collections.write_text(collections.read_text() + "\nmine:\n  - *darkjet_base\n")
        again = components.apply_component(project, "lepton_jets", {"name": "darkjets"})
        assert set(again["blocks"].values()) == {"kept"}
        redo = components.apply_component(project, "lepton_jets", {"name": "darkjets", "radius": 0.3},
                                          overwrite=True)
        assert set(redo["blocks"].values()) == {"replaced"}
        assert "- *darkjet_base" in collections.read_text()

    # names defined in other ways than a dict literal are seen as well
    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp, triggers=["IsoMu24"])
        project = Path(tmp) / "demo_analysis"
        package = project / "demo_analysis"
        for relative, addition in (
                ("definitions/hists.py", 'hist_defs.update(lj_n=obj_attr("muons", "n"))\n'),
                ("definitions/cuts.py", 'evt_cut_defs.setdefault(">=1 ljs", at_least("muons", 1))\n'
                                        'obj_cut_defs.setdefault("ljs", {})["x"] = pt_above(1)\n')):
            path = package / relative
            path.write_text(path.read_text() + addition)
        before = _tree(project)
        message = _refused(components.ComponentError, components.apply_component, project, "lepton_jets")
        assert "histogram 'lj_n'" in message and "event cut '>=1 ljs'" in message
        assert "object cuts for 'ljs'" in message
        # an object of that name defined with update() is found too
        objects_file = package / "definitions" / "objects.py"
        objects_file.write_text(objects_file.read_text()
                                + 'derived_objs.update({"ljs": lambda objs: objs["muons"]})\n')
        message = _refused(components.ComponentError, components.apply_component, project, "lepton_jets")
        assert "'ljs' is already an object" in message
        # a definitions file that does not parse: said plainly, and nothing is touched
        objects_file.write_text(objects_file.read_text() + "def broken(:\n")
        before = _tree(project)
        message = _refused(components.ComponentError, components.apply_component, project,
                           "lepton_jets", {"name": "other"})
        assert "objects.py" in message and "syntax error" in message and "nothing was changed" in message
        assert _tree(project) == before

    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp, triggers=["IsoMu24"])
        project = Path(tmp) / "demo_analysis"
        selections = project / "demo_analysis" / "configs" / "selections.yaml"
        # the anchors only survive in a comment: they must not be used
        text = selections.read_text().replace("_object_cuts: &object_cuts", "_object_cuts:") \
            .replace("    <<: *object_cuts\n", "").replace("_event_cuts: &event_cuts", "_event_cuts:") \
            .replace("    - *event_cuts\n", "")
        selections.write_text(text + "# the anchors were &object_cuts and &event_cuts\n")
        result = components.apply_component(project, "lepton_jets")
        block = selections.read_text().split(">>> component: lepton_jets >>>")[1]
        assert "*object_cuts" not in block and "*event_cuts" not in block
        assert any("anchors" in note for note in result["notes"])
        yaml = _yaml()
        assert yaml.safe_load(selections.read_text())["baseline_2ljs"]["evt_cuts"] == [">=2 ljs"]


def test_a_name_defined_twice_is_reported_by_the_generated_checker():
    """The check a project carries finds a definition that silently replaces another."""
    import importlib

    with tempfile.TemporaryDirectory() as tmp:
        result = _create(tmp, name="dup_analysis", add_components=["lepton_jets"])
        project = Path(tmp) / "dup_analysis"
        assert not result["components_failed"], result["components_failed"]
        package = result["package"]
        sys.path.insert(0, str(project))
        try:
            check = importlib.import_module(f"{package}.tools.check")       # needs no coffea
            # what the scaffold and a component write has every name once
            assert check._duplicate_errors() == [], check._duplicate_errors()

            find = check.duplicate_definitions
            hists = (project / package / "definitions" / "hists.py").read_text(encoding="utf8")
            cuts = (project / package / "definitions" / "cuts.py").read_text(encoding="utf8")
            tables = check.DEFINITION_TABLES
            assert find(hists, tables["hists.py"]) == [] and find(cuts, tables["cuts.py"]) == []

            # the spellings by which an existing name gets defined again
            first = hists.index('"muon_pt"')
            line = hists.count("\n", 0, first) + 1
            again = find(hists + '\nhist_defs["muon_pt"] = obj_attr("muons", "pt", xmax=500)\n',
                         tables["hists.py"])
            assert len(again) == 1 and "'muon_pt' is defined twice in hist_defs" in again[0], again
            assert f"lines {line} and " in again[0], again
            assert len(find(hists + '\nhist_defs.update({"muon_pt": None, "brand_new": None})\n',
                            tables["hists.py"])) == 1
            assert len(find(hists + '\ncounter_defs["Selected muons"] = lambda objs: 0\n',
                            tables["hists.py"])) == 1
            assert find(hists + '\nhist_defs["brand_new"] = None\nobj_labels["muons"] = "Mu"\n',
                        tables["hists.py"]) == []          # a new name; a table that is not one
            nested = find(cuts + '\nobj_cut_defs["muons"]["pT > 10 GeV"] = pt_above(11)\n',
                          tables["cuts.py"])
            assert len(nested) == 1 and "in obj_cut_defs['muons']" in nested[0], nested
            assert len(find(cuts + '\nobj_cut_defs["muons"].update({"looseId": None, "new": None})\n',
                            tables["cuts.py"])) == 1
            assert len(find(cuts + '\nevt_cut_defs.update({">=2 muons": None})\n',
                            tables["cuts.py"])) == 1
            # the same key twice inside one dict literal, wherever it is
            literal = find('x = {"a": 1, "b": {"c": 1, "c": 2}, "a": 3}\n')
            assert len(literal) == 2 and all("written twice in one dict" in m for m in literal), literal
            # starting a table afresh is not a redefinition of what was in it
            assert find('hist_defs = {"a": 1}\nhist_defs = {"a": 2}\n', ("hist_defs",)) == []
            # not followed: keys that are not plain text, and statements inside blocks
            assert find('hist_defs = {}\nfor n in "ab":\n    hist_defs[n] = 1\n'
                        'if True:\n    hist_defs["a"] = 1\nelse:\n    hist_defs["a"] = 2\n',
                        ("hist_defs",)) == []

            # in the project itself: reported as a static error, with the file named
            with open(project / package / "definitions" / "hists.py", "a", encoding="utf8") as stream:
                stream.write('\nhist_defs["electron_pt"] = obj_attr("electrons", "pt", xmax=500)\n')
            errors = check._duplicate_errors()
            assert len(errors) == 1 and errors[0].startswith("definitions/hists.py: 'electron_pt'"), errors
            assert "Rename one of the two" in errors[0]
        finally:
            sys.path.remove(str(project))
            for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
                del sys.modules[name]


def test_damaged_block_markers_are_refused_untouched():
    text = "a = 1\n# >>> component: demo >>>\nb = 2\n"
    for damaged in (text, "# <<< component: demo <<<\n" + text,
                    text + "# <<< component: demo <<<\n" + text + "# <<< component: demo <<<\n"):
        message = _refused(projects.BlockError, projects.upsert_block, damaged, "demo", "c = 3", True)
        assert "damaged" in message
    assert projects.strip_block("a\n# >>> component: demo >>>\nb\n# <<< component: demo <<<\nc\n",
                                "demo") == "a\n\nc\n"
    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp)
        project = Path(tmp) / "demo_analysis"
        package = project / "demo_analysis"
        components.apply_component(project, "lepton_jets")
        cuts = package / "definitions" / "cuts.py"
        cuts.write_text(cuts.read_text().replace("# <<< component: lepton_jets <<<\n", ""))
        snapshot = {path: path.read_text() for path in package.rglob("*") if path.suffix in (".py", ".yaml")}
        for overwrite in (False, True):
            message = _refused(components.ComponentError, components.apply_component, project,
                               "lepton_jets", {}, overwrite)
            assert "damaged" in message and "cuts.py" in message and "Repair" in message
        assert all(path.read_text() == text for path, text in snapshot.items())


def test_the_marker_cannot_point_the_tools_elsewhere():
    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp)
        project = Path(tmp) / "demo_analysis"
        marker = project / projects.MARKER
        assert projects.find_package(project) == "demo_analysis"
        outside = Path(tmp) / "elsewhere"
        for sub in ("tools", "definitions"):
            (outside / sub).mkdir(parents=True)
        (outside / "tools" / "processor.py").write_text("")
        (outside / "definitions" / "objects.py").write_text("")
        for package in ("../elsewhere", str(outside), "-c", ["demo_analysis"], 7, "demo_analysis.tools"):
            marker.write_text(json.dumps({"package": package}))
            assert projects.find_package(project) == "demo_analysis", package
        for content in ("[1, 2]", "not json", '"text"'):
            marker.write_text(content)
            assert projects.read_marker(project) is None
            assert projects.find_package(project) == "demo_analysis"
        # a component can still be added, and writes a usable marker again
        result = components.apply_component(project, "scaleout")
        assert result["package"] == "demo_analysis"
        assert projects.read_marker(project)["components"] == {"scaleout": {}}
        assert projects.find_package(Path(tmp) / "nowhere") is None


def test_chain_report_keeps_the_fixture_list():
    with tempfile.TemporaryDirectory() as tmp:
        _create(tmp)
        project = Path(tmp) / "demo_analysis"
        components.apply_component(project, "chain_report")
        fixtures = project / "tests" / "fixtures.yaml"
        fixtures.write_text("fixtures:\n  - dataset: Mine\n    file: data/Mine_200ev.root\nchunksize: 100\n")
        script = project / "tests" / "chain_report.py"
        script.write_text("# edited\n")
        result = components.apply_component(project, "chain_report", overwrite=True)
        assert "dataset: Mine" in fixtures.read_text() and "tests/fixtures.yaml" in result["files_kept"]
        assert "tests/chain_report.py" in result["files_written"] and "# edited" not in script.read_text()
        maker = (project / "tests" / "make_fixture.py").read_text()
        assert "from demo_analysis import TREE_NAME" in maker and "mktree(TREE_NAME" in maker


# --------------------------------------------------------------------------- #
# the tools
# --------------------------------------------------------------------------- #

def _tools():
    try:
        from tools.framework.check import CheckAnalysisFrameworkTool, build_command, condense
        from tools.framework.components import AddFrameworkComponentTool
        from tools.framework.scaffold import ScaffoldAnalysisFrameworkTool
    except ImportError as exc:
        _skip(f"orchestral is not installed ({exc})")
    return (ScaffoldAnalysisFrameworkTool, AddFrameworkComponentTool, CheckAnalysisFrameworkTool,
            build_command, condense)


def test_scaffold_and_component_tools():
    scaffold_tool, component_tool, _, _, _ = _tools()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        out = json.loads(scaffold_tool(base_directory=tmp, project_dir="ana", year=2018,
                                       objects={"taus": "Tau"}, components=["scaleout"])._run())
        assert out["status"] == "ok" and out["project"] == "ana" and out["package"] == "ana"
        assert "taus" in [o["name"] for o in out["objects"]] and out["components"] == ["scaleout"]
        assert out["components_failed"] == [] and out["tree"] == "Events"
        assert (Path(tmp) / "ana" / "ana" / "tools" / "scaleout.py").is_file()
        assert '"2018":' in (Path(tmp) / "ana" / "ana" / "configs" / "run_periods.yaml").read_text()
        for overwrite in (False, True):
            again = scaffold_tool(base_directory=tmp, project_dir="ana", overwrite=overwrite)._run()
            assert "Invalid Request" in again and "already holds the analysis framework" in again
        for bad in ("../escape", ".", "/abs/path"):
            assert "Access Denied" in scaffold_tool(base_directory=tmp, project_dir=bad)._run(), bad
        assert "File Not Found" in scaffold_tool(base_directory=tmp, project_dir="b",
                                                 sample_file="nope.root")._run()
        assert "Access Denied" in scaffold_tool(base_directory=tmp, project_dir="b",
                                                sample_file="../x.root")._run()
        assert "unknown component" in scaffold_tool(base_directory=tmp, project_dir="c",
                                                    components=["warp_drive"])._run()
        # an error says how to fix that error, not something about components
        refused = scaffold_tool(base_directory=tmp, project_dir="c", lumi=10.0)._run()
        assert "run period" in refused and "year" in refused and "components" not in refused.lower()
        assert not (Path(tmp) / "c").exists()
        # a component that cannot be added does not take the framework down with it
        partial = json.loads(scaffold_tool(
            base_directory=tmp, project_dir="d", objects={"jets": "Jet"},
            include_default_objects=False, components=["lepton_jets"])._run())
        assert partial["status"] == "ok" and partial["components"] == []
        assert partial["components_failed"][0]["component"] == "lepton_jets"

        # sample files: relative ones are found in the working directory and made complete
        os.makedirs(os.path.join(tmp, "data"))
        Path(tmp, "data", "a.root").write_bytes(b"")
        os.symlink(os.path.join(tmp, "data", "a.root"), os.path.join(tmp, "data", "link.root"))
        files = ["data/link.root", "data/missing.root", "/abs/b.root", "root://host//store/c.root"]
        out = json.loads(scaffold_tool(base_directory=tmp, project_dir="e",
                                       samples=[{"name": "S", "files": files, "xsec": 1}])._run())
        assert out["status"] == "ok", out
        text = (Path(tmp) / "e" / "e" / "configs" / "samples" / "samples.yaml").read_text()
        listed = re.findall(r'^\s+- "(.*)"$', text, flags=re.M)
        assert listed == [os.path.join(tmp, "data", "a.root"), os.path.join(tmp, "data", "missing.root"),
                          "/abs/b.root", "root://host//store/c.root"], listed
        assert any("missing.root was not found" in note for note in out["notes"])
        escaping = scaffold_tool(base_directory=tmp, project_dir="f",
                                 samples=[{"name": "S", "files": ["../outside.root"]}])._run()
        assert "Access Denied" in escaping and not (Path(tmp) / "f").exists()
        # a directory is not a file, and wildcards are not expanded
        for files in (["data"], [os.path.join(tmp, "data")]):
            refused = scaffold_tool(base_directory=tmp, project_dir="f",
                                    samples=[{"name": "S", "files": files}])._run()
            assert "is a directory" in refused and "add_samples" in refused
            assert not (Path(tmp) / "f").exists()
        out = json.loads(scaffold_tool(base_directory=tmp, project_dir="g", samples=[
            {"name": "S", "files": ["data/*.root", "~/somewhere/x.root"]}])._run())
        assert any("Wildcards are not expanded" in note for note in out["notes"])
        text = (Path(tmp) / "g" / "g" / "configs" / "samples" / "samples.yaml").read_text()
        assert os.path.join(os.path.expanduser("~"), "somewhere", "x.root") in text
        # requests of the wrong shape are answered, not raised
        for kwargs, expected in (({"samples": 5}, "samples must be a list"),
                                 ({"triggers": 5}, "triggers must be a list"),
                                 ({"lumi": "much", "year": "2018"}, "lumi must be a positive number")):
            refused = scaffold_tool(base_directory=tmp, project_dir="h", **kwargs)._run()
            assert "Invalid Request" in refused and expected in refused, refused
            assert not (Path(tmp) / "h").exists()

        added = json.loads(component_tool(base_directory=tmp, project_dir="ana",
                                          component="chain_report")._run())
        assert added["status"] == "ok" and "tests/chain_report.py" in added["files_written"]
        assert "unknown component" in component_tool(base_directory=tmp, project_dir="ana",
                                                     component="nope")._run()
        assert "Project Not Found" in component_tool(base_directory=tmp, project_dir="missing",
                                                     component="scaleout")._run()
        os.makedirs(os.path.join(tmp, "empty"))
        assert "not a scaffolded" in component_tool(base_directory=tmp, project_dir="empty",
                                                    component="scaleout")._run()

    # where the framework is written must really be inside the working directory
    with tempfile.TemporaryDirectory() as tmp:
        tmp = os.path.realpath(tmp)
        base, outside = os.path.join(tmp, "work"), os.path.join(tmp, "outside")
        os.makedirs(base)
        os.makedirs(outside)
        os.symlink(outside, os.path.join(base, "link"))
        calls = [scaffold_tool(base_directory=base, project_dir="link/ana"),
                 scaffold_tool(base_directory=base, project_dir="link"),
                 component_tool(base_directory=base, project_dir="link/ana", component="scaleout")]
        for call in calls:
            assert "Access Denied" in call._run()
        assert os.listdir(outside) == []
        # data is another matter: a file linked into the working directory is read through
        # the link, and registered where it really is
        Path(outside, "f.root").write_bytes(b"")
        out = json.loads(scaffold_tool(base_directory=base, project_dir="ana",
                                       samples=[{"name": "S", "files": ["link/f.root"]}])._run())
        assert out["status"] == "ok"
        text = (Path(base) / "ana" / "ana" / "configs" / "samples" / "samples.yaml").read_text()
        assert f'- "{os.path.join(outside, "f.root")}"' in text
        # a project that is itself a link to somewhere outside is not touched either
        os.rename(os.path.join(base, "ana"), os.path.join(outside, "moved"))
        os.symlink(os.path.join(outside, "moved"), os.path.join(base, "ana"))
        before = _tree(outside)
        assert "Access Denied" in component_tool(base_directory=base, project_dir="ana",
                                                 component="scaleout")._run()
        assert _tree(outside) == before


def test_check_tool_requests_and_report_handling():
    _, _, check_tool, build_command, condense = _tools()
    from tools.framework import check as check_module

    cmd = build_command("py", "pkg", "/tmp/r.json", sample="S", channels=["a", "b"],
                        hist_collections=[], max_events=50, strict=False)
    assert cmd == ["py", "-m", "pkg.tools.check", "--json", "/tmp/r.json", "--max-events", "50",
                   "--sample", "S", "--channels", "a", "b", "--hists", "--no-strict"]
    cmd = build_command("py", "pkg", "r.json", sample="S", tag="v2", sample_config="signal.yaml")
    assert cmd[-6:] == ["--sample", "S", "--tag", "v2", "--location-cfg", "signal.yaml"]
    # several samples in one call: a simulated and a data sample, say
    cmd = build_command("py", "pkg", "r.json", sample=["Sig", "Data"], channels=["a"])
    assert cmd[-5:] == ["--sample", "Sig", "Data", "--channels", "a"]
    assert "--sample" not in build_command("py", "pkg", "r.json", sample=[])
    cmd = build_command("py", "pkg", "r.json", sample_file="/d/file.root", is_data=True, year="2018")
    assert cmd[-7:] == ["--file", "/d/file.root", "--dataset", "file", "--data", "--year", "2018"]
    assert "--hists" not in cmd
    # a file name must not be able to pass for an option either
    cmd = build_command("py", "pkg", "r.json", sample_file="/d/--no-strict.root")
    assert cmd[-4:] == ["--file", "/d/--no-strict.root", "--dataset", "no-strict"]
    assert check_module.option_like(["S", None, "-x", 2018, "--json"]) == ["-x", "--json"]
    assert check_module.missing_modules({"versions": {"coffea": None, "awkward": "2.8", "uproot": None,
                                                      "hist": "2.9", "numpy": None}}) == ["coffea", "uproot"]
    assert check_module.missing_modules({}) == []
    # more channels than are shown, in an order that is not the alphabet's: the report
    # file is written with sorted keys, the list of channels keeps the config's order
    many = [f"ch{i:02d}" for i in range(44, 0, -1)] + ["k", "b"]
    report = {
        "ok": False, "versions": {"coffea": "x"},
        "static": {"errors": ["e"] * 100, "warnings": [],
                   "inventory": {"channels": many, "hist_collections": {"base": 3}, "n_hists": 3,
                                 "primary_objects": ["muons", "gens"], "derived_objects": [],
                                 "optional_objects": ["gens"],
                                 "selections": {name: {"obj_cuts": {"muons": [f"cut {name}"]},
                                                       "evt_cuts": [">=1 muons"]}
                                                for name in sorted(many)},
                                 "samples": {"S": {"config": "samples.yaml", "tag": "main",
                                                   "is_data": False, "year": "2018", "n_files": 2},
                                             "A": {}, "D": {"is_data": True, "year": "", "n_files": 0}},
                                 "run_periods": {"2018": {"lumi": 59830, "golden_json": None}},
                                 "unused": {"hists": ["h1", "h2"]}}},
        "run": {"ok": True, "seconds": 1.0, "max_events": 2000, "error": None, "datasets": {"S": {
            "n_events": 10, "is_data": [False], "lumixs_weight": 2.0, "n_hists": 3, "empty_hists": [],
            "empty_in_channels": {"h3": ["b"]},
            "year": ["2018"], "scaled_sum_weights": 5.0,
            "warnings": ["w"], "n_removed_golden_json": 4, "unavailable_objects": ["gens"],
            "counters": {"a": {"Selected muons": 12.0}},
            "cutflow": {"a": [{"cut": "None", "raw": 10, "weighted": 20.0, "not_applied": 0},
                              {"cut": "c", "raw": 10, "weighted": 20.0, "not_applied": 1},
                              {"cut": "d", "raw": 3, "weighted": 3.123456789e-05, "not_applied": 0},
                              {"cut": "e", "raw": 2, "weighted": 123456789.123, "not_applied": 0}]}}}},
    }
    short = condense(report)
    assert len(short["static"]["errors"]) == 61 and short["static"]["errors"][-1] == "... and 40 more"
    assert short["static"]["n_unused"] == {"hists": 2}
    # the samples as configured: by name when none was asked about, those asked about first
    assert short["static"]["n_samples"] == 3 and list(short["static"]["samples"]) == ["A", "D", "S"]
    assert short["static"]["samples"]["S"] == {"is_data": False, "year": "2018", "n_files": 2}
    assert short["static"]["samples"]["A"] == {}
    assert list(condense(report, None, ["S", "not a sample"])["static"]["samples"]) == ["S", "A", "D"]
    assert short["run"]["max_events"] == 2000
    # what the run periods are configured with, and how many files the sample has
    assert short["static"]["run_periods"] == {"2018": {"lumi": 59830, "golden_json": None}}
    assert short["run"]["datasets"]["S"]["files_in_sample"] == 2
    # a histogram no collection lists is never filled and nothing warns: it is named
    assert short["static"]["unused_hists"] == ["h1", "h2"]
    assert short["static"]["optional_objects"] == ["gens"]
    # the cuts each channel resolves to: of every channel when none was asked about (up
    # to a limit, in the order of the config), of exactly the ones that were otherwise
    assert list(short["static"]["selections"]) == many[:40]
    assert short["static"]["n_selections_not_shown"] == 6
    asked = condense(report, ["k", "b", "not a channel"])["static"]
    assert asked["selections"] == {
        "k": {"obj_cuts": {"muons": ["cut k"]}, "evt_cuts": [">=1 muons"]},
        "b": {"obj_cuts": {"muons": ["cut b"]}, "evt_cuts": [">=1 muons"]}}
    assert asked["n_selections_not_shown"] == 44
    older = condense({"ok": True, "static": {"inventory": {"channels": ["a"]}}})["static"]
    assert older["selections"] == {} and older["unused_hists"] == []      # a report without them
    assert older["samples"] == {} and older["n_samples"] == 0 and older["run_periods"] == {}
    dataset = short["run"]["datasets"]["S"]
    assert dataset["empty_in_channels"] == {"h3": ["b"]}
    # six significant digits: a small yield is not rounded away
    assert dataset["cutflow"]["a"] == [["None", 10, 20.0], ["c", 10, 20.0, "not applied"],
                                       ["d", 3, 3.12346e-05], ["e", 2, 123457000.0]]
    # what the normalisation was made of is kept
    assert dataset["year"] == ["2018"] and dataset["scaled_sum_weights"] == 5.0
    assert dataset["lumixs_weight"] == 2.0
    assert dataset["n_removed_golden_json"] == 4 and dataset["unavailable_objects"] == ["gens"]
    assert dataset["counters"] == {"a": {"Selected muons": 12.0}}
    assert "error" not in short["run"] and "traceback" not in short["run"]
    failed = condense({"ok": False, "static": {}, "run": {
        "ok": False, "seconds": 0.1, "datasets": {},
        "error": "Exception: Failed processing file\n  caused by RuntimeError: [a] event cut 'c' could not be evaluated",
        "traceback": "\n".join(f"line {i}" for i in range(100))}})
    assert "event cut 'c'" in failed["run"]["error"]                    # the chain is kept whole
    assert failed["run"]["traceback"].splitlines() == [f"line {i}" for i in range(80, 100)]
    with tempfile.TemporaryDirectory() as tmp:
        assert "Project Not Found" in check_tool(base_directory=tmp, project_dir="nope")._run()
        os.makedirs(os.path.join(tmp, "plain"))
        assert "Not A Framework" in check_tool(base_directory=tmp, project_dir="plain")._run()
        _create(tmp, name="ana")
        assert "not both" in check_tool(base_directory=tmp, project_dir="ana", sample="S",
                                        sample_file="f.root")._run()
        assert "not both" in check_tool(base_directory=tmp, project_dir="ana", sample=["S", "T"],
                                        sample_file="f.root")._run()
        assert "File Not Found" in check_tool(base_directory=tmp, project_dir="ana",
                                              sample_file="f.root")._run()
        assert "Interpreter Not Found" in check_tool(base_directory=tmp, project_dir="ana",
                                                     venv="ana/.venv")._run()
        assert "analysis_python does not exist" in check_tool(
            base_directory=tmp, project_dir="ana", analysis_python="/no/such/python")._run()
        assert "at least 1" in check_tool(base_directory=tmp, project_dir="ana", max_events=0)._run()
        # names go to the checker's command line: none may pass for an option
        for kwargs in ({"sample": "--json"}, {"sample": ["S", "--json"]},
                       {"sample": "S", "channels": ["baseline", "-x"]},
                       {"hist_collections": ["--no-strict"]}, {"sample": "S", "tag": "-t"},
                       {"year": "-2018"}, {"channels": "-x"}):
            out = check_tool(base_directory=tmp, project_dir="ana", **kwargs)._run()
            assert "cannot start with '-'" in out, (kwargs, out)
        # the sample config is a file of the framework, by name: never a path
        for name in ("--file", "../x.yaml", "/abs/x.yaml", "sub/x.yaml", ".."):
            out = check_tool(base_directory=tmp, project_dir="ana", sample="S", sample_config=name)._run()
            assert "Invalid Request" in out and "not a path" in out, (name, out)
        # values of the wrong kind are a mistake in the request, and are said to be
        for kwargs, expected in (({"max_events": "many"}, "max_events must be a whole number"),
                                 ({"max_events": True}, "max_events must be a whole number"),
                                 ({"timeout_s": None}, "timeout_s must be a whole number"),
                                 ({"sample": 5}, "sample must be a list of names"),
                                 ({"sample": ["S", ["T"]]}, "sample must be a list of names"),
                                 ({"channels": [["a"]]}, "channels must be a list of names"),
                                 ({"hist_collections": 3}, "hist_collections must be a list"),
                                 ({"year": 20.18}, "year must be"),
                                 ({"strict": "no"}, "strict must be true or false")):
            out = check_tool(base_directory=tmp, project_dir="ana", **kwargs)._run()
            assert "Invalid Request" in out and expected in out, (kwargs, out)
        leftovers = set(os.listdir(tempfile.gettempdir()))
        # a single name is a list of one name, not of its letters
        out = check_tool(base_directory=tmp, project_dir="ana", channels="baseline",
                         hist_collections="base", timeout_s=600)._run()
        assert "--channels baseline --hists base" in out, out
        assert not {name for name in set(os.listdir(tempfile.gettempdir())) - leftovers
                    if name.startswith("framework_check_")}
        # an interpreter given relative to where the tool runs still works from the project
        here = os.getcwd()
        os.chdir(os.path.dirname(sys.executable))
        try:
            out = check_tool(base_directory=tmp, project_dir="ana", timeout_s=600,
                             analysis_python=os.path.basename(sys.executable))._run()
        finally:
            os.chdir(here)
        assert "Execution Failed" not in out and "does not exist" not in out, out
        # a project behind a link that leads out of the working directory is not run
        os.symlink(tempfile.gettempdir(), os.path.join(tmp, "elsewhere"))
        assert "Project Not Found" in check_tool(base_directory=tmp, project_dir="elsewhere")._run()
        # a marker pointing somewhere else does not decide what gets run
        marker = Path(tmp) / "ana" / projects.MARKER
        marker.write_text(json.dumps({"package": "../../somewhere"}))
        # the checker itself, in whatever environment this is (with time for a first import)
        out = check_tool(base_directory=tmp, project_dir="ana", timeout_s=600)._run()
        try:
            for module in check_module.NEEDED_MODULES:
                __import__(module)
        except ImportError:
            assert "Missing Dependency" in out and "cannot import" in out, out
        else:
            answer = json.loads(out)
            assert answer["status"] == "ok" and answer["ok"] is True, out
            assert "-m ana.tools.check" in answer["command"]


def test_launcher_edits_the_harness_configs():
    sys.path.insert(0, str(REPO_ROOT / "examples" / "shared"))
    try:
        import harness_launch as launcher
    finally:
        sys.path.pop(0)
    said = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(said):
        sandbox = Path(tmp)
        # Claude Code and OpenCode: json, other entries untouched, applying twice is the same
        mcp = sandbox / ".mcp.json"
        mcp.write_text(json.dumps({"mcpServers": {
            "toolbase": {"type": "stdio", "command": "toolbase", "args": ["serve"]},
            "other": {"command": "x", "args": []}}}))
        opencode = sandbox / "opencode.json"
        opencode.write_text(json.dumps({"$schema": "https://opencode.ai/config.json", "mcp": {
            "toolbase": {"type": "local", "command": ["toolbase", "serve"], "enabled": True}}}))
        for _ in range(2):
            launcher._set_call_timeout("claude-code", sandbox, 600)
            launcher._set_call_timeout("opencode", sandbox, 600)
        servers = json.loads(mcp.read_text())["mcpServers"]
        assert servers["toolbase"]["args"] == ["serve", "--call-timeout", "600"]
        assert servers["other"] == {"command": "x", "args": []}
        entry = json.loads(opencode.read_text())["mcp"]["toolbase"]
        assert entry["command"] == ["toolbase", "serve", "--call-timeout", "600"] and entry["enabled"]

        # Codex: the table tb connect writes, among others
        codex = sandbox / ".codex" / "config.toml"
        codex.parent.mkdir()
        codex.write_text('model = "x"\n\n[mcp_servers.toolbase]\ncommand = "toolbase"\n'
                         'args = ["serve"]\n\n[mcp_servers.other]\ncommand = "y"\n')
        for _ in range(2):
            launcher._codex_startup_timeout(sandbox, 180)
            launcher._set_call_timeout("codex", sandbox, 600)
        text = codex.read_text()
        assert text.count("[mcp_servers.toolbase]") == 1 and text.count("startup_timeout_sec") == 1
        assert 'args = ["serve", "--call-timeout", "600"]' in text and text.count("tool_timeout_sec = 600") == 1
        assert text.index("tool_timeout_sec") < text.index("[mcp_servers.other]")
        if launcher.tomllib is not None:
            parsed = launcher.tomllib.loads(text)
            assert parsed["mcp_servers"]["toolbase"] == {
                "command": "toolbase", "args": ["serve", "--call-timeout", "600"],
                "startup_timeout_sec": 180, "tool_timeout_sec": 600}
            assert parsed["mcp_servers"]["other"] == {"command": "y"} and parsed["model"] == "x"
        assert "NOTE" not in said.getvalue()

        # anything unexpected is left exactly as it was, with a note instead of a guess:
        # a second table of the same name would make the whole file unreadable
        unexpected = ['[mcp_servers]\ntoolbase = { command = "toolbase", args = ["serve"] }\n',
                      "not toml at all ["]
        if launcher.tomllib is not None:
            unexpected.append('[mcp_servers.toolbase]\nargs = ["serve"]\n\n[broken\n')
        for content in unexpected:
            codex.write_text(content)
            launcher._codex_startup_timeout(sandbox, 180)
            launcher._set_call_timeout("codex", sandbox, 600)
            assert codex.read_text() == content, content
        no_args = '[mcp_servers.toolbase]\ncommand = "toolbase"\n'
        codex.write_text(no_args)
        launcher._set_call_timeout("codex", sandbox, 600)
        assert codex.read_text() == no_args
        mcp.write_text("{broken")
        launcher._set_call_timeout("claude-code", sandbox, 600)
        assert mcp.read_text() == "{broken"
        assert said.getvalue().count("NOTE: could not") >= 2 * len(unexpected) + 2

        # the newest installed binary is the one most recently written, whatever its name
        home = sandbox / "home"
        old = home / ".vscode-server/extensions/anthropic.claude-code-1.9.0/resources/native-binary/claude"
        new = home / ".vscode-server/extensions/anthropic.claude-code-1.10.0/resources/native-binary/claude"
        for path, age in ((old, 2000), (new, 1000)):
            path.parent.mkdir(parents=True)
            path.write_text("#!/bin/sh\n")
            path.chmod(0o755)
            os.utime(path, (os.path.getmtime(path) - age,) * 2)
        saved = {key: os.environ.get(key) for key in ("HOME", "PATH")}
        os.environ.update(HOME=str(home), PATH=str(sandbox / "empty"))
        try:
            assert launcher._resolve_harness_command("claude") == str(new)
            assert launcher._resolve_harness_command("no-such-harness") is None
            assert launcher._toolkit_python() is None
            cache = home / ".toolbase" / "cache" / "heptapod" / "2.3.0"
            cache.mkdir(parents=True)
            (cache / ".install_meta.yaml").write_text(f"name: heptapod\npython_path: {sys.executable}\n")
            assert launcher._toolkit_python() == sys.executable
            launcher._warm_imports(["json", "os"])
            launcher._warm_imports(["no_such_module_at_all"])
            # a second interpreter (the one frameworks are checked with) is warmed as well,
            # and the toolkit's own only once
            other = str(sandbox / "no" / "such" / "python")
            launcher._warm_imports(["json"], [sys.executable, other])
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        assert "warmed the import cache of the toolkit's python (json, os)" in said.getvalue()
        assert "the toolkit's python cannot `import no_such_module_at_all`" in said.getvalue()
        assert said.getvalue().count("warmed the import cache of the toolkit's python (json)") == 1
        assert f"could not warm the import cache of {other}" in said.getvalue()

        # a config that is not text is left alone as well
        codex.write_bytes(b"\xff\xfe[mcp_servers.toolbase]\n")
        launcher._codex_startup_timeout(sandbox, 180)
        assert codex.read_bytes() == b"\xff\xfe[mcp_servers.toolbase]\n"


if __name__ == "__main__":
    no_skips = "--no-skips" in sys.argv[1:]
    passed, skipped, failed = 0, 0, 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"[ok] {name}")
            except _Skipped as reason:
                skipped += 1
                print(f"[skip] {name}: {reason}")
            except Exception:
                import traceback
                failed += 1
                print(f"[FAIL] {name}")
                traceback.print_exc()
    print(f"framework tests: {passed} passed, {skipped} skipped, {failed} failed")
    if skipped and no_skips:
        print("--no-skips: a skipped test counts as a failure")
    sys.exit(1 if failed or (skipped and no_skips) else 0)
