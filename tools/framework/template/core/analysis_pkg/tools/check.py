"""Self-check: are the configs and definitions consistent, and does the analysis run?

    python -m analysis_pkg.tools.check                      # static checks only
    python -m analysis_pkg.tools.check --sample NAME        # ... plus a short run on a sample
    python -m analysis_pkg.tools.check --file events.root   # ... or on any single file

The static checks need no input file: every channel in configs/selections.yaml must
use objects and cuts that exist, every histogram collection must list histograms that
exist, and so on. The run processes a few thousand events through every channel and
reports the cutflows, the histograms that stayed empty and every warning the
processor recorded. A mistake in the configs or definitions is reported as an error
of the check, not as a failure of the checker.

Exit status is 0 when there are no errors and 1 otherwise. ``--json PATH`` writes the
full report (use it rather than parsing the printed summary).
"""

import argparse
import ast
import glob
import json
import os
import platform
import sys
import time
import traceback

from analysis_pkg import BASE_DIR, TREE_NAME, __version__

RESERVED_OBJECT_NAMES = ("evt_weights", "ch")
RESERVED_AXIS_NAMES = ("weight", "sample", "threads", "channel")


def _versions():
    versions = {"python": platform.python_version(), "package": __version__}
    for module in ("coffea", "awkward", "uproot", "hist", "numpy"):
        try:
            versions[module] = __import__(module).__version__
        except Exception:
            versions[module] = None
    return versions


def _plain(value):
    """``value`` in a form json can write: text keys, lists for sets and tuples."""
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_plain(item) for item in value), key=str)
    return value


# The tables of the definitions: a name given twice in one of them replaces the first
# definition without a word, which python allows and nobody means.
DEFINITION_TABLES = {
    "objects.py": ("primary_objs", "derived_objs"),
    "cuts.py": ("obj_cut_defs", "evt_cut_defs"),
    "hists.py": ("hist_defs", "counter_defs"),
}


def _literal_keys(node, prefix=()):
    """(path, line) for every text key of a dict literal, nested dicts included."""
    found = []
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            path = prefix + (key.value,)
            found.append((path, key.lineno))
            if isinstance(value, ast.Dict):
                found += _literal_keys(value, path)
    return found


def _subscript_path(node):
    """``table["a"]["b"]`` -> ("table", ("a", "b")); None for anything else."""
    path = []
    while isinstance(node, ast.Subscript):
        key = node.slice
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return None
        path.append(key.value)
        node = node.value
    if isinstance(node, ast.Name):
        return node.id, tuple(reversed(path))
    return None


def duplicate_definitions(source, tables=()):
    """Names defined more than once in the text of one definitions file.

    Returns messages for (a) a key written twice inside one dict literal, anywhere in
    the file, and (b) an entry of one of ``tables`` that a later top-level statement
    defines again (``table["x"] = ...``, ``table.update({"x": ...})``, the same one
    level down for ``table["obj"]["x"]``). Only statements at the top level of the
    file are followed, and only keys written as plain text.
    """
    tree = ast.parse(source)
    messages = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            seen = {}
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if key.value in seen:
                        messages.append(
                            f"'{key.value}' is written twice in one dict (lines "
                            f"{seen[key.value]} and {key.lineno}): the second replaces the first")
                    else:
                        seen[key.value] = key.lineno
    defined = {}                      # (table, path) -> line of the first definition

    def define(table, path, line):
        if not path:
            return
        first = defined.setdefault((table, path), line)
        if first != line:
            where = table + "".join(f"['{part}']" for part in path[:-1])
            messages.append(
                f"'{path[-1]}' is defined twice in {where} (lines {first} and {line}): the "
                "later definition replaces the earlier one")

    for statement in tree.body:
        targets, value = [], None
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        for target in targets:
            if isinstance(target, ast.Name) and target.id in tables:
                # the table itself is (re)started here
                for key in [k for k in defined if k[0] == target.id]:
                    del defined[key]
                if isinstance(value, ast.Dict):
                    for path, line in _literal_keys(value):
                        defined.setdefault((target.id, path), line)
                continue
            located = _subscript_path(target)
            if located is None or located[0] not in tables:
                continue
            table, path = located
            define(table, path, target.lineno)
            if isinstance(value, ast.Dict):
                for sub_path, line in _literal_keys(value, path):
                    defined.setdefault((table, sub_path), line)
        call = statement.value if isinstance(statement, ast.Expr) else None
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "update" and call.args and isinstance(call.args[0], ast.Dict)):
            base = call.func.value
            located = (base.id, ()) if isinstance(base, ast.Name) else _subscript_path(base)
            if located is not None and located[0] in tables:
                table, prefix = located
                literal = _literal_keys(call.args[0], prefix)
                top = len(prefix) + 1
                for path, line in literal:
                    if len(path) == top:
                        define(table, path, line)
                    else:
                        defined.setdefault((table, path), line)
    return messages


