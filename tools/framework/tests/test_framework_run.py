"""Tests that run a generated framework for real.

They need coffea, awkward, uproot and hist (plus fastjet for the lepton-jet test and
dask for the scale-out test) and are skipped when those are missing. Every test
scaffolds a project from a synthetic NanoAOD-like file whose content is known exactly
(see synthetic.py), runs the generated code, and compares with what the file was built
to contain. A pass therefore means the engine does what it claims, not merely that it
executes.

Run with:  python tools/framework/tests/test_framework_run.py --no-skips
      or:  pytest tools/framework/tests/test_framework_run.py -x -q -rs

Without --no-skips (or -rs, which lists the skips) a run in an environment that lacks
the libraries reports success having tested nothing.
"""
import atexit
import copy
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.analysis import nanoaod_layout  # noqa: E402
from tools.framework import _components as components  # noqa: E402
from tools.framework import _scaffold as scaffold  # noqa: E402

N_EVENTS = 400
LUMI = 1000.0      # /pb
XSEC = 2.0         # pb


class _Skipped(Exception):
    pass


def _skip(reason):
    """Skip properly under pytest; say so when run as a script."""
    if "pytest" in sys.modules:
        import pytest
        pytest.skip(reason)
    raise _Skipped(reason)


def _need(*modules):
    for module in modules:
        try:
            importlib.import_module(module)
        except ImportError as exc:
            _skip(f"{module} is not installed ({exc})")


_WORKDIR = Path(tempfile.mkdtemp(prefix="framework_run_"))
atexit.register(shutil.rmtree, str(_WORKDIR), ignore_errors=True)
_PROJECTS = {}


def _project(name, data=False, add=(), **kwargs):
    """Scaffold (once per name) a project around a synthetic file; return its handle."""
    _need("coffea", "awkward", "uproot", "hist", "yaml")
    if name in _PROJECTS:
        return _PROJECTS[name]
    from tools.framework.tests import synthetic

    base = _WORKDIR / name
    base.mkdir()
    sample_file = base / ("data.root" if data else "signal.root")
    truth = synthetic.write_nanoaod(str(sample_file), n=N_EVENTS, data=data)
    # An analysis is laid out from simulation and then also run on data: the objects
    # (generator-level ones included) come from a simulated file either way.
    layout_file = sample_file
    if data:
        layout_file = base / "layout_from.root"
        synthetic.write_nanoaod(str(layout_file), n=20, data=False)
    branches = nanoaod_layout.read_tree_layout(str(layout_file))["branches"]
    # a TTree with NanoAOD's counters, not an RNTuple or a tree of structs
    assert "nMuon" in branches and "Muon_pt" in branches and "GenPart_px" in branches, branches[:20]
    layout = nanoaod_layout.split_collections(branches)
    sample = {"name": "Data", "files": [str(sample_file)], "is_data": True} if data else \
        {"name": "Signal", "files": [str(sample_file)], "xsec": XSEC}
    sample.update(kwargs.pop("sample", {}))
    package = f"fw_{name}"
    project = base / "analysis"
    result = scaffold.create_project(
        project, package=package, layout=layout, objects={"dsaMuons": "DSAMuon"},
        triggers=["HLT_IsoMu24"], year="2018", lumi=LUMI, samples=[sample], **kwargs)
    assert not result["components_failed"], result["components_failed"]
    for component, options in add:
        components.apply_component(project, component, options)
    sys.path.insert(0, str(project))
    # worker processes of local executors must find the package too
    os.environ["PYTHONPATH"] = str(project) + os.pathsep + os.environ.get("PYTHONPATH", "")
    handle = {"project": project, "package": package, "truth": truth, "file": str(sample_file),
              "sample": sample["name"], "result": result, "base": base}
    _PROJECTS[name] = handle
    return handle


def _module(handle, name):
    if name == "__init__":
        return importlib.import_module(handle["package"])
    return importlib.import_module(f"{handle['package']}.{name}")


def _run(handle, channels, hists=("base",), chunksize=150, executor=None, strict=True, fileset=None,
         skipbadfiles=False, **processor_options):
    from coffea import processor

    utilities = _module(handle, "tools.utilities")
    proc_module = _module(handle, "tools.processor")
    schema = _module(handle, "tools.schema")
    fileset = fileset or utilities.make_fileset([handle["sample"]])
    runner = processor.Runner(
        executor=executor or processor.IterativeExecutor(status=False),
        schema=schema.AnalysisSchema, chunksize=chunksize, skipbadfiles=skipbadfiles,
        metadata_cache={})
    proc = proc_module.AnalysisProcessor(list(channels), list(hists), strict=strict,
                                         **processor_options)
    return runner(fileset, processor_instance=proc, treename=_module(handle, "__init__").TREE_NAME)


def _append(handle, relative, text):
    """Add to a definitions or config file of a project and reload what read it."""
    with open(handle["project"] / handle["package"] / relative, "a", encoding="utf8") as stream:
        stream.write(text)


def _reload(handle, *modules):
    for module in modules or ("definitions.cuts", "definitions.hists", "tools.selection",
                              "tools.processor"):
        importlib.reload(_module(handle, module))


def _must_stop(call, *args, **kwargs):
    """Run something that has to raise, and return the whole chain of errors as text."""
    try:
        call(*args, **kwargs)
    except Exception as exc:
        return _chain_text(exc)
    raise AssertionError("the run was meant to stop")


def _rows(output, channel):
    return [(cut, vals["raw"]) for cut, vals in output["cutflow"][channel].rows.items()]


def _hist(output, name, channel):
    return output["hists"][name][{"channel": channel}]


def _mean(histogram):
    values, centers = histogram.values(), histogram.axes[0].centers
    return float((values * centers).sum() / values.sum())


def _failures(output):
    return sorted(w for w in output["warnings"] if "could not be" in w or "failed" in w)


def _close(a, b, rel=1e-5):
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-12)


def _chain_text(exc):
    parts = []
    while exc is not None and len(parts) < 10:
        parts.append(f"{type(exc).__name__}: {exc}")
        exc = exc.__cause__ or exc.__context__
    return " <- ".join(parts)


# --------------------------------------------------------------------------- #
# the core engine
# --------------------------------------------------------------------------- #

def test_static_check_passes_on_a_fresh_project():
    handle = _project("static")
    report = _module(handle, "tools.check").static_report()
    assert report["errors"] == [], report["errors"]
    assert report["warnings"] == [], report["warnings"]
    inventory = report["inventory"]
    assert inventory["channels"] == ["all", "baseline", "baseline_2muons"]
    assert "dsaMuons" in inventory["primary_objects"] and inventory["samples"]["Signal"]["n_files"] == 1
    assert inventory["optional_objects"] == ["gens"]
    assert inventory["run_periods"] == {"2018": {"lumi": LUMI, "golden_json": None}}
    assert _module(handle, "__init__").TREE_NAME == "Events"
    # what each channel applies, with the yaml anchors and merges resolved
    selections = inventory["selections"]
    assert selections["all"] == {"obj_cuts": {}, "evt_cuts": []}
    assert selections["baseline"]["evt_cuts"] == ["pass triggers", "PV filter"]
    assert selections["baseline_2muons"]["evt_cuts"] == ["pass triggers", "PV filter", ">=2 muons"]
    assert selections["baseline"]["obj_cuts"]["muons"] == ["pT > 10 GeV", "|eta| < 2.4"]
    assert inventory["unused"]["hists"] == []           # every histogram is in a collection


