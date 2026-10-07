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
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:      # Python < 3.11
    tomllib = None

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
    """Find the harness executable: on PATH, or where a VS Code extension keeps it.

    Claude Code and Codex installed as VS Code extensions never put their
    command on PATH: the binary lives inside the extension directory
    (`~/.vscode-server/extensions` on a remote host, `~/.vscode/extensions`
    on a desktop), whose name carries a version that changes on every update.
    Claude Code's own installer uses `~/.claude/local`. Fall back to the most
    recently installed of those rather than asking for a PATH entry that goes
    stale. Other install locations still need the command on PATH.
    """
    found = shutil.which(command)
    if found:
        return found
    patterns = {
        'claude': ['~/.vscode-server/extensions/anthropic.claude-code-*'
                   '/resources/native-binary/claude',
                   '~/.vscode/extensions/anthropic.claude-code-*'
                   '/resources/native-binary/claude',
                   '~/.claude/local/claude'],
        'codex':  ['~/.vscode-server/extensions/openai.chatgpt-*/bin/*/codex',
                   '~/.vscode/extensions/openai.chatgpt-*/bin/*/codex'],
    }
    candidates = []
    for pattern in patterns.get(command, []):
        candidates += glob.glob(os.path.expanduser(pattern))
    candidates = [c for c in candidates if os.path.isfile(c) and os.access(c, os.X_OK)]
    # by modification time, not by name: "1.10" sorts before "1.9" as text
    return max(candidates, key=os.path.getmtime) if candidates else None


def _edit_codex_server(sandbox: Path, settings: dict, serve_args=()) -> bool:
    """Set keys of the `[mcp_servers.toolbase]` table `tb connect` wrote for Codex.

    `settings` are `key = value` lines to add (existing keys are left alone);
    `serve_args` are appended to the server's `args`. Returns False, changing
    nothing, when the table is not there in the form `tb connect` writes it or
    the result would not be valid TOML: appending a second table of the same
    name would break the whole file.
    """
    cfg = sandbox / '.codex' / 'config.toml'
    if not cfg.exists():
        return False
    try:
        lines = cfg.read_text(encoding='utf8').splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == '[mcp_servers.toolbase]')
    except StopIteration:
        return False
    stop = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith('[')),
                len(lines))
    body = lines[start + 1:stop]
    if serve_args:
        for i, line in enumerate(body):
            key, _, value = line.partition('=')
            if key.strip() != 'args':
                continue
            try:
                args = json.loads(value.strip())
            except ValueError:
                return False
            if not isinstance(args, list):
                return False
            if serve_args[0] not in args:
                body[i] = 'args = ' + json.dumps(args + [str(a) for a in serve_args])
            break
        else:
            return False
    present = {line.partition('=')[0].strip() for line in body}
    body += [f'{key} = {value}' for key, value in settings.items() if key not in present]
    text = "\n".join(lines[:start + 1] + body + lines[stop:]) + "\n"
    if tomllib is not None:
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return False
    cfg.write_text(text, encoding='utf8')
    return True


def _codex_startup_timeout(sandbox: Path, seconds: int) -> None:
    """Give the MCP server longer than Codex's default to start.

    Serving a bundle with heavy imports (coffea, awkward, uproot) from a
    shared filesystem can take longer than that on a cold cache, and Codex
    then reports "MCP client for `toolbase` timed out after 30 seconds".
    """
    if _edit_codex_server(sandbox, {'startup_timeout_sec': seconds}):
        print(f'  set startup_timeout_sec = {seconds} for codex')
    else:
        print('  NOTE: could not set startup_timeout_sec in .codex/config.toml; if codex '
              'reports that the MCP client timed out, add it under [mcp_servers.toolbase]')


def _set_call_timeout(harness: str, sandbox: Path, seconds: int) -> None:
    """Let tool calls run for up to `seconds`.

    `tb connect` wires the harness to `toolbase serve`, which cuts every tool
    call at 60 s. Tools that run an analysis need longer: serve with
    `--call-timeout` instead (and tell Codex, which has a limit of its own).
    """
    extra = ['--call-timeout', str(seconds)]
    try:
        if harness == 'codex':
            if not _edit_codex_server(sandbox, {'tool_timeout_sec': seconds}, extra):
                raise ValueError('unexpected .codex/config.toml')
        else:
            path, table, key = {
                'claude-code': ('.mcp.json', 'mcpServers', 'args'),
                'opencode': ('opencode.json', 'mcp', 'command'),
            }[harness]
            cfg = sandbox / path
            data = json.loads(cfg.read_text())
            entry = data[table]['toolbase']
            if extra[0] not in entry[key]:
                entry[key] = list(entry[key]) + extra
            cfg.write_text(json.dumps(data, indent=2) + "\n")
    except (OSError, KeyError, ValueError, TypeError) as exc:
        print(f'  NOTE: could not raise the tool-call time limit ({exc}); toolbase cuts '
              'calls at 60 s. To change it by hand, make the server command '
              f'`toolbase serve --call-timeout {seconds}`.')
        return
    print(f'  tool calls may run for up to {seconds} s (toolbase serve --call-timeout)')


