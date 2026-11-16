"""Tests of scripts/condor_dashboard.py, with HTCondor's answers made up.

    python -m unittest discover -s scripts/tests -v

They need no HTCondor: a fake stands in for condor_q, condor_history and condor_status.
"""
import gzip
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("condor_dashboard", HERE.parent / "condor_dashboard.py")
dashboard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dashboard)

NOW = time.time()


def ad(cluster, proc, status, schedd="lpcschedd5.fnal.gov", **extra):
    """A job ad as condor_q -json gives it."""
    job = {"ClusterId": cluster, "ProcId": proc, "JobStatus": status,
           "GlobalJobId": f"{schedd}#{cluster}.{proc}#{int(NOW) - 4000}",
           "Cmd": "/home/u/run_job.sh", "Iwd": "/home/u/work", "QDate": int(NOW) - 4000,
           "EnteredCurrentStatus": int(NOW) - 600}
    job.update(extra)
    return job


class FakeCondor:
    """condor_q, condor_history and condor_status, answering from what a test sets."""

    def __init__(self):
        self.queue = []
        self.history = {}           # schedd -> ads
        self.schedds = ["lpcschedd4.fnal.gov", "lpcschedd5.fnal.gov"]
        self.failing = set()        # "condor_q", or "condor_history <schedd>"
        self.calls = []

    def __call__(self, command, timeout=120):
        self.calls.append(command)
        if command[0] == "condor_status":
            return "\n".join(self.schedds) + "\n", None
        if command[0] == "condor_q":
            if "condor_q" in self.failing:
                return "", "condor_q: Failed to connect to lpcschedd5.fnal.gov"
            by_schedd = {}
            for job in self.queue:
                by_schedd.setdefault(job["GlobalJobId"].split("#")[0], []).append(job)
            return "".join(json.dumps(jobs, indent=1) + "\n" for jobs in by_schedd.values()), None
        if command[0] == "condor_history":
            schedd = command[command.index("-name") + 1] if "-name" in command else ""
            if f"condor_history {schedd}" in self.failing:
                return "", f"condor_history ({schedd}): timed out"
            return json.dumps(self.history.get(schedd, []), indent=1), None
        raise AssertionError(f"unexpected command {command}")


def monitor(fake, **options):
    return dashboard.Monitor("someone", runner=fake, host="node.example.org", **options)


def jobs_of(snapshot):
    return {job["id"]: job for group in snapshot["groups"] for job in group["jobs"]}


class ReadingHTCondor(unittest.TestCase):

    def test_json_ads_reads_one_array_per_schedd_and_skips_the_rest(self):
        text = ('[\n{"ClusterId": 1}\n]\n'
                '-- Schedd: lpcschedd6.fnal.gov : <131.225.189.168:9618?... @ 11/16/26 08:55:16\n'
                '[{"ClusterId": 2}, {"ClusterId": 3}]\n'
                '[\n{"ClusterId": 4},\n{"ClusterId": 5')          # cut short
        self.assertEqual([a["ClusterId"] for a in dashboard.json_ads(text)], [1, 2, 3, 4])
        self.assertEqual(dashboard.json_ads(""), [])

    def test_states_clocks_and_notes(self):
        start = NOW - 3723
        cases = {
            "running": (ad(1, 0, 2, JobCurrentStartDate=start), "running for 1h 02m 03s", ""),
            "idle": (ad(1, 1, 1, EnteredCurrentStatus=NOW - 600), "waiting for 10m 00s", ""),
            "again": (ad(1, 2, 1, EnteredCurrentStatus=NOW - 60, NumJobStarts=1), "waiting again for 1m 00s", ""),
            "held": (ad(1, 3, 5, EnteredCurrentStatus=NOW - 42, HoldReason="Memory usage exceeded"),
                     "held for 42s", "Memory usage exceeded"),
            "done": (ad(1, 4, 4, JobCurrentStartDate=NOW - 1000, CompletionDate=NOW - 100, ExitCode=0),
                     "ran 15m 00s, finished 1m 40s ago", ""),
            "failed": (ad(1, 5, 4, JobCurrentStartDate=NOW - 1000, CompletionDate=NOW - 100, ExitCode=2),
                       "ran 15m 00s, finished 1m 40s ago", "exit code 2"),
            "signal": (ad(1, 6, 4, JobCurrentStartDate=NOW - 10, CompletionDate=NOW - 5,
                          ExitBySignal=True, ExitSignal=9), "ran 5s, finished 5s ago", "killed by signal 9"),
            "removed": (ad(1, 7, 3, EnteredCurrentStatus=NOW - 7200), "removed 2h 00m 00s ago", ""),
        }
        for name, (job_ad, clock, note) in cases.items():
            job = dashboard.job_record(job_ad, dashboard.state_of(job_ad))
            self.assertEqual(dashboard.job_clock(job, NOW), clock, name)
            self.assertEqual(dashboard.job_note(job), note, name)
        self.assertEqual(dashboard.state_of(cases["failed"][0]), "failed")
        self.assertEqual(dashboard.state_of(cases["signal"][0]), "failed")
        self.assertEqual(dashboard.state_of(cases["done"][0]), "completed")

    def test_a_job_record_keeps_numbers_and_drops_expressions(self):
        job = dashboard.job_record(ad(7, 0, 2, RemoteHost="slot1_3@cmswn2345.fnal.gov",
                                      ResidentSetSize=2048000, RequestMemory="ifThenElse(...)",
                                      Args="0"), "running")
        self.assertEqual((job["host"], job["machine"]), ("cmswn2345", "cmswn2345.fnal.gov"))
        self.assertEqual(job["memory"], 2000)
        self.assertNotIn("request_memory", job)
        self.assertEqual(job["args"], "0")

    def test_a_wrapper_script_without_a_shebang_runs_under_sh(self):
        # the LPC's condor_q is such a script: exec fails with ENOEXEC, a shell runs it
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "condor_q"
            script.write_text('echo "[{\\"ClusterId\\": 1, \\"Args\\": \\"$1\\"}]"\n')
            script.chmod(0o755)
            output, error = dashboard.run([str(script), "first"])
        self.assertIsNone(error)
        self.assertEqual(dashboard.json_ads(output), [{"ClusterId": 1, "Args": "first"}])
        self.assertIn("not installed", dashboard.run(["/no/such/condor_q"])[1])

    def test_durations(self):
        self.assertEqual([dashboard.duration(s) for s in (0, 59, 61, 3723, 90000)],
                         ["0s", "59s", "1m 01s", "1h 02m 03s", "1d 01h"])

    def test_jobs_are_grouped_by_production_batch_or_cluster(self):
        with tempfile.TemporaryDirectory() as tmp:
            production = Path(tmp) / "jpsi_mumu_2023"
            (production / "condor" / "out").mkdir(parents=True)
            (production / "production.json").write_text("{}")
            known = {}
            self.assertEqual(dashboard.group_of(ad(5, 0, 2, Iwd=str(production / "condor" / "out")), "s", known)[:3],
                             ("production:" + str(production), "jpsi_mumu_2023", "production"))
            self.assertEqual(dashboard.group_of(ad(5, 0, 2, Iwd=tmp + "/condor/out"), "s", known)[2], "cluster")
        self.assertEqual(dashboard.group_of(ad(6, 0, 2, JobBatchName="skims"), "s", {})[:3],
                         ("batch:skims", "skims", "batch"))
        self.assertEqual(dashboard.group_of(ad(7, 0, 2), "s", {})[1], "run_job.sh (cluster 7)")


