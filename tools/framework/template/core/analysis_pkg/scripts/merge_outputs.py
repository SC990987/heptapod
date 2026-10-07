"""Merge the outputs of separate runs into one, normalised as a whole.

    python -m analysis_pkg.scripts.merge_outputs job_*.coffea -o merged.coffea

Needed whenever one sample was processed in more than one run, for example in batch
jobs that each take a slice of its files: every run scales its slice to lumi * xs on
its own, so the pieces cannot simply be added. See
``analysis_pkg.tools.utilities.merge_outputs`` for what is done instead.
"""

import argparse
import sys

from analysis_pkg.tools import metadata, utilities


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m analysis_pkg.scripts.merge_outputs",
        description="Merge .coffea outputs of separate runs and normalise the total.")
    parser.add_argument("inputs", nargs="+", help=".coffea files to merge")
    parser.add_argument("-o", "--output", required=True, help="merged .coffea file to write")
    parser.add_argument("--no-scale", action="store_true",
                        help="leave the merged simulation unscaled (sums of event weights)")
    args = parser.parse_args(argv)

    out = utilities.merge_outputs(args.inputs, scale=not args.no_scale)
    for sample, result in out.items():
        meta = result["metadata"]
        scaled = f"lumi * xs weight {meta['lumixs_weight']:.6g}" if "lumixs_weight" in meta \
            else "not scaled"
        print(f"{sample}: {meta['n_evts']} events, {scaled}")
        for message in sorted(result["warnings"]):
            print(f"Warning ({sample}): {message}")

    utilities.save_output(out, args.output)
    sidecar = metadata.write_merge_metadata(args.output, args.inputs,
                                            extra={"scaled": not args.no_scale})
    print(f"\nwrote {args.output} and {sidecar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
