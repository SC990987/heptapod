"""
# _git.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

The little git that building a framework inside the analyst's repository needs:
clone it, recognise a clone, commit what was written. Nothing here pushes.

git runs without a terminal, in a session of its own: a remote that wants a password
fails at once instead of waiting for an answer nobody can give (no terminal prompt, no
askpass window, ssh in batch mode), and a git that runs out of time is stopped
together with everything it started (ssh, the transfer helpers).
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

CLONE_TIMEOUT = 120
LOCAL_TIMEOUT = 60

_SCP_LIKE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^\s]+$")
_URL = re.compile(r"^(https?|ssh|git|file)://\S+$")


class GitError(RuntimeError):
    """A git command failed or ran out of time; the message carries what git said."""


def available() -> bool:
    return shutil.which("git") is not None


def is_url(text: str) -> bool:
    """A remote to clone (https://, ssh://, git://, file:// or user@host:path)."""
    return isinstance(text, str) and bool(_URL.match(text) or _SCP_LIKE.match(text))


def url_problem(text: str) -> Optional[str]:
    if not isinstance(text, str) or not text:
        return "an empty or non-text value"
    if text.startswith("-"):
        return "it starts with '-', which git would read as an option"
    if any(ch.isspace() for ch in text) or not text.isprintable():
        return "it contains spaces or control characters"
    if not is_url(text):
        return ("it is neither a url (https://..., ssh://..., git@host:owner/repo.git) nor "
                "a path inside the working directory")
    return None


def local_path(url: str) -> Optional[str]:
    """The directory a ``file://`` url names, or None for any other url."""
    return url[len("file://"):] if isinstance(url, str) and url.startswith("file://") else None


def repo_name(url: str) -> str:
    """The repository's name from its url: ``.../my-analysis.git`` -> ``my-analysis``."""
    tail = re.split(r"[/:]", url.rstrip("/"))[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def _ssh_command() -> Optional[str]:
    """The ssh command git would use, in batch mode; None when GIT_SSH names a program."""
    configured = os.environ.get("GIT_SSH_COMMAND")
    if not configured:
        if os.environ.get("GIT_SSH"):
            return None
        try:
            got = subprocess.run(["git", "config", "--get", "core.sshCommand"],
                                 capture_output=True, text=True, timeout=10,
                                 stdin=subprocess.DEVNULL, check=False)
            configured = got.stdout.strip() if got.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            configured = ""
    return (configured or "ssh") + " -o BatchMode=yes"


def _env(network: bool) -> Dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"          # no password prompt on a terminal ...
    env["GIT_ASKPASS"] = "true"               # ... nor in a window: the answer is empty
    env["SSH_ASKPASS_REQUIRE"] = "never"
    if network:
        command = _ssh_command()
        if command:
            env["GIT_SSH_COMMAND"] = command
    return env


def _git(args: List[str], cwd: Optional[Path] = None, timeout: Optional[int] = None,
         network: bool = False) -> subprocess.CompletedProcess:
    timeout = timeout or LOCAL_TIMEOUT
    try:
        process = subprocess.Popen(["git", *args], cwd=None if cwd is None else str(cwd),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, text=True, env=_env(network),
                                   start_new_session=True)
    except OSError as exc:
        raise GitError(f"git could not be started: {exc}") from None
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)     # git and what it started
        except (AttributeError, OSError):
            process.kill()
        process.communicate()
        raise GitError(f"git {args[0]} took longer than {timeout} s and was stopped") from None
    return subprocess.CompletedProcess(process.args, process.returncode, out, err)


def _said(result: subprocess.CompletedProcess) -> str:
    text = " ".join((result.stderr or result.stdout or "").split())
    return text[:400] or f"exit code {result.returncode}"


def clone(url: str, target: Path, timeout: Optional[int] = None) -> None:
    """``git clone`` into ``target``, which must not exist yet (or be empty)."""
    result = _git(["clone", "--quiet", "--", url, str(target)],
                  timeout=timeout or CLONE_TIMEOUT, network=True)
    if result.returncode != 0:
        raise GitError(_said(result))


def work_tree_root(path: Path) -> Optional[Path]:
    """The top of the git work tree ``path`` is in, or None if it is in none."""
    try:
        result = _git(["rev-parse", "--show-toplevel"], cwd=path)
    except GitError:
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def half_made(path: Path) -> bool:
    """A clone that knows the remote's branches but has none checked out: what an
    interrupted ``git clone`` leaves behind. (A clone of an empty repository has
    neither.)"""
    head = _git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=path)
    if head.returncode == 0:
        return False
    refs = _git(["for-each-ref", "--count=1", "refs/remotes"], cwd=path)
    return bool(refs.stdout.strip())


def commit(path: Path, paths: List[str],
           message: str) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Stage ``paths`` (relative to ``path``) and commit exactly those.

    Returns ``(commit hash, None, ignored)``, or ``(None, why not, ignored)`` when nothing
    could be committed: git's identity is not configured, a hook refused, and so on.
    ``ignored`` are the paths the repository's ignore rules exclude: they are left out,
    not forced in. What else was staged in the repository stays staged and out of the
    commit; what this staged stays staged when the commit fails.
    """
    # one per line, as given (the framework's own names need no quoting)
    found = _git(["check-ignore", "--", *paths], cwd=path)
    ignored = sorted(set(found.stdout.splitlines()) & set(paths)) if found.returncode == 0 else []
    wanted = [name for name in paths if name not in ignored]
    if not wanted:
        return None, "every file is excluded by the repository's .gitignore", ignored
    # a git stopped for taking too long leaves its lock on the index behind: take it away,
    # so that the analyst can commit by hand
    lock = Path(path) / _git(["rev-parse", "--git-path", "index.lock"], cwd=path).stdout.strip()
    locked_before = lock.exists()
    try:
        added = _git(["add", "--", *wanted], cwd=path)
        if added.returncode != 0:
            return None, f"git add failed: {_said(added)}", ignored
        staged = _git(["diff", "--cached", "--quiet", "--", *wanted], cwd=path)
        if staged.returncode == 0:
            return None, "nothing to commit: the files are already committed as they are", ignored
        done = _git(["commit", "--quiet", "-m", message, "--", *wanted], cwd=path)
    except GitError:
        if not locked_before and lock.is_file():
            try:
                lock.unlink()
            except OSError:
                pass
        raise
    if done.returncode != 0:
        return None, f"git commit failed: {_said(done)}", ignored
    head = _git(["rev-parse", "--short", "HEAD"], cwd=path)
    return (head.stdout.strip() or None), None, ignored


def describe(path: Path) -> Dict[str, Optional[str]]:
    """The branch checked out and the url of the remote 'origin'."""
    branch = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=path).stdout.strip() or None
    remote = _git(["remote", "get-url", "origin"], cwd=path)
    return {"branch": branch,
            "remote": remote.stdout.strip() if remote.returncode == 0 else None}
