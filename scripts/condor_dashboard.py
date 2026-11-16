#!/usr/bin/env python3
"""A live dashboard of your HTCondor jobs, served from the machine you submit from.

    python scripts/condor_dashboard.py           # then open the link it prints
    python scripts/condor_dashboard.py --once    # or print your jobs here and exit

Every --interval seconds (30 by default) it asks HTCondor for your jobs: condor_q
for those in the queue, condor_history for those that left it in the last --hours
hours (24 by default). The page groups the jobs of each submission (by the CMS
simulation production that submitted them, by batch name, or by cluster) and shows
each job's state and how long it has been in it: running for, waiting for, held for,
or how long it ran and when it finished. The clocks tick between updates, and the
page fetches the new numbers by itself.

Opening the page. The link is on this machine's localhost:
  - in VS Code connected to this machine, open the link: VS Code forwards the port;
  - from anywhere else, forward the port with the ssh command the dashboard prints,
    then open the link in your browser.
The link carries a key, so other people logged in to the same machine cannot read
the page. The dashboard only reads: it never holds, releases or removes a job, and
it needs no grid proxy. Ctrl-C stops it.

Where it looks. The queue comes from condor_q as you would run it (at the LPC, every
LPC schedd); the history from every LPC schedd at the LPC, else from your default
schedd, and from any schedd a job of yours was seen on. --schedd NAME, repeated,
names the schedds instead.

Needs Python 3.9 or newer and the HTCondor command-line tools; nothing else.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import errno
import getpass
import gzip
import hmac
import json
import os
import re
import secrets
import shutil
import socket
import statistics
import subprocess
import threading
import time
import urllib.parse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATES = {1: "idle", 2: "running", 3: "removed", 4: "completed", 5: "held",
          6: "transferring", 7: "suspended"}
LEAVING = "leaving"         # gone from the queue, its history record not read yet
ACTIVE = ("held", "running", "transferring", "suspended", "idle", LEAVING)
ORDER = ACTIVE + ("removed", "failed", "completed")
KINDS = {"production": "simulation production", "batch": "batch", "cluster": "cluster"}
ATTRIBUTES = ("ClusterId", "ProcId", "GlobalJobId", "JobStatus", "JobBatchName", "Cmd",
              "Args", "Arguments", "Iwd", "QDate", "JobCurrentStartDate",
              "EnteredCurrentStatus", "CompletionDate", "RemoteWallClockTime",
              "NumJobStarts", "RemoteHost", "LastRemoteHost", "HoldReason", "ExitCode",
              "ExitBySignal", "ExitSignal", "ResidentSetSize", "RequestMemory")
LPC_SCHEDDS = 'FERMIHTC_SCHEDD_TYPE =?= "CMSLPC"'
DEFAULT_PORT = 8765
HISTORY_EVERY = 300         # seconds between history reads while no job leaves the queue
HISTORY_LIMIT = 20000       # records read from one schedd's history, at most
LEAVING_FOR = 3600          # seconds a job gone from the queue waits for its record
COMMAND_TIMEOUT = 120
USER = re.compile(r"[A-Za-z0-9._-]+")


# ── Asking HTCondor ─────────────────────────────────────────────────────────

def run(command: list, timeout: int = COMMAND_TIMEOUT) -> tuple:
    """Run an HTCondor command: (what it printed, None), or (what it printed, the problem).

    A command that is a script without a #! line, as the LPC's condor_q and
    condor_history wrappers are, runs under /bin/sh, the way a shell runs it."""
    def attempt(argv):
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)

    try:
        try:
            done = attempt(command)
        except OSError as exc:
            if exc.errno != errno.ENOEXEC:
                raise
            done = attempt(["/bin/sh", shutil.which(command[0]) or command[0], *command[1:]])
    except FileNotFoundError:
        return "", f"{command[0]} is not installed here: run the dashboard where you submit jobs"
    except subprocess.TimeoutExpired:
        return "", f"{command[0]} got no answer within {timeout} s"
    except OSError as exc:
        return "", f"{command[0]} could not be run: {exc.strerror or exc}"
    if done.returncode == 0:
        return done.stdout, None
    lines = (done.stderr.strip() or done.stdout.strip()).splitlines()
    why = lines[-1] if lines else f"exit code {done.returncode}"
    where = f" ({command[command.index('-name') + 1]})" if "-name" in command else ""
    return done.stdout, f"{command[0]}{where}: {why}"


def json_ads(text: str) -> list:
    """The job ads in HTCondor's -json output, which holds one array per schedd asked."""
    decoder, ads, position = json.JSONDecoder(), [], 0
    while True:
        starts = [p for p in (text.find("[", position), text.find("{", position)) if p >= 0]
        if not starts:
            return ads
        start = min(starts)
        try:
            value, position = decoder.raw_decode(text, start)
        except ValueError:          # a line that is not JSON, or an answer cut short
            position = start + 1
            continue
        for ad in value if isinstance(value, list) else [value]:
            if isinstance(ad, dict):
                ads.append(ad)


def number(value):
    """A number, or None: an attribute that is an expression arrives as a string."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def job_key(ad: dict) -> tuple:
    """(schedd, cluster, proc) of a job ad; the schedd's name starts its GlobalJobId."""
    schedd = str(ad.get("GlobalJobId") or "").split("#", 1)[0]
    return schedd, int(number(ad.get("ClusterId")) or 0), int(number(ad.get("ProcId")) or 0)


# ── What the page shows of a job ────────────────────────────────────────────

