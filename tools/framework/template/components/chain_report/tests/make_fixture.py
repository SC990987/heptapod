"""Cut a small test file out of a real one and register it for the chain report.

    python tests/make_fixture.py /path/to/events.root --dataset MySignal --year 2018 \\
        --events 200 --skim-factor 0.5
    python tests/make_fixture.py /path/to/data.root --dataset MyData --data --year 2018 \\
        --first-event 11649 --events 200

Writes ``tests/data/<dataset>_<N>ev.root`` with the chosen slice of the events tree and
adds it to tests/fixtures.yaml. Keep fixtures small (a few hundred events): they are
committed to the repository and run on every pull request.

Choosing the slice matters more than its size:

- for simulation, any slice in which the main selections keep a few events will do;
- for data, pick a slice that crosses a boundary of the golden JSON, so that one chunk
  is emptied by it. Code that only breaks on empty chunks is otherwise never tested.

Give simulation a ``--skim-factor`` different from 1 (0.5, say): the normalisation
divides by it, and a mistake there is invisible at exactly 1. Give every fixture its
``--year``: simulation is only scaled, and data only finds its golden JSON, with one.

The file is rewritten with uproot: jagged branches are grouped per collection so that
the usual ``nMuon`` counters come out. Branches uproot cannot read or regroup are left
out and listed.
"""

import argparse
import os
import sys

import awkward as ak
import uproot
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from analysis_pkg import TREE_NAME  # noqa: E402

FIXTURES_CFG = os.path.join(HERE, "fixtures.yaml")


def collection_layout(branch_names):
    """Group branch names by the counters among them: ``{collection: [fields]}``.

    A collection is a counter branch ``n<Name>`` with branches ``<Name>_*`` next to
    it (or a single branch ``<Name>``, for which the list of fields is empty). The
    name may itself contain underscores: CMS data has ``nProton_multiRP`` counting
    ``Proton_multiRP_*``. Longer names are matched first, so that each branch goes to
    the most specific counter. Returns the collections and the set of their counters.
    """
    names = set(branch_names)
    jagged, claimed = {}, set()
    candidates = sorted((n for n in names if n.startswith("n") and len(n) > 1),
                        key=lambda n: (-len(n), n))
    for counter in candidates:
        coll = counter[1:]
        fields = sorted(n[len(coll) + 1:] for n in names - claimed if n.startswith(coll + "_"))
        if fields or coll in names:
            jagged[coll] = fields
            claimed.update(f"{coll}_{field}" for field in fields)
    return jagged, {"n" + coll for coll in jagged}


def slice_tree(source, tree_name, first, count):
    """Read ``count`` events starting at ``first`` and regroup them for writing.

    Returns ``(output, skipped, n_events)``: what to write as ``{branch: array}``, the
    branches that were left out, and the number of events read.
    """
    with uproot.open(source) as handle:
        tree = handle[tree_name]
        stop = min(first + count, tree.num_entries)
        if first < 0 or stop <= first:
            sys.exit(f"nothing to cut: the tree has {tree.num_entries} events and the slice "
                     f"starts at {first}")
        branches, skipped = [], []
        for branch in tree.branches:
            # a branch uproot cannot interpret carries the error as its interpretation
            unreadable = isinstance(branch.interpretation, Exception)
            (skipped if unreadable else branches).append(branch.name)
        try:
            arrays = tree.arrays(branches, entry_start=first, entry_stop=stop, library="ak")
        except Exception as first_error:            # noqa: BLE001
            # a damaged basket in some branch: read the branches one at a time over the
            # same events, and leave out those whose read fails
            arrays, readable = {}, []
            for name in branches:
                try:
                    arrays[name] = tree[name].array(entry_start=first, entry_stop=stop, library="ak")
                    readable.append(name)
                except Exception as error:          # noqa: BLE001
                    skipped.append(f"{name} ({type(error).__name__})")
            if not readable:
                sys.exit(f"no branch of {source} could be read: "
                         f"{type(first_error).__name__}: {first_error}")
            branches = readable

    jagged, _ = collection_layout(branches)
    output, written = {}, set()
    for coll, fields in jagged.items():
        counts = ak.to_numpy(arrays["n" + coll])

        def follows(array):
            """Is this a list per event, as long as the collection's counter says?"""
            return array.ndim == 2 and bool((ak.to_numpy(ak.num(array, axis=1)) == counts).all())

        if fields:
            # only lists that follow the collection's own counter can share it
            columns = {field: arrays[f"{coll}_{field}"] for field in fields
                       if follows(arrays[f"{coll}_{field}"])}
            if columns:
                output[coll] = ak.zip(columns)      # written as n<coll> and <coll>_<field>
                written.add("n" + coll)
                written.update(f"{coll}_{field}" for field in columns)
        elif follows(arrays[coll]):
            output[coll] = arrays[coll]             # one list per event: nPSWeight + PSWeight
            written.update(("n" + coll, coll))
    # everything else is written as it is if it holds one value per event (including
    # a counter none of whose branches could be regrouped), and left out otherwise
    for name in branches:
        if name in written:
            continue
        if arrays[name].ndim == 1:
            output[name] = arrays[name]
        else:
            skipped.append(name)
    return output, skipped, stop - first


