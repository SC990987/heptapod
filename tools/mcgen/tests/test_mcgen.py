"""Tests for the CMS simulation tools that need no CMSSW.

They cover the campaign table and the chains built from it, the cmsDriver commands,
the generator fragments, writing a production (its scripts, its shell variables, its
condor submit file), reading where a production stands, and the four tools' handling
of requests. Nothing here runs CMSSW or submits a job: whether a production really
runs is shown at the LPC (tools/mcgen/README.md, "Verification").

    python tools/mcgen/tests/test_mcgen.py [--no-skips]
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.mcgen import _campaigns as campaigns  # noqa: E402
from tools.mcgen import _fragment as fragments  # noqa: E402
from tools.mcgen import _jobs as jobs  # noqa: E402
from tools.mcgen import _production as production  # noqa: E402

SETTINGS = ["Charmonium:all = on", "443:onMode = off", "443:onIfMatch = 13 -13",
            "PhaseSpace:pTHatMin = 8."]
FILTERS = """mumufilter = cms.EDFilter("MCParticlePairFilter",
    ParticleID1 = cms.untracked.vint32(13),
    ParticleID2 = cms.untracked.vint32(13),
)
ProductionFilterSequence = cms.Sequence(generator * mumufilter)"""
LHE_FRAGMENT = """import FWCore.ParameterSet.Config as cms
externalLHEProducer = cms.EDProducer("ExternalLHEProducer",
    args = cms.vstring("/cvmfs/cms.cern.ch/phys_generator/gridpacks/x.tar.xz"),
    nEvents = cms.untracked.uint32(100),
)
generator = cms.EDFilter("Pythia8ConcurrentHadronizerFilter")
"""


class _Skipped(Exception):
    pass


def _skip(reason):
    if "pytest" in sys.modules:
        import pytest
        pytest.skip(reason)
    raise _Skipped(reason)


def _refused(error, call, *args, **kwargs):
    try:
        call(*args, **kwargs)
    except error as exc:
        return f"{exc} | {getattr(exc, 'suggestion', '')}"
    raise AssertionError(f"{getattr(call, '__name__', call)} should have been refused")


def _fragment():
    return fragments.render_pythia8(SETTINGS, campaigns.COM_ENERGY_GEV, campaigns.TUNE_IMPORT, FILTERS)


def _create(tmp, name="jpsi", **kwargs):
    kwargs.setdefault("run_period", "2023")
    kwargs.setdefault("events", 1050)
    kwargs.setdefault("events_per_job", 500)
    kwargs.setdefault("fragment", _fragment())
    return production.create(Path(tmp) / name, name=name, **kwargs)


# --------------------------------------------------------------------------- #
# campaigns and chains
# --------------------------------------------------------------------------- #

def test_the_campaign_table_is_complete():
    assert list(campaigns.RUN_PERIODS) == ["2022", "2022EE", "2023", "2023BPix", "2024"]
    for period, entry in campaigns.RUN_PERIODS.items():
        assert set(entry["steps"]) == {"GEN-SIM", "LHE-GEN-SIM", "DIGI-HLT", "RECO", "MINIAOD", "NANOAOD"}
        assert period in campaigns.DESCRIPTIONS
        for name, step in entry["steps"].items():
            assert re.fullmatch(r"CMSSW_\d+_\d+_\d+(_patch\d+)?", step["release"]), step
            assert step["scram_arch"].startswith("el8_amd64_"), step      # el8, as in McM
            assert re.fullmatch(r"[A-Z0-9]+-" + entry["campaign"] + r"\w+-\d{5}", step["mcm"]), step
            for key in ("step", "era", "conditions", "eventcontent", "datatier"):
                assert key in step["options"], (period, name, key)
        assert "beamspot" in entry["steps"]["GEN-SIM"]["options"]
        digi = entry["steps"]["DIGI-HLT"]["options"]
        assert digi["datamix"] == "PreMix" and digi["pileup_input"].startswith("dbs:/Neutrino_E-10_gun/")
        assert "DATAMIX" in digi["step"] and "premix_stage2" in digi["procModifiers"]
        described = campaigns.describe(period)
        assert described["hlt_menu"] == campaigns.hlt_menu(period) and described["hlt_menu"]
        assert described["premix_dataset"].endswith("/PREMIX")
    assert campaigns.hlt_menu("2024") == "2024v14"


def test_chains_for_every_tier_pileup_and_generator():
    for period in campaigns.RUN_PERIODS:
        full = campaigns.build_steps(period, "NANOAOD", "premix")
        assert [s["name"] for s in full] == ["GEN-SIM", "DIGI-HLT", "RECO", "MINIAOD", "NANOAOD"]
        assert [s["input"] for s in full] == [None] + [s["output"] for s in full[:-1]]
        assert full[-1]["options"]["eventcontent"] == "NANOAODSIM"            # flat, not EDM
        assert full[1]["options"]["pileup_input"].startswith("dbs:")
        quiet = campaigns.build_steps(period, "NANOAOD", "none")[1]["options"]
        assert "DATAMIX" not in quiet["step"] and "HLT:" in quiet["step"]
        assert "datamix" not in quiet and "pileup_input" not in quiet
        assert "premix_stage2" not in quiet.get("procModifiers", "")
        assert quiet["eventcontent"] == "RAWSIM"
        gen = campaigns.build_steps(period, "GEN")
        assert len(gen) == 1 and gen[0]["options"]["step"] == "GEN" and gen[0]["tier"] == "GEN"
        lhe = campaigns.build_steps(period, "GEN-SIM", lhe=True)
        assert lhe[0]["options"]["step"] == "LHE,GEN,SIM"
        assert lhe[0]["release"] == campaigns.RUN_PERIODS[period]["steps"]["LHE-GEN-SIM"]["release"]
        assert campaigns.build_steps(period, "GEN", lhe=True)[0]["options"]["step"] == "LHE,GEN"
        assert [s["name"] for s in campaigns.build_steps(period, "AOD")] == ["GEN-SIM", "DIGI-HLT", "RECO"]
        assert campaigns.build_steps(period, "MINIAOD")[-1]["tier"] == "MINIAOD"
    # 2022 keeps its pixel-quality modifier without pileup
    assert campaigns.build_steps("2022", pileup="none")[1]["options"]["procModifiers"] == "siPixelQualityRawToDigi"
    assert "procModifiers" not in campaigns.build_steps("2023", pileup="none")[1]["options"]
    for bad, expected in ((("2025", "NANOAOD", "premix"), "unknown run period"),
                          (("2023", "RAW", "premix"), "unknown final tier"),
                          (("2023", "NANOAOD", "classic"), "pileup must be")):
        assert expected in _refused(campaigns.CampaignError, campaigns.check_choice, *bad)
    assert campaigns.releases(campaigns.build_steps("2024")) == [
        {"release": "CMSSW_14_0_19", "scram_arch": "el8_amd64_gcc12"},
        {"release": "CMSSW_15_0_2", "scram_arch": "el8_amd64_gcc12"}]


def test_cmsdriver_commands():
    steps = campaigns.build_steps("2023", "NANOAOD", "none")
    first = campaigns.cmsdriver_args(steps[0], "Configuration/GenProduction/python/x.py", 50, "cfg/a.py")
    assert first[:2] == ["cmsDriver.py", "Configuration/GenProduction/python/x.py"]
    assert first[first.index("-n") + 1] == "50" and "--no_exec" in first and "--mc" in first
    assert "--filein" not in first and first[first.index("--fileout") + 1] == "file:step1_GEN-SIM.root"
    second = campaigns.cmsdriver_args(steps[1], "ignored", 50, "cfg/b.py")
    assert second[1] == "--python_filename" and second[second.index("-n") + 1] == "-1"
    assert second[second.index("--filein") + 1] == "file:step1_GEN-SIM.root"
    assert second[second.index("--conditions") + 1] == "130X_mcRun3_2023_realistic_v14"


# --------------------------------------------------------------------------- #
# fragments
# --------------------------------------------------------------------------- #

def test_fragments_rendered_checked_and_refused():
    text = _fragment()
    compile(text, "fragment", "exec")
    assert '"443:onIfMatch = 13 -13",' in text and "comEnergy = cms.double(13600.0)" in text
    assert "MCTunesRun3ECM13p6TeV.PythiaCP5Settings_cfi" in text and "Pythia8ConcurrentGeneratorFilter" in text
    assert text.rstrip().endswith("ProductionFilterSequence = cms.Sequence(generator * mumufilter)")
    plain = fragments.render_pythia8(["HardQCD:all = on"], 13600.0, campaigns.TUNE_IMPORT)
    assert "ProductionFilterSequence = cms.Sequence(generator)" in plain
    # a setting is a python string in the fragment, whatever it says: it cannot become code
    quoted = fragments.render_pythia8(['x = "y"; import os'], 13600.0, campaigns.TUNE_IMPORT)
    assert '"x = \\"y\\"; import os",' in quoted and "\nimport os" not in quoted
    for settings, expected in (([], "non-empty list"), ("HardQCD:all = on", "non-empty list"),
                               (["a\nb"], "one printable line"), ([7], "one printable line"),
                               (["= 5"], "does not look like")):
        assert expected in _refused(fragments.FragmentError, fragments.render_pythia8,
                                    settings, 13600.0, campaigns.TUNE_IMPORT)
    assert "must define ProductionFilterSequence" in _refused(
        fragments.FragmentError, fragments.render_pythia8, ["HardQCD:all = on"], 13600.0,
        campaigns.TUNE_IMPORT, "f = cms.EDFilter('X')")
    assert "not valid python" in _refused(fragments.FragmentError, fragments.check, "generator = (")
    assert "defines no `generator`" in _refused(fragments.FragmentError, fragments.check, "x = 1\n")
    assert fragments.needs_lhe(LHE_FRAGMENT) and not fragments.needs_lhe(text)
    assert "not a McM request id" in _refused(fragments.FragmentError, fragments.from_mcm, "nonsense")
    # McM answers with the fragment as plain text, or as a JSON string; both are read
    import io
    original = fragments.urllib.request.urlopen
    try:
        for body in (LHE_FRAGMENT, json.dumps(LHE_FRAGMENT)):
            fragments.urllib.request.urlopen = lambda request, timeout=0, _b=body: io.BytesIO(_b.encode())
            assert fragments.from_mcm("BPH-Run3Summer23wmLHEGS-00001") == LHE_FRAGMENT
        fragments.urllib.request.urlopen = lambda request, timeout=0: io.BytesIO(
            b'{"results": false, "message": "X does not exist"}')
        assert "does not exist" in _refused(fragments.FragmentError, fragments.from_mcm, "BPH-Run3Summer23GS-00009")
    finally:
        fragments.urllib.request.urlopen = original


# --------------------------------------------------------------------------- #
# writing a production
# --------------------------------------------------------------------------- #

def test_a_production_is_written_complete():
    assert production.split_events(1050, 500) == [500, 500, 50]
    assert production.split_events(10, 5) == [5, 5]
    assert production.output_path("root://h//a/", "NANOAOD", "x", 3) == "root://h//a/NANOAOD/x_NANOAOD_0003.root"
    with tempfile.TemporaryDirectory() as tmp:
        d = _create(tmp, pileup="none", keep_tiers=["MINIAOD", "NANOAOD"], threads=2, seed=99)
        root = Path(tmp) / "jpsi"
        for relative in ("production.json", "fragment.py", "bin/production.env", "bin/configure.sh",
                         "bin/run_job.sh", "bin/driver.sh", "bin/condor_job.sh", "bin/in_el8.sh",
                         "bin/wrap_cfg.py", "cfg", "jobs", "condor/logs", "condor/out"):
            assert (root / relative).exists(), relative
        for script in ("configure.sh", "run_job.sh", "driver.sh", "condor_job.sh", "in_el8.sh"):
            assert os.access(root / "bin" / script, os.X_OK), script
            assert subprocess.run(["bash", "-n", str(root / "bin" / script)]).returncode == 0, script
        compile((root / "bin" / "wrap_cfg.py").read_text(), "wrap_cfg.py", "exec")
        saved = json.loads((root / "production.json").read_text())
        assert saved == json.loads(json.dumps(d))
        assert d["jobs"] == [500, 500, 50] and d["keep_tiers"] == ["MINIAOD", "NANOAOD"]
        assert d["output"] == str(root / "output") and d["seed"] == 99 and not d["lhe"]
        assert d["fragment_module"] == "Configuration/GenProduction/python/jpsi_fragment.py"
        # the shell variables are what bash reads back
        shown = subprocess.run(
            ["bash", "-c", f'source {root}/bin/production.env; echo "$MCGEN_NAME|$MCGEN_KEEP|'
                           '${MCGEN_JOB_EVENTS[2]}|${#MCGEN_STEPS[@]}|${MCGEN_STEPS[1]}"; eval "echo $MCGEN_CMSDRIVER_2"'],
            capture_output=True, text=True, check=True).stdout.splitlines()
        assert shown[0] == ("jpsi|MINIAOD NANOAOD|50|5|2|DIGI-HLT|GEN-SIM-RAW|CMSSW_13_0_14|el8_amd64_gcc11|"
                            "step2_DIGI-HLT_cfg.py|step1_GEN-SIM.root|step2_DIGI-HLT.root")
        assert shown[1].startswith("cmsDriver.py --python_filename @CFG@/step2_DIGI-HLT_cfg.py")
        assert "--step DIGI,L1,DIGI2RAW,HLT:2023v12" in shown[1]
        # refusals: the directory exists, names, numbers, tiers
        assert "exists already" in _refused(production.ProductionError, _create, tmp)
        for kwargs, expected in (({"name": "a b"}, "cannot name"), ({"events": 0}, "positive whole"),
                                 ({"threads": 9}, "at most"), ({"seed": True}, "seed must be"),
                                 ({"final_tier": "AOD", "keep_tiers": ["NANOAOD"]}, "not produced")):
            name = kwargs.pop("name", "other")
            assert expected in _refused(production.ProductionError, _create, tmp, name=name, **kwargs)
        assert not (Path(tmp) / "other").exists()
        lhe = _create(tmp, name="lhe", fragment=LHE_FRAGMENT, final_tier="GEN-SIM")
        assert lhe["lhe"] and lhe["steps"][0]["options"]["step"] == "LHE,GEN,SIM"
        premix = _create(tmp, name="pu", output="root://cmseos.fnal.gov//store/user/someone/x/")
        assert premix["premix_dataset"].endswith("/PREMIX") and premix["premix_files"] == production.PREMIX_FILES
        digi = premix["steps"][1]["cmsdriver"]
        assert digi[digi.index("--pileup_input") + 1] == "@PREMIX@" and not any(a.startswith("dbs:") for a in digi)
        env = subprocess.run(["bash", "-c", f"source {tmp}/pu/bin/production.env; "
                                            'echo "$MCGEN_PREMIX_DATASET|$MCGEN_PREMIX_FILES"'],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert env == f"{premix['premix_dataset']}|{production.PREMIX_FILES}"
        assert _create(tmp, name="pugen", final_tier="GEN")["premix_files"] is None   # no DIGI step
        configure = (Path(tmp) / "pu" / "bin" / "configure.sh").read_text()
        assert "dasgoclient" in configure and "filelist:$PROD/cfg/premix_files.txt" in configure
        submit = production.condor_submit_file(Path(tmp) / "pu", premix, "/tmp/x509up_u1", [0, 2])
        for line in ('+DesiredOS              = "EL8"', "x509userproxy           = /tmp/x509up_u1",
                     "queue Job in (0, 2)", "+McgenJob               = $(Job)", "request_cpus            = 1",
                     "transfer_output_files   = summary_$(Job).txt"):
            assert line in submit, line
        assert f"{tmp}/pu/cfg" in submit and "arguments               = $(Job)" in submit


# --------------------------------------------------------------------------- #
# where a production stands
# --------------------------------------------------------------------------- #

GENXSEC = """GEN-SIM: TrigReport Events total = 100 passed = 25 failed = 75
GEN-SIM: Before Filter: total cross section = 2.000e+06 +- 1.000e+04 pb
GEN-SIM: Filter efficiency (taking into account weights)= (25) / (100) = 2.500e-01 +- 4.330e-02
GEN-SIM: Filter efficiency (event-level)= (25) / (100) = 2.500e-01 +- 4.330e-02    [TO BE USED IN MCM]
GEN-SIM: After filter: final cross section = 5.000e+05 +- 8.700e+04 pb
NANOAOD: TrigReport Events total = 25 passed = 25 failed = 0
"""


def test_status_reads_jobs_generator_and_outputs():
    with tempfile.TemporaryDirectory() as tmp:
        d = _create(tmp, pileup="none", events=300, events_per_job=100)
        root = Path(tmp) / "jpsi"
        report = jobs.status(root)
        assert report["stage"] == "prepared" and report["job_counts"] == {"not started": 3}
        assert not report["complete"] and "for_framework" not in report
        (root / "driver.status").write_text("1 DRIVER local start 1\n2 CONFIGURE start\n3 CONFIGURE done\n")
        (root / "cfg" / ".configured").write_text("3\n")
        for job, text, status in (
                (0, GENXSEC + f"output NANOAOD {root}/output/NANOAOD/jpsi_NANOAOD_0000.root\nDONE\n",
                 "1 START job 0\n2 STEP 1 GEN-SIM start\n9 DONE\n"),
                (1, GENXSEC.replace("(25) / (100)", "(35) / (100)").replace("5.000e+05", "7.000e+05")
                 + "DONE\n", "1 START job 1\n9 DONE\n"),
                (2, "FAILED RECO: cmsRun exited with 65\nRECO log: some exception\n",
                 "1 START job 2\n2 STEP 3 RECO start\n3 FAILED RECO: cmsRun exited with 65\n")):
            work = root / "jobs" / f"job_{job:04d}"
            work.mkdir(parents=True)
            (work / "summary.txt").write_text(text)
            (work / "status").write_text(status)
        (root / "output" / "NANOAOD").mkdir(parents=True)
        for job in (0, 1):
            (root / "output" / "NANOAOD" / f"jpsi_NANOAOD_{job:04d}.root").write_bytes(b"x")
        report = jobs.status(root)
        assert report["stage"] == "configured" and report["job_counts"] == {"done": 2, "failed": 1}
        failed = report["jobs"][2]
        assert failed["state"] == "failed" and failed["error"] == "RECO: cmsRun exited with 65"
        assert failed["log_tail"] == ["RECO log: some exception"]
        assert report["jobs"][0]["events_per_step"]["GEN-SIM"] == {"events_in": 100, "events_out": 25}
        generator = report["generator"]
        assert generator["generated"] == 200 and generator["passed_filter"] == 60
        assert abs(generator["filter_efficiency"] - 0.3) < 1e-12
        assert abs(generator["cross_section_before_filter_pb"] - 2.0e6) < 1e-3
        assert abs(generator["cross_section_pb"] - 6.0e5) < 1e-3      # (5e5 + 7e5) / 2
        assert report["n_output_files"] == 2 and not report["complete"]
        framework = report["for_framework"]
        location = framework["sample_locations"][0]
        assert {k: location[k] for k in ("directory", "name", "year", "is_data", "config", "tag", "xsec")} == {
            "directory": f"{root}/output/NANOAOD/", "name": "jpsi", "year": "2023", "is_data": False,
            "config": "jpsi.yaml", "tag": "mcgen", "xsec": 6.0e5}
        assert "Run3Summer23" in location["comment"] and framework["cross_section_pb"] == 6.0e5
        assert framework["sample_file"].endswith("jpsi_NANOAOD_0000.root") and framework["year"] == "2023"
        assert "not every job has finished" in framework["note"]
        # a job run again starts its record afresh, so it is no longer failed
        (root / "jobs" / "job_0002" / "status").write_text("1 START job 2\n2 STEP 1 GEN-SIM start\n")
        (root / "jobs" / "job_0002" / "summary.txt").write_text("job 2 events 100\n")
        assert jobs.status(root)["jobs"][2]["state"] == "running"
        assert d["output"].endswith("/output")
        # a test run (test_events) is not the job: not finished, not in the generator numbers
        (root / "jobs" / "job_0002" / "status").write_text("1 START job 2 events 40 test\n9 DONE\n")
        (root / "jobs" / "job_0002" / "summary.txt").write_text(
            "job 2 events 40 test\n" + GENXSEC.replace("(25) / (100)", "(9) / (40)")
            + f"test output NANOAOD {root}/jobs/job_0002/step5_NANOAOD.root\nDONE\n")
        report = jobs.status(root)
        assert report["jobs"][2]["state"] == "test done" and report["jobs"][2]["test_events"] == 40
        assert "finished" not in report["jobs"][2] and report["job_counts"]["test done"] == 1
        assert report["generator"]["generated"] == 200 and report["n_output_files"] == 2
        assert 'TEST=${MCGEN_TEST_EVENTS:+test}' in production.RUN_JOB
        assert 'echo "test output $tier $WORK/$output"' in production.RUN_JOB


def test_job_states_and_generator_numbers():
    assert jobs._job_state([]) == {"state": "pending"}
    assert jobs._job_state([["1", "START"], ["2", "STEP", "2", "DIGI-HLT", "start"]]) == {
        "state": "running", "step": "DIGI-HLT", "step_state": "start"}
    assert jobs._job_state([["1", "START"], ["5", "DONE"]]) == {"state": "done"}
    assert jobs.generator_numbers([{"steps": {}}]) is None
    lhe = jobs._summary("GEN: After matching: total cross section = 1.0e+02 +- 1.0e+00 pb\n"
                        "GEN: Filter efficiency (event-level)= (1.2e+03) / (2.4e+03) = 5.000e-01 +- 0\n"
                        "GEN: After filter: final cross section = 5.0e+01 +- 1.0e+00 pb\n")
    numbers = jobs.generator_numbers([lhe])
    assert numbers["cross_section_before_filter_pb"] == 100.0 and numbers["cross_section_pb"] == 50.0
    assert numbers["generated"] == 2400 and numbers["filter_efficiency"] == 0.5


# HTCondor's job log as the LPC's schedds write it (hosts shortened)
JOB_LOG = """000 (5001.000.000) 2026-11-15 23:17:16 Job submitted from host: <131.225.204.62:9618?alias=lpcschedd5.fnal.gov>
...
000 (5001.001.000) 2026-11-15 23:17:16 Job submitted from host: <131.225.204.62:9618?alias=lpcschedd5.fnal.gov>
...
000 (5001.002.000) 2026-11-15 23:17:16 Job submitted from host: <131.225.204.62:9618?alias=lpcschedd5.fnal.gov>
...
040 (5001.000.000) 2026-11-15 23:17:49 Started transferring input files
	Transferring to host: <131.225.204.219:9618?alias=cmswn2272.fnal.gov>