class FollowingTheJobs(unittest.TestCase):

    def test_a_job_that_leaves_the_queue_waits_for_its_history_record(self):
        fake = FakeCondor()
        fake.queue = [ad(10, 0, 2, JobCurrentStartDate=NOW - 300), ad(10, 1, 1)]
        watch = monitor(fake)
        watch.refresh()
        self.assertEqual({i: j["state"] for i, j in jobs_of(watch.snapshot).items()},
                         {"10.0": "running", "10.1": "idle"})
        fake.queue = [ad(10, 1, 2, JobCurrentStartDate=NOW - 5)]
        before = len(fake.calls)
        watch.refresh()
        self.assertEqual(jobs_of(watch.snapshot)["10.0"]["state"], "leaving")
        asked = [call[0] for call in fake.calls[before:]]
        self.assertIn("condor_history", asked, "a job left the queue: the history is read at once")
        fake.history["lpcschedd5.fnal.gov"] = [ad(10, 0, 4, JobCurrentStartDate=NOW - 300,
                                                  CompletionDate=NOW - 2, ExitCode=0,
                                                  EnteredCurrentStatus=NOW - 2)]
        watch.refresh()
        self.assertEqual(jobs_of(watch.snapshot)["10.0"]["state"], "completed")
        self.assertEqual(watch.leaving, {})
        self.assertEqual(jobs_of(watch.snapshot)["10.1"]["state"], "running")

    def test_a_new_version_only_when_the_jobs_change(self):
        fake = FakeCondor()
        fake.queue = [ad(50, 0, 2, JobCurrentStartDate=NOW - 10)]
        watch = monitor(fake)
        watch.refresh()
        first = watch.version
        watch.refresh()
        self.assertEqual(watch.version, first, "the same answer: the page keeps what it has")
        fake.queue = [ad(50, 0, 2, JobCurrentStartDate=NOW - 10, ResidentSetSize=4096)]
        watch.refresh()
        self.assertEqual(watch.version, first + 1)

    def test_a_schedd_that_does_not_answer_keeps_its_jobs(self):
        fake = FakeCondor()
        fake.queue = [ad(11, 0, 2, JobCurrentStartDate=NOW - 300)]
        watch = monitor(fake)
        watch.refresh()
        updated = watch.updated
        fake.failing.add("condor_q")
        watch.refresh()
        snapshot = watch.snapshot
        self.assertEqual(jobs_of(snapshot)["11.0"]["state"], "running")
        self.assertEqual(snapshot["updated"], updated)
        self.assertTrue(any("Failed to connect" in error for error in snapshot["errors"]))

    def test_the_history_comes_from_every_lpc_schedd_within_the_window(self):
        fake = FakeCondor()
        fake.history = {
            "lpcschedd4.fnal.gov": [ad(20, 0, 4, schedd="lpcschedd4.fnal.gov", EnteredCurrentStatus=NOW - 3600,
                                       CompletionDate=NOW - 3600, JobCurrentStartDate=NOW - 4000, ExitCode=0)],
            "lpcschedd5.fnal.gov": [ad(21, 0, 4, EnteredCurrentStatus=NOW - 3 * 86400, ExitCode=0)],
        }
        watch = monitor(fake, hours=24)
        watch.refresh()
        self.assertEqual(set(jobs_of(watch.snapshot)), {"20.0"})       # 21.0 ended 3 days ago
        histories = [call for call in fake.calls if call[0] == "condor_history"]
        self.assertEqual(sorted(call[call.index("-name") + 1] for call in histories),
                         ["lpcschedd4.fnal.gov", "lpcschedd5.fnal.gov"])
        for call in histories:
            self.assertIn('Owner == "someone"', call[call.index("-constraint") + 1])
            self.assertIn("-since", call)
        self.assertEqual(watch.snapshot["history_from"], ["lpcschedd4.fnal.gov", "lpcschedd5.fnal.gov"])

    def test_without_lpc_schedds_the_default_schedd_is_read(self):
        fake = FakeCondor()
        fake.schedds = []
        watch = monitor(fake)
        watch.refresh()
        histories = [call for call in fake.calls if call[0] == "condor_history"]
        self.assertEqual(len(histories), 1)
        self.assertNotIn("-name", histories[0])

    def test_named_schedds_are_asked_one_by_one(self):
        fake = FakeCondor()
        watch = monitor(fake, schedds=["a.example.org", "b.example.org"])
        watch.refresh()
        queues = [call for call in fake.calls if call[0] == "condor_q"]
        self.assertEqual([call[call.index("-name") + 1] for call in queues], ["a.example.org", "b.example.org"])
        self.assertFalse(any(call[0] == "condor_status" for call in fake.calls))

    def test_the_text_report(self):
        fake = FakeCondor()
        fake.queue = [ad(30, 0, 2, JobCurrentStartDate=NOW - 65, RemoteHost="slot1@cmswn1.fnal.gov"),
                      ad(30, 1, 5, HoldReason="Job exceeded its memory")]
        watch = monitor(fake)
        watch.refresh()
        text = dashboard.text_report(watch.snapshot)
        self.assertIn("run_job.sh (cluster 30)", text)
        self.assertIn("1 held, 1 running", text)
        self.assertIn("running for 1m 0", text)
        self.assertIn("Job exceeded its memory", text)
        self.assertIn("on cmswn1", text)


