"""
# test_lpc_scaleout.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Tests for the LPC scale-out runner.

The parts that must not silently break are the ones a failed batch run would
only reveal hours later: path translation to xrootd, chunking, accumulation,
resumability, and the generated submit file honouring the LPC constraints
(container image, transferred inputs, no /uscms_data on the worker).

No HTCondor and no data are needed -- everything here is exercised against
temporary files.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pickle

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNNER = os.path.join(REPO, "scripts", "lpc_scaleout.py")


@pytest.fixture(scope="module")
def mod():
    assert os.path.exists(RUNNER), RUNNER
    spec = importlib.util.spec_from_file_location("lpc_scaleout", RUNNER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------ paths ------------------------------- #

def test_eos_paths_become_xrootd(mod):
    assert mod.to_xrootd("/eos/uscms/store/group/x/f.root") == \
        "root://cmseos.fnal.gov//store/group/x/f.root"
    assert mod.to_xrootd("/store/group/x/f.root") == \
        "root://cmseos.fnal.gov//store/group/x/f.root"


def test_xrootd_conversion_is_idempotent(mod):
    u = "root://cmseos.fnal.gov//store/x.root"
    assert mod.to_xrootd(u) == u
    assert mod.to_xrootd(mod.to_xrootd("/eos/uscms/store/x.root")) == \
        mod.to_xrootd("/eos/uscms/store/x.root")


def test_local_paths_are_left_alone(mod):
    assert mod.to_xrootd("/uscms_data/d3/me/f.root") == "/uscms_data/d3/me/f.root"


def test_to_local_inverts(mod):
    p = "/eos/uscms/store/group/x/f.root"
    assert mod.to_local(mod.to_xrootd(p)) == p


# ----------------------------- chunking ----------------------------- #

def test_chunking_covers_every_file_exactly_once(mod):
    files = [f"f{i}.root" for i in range(47)]
    chunks = mod.chunk(files, 8)
    assert len(chunks) == 6
    flat = [f for c in chunks for f in c]
    assert flat == files
    assert len(chunks[-1]) == 47 - 5 * 8


# --------------------------- accumulation --------------------------- #

def test_accumulate_adds_numbers_and_recurses(mod):
    a = {"n": 3, "sub": {"x": 1.5}}
    b = {"n": 4, "sub": {"x": 2.5}, "new": 7}
    out = mod.accumulate(a, b)
    assert out["n"] == 7
    assert out["sub"]["x"] == 4.0
    assert out["new"] == 7


def test_accumulate_handles_empty_and_none(mod):
    assert mod.accumulate({}, {"a": 1}) == {"a": 1}
    assert mod.accumulate({"a": 1}, {}) == {"a": 1}
    assert mod.accumulate(None, {"a": 1}) == {"a": 1}


def test_accumulate_concatenates_lists(mod):
    assert mod.accumulate({"f": ["a"]}, {"f": ["b"]})["f"] == ["a", "b"]


def test_accumulate_adds_hist_objects(mod):
    hist = pytest.importorskip("hist")
    import numpy as np
    def h():
        x = hist.Hist(hist.axis.Regular(4, 0, 4, name="x"),
                      storage=hist.storage.Weight())
        x.fill(x=np.array([0.5, 1.5]))
        return x
    out = mod.accumulate({"h": h()}, {"h": h()})
    assert out["h"].view().value.sum() == 4


# --------------------------- resumability --------------------------- #

def test_pending_chunks_skips_existing_output(mod, tmp_path):
    w = str(tmp_path)
    os.makedirs(os.path.join(w, "parts"))
    for i in (0, 2):
        open(os.path.join(w, "parts", f"part_{i}.pkl"), "wb").close()
    assert mod.pending_chunks(w, 5) == [1, 3, 4]


def test_pending_chunks_all_when_nothing_done(mod, tmp_path):
    assert mod.pending_chunks(str(tmp_path), 3) == [0, 1, 2]


# ------------------------ the generated submit ---------------------- #

@pytest.fixture
def submit_text(mod, tmp_path):
    analysis = tmp_path / "ana.py"
    analysis.write_text("def process(events, meta):\n    return {'n': 1}\n")
    chunks = [["/eos/uscms/store/a.root", "/eos/uscms/store/b.root"],
              ["/eos/uscms/store/c.root"]]
    sub = mod.write_condor(str(tmp_path), str(analysis), chunks, "Events",
                           1, 4000, 4000, mod.COFFEA_IMAGE, [0, 1])
    return open(sub).read(), tmp_path


def test_submit_requests_the_coffea_container(submit_text):
    text, _ = submit_text
    assert "+SingularityImage" in text
    assert "coffeateam/coffea-almalinux9" in text


def test_submit_transfers_inputs_because_workers_lack_nobackup(submit_text):
    text, _ = submit_text
    assert "should_transfer_files   = YES" in text
    assert "transfer_input_files" in text
    assert "lpc_scaleout.py" in text and "ana.py" in text


def test_submit_brings_the_output_back(submit_text):
    text, _ = submit_text
    assert "transfer_output_files   = part_$(chunk).pkl" in text
    assert "transfer_output_remaps" in text


def test_chunk_lists_are_written_as_xrootd(submit_text):
    _, tmp_path = submit_text
    files = json.load(open(os.path.join(str(tmp_path), "chunks", "chunk_0.json")))
    assert all(f.startswith("root://cmseos.fnal.gov//store/") for f in files), files


def test_wrapper_does_not_reference_nobackup(submit_text):
    """A /uscms_data path in the wrapper would fail on the worker."""
    _, tmp_path = submit_text
    wrapper = open(os.path.join(str(tmp_path), "run_chunk.sh")).read()
    assert "/uscms_data" not in wrapper, wrapper


# --------------------- the analysis contract ------------------------ #

def test_process_chunk_survives_one_bad_file(mod, tmp_path):
    """A single unreadable file must not lose the chunk."""
    analysis = tmp_path / "ana.py"
    analysis.write_text(
        "def open_events(path):\n"
        "    if 'bad' in path:\n"
        "        raise OSError('cannot read')\n"
        "    return ['e'] * 5\n"
        "def process(events, meta):\n"
        "    return {'n_seen': len(events)}\n")
    out = str(tmp_path / "part.pkl")
    res = mod.process_chunk(str(analysis), ["ok1.root", "bad.root", "ok2.root"], out)
    assert res["accumulator"]["n_seen"] == 10          # the two good files
    assert len(res["report"]["files_ok"]) == 2
    assert "bad.root" in res["report"]["files_failed"]
    assert os.path.exists(out)


def test_load_analysis_rejects_a_module_without_process(mod, tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("x = 1\n")
    with pytest.raises(AttributeError, match="process"):
        mod.load_analysis(str(bad))


def test_example_analysis_satisfies_the_contract(mod):
    ex = os.path.join(REPO, "scripts", "example_analysis_sidm.py")
    if not os.path.exists(ex):
        pytest.skip("example analysis not present")
    pytest.importorskip("coffea")
    m = mod.load_analysis(ex)
    assert callable(m.process)
    assert callable(m.open_events)


# ------------------- the documented interpreter idiom ---------------- #

def test_documented_interpreter_actually_has_coffea():
    """The skill and the system prompt tell the agent to find the toolkit
    interpreter from toolbase's install metadata. If that lookup returns an
    interpreter without coffea, every documented command fails.

    Guards against the earlier mistake of pointing at the venv next to the
    `toolbase` executable, which has toolbase but not coffea.
    """
    import glob
    import subprocess

    metas = glob.glob(os.path.expanduser(
        "~/.toolbase/cache/heptapod/*/.install_meta.yaml"))
    if not metas:
        pytest.skip("heptapod is not installed via toolbase here")

    yaml = pytest.importorskip("yaml")
    python_path = yaml.safe_load(open(metas[0]))["python_path"]
    assert os.path.exists(python_path), f"install meta names a missing interpreter: {python_path}"

    r = subprocess.run([python_path, "-c", "import coffea; print(coffea.__version__)"],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, (
        f"the interpreter the docs point at cannot import coffea:\n{r.stderr[-500:]}")
    assert r.stdout.strip(), "no coffea version reported"


def test_docs_do_not_point_at_the_toolbase_venv():
    """`dirname $(command -v toolbase)`/python is the WRONG interpreter."""
    repo_docs = [
        os.path.join(REPO, "skills", "scaleout", "SKILL.md"),
        os.path.join(REPO, "examples", "sidm", "README.md"),
        os.path.join(REPO, "examples", "sidm", "prompts", "system_prompt.md"),
    ]
    for d in repo_docs:
        if not os.path.exists(d):
            continue
        text = open(d).read()
        assert "dirname $(command -v toolbase)" not in text, (
            f"{os.path.basename(d)} points at the toolbase venv, which has no coffea")