def _duplicate_errors():
    """duplicate_definitions over the three definitions files, as static errors."""
    errors = []
    for name, tables in DEFINITION_TABLES.items():
        path = os.path.join(BASE_DIR, "definitions", name)
        try:
            with open(path, encoding="utf8") as handle:
                found = duplicate_definitions(handle.read(), tables)
        except (OSError, SyntaxError, ValueError):
            continue        # a file that cannot be read or parsed is reported by the import
        errors += [f"definitions/{name}: {message}. Rename one of the two, or delete the one "
                   "that is not wanted" for message in found]
    return errors


def static_report():
    """Check the definitions and configs against each other, without reading any events."""
    report = {"errors": [], "warnings": [], "inventory": {}}
    try:
        _static_checks(report)
    except Exception as exc:
        # whatever shape the definitions and configs are in, the answer is a report
        reason = f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"
        report["errors"].append(
            f"the static checks could not be completed ({reason}): a config or definition "
            "is not laid out the way the scaffold wrote it\n" + traceback.format_exc(limit=4))
    return report


def _static_checks(report):
    errors, warnings = report["errors"], report["warnings"]
    errors += _duplicate_errors()
    try:
        from analysis_pkg.definitions import objects as object_defs
        from analysis_pkg.definitions import weights
        from analysis_pkg.definitions.cuts import evt_cut_defs, obj_cut_defs
        from analysis_pkg.definitions.hists import counter_defs, hist_defs
        from analysis_pkg.definitions.objects import derived_objs, primary_objs
        from analysis_pkg.tools import utilities
        from analysis_pkg.tools.processor import (
            AnalysisProcessor, list_channels, list_hist_collections)
    except Exception:
        errors.append("the analysis cannot be imported:\n" + traceback.format_exc(limit=6))
        return report

    # objects
    known = list(primary_objs) + list(derived_objs)
    for name in sorted(set(primary_objs) & set(derived_objs)):
        errors.append(f"object '{name}' is defined as both a primary and a derived object")
    for name in RESERVED_OBJECT_NAMES:
        if name in known:
            errors.append(f"object name '{name}' is reserved by the processor")
    for name, cuts in obj_cut_defs.items():
        if name not in known:
            warnings.append(f"definitions/cuts.py has object cuts for '{name}', which is not "
                            "an object in definitions/objects.py")
        if not isinstance(cuts, dict):
            errors.append(f"obj_cut_defs['{name}'] in definitions/cuts.py must be a dict of "
                          "cut name -> function")
    optional = list(getattr(object_defs, "optional_objs", ()))
    for name in optional:
        if name not in known:
            warnings.append(f"optional_objs lists '{name}', which is not an object in "
                            "definitions/objects.py")
    for name in ("generator_weight", "event_weight", "object_weight"):
        if not callable(getattr(weights, name, None)):
            errors.append(f"definitions/weights.py must define {name}()")

    # histograms
    for name, definition in hist_defs.items():
        axis_names = [axis.name for axis in getattr(definition, "axes", [])]
        if not axis_names:
            errors.append(f"histogram '{name}' has no axes")
        if len(set(axis_names)) != len(axis_names):
            errors.append(f"histogram '{name}' uses an axis name more than once: {axis_names}")
        for axis_name in axis_names:
            if axis_name in RESERVED_AXIS_NAMES or not axis_name:
                errors.append(f"histogram '{name}': axis name '{axis_name}' is reserved "
                              f"(avoid {list(RESERVED_AXIS_NAMES)} and give every axis a name)")

    # selections and histogram collections, validated exactly as the processor does
    try:
        channels = list_channels()
        collections = list_hist_collections()
    except Exception as exc:
        errors.append(f"the configs cannot be read ({utilities.brief(exc)})")
        return report
    resolved = {}
    if not channels:
        errors.append("configs/selections.yaml defines no channel")
    else:
        try:
            built = AnalysisProcessor(channels, collections)
            # what each channel applies once the yaml anchors and merges are resolved
            resolved = {channel: {"obj_cuts": {obj: list(names) for obj, names in cuts["obj"].items()},
                                  "evt_cuts": list(cuts["evt"])}
                        for channel, cuts in built.channel_cuts.items()}
        except Exception as exc:
            lines = str(exc).splitlines()
            if isinstance(exc, ValueError) and len(lines) > 1 and \
                    lines[0].startswith("the processor cannot be built"):
                errors.extend(line.strip()[2:] for line in lines[1:])   # one per problem
            else:
                errors.append(f"the processor cannot be built ({utilities.brief(exc)})")

    # what is defined but never used (worth knowing, not an error)
    selections = utilities.load_yaml(utilities.resolve_path("configs/selections.yaml", ""))
    used_obj_cuts, used_evt_cuts = set(), set()
    for channel in channels:
        cuts = selections.get(channel) or {}
        requested = cuts.get("obj_cuts") or {}
        if isinstance(requested, dict):      # anything else was reported above
            for obj, names in requested.items():
                used_obj_cuts.update((obj, cut) for cut in utilities.flatten(names))
        used_evt_cuts.update(utilities.flatten(cuts.get("evt_cuts") or []))
    hist_menu = utilities.load_yaml(utilities.resolve_path("configs/hist_collections.yaml", ""))
    used_hists = set()
    for collection in collections:
        used_hists.update(utilities.flatten(hist_menu.get(collection)))
    unused = {
        "obj_cuts": sorted(f"{obj}: {cut}" for obj, cuts in obj_cut_defs.items()
                           if isinstance(cuts, dict)
                           for cut in cuts if (obj, cut) not in used_obj_cuts),
        "evt_cuts": sorted(str(cut) for cut in evt_cut_defs if cut not in used_evt_cuts),
        "hists": sorted(str(name) for name in hist_defs if name not in used_hists),
    }

    # run periods (a file that cannot be read, or an entry of the wrong shape, was
    # reported when the processor was built)
    try:
        periods = utilities.load_yaml(utilities.resolve_path("configs/run_periods.yaml", ""))
    except Exception:
        periods = {}
    run_periods = {}
    for year, cfg in periods.items():
        cfg = cfg or {}
        if not isinstance(cfg, dict):
            continue
        run_periods[str(year)] = {"lumi": cfg.get("lumi"), "golden_json": cfg.get("golden_json")}
        if cfg.get("lumi") is None:
            warnings.append(f"run period '{year}' has no lumi: simulation for it will not be scaled")
        golden = cfg.get("golden_json")
        golden = str(golden) if golden else None
        if golden and not os.path.exists(
                golden if os.path.isabs(golden) else os.path.join(BASE_DIR, "data", golden)):
            errors.append(f"golden JSON '{golden}' of run period '{year}' is not in "
                          f"{os.path.join(BASE_DIR, 'data')}")

    # samples
    try:
        utilities.load_yaml(utilities.resolve_path("cross_sections.yaml", "configs"))
        cross_sections_readable = True
    except Exception as exc:
        cross_sections_readable = False
        errors.append(f"configs/cross_sections.yaml cannot be read ({utilities.brief(exc)})")
    samples = {}
    for path in sorted(glob.glob(os.path.join(BASE_DIR, "configs", "samples", "*.yaml"))):
        cfg_name = os.path.basename(path)
        try:
            locations = utilities.load_samples(path)
        except Exception as exc:
            errors.append(f"configs/samples/{cfg_name} cannot be read ({utilities.brief(exc)})")
            continue
        groups = {}
        for tag, block in locations.items():
            for sample in block["samples"]:
                groups.setdefault(sample, []).append(tag)
        for sample, tags in groups.items():
            if len(tags) > 1:
                warnings.append(f"sample '{sample}' is defined in several groups of {cfg_name} "
                                f"({tags}): it can only be used with its group named "
                                "(tag=... or --tag)")
        for tag, block in locations.items():
            for sample, cfg in block["samples"].items():
                is_data = utilities.as_bool(cfg.get("is_data", False))
                year = str(cfg.get("year", block.get("year", "")) or "")
                files = cfg.get("files") or []
                files = [files] if isinstance(files, str) else files
                if not isinstance(files, (list, tuple)):
                    errors.append(f"sample '{sample}' ({cfg_name}): 'files' must be a list of paths")
                    files = []
                samples[sample] = {"config": cfg_name, "tag": tag, "is_data": is_data,
                                   "year": year, "n_files": len(files)}
                if not files:
                    warnings.append(f"sample '{sample}' ({cfg_name}) lists no files")
                if year and year not in periods:
                    warnings.append(f"sample '{sample}' is in run period '{year}', which is "
                                    "not in configs/run_periods.yaml")
                if not is_data and cross_sections_readable:
                    try:
                        utilities.get_xs(sample)
                    except KeyError:
                        warnings.append(f"sample '{sample}' has no cross section in "
                                        "configs/cross_sections.yaml: it will not be scaled to "
                                        "lumi * xs")
                    except Exception as exc:
                        errors.append(f"the cross section of sample '{sample}' cannot be read "
                                      f"({utilities.brief(exc)})")

    report["inventory"] = {
        "primary_objects": list(primary_objs),
        "derived_objects": list(derived_objs),
        "optional_objects": optional,
        "channels": channels,
        "hist_collections": {name: len(utilities.flatten(hist_menu.get(name)))
                             for name in collections},
        "n_obj_cuts": {str(obj): len(cuts) for obj, cuts in obj_cut_defs.items()
                       if isinstance(cuts, dict)},
        "n_evt_cuts": len(evt_cut_defs),
        "n_hists": len(hist_defs),
        "counters": list(counter_defs),
        "unused": unused,
        "selections": resolved,
        "samples": samples,
        "run_periods": run_periods,
    }