...
001 (5001.000.000) 2026-11-15 23:17:55 Job executing on host: <131.225.204.219:9618?alias=cmswn2272.fnal.gov>
...
001 (5001.001.000) 2026-11-15 23:18:02 Job executing on host: <131.225.204.220:9618?alias=cmswn2273.fnal.gov>
...
006 (5001.000.000) 2026-11-15 23:19:04 Image size of job updated: 29
	1  -  MemoryUsage of job (MB)
...
005 (5001.001.000) 2026-11-15 23:24:10 Job terminated.
	(1) Normal termination (return value 65)
		Usr 0 00:05:50, Sys 0 00:00:15  -  Run Remote Usage
...
005 (5001.000.000) 2026-11-15 23:26:55 Job terminated.
	(1) Normal termination (return value 0)
		Usr 0 00:05:50, Sys 0 00:00:15  -  Run Remote Usage
...
012 (5001.002.000) 2026-11-15 23:30:00 Job was held.
	Error from slot1_1@cmswn2272.fnal.gov: memory usage exceeded request_memory
	Code 34 Subcode 0
...
000 (5002.000.000) 2026-11-15 23:40:00 Job submitted from host: <131.225.204.62:9618?alias=lpcschedd5.fnal.gov>
...
001 (5002.000.000) 2026-11-15 23:41:00 Job executing on host: <131.225.204.221:9618?alias=cmswn2274.fnal.gov>
...
"""


def test_condor_jobs_are_read_from_the_job_log():
    with tempfile.TemporaryDirectory() as tmp:
        d = _create(tmp, pileup="none", events=300, events_per_job=100,
                    output="root://cmseos.fnal.gov//store/user/someone/jpsi")
        root = Path(tmp) / "jpsi"
        condor = root / "condor"
        (condor / "logs").mkdir(parents=True, exist_ok=True)
        (condor / "out").mkdir(parents=True, exist_ok=True)
        (condor / "logs" / "condor.log").write_text(JOB_LOG)
        # all three jobs submitted in cluster 5001, then job 1 again in cluster 5002
        (condor / "clusters").write_text("5001 0 1 2\n5002 1\n")
        (condor / "cluster").write_text("5002\n")
        (condor / "out" / "summary_0.txt").write_text(GENXSEC + "DONE\n")
        cluster, states, problem = jobs._condor(root)
        assert cluster == 5002 and problem is None
        assert states == {0: {"state": "completed", "exit_code": 0},
                          1: {"state": "running"},
                          2: {"state": "held", "hold_reason": "Error from slot1_1@cmswn2272.fnal.gov: "
                                                              "memory usage exceeded request_memory"}}
        original = jobs.list_outputs
        jobs.list_outputs = lambda output, tier: ([f"{output}/{tier}/jpsi_{tier}_0000.root"], None)
        try:
            report = jobs.status(root)
        finally:
            jobs.list_outputs = original
        assert report["condor_cluster"] == 5002
        assert report["job_counts"] == {"done": 1, "running": 1, "held": 1}
        assert report["jobs"][0]["finished"] and report["generator"]["passed_filter"] == 25
        assert report["for_framework"]["sample_locations"][0]["xsec"] == 5.0e5
        # with no record of the submissions, the last one: the cluster and the submit file
        (condor / "clusters").unlink()
        (condor / "cluster").write_text("5001\n")
        (condor / "job.sub").write_text(production.condor_submit_file(root, d, "/tmp/x509up_u1", [0, 1, 2]))
        assert jobs._condor(root)[1][1] == {"state": "completed", "exit_code": 65}
        (condor / "logs" / "condor.log").unlink()
        assert jobs._condor(root) == (5001, {0: {"state": "submitted"}, 1: {"state": "submitted"},
                                             2: {"state": "submitted"}},
                                      "HTCondor's job log, condor/logs/condor.log, is missing")


def test_the_job_summary_keeps_the_generator_numbers_once():
    """run_job.sh's summary of a step: TrigReport's and GenXSecAnalyzer's lines, once each."""
    start = production.RUN_JOB.index('    grep -h -E "^TrigReport')
    pipeline = production.RUN_JOB[start:production.RUN_JOB.index('\n', production.RUN_JOB.index('>> "$SUMMARY"', start))]
    block = ("------------------------------------\nGenXsecAnalyzer:\n------------------------------------\n"
             "Before Filter: total cross section = 3.095e+06 +- 6.412e+04 pb\n"
             "Filter efficiency (taking into account weights)= (98) / (700) = 1.400e-01 +- 1.311e-02\n"
             "Filter efficiency (event-level)= (98) / (700) = 1.400e-01 +- 1.311e-02    [TO BE USED IN MCM]\n"
             "\nAfter filter: final cross section = 4.333e+05 +- 4.157e+04 pb\n"
             "After filter: final fraction of events with negative weights = 0.000e+00 +- 0.000e+00\n")
    log = (" 1/0 extra term in cross section, density matrix on/off\n"
           "TrigReport Events total = 700 passed = 98 failed = 602\n" + block + block)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "GEN-SIM.log").write_text(log)
        env = dict(os.environ, WORK=tmp, name="GEN-SIM", SUMMARY=str(Path(tmp) / "summary.txt"))
        subprocess.run(["bash", "-c", pipeline], env=env, check=True)
        lines = (Path(tmp) / "summary.txt").read_text().splitlines()
    assert lines == [
        "GEN-SIM: TrigReport Events total = 700 passed = 98 failed = 602",
        "GEN-SIM: Before Filter: total cross section = 3.095e+06 +- 6.412e+04 pb",
        "GEN-SIM: Filter efficiency (taking into account weights)= (98) / (700) = 1.400e-01 +- 1.311e-02",
        "GEN-SIM: Filter efficiency (event-level)= (98) / (700) = 1.400e-01 +- 1.311e-02    [TO BE USED IN MCM]",
        "GEN-SIM: After filter: final cross section = 4.333e+05 +- 4.157e+04 pb"], lines
    numbers = jobs.generator_numbers([jobs._summary("\n".join(lines))])
    assert numbers["generated"] == 700 and numbers["passed_filter"] == 98
    assert numbers["cross_section_pb"] == 4.333e5


