"""Tests for nanoaod_layout (pure python) and InspectFileTool.

The layout functions work on branch names alone, so most of this runs without any
analysis library. The branch list below is a reduced copy of a real LLP-NanoAOD file:
standard collections, a custom one without a mass (DSAMuon), a lower-case collection
(boostedTau), a GenPart that stores its momentum twice, and single-branch lists.
One test at the end reads a real file and is skipped when uproot is missing.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.analysis import nanoaod_layout as layout  # noqa: E402

BRANCHES = (
    ["run", "luminosityBlock", "event", "genWeight", "fixedGridRhoFastjetAll"]
    + ["nMuon"] + [f"Muon_{f}" for f in ("pt", "eta", "phi", "mass", "charge", "looseId",
                                         "jetIdx", "dsaMatch1idx", "dsaMatch2idx")]
    + ["nElectron"] + [f"Electron_{f}" for f in ("pt", "eta", "phi", "mass", "charge", "cutBased")]
    + ["nPhoton"] + [f"Photon_{f}" for f in ("pt", "eta", "phi", "mass", "charge", "cutBased",
                                             "electronVeto")]
    + ["nJet"] + [f"Jet_{f}" for f in ("pt", "eta", "phi", "mass", "jetId", "chEmEF", "neEmEF",
                                       "muEF")]
    + ["nDSAMuon"] + [f"DSAMuon_{f}" for f in ("pt", "eta", "phi", "charge", "dxy", "displacedID")]
    + ["nboostedTau"] + [f"boostedTau_{f}" for f in ("pt", "eta", "phi", "mass")]
    + ["nGenPart"] + [f"GenPart_{f}" for f in ("pt", "eta", "phi", "mass", "px", "py", "pz",
                                               "pdgId", "status", "statusFlags",
                                               "genPartIdxMother", "vx", "vy", "vz")]
    + ["nBS", "BS_x", "BS_y", "BS_z"]
    + [f"MET_{f}" for f in ("pt", "phi", "sumEt")]
    + [f"PV_{f}" for f in ("npvs", "npvsGood", "x", "y", "z", "ndof")]
    + [f"HLT_{f}" for f in ("IsoMu24", "DoubleL2Mu23NoVtx_2Cha", "DoubleL2Mu23NoVtx_2Cha_CosmicSeed",
                            "Mu50")]
    + [f"Flag_{f}" for f in ("METFilters", "goodVertices")]
    + ["nPSWeight", "PSWeight", "nOtherPV", "OtherPV_z"]
)


def test_collections_are_grouped_like_the_schema():
    c = layout.split_collections(BRANCHES)
    assert c["Muon"]["kind"] == "jagged" and c["Muon"]["counter"] == "nMuon"
    assert "pt" in c["Muon"]["fields"] and "dsaMatch1idx" in c["Muon"]["fields"]
    assert c["MET"]["kind"] == "record" and c["MET"]["counter"] is None
    assert c["HLT"]["kind"] == "record" and "IsoMu24" in c["HLT"]["fields"]
    assert c["run"]["kind"] == "value" and c["genWeight"]["kind"] == "value"
    assert c["PSWeight"]["kind"] == "jagged_value"
    assert c["OtherPV"]["kind"] == "jagged"
    assert c["BS"]["kind"] == "jagged"                 # a counter makes it a list
    # counters are not collections, including for lower-case names
    assert "nMuon" not in c and "nboostedTau" not in c
    assert c["boostedTau"]["kind"] == "jagged"


def test_counter_only_branch_is_a_plain_value():
    c = layout.split_collections(["nothing", "nGhost", "x_a"])
    assert c["nothing"]["kind"] == "value"
    assert c["nGhost"]["kind"] == "value"      # no Ghost_* branches: just a number per event


def test_schema_notes_flag_what_changes_how_the_file_is_read():
    notes = layout.schema_notes(layout.split_collections(BRANCHES))
    found = {(n["collection"], n["issue"]) for n in notes}
    assert ("DSAMuon", "no_vector_behaviour") in found
    assert ("boostedTau", "no_vector_behaviour") in found
    assert ("GenPart", "duplicate_momenta") in found
    # standard, well-formed collections are not flagged
    assert not any(c in ("Muon", "Jet", "MET", "PV") for c, _ in found)
    dsa = next(n for n in notes if n["collection"] == "DSAMuon")
    assert "no mass" in dsa["detail"]


def test_default_objects_follow_the_file():
    c = layout.split_collections(BRANCHES)
    objects = layout.default_objects(c)
    assert objects["muons"] == "Muon" and objects["met"] == "MET" and objects["gens"] == "GenPart"
    # data has no generator collections
    data = layout.split_collections([b for b in BRANCHES if not b.startswith(("GenPart", "nGenPart"))])
    assert "gens" not in layout.default_objects(data)
    # PuppiMET stands in for a missing MET
    puppi = layout.split_collections(["PuppiMET_pt", "PuppiMET_phi", "run"])
    assert layout.default_objects(puppi) == {"met": "PuppiMET"}
    # without a file, standard NanoAOD is assumed
    assert set(layout.default_objects(None)) >= {"muons", "electrons", "jets", "met", "pvs"}


def test_object_spec_knows_what_needs_wrapping():
    c = layout.split_collections(BRANCHES)
    muons = layout.object_spec("muons", "Muon", c)
    assert muons["kind"] == "jagged" and muons["kinematic"] and muons["wrap"] is None
    dsa = layout.object_spec("dsaMuons", "DSAMuon", c)
    assert dsa["wrap"] == {"mass": 0.105658}           # no mass branch: the muon mass is assumed
    assert any("muon mass" in n for n in dsa["notes"])
    tau = layout.object_spec("btaus", "boostedTau", c)
    assert tau["wrap"] == {"mass": None}               # has its own mass, only needs behaviour
    met = layout.object_spec("met", "MET", c)
    assert met["kind"] == "record" and not met["kinematic"]
    gens = layout.object_spec("gens", "GenPart", c)
    assert gens["mc_only"] and gens["wrap"] is None
    assert any("polar set" in n for n in gens["notes"])
    try:
        layout.object_spec("x", "NoSuchCollection", c)
    except KeyError:
        pass
    else:
        raise AssertionError("an unknown collection must raise KeyError")


def test_guesses_when_the_file_does_not_say():
    # a mass for a collection that stores none: whole words of the name, not fragments
    assert layout.guess_mass("DSAMuon")[0] == 0.105658 and layout.guess_mass("dimuon")[0] == 0.105658
    assert layout.guess_mass("LowPtElectron")[0] == 0.000511
    assert layout.guess_mass("boostedTau")[0] == 1.77686
    assert layout.guess_mass("IsoTrack")[0] == 0.13957 and layout.guess_mass("PFCands")[0] == 0.13957
    assert layout.guess_mass("MultiJet") == (0.0, "zero")          # not a muon
    assert layout.guess_mass("SelectedTrack")[0] == 0.13957        # not an electron
    assert layout.guess_mass("Photon")[0] == 0.0 and layout.guess_mass("Thing")[0] == 0.0
    # the shape of a standard collection, with no file to look at
    for name in ("MET", "PuppiMET", "RawMET", "CaloMET", "GenMET", "PV", "HLT", "Flag", "Pileup"):
        assert layout.object_spec("x", name)["kind"] == "record", name
    for name in ("Muon", "Jet", "SV", "GenPart", "SomethingNew"):
        assert layout.object_spec("x", name)["kind"] == "jagged", name
    assert layout.object_spec("met", "RawMET")["mixin"] == "MissingET"
    assert layout.object_spec("x", "SomethingNew")["mixin"] is None
    assert layout.object_spec("gens", "GenPart")["mc_only"] and not layout.object_spec("m", "Muon")["mc_only"]
    # a plain list of numbers, and a list of objects without pt/eta/phi
    assert layout.object_spec("ps", "PSWeight")["kind"] == "value"
    assert layout.object_spec("o", "OtherPV")["kinematic"] is False
    assert layout.object_spec("m", "Muon")["kinematic"] is True


def test_a_branch_named_like_a_collection_takes_its_place():
    c = layout.split_collections(["nTrk", "Trk", "Trk_pt", "Trk_eta", "Trk_phi",
                                  "weight", "weight_up", "weight_down", "run"])
    # coffea reads "Trk" as that one branch: Trk_pt and the others are out of reach
    assert c["Trk"] == {"kind": "jagged_value", "counter": "nTrk", "fields": [],
                        "shadowed": ["eta", "phi", "pt"]}
    assert c["weight"]["kind"] == "value" and c["weight"]["shadowed"] == ["down", "up"]
    assert "shadowed" not in c["run"]
    notes = layout.schema_notes(c)
    assert [(n["collection"], n["issue"]) for n in notes] == [("Trk", "shadowed_branches"),
                                                             ("weight", "shadowed_branches")]
    assert "hidden_branches=['Trk']" in notes[0]["detail"] and "Trk_eta" in notes[0]["detail"]
    assert layout.other_kinematic_collections(c, []) == []
    # a counter with an underscore in its name, as CMS data has: coffea sees no list there
    data = layout.split_collections(["nProton_multiRP", "Proton_multiRP_xi",
                                     "nProton_singleRP", "Proton_singleRP_xi"])
    assert data["Proton"] == {"kind": "record", "counter": None,
                              "fields": ["multiRP_xi", "singleRP_xi"]}


def test_fields_a_behaviour_needs_are_checked_the_way_coffea_checks_them():
    def missing(name, fields, kind="jagged"):
        return layout.missing_required(name, {"kind": kind, "counter": None, "fields": fields})

    assert missing("PV", ["npvs", "npvsGood"], "record") == ["x", "y", "z"]
    assert missing("PV", ["x", "y", "z", "npvs"], "record") == []
    assert missing("SV", ["x", "y", "z", "pt", "eta", "phi"]) == ["mass"]
    assert missing("Muon", ["pt", "eta", "phi", "mass"]) == ["charge"]
    assert missing("Muon", ["pt", "eta", "phi", "charge"]) == ["mass"]
    assert missing("Muon", ["px", "py", "pz", "energy", "charge"]) == []
    assert missing("GenPart", ["pt", "eta", "phi", "mass"]) == []     # a vector, not a candidate
    # what the schema supplies itself is not missing
    assert missing("Photon", ["pt", "eta", "phi"]) == [] and missing("Jet", ["pt", "eta", "phi", "mass"]) == []
    assert missing("MET", ["pt", "phi", "sumEt"], "record") == []
    assert missing("MET", ["sumEt"], "record") == ["pt", "phi"]
    # no behaviour, nothing to check
    assert missing("DSAMuon", ["pt"]) == [] and missing("HLT", ["IsoMu24"], "record") == []
    # a missing-energy vector must not carry a third or fourth coordinate
    traits = layout.collection_traits("MET", {"kind": "record", "counter": None,
                                              "fields": ["pt", "phi", "eta", "mass", "sumEt"]})
    assert traits["conflicts"] and traits["conflicting_fields"] == ["eta", "mass"]

    c = layout.split_collections([b for b in BRANCHES
                                  if b not in ("PV_x", "PV_y", "PV_z", "Muon_charge")])
    issues = {(n["collection"], n["issue"]) for n in layout.schema_notes(c)}
    assert ("PV", "missing_fields") in issues and ("Muon", "missing_fields") in issues
    defaults = layout.default_objects(c)
    assert "pvs" not in defaults and "muons" not in defaults and defaults["electrons"] == "Electron"
    skipped = layout.skipped_defaults(c)
    assert set(skipped) == {"pvs", "muons"}
    assert "mixins={'PV': 'NanoCollection'}" in skipped["pvs"]
    assert "constant_fields={'Muon_charge': <value>}" in skipped["muons"]
    assert any("refuse to build" in note for note in layout.object_spec("pvs", "PV", c)["notes"])
    assert layout.skipped_defaults(layout.split_collections(BRANCHES)) == {}
    assert layout.skipped_defaults(None) == {}


def test_patterns_and_requirements():
    assert layout.match_branches(BRANCHES, "HLT_DoubleL2Mu*") == [
        "HLT_DoubleL2Mu23NoVtx_2Cha", "HLT_DoubleL2Mu23NoVtx_2Cha_CosmicSeed"]
    req = layout.check_required(BRANCHES, ["Muon", "Muon_pt", "DSAMuon_mass", "Tau"])
    assert req == {"present": ["Muon", "Muon_pt"], "missing": ["DSAMuon_mass", "Tau"]}
    others = layout.other_kinematic_collections(layout.split_collections(BRANCHES), ["Muon"])
    assert "DSAMuon" in others and "Muon" not in others and "MET" not in others


def test_summary_is_truncated():
    summary = layout.summarise(layout.split_collections(BRANCHES), max_fields=3)
    assert summary["Muon"]["n_fields"] == 9 and len(summary["Muon"]["fields"]) == 3
    assert summary["Muon"]["fields_truncated"] is True
    assert "run" not in summary                        # single branches are listed elsewhere
    assert "fields_truncated" not in layout.summarise(layout.split_collections(BRANCHES), 0)["Muon"]


def test_coordinate_conflicts_follow_coffea():
    conflicts = layout.coordinate_conflicts
    assert conflicts(["pt", "eta", "phi", "mass", "charge", "dxy"]) == []
    assert conflicts(["pt", "phi", "sumEt"]) == []                      # a 2D polar vector
    assert conflicts(["x", "y", "z", "t"]) == []
    assert len(conflicts(["pt", "eta", "phi", "px", "py", "pz"])) == 2  # azimuthal + longitudinal
    assert "energy, mass" in conflicts(["pt", "eta", "phi", "mass", "energy"])[0]
    assert len(conflicts(["pt", "eta", "phi", "x", "y", "z"])) == 2     # a position next to a momentum
    assert conflicts(["pt", "phi", "x"]) and conflicts(["pt", "eta", "theta"])
    assert "px, x" in conflicts(["x", "px", "y"])[0]


def test_traits_separate_what_the_schema_hides_from_what_is_left():
    def traits(name, fields):
        return layout.collection_traits(name, {"kind": "jagged", "counter": "n" + name, "fields": fields})

    gen = traits("GenPart", ["pt", "eta", "phi", "mass", "px", "py", "pz", "pdgId"])
    assert gen["duplicate_momenta"] == ["px", "py", "pz"] and gen["conflicts"] == []
    jet = traits("Jet", ["pt", "eta", "phi", "mass", "energy"])
    assert jet["conflicts"] and jet["conflicting_fields"] == ["energy"]
    # coffea renames this one itself, and vertices are positions, not vectors
    assert traits("Electron", ["pt", "eta", "phi", "mass", "energy", "charge"])["conflicts"] == []
    assert traits("SV", ["pt", "eta", "phi", "mass", "x", "y", "z"])["conflicts"] == []
    # a collection without a behaviour is never checked by coffea
    assert traits("DSAMuon", ["pt", "eta", "phi", "px", "py", "pz"])["conflicts"] == []
    notes = layout.schema_notes({"Jet": {"kind": "jagged", "counter": "nJet",
                                         "fields": ["pt", "eta", "phi", "mass", "energy"]}})
    assert [n["issue"] for n in notes] == ["conflicting_coordinates"] and "Jet_energy" in notes[0]["detail"]
    spec = layout.object_spec("vtx", "MyVtx", {"MyVtx": {
        "kind": "jagged", "counter": "nMyVtx", "fields": ["pt", "eta", "phi", "x", "y", "z"]}})
    assert spec["wrap"] is not None and any("vx, vy, vz" in note for note in spec["notes"])


class _Skipped(Exception):
    pass


def _skip(reason):
    """Skip properly under pytest; say so when run as a script."""
    if "pytest" in sys.modules:
        import pytest
        pytest.skip(reason)
    raise _Skipped(reason)


def _tool():
    from tools.analysis.nanoaod_inspect import InspectFileTool
    return InspectFileTool


def test_describe_builds_the_report_without_a_file():
    try:
        tool = _tool()
    except ImportError:
        _skip("orchestral is not installed")
    report = tool.describe(BRANCHES, collection="DSAMuon", branch_pattern="Flag_*",
                           require=["Jet_muEF", "Jet_btagDeepB"], max_fields=4)
    assert report["n_branches"] == len(BRANCHES)
    assert report["collections"]["Jet"]["kind"] == "jagged"
    assert "genWeight" in report["per_event_branches"]
    detail = report["collection_detail"]
    assert detail["fields"] == sorted(["pt", "eta", "phi", "charge", "dxy", "displacedID"])
    assert detail["has_kinematics"] and not detail["has_mass"] and detail["mixin"] is None
    assert report["matching_branches"] == ["Flag_METFilters", "Flag_goodVertices"]
    assert report["required"] == {"present": ["Jet_muEF"], "missing": ["Jet_btagDeepB"]}
    assert "all_branches" not in report


def test_tool_refuses_paths_outside_the_sandbox_and_missing_files():
    try:
        tool = _tool()
    except ImportError:
        _skip("orchestral is not installed")
    with tempfile.TemporaryDirectory() as base:
        out = tool(base_directory=base, root_path="../outside.root")._run()
        assert "Access Denied" in out
        out = tool(base_directory=base, root_path="missing.root")._run()
        assert "File Not Found" in out


def test_tool_reads_a_real_file():
    try:
        tool = _tool()
        import awkward as ak
        import numpy as np
        import uproot
    except ImportError as exc:
        _skip(str(exc))
    with tempfile.TemporaryDirectory() as base:
        n = 20
        counts = np.arange(n) % 3
        pt = ak.unflatten(np.linspace(5, 50, int(counts.sum())), counts)
        data = {
            "run": np.ones(n, dtype=np.uint32),
            "Muon": ak.zip({"pt": pt, "eta": pt * 0, "phi": pt * 0, "mass": pt * 0}),
            "MET_pt": np.linspace(0, 100, n),
        }
        with uproot.recreate(os.path.join(base, "f.root")) as f:
            # a TTree, which f["Events"] = data no longer writes (uproot >= 5.7: RNTuple)
            f.mktree("Events", {key: value.type if isinstance(value, ak.Array) else value.dtype
                                for key, value in data.items()}).extend(data)
        out = json.loads(tool(base_directory=base, root_path="f.root", collection="Muon",
                              require=["Muon_pt", "Jet"])._run())
        assert out["status"] == "ok" and out["n_entries"] == n and out["trees"] == {"Events": n}
        assert out["collections"]["Muon"] == {"kind": "jagged", "n_fields": 4, "counter": "nMuon",
                                              "fields": ["eta", "mass", "phi", "pt"]}
        assert out["collections"]["MET"]["kind"] == "record"
        assert out["required"] == {"present": ["Muon_pt"], "missing": ["Jet"]}
        assert "unreadable" not in out
        described = layout.read_tree_layout(os.path.join(base, "f.root"))
        assert described["kind"] == "TTree" and described["unreadable"] == {}
        missing_tree = tool(base_directory=base, root_path="f.root", tree_name="Nope")._run()
        assert "Tree Not Found" in missing_tree and "present: ['Events']" in missing_tree
        # what uproot >= 5.7 writes for a plain assignment is an RNTuple: said, not described
        with uproot.recreate(os.path.join(base, "g.root")) as f:
            f["Events"] = {"x": np.arange(n)}
        kind = layout.read_tree_layout(os.path.join(base, "g.root"))["kind"]
        answer = tool(base_directory=base, root_path="g.root")._run()
        assert ("Not A TTree" in answer) == (kind == "RNTuple"), (kind, answer)
        missing_coll = tool(base_directory=base, root_path="f.root", collection="Tau")._run()
        assert "Collection Not Found" in missing_coll


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
    print(f"nanoaod inspect tests: {passed} passed, {skipped} skipped, {failed} failed")
    if skipped and no_skips:
        print("--no-skips: a skipped test counts as a failure")
    sys.exit(1 if failed or (skipped and no_skips) else 0)
