#!/usr/bin/env python3
"""
# plot_results.py is a part of the HEPTAPOD package.
# Copyright (C) 2025 HEPTAPOD authors (see AUTHORS for details).
# HEPTAPOD is licensed under the GNU GPL v3 or later, see LICENSE for details.
# Please respect the MCnet Guidelines, see GUIDELINES for details.

Plot and archive the output of `lpc_scaleout.py`.

    plot     one result  -> PNGs + summary.json + histograms.root
    compare  N results   -> overlay PNGs + a side-by-side table

The archive is the point: `summary.json` and `histograms.root` are small, stable
and readable without this repo, so a run stays comparable months later. The
`hist` objects inside the pickle add, so runs also merge.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from typing import Any, Dict

import matplotlib
matplotlib.use("Agg")                      # headless: write files, never a window
import matplotlib.pyplot as plt
import numpy as np


def load(path: str) -> Dict[str, Any]:
    with open(path, "rb") as fh:
        blob = pickle.load(fh)
    return blob.get("accumulator", blob), blob.get("report", {})


def is_hist(o) -> bool:
    return hasattr(o, "axes") and hasattr(o, "view")


def hist_arrays(h):
    """(centers, values, errors) for a 1-D hist, else None."""
    if len(h.axes) != 1:
        return None
    v = np.asarray(h.view().value if hasattr(h.view(), "value") else h.view(), dtype=float)
    try:
        e = np.sqrt(np.asarray(h.view().variance, dtype=float))
    except Exception:                                            # noqa: BLE001
        e = np.sqrt(np.abs(v))
    return h.axes[0].centers, v, e


def summarise(h) -> Dict[str, Any]:
    got = hist_arrays(h)
    if got is None:
        return {"ndim": len(h.axes)}
    c, v, _ = got
    tot = float(v.sum())
    mean = float((c * v).sum() / tot) if tot else None
    var = float((v * (c - mean) ** 2).sum() / tot) if tot and mean is not None else None
    peak = float(c[int(v.argmax())]) if tot else None
    return {"entries": tot, "mean": mean,
            "std": float(np.sqrt(var)) if var and var > 0 else None,
            "peak_bin_center": peak,
            "edges": [float(h.axes[0].edges[0]), float(h.axes[0].edges[-1])],
            "nbins": int(len(c))}


def cmd_plot(args):
    acc, report = load(args.result)
    os.makedirs(args.outdir, exist_ok=True)
    summary: Dict[str, Any] = {"source": os.path.abspath(args.result),
                               "label": args.label or os.path.basename(args.result),
                               "report": {k: report.get(k) for k in ("n_events",)},
                               "scalars": {}, "histograms": {}, "plots": []}
    if report.get("files_failed"):
        summary["report"]["files_failed"] = report["files_failed"]

    hists = {k: v for k, v in acc.items() if is_hist(v)}
    for k, v in acc.items():
        if not is_hist(v):
            summary["scalars"][k] = v

    for name, h in sorted(hists.items()):
        summary["histograms"][name] = summarise(h)
        got = hist_arrays(h)
        if got is None:
            continue                                   # 2-D: archived, not drawn
        c, v, e = got
        fig, ax = plt.subplots(figsize=(7, 4.5))
        w = (h.axes[0].edges[1:] - h.axes[0].edges[:-1])
        ax.bar(c, v, width=w, alpha=.75, color="#337ab7", edgecolor="#174a73", linewidth=.5)
        ax.errorbar(c, v, yerr=e, fmt="none", ecolor="#174a73", elinewidth=.7, alpha=.6)
        ax.set_xlabel(getattr(h.axes[0], "label", None) or name)
        ax.set_ylabel("entries")
        ax.set_title(f"{summary['label']} — {name}")
        s = summary["histograms"][name]
        if s.get("mean") is not None:
            ax.axvline(s["mean"], ls="--", lw=1.4, color="#c0392b",
                       label=f"mean {s['mean']:.3g}")
            ax.legend()
        ax.grid(alpha=.3)
        fig.tight_layout()
        out = os.path.join(args.outdir, f"{name}.png")
        fig.savefig(out, dpi=140); plt.close(fig)
        summary["plots"].append(os.path.relpath(out, args.outdir))
        print(f"  wrote {out}")

    # a portable archive: readable without this repo
    try:
        import uproot
        rp = os.path.join(args.outdir, "histograms.root")
        with uproot.recreate(rp) as f:
            for name, h in hists.items():
                if len(h.axes) == 1:
                    f[name] = h
        print(f"  wrote {rp}")
        summary["root"] = "histograms.root"
    except Exception as e:                                       # noqa: BLE001
        print(f"  (no ROOT archive: {e})")

    sp = os.path.join(args.outdir, "summary.json")
    with open(sp, "w") as fh:
        json.dump(summary, fh, indent=1)
    print(f"  wrote {sp}")
    print(f"\n{summary['label']}: {len(summary['plots'])} plots, "
          f"{len(summary['histograms'])} histograms, {len(summary['scalars'])} scalars")
    for k, v in sorted(summary["scalars"].items()):
        print(f"    {k:24s} {v}")
    return 0


def cmd_compare(args):
    labels = args.labels or [os.path.basename(os.path.dirname(os.path.abspath(p)) or p)
                             for p in args.results]
    if len(labels) != len(args.results):
        sys.exit("--labels must match the number of results")
    loaded = [load(p)[0] for p in args.results]
    os.makedirs(args.outdir, exist_ok=True)

    names = sorted(set.intersection(*[{k for k, v in a.items() if is_hist(v)} for a in loaded]))
    if not names:
        sys.exit("the results share no histograms")
    table: Dict[str, Any] = {}
    for name in names:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        row = {}
        for lab, acc in zip(labels, loaded):
            got = hist_arrays(acc[name])
            if got is None:
                continue
            c, v, _ = got
            tot = v.sum()
            norm = v / tot if (args.normalise and tot) else v
            ax.step(c, norm, where="mid", lw=1.6, label=lab)
            row[lab] = summarise(acc[name])
        ax.set_xlabel(name); ax.set_ylabel("fraction" if args.normalise else "entries")
        ax.set_title(f"{name}"); ax.legend(); ax.grid(alpha=.3)
        fig.tight_layout()
        out = os.path.join(args.outdir, f"compare_{name}.png")
        fig.savefig(out, dpi=140); plt.close(fig)
        table[name] = row
        print(f"  wrote {out}")

    with open(os.path.join(args.outdir, "comparison.json"), "w") as fh:
        json.dump({"labels": labels, "sources": [os.path.abspath(p) for p in args.results],
                   "histograms": table}, fh, indent=1)
    print(f"\n{'histogram':22s} " + " ".join(f"{l:>14s}" for l in labels))
    for name, row in table.items():
        cells = []
        for l in labels:
            m = row.get(l, {}).get("mean")
            cells.append(f"{m:14.4g}" if m is not None else f"{'-':>14s}")
        print(f"  {name:20s} " + " ".join(cells) + "   (mean)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[5],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("plot"); a.add_argument("result")
    a.add_argument("--outdir", default="plots"); a.add_argument("--label")
    a.set_defaults(func=cmd_plot)
    b = sub.add_parser("compare"); b.add_argument("results", nargs="+")
    b.add_argument("--labels", nargs="+"); b.add_argument("--outdir", default="plots_compare")
    b.add_argument("--normalise", action="store_true", help="shape comparison")
    b.set_defaults(func=cmd_compare)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
