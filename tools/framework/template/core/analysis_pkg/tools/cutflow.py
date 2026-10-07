"""The Cutflow accumulator: events surviving each event-level cut, raw and weighted."""

from coffea import processor


class Cutflow(processor.AccumulatorABC):
    """Number of events left after each event-level cut of one channel.

    Rows are kept in the order the cuts are applied; the first row, "None", is the
    count before any event cut. Each row holds

    - ``raw``: number of events,
    - ``weighted``: sum of their event weights (scaled to lumi * xs in postprocess),
    - ``not_applied``: number of chunks in which the cut could not be applied (for
      example a generator-level cut on data). When this is not zero the row repeats
      the previous counts for those chunks, and the table flags it.

    Object-level cuts do not appear here: they slim collections and never drop events.
    """

    def __init__(self):
        self.rows = {}

    def identity(self):
        """Empty cutflow (the additive identity coffea uses when merging chunks)."""
        return Cutflow()

    def add(self, other):
        """Add another cutflow to this one in place."""
        for cut, vals in other.rows.items():
            if cut in self.rows:
                for key in ("raw", "weighted", "not_applied"):
                    self.rows[cut][key] += vals[key]
            else:
                self.rows[cut] = dict(vals)

    def add_row(self, cut, raw, weighted, applied=True):
        """Append the counts after one more cut."""
        if cut in self.rows:
            raise RuntimeError(f"'{cut}' is already in the cutflow and cannot be added twice")
        self.rows[cut] = {
            "raw": int(raw),
            "weighted": float(weighted),
            "not_applied": 0 if applied else 1,
        }

    def scale(self, weight):
        """Apply an overall factor to the weighted counts."""
        for vals in self.rows.values():
            vals["weighted"] *= weight

    def cuts(self):
        """Cut names in the order they were applied."""
        return list(self.rows)

    def yields(self, weighted=True):
        """Counts after each cut, in order."""
        key = "weighted" if weighted else "raw"
        return [vals[key] for vals in self.rows.values()]

    def efficiency(self, weighted=True):
        """Fraction of the initial events left after the last cut."""
        counts = self.yields(weighted)
        if not counts or not counts[0]:
            return float("nan")
        return counts[-1] / counts[0]

    def to_dict(self):
        """Plain-python copy of the rows, for json or yaml."""
        return {cut: dict(vals) for cut, vals in self.rows.items()}

    def table(self):
        """The cutflow as a list of text lines."""
        counts = self.yields(weighted=True)
        total = counts[0] if counts else 0.0
        header = ("cut name", "raw N", "weighted N", "weighted %")
        body = []
        for cut, vals in self.rows.items():
            name = cut
            if vals["not_applied"]:
                name += f"  [not applied in {vals['not_applied']} chunk(s)]"
            pct = 100.0 * vals["weighted"] / total if total else float("nan")
            body.append((name, str(vals["raw"]), f"{vals['weighted']:.1f}", f"{pct:.1f}"))
        widths = [max(len(row[i]) for row in [header] + body) for i in range(len(header))]

        def fmt(row):
            cells = [row[0].ljust(widths[0])] + [row[i].rjust(widths[i]) for i in range(1, len(row))]
            return "  ".join(cells)

        return [fmt(header), "  ".join("-" * w for w in widths)] + [fmt(row) for row in body]

    def print_table(self):
        """Print the cutflow."""
        print("\n".join(self.table()))

    def __repr__(self):
        return f"<Cutflow: {len(self.rows)} rows>"
