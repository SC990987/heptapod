"""Helpers shared by the processor, the definitions and the notebooks.

Nothing here imports matplotlib: plotting lives in analysis_pkg.tools.plotting, so batch
jobs and dask workers never need a display stack.
"""

import copy
import os

import awkward as ak
import numpy as np
import yaml

from analysis_pkg import BASE_DIR


# --------------------------------------------------------------------------- #
# configs
# --------------------------------------------------------------------------- #

def resolve_path(name, *subdirs):
    """Turn a config or data file name into a path.

    Absolute paths are returned unchanged. Anything else is looked up under each
    ``BASE_DIR/<subdir>`` in turn; if none exists, the first candidate is returned so
    that the error the caller gets names the place the file was expected.
    """
    name = str(name)
    if os.path.isabs(name):
        return name
    candidates = [os.path.join(BASE_DIR, sub, name) for sub in (subdirs or ("",))]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def load_yaml(cfg):
    """Load a yaml config and return its top-level mapping ({} for an empty file).

    Every config of the analysis is a mapping of names to values. The names are
    returned as text, so that a run period written as 2018 and one written as "2018"
    are the same entry; a file that holds anything else at its top level is refused.
    """
    with open(cfg, encoding="utf8") as yaml_cfg:
        content = yaml.safe_load(yaml_cfg)
    if content is None:
        return {}
    if not isinstance(content, dict):
        raise ValueError(f"{cfg} must hold 'name: value' entries at its top level, not a "
                         f"{type(content).__name__}")
    return {str(name): value for name, value in content.items()}


def flatten(x):
    """Flatten an arbitrarily nested list or dict into a flat list.

    This is what lets selections and histogram collections be assembled from yaml
    anchors: ``- *muon_base`` drops a list inside a list, and this undoes the nesting.
    """
    flattened = []

    def loop(sub):
        if isinstance(sub, dict):
            sub = sub.values()
        for item in sub:
            if isinstance(item, (dict, list, tuple)):
                loop(item)
            else:
                flattened.append(item)

    if isinstance(x, (dict, list, tuple)):
        loop(x)
    elif x is not None:
        flattened.append(x)
    return flattened


def unique(items):
    """Drop repeated items, keeping the first occurrence of each in order."""
    return list(dict.fromkeys(items))


# --------------------------------------------------------------------------- #
# samples and normalisation
# --------------------------------------------------------------------------- #

def _join(base, name):
    """Concatenate a base path and a file name, adding a slash only when both lack one."""
    if not base:
        return name
    if base.endswith("/") or name.startswith("/"):
        return base + name
    return base + "/" + name


def _is_complete_path(name):
    return "://" in name or os.path.isabs(name)


