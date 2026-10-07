"""
# _projects.py is a part of the HEPTAPOD package.
# Copyright (C) 2026 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Create and modify scaffolded analysis projects on disk.

Pure python (no orchestral, no coffea): copy the template with the package renamed,
keep a small marker file that records what a project was built from, and insert or
replace clearly delimited blocks in files the analyst also edits by hand.

Everything that writes takes a ``Writes`` object, so that an operation touching several
files either completes or leaves the project as it found it.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tools.framework._render import PLACEHOLDER_PACKAGE, package_name_problem

TEMPLATE_DIR = Path(__file__).resolve().parent / "template"
MARKER = ".analysis_framework.json"
FRAMEWORK_VERSION = 1

# Template files are stored under names that are inert inside this repository and get
# their real name when a project is written.
RENAMED = {"gitignore": ".gitignore", "github": ".github"}
SKIPPED_DIRS = {"__pycache__", ".ipynb_checkpoints", ".ruff_cache", ".pytest_cache", ".mypy_cache"}
SKIPPED_SUFFIXES = {".pyc", ".pyo"}


class BlockError(ValueError):
    """The markers of a component's block in a file are damaged."""


class SourceError(ValueError):
    """A file of the project that has to be understood does not parse."""


# --------------------------------------------------------------------------- #
# writing, with a way back
# --------------------------------------------------------------------------- #

class Writes:
    """File writes that are undone together if any step of an operation fails.

        with Writes() as writes:
            writes.write_text(path, text)
            ...

    If the block raises, every file written through the object is put back as it was
    (or removed, if it did not exist), directories it created are removed again, and
    the exception carries on. Previous contents are held in memory, which suits the
    small text files of a project.
    """

    def __init__(self) -> None:
        self._before: Dict[Path, Optional[bytes]] = {}
        self._directories: List[Path] = []

    def mkdir(self, directory: Path) -> None:
        """Create a directory and the parents it lacks, remembering which were new."""
        missing = []
        current = Path(directory)
        while not current.exists():
            missing.append(current)
            current = current.parent
        for new in reversed(missing):
            new.mkdir()
            self._directories.append(new)

    def _prepare(self, path: Path) -> None:
        if path not in self._before:
            self._before[path] = path.read_bytes() if path.is_file() else None
        self.mkdir(path.parent)

    def write_bytes(self, path: Path, data: bytes) -> None:
        path = Path(path)
        self._prepare(path)
        path.write_bytes(data)

    def write_text(self, path: Path, text: str) -> None:
        """Write text as UTF-8, with its line endings exactly as given."""
        self.write_bytes(path, text.encode("utf8"))

    def copy(self, source: Path, path: Path) -> None:
        path = Path(path)
        if path.exists() and os.path.samefile(str(source), str(path)):
            return                      # already there: nothing to copy, nothing to undo
        self._prepare(path)
        shutil.copyfile(str(source), str(path))

    def undo(self) -> None:
        for path, data in reversed(list(self._before.items())):
            try:
                if data is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.write_bytes(data)
            except OSError:
                pass
        for directory in reversed(self._directories):
            try:
                directory.rmdir()
            except OSError:
                pass
        self._before.clear()
        self._directories.clear()

    def __enter__(self) -> "Writes":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc_type is not None:
            self.undo()
        return False


def _read(path: Path) -> Tuple[str, bool]:
    """A text file with ``\\n`` line endings, and whether it used ``\\r\\n`` on disk."""
    text = path.read_bytes().decode("utf8")
    crlf = "\r\n" in text
    return (text.replace("\r\n", "\n") if crlf else text), crlf


def read_text(project: Path, relative: str) -> str:
    """Content of a file of the project with ``\\n`` line endings, or "" if it is not there."""
    path = project / relative
    return _read(path)[0] if path.is_file() else ""


def render_text(text: str, package: str, tokens: Optional[Dict[str, str]] = None) -> str:
    """Rename the placeholder package and fill in ``__TOKEN__`` placeholders.

    Done in one pass over the template text, so a value that happens to contain a
    placeholder (a title mentioning ``__AUTHOR__``) is written as it is and not
    filled in a second time.
    """
    tokens = dict(tokens or {})
    pattern = rf"\b{re.escape(PLACEHOLDER_PACKAGE)}\b"
    if tokens:
        pattern += "|" + "|".join(re.escape(token) for token in sorted(tokens, key=len, reverse=True))

    def fill(match):
        found = match.group(0)
        return tokens[found] if found in tokens else package

    return re.sub(pattern, fill, text)