def production_of(directory: str, known: dict):
    """The CMS simulation production a job came from, or None. HEPTAPOD's mcgen tools
    submit from <production>/condor/out, beside <production>/production.json."""
    if directory not in known:
        found = None
        head, tail = os.path.split(directory.rstrip("/"))
        if tail == "out" and os.path.basename(head) == "condor":
            production = os.path.dirname(head)
            if os.path.isfile(os.path.join(production, "production.json")):
                found = production
        known[directory] = found
    return known[directory]


def group_of(ad: dict, schedd: str, known: dict) -> tuple:
    """(key, label, kind, path) of the submission a job belongs to."""
    directory = str(ad.get("Iwd") or "")
    production = production_of(directory, known)
    if production:
        return "production:" + production, os.path.basename(production), "production", production
    batch = ad.get("JobBatchName")
    if isinstance(batch, str) and batch:
        return "batch:" + batch, batch, "batch", directory
    cluster = ad.get("ClusterId")
    command = os.path.basename(str(ad.get("Cmd") or "")) or "job"
    return f"cluster:{schedd}#{cluster}", f"{command} (cluster {cluster})", "cluster", directory


def state_of(ad: dict) -> str:
    """The job's state; a completed job that exited with an error or a signal failed."""
    status = number(ad.get("JobStatus"))
    if status is None:
        return "unknown"
    state = STATES.get(int(status), f"status {int(status)}")
    if state == "completed" and (ad.get("ExitBySignal") is True
                                 or (number(ad.get("ExitCode")) or 0) != 0):
        return "failed"
    return state


def job_record(ad: dict, state: str) -> dict:
    """What the page and the text report need of one job."""
    machine = str(ad.get("RemoteHost") or ad.get("LastRemoteHost") or "").rsplit("@", 1)[-1]
    memory = number(ad.get("ResidentSetSize"))          # KiB
    record = {
        "id": f"{ad.get('ClusterId')}.{ad.get('ProcId')}",
        "state": state,
        "since": number(ad.get("EnteredCurrentStatus")),
        "queued": number(ad.get("QDate")),
        "started": number(ad.get("JobCurrentStartDate")),
        "finished": number(ad.get("CompletionDate")) or None,
        "wall": number(ad.get("RemoteWallClockTime")),
        "starts": number(ad.get("NumJobStarts")),
        "host": machine.split(".", 1)[0],
        "machine": machine,
        "memory": round(memory / 1024) if memory else None,   # MB
        "request_memory": number(ad.get("RequestMemory")),    # MB
        "args": str(ad.get("Args") or ad.get("Arguments") or "")[:120],
        "hold": str(ad.get("HoldReason") or "")[:500],
        "exit": number(ad.get("ExitCode")),
        "signal": number(ad.get("ExitSignal")) if ad.get("ExitBySignal") is True else None,
    }
    return {key: value for key, value in record.items() if value is not None and value != ""}


def duration(seconds) -> str:
    """1d 04h, 2h 05m 13s, 5m 02s or 42s."""
    if seconds is None:
        return ""
    total = max(0, int(round(seconds)))
    days, hours = total // 86400, total % 86400 // 3600
    minutes, secs = total % 3600 // 60, total % 60
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def job_clock(job: dict, now: float) -> str:
    """How long the job has been doing what it does, or how it went (the page's 'How long')."""
    state, since = job["state"], job.get("since")
    started, finished = job.get("started"), job.get("finished")
    if state in ("running", "transferring"):
        return f"{state} for {duration(now - started)}" if started else state
    if state == "idle":
        waited = since or job.get("queued")
        again = "again " if job.get("starts") else ""
        return f"waiting {again}for {duration(now - waited)}" if waited else "waiting"
    if state in ("held", "suspended"):
        return f"{state} for {duration(now - since)}" if since else state
    if state == LEAVING:
        return f"left the queue {duration(now - since)} ago" if since else "left the queue"
    if state == "removed":
        return f"removed {duration(now - since)} ago" if since else state
    if state in ("completed", "failed"):
        end = finished or since
        ran = (f"ran {duration(finished - started)}, "
               if finished and started and finished >= started else "")
        return f"{ran}finished {duration(now - end)} ago" if end else state
    return state


def job_note(job: dict) -> str:
    """Why a job is held, or how it failed."""
    if job.get("hold"):
        return job["hold"]
    if job["state"] == "failed":
        if "signal" in job:
            return f"killed by signal {job['signal']}"
        return f"exit code {job.get('exit')}"
    return ""


# ── Keeping the picture ─────────────────────────────────────────────────────