def as_bool(value):
    """A yaml value as a bool: the string "false" must not count as true."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def load_samples(location_cfg="samples.yaml", tag=None):
    """Return ``{tag: block}`` for the groups of samples in a location config.

    ``location_cfg`` is a file under ``configs/samples/`` (or an absolute path) shaped as
    ``{tag: {path: ..., year: ..., samples: {name: {files: [...], ...}}}}``. With
    ``tag`` only that group is returned.
    """
    path = resolve_path(location_cfg, "configs/samples", "configs")
    locations = {}
    for name, block in load_yaml(path).items():
        block = {} if block is None else block
        samples = block.get("samples") if isinstance(block, dict) else None
        samples = {} if samples is None else samples
        if not isinstance(block, dict) or not isinstance(samples, dict):
            raise ValueError(
                f"{path}: group '{name}' must be laid out as "
                "{path: ..., year: ..., samples: {<sample name>: {files: [...]}}}")
        for sample, cfg in samples.items():
            if cfg is not None and not isinstance(cfg, dict):
                raise ValueError(f"{path}: sample '{sample}' of group '{name}' must be laid "
                                 "out as {files: [...], is_data: ..., year: ..., skim_factor: ...}")
        locations[name] = {**block, "samples": {str(sample): (cfg or {})
                                                for sample, cfg in samples.items()}}
    if tag is None:
        return locations
    if str(tag) not in locations:
        raise KeyError(f"'{tag}' is not defined in {path} (available: {sorted(locations)})")
    return {str(tag): locations[str(tag)]}


def make_fileset(samples, tag=None, max_files=-1, location_cfg="samples.yaml",
                 fileset=None, replace_prefix=None):
    """Build the fileset that coffea's Runner takes.

    samples: sample name or list of names defined in the location config
    tag: which group of the location config to read. Without it every group is
        searched, which works as long as a sample name is not used in two groups
    max_files: keep only the first N files of each sample (-1 keeps all)
    location_cfg: file under configs/samples/, or an absolute path
    fileset: an existing fileset to extend, so that several configs can be combined
    replace_prefix: ``{old: new}`` substitutions applied to every file path, for sites
        that reach the same storage through a different door (a cache versus a
        redirector, say)

    Each sample carries the metadata the processor reads: ``is_data``, ``year`` and
    ``skim_factor`` (the fraction of the original events that survived a skim; the
    sum of generator weights is divided by it before normalising).

    Hand the Runner that takes this fileset ``metadata_cache={}``. coffea otherwise
    remembers, for the rest of the python session, the metadata a file was first run
    with, and a sample whose ``skim_factor`` or ``is_data`` was edited since would
    silently be processed with the old values.
    """
    groups = load_samples(location_cfg, tag)
    if fileset is None:
        fileset = {}
    if isinstance(samples, str):
        samples = [samples]
    for sample in samples:
        found = [name for name, group in groups.items() if sample in (group.get("samples") or {})]
        if not found:
            available = sorted(s for group in groups.values() for s in (group.get("samples") or {}))
            where = f"under '{tag}' " if tag is not None else ""
            raise KeyError(
                f"sample '{sample}' is not defined {where}in {location_cfg} (available: {available})"
            )
        if len(found) > 1:
            raise KeyError(
                f"sample '{sample}' is defined in several groups of {location_cfg} ({found}): "
                "pass tag=... to choose one"
            )
        block = groups[found[0]]
        cfg = block["samples"][sample] or {}
        base = _join(block.get("path", "") or "", cfg.get("path", "") or "")
        listed = cfg.get("files") or []
        if isinstance(listed, str):
            listed = [listed]
        if not isinstance(listed, (list, tuple)) or not all(isinstance(f, str) for f in listed):
            raise ValueError(f"sample '{sample}': 'files' must be a list of paths in {location_cfg}")
        files = [f if _is_complete_path(f) else _join(base, f) for f in listed]
        for old, new in (replace_prefix or {}).items():
            files = [f.replace(old, new) for f in files]
        if max_files != -1:
            files = files[:max_files]
        if not files:
            raise ValueError(f"sample '{sample}' has no files in {location_cfg}")
        skim_factor = cfg.get("skim_factor")
        skim_factor = 1.0 if skim_factor is None else float(skim_factor)
        if not skim_factor > 0:
            raise ValueError(f"sample '{sample}': skim_factor must be a positive number")
        fileset[sample] = {
            "files": files,
            "metadata": {
                "skim_factor": skim_factor,
                "is_data": as_bool(cfg.get("is_data", False)),
                "year": str(cfg.get("year", block.get("year", "")) or ""),
            },
        }
    _check_shared_files(fileset)
    return fileset


def _check_shared_files(fileset):
    """Refuse a file listed under two samples that are to be treated differently.

    coffea up to 2025.7 tells files apart by name alone, so such a file would be
    processed with the metadata of one of the two samples for both.
    """
    owners = {}
    for sample, entry in fileset.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("files"), (list, tuple)):
            continue
        for path in entry["files"]:
            other = owners.setdefault(path, sample)
            if other != sample and fileset[other].get("metadata") != entry.get("metadata"):
                raise ValueError(
                    f"{path} is listed under both '{other}' and '{sample}', with different "
                    "is_data, year or skim_factor: a file can only be processed one way in "
                    "one run. Run the two samples separately, or give them the same settings")


def get_xs(dataset, cfg="cross_sections.yaml"):
    """Cross section of a dataset in pb, from configs/cross_sections.yaml."""
    path = resolve_path(cfg, "configs")
    value = load_yaml(path).get(str(dataset))
    if value is None:
        raise KeyError(f"no cross section for '{dataset}' in {path}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"the cross section of '{dataset}' in {path} is not a number: "
                         f"{value!r}") from None


def get_lumi(year, cfg="run_periods.yaml"):
    """Integrated luminosity of a run period in /pb, from configs/run_periods.yaml."""
    path = resolve_path(cfg, "configs")
    period = load_yaml(path).get(str(year))
    if not isinstance(period, dict) or period.get("lumi") is None:
        raise KeyError(f"no luminosity for run period '{year}' in {path}")
    try:
        return float(period["lumi"])
    except (TypeError, ValueError):
        raise ValueError(f"the luminosity of run period '{year}' in {path} is not a number: "
                         f"{period['lumi']!r}") from None


def get_lumixs_weight(dataset, year, sum_weights, run_periods_cfg="run_periods.yaml"):
    """Weight that scales the processed simulation to lumi * cross section.

    sum_weights is the sum of generator weights of the processed events (already
    corrected for any skim), so the weight is ``lumi * xs / sum_weights``. A weight
    of zero (a luminosity or cross section still set to 0) is refused: scaling by it
    would wipe the sample out with no way back.
    """
    if not sum_weights:
        raise ValueError(f"sum of generator weights is zero for '{dataset}'")
    weight = get_lumi(year, run_periods_cfg) * get_xs(dataset) / sum_weights
    if weight == 0 or not np.isfinite(weight):
        raise ValueError(f"lumi * xs / sum of weights is {weight} for '{dataset}': check the "
                         f"luminosity of '{year}' and the cross section")
    return weight


def get_hist(out, sample, name, channel):
    """One histogram of one sample and channel, with the channel axis removed."""
    return out[sample]["hists"][name][{"channel": channel}]


def sum_hists(out, samples, name, channel):
    """Sum of one histogram over several samples (a background stack as a single shape)."""
    total = None
    for sample in samples:
        h = get_hist(out, sample, name, channel)
        total = h.copy() if total is None else total + h
    return total


def cutflow_table(out, channel, samples=None, weighted=True):
    """One channel's cutflow for several samples side by side, as a list of text lines."""
    samples = list(out) if samples is None else list(samples)
    flows = {sample: out[sample]["cutflow"][channel] for sample in samples}
    cuts = unique(cut for flow in flows.values() for cut in flow.cuts())
    key = "weighted" if weighted else "raw"
    header = ["cut name"] + samples
    body = []
    for cut in cuts:
        row = [cut]
        for sample in samples:
            vals = flows[sample].rows.get(cut)
            if vals is None:
                row.append("-")
            else:
                row.append(f"{vals[key]:.1f}" if weighted else str(vals[key]))
        body.append(row)
    widths = [max(len(row[i]) for row in [header] + body) for i in range(len(header))]
    lines = ["  ".join([header[0].ljust(widths[0])]
                       + [header[i].rjust(widths[i]) for i in range(1, len(header))])]
    lines.append("  ".join("-" * w for w in widths))
    for row in body:
        lines.append("  ".join([row[0].ljust(widths[0])]
                               + [row[i].rjust(widths[i]) for i in range(1, len(row))]))
    return lines