def _error_chain(exc, limit=6):
    """One line per link of an exception chain, outermost first.

    coffea wraps whatever the processor raises, and depending on its version the
    wrapper's own message may or may not repeat the cause. Spelling the chain out puts
    the line that names the failing cut in front of the reader either way.
    """
    lines, seen = [], set()
    while exc is not None and id(exc) not in seen and len(lines) < limit:
        seen.add(id(exc))
        message = str(exc)
        if isinstance(exc, KeyError) and exc.args and isinstance(exc.args[0], str):
            message = exc.args[0]   # str(KeyError) wraps its message in quotes
        text = " ".join(message.split())
        if len(text) > 600:
            text = text[:597] + "..."
        line = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
        lines.append(line if not lines else "  caused by " + line)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return "\n".join(lines)


def _is_filled(histogram):
    """True if anything was filled into a hist.Hist (None when that cannot be told)."""
    import numpy as np
    try:
        view = histogram.view(flow=True)
        if view.dtype.names and "variance" in view.dtype.names:
            return bool(np.any(view["variance"] > 0))
        return bool(np.any(np.asarray(view) != 0))
    except Exception:
        return None


def _is_filled_in(histogram, channel):
    """_is_filled for one channel of a histogram (None when that cannot be told)."""
    try:
        return _is_filled(histogram[{"channel": channel}])
    except Exception:
        return None


