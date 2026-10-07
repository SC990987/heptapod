"""
# launch.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.
"""

"""Launch a coding agent on the analysis-framework tools.

Usage, from the repository root:

    python examples/framework/launch.py --harness claude-code
    python examples/framework/launch.py --harness codex
    python examples/framework/launch.py --harness opencode

Give the agent data to look at by pointing ANALYSIS_DATA at a NanoAOD-like ROOT
file, or at a directory of them:

    ANALYSIS_DATA=/path/to/nanoaod_dir python examples/framework/launch.py --harness claude-code

The tools confine every file access to the sandbox this creates, so data living
outside it is linked in as `data/`, and the agent refers to it by that relative
path.

To check generated frameworks with an environment of your own rather than the
toolkit's (an existing analysis environment, say), point ANALYSIS_PYTHON at its
interpreter:

    ANALYSIS_PYTHON=/path/to/venv/bin/python python examples/framework/launch.py --harness claude-code
"""

# Setup repository path for imports
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'shared'))

from harness_launch import main

# Seconds one tool call may take. toolbase's default of 60 s is short for a tool
# that imports coffea and runs an analysis over a few thousand events.
CALL_TIMEOUT = 600


def stage_inputs(sandbox: Path) -> None:
    """Link ANALYSIS_DATA into the sandbox as data/.

    The tools check that a file they read stays inside the sandbox without
    resolving links, so `data/x.root` passes the check and still reads the real
    file. (Where they write is different: a project directory that is a link
    leading out of the sandbox is refused.)
    """
    source = os.environ.get('ANALYSIS_DATA')
    if not source:
        print("  no data linked: set ANALYSIS_DATA to a ROOT file or a directory of "
              "them, or copy a file into the sandbox")
        return
    source = Path(source).expanduser().resolve()
    if not source.exists():
        print(f"  NOTE: ANALYSIS_DATA={source} does not exist here; no data/ link made")
        return
    link = sandbox / 'data'
    if source.is_dir():
        if link.exists() or link.is_symlink():
            print(f"  NOTE: {link} already exists; ANALYSIS_DATA was not linked")
            return
        link.symlink_to(source, target_is_directory=True)
        n_files = len(list(source.glob('*.root')))
        print(f"  linked data/ -> {source}  ({n_files} ROOT files directly inside)")
    else:
        link.mkdir(exist_ok=True)
        target = link / source.name
        if target.exists() or target.is_symlink():
            print(f"  NOTE: {target} already exists; ANALYSIS_DATA was not linked")
            return
        target.symlink_to(source)
        print(f"  linked data/{source.name} -> {source}")


def analysis_python() -> dict:
    """The heptapod config for ANALYSIS_PYTHON, if it names an interpreter."""
    given = os.environ.get('ANALYSIS_PYTHON')
    if not given:
        return {}
    # abspath, not resolve: the interpreter of a virtual environment is a link to
    # the one it was made from, and following it would leave the environment
    python = os.path.abspath(os.path.expanduser(given))
    if not os.path.isfile(python):
        print(f"  NOTE: ANALYSIS_PYTHON={given} is not a file; the toolkit's python is used")
        return {}
    return {'analysis_python': python}


CONFIG = analysis_python()

main(
    example='framework',
    bundles=['framework', 'coffea'],
    prompt_path=Path(__file__).resolve().parent / 'prompts' / 'system_prompt.md',
    sandbox_dir=Path(__file__).resolve().parent,
    mode='explorer',
    post_setup=stage_inputs,
    config_values=CONFIG,
    # importing coffea from a shared filesystem can exceed Codex's default
    mcp_startup_timeout=180,
    call_timeout=CALL_TIMEOUT,
    warm_imports=('coffea', 'awkward', 'uproot', 'hist'),
    # the interpreter the frameworks are checked with, when it is not the toolkit's
    warm_pythons=list(CONFIG.values()),
)
