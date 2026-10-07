"""Selection (event-level cuts) and JaggedSelection (object-level cuts).

All available cuts are defined in analysis_pkg.definitions.cuts. The cuts that make up a
selection are listed by name in configs/selections.yaml and handed to these classes
as lists of strings.

When a cut cannot be applied there are two cases, and they are treated differently:

- the cut needs an object this sample does not have (a generator-level cut on data):
  the cut is skipped with a warning, because it does not apply there;
- anything else (a typo, a missing branch, a mask of the wrong shape): with
  ``strict=True`` the run stops with the cut and channel named; with ``strict=False``
  the cut is skipped with a warning.
"""

import awkward as ak
import numpy as np

from analysis_pkg.definitions.cuts import evt_cut_defs, obj_cut_defs
from analysis_pkg.tools import utilities
from analysis_pkg.tools.cutflow import Cutflow


class Selection:
    """Event-level cuts: each one rejects whole events, and the cutflow records it.

    Cuts are applied one after another, so each cut sees only the events (and the
    slimmed collections) that survived the cuts before it.
    """

    def __init__(self, cuts, verbose=False, strict=True, warn=None, label=""):
        self.evt_cuts = list(cuts)  # names of the cuts to apply, in order
        self.cutflow = Cutflow()    # filled when the cuts are applied
        self.verbose = verbose
        self.strict = strict
        self.warn = warn if warn is not None else utilities.WarningLog()
        self.label = f"[{label}] " if label else ""

    def apply_evt_cuts(self, objs):
        """Apply every event cut to every collection and return the surviving objects."""
        sel_objs = objs.copy()
        weights = sel_objs["evt_weights"]
        self.cutflow.add_row("None", len(weights), ak.sum(weights))
        for cut in self.evt_cuts:
            if self.verbose:
                print(f"{self.label}applying event cut: {cut}")
            n_events = len(sel_objs["evt_weights"])
            try:
                mask = utilities.event_mask(
                    evt_cut_defs[cut](sel_objs), n_events, what=f"event cut '{cut}'"
                )
            except Exception as exc:
                if utilities.is_io_error(exc):
                    raise
                missing = utilities.missing_object(exc)
                if missing is not None:
                    self.warn(f"{self.label}event cut '{cut}' not applied: "
                              f"'{missing}' is not available in this sample")
                elif self.strict:
                    raise RuntimeError(
                        f"{self.label}event cut '{cut}' could not be evaluated "
                        f"({utilities.brief(exc)}). Fix it in definitions/cuts.py, or build "
                        "the processor with strict=False to skip cuts that fail."
                    ) from utilities.cause(exc)
                else:
                    self.warn(f"{self.label}event cut '{cut}' could not be evaluated "
                              f"and was skipped ({utilities.brief(exc)})")
                weights = sel_objs["evt_weights"]
                self.cutflow.add_row(cut, len(weights), ak.sum(weights), applied=False)
                continue

            for name, obj in sel_objs.items():
                if isinstance(obj, (ak.Array, np.ndarray)) and len(obj) == n_events:
                    sel_objs[name] = obj[mask]
                elif isinstance(obj, (ak.Array, np.ndarray)):
                    message = (f"{self.label}'{name}' does not hold one entry per event and "
                               "could not be cut along with the events")
                    if self.strict:
                        raise RuntimeError(message + ". Objects must hold one entry per event.")
                    self.warn(message)
            weights = sel_objs["evt_weights"]
            self.cutflow.add_row(cut, len(weights), ak.sum(weights))
        return sel_objs


class JaggedSelection:
    """Object-level cuts: they slim collections and never reject events.

    ``cuts`` maps an object name to the list of its cut names. Each cut is a function
    ``(objs, obj) -> mask`` with one True/False per object.
    """

    def __init__(self, cuts, verbose=False, strict=True, warn=None, label=""):
        self.obj_cuts = cuts
        self.verbose = verbose
        self.strict = strict
        self.warn = warn if warn is not None else utilities.WarningLog()
        self.label = f"[{label}] " if label else ""

    def apply_obj_cuts(self, objs, names=None):
        """Apply the object cuts of the listed objects (default: all that have cuts)."""
        sel_objs = objs.copy()
        for obj in (list(self.obj_cuts) if names is None else names):
            cuts = self.obj_cuts.get(obj)
            if not cuts:
                continue
            if obj not in sel_objs:
                self.warn(f"{self.label}'{obj}' is not available in this sample: "
                          f"object cuts {list(cuts)} not applied")
                continue
            for cut in cuts:
                if self.verbose:
                    print(f"{self.label}applying {obj} cut: {cut}")
                try:
                    collection = sel_objs[obj]
                    if getattr(collection, "ndim", 1) < 2:
                        raise ValueError(
                            f"'{obj}' holds one entry per event, so it cannot take object "
                            "cuts; use an event cut instead"
                        )
                    # an object the cut has no answer for (None) does not pass
                    mask = ak.fill_none(obj_cut_defs[obj][cut](sel_objs, collection), False)
                    if mask.ndim != collection.ndim or len(mask) != len(collection):
                        raise ValueError(
                            "an object cut must return one True/False per object, with the "
                            f"same nesting as the collection (got {mask.ndim} dimensions "
                            f"for a collection with {collection.ndim})"
                        )
                    if ak.any(ak.is_none(mask, axis=0)):
                        # No answer for whole events: the cut compared with something
                        # those events do not have (their leading jet, say). None of
                        # their objects pass, and the event keeps an empty list rather
                        # than a missing one, which later cuts would count wrongly.
                        if collection.ndim != 2:
                            raise ValueError("the cut gave no answer (None) for whole events")
                        positions = ak.local_index(collection, axis=1)[mask]
                        sel_objs[obj] = collection[ak.fill_none(positions, [], axis=0)]
                    else:
                        sel_objs[obj] = collection[mask]
                except Exception as exc:
                    if utilities.is_io_error(exc):
                        raise
                    missing = utilities.missing_object(exc)
                    if missing is not None:
                        self.warn(f"{self.label}{obj} cut '{cut}' not applied: "
                                  f"'{missing}' is not available in this sample")
                    elif self.strict:
                        raise RuntimeError(
                            f"{self.label}{obj} cut '{cut}' could not be applied "
                            f"({utilities.brief(exc)}). Fix it in definitions/cuts.py, or "
                            "build the processor with strict=False to skip cuts that fail."
                        ) from utilities.cause(exc)
                    else:
                        self.warn(f"{self.label}{obj} cut '{cut}' could not be applied "
                                  f"and was skipped ({utilities.brief(exc)})")
        return sel_objs