def run_report(fileset, channels=None, collections=None, max_events=2000,
               treename=TREE_NAME, strict=True, chunksize=None, maxchunks=1):
    """Run the processor over a fileset and summarise the outcome.

    By default one chunk of about ``max_events`` events from the start of each dataset
    is processed (coffea splits a file into chunks of even size, so the chunk can be
    up to half as large again). Pass ``chunksize`` and ``maxchunks=None`` to process
    whole files in chunks of that size instead (small test files, where chunk
    boundaries matter).
    """
    report = {"ok": False, "error": None, "traceback": None, "datasets": {},
              "max_events": max_events}
    start = time.time()
    try:
        from coffea import processor
        from analysis_pkg.tools.processor import (
            AnalysisProcessor, list_channels, list_hist_collections)
        from analysis_pkg.tools.schema import AnalysisSchema

        channels = list(channels) if channels else list_channels()
        collections = list(collections) if collections is not None else list_hist_collections()
        report["channels"], report["hist_collections"] = channels, collections
        executor = processor.IterativeExecutor(status=False)
        if hasattr(executor, "retries"):
            executor.retries = 0     # recent coffea would run a failing chunk four times
        runner = processor.Runner(
            executor=executor,
            schema=AnalysisSchema,
            chunksize=chunksize or max_events,
            maxchunks=maxchunks,
            skipbadfiles=False,
            # coffea would otherwise reuse the sample metadata (is_data, year,
            # skim_factor) a file was first run with in this python session
            metadata_cache={},
        )
        proc = AnalysisProcessor(channels, collections, strict=strict)
        out = runner(fileset, processor_instance=proc, treename=treename)
        for dataset, output in out.items():
            hists = output["hists"]
            empty = sorted(name for name, h in hists.items() if _is_filled(h) is False)
            # filled somewhere, but with nothing in these channels
            partly = {}
            for name, h in sorted(hists.items()):
                if name in empty or len(channels) < 2:
                    continue
                without = [channel for channel in channels if _is_filled_in(h, channel) is False]
                if without:
                    partly[name] = without
            meta = output["metadata"]
            report["datasets"][dataset] = {
                "n_events": int(meta["n_evts"]),
                "is_data": sorted(meta["is_data"]),
                "year": sorted(meta["year"]),
                "scaled_sum_weights": float(meta["scaled_sum_weights"]),
                "lumixs_weight": meta.get("lumixs_weight"),
                "n_removed_golden_json": int(meta["n_removed_golden_json"]),
                "unavailable_objects": sorted(meta.get("unavailable_objects", ())),
                "cutflow": {
                    channel: [{"cut": cut, **vals} for cut, vals in cutflow.to_dict().items()]
                    for channel, cutflow in output["cutflow"].items()
                },
                "counters": output["counters"],
                "n_hists": len(hists),
                "empty_hists": empty,
                "empty_in_channels": partly,
                "warnings": sorted(output["warnings"]),
            }
        report["ok"] = True
    except Exception as exc:
        report["error"] = _error_chain(exc)
        report["traceback"] = traceback.format_exc(limit=12)
    report["seconds"] = round(time.time() - start, 2)
    return report