def save_output(output, path):
    """Write a processor output to a .coffea file, creating its directory if needed."""
    from coffea.util import save
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    save(output, path)


def load_output(path):
    """Read a processor output back from a .coffea file."""
    from coffea.util import load
    return load(path)


_NOT_SCALED = "not scaled to lumi * xs"


def scale_to_lumi_xs(output, verbose=False, run_periods_cfg="run_periods.yaml"):
    """Scale the simulated samples of a processor output to lumi * cross section, in place.

    ``run_periods_cfg`` is the file the luminosities are read from: a name under
    configs/, or a complete path.

    The weight of a sample is ``lumi * xs / (sum of generator weights / skim_factor)``,
    the sum running over the events that were processed. It multiplies the weighted
    cutflow columns and, unless the histograms were filled unweighted, the histograms.
    Data is left as it is. So is a sample with no cross section or luminosity
    configured, with a warning recorded in its output: its weighted yields are then
    sums of generator weights, not event counts. A sample that was scaled before (it
    carries ``lumixs_weight`` in its metadata) is not scaled again.

    The processor calls this once all chunks of a run are merged. Outputs of separate
    runs are combined with merge_outputs, which redoes the scaling for the total.
    """
    if not output:
        return output
    for sample, result in output.items():
        meta = result["metadata"]
        if "lumixs_weight" in meta:
            continue
        if len(meta["is_data"]) != 1 or len(meta["year"]) != 1:
            message = (f"'{sample}' mixes data and simulation or several run periods: "
                       f"{_NOT_SCALED}")
            print(f"Warning: {message}")
            result["warnings"].add(message)
            continue
        if next(iter(meta["is_data"])):
            continue
        year = next(iter(meta["year"]))
        try:
            weight = get_lumixs_weight(sample, year, meta["scaled_sum_weights"],
                                       run_periods_cfg)
        except (KeyError, ValueError, OSError) as exc:
            message = f"'{sample}' {_NOT_SCALED} ({brief(exc)})"
            print(f"Warning: {message}")
            result["warnings"].add(message)
            continue
        for cutflow in result["cutflow"].values():
            cutflow.scale(weight)
        if set(meta.get("unweighted_hist", {False})) == {False}:
            for name in result["hists"]:
                result["hists"][name] *= weight
        meta["lumixs_weight"] = float(weight)
        if verbose:
            print(f"{sample}: scaled to lumi * xs with weight {weight:.6g}")
    return output