def _target_parts(parts: Iterable[str], package: str) -> List[str]:
    return [package if p == PLACEHOLDER_PACKAGE else RENAMED.get(p, p) for p in parts]


def template_files(subdir: str) -> List[Path]:
    """Every file of one template tree (``core`` or ``components/<name>``), sorted."""
    root = TEMPLATE_DIR / subdir
    if not root.is_dir():
        raise FileNotFoundError(f"template '{subdir}' not found in {TEMPLATE_DIR}")
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix in SKIPPED_SUFFIXES:
            continue
        if SKIPPED_DIRS & set(path.relative_to(root).parts):
            continue
        files.append(path)
    return files


def template_targets(subdir: str, package: str) -> List[str]:
    """Where the files of a template tree go in a project, as relative paths."""
    root = TEMPLATE_DIR / subdir
    return [str(Path(*_target_parts(source.relative_to(root).parts, package)))
            for source in template_files(subdir)]


def copy_template(subdir: str, project: Path, package: str, writes: Writes,
                  tokens: Optional[Dict[str, str]] = None,
                  overwrite: bool = False,
                  never_overwrite: Iterable[str] = ()) -> Tuple[List[str], List[str]]:
    """Copy a template tree into a project. Returns (written, kept) relative paths.

    Files that already exist are left alone unless ``overwrite`` is set, so that
    re-running never discards the analyst's edits by accident. ``never_overwrite``
    lists files (by their path in the project) that hold the analyst's own data and
    are kept even then.
    """
    root = TEMPLATE_DIR / subdir
    protected = {str(Path(name)) for name in never_overwrite}
    written, kept = [], []
    for source in template_files(subdir):
        relative = Path(*_target_parts(source.relative_to(root).parts, package))
        target = project / relative
        if target.exists() and (not overwrite or str(relative) in protected):
            kept.append(str(relative))
            continue
        data = source.read_bytes()
        try:
            text = data.decode("utf8")
        except UnicodeDecodeError:
            writes.write_bytes(target, data)
        else:
            writes.write_text(target, render_text(text, package, tokens))
        written.append(str(relative))
    return written, kept


def write_file(project: Path, relative: str, text: str, writes: Writes,
               overwrite: bool = False) -> bool:
    """Write one generated file. Returns False (and writes nothing) if it is kept."""
    target = project / relative
    if target.exists() and not overwrite:
        return False
    writes.write_text(target, text)
    return True


# --------------------------------------------------------------------------- #
# the marker
# --------------------------------------------------------------------------- #

def read_marker(project: Path) -> Optional[Dict[str, Any]]:
    """The project's marker, or None if there is none or it cannot be read."""
    path = project / MARKER
    if not path.is_file():
        return None
    try:
        marker = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    return marker if isinstance(marker, dict) else None


def write_marker(project: Path, data: Dict[str, Any], writes: Writes) -> None:
    data = {"framework_version": FRAMEWORK_VERSION, **data}
    writes.write_text(project / MARKER, json.dumps(data, indent=2) + "\n")


def _is_package_dir(project: Path, name: Any) -> bool:
    """Is ``name`` a usable package name and a directory directly inside the project?

    The name ends up in ``python -m <name>.tools.check`` and in file paths, and the
    marker it may come from is a file anyone can edit: only a plain module name is
    accepted, never a path.
    """
    if package_name_problem(name) is not None:
        return False
    return (project / name / "tools" / "processor.py").is_file() and \
        (project / name / "definitions" / "objects.py").is_file()


def find_package(project: Path) -> Optional[str]:
    """Name of the analysis package of a project, from the marker or from its layout."""
    if not project.is_dir():
        return None
    marker = read_marker(project)
    if isinstance(marker, dict) and _is_package_dir(project, marker.get("package")):
        return marker["package"]
    for child in sorted(project.iterdir()):
        if child.is_dir() and _is_package_dir(project, child.name):
            return child.name
    return None


# --------------------------------------------------------------------------- #
# reading the analyst's files without importing them
# --------------------------------------------------------------------------- #