# --------------------------------------------------------------------------- #
# the tools
# --------------------------------------------------------------------------- #

def _tools():
    try:
        from tools.mcgen.campaigns import ListMCCampaignsTool
        from tools.mcgen.prepare import PrepareMCProductionTool
        from tools.mcgen.run import RunMCProductionTool
        from tools.mcgen.status import MCProductionStatusTool
    except ImportError as exc:
        _skip(f"orchestral is not installed ({exc})")
    return ListMCCampaignsTool, PrepareMCProductionTool, RunMCProductionTool, MCProductionStatusTool


def _call(tool, **kwargs):
    """A tool's answer as a dict, or the error text; a refusal of an argument by the
    tool's own field validation counts as an error text too."""
    try:
        text = tool(**kwargs)._run()
    except Exception as exc:          # validation before the tool runs
        return f"Error: {type(exc).__name__}: {exc}"
    try:
        return json.loads(text)
    except ValueError:
        return text


def test_tools_answer_and_refuse():
    listing, prepare, run, status = _tools()
    answer = _call(listing)
    assert [p["run_period"] for p in answer["run_periods"]] == list(campaigns.RUN_PERIODS)
    assert "Unknown Run Period" in _call(listing, run_period="2017")
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.realpath(tmp)
        good = dict(base_directory=base, name="jpsi", run_period="2023", events=20,
                    pythia8_settings=SETTINGS, filters=FILTERS, pileup="none")
        made = _call(prepare, **good)
        assert made["status"] == "ok" and made["production"] == "jpsi", made
        assert [s["step"] for s in made["steps"]] == ["GEN-SIM", "DIGI-HLT", "RECO", "MINIAOD", "NANOAOD"]
        assert made["jobs"] == {"n": 1, "events_per_job": 20, "last_job_events": 20}
        assert made["needs"] == [] and "cfg/step1_GEN-SIM_cfg.py" in made["steps"][0]["cmsdriver"]
        assert "exists already" in _call(prepare, **good)
        for change, expected in (({"run_period": "2017"}, "unknown run period"),
                                 ({"fragment": LHE_FRAGMENT}, "given 2 ways"),
                                 ({"pythia8_settings": None, "filters": None}, "no process"),
                                 ({"pythia8_settings": None, "fragment": LHE_FRAGMENT}, "filters go with"),
                                 ({"events": 0}, "positive whole"),
                                 ({"name": "../up"}, "Invalid Name"),
                                 ({"output": "/elsewhere"}, "inside the working directory"),
                                 ({"pythia8_settings": None, "filters": None,
                                   "fragment_file": "/etc/passwd"}, "Access Denied")):
            kwargs = {**good, "name": "x", **change}
            got = _call(prepare, **kwargs)
            assert isinstance(got, str) and expected in got, (change, got)
        assert not (Path(base) / "x").exists()
        lhe = _call(prepare, **{**good, "name": "lhe", "pythia8_settings": None, "filters": None,
                                "fragment": LHE_FRAGMENT, "final_tier": "GEN"})
        assert lhe["lhe"] and lhe["steps"][0]["step"] == "GEN" and "gridpack" in " ".join(lhe["notes"])
        premix = _call(prepare, **{**good, "name": "pu", "pileup": "premix",
                                   "output": "root://cmseos.fnal.gov//store/user/someone/x/"})
        assert any("grid proxy" in need for need in premix["needs"])
        # running: refusals before anything starts, and a start without CMSSW (launch stubbed)
        assert "Production Not Found" in _call(run, base_directory=base, production="nope")
        assert "where must be" in _call(run, base_directory=base, production="jpsi", where="cloud")
        assert "job numbers from 0 to 0" in _call(run, base_directory=base, production="jpsi", jobs=[3])
        assert "test_events is for local" in _call(run, base_directory=base, production="jpsi",
                                                    where="condor", test_events=5)
        from tools.mcgen import run as run_module
        launched = []
        original_launch, original_proxy = run_module.jobs_lib.launch, run_module.jobs_lib.proxy
        run_module.jobs_lib.launch = lambda directory, mode, selected, test: launched.append(
            (directory.name, mode, selected, test)) or 4242
        run_module.jobs_lib.proxy = lambda hours: {"valid": False, "why": "no proxy here"}
        try:
            started = _call(run, base_directory=base, production="jpsi", test_events=5)
            assert started["driver_pid"] == 4242 and launched == [("jpsi", "local", [0], 5)]
            assert "No Usable Grid Proxy" in _call(run, base_directory=base, production="pu")
            run_module.jobs_lib.proxy = lambda hours: {"valid": True, "path": "/tmp/x509up_u1", "hours_left": 100}
            assert "only write to a root:// url" in _call(run, base_directory=base, production="jpsi",
                                                         where="condor")
            submitted = _call(run, base_directory=base, production="pu", where="condor")
            assert submitted["where"] == "condor" and launched[-1] == ("pu", "condor", [0], None)
            assert "x509userproxy           = /tmp/x509up_u1" in (Path(base) / "pu" / "condor" / "job.sub").read_text()
        finally:
            run_module.jobs_lib.launch, run_module.jobs_lib.proxy = original_launch, original_proxy
        report = _call(status, base_directory=base, production="jpsi")
        assert report["status"] == "ok" and report["stage"] == "prepared" and report["jobs"][0]["events"] == 20
        assert "Production Not Found" in _call(status, base_directory=base, production="../..")


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
    print(f"mcgen tests: {passed} passed, {skipped} skipped, {failed} failed")
    if skipped and no_skips:
        print("--no-skips: a skipped test counts as a failure")
    sys.exit(1 if failed or (skipped and no_skips) else 0)
