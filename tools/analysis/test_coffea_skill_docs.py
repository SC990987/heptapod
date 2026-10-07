"""
# test_coffea_skill_docs.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Execute every code block in the `coffea` skill's recipes.

A skill is only useful to an agent if its examples actually run. This module
extracts the fenced ``python`` blocks from
``skills/coffea/references/recipes.md`` and runs them in order against a
synthetic NanoAOD file, sharing one namespace so later blocks can use names
that earlier ones defined -- exactly how an agent would read them.

If coffea changes an API, this fails and the skill gets fixed, rather than
quietly teaching the agent something that no longer works.
"""
from __future__ import annotations

import os
import re

import numpy as np
import pytest

ak = pytest.importorskip("awkward")
pytest.importorskip("coffea")
pytest.importorskip("uproot")
pytest.importorskip("hist")

SKILL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "skills", "coffea")
RECIPES = os.path.join(SKILL_DIR, "references", "recipes.md")

# Blocks that are illustrative preamble rather than standalone steps: the first
# fenced block defines the shared preamble and is executed as the setup.
FENCE = re.compile(r"^```python\n(.*?)^```", re.M | re.S)


def build_nanoaod(path: str, n: int = 500) -> None:
    """A NanoAOD-shaped file with the collections the recipes reference."""
    import uproot
    rng = np.random.default_rng(7)
    nmu, njet, ngen = (rng.integers(0, 5, n), rng.integers(0, 7, n),
                       rng.integers(4, 10, n))

    def jag(counts, lo, hi):
        return ak.unflatten(rng.uniform(lo, hi, int(counts.sum())), counts)

    def jagi(counts, lo, hi):
        return ak.unflatten(rng.integers(lo, hi, int(counts.sum())), counts)

    mu_pt = jag(nmu, 3, 90)
    # a valid gen tree: every mother index is strictly below its daughter's,
    # so distinctParent terminates (a cyclic tree hangs forever)
    mother = ak.unflatten(np.concatenate([
        np.concatenate([[-1, 0, 0], [rng.integers(0, i) for i in range(3, c)]])
        for c in ngen]).astype(np.int32), ngen)
    data = {
        "run": np.full(n, 1, dtype=np.uint32),
        "luminosityBlock": rng.integers(1, 40, n).astype(np.uint32),
        "event": np.arange(n, dtype=np.uint64),
        "MET_pt": rng.uniform(0, 220, n), "MET_phi": rng.uniform(-np.pi, np.pi, n),
        "HLT_IsoMu24": rng.random(n) > 0.4,
        "Pileup_nTrueInt": rng.uniform(10, 60, n),
        "genWeight": rng.normal(1.0, 0.1, n),
        "nMuon": nmu.astype(np.uint32), "Muon_pt": mu_pt,
        "Muon_eta": jag(nmu, -2.6, 2.6), "Muon_phi": jag(nmu, -np.pi, np.pi),
        "Muon_mass": ak.zeros_like(mu_pt),
        "Muon_charge": ak.values_astype(jag(nmu, -1, 1) > 0, np.int32) * 2 - 1,
        "Muon_jetIdx": ak.values_astype(jagi(nmu, -1, 3), np.int32),
        "Muon_genPartIdx": ak.values_astype(jagi(nmu, -1, 2), np.int32),
        "nJet": njet.astype(np.uint32), "Jet_pt": jag(njet, 15, 400),
        "Jet_eta": jag(njet, -4.7, 4.7), "Jet_phi": jag(njet, -np.pi, np.pi),
        "Jet_mass": jag(njet, 0, 30),
        "nGenPart": ngen.astype(np.uint32), "GenPart_pt": jag(ngen, 1, 300),
        "GenPart_eta": jag(ngen, -4, 4), "GenPart_phi": jag(ngen, -np.pi, np.pi),
        "GenPart_mass": jag(ngen, 0, 90),
        # event 0 of every GenPart list: a resonance (pdgId 32) whose next two
        # entries are its lepton daughters, so the gen-pairing recipe has
        # something real to find
        "GenPart_pdgId": ak.concatenate(
            [ak.Array([[32, 13, -13]] * n),
             ak.values_astype(jagi(ngen - 3, -15, 25), np.int32)], axis=1),
        "GenPart_genPartIdxMother": mother,
        "GenPart_statusFlags": ak.values_astype(ak.concatenate(
            [ak.Array([[1 << 13] * 3] * n),
             jagi(ngen - 3, 0, 8192)], axis=1), np.int32),
        "GenPart_status": ak.values_astype(jagi(ngen, 1, 64), np.int32),
    }
    with uproot.recreate(path) as f:
        # a TTree, which f["Events"] = data no longer writes (uproot >= 5.7: RNTuple)
        f.mktree("Events", {key: value.type if isinstance(value, ak.Array) else value.dtype
                            for key, value in data.items()}).extend(data)