def _toolkit_python():
    """The interpreter of the installed heptapod toolkit, or None if not found.

    With several versions in toolbase's cache this is the one installed last,
    which is the one in use unless an older version was pinned by hand.
    """
    metas = glob.glob(os.path.expanduser('~/.toolbase/cache/heptapod/*/.install_meta.yaml'))
    for meta in sorted(metas, key=os.path.getmtime, reverse=True):
        try:
            text = Path(meta).read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith('python_path:'):
                python = line.split(':', 1)[1].strip().strip('\'"')
                if os.path.isfile(python):
                    return python
    return None


def _warm_imports(modules, pythons=()) -> None:
    """Import the heavy libraries once, before the agent's first tool call does.

    The first import from a fresh environment, or from a shared filesystem,
    can take longer than a tool call is allowed. Doing it here also says early,
    and plainly, when an environment lacks one of them. The toolkit's own
    interpreter is warmed, and any further one in `pythons` (an analysis
    environment the tools were configured to run with).
    """
    toolkit = _toolkit_python()
    if toolkit is None:
        print('  NOTE: toolkit interpreter not found; skipped warming its import cache')
    statement = 'import ' + ', '.join(modules)
    for python in dict.fromkeys([toolkit, *pythons]):
        if not python:
            continue
        which = "the toolkit's python" if python == toolkit else python
        try:
            r = subprocess.run([python, '-c', statement], capture_output=True, text=True,
                               timeout=600, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f'  NOTE: could not warm the import cache of {which} ({exc})')
            continue
        if r.returncode == 0:
            print(f"  warmed the import cache of {which} ({', '.join(modules)})")
        else:
            last = (r.stderr.strip().splitlines() or ['unknown error'])[-1]
            fix = ("install the bundle's dependencies (tb install . --bundle <name>)"
                   if python == toolkit else "install them into that environment")
            print(f"  NOTE: {which} cannot `{statement}`: {last}\n"
                  f"        the tools that run with it will fail until then: {fix}")


def main(*, example, bundles, prompt_path, sandbox_dir, mode='explorer',
         config_keys=(), post_setup=None, mcp_startup_timeout=None,
         config_values=None, call_timeout=None, warm_imports=(), warm_pythons=()):
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
        post_setup: optional callable(sandbox: Path), run after the bundles
            and config are set and before the harness is wired. Use it to
            stage inputs the example needs inside the sandbox: tools confine
            file access to it, so data living elsewhere has to be linked in
            rather than referenced by absolute path.
        mcp_startup_timeout: seconds Codex should wait for the MCP server to
            start, for bundles whose imports are slow. None leaves Codex's
            default alone. Ignored by the other harnesses.
        config_values: {key: value} of heptapod config fields to set in the
            sandbox, for values that do not come from config.py.
        call_timeout: seconds a single tool call may take. None keeps
            toolbase's default of 60 s; examples whose tools run an analysis
            need more.
        warm_imports: module names to import once with the toolkit's
            interpreter before the agent starts, so that its first tool call
            does not pay for a cold import cache.
        warm_pythons: further interpreters to import them with (an analysis
            environment the tools are configured to use).
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

    from sandbox_utils import create_new_sandbox
    sandbox = Path(create_new_sandbox(Path(sandbox_dir), mode=args.mode)).resolve()

    prompt = prompt_path[args.mode] if isinstance(prompt_path, dict) else prompt_path
    (sandbox / harness['instructions']).write_text(Path(prompt).read_text())
    print(f"  wrote {harness['instructions']} from {Path(prompt).name}")

    # Make the sandbox its own toolbase project BEFORE activating. toolbase
    # takes the nearest directory with a `.toolbase/` in it, looking upward,
    # as the project, and this repo ships one at its root (for the demo
    # profiles). Without this line `tb activate` and `tb config set` below
    # would write to the repository's project instead of the sandbox's, and
    # serve.yaml would have no directory to go into.
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

    for key, value in (config_values or {}).items():
        _run([tb, 'config', 'set', 'heptapod', key, str(value)], cwd=sandbox)
        print(f"  set {key} = {value}")

    if post_setup is not None:
        post_setup(sandbox)

    _run([tb, 'connect', args.harness], cwd=sandbox)
    print(f"  wired {args.harness}")

    if args.harness == 'codex' and mcp_startup_timeout:
        _codex_startup_timeout(sandbox, int(mcp_startup_timeout))
    if call_timeout:
        _set_call_timeout(args.harness, sandbox, int(call_timeout))
    if warm_imports:
        _warm_imports(list(warm_imports), list(warm_pythons))

    if args.no_launch:
        print(f"\nSandbox ready: {sandbox}\nStart the agent with: cd {sandbox} && {harness['command']}")
        return

    cmd = _resolve_harness_command(harness['command'])
    if not cmd:
        sys.exit(f"\nSandbox ready at {sandbox}, but '{harness['command']}' is not on PATH. "
                 f"Install it, then run: cd {sandbox} && {harness['command']}")
    if not shutil.which(harness['command']):
        print(f"  '{harness['command']}' is not on PATH; using {cmd}")

    print(f"\nLaunching {harness['command']} in {sandbox.name} ...")
    os.chdir(sandbox)
    os.execvp(cmd, [cmd])
