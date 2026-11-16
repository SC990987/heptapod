"""
# sandbox_utils.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.
"""
"""
Utilities for managing demo sandbox directories.
"""

import shutil
import re
from pathlib import Path


def create_new_sandbox(demo_files_dir: Path, mode: str = "todo", target=None) -> str:
    """
    Create a new sandbox directory with the next available number.
    Copies appropriate template files based on the mode.

    Args:
        demo_files_dir: directory the numbered sandbox is created under
        mode: Operating mode - "todo", "plan", or "explorer"
              - "todo": template files AND todos.md
              - "plan": template files only (agent makes its own plan)
              - "explorer": template files only
        target: a directory of the caller's choosing, used instead of the next
            numbered one and created if missing. A directory that already holds
            files is refused (a new git repository, holding nothing but .git,
            counts as empty), unless it was set up as a sandbox before (it has a
            .toolbase/ directory): then it is used as it is, and the template
            files are not copied over its files again.

    Returns:
        Absolute path to the new sandbox. The system prompt is the caller's
        business: each example keeps its own under <example>/prompts/.

    Raises:
        ValueError: for an unknown mode, or a target that cannot be used.
    """
    # Mode configuration
    MODE_CONFIG = {
        "todo":     {"copy_todos": True,  "description": "Todo list mode"},
        "plan":     {"copy_todos": False, "description": "Plan mode"},
        "explorer": {"copy_todos": False, "description": "Interactive explorer mode"},
    }

    # Validate mode
    if mode not in MODE_CONFIG:
        raise ValueError(f"Invalid mode '{mode}'. Must be one of: {', '.join(MODE_CONFIG.keys())}")

    config = MODE_CONFIG[mode]

    if target is not None:
        # A directory the caller chose. Never write the sandbox's files over
        # someone's own: only an empty directory, or one set up before, is used.
        new_sandbox_path = Path(target).expanduser().resolve()
        new_sandbox_name = str(new_sandbox_path)
        if new_sandbox_path.exists() and not new_sandbox_path.is_dir():
            raise ValueError(f"{new_sandbox_path} exists and is not a directory")
        held = {item.name for item in new_sandbox_path.iterdir()} if new_sandbox_path.is_dir() else set()
        if held - {'.git'}:
            if not (new_sandbox_path / '.toolbase').is_dir():
                raise ValueError(f"{new_sandbox_path} already holds files and was not set up "
                                 "as a sandbox before: choose a new or an empty directory (a new "
                                 "git repository, holding nothing but .git, will do)")
            print(f"Using the sandbox set up before in {new_sandbox_path} ({config['description']})")
            return str(new_sandbox_path)
        new_sandbox_path.mkdir(parents=True, exist_ok=True)
        print(f"Created sandbox: {new_sandbox_path} ({config['description']})")
    else:
        # Ensure the workspace dir exists. On a fresh checkout it may not be,
        # in which case iterdir() would otherwise raise FileNotFoundError.
        demo_files_dir.mkdir(parents=True, exist_ok=True)

        # Find existing sandbox directories
        existing_sandboxes = []
        for item in demo_files_dir.iterdir():
            if item.is_dir() and item.name.startswith('sandbox'):
                match = re.match(r'sandbox(\d+)', item.name)
                if match:
                    existing_sandboxes.append(int(match.group(1)))

        # Determine next sandbox number
        next_num = max(existing_sandboxes) + 1 if existing_sandboxes else 1
        new_sandbox_name = f'sandbox{next_num:03d}'
        new_sandbox_path = demo_files_dir / new_sandbox_name

        # Create new sandbox directory
        new_sandbox_path.mkdir(exist_ok=True)
        print(f"Created new sandbox: {new_sandbox_name} ({config['description']})")

    # Copy template files (excluding .DS_Store files)
    template_dir = demo_files_dir / 'template'
    if template_dir.exists():
        def ignore_ds_store(directory, contents):
            """Ignore function to exclude .DS_Store files during copy."""
            return ['.DS_Store']

        for item in template_dir.iterdir():
            if item.name == '.DS_Store':
                continue
            src = item
            dst = new_sandbox_path / item.name
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True, ignore=ignore_ds_store)
                print(f"Copied template directory: {item.name}")
            else:
                shutil.copy2(src, dst)
                print(f"Copied template file: {item.name}")

    # Copy todos.md only in "todo" mode. It lives beside the example, next to
    # its template/ dir, so each example owns its own task list.
    if config["copy_todos"]:
        todos_src = demo_files_dir / 'todos' / 'todos.md'
        if todos_src.exists():
            shutil.copy2(todos_src, new_sandbox_path / 'todos.md')
            print(f"Copied todos.md to {new_sandbox_name}")
        else:
            print(f"Warning: no todos.md at {todos_src}; "
                  f"'{mode}' mode expects one there")

    return str(new_sandbox_path)
