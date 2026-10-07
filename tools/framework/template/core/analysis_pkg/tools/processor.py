"""AnalysisProcessor: apply selections, fill histograms and cutflows.

For every chunk of events the processor

1. keeps, for data, only the luminosity sections certified in the golden JSON;
2. builds the primary objects (``primary_objs`` in definitions/objects.py) and orders
   the jagged ones by pT. An object listed in ``optional_objs`` may be missing from a
   sample; any other object that cannot be built stops a strict run;
3. for each channel (a selection in configs/selections.yaml):
   a. applies the object cuts to the primary objects, one object after another in the
      order they are defined. A cut therefore sees the objects defined before its
      own after their cuts, the ones defined after it before theirs, and no derived
      object yet;
   b. builds the derived objects in the order they are defined, applying the object
      cuts of each one as soon as it is built (and ordering it by pT if it is a
      jagged collection with a pt),
   c. applies the event cuts one after another and records the cutflow,
   d. fills the histograms and counters with the events that are left;
4. returns ``{dataset: {"cutflow", "hists", "counters", "metadata", "warnings"}}``.

Once all chunks are merged, ``postprocess`` scales simulation to lumi * cross section.
"""

import copy
import os

import awkward as ak
import numpy as np
from coffea import processor
from coffea.lumi_tools import LumiMask

from analysis_pkg import BASE_DIR
from analysis_pkg.definitions import objects as object_defs
from analysis_pkg.definitions import weights as weight_defs
from analysis_pkg.definitions.cuts import evt_cut_defs, obj_cut_defs
from analysis_pkg.definitions.hists import counter_defs, hist_defs
from analysis_pkg.definitions.objects import derived_objs, primary_objs
from analysis_pkg.tools import selection, utilities
from analysis_pkg.tools.cutflow import Cutflow

SELECTION_KEYS = ("obj_cuts", "evt_cuts", "description")


def _as_list(names):
    if names is None:
        return []
    return [names] if isinstance(names, str) else list(names)


def _as_array(values):
    return values if isinstance(values, ak.Array) else ak.Array(values)


def list_channels(selections_cfg="configs/selections.yaml"):
    """Names of the selections that can be run as channels.

    Top-level entries whose name starts with an underscore are fragments that exist
    only to be reused through yaml anchors, and are left out.
    """
    menu = utilities.load_yaml(utilities.resolve_path(selections_cfg, ""))
    return [name for name, cuts in menu.items()
            if isinstance(cuts, dict) and not str(name).startswith("_")]


def list_hist_collections(histograms_cfg="configs/hist_collections.yaml"):
    """Names of the histogram collections (entries starting with an underscore excluded)."""
    menu = utilities.load_yaml(utilities.resolve_path(histograms_cfg, ""))
    return [name for name in menu if not str(name).startswith("_")]