def build_fileset(args):
    """Fileset for the run part of the check, from --sample or --file."""
    from analysis_pkg.tools import utilities

    if args.sample:
        fileset = {}
        for sample in args.sample:
            utilities.make_fileset([sample], tag=args.tag, max_files=1,
                                   location_cfg=args.location_cfg, fileset=fileset)
        return fileset
    if args.file:
        path = args.file if "://" in args.file else os.path.abspath(args.file)
        metadata = {"skim_factor": 1.0, "year": args.year or ""}
        if args.data:
            metadata["is_data"] = True
        elif args.mc:
            metadata["is_data"] = False
        # with neither flag the processor decides from the presence of genWeight
        return {args.dataset or "check": {"files": [path], "metadata": metadata}}
    return None


def summarise(report, stream=None):
    """Print the report in a form meant for people."""
    stream = stream or sys.stdout
    static = report["static"]
    inventory = static.get("inventory") or {}
    print(f"static checks: {len(static['errors'])} error(s), "
          f"{len(static['warnings'])} warning(s)", file=stream)
    if inventory:
        print(f"  objects: {len(inventory['primary_objects'])} primary, "
              f"{len(inventory['derived_objects'])} derived | "
              f"channels: {len(inventory['channels'])} | "
              f"hist collections: {len(inventory['hist_collections'])} | "
              f"hists: {inventory['n_hists']}", file=stream)
    for message in static["errors"]:
        print(f"  ERROR   {message}", file=stream)
    for message in static["warnings"]:
        print(f"  warning {message}", file=stream)
    run = report.get("run")
    if run is None:
        return
    if not run["ok"]:
        print("run: FAILED", file=stream)
        print(run["error"], file=stream)
        if run.get("traceback"):
            print("\n" + run["traceback"], file=stream)
        return
    print(f"run: ok in {run['seconds']} s", file=stream)
    for dataset, info in run["datasets"].items():
        print(f"  {dataset}: {info['n_events']} events, {len(info['warnings'])} warning(s), "
              f"{len(info['empty_hists'])}/{info['n_hists']} histograms empty", file=stream)
        for name, without in (info.get("empty_in_channels") or {}).items():
            print(f"    histogram '{name}' has no entries in: {', '.join(without)}", file=stream)
        if info.get("unavailable_objects"):
            print(f"    objects not available: {', '.join(info['unavailable_objects'])}",
                  file=stream)
        for channel, rows in info["cutflow"].items():
            last = rows[-1]
            print(f"    [{channel}] {rows[0]['raw']} -> {last['raw']} events "
                  f"after {len(rows) - 1} event cut(s)", file=stream)
        for message in info["warnings"]:
            print(f"    warning {message}", file=stream)


