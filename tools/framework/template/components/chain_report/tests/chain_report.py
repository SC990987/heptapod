"""Chain report: what does a change do to the analysis chain?

    python tests/chain_report.py compute state.json          # run the chain, write its state
    python tests/chain_report.py render base.json new.json   # compare two states, as markdown

``compute`` runs every channel and every histogram collection over the small files
listed in tests/fixtures.yaml and records what happened: the static consistency checks,
the cutflows (raw and weighted), the histograms that stayed empty, and every warning.

``render`` puts two such states side by side, typically the main branch and a pull
request, and prints what moved. It exits with status 1 when the newer state has *new
errors* (a new crash, a new inconsistency between configs and definitions, or a cut,
object, weight, histogram or counter that newly fails), and also when the chain could
not be run on a fixture at all, on both sides or with no fixture registered: then
nothing was compared. Everything else is information for the reviewer.

This checks that the chain executes and shows how its numbers move. It does not check
that the physics is right: a cut that runs cleanly can still be the wrong cut.
"""

import json
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

FIXTURES_CFG = os.path.join(HERE, "fixtures.yaml")

# Warnings that mean something broke, as opposed to "does not apply to this sample".
FAILURE_MARKERS = ("could not be", "object_weight failed")


def load_fixtures():
    """The fixtures listed in tests/fixtures.yaml, and the chunk size to run them with."""
    with open(FIXTURES_CFG, encoding="utf8") as handle:
        cfg = yaml.safe_load(handle) or {}
    fixtures = cfg.get("fixtures") if isinstance(cfg, dict) else None
    fixtures = [] if fixtures is None else fixtures
    if not isinstance(cfg, dict) or not isinstance(fixtures, list) or not all(
            isinstance(f, dict) and f.get("dataset") and f.get("file") for f in fixtures):
        raise ValueError("tests/fixtures.yaml must hold 'fixtures:', a list of entries with at "
                         "least 'dataset' and 'file' (tests/make_fixture.py writes them)")
    return fixtures, int(cfg.get("chunksize") or 100)


