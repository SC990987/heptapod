"""Run the analysis from the command line and save the output.

    python -m analysis_pkg.scripts.run_analysis \\
        --samples SampleA SampleB --channels baseline --hists base -o output.coffea

The same thing the test notebook does, without a notebook: build the fileset, run the
processor, print the cutflows, and write ``output.coffea`` with its ``.meta.yaml``
sidecar. Read the result back with ``analysis_pkg.tools.utilities.load_output``.

Each run normalises simulation with the events it processed. When a sample is split
over several runs, combine their outputs with ``analysis_pkg.scripts.merge_outputs``
rather than adding them by hand.
"""

import argparse
import sys
import time

from coffea import processor

from analysis_pkg import TREE_NAME
from analysis_pkg.tools import metadata, utilities
from analysis_pkg.tools.processor import AnalysisProcessor
from analysis_pkg.tools.schema import AnalysisSchema


def make_executor(name, workers=4, scheduler=None):
    """Build a coffea executor: 'iterative', 'futures' (local processes) or 'dask'."""
    if name == "iterative":
        return processor.IterativeExecutor()
    if name == "futures":
        return processor.FuturesExecutor(workers=workers)
    if name == "dask":
        from dask.distributed import Client
        if scheduler:
            client = Client(scheduler)
        else:
            client = Client(n_workers=workers, threads_per_worker=1)
        return processor.DaskExecutor(client=client)
    raise ValueError(f"unknown executor '{name}'")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m analysis_pkg.scripts.run_analysis",
        description="Run the analysis processor over configured samples.")
    parser.add_argument("--samples", nargs="+", required=True, help="sample names")
    parser.add_argument("--tag", help="group of the location config the samples belong to")
    parser.add_argument("--location-cfg", default="samples.yaml",
                        help="file under configs/samples/ (default: samples.yaml)")
    parser.add_argument("--channels", nargs="+", required=True,
                        help="selections to run (configs/selections.yaml)")
    parser.add_argument("--hists", nargs="*", default=[],
                        help="histogram collections to fill (configs/hist_collections.yaml)")
    parser.add_argument("--max-files", type=int, default=-1, help="files per sample (-1: all)")
    parser.add_argument("--chunksize", type=int, default=100000)
    parser.add_argument("--maxchunks", type=int, default=None, help="chunks per sample (default: all)")
    parser.add_argument("--executor", choices=("iterative", "futures", "dask"), default="iterative")
    parser.add_argument("--workers", type=int, default=4, help="processes for futures / local dask")
    parser.add_argument("--scheduler", help="address of an existing dask scheduler")
    parser.add_argument("--treename", default=TREE_NAME)
    parser.add_argument("--skipbadfiles", action="store_true",
                        help="carry on past unreadable input files (what was skipped is not "
                             "recorded: compare the event counts with what the samples hold)")
    parser.add_argument("--unweighted-hist", action="store_true")
    parser.add_argument("--no-strict", action="store_true",
                        help="turn cut failures into warnings instead of stopping")
    parser.add_argument("-o", "--output", default="output.coffea")
    args = parser.parse_args(argv)

    fileset = utilities.make_fileset(args.samples, tag=args.tag, max_files=args.max_files,
                                     location_cfg=args.location_cfg)
    proc = AnalysisProcessor(args.channels, args.hists, unweighted_hist=args.unweighted_hist,
                             strict=not args.no_strict)
    runner = processor.Runner(
        executor=make_executor(args.executor, args.workers, args.scheduler),
        schema=AnalysisSchema,
        chunksize=args.chunksize,
        maxchunks=args.maxchunks,
        skipbadfiles=args.skipbadfiles,
        metadata_cache={},   # never the sample metadata of an earlier run in this session
    )

    start = time.time()
    out = runner(fileset, processor_instance=proc, treename=args.treename)
    print(f"processed {len(fileset)} sample(s) in {time.time() - start:.1f} s")

    for sample in out:
        for channel in args.channels:
            print(f"\n{sample} / {channel}")
            out[sample]["cutflow"][channel].print_table()
        for message in sorted(out[sample]["warnings"]):
            print(f"Warning ({sample}): {message}")

    utilities.save_output(out, args.output)
    sidecar = metadata.write_run_metadata(
        args.output, fileset=fileset, channels=args.channels, hist_collections=args.hists,
        schema=AnalysisSchema, chunksize=args.chunksize, unweighted_hist=args.unweighted_hist,
        extra={"executor": args.executor},
    )
    print(f"\nwrote {args.output} and {sidecar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