class AnalysisProcessor(processor.ProcessorABC):
    """Apply selections, fill histograms and make cutflows.

    channel_names: selections to run, as named in configs/selections.yaml
    hist_collection_names: histogram collections to fill, as named in
        configs/hist_collections.yaml
    selections_cfg, histograms_cfg, run_periods_cfg: config files, relative to the
        package directory (or absolute). The run periods give the golden JSON applied
        to data and the luminosity simulation is scaled to
    unweighted_hist: fill histograms with weight 1 and skip their lumi * xs scaling
    verbose: print each cut as it is applied
    strict: what happens when something in the definitions fails.
        True (default): the run stops, naming the culprit, when an object cannot be
        built (unless it is listed in ``optional_objs`` and what it reads is simply
        not in the sample), or when a cut or ``object_weight`` fails for any reason
        other than an optional object being absent.
        False: those failures become warnings instead. The object counts as absent
        and the cut is skipped, its cutflow row marked as not applied.
        In both modes, whatever needs an absent optional object is skipped with a
        warning, a histogram or counter that cannot be filled is skipped with a
        warning, and a failing ``generator_weight`` or ``event_weight`` stops the run.

    The names are resolved when the processor is built, so a misspelt channel, cut or
    histogram fails immediately rather than after the files have been opened.
    """

    def __init__(
        self,
        channel_names,
        hist_collection_names=(),
        selections_cfg="configs/selections.yaml",
        histograms_cfg="configs/hist_collections.yaml",
        run_periods_cfg="configs/run_periods.yaml",
        unweighted_hist=False,
        verbose=False,
        strict=True,
    ):
        self.channel_names = _as_list(channel_names)
        self.hist_collection_names = _as_list(hist_collection_names)
        self.selections_cfg = selections_cfg
        self.histograms_cfg = histograms_cfg
        self.run_periods_cfg = run_periods_cfg
        self.unweighted_hist = bool(unweighted_hist)
        self.verbose = verbose
        self.strict = strict
        self.known_objects = frozenset(primary_objs) | frozenset(derived_objs)
        # objects a sample is allowed not to have (generator-level objects on data, ...)
        self.optional_objects = frozenset(getattr(object_defs, "optional_objs", ()))

        problems = []
        if not self.channel_names:
            problems.append("no channel was requested")
        self.channel_cuts = self.build_cuts(problems)
        self.hist_names = self.build_hist_names(problems)
        self.run_periods = self.build_run_periods(problems)
        if problems:
            raise ValueError("the processor cannot be built:\n  - " + "\n  - ".join(problems))

    # ------------------------------------------------------------------ #
    # menus
    # ------------------------------------------------------------------ #

    def build_run_periods(self, problems=None):
        """Read the run periods into ``{period: {"lumi": ..., "golden_json": ...}}``."""
        problems = [] if problems is None else problems
        try:
            periods = utilities.load_yaml(utilities.resolve_path(self.run_periods_cfg, ""))
        except Exception as exc:
            problems.append(f"{self.run_periods_cfg} cannot be read ({utilities.brief(exc)})")
            return {}
        run_periods = {}
        for year, cfg in periods.items():
            if cfg is not None and not isinstance(cfg, dict):
                problems.append(f"run period '{year}' in {self.run_periods_cfg} must be laid "
                                "out as {lumi: <value in /pb>, golden_json: <file in data/>}")
                continue
            run_periods[year] = cfg or {}
        return run_periods

    def build_cuts(self, problems=None):
        """Resolve the requested channels into ``{channel: {"obj": {...}, "evt": [...]}}``."""
        problems = [] if problems is None else problems
        menu = utilities.load_yaml(utilities.resolve_path(self.selections_cfg, ""))
        channel_cuts = {}
        for channel in self.channel_names:
            if str(channel).startswith("_"):
                problems.append(
                    f"'{channel}' is a building block of {self.selections_cfg}, not a channel: "
                    "entries whose name starts with an underscore cannot be run"
                )
                continue
            if channel not in menu or not isinstance(menu[channel], dict):
                problems.append(
                    f"channel '{channel}' is not a selection in {self.selections_cfg} "
                    f"(available: {list_channels(self.selections_cfg)})"
                )
                continue
            cuts = menu[channel]
            for key in cuts:
                if key not in SELECTION_KEYS:
                    problems.append(f"[{channel}] unknown key '{key}' (expected one of "
                                    f"{list(SELECTION_KEYS)})")

            obj_cuts = {}
            requested = cuts.get("obj_cuts") or {}
            if not isinstance(requested, dict):
                problems.append(
                    f"[{channel}] obj_cuts must give the cuts object by object "
                    f"(<object>: [<cut>, ...]), not as a {type(requested).__name__}")
                requested = {}
            for obj, names in requested.items():
                names = utilities.flatten(names)
                if obj not in self.known_objects:
                    problems.append(f"[{channel}] '{obj}' has object cuts but is not defined "
                                    "in definitions/objects.py")
                    continue
                defined = obj_cut_defs.get(obj, {})
                if not isinstance(defined, dict):
                    problems.append(f"obj_cut_defs['{obj}'] in definitions/cuts.py must be a "
                                    "dict of cut name -> function")
                    continue
                for cut in names:
                    if cut not in defined:
                        problems.append(f"[{channel}] {obj} cut '{cut}' is not defined in "
                                        "definitions/cuts.py")
                obj_cuts[obj] = utilities.unique(names)

            evt_cuts = utilities.flatten(cuts.get("evt_cuts") or [])
            for cut in evt_cuts:
                if cut not in evt_cut_defs:
                    problems.append(f"[{channel}] event cut '{cut}' is not defined in "
                                    "definitions/cuts.py")
            if len(set(evt_cuts)) != len(evt_cuts):
                repeated = sorted({c for c in evt_cuts if evt_cuts.count(c) > 1})
                problems.append(f"[{channel}] event cuts listed more than once: {repeated}")
            if "None" in evt_cuts:
                problems.append(f"[{channel}] 'None' is reserved for the first cutflow row")
            channel_cuts[channel] = {"obj": obj_cuts, "evt": evt_cuts}
        return channel_cuts

    def build_hist_names(self, problems=None):
        """Resolve the requested collections into an ordered list of histogram names."""
        problems = [] if problems is None else problems
        menu = utilities.load_yaml(utilities.resolve_path(self.histograms_cfg, ""))
        names = []
        for collection in self.hist_collection_names:
            if str(collection).startswith("_"):
                problems.append(
                    f"'{collection}' is a building block of {self.histograms_cfg}, not a "
                    "histogram collection: entries whose name starts with an underscore "
                    "cannot be filled"
                )
                continue
            if collection not in menu:
                problems.append(
                    f"histogram collection '{collection}' is not defined in "
                    f"{self.histograms_cfg} (available: {list_hist_collections(self.histograms_cfg)})"
                )
                continue
            for name in utilities.flatten(menu[collection]):
                if name not in hist_defs:
                    problems.append(f"histogram '{name}' (collection '{collection}') is not "
                                    "defined in definitions/hists.py")
                else:
                    names.append(name)
        return utilities.unique(names)

    def build_histograms(self):
        """Fresh Histogram objects for one chunk, each with a channel axis."""
        hists = {}
        for name in self.hist_names:
            hists[name] = copy.deepcopy(hist_defs[name])
            hists[name].make_hist(name, self.channel_names)
        return hists

    # ------------------------------------------------------------------ #
    # per-chunk work
    # ------------------------------------------------------------------ #

    def golden_json_mask(self, events, year, warn):
        """Mask of the events in certified luminosity sections, or None if not configured."""
        name = self.run_periods.get(year, {}).get("golden_json")
        if not name:
            warn(f"no golden JSON is configured for run period '{year}' in "
                 f"{self.run_periods_cfg}: all luminosity sections of the data are kept")
            return None
        path = str(name) if os.path.isabs(str(name)) else os.path.join(BASE_DIR, "data", str(name))
        # A golden JSON that is missing or unreadable must stop the run. The errors
        # below are deliberately not OSErrors and carry no cause: coffea, told to skip
        # bad input files, would otherwise drop every chunk of data instead.
        if not os.path.exists(path):
            raise RuntimeError(f"golden JSON for run period '{year}' not found: {path}")
        try:
            lumi_mask = LumiMask(path)
        except Exception as exc:
            raise RuntimeError(f"golden JSON for run period '{year}' could not be read: "
                               f"{path} ({utilities.brief(exc)})") from None
        # LumiMask's compiled lookup takes 32-bit unsigned integers and nothing else
        runs = np.asarray(ak.to_numpy(events.run)).astype(np.uint32)
        lumis = np.asarray(ak.to_numpy(events.luminosityBlock)).astype(np.uint32)
        return np.asarray(lumi_mask(runs, lumis), dtype=bool)

    def process(self, events):
        """Apply the selections to one chunk and fill histograms and cutflows."""
        meta = dict(events.metadata)
        dataset = meta["dataset"]
        if "entrystart" in meta and "entrystop" in meta:
            n_chunk = int(meta["entrystop"]) - int(meta["entrystart"])
        else:
            n_chunk = len(events)
        if "is_data" in meta:
            is_data = utilities.as_bool(meta["is_data"])
        else:
            is_data = "genWeight" not in events.fields
        year = str(meta.get("year", "") or "")
        skim_factor = float(meta.get("skim_factor", 1.0) or 1.0)
        warn = utilities.WarningLog()

        n_removed = 0
        if is_data:
            mask = self.golden_json_mask(events, year, warn)
            if mask is not None:
                n_before = len(events)
                events = events[mask]
                n_removed = n_before - len(events)

        hists = self.build_histograms()
        cutflows = {}
        counters = {}

        if len(events) == 0:
            # Nothing to select: return zeros in the usual shape, so that merging with
            # the other chunks (and reading a sample that is empty altogether) just works.
            for channel, cuts in self.channel_cuts.items():
                cutflows[channel] = Cutflow()
                for cut in ["None"] + cuts["evt"]:
                    cutflows[channel].add_row(cut, 0, 0.0)
                counters[channel] = {}
            return {dataset: self._output(cutflows, hists, counters, n_chunk, 0.0,
                                          n_removed, year, is_data, warn)}

        # weights
        try:
            if is_data:
                sum_weights = float(len(events))
            else:
                # summed in double precision: a float32 sum depends on how the events
                # are split into chunks, and the normalisation with it
                generator_weights = ak.values_astype(
                    _as_array(weight_defs.generator_weight(events)), np.float64)
                sum_weights = float(ak.sum(generator_weights)) / skim_factor
            evt_weights = ak.values_astype(
                _as_array(weight_defs.event_weight(events, is_data)), np.float64)
            if evt_weights.ndim != 1 or len(evt_weights) != len(events):
                raise ValueError("event_weight must return one value per event")
        except Exception as exc:
            if utilities.is_io_error(exc):
                raise
            raise RuntimeError(
                f"definitions/weights.py: the event weights could not be computed "
                f"({utilities.brief(exc)})"
            ) from utilities.cause(exc)

        # primary objects
        objs = utilities.ObjectStore(known=self.known_objects)
        unavailable = set()
        for name, builder in primary_objs.items():
            try:
                objs[name] = utilities.pt_ordered(builder(events))
            except Exception as exc:
                if utilities.is_io_error(exc):
                    raise
                # reading a collection or branch the file does not have
                absent = utilities.is_missing_field(exc)
                if absent and name in self.optional_objects:
                    warn(f"'{name}' is not available in this sample ({utilities.brief(exc)})")
                elif self.strict:
                    fix = ("If it is meant to be missing from some samples, add it to "
                           "optional_objs in definitions/objects.py; otherwise fix its "
                           "definition there." if absent else "Fix it in definitions/objects.py.")
                    raise RuntimeError(
                        f"primary object '{name}' could not be built ({utilities.brief(exc)}). "
                        f"{fix} Building the processor with strict=False carries on without it."
                    ) from utilities.cause(exc)
                else:
                    warn(f"'{name}' could not be built and is unavailable ({utilities.brief(exc)})")
                objs.unavailable.add(name)
        unavailable.update(objs.unavailable)

        # each channel is an independent selection of the same primary objects
        for channel, cuts in self.channel_cuts.items():
            channel_objs = utilities.ObjectStore(
                objs, known=self.known_objects, unavailable=set(objs.unavailable))
            obj_selection = selection.JaggedSelection(
                cuts["obj"], verbose=self.verbose, strict=self.strict, warn=warn, label=channel)
            sel_objs = obj_selection.apply_obj_cuts(channel_objs, list(primary_objs))

            for name, builder in derived_objs.items():
                try:
                    sel_objs[name] = utilities.pt_ordered(builder(sel_objs))
                except Exception as exc:
                    if utilities.is_io_error(exc):
                        raise
                    missing = utilities.missing_object(exc)
                    if missing is not None:
                        warn(f"[{channel}] '{name}' not built: '{missing}' is not "
                             "available in this sample")
                    elif name in self.optional_objects and utilities.is_missing_field(exc):
                        # it reads a branch this sample does not have, and is allowed to
                        warn(f"[{channel}] '{name}' is not available in this sample "
                             f"({utilities.brief(exc)})")
                    elif self.strict:
                        raise RuntimeError(
                            f"[{channel}] derived object '{name}' could not be built "
                            f"({utilities.brief(exc)}). Fix it in definitions/objects.py, or "
                            "build the processor with strict=False to carry on without it."
                        ) from utilities.cause(exc)
                    else:
                        warn(f"[{channel}] '{name}' could not be built and is unavailable "
                             f"({utilities.brief(exc)})")
                    # whatever uses it from here on is told that it is not available
                    sel_objs.unavailable.add(name)
                    unavailable.add(name)
                    continue
                sel_objs = obj_selection.apply_obj_cuts(sel_objs, [name])

            sel_objs["evt_weights"] = self._channel_weights(sel_objs, evt_weights, is_data,
                                                            channel, warn)

            evt_selection = selection.Selection(
                cuts["evt"], verbose=self.verbose, strict=self.strict, warn=warn, label=channel)
            sel_objs = evt_selection.apply_evt_cuts(sel_objs)
            cutflows[channel] = evt_selection.cutflow

            sel_objs["ch"] = channel
            hist_weights = sel_objs["evt_weights"]
            if self.unweighted_hist:
                hist_weights = ak.ones_like(hist_weights)
            for hist_def in hists.values():
                hist_def.fill(sel_objs, hist_weights, warn=warn, label=channel)

            counters[channel] = {}
            for name, counter in counter_defs.items():
                try:
                    counters[channel][name] = float(counter(sel_objs))
                except Exception as exc:
                    if utilities.is_io_error(exc):
                        raise
                    missing = utilities.missing_object(exc)
                    if missing is not None:
                        warn(f"[{channel}] counter '{name}' not filled: '{missing}' is not "
                             "available in this sample")
                    else:
                        warn(f"[{channel}] counter '{name}' could not be filled and was skipped "
                             f"({utilities.brief(exc)})")

        return {dataset: self._output(cutflows, hists, counters, n_chunk, sum_weights,
                                      n_removed, year, is_data, warn, unavailable)}

    def _channel_weights(self, sel_objs, evt_weights, is_data, channel, warn):
        """Event weights of one channel: the event weight times the object weight."""
        try:
            extra = weight_defs.object_weight(sel_objs, is_data)
            if extra is None:
                return evt_weights
            extra = ak.values_astype(_as_array(extra), np.float64)
            if extra.ndim != 1 or len(extra) != len(evt_weights):
                raise ValueError("object_weight must return one value per event")
            return evt_weights * extra
        except Exception as exc:
            if utilities.is_io_error(exc):
                raise
            missing = utilities.missing_object(exc)
            if missing is not None:
                warn(f"[{channel}] object_weight not applied: '{missing}' is not "
                     "available in this sample")
            elif self.strict:
                raise RuntimeError(
                    f"[{channel}] definitions/weights.py: object_weight failed "
                    f"({utilities.brief(exc)})"
                ) from utilities.cause(exc)
            else:
                warn(f"[{channel}] object_weight failed and was not applied "
                     f"({utilities.brief(exc)})")
            return evt_weights

    def _output(self, cutflows, hists, counters, n_evts, sum_weights, n_removed,
                year, is_data, warn, unavailable=()):
        """Assemble what one chunk returns.

        Everything in here must add up across chunks: numbers add, sets take the
        union, histograms and cutflows add bin by bin and row by row. Plain strings,
        lists and None do not merge sensibly, so sample-level facts are kept in sets.
        """
        return {
            "cutflow": cutflows,
            "hists": {name: h.hist for name, h in hists.items()},
            "counters": counters,
            "metadata": {
                "n_evts": int(n_evts),
                # sum of generator weights, corrected for the skim: the lumi * xs denominator
                "scaled_sum_weights": float(sum_weights),
                "n_removed_golden_json": int(n_removed),
                "year": {year},
                "is_data": {bool(is_data)},
                "unweighted_hist": {self.unweighted_hist},
                # objects that could not be built in at least one chunk
                "unavailable_objects": set(unavailable),
            },
            "warnings": set(warn.messages),
        }

    # ------------------------------------------------------------------ #
    # after all chunks are merged
    # ------------------------------------------------------------------ #

    def postprocess(self, accumulator):
        """Scale the cutflows and histograms of simulated samples to lumi * xs.

        Called by coffea once all chunks of a run are merged. See
        utilities.scale_to_lumi_xs for what is scaled, and utilities.merge_outputs for
        combining the outputs of separate runs. The accumulator is modified in place
        and returned, which suits every coffea version.
        """
        return utilities.scale_to_lumi_xs(
            accumulator, verbose=self.verbose,
            run_periods_cfg=utilities.resolve_path(self.run_periods_cfg, ""))
