"""The NanoEvents schema used to read the input files.

AnalysisSchema is coffea's NanoAODSchema made tolerant of skims and private
productions:

- a missing cross-reference index (a dropped ``Muon_jetIdx``, say) is not reported on
  every file open: the corresponding attribute is simply unavailable;
- missing run / luminosityBlock / event branches do not stop the file from opening;
- when a collection stores its momentum twice, as (pt, eta, phi) and as (px, py, pz),
  only the polar set is read. coffea's vector behaviour accepts one representation
  per collection (recent versions refuse to build a collection that carries both)
  and px, py and pz stay available as computed attributes;
- index branches stored as floating-point numbers, as some private productions do,
  are read as integers (see _accept_any_index_type).

Collections the schema does not know keep their branches but get no vector behaviour.
Wrap them with ``utilities.as_lorentz`` in definitions/objects.py, or give them a
behaviour here by extending ``mixins``.
"""

import awkward as ak
import numpy as np
from coffea.nanoevents import NanoAODSchema, transforms


def _accept_any_index_type(local2global):
    """Wrap coffea's local2global so that it takes index branches of any numeric type.

    coffea turns the per-event index of a cross-reference into a global one and
    insists on the result being 64-bit integers, which it is not when the branch is
    stored as floats (-1.0, 0.0, 1.0, ...). The index is converted first, and coffea's
    own function does the rest.
    """
    def int_index_local2global(stack):
        target_offsets = stack.pop()
        index = stack.pop()
        stack.append(ak.values_astype(index, np.int64))
        stack.append(target_offsets)
        local2global(stack)

    int_index_local2global.original = local2global
    return int_index_local2global


# coffea looks its transforms up by name in that module each time one is needed. The
# replacement is installed once per process, wherever this module is first imported
# (dask workers import it when they receive the schema), and applies to every schema.
# Reloading this module leaves the one that is installed in place.
if not hasattr(transforms.local2global, "original"):
    transforms.local2global = _accept_any_index_type(transforms.local2global)


class AnalysisSchema(NanoAODSchema):
    """NanoAODSchema for skims and private NanoAOD-like productions."""

    warn_missing_crossrefs = False
    error_missing_event_ids = False

    # Collection -> behaviour. Start from coffea's table; add custom collections here,
    # e.g. ``"MyTrack": "PtEtaPhiMCollection"`` (needs pt, eta, phi and mass branches).
    mixins = {
        **NanoAODSchema.mixins,
    }

    # Branches that are never read, by full name (``"Jet_someBrokenBranch"``).
    hidden_branches = ()

    # Hide px/py/pz of collections that also store pt/eta/phi (see the module docstring).
    hide_duplicate_momenta = True

    def _build_collections(self, field_names, input_contents):
        hidden = set(self.hidden_branches)
        names = set(field_names)
        if self.hide_duplicate_momenta:
            for name in field_names:
                collection, _, field = name.partition("_")
                # only collections that get a vector behaviour are affected: for the
                # others the branches are plain columns and nothing would recompute them
                if field != "pt" or collection not in self.mixins:
                    continue
                if f"{collection}_phi" in names:
                    hidden.update((f"{collection}_px", f"{collection}_py"))
                if f"{collection}_eta" in names:
                    hidden.add(f"{collection}_pz")
        hidden &= names
        if hidden:
            kept = [(n, c) for n, c in zip(field_names, input_contents) if n not in hidden]
            field_names = [n for n, _ in kept]
            input_contents = [c for _, c in kept]
        return super()._build_collections(field_names, input_contents)