def _without_scaling(output):
    """A copy of one processor output with its lumi * xs scaling taken out again."""
    output = copy.deepcopy(output)
    for result in output.values():
        meta = result["metadata"]
        result["warnings"] = {w for w in result["warnings"] if _NOT_SCALED not in w}
        weight = meta.pop("lumixs_weight", None)
        if not weight:
            continue
        for cutflow in result["cutflow"].values():
            cutflow.scale(1.0 / weight)
        if set(meta.get("unweighted_hist", {False})) == {False}:
            for name in result["hists"]:
                result["hists"][name] *= 1.0 / weight
    return output


def merge_outputs(outputs, scale=True, verbose=False, run_periods_cfg="run_periods.yaml"):
    """Combine the outputs of separate runs into one, normalised as a whole.

    outputs: processor outputs, or paths of .coffea files holding them
    scale: scale simulation to lumi * xs once everything is added up (default)
    run_periods_cfg: file the luminosities are read from (as for the processor)

    Use this whenever one sample was processed in more than one run, for example in
    batch jobs that each take a slice of its files. Every run scales its own slice to
    lumi * xs with the generator weights of that slice alone, so adding such outputs
    by hand counts the sample once per run. Here the scaling of every piece is taken
    out first, the pieces are added, and the total is scaled once with the sum of all
    generator weights. The inputs are not modified.

    All pieces must have been produced with the same channels, histogram collections
    and ``unweighted_hist`` setting.
    """
    from coffea.processor import accumulate

    pieces = []
    for item in outputs:
        piece = load_output(item) if isinstance(item, (str, os.PathLike)) else item
        pieces.append(_without_scaling(piece))
    if not pieces:
        raise ValueError("merge_outputs needs at least one output")
    merged = accumulate(pieces)
    for sample, result in merged.items():
        if len(result["metadata"].get("unweighted_hist", ())) > 1:
            raise ValueError(
                f"'{sample}' was filled with unweighted_hist in some outputs and without "
                "it in others: those histograms cannot be added"
            )
    if not scale:
        return merged
    return scale_to_lumi_xs(merged, verbose=verbose, run_periods_cfg=run_periods_cfg)