def defined_keys(text: str, variable: str, where: str = "the file",
                 outside: Optional[Tuple[int, int]] = None) -> List[str]:
    """The string keys python source gives to the dict ``variable``, in source order.

    Understands the ways such a table gets filled: a dict literal assigned to it,
    ``variable["key"] = ...`` (and ``variable["key"]["inner"] = ...``, for the outer
    key), ``variable.update({...})``, ``variable.update(key=...)`` and
    ``variable.setdefault("key", ...)``. Keys that are computed (in a loop, say) cannot
    be read off the source and are not found. Comments and docstrings are not code and
    are not looked at. ``outside`` is a span of lines (first, last; see block_lines)
    whose keys are left out. Raises SourceError if the source does not parse.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise SourceError(f"{where} has a python syntax error on line {exc.lineno}: "
                          f"{exc.msg}") from None
    found: List[Tuple[int, int, str]] = []

    def add(node: Any, line: int, column: int) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append((line, column, node.value))

    def literal(node: Any) -> None:
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if key is not None:
                    add(key, key.lineno, key.col_offset)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == variable:
                    literal(getattr(node, "value", None))
                outer, first = target, None
                while isinstance(outer, ast.Subscript):
                    first, outer = outer.slice, outer.value
                if first is not None and isinstance(outer, ast.Name) and outer.id == variable:
                    add(first, target.lineno, target.col_offset)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and isinstance(node.func.value, ast.Name) and node.func.value.id == variable):
            if node.func.attr == "update":
                for argument in node.args:
                    literal(argument)
                found += [(node.lineno, node.col_offset, keyword.arg)
                          for keyword in node.keywords if keyword.arg]
            elif node.func.attr == "setdefault" and node.args:
                add(node.args[0], node.lineno, node.col_offset)
    keys: List[str] = []
    for line, _, key in sorted(found):
        if outside is not None and outside[0] <= line <= outside[1]:
            continue
        if key not in keys:
            keys.append(key)
    return keys


def top_level_keys(text: str, where: str = "the file",
                   outside: Optional[Tuple[int, int]] = None) -> List[str]:
    """Keys of the top-level mapping of a yaml text, as strings, in the order written.

    The whole text is parsed (with PyYAML when it is there, otherwise the lines that
    start a top-level entry are read off), so aliases to anchors defined anywhere in
    it are fine. ``outside`` is a span of lines (first, last; see block_lines) whose
    keys are left out. Raises SourceError if the text is not a yaml mapping.
    """
    found: List[Tuple[int, str]] = []
    try:
        import yaml
    except ImportError:
        pattern = re.compile(r"""(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_][^:#]*?))\s*:(?:\s|$)""")
        for number, line in enumerate(text.splitlines(), start=1):
            match = pattern.match(line)
            if match:
                found.append((number, next(g for g in match.groups() if g is not None)))
    else:
        try:
            root = yaml.compose(text, Loader=yaml.SafeLoader)
        except yaml.YAMLError as exc:
            problem = " ".join(str(exc).split())
            raise SourceError(f"{where} is not valid yaml: {problem[:300]}") from None
        if root is not None:
            if not isinstance(root, yaml.MappingNode):
                raise SourceError(f"{where} must hold a mapping at its top level")
            for key, _ in root.value:
                if isinstance(key, yaml.ScalarNode) and key.tag != "tag:yaml.org,2002:merge":
                    found.append((key.start_mark.line + 1, str(key.value)))
    keys: List[str] = []
    for line, key in found:
        if outside is not None and outside[0] <= line <= outside[1]:
            continue
        if key not in keys:
            keys.append(key)
    return keys


def defined_objects(project: Path, package: str,
                    ignore_block: Optional[str] = None) -> Dict[str, List[str]]:
    """Object names defined in definitions/objects.py, read without importing it.

    ``ignore_block`` names a component whose own block is left out, so that a
    component asking "is this name taken?" does not find itself.
    """
    relative = f"{package}/definitions/objects.py"
    text = read_text(project, relative)
    outside = block_lines(text, ignore_block) if ignore_block else None
    return {"primary": defined_keys(text, "primary_objs", relative, outside),
            "derived": defined_keys(text, "derived_objs", relative, outside)}


# --------------------------------------------------------------------------- #
# delimited blocks in hand-edited files
# --------------------------------------------------------------------------- #

def block_markers(component: str, comment: str = "#") -> Tuple[str, str]:
    return (f"{comment} >>> component: {component} >>>",
            f"{comment} <<< component: {component} <<<")


def _block_span(text: str, component: str, comment: str = "#") -> Optional[Tuple[int, int]]:
    """Where the component's block is in ``text`` (which uses ``\\n``), or None.

    The two marker lines must each be a line of their own, starting in the first
    column. Anything else (a marker that is indented, repeated, missing its partner or
    out of order) raises BlockError: guessing where such a block ends could delete the
    analyst's own lines.
    """
    begin, end = block_markers(component, comment)
    starts = [m for m in re.finditer(rf"^{re.escape(begin)}[ \t]*$", text, flags=re.M)]
    stops = [m for m in re.finditer(rf"^{re.escape(end)}[ \t]*$", text, flags=re.M)]
    stray = text.count(begin) - len(starts) + text.count(end) - len(stops)
    if not starts and not stops and not stray:
        return None
    if len(starts) == 1 and len(stops) == 1 and not stray and starts[0].start() < stops[0].start():
        return starts[0].start(), stops[0].end()
    raise BlockError(
        f"the markers of the '{component}' block are damaged: there must be exactly one "
        f"line '{begin}' followed, further down, by one line '{end}', both starting in "
        f"the first column (found {text.count(begin)} and {text.count(end)})")


def block_lines(text: str, component: str, comment: str = "#") -> Optional[Tuple[int, int]]:
    """First and last line (counted from 1) of the component's block, or None.

    Raises BlockError if the block's markers are damaged.
    """
    text = text.replace("\r\n", "\n")
    span = _block_span(text, component, comment)
    if span is None:
        return None
    return text.count("\n", 0, span[0]) + 1, text.count("\n", 0, span[1]) + 1


def has_block(text: str, component: str, comment: str = "#") -> bool:
    """Is the component's block in the text? Raises BlockError if its markers are damaged."""
    return _block_span(text.replace("\r\n", "\n"), component, comment) is not None


def strip_block(text: str, component: str, comment: str = "#") -> str:
    """The text without the component's block (unchanged if it has none)."""
    text = text.replace("\r\n", "\n")
    span = _block_span(text, component, comment)
    return text if span is None else text[:span[0]] + text[span[1]:]


def upsert_block(text: str, component: str, body: str, overwrite: bool = False,
                 comment: str = "#", gap: int = 2) -> Tuple[str, str]:
    """Add a component's block to the end of a file, or replace the one already there.

    ``text`` uses ``\\n`` line endings. Returns ``(new_text, action)`` with action
    "added", "replaced" or "kept". An existing block is kept unless ``overwrite`` is
    set, since it may have been edited. Raises BlockError, changing nothing, if the
    block's markers are damaged.
    """
    begin, end = block_markers(component, comment)
    block = f"{begin}\n{body.strip(chr(10))}\n{end}\n"
    span = _block_span(text, component, comment)
    if span is not None:
        if not overwrite:
            return text, "kept"
        start, stop = span
        if stop < len(text) and text[stop] == "\n":
            stop += 1
        return text[:start] + block + text[stop:], "replaced"
    separator = "" if not text else ("\n" * gap if text.endswith("\n") else "\n" * (gap + 1))
    return text + separator + block, "added"


def edit_block(project: Path, relative: str, component: str, body: str, writes: Writes,
               overwrite: bool = False, comment: str = "#", gap: int = 2) -> str:
    """Apply upsert_block to a file of the project (created if missing). Returns the action.

    The file keeps the line endings it has.
    """
    path = project / relative
    text, crlf = _read(path) if path.is_file() else ("", False)
    try:
        new_text, action = upsert_block(text, component, body, overwrite, comment, gap)
    except BlockError as exc:
        raise BlockError(f"{relative}: {exc}") from None
    if action != "kept":
        writes.write_text(path, new_text.replace("\n", "\r\n") if crlf else new_text)
    return action


def safe_path(base_directory: str, relative: str, follow_links: bool = False) -> Optional[str]:
    """Absolute path of ``relative`` inside ``base_directory``, or None if it escapes.

    By default links are not resolved: data linked into the working directory is
    meant to be readable through the link. With ``follow_links`` the place the path
    really leads to must be inside the working directory as well, which is what
    anything that gets written to has to satisfy.
    """
    if relative is None or relative == "":
        return None
    base = os.path.abspath(base_directory)
    full = os.path.abspath(os.path.join(base, relative))
    if full != base and not full.startswith(base + os.sep):
        return None
    if follow_links:
        real_base = os.path.realpath(base)
        real = os.path.realpath(full)
        if real != real_base and not real.startswith(real_base + os.sep):
            return None
    return full