class ThePage(unittest.TestCase):

    def setUp(self):
        fake = FakeCondor()
        fake.queue = [ad(40, proc, 2, JobCurrentStartDate=NOW - 100, Args=f"argument {proc}")
                      for proc in range(60)]
        self.watch = monitor(fake)
        self.watch.refresh()
        self.server = dashboard.make_server(self.watch, "the-key", port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path, headers=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        return urllib.request.urlopen(request, timeout=10)

    def test_the_page_and_the_state_need_the_key(self):
        for path in ("/", "/state", "/?token=wrong", "/state?token=wrong"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, 403, path)
        with self.get("/?token=the-key") as reply:
            page = reply.read().decode()
            self.assertIn("<title>Condor jobs</title>", page)
            self.assertIn("default-src 'none'", reply.headers["Content-Security-Policy"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/elsewhere?token=the-key")
        self.assertEqual(caught.exception.code, 404)

    def test_the_state_is_sent_once_per_version_and_compressed(self):
        with self.get("/state?token=the-key", {"Accept-Encoding": "gzip"}) as reply:
            self.assertEqual(reply.headers["Content-Encoding"], "gzip")
            self.assertTrue(float(reply.headers["X-Server-Time"]) > 0)
            state = json.loads(gzip.decompress(reply.read()))
        self.assertEqual(len(state["groups"][0]["jobs"]), 60)
        self.assertEqual(state["user"], "someone")
        with self.get(f"/state?token=the-key&version={state['version']}") as reply:
            unchanged = json.loads(reply.read())
        self.assertIs(unchanged["unchanged"], True)
        self.assertEqual(unchanged["updated"], state["updated"])
        self.assertNotIn("groups", unchanged)


class TheCommandLine(unittest.TestCase):

    def test_a_user_name_cannot_carry_an_expression(self):
        with self.assertRaises(SystemExit):
            dashboard.main(["--once", "--user", 'x" || true || "'])

    def test_the_interval_spares_the_schedds(self):
        with self.assertRaises(SystemExit):
            dashboard.main(["--once", "--interval", "2"])


if __name__ == "__main__":
    unittest.main()