# --------------------------------------------------------------------------- #
# bookkeeping used by the engine
# --------------------------------------------------------------------------- #

class MissingObject(KeyError):
    """An object the definitions know about is not available in this sample.

    Raised when a cut, histogram or derived object asks for, say, ``objs["gens"]`` on
    data. The engine treats it as "does not apply here": the cut or histogram is
    skipped with a warning instead of stopping the run.
    """


class ObjectStore(dict):
    """The ``objs`` dictionary handed to every cut, histogram and derived object.

    A plain dict, except for what happens when a name is asked for and is not there:

    - the object is not available in this sample (``unavailable``): MissingObject,
      which the engine reports as "does not apply here" and carries on from;
    - the object is defined but has not been built yet at this point (a derived object
      used by a primary object's cut, or by a derived object defined above it): a
      KeyError that says so, because that is a mistake in the definitions;
    - nobody defined the name: a KeyError listing what is defined.
    """

    def __init__(self, *args, known=(), unavailable=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.known = frozenset(known)
        # shared with the copies made while one channel is processed
        self.unavailable = set() if unavailable is None else unavailable

    def __missing__(self, key):
        if key in self.unavailable:
            raise MissingObject(key)
        if key in self.known:
            raise KeyError(
                f"'{key}' has not been built yet at this point. Object cuts of primary "
                "objects cannot use derived objects, and a derived object can only use "
                "those defined above it in definitions/objects.py"
            )
        raise KeyError(
            f"'{key}' is not an object: add it to primary_objs or derived_objs in "
            f"definitions/objects.py (defined: {sorted(self.known)})"
        )

    def copy(self):
        return ObjectStore(self, known=self.known, unavailable=self.unavailable)


def _chain(exc):
    """The exception and everything it was raised from."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def missing_object(exc):
    """Name of the absent object if ``exc`` is (or wraps) a MissingObject, else None."""
    for link in _chain(exc):
        if isinstance(link, MissingObject):
            return link.args[0] if link.args else "?"
    return None


# Where a failure to read the input comes from: the libraries that do the reading,
# called from coffea's lazy column loader (the same module in every coffea version
# this engine supports).
_COLUMN_LOADER = "coffea.nanoevents.mapping"
_IO_LIBRARIES = ("uproot", "fsspec", "fsspec_xrootd", "XRootD", "pyxrootd")


def is_io_error(exc):
    """True if ``exc`` came out of reading the input file, rather than out of the analysis.

    Columns are read lazily, so an unreadable input can surface in the middle of a cut.
    Such an error must reach coffea unchanged (``skipbadfiles`` acts on it) instead of
    being reported as a cut that cannot be evaluated.

    It is recognised by where it was raised, not by its type: in uproot, fsspec or
    XRootD, underneath coffea's column loader. An error of the very same type from
    anywhere else is the analysis's own (a correction file that is missing, a golden
    JSON that cannot be opened) and must be reported as that: were it passed on, coffea
    would drop the chunk as if the input were bad, and the output would quietly shrink.
    """
    for link in _chain(exc):
        below_loader = False
        frame = link.__traceback__
        while frame is not None:
            module = frame.tb_frame.f_globals.get("__name__") or ""
            if module.startswith(_COLUMN_LOADER):
                below_loader = True
            elif below_loader and module.split(".")[0] in _IO_LIBRARIES:
                return True
            frame = frame.tb_next
    return False


def cause(exc):
    """What a wrapped error is raised from: ``raise RuntimeError(...) from cause(exc)``.

    With ``skipbadfiles``, coffea drops a chunk when an OSError is among the causes of
    its failure. An error that is not about the input must not carry one along, or a
    missing correction file would be skipped over chunk by chunk instead of reported.
    """
    return None if any(isinstance(link, OSError) for link in _chain(exc)) else exc


def is_missing_field(exc):
    """True if ``exc`` is (or wraps) awkward's "this array has no such field" error.

    That is what reading a collection or branch the file does not have looks like.
    awkward also raises AttributeError when a field exists but could not be produced
    (a behaviour rejecting the collection, say); that is a failure, not an absence.
    """
    not_found = getattr(ak.errors, "FieldNotFoundError", ())
    for link in _chain(exc):
        if isinstance(link, AttributeError) and str(link).startswith("no field named"):
            return True         # evts.Collection, obj.branch
        if isinstance(link, not_found):
            return True         # evts["Collection"], obj["branch"]
    return False


def brief(exc, limit=300):
    """One line describing an exception, short enough to keep in the output."""
    message = str(exc)
    if isinstance(exc, KeyError) and exc.args and isinstance(exc.args[0], str):
        message = exc.args[0]  # str(KeyError) wraps its message in quotes
    lines = [line.strip() for line in message.strip().splitlines() if line.strip()]
    text = lines[0] if lines else ""
    if text.endswith(":") and len(lines) > 1:
        # "while trying to get field 'X', an exception occurred:" and the like put the
        # reason on the next line
        text = f"{text} {lines[1]}"
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


class WarningLog:
    """The warnings of one chunk: each printed once, all returned with the output.

    The warnings of all chunks end up in one set, so a message that says the same
    thing for every chunk is recorded once. Keep numbers that change from chunk to
    chunk out of messages for that reason.
    """

    def __init__(self, quiet=False):
        self.messages = set()
        self.quiet = quiet

    def __call__(self, message):
        if message not in self.messages and not self.quiet:
            print(f"Warning: {message}")
        self.messages.add(message)


def event_mask(mask, n_events, what="event cut"):
    """Check and normalise the result of an event cut into a numpy bool array.

    An event cut must give one True/False per event. Missing values count as False.
    A per-object (jagged) mask is refused: silently using it would drop the wrong
    events.
    """
    if isinstance(mask, (bool, np.bool_)):
        return np.full(n_events, bool(mask), dtype=bool)
    if not isinstance(mask, ak.Array):
        mask = ak.Array(mask)
    if mask.ndim != 1:
        raise ValueError(
            f"{what} returned a mask with {mask.ndim} dimensions; it must give one "
            "True/False per event (reduce a per-object mask with ak.any, ak.all or "
            "ak.num(...) >= n)"
        )
    if len(mask) != n_events:
        raise ValueError(f"{what} did not return one value per event")
    return ak.to_numpy(ak.fill_none(mask, False)).astype(bool)


def pt_ordered(obj):
    """Sort a jagged collection by descending pT; anything else is returned untouched."""
    if not isinstance(obj, ak.Array) or obj.ndim != 2:
        return obj
    try:
        pt = obj.pt
    except Exception as exc:  # vertices, flags, ... have nothing to order by
        if is_io_error(exc):
            raise
        return obj
    return obj[ak.argsort(pt, ascending=False, axis=1)]


# --------------------------------------------------------------------------- #
# columnar idioms for the definitions
# --------------------------------------------------------------------------- #

# Field names coffea's vector behaviours interpret as coordinates. A record may carry
# one consistent set of them, so helpers that re-zip a collection leave the rest out.
COORDINATE_NAMES = frozenset({
    "x", "y", "z", "t", "px", "py", "pz", "rho", "pt", "phi", "eta", "theta",
    "tau", "E", "e", "energy", "M", "m", "mass",
})


def as_lorentz(coll, mass=None, keep=None):
    """Re-zip a collection as (pt, eta, phi, mass) Lorentz vectors with coffea behaviour.

    Use it in definitions/objects.py for collections the schema does not know: they
    arrive as plain records with no ``delta_r``, ``px`` or ``+``. It also covers
    collections that have no mass branch (pass ``mass`` in GeV).

    coll: a collection with pt, eta and phi fields
    mass: fixed mass in GeV for every object; by default the ``mass`` field is used
    keep: names of the other fields to carry over (default: all of them, apart from
        fields whose name would be read as a second set of coordinates)

    On a Lorentz vector the names x, y, z, t, px, energy and the like all mean
    components of the momentum, so branches with those names cannot be carried over
    as they are: stored copies of the momentum are left out, and a position stored as
    x, y, z is carried over as vx, vy, vz (the names generator particles use).
    """
    from coffea.nanoevents.methods import nanoaod

    fields = coll.fields
    for needed in ("pt", "eta", "phi"):
        if needed not in fields:
            raise ValueError(f"as_lorentz needs a '{needed}' field (found {fields})")
    if mass is not None:
        mass_values = ak.full_like(coll.pt, mass)
    elif "mass" in fields:
        mass_values = coll.mass
    else:
        raise ValueError("the collection has no 'mass' field: pass mass=<value in GeV>")
    columns = {"pt": coll.pt, "eta": coll.eta, "phi": coll.phi, "mass": mass_values}
    extra = [f for f in fields if f not in COORDINATE_NAMES] if keep is None else list(keep)
    for name in extra:
        if name in COORDINATE_NAMES:
            raise ValueError(f"'{name}' is a coordinate name and cannot be kept as an extra field")
        if name in fields:
            columns[name] = coll[name]
    if keep is None:
        for name in ("x", "y", "z"):
            if name in fields and "v" + name not in columns:
                columns["v" + name] = coll[name]
    record = "PtEtaPhiMCandidate" if "charge" in columns else "PtEtaPhiMLorentzVector"
    return ak.zip(columns, depth_limit=coll.ndim, with_name=record, behavior=nanoaod.behavior)


def as_int(array):
    """Return the array with its values converted to 64-bit integers."""
    return ak.values_astype(array, "int64")


def drop_none(obj):
    """Remove missing entries from each event's list."""
    return obj[~ak.is_none(obj, axis=1)]


