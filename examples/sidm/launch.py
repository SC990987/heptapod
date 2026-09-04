"""
# launch.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.
"""

"""Launch a coding agent on the SIDM / Lepton-Jet tools at the LPC.

Usage, from the repository root:

    python examples/sidm/launch.py --harness claude-code
    python examples/sidm/launch.py --harness claude-code --no-launch

The sample directory defaults to the LPC signal sample and can be pointed
anywhere:

    SIDM_DATA_DIR=/eos/uscms/store/group/.../MyNanoAOD \\
        python examples/sidm/launch.py --harness claude-code

The tools sandbox every file access to `base_directory`, which is the sandbox
this creates. Data living outside it -- on EOS, say -- is therefore linked in
as `data/`, and the agent refers to it by that relative path.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'examples' / 'shared'))

from harness_launch import main

# The LPC SIDM signal sample: Bs -> 2 dark photons -> leptons, LLPnanoAOD.
DEFAULT_DATA_DIR = (
    "/eos/uscms/store/group/lpcmetx/SIDM/ULSignalSamples/2018_v10/"
    "BsTo2DpTo2Mu2e/CutDecayFalse_SIDM_BsTo2DpTo2Mu2e_MBs-500_MDp-0p25_"
    "ctau-0p4_v3/LLPnanoAODv2"
)


def stage_inputs(sandbox: Path) -> None:
    """Link the sample and the scale-out scripts into the sandbox.

    `_safe_path` in every tool resolves with `os.path.abspath`, which does not
    follow symlinks -- so a link inside the sandbox passes the containment
    check while still reading the real file. An absolute EOS path handed to a
    tool is refused.
    """
    data_dir = os.environ.get("SIDM_DATA_DIR", DEFAULT_DATA_DIR)
    link = sandbox / "data"
    if Path(data_dir).exists():
        if not link.exists():
            link.symlink_to(data_dir)
        n = len(list(Path(data_dir).glob("*.root")))
        print(f"  linked data/ -> {data_dir}  ({n} ROOT files)")
    else:
        print(f"  NOTE: {data_dir} not readable from here; no data/ link made.")
        print("        Set SIDM_DATA_DIR to a directory of ROOT files.")

    # the scale-out runner and the example analysis, so `scripts/...` resolves
    scripts = sandbox / "scripts"
    if not scripts.exists():
        scripts.symlink_to(REPO_ROOT / "scripts")
    print("  linked scripts/ -> repo scripts (lpc_scaleout.py, example_analysis_sidm.py)")

    # the analysis configs. Tools resolve a relative `config` against
    # base_directory (the sandbox), so without this `config="configs/sidm.yaml"`
    # -- which is what the skills tell the agent to write -- raises
    # FileNotFoundError.
    configs = sandbox / "configs"
    if not configs.exists():
        configs.symlink_to(REPO_ROOT / "configs")
    names = sorted(p.name for p in (REPO_ROOT / "configs").glob("*.y*ml"))
    print(f"  linked configs/ -> repo configs ({', '.join(names)})")

    # where batch work should write; keeps the sandbox itself small
    (sandbox / "work").mkdir(exist_ok=True)


main(
    example='sidm',
    bundles=['coffea', 'leptonjets', 'analysis', 'cmssw'],
    prompt_path=Path(__file__).resolve().parent / 'prompts' / 'system_prompt.md',
    sandbox_dir=Path(__file__).resolve().parent,
    mode='explorer',
    post_setup=stage_inputs,
)