def main(argv=None):
    # no abbreviations: the options are also passed by programs, and "--js" must not
    # be taken for "--json"
    parser = argparse.ArgumentParser(
        prog="python -m analysis_pkg.tools.check", allow_abbrev=False,
        description="Check that the analysis is self-consistent and, optionally, that it runs.")
    parser.add_argument("--sample", nargs="+", help="configured sample(s) to run on (first file of each)")
    parser.add_argument("--tag", help="group of the location config the samples belong to")
    parser.add_argument("--location-cfg", default="samples.yaml",
                        help="file under configs/samples/ (default: samples.yaml)")
    parser.add_argument("--file", help="run on this file instead of a configured sample")
    parser.add_argument("--dataset", help="dataset name to use with --file (default: check)")
    parser.add_argument("--data", action="store_true", help="--file is data")
    parser.add_argument("--mc", action="store_true", help="--file is simulation")
    parser.add_argument("--year", help="run period of --file")
    parser.add_argument("--channels", nargs="+", help="channels to run (default: all)")
    parser.add_argument("--hists", nargs="*", help="hist collections to fill (default: all)")
    parser.add_argument("--max-events", type=int, default=2000)
    parser.add_argument("--treename", default=TREE_NAME)
    parser.add_argument("--no-strict", action="store_true",
                        help="turn cut failures into warnings instead of stopping")
    parser.add_argument("--json", help="write the full report to this file ('-' for stdout)")
    args = parser.parse_args(argv)

    report = {"versions": _versions(), "static": static_report(), "run": None}
    if (args.sample or args.file) and not report["static"]["errors"]:
        try:
            fileset = build_fileset(args)
        except Exception as exc:
            report["run"] = {"ok": False, "error": _error_chain(exc),
                             "traceback": traceback.format_exc(limit=6),
                             "datasets": {}, "seconds": 0.0}
        else:
            report["run"] = run_report(fileset, args.channels, args.hists, args.max_events,
                                       args.treename, strict=not args.no_strict)
    elif args.sample or args.file:
        report["run"] = {"ok": False, "datasets": {}, "seconds": 0.0, "traceback": None,
                         "error": "not run: fix the static errors first"}
    report["ok"] = not report["static"]["errors"] and (report["run"] is None or report["run"]["ok"])

    text = json.dumps(_plain(report), indent=2, sort_keys=True, default=str)
    if args.json == "-":
        print(text)
    else:
        summarise(report)
        if args.json:
            with open(args.json, "w", encoding="utf8") as handle:
                handle.write(text + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