class Monitor:
    """Asks HTCondor about one user's jobs and keeps the latest picture of them."""

    def __init__(self, user, schedds=(), interval=30, hours=24.0, history=True,
                 runner=None, host=None):
        self.user, self.schedds = user, list(schedds)
        self.interval, self.hours, self.history = interval, hours, history
        self.runner = runner or run
        self.host = host or socket.getfqdn()
        self.queue = {}             # (schedd, cluster, proc) -> job ad, as condor_q showed it last
        self.finished = {}          # the same, from condor_history
        self.leaving = {}           # gone from the queue, not in the history yet: key -> (ad, when)
        self.history_schedds = None
        self.history_from = []
        self.last_history = None
        self.updated = None         # when condor_q last answered without a problem
        self.errors = []
        self.productions = {}
        self.lock = threading.Lock()
        self.version = 0
        self.content = None         # the last picture without its times: a new version if it changes
        self.snapshot = self.picture(time.time())
        self.body = None

    def ask(self, command: list) -> tuple:
        return self.runner(command + ["-json", "-attributes", ",".join(ATTRIBUTES)])

    def read_queue(self) -> tuple:
        commands = ([["condor_q", "-name", schedd, self.user] for schedd in self.schedds]
                    or [["condor_q", self.user]])
        ads, errors = {}, []
        for command in commands:
            output, error = self.ask(command)
            if error:
                errors.append(error)
            for ad in json_ads(output or ""):
                ads[job_key(ad)] = ad
        return ads, errors

    def history_targets(self) -> list:
        """The schedds whose history is read: the ones named, else the LPC schedds, and
        any schedd a job was seen on; "" (the default schedd) when there is none."""
        if self.history_schedds is None:
            if self.schedds:
                self.history_schedds = set(self.schedds)
            else:
                output, _ = self.runner(["condor_status", "-schedd", "-constraint",
                                         LPC_SCHEDDS, "-af", "Name"], timeout=30)
                self.history_schedds = {line.strip() for line in (output or "").splitlines()
                                        if line.strip()}
        seen = {key[0] for key in list(self.queue) + list(self.leaving) if key[0]}
        return sorted(self.history_schedds | seen) or [""]

    def read_history(self, now: float) -> tuple:
        since = int(now - self.hours * 3600)
        constraint = f'Owner == "{self.user}" && EnteredCurrentStatus >= {since}'

        def one(schedd):
            command = ["condor_history"] + (["-name", schedd] if schedd else []) + [
                "-constraint", constraint,
                "-since", f"EnteredCurrentStatus < {since}",    # stop at the window's start
                "-limit", str(HISTORY_LIMIT)]
            return self.ask(command)

        targets = self.history_targets()
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
            answers = list(pool.map(one, targets))
        ads, errors = {}, []
        for output, error in answers:
            if error:
                errors.append(error)
            for ad in json_ads(output or ""):
                ads[job_key(ad)] = ad
        return ads, errors, targets

    def refresh(self) -> None:
        """Ask HTCondor once and publish what it said."""
        now = time.time()
        queue, errors = self.read_queue()
        if errors:      # a schedd that did not answer still has its jobs: keep them
            queue = {**self.queue, **queue}
        else:
            self.updated = now
        for key in self.queue.keys() - queue.keys():
            self.leaving[key] = (self.queue[key], now)
        self.queue = queue
        for key in self.leaving.keys() & queue.keys():
            del self.leaving[key]
        if self.history and (self.last_history is None or self.leaving
                             or now - self.last_history >= HISTORY_EVERY):
            finished, history_errors, self.history_from = self.read_history(now)
            errors += history_errors
            self.finished = {**self.finished, **finished} if history_errors else finished
            self.last_history = now
        for key, (_, when) in list(self.leaving.items()):
            if key in self.finished or now - when > LEAVING_FOR:
                del self.leaving[key]
        self.errors = errors
        self.publish(now)

    def publish(self, now: float) -> None:
        """Make this the picture the page gets: a new version only if the jobs changed."""
        snapshot = self.picture(now)
        content = json.dumps({key: value for key, value in snapshot.items()
                              if key not in ("now", "updated", "version")},
                             sort_keys=True, separators=(",", ":"))
        with self.lock:
            if content != self.content:
                self.version += 1
                self.content = content
            snapshot["version"] = self.version
            self.snapshot, self.body = snapshot, None

    def payload(self, known: str = "") -> bytes:
        """The snapshot as JSON; to a page that has this version already, only its times."""
        with self.lock:
            if known == str(self.version):
                return json.dumps({"unchanged": True, "now": self.snapshot["now"],
                                   "updated": self.snapshot["updated"]}).encode()
            if self.body is None:
                self.body = json.dumps(self.snapshot, separators=(",", ":")).encode()
            return self.body

    def picture(self, now: float) -> dict:
        cutoff = now - self.hours * 3600
        ads = {key: ad for key, ad in self.finished.items()
               if (number(ad.get("EnteredCurrentStatus")) or now) >= cutoff}
        states = {key: state_of(ad) for key, ad in ads.items()}
        for key, (ad, when) in self.leaving.items():
            if key not in ads:
                ads[key], states[key] = dict(ad, EnteredCurrentStatus=when), LEAVING
        for key, ad in self.queue.items():
            ads[key], states[key] = ad, state_of(ad)
        groups = {}
        for key in sorted(ads, key=lambda k: (k[1], k[2])):
            name, label, kind, path = group_of(ads[key], key[0], self.productions)
            group = groups.setdefault(name, {
                "key": name, "label": label, "kind": kind, "path": path,
                "command": str(ads[key].get("Cmd") or ""), "schedds": [], "clusters": [],
                "jobs": []})
            if key[0] and key[0] not in group["schedds"]:
                group["schedds"].append(key[0])
            if key[1] not in group["clusters"]:
                group["clusters"].append(key[1])
            group["jobs"].append(job_record(ads[key], states[key]))
        rank = {state: index for index, state in enumerate(ORDER)}
        for group in groups.values():
            group["jobs"].sort(key=lambda job: rank.get(job["state"], len(ORDER)))

        def busy_then_recent(group):
            active = any(job["state"] in ACTIVE for job in group["jobs"])
            latest = max((job.get("since") or 0) for job in group["jobs"])
            return (not active, -latest)

        return {
            "user": self.user, "host": self.host, "now": now, "updated": self.updated,
            "interval": self.interval, "hours": self.hours, "history": self.history,
            "queue_from": ", ".join(self.schedds) or "condor_q",
            "history_from": [schedd or "the default schedd" for schedd in self.history_from],
            "errors": self.errors, "version": self.version,
            "groups": sorted(groups.values(), key=busy_then_recent),
        }

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.refresh()
            except Exception as exc:    # keep serving what there is, and say what failed
                self.errors = [f"reading the jobs failed: {exc!r}"]
                self.publish(time.time())
            stop.wait(self.interval)


