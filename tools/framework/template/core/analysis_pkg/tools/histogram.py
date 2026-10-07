"""Histogram and Axis: a hist.Hist bundled with the functions that fill it."""

import hist
import awkward as ak

from analysis_pkg.tools import utilities


class Axis:
    """A hist axis together with the function that produces its values.

    fill_func is called as ``fill_func(objs, mask)`` where ``objs`` are the selected
    objects of one channel and ``mask`` is the histogram's event mask (see Histogram).
    It returns one entry per event that passes the mask: a value, or a list of values
    (one per object, say). Missing values (None) are left out of the histogram.
    """

    def __init__(self, axis, fill_func):
        self.axis = axis
        self.name = self.axis.name
        self.fill_func = fill_func


class Histogram:
    """A histogram definition: its axes, how to fill them, and which events to use.

    Histogram exists so that a hist.Hist and its filling arguments are defined in one
    place. ``evt_mask`` optionally restricts the events used for every axis, for
    example to events with at least two muons for a dR(mu, mu) histogram; the fill
    functions receive the mask so they can apply it before indexing.

    With several axes, the entries are paired up event by event: a value per event
    on one axis goes with each of the values a list on another axis holds for that
    event, two lists must have the same lengths, and an entry that is missing on one
    axis is left out on all of them.

    The storage must accept weights. Keep the default, "weight": it records the
    variance of the weights, which the error bars and ratio helpers need.
    """

    def __init__(self, axes, storage="weight", evt_mask=None):
        self.axes = list(axes)
        self.storage = storage
        # every event passes if no mask is given
        self.evt_mask = (lambda objs: slice(None)) if evt_mask is None else evt_mask
        self.hist = None
        self.name = None

    @classmethod
    def simple_hist(cls, obj, attr, absval, nbins, xmin, xmax, label):
        """One-axis histogram of ``objs[obj].attr`` ("n" counts the objects per event)."""

        def fill(objs, mask):
            if attr == "n":
                return ak.num(objs[obj], axis=1)
            values = getattr(objs[obj], attr)
            return abs(values) if absval else values

        return cls([
            Axis(hist.axis.Regular(nbins, xmin, xmax, name=f"{obj}_{attr}", label=label), fill)
        ])

    def make_hist(self, name, channels=None):
        """Build the hist.Hist. Done at run time because the channels are only known then."""
        self.name = name
        if channels is not None:
            channel_axis = hist.axis.StrCategory(list(channels), name="channel")
            self.axes = [Axis(channel_axis, lambda objs, mask: objs["ch"])] + self.axes
        self.hist = hist.Hist(*[a.axis for a in self.axes], storage=self.storage)

    def fill(self, objs, evt_weights, warn=None, label=""):
        """Fill the hist.Hist for one channel; a histogram that cannot be filled is skipped."""
        warn = warn if warn is not None else utilities.WarningLog()
        label = f"[{label}] " if label else ""
        try:
            mask = self.evt_mask(objs)
            fill_args = {a.name: a.fill_func(objs, mask) for a in self.axes}
            names = [name for name in fill_args if name != "channel"]
            weights = evt_weights[mask]
            for name in names:
                values = fill_args[name]
                if hasattr(values, "__len__") and len(values) != len(weights):
                    # (no counts in the message: it would then differ from chunk to chunk
                    # and be recorded once per chunk instead of once)
                    raise ValueError(
                        f"axis '{name}' does not have one entry per event: a fill function "
                        "returns one entry (a value or a list) for each event that passes "
                        "the histogram's evt_mask")
            # Give the weight and every axis one common structure. A value per event is
            # repeated for each entry of a list on another axis (so a per-object
            # histogram gets one weight per object), and what is missing on one axis is
            # marked missing on all, so that flattening drops the same entries everywhere.
            columns = ak.broadcast_arrays(weights, *[fill_args[name] for name in names])
            columns = [ak.to_numpy(ak.flatten(column, axis=None)) for column in columns]
            fill_args["weight"] = columns[0]
            fill_args.update(zip(names, columns[1:]))
            self.hist.fill(**fill_args)
        except Exception as exc:
            if utilities.is_io_error(exc):
                raise
            missing = utilities.missing_object(exc)
            if missing is not None:
                warn(f"{label}histogram '{self.name}' not filled: "
                     f"'{missing}' is not available in this sample")
            else:
                warn(f"{label}histogram '{self.name}' could not be filled and was skipped "
                     f"({utilities.brief(exc)})")