def _plain(value):
    """``value`` in a form json can write: text keys, lists for sets and tuples."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(item) for item in value), key=str)
    return value


def compute_state():
    """Run the chain over every fixture and return a json-able description of the result."""
    from analysis_pkg.tools import check

    state = {"versions": check._versions(), "static": check.static_report(),
             "fixtures": {}, "run_errors": {}}
    try:
        fixtures, chunksize = load_fixtures()
    except Exception as exc:
        state["run_errors"]["(fixtures)"] = f"{type(exc).__name__}: {exc}"
        return state
    if not fixtures:
        state["run_errors"]["(no fixtures)"] = (
            "tests/fixtures.yaml lists no fixture: make one with tests/make_fixture.py")
    for fixture in fixtures:
        dataset = str(fixture["dataset"])
        if state["static"]["errors"]:
            state["run_errors"][dataset] = "not run: the static checks found errors"
            continue
        path = str(fixture["file"])
        if not os.path.isabs(path) and "://" not in path:
            path = os.path.join(HERE, path)
        try:
            skim_factor = float(fixture.get("skim_factor") or 1.0)
        except (TypeError, ValueError):
            state["run_errors"][dataset] = "tests/fixtures.yaml: skim_factor is not a number"
            continue
        metadata = {"is_data": fixture.get("is_data", False) is True,
                    "year": str(fixture.get("year", "") or ""),
                    "skim_factor": skim_factor}
        fileset = {dataset: {"files": [path], "metadata": metadata}}
        # Small chunks on purpose: several chunks per file exercise the merging of
        # outputs, and on data a chunk can come back empty after the golden JSON.
        # strict=False so that one failing cut is reported instead of hiding the rest.
        report = check.run_report(fileset, chunksize=chunksize, maxchunks=None, strict=False)
        if report["ok"] and dataset in report["datasets"]:
            state["fixtures"][dataset] = report["datasets"][dataset]
        else:
            details = [part for part in (report["error"], report.get("traceback")) if part]
            state["run_errors"][dataset] = "\n\n".join(details) or "the run returned no output"
    return state


def failures(warnings):
    """The warnings that report something failing."""
    return sorted(w for w in warnings if any(marker in w for marker in FAILURE_MARKERS))


def failure_key(message):
    """What a failure is about, for telling new failures from old ones.

    A histogram that cannot be filled fails the same way in every channel, so its
    channel prefix is dropped: adding a channel must not turn an old histogram problem
    into a new error. A failing cut or object is kept per channel, because which cuts a
    channel applies is exactly what a change decides.
    """
    if "histogram '" in message and message.startswith("["):
        return message.split("] ", 1)[-1]
    return message


def _cutflow_rows(fixture_state):
    """{(channel, cut): (raw, weighted)} for one fixture."""
    rows = {}
    for channel, cuts in (fixture_state.get("cutflow") or {}).items():
        for row in cuts:
            # ten significant digits: beyond that two runs differ by rounding alone
            rows[(channel, row["cut"])] = (row["raw"], float(f"{float(row['weighted']):.10g}"))
    return rows


def new_errors(base, new):
    """Reasons the newer state is worse in a way that should turn the check red."""
    reasons = []
    for message in sorted(set(new["static"]["errors"]) - set(base["static"]["errors"])):
        reasons.append(f"new static error: {message.splitlines()[0]}")
    for dataset, error in new["run_errors"].items():
        if dataset in base["run_errors"]:
            reasons.append(f"`{dataset}`: the chain fails on both sides (environment, test "
                           "harness, or the base branch itself is broken)")
        else:
            reasons.append(f"`{dataset}`: the chain fails with this change")
    for dataset, state in new["fixtures"].items():
        if dataset not in base["fixtures"]:
            continue  # nothing to compare with: a new fixture, or the base side failed
        before = {failure_key(m) for m in failures(base["fixtures"][dataset]["warnings"])}
        reported = set()
        for message in failures(state["warnings"]):
            key = failure_key(message)
            if key not in before and key not in reported:
                reported.add(key)
                reasons.append(f"`{dataset}`: {message}")
    return reasons


def _bullets(items, empty="_none_"):
    return "\n".join(f"- `{item}`" for item in items) if items else empty


def _cell(text):
    """Text for a cell of a markdown table: a ``|`` in it would end the cell."""
    return str(text).replace("|", "\\|")


def render(base, new):
    """Markdown comparison of two states (base first)."""
    out = ["## Chain report", "",
           "_Base -> this change. Checks that the chain executes and shows how its numbers "
           "move; it does not check that the physics is right._", ""]
    errors = new_errors(base, new)
    if errors:
        out += ["### This change introduces new errors", ""] + [f"- {e}" for e in errors] + [""]
    else:
        out += ["### No new errors", ""]

    b_inv = base["static"].get("inventory") or {}
    n_inv = new["static"].get("inventory") or {}
    out += ["### Summary", "", "| | base | this change |", "|---|--:|--:|",
            f"| channels | {len(b_inv.get('channels', []))} | {len(n_inv.get('channels', []))} |",
            f"| histograms defined | {b_inv.get('n_hists', 0)} | {n_inv.get('n_hists', 0)} |",
            f"| static errors | {len(base['static']['errors'])} | {len(new['static']['errors'])} |",
            f"| static warnings | {len(base['static']['warnings'])} | {len(new['static']['warnings'])} |",
            f"| fixtures run | {len(base['fixtures'])} | {len(new['fixtures'])} |",
            f"| fixtures failing | {len(base['run_errors'])} | {len(new['run_errors'])} |", ""]

    for label, state in (("base", base), ("this change", new)):
        for dataset, error in state["run_errors"].items():
            out += [f"<details><summary>{label}: `{dataset}` did not run</summary>", "",
                    "```", str(error).strip()[:4000], "```", "", "</details>", ""]

    added = sorted(set(n_inv.get("channels", [])) - set(b_inv.get("channels", [])))
    removed = sorted(set(b_inv.get("channels", [])) - set(n_inv.get("channels", [])))
    if added or removed:
        out += [f"**Channels added:** {added or '_none_'}  **removed:** {removed or '_none_'}", ""]
    fixed = sorted(set(base["static"]["errors"]) - set(new["static"]["errors"]))
    if fixed:
        out += ["**Static errors fixed:**", _bullets([m.splitlines()[0] for m in fixed]), ""]

    for dataset in sorted(set(base["fixtures"]) | set(new["fixtures"])):
        b, n = base["fixtures"].get(dataset), new["fixtures"].get(dataset)
        out += [f"### `{dataset}`", ""]
        if b is None or n is None:
            out += [f"_only available on {'this change' if b is None else 'the base'}: "
                    "nothing to compare_", ""]
            continue
        changes = False
        if b["scaled_sum_weights"] != n["scaled_sum_weights"]:
            changes = True
            out += [f"**Sum of generator weights changed:** {b['scaled_sum_weights']} -> "
                    f"{n['scaled_sum_weights']}", ""]
        b_warn, n_warn = set(b["warnings"]), set(n["warnings"])
        if n_warn - b_warn:
            changes = True
            out += [f"**New warnings ({len(n_warn - b_warn)}):**", _bullets(sorted(n_warn - b_warn)), ""]
        if b_warn - n_warn:
            changes = True
            out += [f"**Warnings gone ({len(b_warn - n_warn)}):**", _bullets(sorted(b_warn - n_warn)), ""]
        b_empty, n_empty = set(b["empty_hists"]), set(n["empty_hists"])
        if n_empty - b_empty:
            changes = True
            out += ["**Histograms newly empty:**", _bullets(sorted(n_empty - b_empty)), ""]
        if b_empty - n_empty:
            changes = True
            out += ["**Histograms newly filled:**", _bullets(sorted(b_empty - n_empty)), ""]
        b_rows, n_rows = _cutflow_rows(b), _cutflow_rows(n)
        moved = [key for key in list(b_rows) + [k for k in n_rows if k not in b_rows]
                 if b_rows.get(key) != n_rows.get(key)]
        if moved:
            changes = True
            out += [f"**Cutflow changed in {len({c for c, _ in moved})} channel(s)** "
                    "(events left after each event-level cut):", "",
                    "| channel | cut | raw base | raw new | weighted base | weighted new |",
                    "|---|---|--:|--:|--:|--:|"]
            for channel, cut in moved[:200]:
                before, after = b_rows.get((channel, cut)), n_rows.get((channel, cut))
                cells = [str(x) if x is not None else "-" for x in (
                    before and before[0], after and after[0], before and before[1], after and after[1])]
                out.append(f"| {_cell(channel)} | {_cell(cut)} | {cells[0]} | {cells[1]} | "
                           f"{cells[2]} | {cells[3]} |")
            if len(moved) > 200:
                out.append(f"\n_... and {len(moved) - 200} more rows_")
            out.append("")
        if not changes:
            out += ["No change in cutflows, warnings or histogram coverage.", ""]
        out += ["<details><summary>All warnings with this change</summary>", "",
                _bullets(sorted(n_warn)), "", "</details>", ""]
    return "\n".join(out)


def main(argv):
    # compute writes to a file rather than stdout: libraries used by the chain print
    # banners to stdout at the C level, which would corrupt a json dump there
    if len(argv) == 2 and argv[0] == "compute":
        with open(argv[1], "w", encoding="utf8") as handle:
            json.dump(_plain(compute_state()), handle, indent=2, sort_keys=True, default=str)
        return 0
    if len(argv) == 3 and argv[0] == "render":
        with open(argv[1], encoding="utf8") as handle:
            base = json.load(handle)
        with open(argv[2], encoding="utf8") as handle:
            new = json.load(handle)
        print(render(base, new))
        return 1 if new_errors(base, new) else 0
    sys.exit("usage: chain_report.py compute OUT.json | render BASE.json NEW.json")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