# ── Text, for --once ────────────────────────────────────────────────────────

def text_report(snapshot: dict, rows: int = 40) -> str:
    now = snapshot["now"]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    scope = (f"the queue and the last {snapshot['hours']:g} h" if snapshot["history"]
             else "the queue")
    lines = [f"HTCondor jobs of {snapshot['user']} on {snapshot['host']} at {when} ({scope})"]
    lines += [f"  problem: {problem}" for problem in snapshot["errors"]]
    if not snapshot["groups"]:
        lines.append("  none")
    rank = {state: index for index, state in enumerate(ORDER)}
    for group in snapshot["groups"]:
        jobs = group["jobs"]
        counts = Counter(job["state"] for job in jobs)
        tally = ", ".join(f"{n} {state}" for state, n in
                          sorted(counts.items(), key=lambda item: rank.get(item[0], len(ORDER))))
        clusters = ", ".join(str(c) for c in group["clusters"][:3])
        clusters += ", ..." if len(group["clusters"]) > 3 else ""
        where = f"{', '.join(group['schedds'])} " if group["schedds"] else ""
        lines += ["", f"{group['label']}  ({KINDS[group['kind']]}, {where}cluster {clusters})",
                  f"  {tally}"]
        done = [job for job in jobs if job["state"] == "completed"]
        listed = [job for job in jobs if job["state"] != "completed"] + (done if len(done) <= 5 else [])
        for job in listed[:rows]:
            extras = [f"args {job['args']}" if job.get("args") else "",
                      f"{job['starts']} starts" if (job.get("starts") or 0) > 1 else "",
                      f"on {job['host']}" if job.get("host") else "", job_note(job)]
            lines.append(f"  {job['id']:<14} {job_clock(job, now):<44} "
                         + "; ".join(extra for extra in extras if extra))
        if len(listed) > rows:
            lines.append(f"  ... and {len(listed) - rows} more")
        if len(done) > 5:
            runs = [job["finished"] - job["started"] for job in done
                    if job.get("finished") and job.get("started")]
            last = max((job.get("finished") or job.get("since") or now) for job in done)
            median = f", median run {duration(statistics.median(runs))}" if runs else ""
            lines.append(f"  {len(done)} completed{median}, the last {duration(now - last)} ago")
    return "\n".join(line.rstrip() for line in lines)


# ── The page ────────────────────────────────────────────────────────────────

CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
       "frame-ancestors 'none'")


