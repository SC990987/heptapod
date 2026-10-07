"""Event weights.

Three functions, called by the processor in this order:

1. ``generator_weight(evts)``   simulation only; its sum over the processed events is
   the denominator of the lumi * xs normalisation.
2. ``event_weight(evts, is_data)``   the weight each event enters every histogram and
   cutflow with, before normalisation. Event-level corrections belong here.
3. ``object_weight(objs, is_data)``   an extra per-event factor that depends on the
   *selected* objects of a channel (lepton or b-tag scale factors).

Keep corrections out of ``generator_weight``: a scale factor in the denominator would
be normalised away.

If ``generator_weight`` or ``event_weight`` fails the run stops, whatever the strict
setting: events cannot be counted without their weights. A failing ``object_weight``
stops a strict run and is skipped with a warning in a lenient one.
"""

import awkward as ak  # noqa: F401  (for the corrections you add)
import numpy as np


def generator_weight(evts):
    """Generator weight of each simulated event."""
    return evts.genWeight


def event_weight(evts, is_data):
    """Weight of each event before lumi * xs scaling (1 for data).

    Multiply event-level corrections in here, for example a pileup weight looked up
    from ``evts.Pileup.nTrueInt``.
    """
    if is_data:
        return np.ones(len(evts))
    return generator_weight(evts)


def object_weight(objs, is_data):
    """Extra per-event factor computed from the selected objects of one channel.

    Called once per channel, after the object cuts and before the event cuts, with the
    same ``objs`` the cuts see. Return an array with one value per event, or None for
    no correction (the default).
    """
    return None