def main(argv=None):
    parser = argparse.ArgumentParser(description="Make a small test file for the chain report.")
    parser.add_argument("source", help="ROOT file to cut the slice from (local path or root:// url)")
    parser.add_argument("--dataset", required=True, help="dataset name the fixture is run as")
    parser.add_argument("--events", type=int, default=200, help="number of events to keep")
    parser.add_argument("--first-event", type=int, default=0, help="first event of the slice")
    parser.add_argument("--tree", default=TREE_NAME,
                        help=f"tree to read in the source file (default: {TREE_NAME})")
    parser.add_argument("--data", action="store_true", help="the file is data")
    parser.add_argument("--year", default="", help="run period (must exist in configs/run_periods.yaml)")
    parser.add_argument("--skim-factor", type=float, default=None)
    args = parser.parse_args(argv)
    if args.events < 2:
        sys.exit("--events must be at least 2, so that the fixture can be read in two chunks")

    output, skipped, n_events = slice_tree(args.source, args.tree, args.first_event, args.events)
    relative = os.path.join("data", f"{args.dataset}_{n_events}ev.root")
    target = os.path.join(HERE, relative)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with uproot.recreate(target) as handle:
        # mktree, not handle[name] = output: since uproot 5.7 that writes an RNTuple.
        # The fixture is written under the name the analysis reads its trees by.
        types = {name: array.type for name, array in output.items()}
        handle.mktree(TREE_NAME, types).extend(output)
    size_kb = os.path.getsize(target) / 1024

    cfg, header = {}, []
    if os.path.exists(FIXTURES_CFG):
        with open(FIXTURES_CFG, encoding="utf8") as handle:
            text = handle.read()
        cfg = yaml.safe_load(text) or {}
        for line in text.splitlines():      # the comment block at the top is kept
            if not line.startswith("#"):
                break
            header.append(line)
    fixtures = [f for f in (cfg.get("fixtures") or []) if f.get("dataset") != args.dataset]
    entry = {"dataset": args.dataset, "file": relative, "is_data": bool(args.data)}
    if args.year:
        entry["year"] = str(args.year)
    if args.skim_factor is not None:
        entry["skim_factor"] = args.skim_factor
    fixtures.append(entry)
    cfg["fixtures"] = fixtures
    # every fixture must be read in at least two chunks, or merging is never exercised
    before = int(cfg.get("chunksize") or 100)
    cfg["chunksize"] = max(1, min(before, n_events // 2))
    with open(FIXTURES_CFG, "w", encoding="utf8") as handle:
        if header:
            handle.write("\n".join(header) + "\n")
        yaml.safe_dump(cfg, handle, sort_keys=False, default_flow_style=False)

    print(f"wrote {target} ({n_events} events, {size_kb:.0f} kB) and registered it as '{args.dataset}'")
    if cfg["chunksize"] != before:
        print(f"chunksize in tests/fixtures.yaml lowered from {before} to {cfg['chunksize']}, "
              "so that this fixture is read in two chunks")
    if not args.year:
        print("no --year was given: simulation will not be scaled and data will keep every "
              "luminosity section")
    if skipped:
        print(f"left out {len(skipped)} branch(es) that could not be read or regrouped: "
              f"{skipped[:10]}" + (" ..." if len(skipped) > 10 else ""))
    print("check it with: python tests/chain_report.py compute state.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