def make_server(monitor: Monitor, token: str, port=None) -> ThreadingHTTPServer:
    """The page's server on this machine's localhost: on port, or the first free port
    from DEFAULT_PORT on."""
    page, key = PAGE.encode(), token.encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = "condor-dashboard"
        sys_version = ""

        def log_message(self, format, *args):     # the terminal is for the link
            pass

        def reply(self, code, body, kind, headers=()):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for name, value in headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            if not hmac.compare_digest(query.get("token", [""])[0].encode(), key):
                self.reply(403, b"This dashboard opens with the link it printed when it started.\n",
                           "text/plain; charset=utf-8")
            elif url.path == "/":
                self.reply(200, page, "text/html; charset=utf-8",
                           [("Content-Security-Policy", CSP)])
            elif url.path == "/state":
                body = monitor.payload(query.get("version", [""])[0])
                headers = [("X-Server-Time", f"{time.time():.3f}")]
                if len(body) > 2048 and "gzip" in self.headers.get("Accept-Encoding", ""):
                    body = gzip.compress(body, 5)
                    headers.append(("Content-Encoding", "gzip"))
                self.reply(200, body, "application/json", headers)
            else:
                self.reply(404, b"Not found\n", "text/plain; charset=utf-8")

    for candidate in ([port] if port is not None else range(DEFAULT_PORT, DEFAULT_PORT + 20)):
        try:
            return ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE or port is not None:
                raise
    raise OSError(errno.EADDRINUSE, f"ports {DEFAULT_PORT} to {DEFAULT_PORT + 19} are all taken; "
                                    "choose one with --port")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="condor_dashboard.py",
        description="A live dashboard of your HTCondor jobs in your browser: each job's state "
                    "and how long it has been in it.",
        epilog="The page is on this machine's localhost. In VS Code connected to this machine, "
               "open the link it prints; elsewhere, forward the port with the ssh command it "
               "prints. It only reads, and needs no grid proxy.")
    parser.add_argument("--port", type=int,
                        help=f"port on this machine's localhost (default: {DEFAULT_PORT}, "
                             "or the next free one)")
    parser.add_argument("--interval", type=int, default=30,
                        help="seconds between questions to HTCondor, at least 10 (default: 30)")
    parser.add_argument("--hours", type=float, default=24.0,
                        help="how far back finished jobs are shown, in hours (default: 24)")
    parser.add_argument("--schedd", action="append", default=[], metavar="NAME",
                        help="a schedd to read; repeat for several (default: condor_q's own, "
                             "and for the history the LPC schedds or your default one)")
    parser.add_argument("--user", default=getpass.getuser(),
                        help="whose jobs to show (default: yours)")
    parser.add_argument("--no-history", action="store_true",
                        help="show only the jobs in the queue")
    parser.add_argument("--once", action="store_true",
                        help="print the jobs here and exit, without the page")
    args = parser.parse_args(argv)
    if not USER.fullmatch(args.user):
        parser.error(f"not a user name: {args.user!r}")
    if args.interval < 10:
        parser.error("--interval is at least 10 seconds: the schedds are shared")
    if args.hours <= 0:
        parser.error("--hours must be more than 0")

    monitor = Monitor(args.user, args.schedd, args.interval, args.hours,
                      history=not args.no_history)
    if args.once:
        monitor.refresh()
        print(text_report(monitor.snapshot))
        return 1 if monitor.errors and not monitor.snapshot["groups"] else 0

    token = secrets.token_urlsafe(18)
    try:
        server = make_server(monitor, token, args.port)
    except OSError as exc:
        parser.exit(1, f"condor_dashboard.py: cannot listen on this machine's localhost: "
                       f"{exc.strerror or exc}\n")
    port = server.server_address[1]
    stop = threading.Event()
    threading.Thread(target=monitor.run_forever, args=(stop,), name="condor", daemon=True).start()
    print(f"Dashboard of {args.user}'s HTCondor jobs, asking HTCondor every {args.interval} s:\n\n"
          f"    http://localhost:{port}/?token={token}\n\n"
          "In VS Code connected to this machine, open the link: VS Code forwards the port.\n"
          "Anywhere else, forward the port first, then open the link in your browser:\n"
          f"    ssh -L {port}:localhost:{port} {getpass.getuser()}@{monitor.host}\n"
          "Ctrl-C stops it.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop.set()
        server.server_close()
    return 0


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Condor jobs</title>
<style>
:root {
  color-scheme: light;
  --bg: #f3f5f8; --panel: #ffffff; --ink: #172030; --muted: #5c6778; --faint: #858f9e;
  --line: #e0e5eb; --soft: #eef1f5; --accent: #2563c9;
  --running: #2563c9; --transferring: #0e7490; --idle: #8a94a3; --held: #c2410c;
  --suspended: #a16207; --leaving: #7c3aed; --completed: #15803d; --failed: #be123c;
  --removed: #6b7280;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --bg: #0e131a; --panel: #151c25; --ink: #e3e8ef; --muted: #9aa5b4; --faint: #738092;
    --line: #26303d; --soft: #1c2530; --accent: #60a5fa;
    --running: #60a5fa; --transferring: #22d3ee; --idle: #8792a3; --held: #fb923c;
    --suspended: #facc15; --leaving: #a78bfa; --completed: #4ade80; --failed: #fb7185;
    --removed: #9ca3af;
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body { background: var(--bg); color: var(--ink);
       font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
.top { position: sticky; top: 0; z-index: 2; display: flex; flex-wrap: wrap; gap: 6px 20px;
       align-items: center; justify-content: space-between; padding: 12px 16px;
       background: var(--bg); border-bottom: 1px solid var(--line); }
.brand { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; min-width: 0; }
h1 { font-size: 18px; margin: 0; letter-spacing: -0.01em; }
.who { color: var(--muted); font-size: 13.5px; overflow-wrap: anywhere; }
.status { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13.5px;
          font-variant-numeric: tabular-nums; }
.dot { width: 9px; height: 9px; border-radius: 50%; background: var(--faint); flex: none; }
.dot.ok { background: var(--completed); }
.dot.warn { background: var(--held); }
.dot.bad { background: var(--failed); }
main { max-width: 1240px; margin: 0 auto; padding: 18px 16px 48px; }
.problems { border: 1px solid var(--held); background: var(--panel); border-left-width: 4px;
            border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; font-size: 14px; }
.problems p { margin: 2px 0; overflow-wrap: anywhere; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-top: 3px solid var(--c);
        border-radius: 12px; padding: 12px 16px; min-width: 0; }
.tile .k { color: var(--muted); font-size: 12.5px; font-weight: 600; letter-spacing: .05em;
           text-transform: uppercase; }
.tile .n { font-size: 30px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; }
.tile .d { color: var(--muted); font-size: 13px; min-height: 1.4em; font-variant-numeric: tabular-nums; }
.controls { display: flex; flex-wrap: wrap; gap: 10px 18px; align-items: center; margin: 18px 0 4px; }
.controls input[type=search] { flex: 1 1 240px; max-width: 420px; min-width: 0; padding: 8px 12px;
  border-radius: 9px; border: 1px solid var(--line); background: var(--panel); color: var(--ink); font: inherit; }
.check { display: inline-flex; gap: 8px; align-items: center; color: var(--muted); font-size: 14px; cursor: pointer; }
.group { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; margin-top: 12px; overflow: hidden; }
.ghead { padding: 14px 16px; display: grid; gap: 9px; }
.gtitle { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }
.gtitle h2 { font-size: 16.5px; margin: 0; overflow-wrap: anywhere; }
.badge { font-size: 12px; font-weight: 600; color: var(--muted); background: var(--soft);
         border-radius: 99px; padding: 1px 9px; }
.meta { color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
.meta code { font: 12.5px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.bar { display: flex; height: 8px; border-radius: 99px; overflow: hidden; background: var(--soft); }
.bar span { height: 100%; background: var(--c); }
.chips { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 13.5px; color: var(--muted);
         font-variant-numeric: tabular-nums; }
.chips b { color: var(--ink); font-weight: 650; }
.chip::before { content: ""; display: inline-block; width: 8px; height: 8px; border-radius: 50%;
                background: var(--c); margin-right: 6px; vertical-align: 1px; }
.times { color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }
.toggle { justify-self: start; font: inherit; font-size: 13.5px; color: var(--accent);
          background: none; border: 0; padding: 2px 0; cursor: pointer; }
.toggle:hover { text-decoration: underline; }
.tablewrap { overflow-x: auto; border-top: 1px solid var(--line); }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; font-variant-numeric: tabular-nums; }
th { text-align: left; font-weight: 600; color: var(--muted); font-size: 12px; text-transform: uppercase;
     letter-spacing: .04em; padding: 8px 12px; background: var(--soft); white-space: nowrap; }
td { padding: 7px 12px; border-top: 1px solid var(--line); white-space: nowrap; vertical-align: top; }
td.note { white-space: normal; color: var(--muted); min-width: 200px; max-width: 520px; overflow-wrap: anywhere; }
td .args { color: var(--faint); margin-left: 8px; }
.pill { display: inline-block; font-size: 12px; font-weight: 650; padding: 0 9px; border-radius: 99px;
        color: var(--c); border: 1px solid var(--c); }
.more { padding: 10px 16px; border-top: 1px solid var(--line); }
.empty { text-align: center; color: var(--muted); padding: 44px 16px; background: var(--panel);
         border: 1px dashed var(--line); border-radius: 12px; margin-top: 12px; }
.foot { color: var(--faint); font-size: 12.5px; margin-top: 22px; overflow-wrap: anywhere; }
.s-running { --c: var(--running); } .s-transferring { --c: var(--transferring); }
.s-idle { --c: var(--idle); } .s-held { --c: var(--held); } .s-suspended { --c: var(--suspended); }
.s-leaving { --c: var(--leaving); } .s-completed { --c: var(--completed); }
.s-failed { --c: var(--failed); } .s-removed { --c: var(--removed); }
@media (max-width: 640px) { .tile .n { font-size: 26px; } .wide { display: none; } }
</style>
</head>
<body>
<header class="top">
  <div class="brand"><h1>Condor jobs</h1><span id="who" class="who"></span></div>
  <div class="status" role="status"><span id="dot" class="dot"></span><span id="updated">Asking HTCondor…</span></div>
</header>
<main>
  <div id="problems" class="problems" hidden></div>
  <section id="tiles" class="tiles" aria-label="Totals"></section>
  <div class="controls">
    <input id="filter" type="search" placeholder="Filter by name, job id or host" aria-label="Filter the jobs">
    <label class="check"><input id="active" type="checkbox"> Only jobs still in the queue</label>
  </div>
  <section id="groups" aria-label="Submissions"></section>
  <p id="foot" class="foot"></p>
</main>
<script>
"use strict";
(() => {
  const token = new URLSearchParams(location.search).get("token") || "";
  const ACTIVE = ["held", "running", "transferring", "suspended", "idle", "leaving"];
  const SHOWN = ["running", "transferring", "idle", "held", "suspended", "leaving", "completed", "failed", "removed"];
  const BAR = ["completed", "failed", "removed", "running", "transferring", "suspended", "leaving", "held", "idle"];
  const WORDS = {running: "running", transferring: "transferring", idle: "idle", held: "held",
                 suspended: "suspended", leaving: "leaving the queue", completed: "done",
                 failed: "failed", removed: "removed"};
  const KINDS = {production: "simulation production", batch: "batch", cluster: "cluster"};
  const ROWS = 200, POLL = 5000;
  const opened = new Set(), unfolded = new Set();
  let snap = null, version = null, offset = 0, lost = null, ticks = [], chosen = false;

  const $ = (id) => document.getElementById(id);
  const now = () => Date.now() / 1000 + offset;

  function el(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "width") node.style.width = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, String(value));
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false || kid === "") continue;
      node.append(kid instanceof Node ? kid : String(kid));
    }
    return node;
  }
  function ticking(node, show) { ticks.push([node, show]); node.textContent = show(now()); return node; }
  const earliest = (values) => values.reduce((a, b) => (b != null && (a == null || b < a) ? b : a), null);
  const latest = (values) => values.reduce((a, b) => (b != null && (a == null || b > a) ? b : a), null);
  const hours = (h) => (h === 1 ? "hour" : (Number.isInteger(h) ? h : h.toFixed(1)) + " hours");

  function duration(seconds) {
    if (seconds == null || !isFinite(seconds)) return "";
    const total = Math.max(0, Math.round(seconds)), two = (n) => String(n).padStart(2, "0");
    const d = Math.floor(total / 86400), h = Math.floor(total % 86400 / 3600);
    const m = Math.floor(total % 3600 / 60), s = total % 60;
    if (d) return d + "d " + two(h) + "h";
    if (h) return h + "h " + two(m) + "m " + two(s) + "s";
    if (m) return m + "m " + two(s) + "s";
    return s + "s";
  }
  function clock(job, t) {
    const {state, since, started, finished} = job;
    if (state === "running" || state === "transferring") return started ? state + " for " + duration(t - started) : state;
    if (state === "idle") {
      const waited = since || job.queued;
      return waited ? "waiting " + (job.starts ? "again " : "") + "for " + duration(t - waited) : "waiting";
    }
    if (state === "held" || state === "suspended") return since ? state + " for " + duration(t - since) : state;
    if (state === "leaving") return since ? "left the queue " + duration(t - since) + " ago" : "left the queue";
    if (state === "removed") return since ? "removed " + duration(t - since) + " ago" : state;
    if (state === "completed" || state === "failed") {
      const end = finished || since;
      const ran = finished && started && finished >= started ? "ran " + duration(finished - started) + ", " : "";
      return end ? ran + "finished " + duration(t - end) + " ago" : state;
    }
    return state;
  }
  function note(job) {
    if (job.hold) return job.hold;
    if (job.state === "failed") return job.signal != null ? "killed by signal " + job.signal : "exit code " + job.exit;
    return "";
  }
  function memory(job) {
    if (job.memory == null) return "";
    const gb = (mb) => (mb / 1024).toFixed(mb < 10240 ? 1 : 0);
    return job.request_memory ? gb(job.memory) + " of " + gb(job.request_memory) + " GB" : gb(job.memory) + " GB";
  }

  function tile(label, count, state, detail) {
    return el("div", {class: "tile s-" + state},
      el("div", {class: "k", text: label}),
      el("div", {class: "n", text: String(count)}),
      detail ? ticking(el("div", {class: "d"}), detail) : el("div", {class: "d"}));
  }

  function card(group, jobs) {
    const all = group.jobs, counts = {};
    for (const job of all) counts[job.state] = (counts[job.state] || 0) + 1;
    const running = all.filter((j) => j.state === "running" || j.state === "transferring");
    const firstStart = earliest(running.map((j) => j.started));
    const oldestWait = earliest(all.filter((j) => j.state === "idle").map((j) => j.since || j.queued));
    const submitted = earliest(all.map((j) => j.queued));
    const runs = all.filter((j) => (j.state === "completed" || j.state === "failed") && j.finished && j.started && j.finished >= j.started)
                    .map((j) => j.finished - j.started).sort((a, b) => a - b);
    const median = runs.length ? (runs[Math.floor((runs.length - 1) / 2)] + runs[Math.ceil((runs.length - 1) / 2)]) / 2 : null;
    const lastEnd = latest(all.filter((j) => !ACTIVE.includes(j.state)).map((j) => j.finished || j.since));
    const busy = all.some((j) => ACTIVE.includes(j.state));
    const open = opened.has(group.key);

    const where = [group.schedds.join(", "),
                   (group.clusters.length > 1 ? "clusters " : "cluster ") + group.clusters.slice(0, 4).join(", ") + (group.clusters.length > 4 ? ", …" : "")]
                  .filter(Boolean).join(" · ");
    const head = el("div", {class: "ghead"},
      el("div", {class: "gtitle"},
        el("h2", {text: group.label}),
        el("span", {class: "badge", text: KINDS[group.kind] || group.kind}),
        el("span", {class: "meta", text: all.length === 1 ? "1 job" : all.length + " jobs"})),
      el("div", {class: "meta"}, where, group.path ? [" · ", el("code", {text: group.path, title: group.command || null})] : null),
      el("div", {class: "bar", role: "img", "aria-label": BAR.filter((s) => counts[s]).map((s) => counts[s] + " " + WORDS[s]).join(", ")},
        BAR.filter((s) => counts[s]).map((s) => el("span", {class: "s-" + s, width: (100 * counts[s] / all.length) + "%", title: counts[s] + " " + WORDS[s]}))),
      el("div", {class: "chips"},
        SHOWN.filter((s) => counts[s]).map((s) => el("span", {class: "chip s-" + s}, el("b", {text: String(counts[s])}), " " + WORDS[s]))),
      ticking(el("div", {class: "times"}), (t) => [
        firstStart != null ? "longest running " + duration(t - firstStart) : "",
        oldestWait != null ? "waiting up to " + duration(t - oldestWait) : "",
        median != null ? "median run " + duration(median) : "",
        submitted != null ? "submitted " + duration(t - submitted) + " ago" : "",
        !busy && lastEnd != null ? "last finished " + duration(t - lastEnd) + " ago" : "",
      ].filter(Boolean).join(" · ")),
      el("button", {class: "toggle", type: "button", "aria-expanded": open ? "true" : "false",
                    text: open ? "Hide the jobs" : (jobs.length === 1 ? "Show the job" : "Show the " + jobs.length + " jobs"),
                    onclick: () => { chosen = true; if (open) opened.delete(group.key); else opened.add(group.key); render(); }}));
    const box = el("article", {class: "group"}, head);
    if (open) {
      const limit = unfolded.has(group.key) ? jobs.length : Math.min(ROWS, jobs.length);
      const columns = [["Job"], ["State"], ["How long"], ["Starts", "wide"], ["Host"], ["Memory", "wide"], ["Notes"]];
      const rows = jobs.slice(0, limit).map((job) => el("tr", null,
        el("td", null, job.id, job.args ? el("span", {class: "args", text: job.args}) : null),
        el("td", null, el("span", {class: "pill s-" + job.state, text: WORDS[job.state] || job.state})),
        ticking(el("td"), (t) => clock(job, t)),
        el("td", {class: "wide", text: job.starts > 1 ? String(job.starts) : "", title: job.starts > 1 && job.wall ? "worked " + duration(job.wall) + " in all" : null}),
        el("td", {text: job.host || "", title: job.machine || null}),
        el("td", {class: "wide", text: memory(job)}),
        el("td", {class: "note", text: note(job)})));
      box.append(el("div", {class: "tablewrap"}, el("table", null,
        el("thead", null, el("tr", null, columns.map(([name, cls]) => el("th", {class: cls || null, text: name})))),
        el("tbody", null, rows))));
      if (limit < jobs.length) {
        box.append(el("div", {class: "more"}, el("button", {class: "toggle", type: "button",
          text: "Show all " + jobs.length + " jobs", onclick: () => { unfolded.add(group.key); render(); }})));
      }
    }
    return box;
  }

  function render() {
    if (!snap) { status(); return; }
    ticks = [];
    $("who").textContent = snap.user + " on " + snap.host;
    const problems = (lost ? [lost] : []).concat(snap.errors || []);
    $("problems").hidden = !problems.length;
    $("problems").replaceChildren(...problems.map((text) => el("p", {text})));

    const all = snap.groups.flatMap((g) => g.jobs);
    const count = (state) => all.filter((j) => j.state === state).length;
    const running = all.filter((j) => j.state === "running" || j.state === "transferring");
    const firstStart = earliest(running.map((j) => j.started));
    const oldestWait = earliest(all.filter((j) => j.state === "idle").map((j) => j.since || j.queued));
    const window = snap.history ? (() => "in the last " + hours(snap.hours)) : (() => "queue only");
    document.title = (running.length ? running.length + " running · " : "") + "Condor jobs";
    $("tiles").replaceChildren(
      tile("Running", running.length, "running", firstStart != null ? (t) => "longest " + duration(t - firstStart) : null),
      tile("Idle", count("idle"), "idle", oldestWait != null ? (t) => "waiting up to " + duration(t - oldestWait) : null),
      tile("Held", count("held"), "held", count("held") ? () => "the reasons are in the notes" : null),
      tile("Done", count("completed"), "completed", window),
      tile("Failed or removed", count("failed") + count("removed"), "failed", window));

    if (!chosen) {
      opened.clear();
      snap.groups.filter((g) => g.jobs.some((j) => ACTIVE.includes(j.state))).slice(0, 3).forEach((g) => opened.add(g.key));
      if (!opened.size && snap.groups.length === 1) opened.add(snap.groups[0].key);
    }
    const query = $("filter").value.trim().toLowerCase(), onlyActive = $("active").checked;
    const cards = [];
    for (const group of snap.groups) {
      let jobs = group.jobs;
      if (onlyActive) jobs = jobs.filter((j) => ACTIVE.includes(j.state));
      if (query && !(group.label + " " + group.kind + " " + group.clusters.join(" ") + " " + (group.path || "")).toLowerCase().includes(query)) {
        jobs = jobs.filter((j) => (j.id + " " + (j.host || "") + " " + (j.args || "") + " " + j.state).toLowerCase().includes(query));
      }
      if (jobs.length) cards.push(card(group, jobs));
    }
    if (!cards.length) {
      const nothing = !snap.groups.length
        ? (snap.updated ? "No jobs in the queue" + (snap.history ? ", and none finished in the last " + hours(snap.hours) : "") + "." : "Waiting for HTCondor's first answer…")
        : (onlyActive && !query ? "None of these jobs is in the queue now." : "No job matches.");
      cards.push(el("div", {class: "empty", text: nothing}));
    }
    $("groups").replaceChildren(...cards);
    const history = snap.history
      ? "the history of the last " + hours(snap.hours) + " from " + (snap.history_from.length ? snap.history_from.join(", ") : "…")
      : "no history";
    $("foot").textContent = "Queue from " + snap.queue_from + "; " + history + ". HTCondor is asked every " + snap.interval +
      " s, and this page checks for news every " + POLL / 1000 + " s. The dashboard only reads: it never changes a job.";
    status();
  }

  function status() {
    const dot = $("dot"), text = $("updated");
    if (lost) { dot.className = "dot bad"; text.textContent = "Lost contact with the dashboard"; return; }
    if (!snap || !snap.updated) {
      dot.className = "dot" + (snap && snap.errors.length ? " warn" : "");
      text.textContent = snap && snap.errors.length ? "HTCondor has not answered" : "Asking HTCondor…";
      return;
    }
    const t = now(), age = t - snap.updated, stale = age > 2 * snap.interval + 20;
    dot.className = "dot " + (stale ? "bad" : snap.errors.length ? "warn" : "ok");
    const next = snap.now + snap.interval - t;
    text.textContent = "Updated " + duration(age) + " ago" + (!stale && next > 0 ? " · next in " + duration(next) : "");
  }

  async function poll() {
    try {
      const reply = await fetch("state?token=" + encodeURIComponent(token) + (version != null ? "&version=" + version : ""), {cache: "no-store"});
      if (!reply.ok) throw new Error(reply.status === 403 ? "this link's key is not the dashboard's: open the link it printed" : "it answered " + reply.status);
      const served = Number(reply.headers.get("X-Server-Time"));
      if (served) offset = served - Date.now() / 1000;
      const data = await reply.json();
      const wasLost = lost;
      lost = null;
      if (!data.unchanged) { snap = data; version = data.version; render(); }
      else {
        if (snap) { snap.now = data.now; snap.updated = data.updated; }
        if (wasLost) render();
      }
    } catch (error) {
      lost = "Cannot reach the dashboard (" + ((error && error.message) || error) + "). Is it still running?";
      render();
    }
    status();
    setTimeout(poll, POLL);
  }

  try { $("active").checked = localStorage.getItem("condor-dashboard:active") === "1"; } catch (e) { /* no storage */ }
  $("active").addEventListener("change", () => {
    try { localStorage.setItem("condor-dashboard:active", $("active").checked ? "1" : "0"); } catch (e) { /* no storage */ }
    render();
  });
  $("filter").addEventListener("input", render);
  setInterval(() => { const t = now(); for (const [node, show] of ticks) node.textContent = show(t); status(); }, 1000);
  poll();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