def dR(obj1, obj2):
    """dR from each obj1 to the nearest obj2 (inf where the event has no obj2)."""
    dr = obj1.nearest(obj2, return_metric=True)[1]
    return ak.fill_none(dr, np.inf)


def matched(obj1, obj2, r):
    """The obj1 that have at least one obj2 within dR < r."""
    return drop_none(obj1[dR(obj1, obj2) < r])


def unmatched(obj1, obj2, r):
    """The obj1 that have no obj2 within dR < r (overlap removal)."""
    return drop_none(obj1[dR(obj1, obj2) >= r])


def rho(obj, ref=None, use_v=False):
    """Transverse distance between an object and a reference point (default: the origin).

    use_v reads the vertex fields (vx, vy) instead of the position fields (x, y).
    """
    if use_v:
        obj_x, obj_y = obj.vx, obj.vy
        ref_x = ref.vx if ref is not None else 0.0
        ref_y = ref.vy if ref is not None else 0.0
    else:
        obj_x, obj_y = obj.x, obj.y
        ref_x = ref.x if ref is not None else 0.0
        ref_y = ref.y if ref is not None else 0.0
    return np.sqrt((obj_x - ref_x) ** 2 + (obj_y - ref_y) ** 2)


def lxy(obj):
    """Transverse decay length of generator particles.

    Measured from the particle's own vertex (where it was produced) to the vertex of
    its first daughter (where it decayed). The particle's own vx/vy alone is the
    production point and is not a decay length.
    """
    return rho(obj, ak.firsts(obj.children, axis=2), use_v=True)


def check_bit(array, bit):
    """True where bit number ``bit`` of an integer flag word is set."""
    return (array & (1 << bit)) > 0


def check_bits(array, bits):
    """True where every listed bit of an integer flag word is set."""
    result = check_bit(array, bits[0])
    for bit in bits[1:]:
        result = result & check_bit(array, bit)
    return result


def leading(obj, n=1):
    """The n highest-pT objects of each event (collections are already pT ordered)."""
    return obj[:, :n]


def pairs(obj):
    """All unordered pairs of objects in each event, as a record with fields '0' and '1'."""
    return ak.combinations(obj, 2, axis=1)