@pytest.fixture(scope="module")
def recipe_blocks():
    assert os.path.exists(RECIPES), f"recipes not found: {RECIPES}"
    blocks = FENCE.findall(open(RECIPES).read())
    assert len(blocks) >= 10, f"expected the recipes to have blocks, found {len(blocks)}"
    return blocks


@pytest.fixture(scope="module")
def namespace(tmp_path_factory, recipe_blocks):
    """Run the preamble block; hand back the namespace the rest share."""
    d = tmp_path_factory.mktemp("skill_docs")
    path = str(d / "nano.root")
    build_nanoaod(path)
    ns = {"PATH": path, "OUTDIR": str(d),
          "RESONANCE_PDGID": 32,
          "cvector": __import__("coffea.nanoevents.methods.vector",
                                fromlist=["*"]),
          "__name__": "__recipes__"}
    exec(compile(recipe_blocks[0], "<recipes preamble>", "exec"), ns)
    assert "events" in ns, "the preamble block must define `events`"
    return ns


def test_preamble_opens_the_file(namespace):
    assert len(namespace["events"]) == 500


def test_every_recipe_block_runs(namespace, recipe_blocks, capsys):
    """Run each block in document order in the shared namespace."""
    failures = []
    for i, block in enumerate(recipe_blocks[1:], start=1):
        try:
            exec(compile(block, f"<recipe block {i}>", "exec"), namespace)
        except Exception as e:                                   # noqa: BLE001
            first = next((line for line in block.strip().splitlines()
                          if line.strip() and not line.strip().startswith("#")), "")
            failures.append(f"block {i} ({first.strip()[:60]!r}): "
                            f"{type(e).__name__}: {e}")
    assert not failures, "recipe blocks failed:\n  " + "\n  ".join(failures)


def test_recipes_cover_the_core_apis(recipe_blocks):
    """The recipes must actually exercise the APIs the skill claims."""
    text = "\n".join(recipe_blocks)
    for api in ("nearest", "metric_table", "ak.combinations", "PackedSelection",
                "nminusone", "Weights", "hist.Hist", "distinctParent",
                "hasFlags", "delta_phi", "ak.pad_none", "ak.argsort"):
        assert api in text, f"recipes never demonstrate {api}"


def test_pitfalls_and_object_model_exist():
    for name in ("object_model.md", "subsystems.md", "pitfalls.md", "recipes.md"):
        p = os.path.join(SKILL_DIR, "references", name)
        assert os.path.exists(p), f"missing reference: {name}"
        assert os.path.getsize(p) > 1000, f"reference too small to be useful: {name}"


def test_skill_frontmatter_is_valid():
    yaml = pytest.importorskip("yaml")
    text = open(os.path.join(SKILL_DIR, "SKILL.md")).read()
    assert text.startswith("---"), "SKILL.md must open with YAML frontmatter"
    meta = yaml.safe_load(text.split("---")[1])
    assert meta["name"] == "coffea"
    assert meta["bundle"] == "coffea"
    assert len(meta["description"]) > 200, "description drives skill selection"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
