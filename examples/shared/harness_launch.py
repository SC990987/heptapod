"""
# harness_launch.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.
"""

"""Set up a sandbox and launch a coding agent against HEPTAPOD's tools.

The per-example `launch.py` scripts call `main()` with their bundles, prompt and
sandbox dir. This is the MCP counterpart to the Orchestral `*_demo.py` scripts:
same sandbox, same tools, same system prompt — the agent comes from the harness
instead of being built in Python.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Per-harness differences: the instruction file it reads, the `tb connect`
# target, and the command that starts it.
HARNESSES = {
    'claude-code': {'instructions': 'CLAUDE.md', 'command': 'claude'},
    'codex':       {'instructions': 'AGENTS.md', 'command': 'codex'},
    'opencode':    {'instructions': 'AGENTS.md', 'command': 'opencode'},
}


def _toolbase() -> str:
    """Locate the toolbase CLI, preferring the short `tb` alias."""
    for name in ('tb', 'toolbase'):
        found = shutil.which(name)
        if found:
            return found
    sys.exit("toolbase not found on PATH. Install it with: pip install toolbase")


def _run(argv, cwd):
    """Run a toolbase command, surfacing its output only on failure."""
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"command failed: {' '.join(argv)}\n{r.stdout}{r.stderr}")
    return r


def _resolve_harness_command(command):
    """Find the harness executable, PATH or not.

    Claude Code installed as the VS Code extension never puts `claude` on
    PATH -- the binary lives inside the extension directory, whose name
    carries a version that changes on every update. Fall back to the newest
    one rather than making the user maintain a PATH entry that goes stale.
    """
    found = shutil.which(command)
    if found:
        return found
    # Both Claude Code and Codex ship inside VS Code extensions whose directory
    # names carry a version that changes on update; prefer the newest.
    patterns = {
        'claude': ['~/.vscode-server/extensions/anthropic.claude-code-*'
                   '/resources/native-binary/claude',
                   '~/.claude/local/claude'],
        'codex':  ['~/.vscode-server/extensions/openai.chatgpt-*'
                   '/bin/linux-x86_64/codex',
                   '~/.vscode-server/extensions/openai.chatgpt-*/bin/*/codex'],
    }
    candidates = []
    for pat in patterns.get(command, []):
        candidates += sorted(glob.glob(os.path.expanduser(pat)))
    for cand in reversed(candidates):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _codex_startup_timeout(sandbox: Path, seconds: int = 180) -> None:
    """Give the MCP server long enough to start.

    Codex defaults to a 30 s MCP startup budget. Serving this toolkit means
    importing coffea/awkward/uproot/fastjet, which on a shared filesystem is
    dominated by I/O -- ~15 s warm and considerably more cold -- so the default
    fails with "MCP client for `toolbase` timed out after 30 seconds".
    """
    cfg = sandbox / '.codex' / 'config.toml'
    if not cfg.exists():
        return
    text = cfg.read_text()
    if 'startup_timeout_sec' in text:
        return
    lines, out, done = text.splitlines(), [], False
    for line in lines:
        out.append(line)
        if not done and line.strip() == '[mcp_servers.toolbase]':
            out.append(f'startup_timeout_sec = {seconds}')
            done = True
    if not done:                      # no section found; append one
        out += ['', '[mcp_servers.toolbase]', f'startup_timeout_sec = {seconds}']
    cfg.write_text("\n".join(out) + "\n")
    print(f'  set startup_timeout_sec = {seconds} for codex')


def _warm_import_cache(tb: str) -> None:
    """Touch the toolkit's interpreter so the first agent call is not the one
    paying the cold-import cost."""
    meta = sorted(Path.home().glob('.toolbase/cache/*/*/.install_meta.yaml'))
    for m in meta:
        for line in m.read_text().splitlines():
            if line.startswith('python_path:'):
                py = line.split(':', 1)[1].strip()
                if os.path.exists(py):
                    subprocess.run([py, '-c', 'import coffea, awkward, uproot'],
                                   capture_output=True, timeout=300)
                    print('  warmed the import cache')
                return


def _require_installed(tb: str) -> None:
    """Fail early, and usefully, if the toolkit is not installed.

    Without this the launcher builds a sandbox, writes the prompt, and only
    then dies inside `tb activate` with "'heptapod' is not installed" --
    leaving a half-made sandbox behind and no hint about what to do.
    """
    r = subprocess.run([tb, 'list'], capture_output=True, text=True)
    if 'heptapod' in (r.stdout + r.stderr):
        return
    sys.exit(
        "heptapod is not installed for toolbase.\n\n"
        "Install it from the repository root (the directory holding "
        "toolkit.yaml):\n\n"
        "    cd <heptapod checkout>\n"
        "    source env.sh                     # if you use one\n"
        "    tb install -e . --bundle analysis --bundle leptonjets \\\n"
        "                    --bundle coffea --bundle cmssw --bundle pdg \\\n"
        "                    --bundle units --bundle inspire\n\n"
        "Then check with `tb list`. If `tb list` says nothing is installed but "
        "~/.toolbase/cache/heptapod exists, the slot is a partial install: "
        "remove it with `rm -rf ~/.toolbase/cache/heptapod` and install again."
    )


def main(*, example, bundles, prompt_path, sandbox_dir, mode='explorer',
         config_keys=(), post_setup=None):
    """Create a sandbox, wire a harness to HEPTAPOD's tools, and launch it.

    Args:
        example: short name, used in messages only.
        bundles: bundle names to activate, e.g. ['nda', 'pdg'].
        prompt_path: system prompt copied in as the harness's instruction file.
            Either one path, or a dict keyed by mode when the example ships a
            prompt per mode.
        sandbox_dir: directory the numbered sandbox is created under.
        mode: sandbox mode passed to create_new_sandbox.
        config_keys: heptapod config fields this example needs (e.g.
            'mg5_path'), read from the repo's config.py. Bundles gated on a
            key stay hidden until it is set, so this is what makes eda / mg5 /
            feynrules tools show up.
        post_setup: optional callable(sandbox: Path) run after the bundles and
            config are set but before the harness is wired. Use it to stage
            inputs the example needs inside the sandbox -- tools sandbox all
            file access to base_directory, so data living elsewhere has to be
            linked in rather than referenced by absolute path.
    """
    ap = argparse.ArgumentParser(description=f'Launch a coding agent on the {example} tools.')
    ap.add_argument('--harness', required=True, choices=sorted(HARNESSES),
                    help='which coding agent to wire up and start')
    ap.add_argument('--mode', default=mode, choices=('todo', 'plan', 'explorer'),
                    help='sandbox mode (default: %(default)s)')
    ap.add_argument('--no-launch', action='store_true',
                    help='set the sandbox up but do not start the agent')
    args = ap.parse_args()

    harness = HARNESSES[args.harness]
    tb = _toolbase()
    _require_installed(tb)

    from sandbox_utils import create_new_sandbox
    sandbox = Path(create_new_sandbox(Path(sandbox_dir), mode=args.mode)).resolve()

    prompt = prompt_path[args.mode] if isinstance(prompt_path, dict) else prompt_path
    (sandbox / harness['instructions']).write_text(Path(prompt).read_text())
    print(f"  wrote {harness['instructions']} from {Path(prompt).name}")

    # Claim the sandbox as its own toolbase project BEFORE activating.
    # `tb activate` walks up the tree for the nearest `.toolbase/`; the repo
    # root ships one (for its profiles), so without this the loadout lands at
    # the repo root instead of here -- the sandbox gets no config, and every
    # example run mutates the checkout.
    (sandbox / '.toolbase').mkdir(exist_ok=True)

    for b in bundles:
        _run([tb, 'activate', f'heptapod/{b}'], cwd=sandbox)
    print(f"  activated: {', '.join(bundles)}")

    # The system prompts name tools bare (EnumerateDiagrams), but toolbase
    # namespaces them as heptapod__* by default.
    (sandbox / '.toolbase' / 'serve.yaml').write_text('default:\n  bare: true\n')
    print('  wrote .toolbase/serve.yaml (bare tool names)')

    if config_keys:
        import config
        for key in config_keys:
            value = getattr(config, key, None)
            if not value:
                print(f"  skipped {key}: not set in config.py")
                continue
            _run([tb, 'config', 'set', 'heptapod', key, str(value)], cwd=sandbox)
            print(f"  set {key} = {value}")

    if post_setup is not None:
        post_setup(sandbox)

    _run([tb, 'connect', args.harness], cwd=sandbox)
    print(f"  wired {args.harness}")

    if args.harness == 'codex':
        _codex_startup_timeout(sandbox)
    _warm_import_cache(tb)

    if args.no_launch:
        print(f"\nSandbox ready: {sandbox}\nStart the agent with: cd {sandbox} && {harness['command']}")
        return

    cmd = _resolve_harness_command(harness['command'])
    if not cmd:
        sys.exit(
            f"\nSandbox ready at {sandbox}\n\n"
            f"...but '{harness['command']}' was not found on PATH or in the usual\n"
            f"install locations. Two ways to use the sandbox anyway:\n\n"
            f"  * open it as a folder in your editor:\n      {sandbox}\n\n"
            f"  * or put the executable on PATH and re-run this launcher.")
    if not shutil.which(harness['command']):
        print(f"  '{harness['command']}' is not on PATH; using {cmd}")

    print(f"\nLaunching {harness['command']} in {sandbox.name} ...")
    os.chdir(sandbox)
    os.execvp(cmd, [cmd])
