"""
# launch.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.
"""

"""Launch a coding agent on the CMS simulation tools, with the framework tools beside them.

Usage, from the repository root:

    python examples/mcgen/launch.py --harness claude-code
    python examples/mcgen/launch.py --harness codex
    python examples/mcgen/launch.py --harness opencode

The agent can then produce simulation for a process and a run period, here or on
HTCondor, and build an analysis framework on the result in the same session.

To check generated frameworks with an environment of your own rather than the
toolkit's, point ANALYSIS_PYTHON at its interpreter:

    ANALYSIS_PYTHON=/path/to/venv/bin/python python examples/mcgen/launch.py --harness claude-code
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'shared'))

from harness_launch import main

# Seconds one tool call may take: the framework's check runs an analysis. The
# simulation tools themselves return at once and leave the long work to a driver.
CALL_TIMEOUT = 600


def analysis_python() -> dict:
    """The heptapod config for ANALYSIS_PYTHON, if it names an interpreter."""
    given = os.environ.get('ANALYSIS_PYTHON')
    if not given:
        return {}
    python = os.path.abspath(os.path.expanduser(given))
    if not os.path.isfile(python):
        print(f"  NOTE: ANALYSIS_PYTHON={given} is not a file; the toolkit's python is used")
        return {}
    return {'analysis_python': python}


CONFIG = analysis_python()

main(
    example='mcgen',
    bundles=['mcgen', 'framework', 'coffea'],
    prompt_path=Path(__file__).resolve().parent / 'prompts' / 'system_prompt.md',
    sandbox_dir=Path(__file__).resolve().parent,
    mode='explorer',
    config_values=CONFIG,
    mcp_startup_timeout=180,
    call_timeout=CALL_TIMEOUT,
    warm_imports=('coffea', 'awkward', 'uproot', 'hist'),
    warm_pythons=list(CONFIG.values()),
)