def test_processor_counts_what_the_file_contains():
    handle = _project("counts")
    truth = handle["truth"]
    out = _run(handle, ["all", "baseline", "baseline_2muons"], chunksize=150)["Signal"]
    assert _failures(out) == [], out["warnings"]

    meta = out["metadata"]
    assert meta["n_evts"] == N_EVENTS and meta["is_data"] == {False} and meta["year"] == {"2018"}
    assert _close(meta["scaled_sum_weights"], truth["sum_gen_weights"]), meta
    assert _close(meta["lumixs_weight"], LUMI * XSEC / truth["sum_gen_weights"]), meta

    # event cuts, in order, against the numbers the file was built with
    assert _rows(out, "all") == [("None", N_EVENTS)]
    assert _rows(out, "baseline") == [
        ("None", N_EVENTS), ("pass triggers", truth["n_pass_trigger"]),
        ("PV filter", truth["n_pass_trigger_and_pv"])]
    # every event has its two signal muons above the baseline cuts
    assert _rows(out, "baseline_2muons")[-1] == (">=2 muons", truth["n_pass_trigger_and_pv"])
    for channel in ("baseline", "baseline_2muons"):
        assert all(row["not_applied"] == 0 for row in out["cutflow"][channel].rows.values()), channel
    assert meta["unavailable_objects"] == set()
    # simulation is scaled to lumi * xs
    assert _close(out["cutflow"]["all"].rows["None"]["weighted"], LUMI * XSEC)

    # object cuts: the soft extra muons are removed, leaving exactly two per event
    muon_n = _hist(out, "muon_n", "baseline")
    assert _close(muon_n.values()[2], muon_n.values().sum()), muon_n.values()
    loose = _hist(out, "muon_n", "all").values()
    assert loose[3:].sum() > 0, "the uncut channel must still see the soft muons"
    # histograms are filled with the surviving events' weights
    final = out["cutflow"]["baseline_2muons"].rows[">=2 muons"]["weighted"]
    assert final > 0
    assert _close(_hist(out, "muon_muon_invmass", "baseline_2muons").sum(flow=True).value, final, 1e-4)
    assert _close(_hist(out, "met_pt", "baseline_2muons").sum(flow=True).value, final, 1e-4)
    # one-per-event records and the re-zipped custom collection are usable
    assert _hist(out, "pv_n", "all").sum(flow=True).value > 0
    assert _hist(out, "dsaMuon_pt", "all").sum(flow=True).value > 0
    assert out["counters"]["baseline"]["Selected muons"] == 2 * truth["n_pass_trigger_and_pv"]


def test_chunking_and_executor_do_not_change_the_answer():
    handle = _project("chunks")
    from coffea import processor

    channels = ["baseline", "baseline_2muons"]
    one = _run(handle, channels, chunksize=100000)["Signal"]
    many = _run(handle, channels, chunksize=97)["Signal"]
    futures = _run(handle, channels, chunksize=97,
                   executor=processor.FuturesExecutor(workers=2, status=False))["Signal"]
    for other in (many, futures):
        for channel in channels:
            assert _rows(other, channel) == _rows(one, channel)
            for cut, vals in one["cutflow"][channel].rows.items():
                assert _close(other["cutflow"][channel].rows[cut]["weighted"], vals["weighted"], 1e-9)
        for name in one["hists"]:
            a, b = one["hists"][name].values(flow=True), other["hists"][name].values(flow=True)
            assert a.shape == b.shape and abs(a - b).max() <= 1e-6 * max(1.0, abs(a).max()), name
        assert _close(other["metadata"]["scaled_sum_weights"], one["metadata"]["scaled_sum_weights"], 1e-9)
        assert other["warnings"] == one["warnings"]


def test_postprocess_scales_once():
    handle = _project("scale")
    out = _run(handle, ["all"], hists=["muon_base"])
    proc = _module(handle, "tools.processor").AnalysisProcessor(["all"], ["muon_base"])
    before = out["Signal"]["cutflow"]["all"].rows["None"]["weighted"]
    assert _close(before, LUMI * XSEC), before                    # scaled once by the run itself
    hist_before = _hist(out["Signal"], "muon_pt", "all").sum(flow=True).value
    assert hist_before > 0
    proc.postprocess(out)
    assert out["Signal"]["cutflow"]["all"].rows["None"]["weighted"] == before
    assert _hist(out["Signal"], "muon_pt", "all").sum(flow=True).value == hist_before


def test_outputs_of_separate_runs_merge_to_the_single_run_result():
    handle = _project("merge")
    from coffea.processor import accumulate

    from tools.framework.tests import synthetic

    utilities = _module(handle, "tools.utilities")
    second = handle["base"] / "signal_2.root"
    synthetic.write_nanoaod(str(second), n=150, seed=11)

    def fileset(*files):
        return {"Signal": {"files": [str(f) for f in files],
                           "metadata": {"is_data": False, "year": "2018", "skim_factor": 1.0}}}

    channels = ["baseline", "baseline_2muons"]
    full = _run(handle, channels, fileset=fileset(handle["file"], second))
    part_a = _run(handle, channels, fileset=fileset(handle["file"]))
    part_b = _run(handle, channels, fileset=fileset(second))
    weight_a = part_a["Signal"]["metadata"]["lumixs_weight"]

    def same(output, what):
        for channel in channels:
            assert _rows(output["Signal"], channel) == _rows(full["Signal"], channel), what
            for cut, vals in full["Signal"]["cutflow"][channel].rows.items():
                other = output["Signal"]["cutflow"][channel].rows[cut]["weighted"]
                assert _close(other, vals["weighted"], 1e-9), (what, channel, cut)
        for name, reference in full["Signal"]["hists"].items():
            a, b = reference.values(flow=True), output["Signal"]["hists"][name].values(flow=True)
            assert abs(a - b).max() <= 1e-9 * max(1.0, abs(a).max()), (what, name)
        meta = output["Signal"]["metadata"]
        assert meta["n_evts"] == N_EVENTS + 150
        assert _close(meta["lumixs_weight"], full["Signal"]["metadata"]["lumixs_weight"], 1e-12)

    merged = utilities.merge_outputs([part_a, part_b])
    same(merged, "merged in memory")
    assert _close(merged["Signal"]["cutflow"]["baseline"].rows["None"]["weighted"], LUMI * XSEC)
    assert part_a["Signal"]["metadata"]["lumixs_weight"] == weight_a          # inputs untouched
    # each piece is normalised to lumi * xs on its own: adding them by hand counts twice
    naive = accumulate([part_a, part_b])
    assert _close(naive["Signal"]["cutflow"]["baseline"].rows["None"]["weighted"], 2 * LUMI * XSEC)

    # the same through files and the command line
    paths = [str(handle["base"] / name) for name in ("job_a.coffea", "job_b.coffea")]
    utilities.save_output(part_a, paths[0])
    utilities.save_output(part_b, paths[1])
    target = str(handle["base"] / "merged.coffea")
    run = subprocess.run(
        [sys.executable, "-m", f"{handle['package']}.scripts.merge_outputs", *paths, "-o", target],
        cwd=str(handle["project"]), env=dict(os.environ, MPLBACKEND="Agg"), capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    same(utilities.load_output(target), "merged by the script")
    sidecar = _module(handle, "tools.metadata").load_run_metadata(target)
    assert sidecar["n_merged"] == 2 and sidecar["scaled"] is True


def test_sample_settings_are_read_afresh_on_every_run():
    handle = _project("cache")
    utilities = _module(handle, "tools.utilities")
    check = _module(handle, "tools.check")

    def fileset(skim_factor, name="Signal"):
        return {name: {"files": [handle["file"]],
                       "metadata": {"is_data": False, "year": "2018", "skim_factor": skim_factor}}}

    # coffea keeps, for the whole python session, the metadata a file was first run with.
    # The second run must see the skim factor it was given, not the first run's.
    sums = [_run(handle, ["all"], hists=[], fileset=fileset(skim))["Signal"]["metadata"]
            ["scaled_sum_weights"] for skim in (1.0, 0.5, 0.25)]
    assert _close(sums[1], 2 * sums[0]) and _close(sums[2], 4 * sums[0]), sums
    # the same through the checker
    reports = [check.run_report(fileset(skim), channels=["all"], collections=[],
                                max_events=N_EVENTS) for skim in (1.0, 0.5)]
    assert all(report["ok"] for report in reports), [report["error"] for report in reports]
    sums = [report["datasets"]["Signal"]["scaled_sum_weights"] for report in reports]
    assert _close(sums[1], 2 * sums[0]), sums
    # one file under two samples that are to be treated differently cannot be run at once
    both = {**fileset(1.0, "A"), **fileset(0.5, "B")}
    try:
        utilities._check_shared_files(both)
    except ValueError as exc:
        assert "'A' and 'B'" in str(exc)
    else:
        raise AssertionError("a file with two sets of metadata must be refused")
    utilities._check_shared_files({**fileset(1.0, "A"), **fileset(1.0, "B")})


def test_run_periods_given_to_the_processor_are_the_ones_it_normalises_with():
    handle = _project("periods")
    utilities = _module(handle, "tools.utilities")
    other = handle["base"] / "other_periods.yaml"
    other.write_text(f'"2018":\n  lumi: {3 * LUMI}\n', encoding="utf8")
    out = _run(handle, ["all"], hists=["muon_base"], run_periods_cfg=str(other))
    assert _close(out["Signal"]["cutflow"]["all"].rows["None"]["weighted"], 3 * LUMI * XSEC)
    merged = utilities.merge_outputs([out], run_periods_cfg=str(other))
    assert _close(merged["Signal"]["cutflow"]["all"].rows["None"]["weighted"], 3 * LUMI * XSEC)
    merged = utilities.merge_outputs([out])                    # the package's own run periods
    assert _close(merged["Signal"]["cutflow"]["all"].rows["None"]["weighted"], LUMI * XSEC)

    # a luminosity of zero is "not configured yet": scaling by it would wipe the sample
    # out for good, so it is left unscaled, says so, and can still be scaled later
    other.write_text('"2018":\n  lumi: 0\n', encoding="utf8")
    unscaled = _run(handle, ["all"], hists=["muon_base"], run_periods_cfg=str(other))["Signal"]
    assert "lumixs_weight" not in unscaled["metadata"]
    assert any("not scaled" in warning for warning in unscaled["warnings"]), unscaled["warnings"]
    assert _close(unscaled["cutflow"]["all"].rows["None"]["weighted"],
                  unscaled["metadata"]["scaled_sum_weights"])
    recovered = utilities.merge_outputs([{"Signal": unscaled}])
    assert _close(recovered["Signal"]["cutflow"]["all"].rows["None"]["weighted"], LUMI * XSEC)
    # a run-periods file of the wrong shape is a mistake found when the processor is built
    other.write_text('"2018": 59830\n', encoding="utf8")
    processor_class = _module(handle, "tools.processor").AnalysisProcessor
    text = _must_stop(processor_class, ["all"], [], run_periods_cfg=str(other))
    assert "run period '2018'" in text and "lumi" in text, text


def test_cuts_that_have_no_answer_for_some_events_keep_those_events():
    handle = _project("nomask")
    import awkward as ak

    _append(handle, "definitions/cuts.py", '''

# no photon in the event: ak.firsts gives None, and so does the comparison
obj_cut_defs["muons"]["away from the leading photon"] = (
    lambda objs, obj: obj.delta_r(ak.firsts(objs["photons"])) > 0.4)
evt_cut_defs["no muons"] = lambda objs: ak.num(objs["muons"], axis=1) == 0
''')
    _append(handle, "configs/selections.yaml", '''
away:
  obj_cuts: {muons: ["away from the leading photon"]}
  evt_cuts: []
away_with_muons:
  obj_cuts: {muons: ["away from the leading photon"]}
  evt_cuts: [">=1 muons"]
away_without_muons:
  obj_cuts: {muons: ["away from the leading photon"]}
  evt_cuts: ["no muons"]
''')
    _reload(handle)
    events = _events(handle)
    photon = ak.firsts(events.Photon)
    assert int(ak.sum(ak.is_none(photon))) > 0, "the file must have events without a photon"
    # worked out by hand: events in which at least one muon is away from a photon that exists
    expected = int(ak.sum(ak.fill_none(ak.any(events.Muon.delta_r(photon) > 0.4, axis=1), False)))
    assert 0 < expected < N_EVENTS
    out = _run(handle, ["away", "away_with_muons", "away_without_muons"], hists=["muon_base"],
               unweighted_hist=True)["Signal"]
    assert _failures(out) == [], out["warnings"]
    assert _rows(out, "away_with_muons")[-1] == (">=1 muons", expected)
    # an event whose muons all fail has no muons: it is not an event without an answer
    assert _rows(out, "away_without_muons")[-1] == ("no muons", N_EVENTS - expected)
    # and it is counted in the multiplicity histogram, in the zero bin
    muon_n = _hist(out, "muon_n", "away")
    assert muon_n.sum(flow=True).value == N_EVENTS and muon_n.values()[0] == N_EVENTS - expected


def test_histograms_with_several_axes_pair_their_entries_event_by_event():
    handle = _project("axes")
    import awkward as ak

    _append(handle, "definitions/hists.py", '''

def _axis(name, fill, high=1000):
    return h.Axis(hist.axis.Regular(4, 0, high, name=name), fill)


hist_defs.update({
    # one value per event against one per muon: each muon goes with its event's MET
    "met_vs_muon_pt": h.Histogram([_axis("met", lambda objs, mask: objs["met"].pt),
                                   _axis("mu", lambda objs, mask: objs["muons"].pt)]),
    # each missing where the event has none of that kind: only events with both count
    "photon_vs_dsa_pt": h.Histogram([
        _axis("pho", lambda objs, mask: ak.firsts(objs["photons"].pt)),
        _axis("dsa", lambda objs, mask: ak.firsts(objs["dsaMuons"].pt))]),
    # lists that do not correspond: cannot be paired, so not filled
    "muon_vs_electron_pt": h.Histogram([_axis("mu", lambda objs, mask: objs["muons"].pt),
                                        _axis("el", lambda objs, mask: objs["electrons"].pt)]),
})
''')
    _append(handle, "configs/hist_collections.yaml",
            '\npairs:\n  - "met_vs_muon_pt"\n  - "photon_vs_dsa_pt"\n  - "muon_vs_electron_pt"\n'
            '  - "muon_pt"\n')
    _reload(handle)
    events = _events(handle)
    out = _run(handle, ["all"], hists=["pairs"], unweighted_hist=True)["Signal"]
    n_muons = int(ak.sum(ak.num(events.Muon, axis=1)))
    assert _hist(out, "muon_pt", "all").sum(flow=True).value == n_muons
    assert _hist(out, "met_vs_muon_pt", "all").sum(flow=True).value == n_muons
    both = int(ak.sum((ak.num(events.Photon, axis=1) > 0) & (ak.num(events.DSAMuon, axis=1) > 0)))
    one = int(ak.sum(ak.num(events.Photon, axis=1) > 0))
    assert 0 < both < one, "the file must have events with a photon and no DSA muon"
    assert _hist(out, "photon_vs_dsa_pt", "all").sum(flow=True).value == both
    assert _hist(out, "muon_vs_electron_pt", "all").sum(flow=True).value == 0
    failed = _failures(out)
    assert failed and all("muon_vs_electron_pt" in message for message in failed), out["warnings"]


def test_the_checker_reports_config_mistakes_instead_of_crashing():
    handle = _project("shapes")
    check = _module(handle, "tools.check")
    configs = handle["project"] / handle["package"] / "configs"
    assert check.static_report()["errors"] == []

    def broken(path, text):
        """The static report with one config replaced, which is then put back."""
        saved = path.read_text(encoding="utf8") if path.exists() else None
        path.write_text(text, encoding="utf8")
        try:
            report = check.static_report()
            json.dumps(check._plain(report), sort_keys=True)       # whatever it holds can be written
            return report
        finally:
            path.unlink() if saved is None else path.write_text(saved, encoding="utf8")

    selections = configs / "selections.yaml"
    original = selections.read_text(encoding="utf8")
    # object cuts written like event cuts: a list instead of object -> cuts
    report = broken(selections, original + "\nlisty:\n  obj_cuts:\n    - *object_cuts\n  evt_cuts: []\n")
    assert any("[listy] obj_cuts must give the cuts object by object" in e for e in report["errors"])
    assert report["inventory"]["channels"][-1] == "listy"          # the rest is still reported
    report = broken(selections, "- not\n- a mapping\n")
    assert any("cannot be read" in e and "name: value" in e for e in report["errors"]), report["errors"]
    # sample names that yaml reads as numbers, next to ones that are text
    extra = configs / "samples" / "extra.yaml"
    report = broken(extra, 'v2:\n  samples:\n    2018:\n      files: ["/a.root"]\n'
                           '    Other:\n      files: ["/b.root"]\n')
    assert report["errors"] == [] and {"2018", "Other"} <= set(report["inventory"]["samples"])
    report = broken(extra, "v2:\n  samples:\n    - A\n    - B\n")
    assert any("extra.yaml cannot be read" in e for e in report["errors"]), report["errors"]
    report = broken(configs / "run_periods.yaml", '"2018": 59830\n')
    assert any("run period '2018'" in e and "lumi" in e for e in report["errors"]), report["errors"]
    report = broken(configs / "cross_sections.yaml", "Signal:\n")       # a name with no value
    assert any("no cross section" in w for w in report["warnings"]), report["warnings"]
    assert check.static_report()["errors"] == []                       # everything was put back

    # through the command line: a report and exit status 1, not a traceback
    selections.write_text(original + "\nlisty:\n  obj_cuts: [a, b]\n  evt_cuts: []\n", encoding="utf8")
    try:
        target = handle["base"] / "report.json"
        run = subprocess.run(
            [sys.executable, "-m", f"{handle['package']}.tools.check", "--json", str(target)],
            cwd=str(handle["project"]), env=dict(os.environ, MPLBACKEND="Agg"),
            capture_output=True, text=True)
        assert run.returncode == 1 and "Traceback" not in run.stderr, run.stderr
        written = json.loads(target.read_text(encoding="utf8"))
        assert written["ok"] is False and any("listy" in e for e in written["static"]["errors"])
    finally:
        selections.write_text(original, encoding="utf8")


def test_the_check_shows_what_an_edit_did():
    """What an agent (or a person) needs to see after editing definitions and configs."""
    handle = _project("edits")
    check = _module(handle, "tools.check")
    _append(handle, "definitions/hists.py", '''

# the third muon of an event: only events with a soft extra muon have one
hist_defs["third_muon_pt"] = h.Histogram(
    [h.Axis(hist.axis.Regular(10, 0, 10, name="third_muon_pt"),
            lambda objs, mask: objs["muons"][mask, 2].pt)],
    evt_mask=lambda objs: ak.num(objs["muons"], axis=1) > 2)
hist_defs["forgotten"] = obj_attr("muons", "phi")
''')
    _append(handle, "configs/selections.yaml", '''
hard_muons:
  obj_cuts:
    <<: *object_cuts
    muons:
      - "pT > 30 GeV"
  evt_cuts:
    - *event_cuts
''')
    _append(handle, "configs/hist_collections.yaml", '\nextra:\n  - "third_muon_pt"\n  - "muon_n"\n')
    _reload(handle, "definitions.hists", "tools.processor", "tools.check")
    static = check.static_report()
    assert static["errors"] == [], static["errors"]
    # a histogram that no collection lists is never filled: it is named
    assert static["inventory"]["unused"]["hists"] == ["forgotten"]
    # the muon list written next to the merge replaced the shared one, for this channel only
    selections = static["inventory"]["selections"]
    assert selections["hard_muons"]["obj_cuts"]["muons"] == ["pT > 30 GeV"]
    assert selections["hard_muons"]["obj_cuts"]["electrons"] == selections["baseline"]["obj_cuts"]["electrons"]
    assert selections["baseline"]["obj_cuts"]["muons"] == ["pT > 10 GeV", "|eta| < 2.4"]

    utilities = _module(handle, "tools.utilities")
    fileset = utilities.make_fileset(["Signal"])
    report = check.run_report(fileset, channels=["all", "hard_muons"], collections=["extra"],
                              max_events=N_EVENTS)
    assert report["ok"], report["error"]
    dataset = report["datasets"]["Signal"]
    # the soft third muon does not pass 30 GeV: filled in one channel, empty in the other
    assert dataset["empty_hists"] == [] and dataset["warnings"] == [], dataset
    assert dataset["empty_in_channels"] == {"third_muon_pt": ["hard_muons"]}, dataset["empty_in_channels"]
    # the object cut shows in the counter, not in the cutflow
    assert dataset["counters"]["hard_muons"]["Selected muons"] == 2 * dataset["cutflow"]["hard_muons"][-1]["raw"]
    alone = check.run_report(fileset, channels=["hard_muons"], collections=["extra"], max_events=N_EVENTS)
    assert alone["datasets"]["Signal"]["empty_hists"] == ["third_muon_pt"]
    assert alone["datasets"]["Signal"]["empty_in_channels"] == {}

    # a name that is already taken: reported before anything runs, not silently replaced
    _append(handle, "definitions/hists.py", '\nhist_defs["muon_pt"] = obj_attr("muons", "pt", xmax=500)\n')
    _reload(handle, "definitions.hists", "tools.processor", "tools.check")
    errors = check.static_report()["errors"]
    assert len(errors) == 1 and "'muon_pt' is defined twice in hist_defs" in errors[0], errors
    run = subprocess.run(
        [sys.executable, "-m", f"{handle['package']}.tools.check", "--sample", "Signal", "--json", "-"],
        cwd=str(handle["project"]), env=dict(os.environ, MPLBACKEND="Agg"), capture_output=True, text=True)
    assert run.returncode == 1, run.stdout + run.stderr
    written = json.loads(run.stdout)
    assert written["run"]["error"] == "not run: fix the static errors first"


def test_unknown_names_fail_when_the_processor_is_built():
    handle = _project("names")
    processor_class = _module(handle, "tools.processor").AnalysisProcessor
    for kwargs, expected in (
            ({"channel_names": ["nope"]}, "channel 'nope'"),
            ({"channel_names": ["baseline"], "hist_collection_names": ["nope"]}, "collection 'nope'"),
            ({"channel_names": []}, "no channel")):
        try:
            processor_class(**kwargs)
        except ValueError as exc:
            assert expected in str(exc), str(exc)
        else:
            raise AssertionError(f"{kwargs} should not build")


# --------------------------------------------------------------------------- #
# data, missing collections, strictness
# --------------------------------------------------------------------------- #

def test_data_golden_json_and_empty_chunks():
    _need("coffea", "awkward", "uproot", "hist", "yaml")
    from tools.framework.tests import synthetic

    golden = _WORKDIR / "Cert_run1_only.json"
    synthetic.write_golden_json(str(golden), runs=(1,))
    handle = _project("data", data=True, golden_json=golden)
    truth = handle["truth"]
    # chunks of 100: the first two hold only run 2, which the golden JSON rejects entirely
    out = _run(handle, ["all", "baseline"], chunksize=100)["Data"]
    meta = out["metadata"]
    assert meta["is_data"] == {True} and meta["n_evts"] == N_EVENTS
    assert meta["n_removed_golden_json"] == truth["n_first_run"]
    assert "lumixs_weight" not in meta
    kept = N_EVENTS - truth["n_first_run"]
    rows = out["cutflow"]["all"].rows
    assert rows["None"]["raw"] == kept and rows["None"]["weighted"] == float(kept)   # weight 1 for data
    assert [cut for cut, _ in _rows(out, "baseline")] == ["None", "pass triggers", "PV filter"]
    # generator-level objects do not exist in data: reported, not fatal
    assert any("'gens' is not available" in w for w in out["warnings"]), out["warnings"]
    assert _failures(out) == [], out["warnings"]
    assert _hist(out, "muon_pt", "all").sum(flow=True).value > 0
    assert _hist(out, "gen_pt", "all").sum(flow=True).value == 0

    # A golden JSON that is there but cannot be read, or has gone missing, stops the
    # run. Being told to skip bad input files must not turn that into "no data".
    copied = handle["project"] / handle["package"] / "data" / "Cert_run1_only.json"
    saved = copied.read_text(encoding="utf8")
    try:
        copied.write_text("{ this is not json", encoding="utf8")
        text = _must_stop(_run, handle, ["all"], hists=[], chunksize=100, skipbadfiles=True)
        assert "golden JSON for run period '2018' could not be read" in text, text
        copied.unlink()
        text = _must_stop(_run, handle, ["all"], hists=[], chunksize=100, skipbadfiles=True)
        assert "golden JSON for run period '2018' not found" in text, text
    finally:
        copied.write_text(saved, encoding="utf8")
    again = _run(handle, ["all"], hists=[], chunksize=100, skipbadfiles=True)["Data"]
    assert again["cutflow"]["all"].rows["None"]["raw"] == kept


def test_a_failing_cut_stops_a_strict_run_and_is_reported_by_a_lenient_one():
    handle = _project("strict")
    package_dir = handle["project"] / handle["package"]
    with open(package_dir / "definitions" / "cuts.py", "a", encoding="utf8") as stream:
        stream.write('\nevt_cut_defs["broken"] = lambda objs: objs["muons"].no_such_branch > 1\n'
                     'evt_cut_defs["needs gens"] = lambda objs: ak.num(objs["gens"], axis=1) > 0\n'
                     'obj_cut_defs["muons"]["per event"] = lambda objs, obj: ak.num(obj, axis=1) > 0\n')
    with open(package_dir / "configs" / "selections.yaml", "a", encoding="utf8") as stream:
        stream.write('\nwith_broken:\n  obj_cuts: {}\n  evt_cuts: ["PV filter", "broken", ">=1 muons"]\n'
                     'with_wrong_mask:\n  obj_cuts: {muons: ["per event"]}\n  evt_cuts: []\n'
                     'with_gens:\n  obj_cuts: {}\n  evt_cuts: ["needs gens"]\n')
    for module in ("definitions.cuts", "tools.selection", "tools.processor"):
        importlib.reload(_module(handle, module))

    for channel, cut in (("with_broken", "broken"), ("with_wrong_mask", "per event")):
        try:
            _run(handle, [channel], hists=[])
        except Exception as exc:
            text = _chain_text(exc)
            assert f"'{cut}'" in text and f"[{channel}]" in text, text
        else:
            raise AssertionError(f"a strict run of '{channel}' must stop")

    out = _run(handle, ["with_broken", "with_wrong_mask"], hists=[], strict=False)["Signal"]
    rows = out["cutflow"]["with_broken"].rows
    assert rows["broken"]["not_applied"] >= 1 and rows["broken"]["raw"] == rows["PV filter"]["raw"]
    assert rows[">=1 muons"]["not_applied"] == 0
    assert len(_failures(out)) == 2, out["warnings"]
    # a cut on a collection that exists is simply applied
    assert _rows(_run(handle, ["with_gens"], hists=[])["Signal"], "with_gens")[-1] == ("needs gens", N_EVENTS)


def test_errors_of_the_analysis_are_reported_and_errors_of_the_input_are_passed_on():
    handle = _project("auxfile")
    utilities = _module(handle, "tools.utilities")
    _append(handle, "definitions/cuts.py", '''

import gzip


def _needs_a_file(objs):
    with open("/nonexistent/corrections.json") as handle:
        return handle.read()


def _bad_gzip(objs):
    raise gzip.BadGzipFile("Not a gzipped file")


def _storage_hiccup(objs):
    raise OSError(5, "Input/output error")


def _input_read_error():
    """An OSError raised the way a failed lazy read of the input raises it:
    in the reading library, called from coffea's column loader."""
    reader = {"__name__": "uproot.source.fsspec"}
    loader = {"__name__": "coffea.nanoevents.mapping.uproot"}
    exec("def read():\\n    raise OSError('File did not vector_read properly')", reader)
    exec("def extract_column(read):\\n    return read()", loader)
    return loader["extract_column"](reader["read"])


evt_cut_defs["needs a file"] = _needs_a_file
evt_cut_defs["bad gzip"] = _bad_gzip
evt_cut_defs["storage hiccup"] = _storage_hiccup
evt_cut_defs["input read error"] = lambda objs: _input_read_error()
''')
    _append(handle, "configs/selections.yaml", '''
with_file: {obj_cuts: {}, evt_cuts: ["needs a file"]}
with_gzip: {obj_cuts: {}, evt_cuts: ["bad gzip"]}
with_hiccup: {obj_cuts: {}, evt_cuts: ["storage hiccup"]}
with_input_error: {obj_cuts: {}, evt_cuts: ["input read error"]}
''')
    _reload(handle)
    cuts = _module(handle, "definitions.cuts")

    # the classification itself: by where the error was raised, not by its type
    def raised(call, *args):
        try:
            call(*args)
        except Exception as exc:
            return exc
        raise AssertionError("no error was raised")

    assert utilities.is_io_error(raised(cuts._input_read_error))
    for own in (cuts._needs_a_file, cuts._bad_gzip, cuts._storage_hiccup):
        assert not utilities.is_io_error(raised(own, None)), own.__name__

    def as_awkward_wraps_it():
        try:
            cuts._input_read_error()
        except OSError as inner:
            raise AttributeError("while trying to get field 'pt', an exception occurred") from inner

    assert utilities.is_io_error(raised(as_awkward_wraps_it))

    # skipbadfiles drops chunks whose failure involves an OSError. A file of the analysis
    # that is missing or broken must not be taken for an unreadable input, or the output
    # would come back smaller, or empty, instead of the run stopping.
    for channel, cut, said in (("with_file", "needs a file", "corrections.json"),
                               ("with_gzip", "bad gzip", "Not a gzipped file"),
                               ("with_hiccup", "storage hiccup", "Input/output error")):
        text = _must_stop(_run, handle, [channel], hists=[], skipbadfiles=True)
        assert f"event cut '{cut}' could not be evaluated" in text and said in text, text
        out = _run(handle, [channel], hists=[], skipbadfiles=True, strict=False)["Signal"]
        assert out["cutflow"][channel].rows[cut]["not_applied"] >= 1
        assert out["metadata"]["n_evts"] == N_EVENTS                 # no chunk was dropped
        assert any(said in w for w in out["warnings"]), out["warnings"]

    # An error from reading the input is not the cut's fault. It is passed on as it is,
    # in both modes, for coffea to stop on (or to skip, where it was told to).
    for strict in (True, False):
        text = _must_stop(_run, handle, ["with_input_error"], hists=[], strict=strict)
        assert "vector_read" in text and "could not be evaluated" not in text, text


# --------------------------------------------------------------------------- #
# reading: the schema, custom collections, generator navigation
# --------------------------------------------------------------------------- #

def _events(handle, schema=None):
    from coffea.nanoevents import NanoEventsFactory

    schema = schema or _module(handle, "tools.schema").AnalysisSchema
    return NanoEventsFactory.from_root({handle["file"]: "Events"}, schemaclass=schema,
                                       mode="virtual").events()


def test_schema_reads_duplicate_momenta_and_custom_collections():
    handle = _project("schema_core")
    import awkward as ak
    import numpy as np

    utilities = _module(handle, "tools.utilities")
    objects = _module(handle, "definitions.objects")
    events = _events(handle)

    gen = events.GenPart
    assert "px" not in gen.fields and "pt" in gen.fields            # the cartesian copy is hidden
    # ... and what is computed in its place is what the file stores
    import uproot
    with uproot.open(handle["file"]) as stored:
        stored_px = stored["Events"]["GenPart_px"].array(library="ak")
    assert np.allclose(ak.flatten(gen.px), ak.flatten(stored_px), rtol=1e-4, atol=1e-3)
    muons = objects.pid(gen, 13)
    assert ak.all(ak.num(muons, axis=1) == 2)
    assert ak.all(abs(ak.flatten(muons.parent.pdgId)) == 32)        # navigation still works
    assert ak.all(ak.num(objects.from_pid(objects.pid(gen, 11), 32), axis=1) == 2)
    assert ak.all(ak.num(objects.to_pid(objects.pid(gen, 32), 13), axis=1) == 1)
    # decay length: from where the particle was made to where its daughters were made
    lxy = utilities.lxy(objects.pid(gen, 32))
    assert _close(float(ak.mean(lxy)), handle["truth"]["mean_lxy"], 1e-3)

    raw = events.DSAMuon
    assert "mass" not in raw.fields
    dsa = utilities.as_lorentz(raw, mass=0.105658)
    assert ak.all(ak.num(dsa, axis=1) == ak.num(raw, axis=1))
    assert set(raw.fields) <= set(dsa.fields) and "mass" in dsa.fields
    assert np.allclose(ak.flatten(dsa.mass), 0.105658)
    assert ak.all(ak.num(dsa.delta_r(dsa), axis=1) == ak.num(raw, axis=1))      # vector behaviour
    nearest, dr = dsa.nearest(events.Muon, return_metric=True)
    assert ak.all(ak.num(dr, axis=1) == ak.num(raw, axis=1))


def test_schema_component_adds_cross_references_and_hides_branches():
    handle = _project("schema_addon", add=[("schema", {
        "cross_references": {"Muon_dsaMatch1idx": "DSAMuon", "Muon_dsaMatch2idx": "DSAMuon"},
        "nested_items": {"Muon_dsaIdxG": ["Muon_dsaMatch1idx", "Muon_dsaMatch2idx"]},
        "hidden_branches": ["Jet_jetId"]})])
    import awkward as ak

    truth = handle["truth"]
    schema = _module(handle, "tools.schema")
    events = _events(handle, schema.AnalysisSchema)
    assert "jetId" not in events.Jet.fields and "pt" in events.Jet.fields
    # the index branches are stored as floats, which coffea alone does not accept
    assert "float32" in str(ak.type(events.Muon.dsaMatch1idx))
    assert "dsaMatch1idxG" in events.Muon.fields and "dsaIdxG" in events.Muon.fields
    followed = schema.follow(events.Muon, "dsaMatch1idxG", events.DSAMuon)
    assert ak.all(ak.num(followed, axis=1) == ak.num(events.Muon, axis=1))
    assert int(ak.sum(~ak.is_none(followed, axis=1))) == truth["n_muons_with_dsa"]
    # the first signal muon points at the first DSAMuon of its event
    has_dsa = ak.num(events.DSAMuon, axis=1) > 0
    assert ak.all(followed[has_dsa][:, 0].pt == events.DSAMuon[has_dsa][:, 0].pt)

    # From objects that were re-ordered and cut since they were read, as the objects of
    # a selection are, and with the target given by name or as a cut collection itself:
    # what is followed is always the collection as it is in the file.
    reordered = events.Muon[ak.argsort(events.Muon.eta, axis=1)]
    selected = reordered[reordered.pt > 10]                      # the soft extra muons go
    assert not ak.all(ak.num(selected, axis=1) == ak.num(events.Muon, axis=1))
    emptied = events.DSAMuon[events.DSAMuon.pt > 1e6]
    for target in ("DSAMuon", events.DSAMuon, emptied):
        again = schema.follow(selected, "dsaMatch1idxG", target)
        assert ak.all(ak.num(again, axis=1) == ak.num(selected, axis=1))
        pointing = again[selected.dsaMatch1idx >= 0]             # one per event that has a DSAMuon
        assert ak.all(ak.num(pointing, axis=1) == ak.values_astype(has_dsa, int))
        assert ak.all(ak.flatten(pointing.pt) == ak.flatten(events.DSAMuon[:, :1].pt))
        assert ak.all(ak.flatten(pointing.dxy) == ak.flatten(events.DSAMuon[:, :1].dxy))

    # a nested item: one list per muon, with one slot per index branch
    both = schema.follow(selected, "dsaIdxG", "DSAMuon")
    assert ak.all(ak.flatten(ak.num(both, axis=2)) == 2)
    assert int(ak.sum(~ak.is_none(both, axis=2))) == (truth["n_muons_with_dsa"]
                                                      + truth["n_muons_with_two_dsa"])
    second = both[selected.dsaMatch2idx >= 0][:, :, 1]
    assert ak.all(ak.flatten(second.pt) == ak.flatten(events.DSAMuon[:, 1:2].pt))

    # in the definitions: what a muon points to, kept with the muon through cuts and ordering
    _append(handle, "definitions/objects.py", f'''

from {handle["package"]}.tools.schema import follow  # noqa: E402

derived_objs["matched_muons"] = lambda objs: ak.with_field(
    objs["muons"], follow(objs["muons"], "dsaMatch1idxG", "DSAMuon"), "dsa")
''')
    _append(handle, "definitions/cuts.py", '''
obj_cut_defs["matched_muons"] = {
    "has a DSA muon": lambda objs, obj: ~ak.is_none(obj.dsa.pt, axis=1)}
evt_cut_defs[">=1 matched muons"] = at_least("matched_muons", 1)
''')
    _append(handle, "configs/selections.yaml", '''
matched:
  obj_cuts: {matched_muons: ["has a DSA muon"]}
  evt_cuts: [">=1 matched muons"]
''')
    _reload(handle, "definitions.objects", "definitions.cuts", "definitions.hists",
            "tools.selection", "tools.processor")
    out = _run(handle, ["baseline", "matched"], hists=["jet_base"])["Signal"]
    assert _failures(out) == [], out["warnings"]
    assert _rows(out, "matched")[-1] == (">=1 matched muons", truth["n_muons_with_dsa"])


# --------------------------------------------------------------------------- #
# components
# --------------------------------------------------------------------------- #

def test_lepton_jets_reconstruct_the_resonance():
    _need("coffea", "awkward", "fastjet", "vector")
    handle = _project("ljs", add=[("lepton_jets", {"sources": ["muons", "electrons", "photons"]})])
    import awkward as ak
    import numpy as np

    truth = handle["truth"]
    assert _module(handle, "tools.check").static_report()["errors"] == []
    out = _run(handle, ["baseline_2ljs"], hists=["lj_base"], chunksize=150)["Signal"]
    assert _failures(out) == [], out["warnings"]
    assert _rows(out, "baseline_2ljs")[-1] == (">=2 ljs", truth["n_pass_trigger_and_pv"])
    assert out["cutflow"]["baseline_2ljs"].rows[">=2 ljs"]["not_applied"] == 0
    # exactly two lepton jets per event, and their mass is the resonance mass
    lj_n = _hist(out, "lj_n", "baseline_2ljs").values()
    assert lj_n.sum() > 0 and _close(lj_n[2], lj_n.sum()), lj_n
    mass = _hist(out, "lj_lj_invmass", "baseline_2ljs")
    assert mass.values().sum() > 0
    assert abs(_mean(mass) - truth["resonance_mass"]) < 10.0, _mean(mass)     # bins are 10 GeV wide
    # two constituents each: the soft extra muons and photons are below the baseline cuts
    constituents = _hist(out, "lj_n_constituents", "baseline_2ljs").values()
    assert constituents.sum() > 0 and _close(constituents[2], constituents.sum()), constituents
    # the matched jet has 1.05 times the pair's energy and is 95% leptonic: isolation ~ 0.0525,
    # which is the 0.04 - 0.08 bin of the histogram
    isolation = _hist(out, "lj_isolation", "baseline_2ljs").values()
    assert isolation.sum() > 0 and _close(isolation[1], isolation.sum()), isolation

    # the builder itself, on selected objects
    lepton_jets = _module(handle, "tools.lepton_jets")
    utilities = _module(handle, "tools.utilities")
    events = _events(handle)
    objs = utilities.ObjectStore(known=["muons", "electrons", "jets"])
    objs["muons"] = events.Muon[events.Muon.pt > 10]
    objs["electrons"] = events.Electron[events.Electron.pt > 10]
    objs["jets"] = events.Jet
    ljs = lepton_jets.build_lepton_jets(objs, ["muons", "electrons"], jets="jets")
    assert ak.all(ak.num(ljs, axis=1) == 2)
    assert ak.all(ljs.n_constituents == 2)
    assert ak.all(ljs.muons_n + ljs.electrons_n == ljs.n_constituents)
    assert ak.all((ljs.muons_n == 2) | (ljs.electrons_n == 2))                # pairs are not mixed
    assert ak.all(ak.num(ljs.muons, axis=2) == ljs.muons_n)
    opening = np.hypot(0.02, 0.02)
    assert ak.all(abs(ljs.dRSpread - opening) < 0.003), ak.to_list(ljs.dRSpread[:3])
    total = ljs[:, 0] + ljs[:, 1]
    assert np.allclose(total.mass, truth["resonance_mass"], rtol=5e-3)
    assert ak.all(abs(ljs[:, 0].delta_phi(ljs[:, 1])) > 3.0)                  # back to back
    assert not ak.any(ak.is_none(ljs.matched_jet, axis=1))                    # every one found its jet
    assert ak.all((ljs.isolation > 0.04) & (ljs.isolation < 0.07)), ak.to_list(ljs.isolation[:3])
    assert ak.all(abs(ljs.lepton_fraction - 0.95) < 1e-5)
    assert ak.all(ak.flatten(ljs.muons.charge, axis=None) != 0)               # carried field
    # going back to the full source objects
    full = lepton_jets.source_objects(objs, ljs, "muons")
    assert ak.all(ak.num(full, axis=2) == ljs.muons_n)
    assert ak.all(ak.flatten(full.tightId, axis=None))
    assert np.allclose(ak.flatten(full.pt, axis=None), ak.flatten(ljs.muons.pt, axis=None))
    # nothing to cluster anywhere: an empty collection with the same fields, not a crash
    objs["muons"] = events.Muon[events.Muon.pt > 1e6]
    objs["electrons"] = events.Electron[events.Electron.pt > 1e6]
    empty = lepton_jets.build_lepton_jets(objs, ["muons", "electrons"], jets="jets")
    assert len(empty) == len(events) and ak.all(ak.num(empty, axis=1) == 0)
    assert set(ljs.fields) == set(empty.fields)
    assert int(ak.sum(empty.pt > 0)) == 0


def test_chain_report_roundtrip():
    handle = _project("chain", add=[("chain_report", None)])
    tests_dir = handle["project"] / "tests"
    env = dict(os.environ, MPLBACKEND="Agg")
    made = subprocess.run(
        [sys.executable, str(tests_dir / "make_fixture.py"), handle["file"], "--dataset", "Signal",
         "--events", "120", "--year", "2018", "--skim-factor", "0.5"],
        cwd=str(handle["project"]), env=env, capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    assert (tests_dir / "data" / "Signal_120ev.root").is_file()
    # the fixture must be read in two chunks, or the merging of chunks is never exercised
    import yaml
    fixtures_text = (tests_dir / "fixtures.yaml").read_text()
    fixtures_cfg = yaml.safe_load(fixtures_text)
    assert fixtures_cfg["chunksize"] == 60 and "lowered" in made.stdout, made.stdout
    assert fixtures_cfg["fixtures"] == [{"dataset": "Signal", "file": "data/Signal_120ev.root",
                                         "is_data": False, "year": "2018", "skim_factor": 0.5}]
    assert fixtures_text.startswith("# Small files the chain report runs over")
    rewritten = nanoaod_layout.read_tree_layout(str(tests_dir / "data" / "Signal_120ev.root"))
    assert rewritten["trees"] == {"Events": 120} and "nMuon" in rewritten["branches"], rewritten["trees"]
    assert "nGenPart" in rewritten["branches"] and "HLT_IsoMu24" in rewritten["branches"]
    state_path = handle["base"] / "state.json"
    computed = subprocess.run(
        [sys.executable, str(tests_dir / "chain_report.py"), "compute", str(state_path)],
        cwd=str(handle["project"]), env=env, capture_output=True, text=True)
    assert computed.returncode == 0, computed.stdout + computed.stderr
    state = json.loads(state_path.read_text())
    assert state["static"]["errors"] == [] and state["run_errors"] == {}, state["run_errors"]
    fixture = state["fixtures"]["Signal"]
    assert fixture["n_events"] == 120
    assert fixture["cutflow"]["all"][0]["raw"] == 120
    # scaled to lumi * xs, times the skim factor: the 120 events stand for 240 produced
    assert _close(fixture["cutflow"]["all"][0]["weighted"], LUMI * XSEC * 0.5)
    # the fixture, rewritten by uproot, still has every collection the analysis reads
    assert not [w for w in fixture["warnings"] if "not available" in w], fixture["warnings"]
    assert fixture["unavailable_objects"] == []

    # A slice of data, taken across the boundary between its two runs. Data has a counter
    # with an underscore in its name (nProton_multiRP), which must survive the rewrite.
    from tools.framework.tests import synthetic
    data_file = handle["base"] / "data.root"
    synthetic.write_nanoaod(str(data_file), n=N_EVENTS, data=True)
    made = subprocess.run(
        [sys.executable, str(tests_dir / "make_fixture.py"), str(data_file), "--dataset", "Data",
         "--data", "--year", "2018", "--first-event", "150", "--events", "100"],
        cwd=str(handle["project"]), env=env, capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    rewritten = nanoaod_layout.read_tree_layout(str(tests_dir / "data" / "Data_100ev.root"))
    assert rewritten["trees"] == {"Events": 100}
    assert {"nProton_multiRP", "Proton_multiRP_xi", "nMuon", "run"} <= set(rewritten["branches"])
    assert "left out" not in made.stdout, made.stdout
    fixtures_cfg = yaml.safe_load((tests_dir / "fixtures.yaml").read_text())
    assert [f["dataset"] for f in fixtures_cfg["fixtures"]] == ["Signal", "Data"]
    assert fixtures_cfg["fixtures"][1]["is_data"] is True and fixtures_cfg["chunksize"] == 50
    computed = subprocess.run(
        [sys.executable, str(tests_dir / "chain_report.py"), "compute", str(state_path)],
        cwd=str(handle["project"]), env=env, capture_output=True, text=True)
    assert computed.returncode == 0, computed.stdout + computed.stderr
    state = json.loads(state_path.read_text())
    assert state["run_errors"] == {}, state["run_errors"]
    assert state["fixtures"]["Data"]["n_events"] == 100 and state["fixtures"]["Data"]["is_data"] == [True]
    assert any("'gens' is not available" in w for w in state["fixtures"]["Data"]["warnings"])
    fixture = state["fixtures"]["Signal"]

    sys.path.insert(0, str(tests_dir))
    chain_report = importlib.import_module("chain_report")
    assert chain_report._cell("leading ljs |dphi| > 2") == "leading ljs \\|dphi\\| > 2"
    assert chain_report.failures(fixture["warnings"]) == [], fixture["warnings"]
    assert chain_report.new_errors(state, state) == []
    assert "No new errors" in chain_report.render(state, state)
    worse = copy.deepcopy(state)
    worse["fixtures"]["Signal"]["warnings"].append(
        "[baseline] event cut 'x' could not be evaluated and was skipped (AttributeError)")
    worse["fixtures"]["Signal"]["cutflow"]["baseline"][-1]["raw"] -= 1
    assert len(chain_report.new_errors(state, worse)) == 1
    assert "Cutflow changed" in chain_report.render(state, worse)
    rendered = subprocess.run(
        [sys.executable, str(tests_dir / "chain_report.py"), "render", str(state_path), str(state_path)],
        cwd=str(handle["project"]), env=env, capture_output=True, text=True)
    assert rendered.returncode == 0 and "## Chain report" in rendered.stdout


def test_make_fixture_leaves_out_a_branch_it_cannot_read():
    # Real files can hold a damaged basket (one did, at the LPC): the fixture is cut
    # from the branches that can be read, and the damaged one is listed as left out.
    import struct

    import awkward as ak
    import numpy as np
    import uproot
    from tools.framework.tests import synthetic

    handle = _project("damaged", add=[("chain_report", None)])
    tests_dir = handle["project"] / "tests"
    source = handle["base"] / "damaged.root"
    n = 200
    counts = np.arange(n) % 3
    with uproot.recreate(str(source)) as out:
        synthetic.write_tree(out, "Events", {
            "run": np.ones(n, dtype=np.uint32), "luminosityBlock": np.ones(n, dtype=np.uint32),
            "event": np.arange(n, dtype=np.uint64),
            "Muon": ak.zip({"pt": ak.unflatten(np.full(counts.sum(), 30.0), counts),
                            "eta": ak.unflatten(np.zeros(counts.sum()), counts)}),
            "Damaged": np.zeros(n)})            # zeros: its basket is stored compressed

    with uproot.open(str(source)) as handle_in:
        branch = handle_in["Events"]["Damaged"]
        seek = int(branch.member("fBasketSeek")[0])
    raw = bytearray(source.read_bytes())
    # the basket's key: fNbytes (4 bytes), fVersion (2), fObjlen (4), fDatime (4), fKeylen (2)
    key_length = struct.unpack(">h", bytes(raw[seek + 14:seek + 16]))[0]
    data = seek + key_length
    assert bytes(raw[data:data + 2]) in (b"ZL", b"L4", b"XZ", b"ZS", b"CS"), bytes(raw[data:data + 9])
    raw[data + 9:data + 25] = b"\xff" * 16    # the compressed stream after its 9-byte header
    source.write_bytes(bytes(raw))
    with uproot.open(str(source)) as handle_in:
        tree = handle_in["Events"]
        try:
            tree["Damaged"].array(library="ak")
            readable = True
        except Exception:                       # noqa: BLE001
            readable = False
        assert not readable, "the damaged branch must really be unreadable"
        assert ak.sum(tree["nMuon"].array(library="ak")) == counts.sum()

    made = subprocess.run(
        [sys.executable, str(tests_dir / "make_fixture.py"), str(source), "--dataset", "Damaged",
         "--events", "120", "--year", "2018", "--skim-factor", "0.5"],
        cwd=str(handle["project"]), env=dict(os.environ, MPLBACKEND="Agg"),
        capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    fixture = tests_dir / "data" / "Damaged_120ev.root"
    assert fixture.is_file(), made.stdout
    import yaml
    registered = yaml.safe_load((tests_dir / "fixtures.yaml").read_text())["fixtures"]
    assert [f["dataset"] for f in registered] == ["Damaged"], registered
    assert "left out 1 branch(es)" in made.stdout and "'Damaged (" in made.stdout, made.stdout
    rewritten = nanoaod_layout.read_tree_layout(str(fixture))
    assert rewritten["trees"] == {"Events": 120}, rewritten["trees"]
    assert {"run", "luminosityBlock", "event", "nMuon", "Muon_pt", "Muon_eta"} <= set(rewritten["branches"])
    assert "Damaged" not in rewritten["branches"], rewritten["branches"]


def test_scaleout_ships_the_package_and_dask_gives_the_same_answer():
    # coffea's DaskExecutor imports dask.dataframe, which needs pandas and pyarrow
    _need("coffea", "dask", "distributed", "dask.dataframe")
    import io
    import zipfile

    from coffea import processor

    handle = _project("scaleout", add=[("scaleout", None)])
    scaleout = _module(handle, "tools.scaleout")
    package = handle["package"]
    package_dir = handle["project"] / package
    # a saved output lying in the package is not shipped; a lookup table under data/ is
    (package_dir / "old_output.coffea").write_bytes(b"x" * 10)
    (package_dir / "data" / "table.coffea").write_bytes(b"x" * 10)
    plugin = scaleout.build_upload_plugin()
    names = zipfile.ZipFile(io.BytesIO(plugin.data)).namelist()
    assert f"{package}/tools/processor.py" in names and f"{package}/definitions/cuts.py" in names
    assert f"{package}/configs/selections.yaml" in names
    assert not [n for n in names if n.endswith(".ipynb") or "test_notebooks" in n], names
    assert f"{package}/data/table.coffea" in names and f"{package}/old_output.coffea" not in names
    assert plugin.restart_workers is False
    assert scaleout.build_upload_plugin(restart=True).restart_workers is True

    # the worker image: the one for this coffea and python, under its current name if
    # it is there, else under the earlier one
    import platform

    import coffea
    images = handle["base"] / "images"
    python = ".".join(platform.python_version_tuple()[:2])
    tag = f"{coffea.__version__.replace('rc', '.rc')}-py{python}"
    for name in (f"coffea-dask-almalinux8:{tag}", f"coffea-dask-almalinux9:{tag}",
                 f"coffea-dask-almalinux9:0.0.1-py{python}", f"coffea-dask-almalinux9-noml:{tag}"):
        (images / name).mkdir(parents=True)
    assert scaleout.find_lpc_image(str(images)).endswith(f"coffea-dask-almalinux9:{tag}")
    for name in (f"coffea-dak-almalinux9:{tag}", f"coffea-dak-almalinux9-eaf:{tag}"):
        (images / name).mkdir()
    assert scaleout.find_lpc_image(str(images)).endswith(f"coffea-dak-almalinux9:{tag}")
    try:
        scaleout.find_lpc_image(str(handle["base"] / "no_images"))
    except RuntimeError as exc:
        assert "Pass image=" in str(exc)
    else:
        raise AssertionError("no matching image must be an error")

    reference = _run(handle, ["baseline_2muons"], chunksize=100)["Signal"]
    client = scaleout.make_local_client(n_workers=2)
    try:
        dask_out = _run(handle, ["baseline_2muons"], chunksize=100,
                        executor=processor.DaskExecutor(client=client, status=False))["Signal"]
    finally:
        client.close()
    assert _rows(dask_out, "baseline_2muons") == _rows(reference, "baseline_2muons")


# --------------------------------------------------------------------------- #
# command line and the heptapod tool
# --------------------------------------------------------------------------- #

def test_command_line_runner_writes_output_and_sidecar():
    handle = _project("cli")
    # into a directory that does not exist yet: it is made before the run, not found
    # missing after it
    output = handle["base"] / "output" / "first" / "out.coffea"
    assert not output.parent.exists()
    run = subprocess.run(
        [sys.executable, "-m", f"{handle['package']}.scripts.run_analysis", "--samples", "Signal",
         "--channels", "baseline", "--hists", "base", "--chunksize", "150", "-o", str(output)],
        cwd=str(handle["project"]), env=dict(os.environ, MPLBACKEND="Agg"), capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "PV filter" in run.stdout                                   # the cutflow table was printed
    utilities = _module(handle, "tools.utilities")
    metadata = _module(handle, "tools.metadata")
    out = utilities.load_output(str(output))
    assert out["Signal"]["metadata"]["n_evts"] == N_EVENTS
    sidecar = metadata.load_run_metadata(str(output))
    assert sidecar["selections"][0]["name"] == "baseline" and sidecar["samples"][0]["xsec_pb"] == XSEC
    assert sidecar["schema"] == "AnalysisSchema" and sidecar["chunksize"] == 150
    table = utilities.cutflow_table(out, "baseline")
    assert table[0].split() == ["cut", "name", "Signal"] and len(table) == 5


def test_check_tool_runs_the_generated_checker():
    handle = _project("tool")
    try:
        from tools.framework.check import CheckAnalysisFrameworkTool
    except ImportError as exc:
        _skip(f"orchestral is not installed ({exc})")
    # the working directory of the tool, with links resolved as the tool resolves them
    workdir = os.path.realpath(str(_WORKDIR))
    relative = os.path.relpath(os.path.realpath(str(handle["project"])), workdir)
    # time for a first import of coffea: this is not a test of how fast that is
    patient = dict(base_directory=workdir, project_dir=relative, timeout_s=600)
    static = json.loads(CheckAnalysisFrameworkTool(**patient)._run())
    assert static["status"] == "ok" and static["ok"] is True and "run" not in static
    assert static["static"]["channels"] == ["all", "baseline", "baseline_2muons"]
    assert static["static"]["selections"]["baseline"]["evt_cuts"] == ["pass triggers", "PV filter"]
    assert static["static"]["unused_hists"] == [] and static["static"]["n_selections_not_shown"] == 0
    # the samples as the configs describe them
    assert static["static"]["n_samples"] == 1
    assert static["static"]["samples"] == {"Signal": {"is_data": False, "year": "2018", "n_files": 1}}
    assert static["static"]["run_periods"] == {"2018": {"lumi": LUMI, "golden_json": None}}
    report = json.loads(CheckAnalysisFrameworkTool(
        **patient, sample="Signal", max_events=200,
        channels=["baseline_2muons"], hist_collections=["muon_base"])._run())
    assert report["ok"] is True and report["run"]["ok"] is True, report
    # the cuts of the channel that was asked about, and of that one only
    assert list(report["static"]["selections"]) == ["baseline_2muons"]
    assert report["static"]["selections"]["baseline_2muons"]["obj_cuts"]["muons"] == ["pT > 10 GeV", "|eta| < 2.4"]
    dataset = report["run"]["datasets"]["Signal"]
    assert dataset["empty_in_channels"] == {}
    assert dataset["n_events"] == 200 and dataset["empty_hists"] == []
    assert report["run"]["max_events"] == 200 and dataset["files_in_sample"] == 1
    assert dataset["year"] == ["2018"] and dataset["scaled_sum_weights"] > 0
    assert _close(dataset["lumixs_weight"], LUMI * XSEC / dataset["scaled_sum_weights"])
    cutflow = dataset["cutflow"]["baseline_2muons"]
    assert cutflow[0][:2] == ["None", 200] and cutflow[-1][0] == ">=2 muons" and cutflow[-1][1] > 0
    assert _close(cutflow[0][2], LUMI * XSEC, 1e-5)
    by_file = json.loads(CheckAnalysisFrameworkTool(
        **patient, sample_file=os.path.relpath(os.path.realpath(handle["file"]), workdir),
        max_events=100, channels=["all"], hist_collections=[])._run())
    assert by_file["run"]["datasets"]["signal"]["is_data"] == [False]     # decided from the file
    assert by_file["run"]["datasets"]["signal"]["files_in_sample"] is None  # not a configured sample
    # one call for a simulated and a data sample
    import yaml
    from tools.framework.tests import synthetic
    data_file = handle["base"] / "data_b.root"
    synthetic.write_nanoaod(str(data_file), n=120, data=True)
    sample_cfg = handle["project"] / handle["package"] / "configs" / "samples" / "samples.yaml"
    configured = sample_cfg.read_text(encoding="utf8")
    locations = yaml.safe_load(configured)
    (group,) = locations
    locations[group]["samples"]["DataB"] = {"files": [str(data_file)], "is_data": True}
    sample_cfg.write_text(yaml.safe_dump(locations), encoding="utf8")
    try:
        both = json.loads(CheckAnalysisFrameworkTool(
            **patient, sample=["DataB", "Signal"], max_events=100,
            channels=["baseline"], hist_collections=["muon_base"])._run())
    finally:
        sample_cfg.write_text(configured, encoding="utf8")
    assert both["ok"] is True and set(both["run"]["datasets"]) == {"Signal", "DataB"}, both
    assert "--sample DataB Signal" in both["command"]
    assert both["run"]["datasets"]["DataB"]["is_data"] == [True]
    assert both["run"]["datasets"]["Signal"]["is_data"] == [False]
    assert both["run"]["datasets"]["DataB"]["lumixs_weight"] is None       # data is not scaled
    # the samples that were asked about are listed first, as configured
    assert list(both["static"]["samples"]) == ["DataB", "Signal"] and both["static"]["n_samples"] == 2
    assert both["static"]["samples"]["DataB"] == {"is_data": True, "year": "2018", "n_files": 1}
    # a mistake in a config comes back as a report with the mistake in it
    selections = handle["project"] / handle["package"] / "configs" / "selections.yaml"
    original = selections.read_text(encoding="utf8")
    selections.write_text(original + "\nlisty:\n  obj_cuts: [a, b]\n  evt_cuts: []\n", encoding="utf8")
    try:
        mistaken = json.loads(CheckAnalysisFrameworkTool(**patient)._run())
    finally:
        selections.write_text(original, encoding="utf8")
    assert mistaken["status"] == "ok" and mistaken["ok"] is False
    assert any("listy" in error for error in mistaken["static"]["errors"]), mistaken["static"]


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
    print(f"framework run tests: {passed} passed, {skipped} skipped, {failed} failed")
    if skipped and not passed and not failed:
        print("NOTHING WAS TESTED: every test was skipped. Run this with an interpreter that "
              "has coffea, awkward, uproot and hist (the framework bundle's environment).")
    if skipped and no_skips:
        print("--no-skips: a skipped test counts as a failure")
    sys.exit(1 if failed or (skipped and no_skips) else 0)
