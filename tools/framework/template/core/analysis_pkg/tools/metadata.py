"""Sidecar metadata for saved outputs.

``write_run_metadata`` writes a ``.meta.yaml`` file next to a ``.coffea`` output that
records what produced it: the definition of every selection that was run, the
histograms in every collection, the input files and cross section of every sample,
the git commit of the analysis code, the coffea version, and a UTC timestamp.
``write_merge_metadata`` does the same for an output made by merging others.
``load_run_metadata`` reads either back.

An output file on its own does not say which cuts made it. The sidecar does.
"""

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from analysis_pkg import BASE_DIR
from analysis_pkg.tools import utilities


def write_run_metadata(coffea_path, *, fileset, channels, hist_collections=(), schema=None,
                       chunksize=None, unweighted_hist=None, extra=None,
                       selections_cfg="configs/selections.yaml",
                       histograms_cfg="configs/hist_collections.yaml"):
    """Write the ``.meta.yaml`` sidecar describing what produced ``coffea_path``.

    coffea_path: path of the output (``foo.coffea`` gives ``foo.meta.yaml``)
    fileset: the fileset that was processed
    channels, hist_collections: the names the processor was built with
    schema, chunksize, unweighted_hist: optional run settings worth recording
    extra: any further fields to store (a batch job id, a dashboard address, ...)

    Returns the path of the sidecar.
    """
    import coffea

    selections = utilities.load_yaml(utilities.resolve_path(selections_cfg, ""))
    hist_menu = utilities.load_yaml(utilities.resolve_path(histograms_cfg, ""))

    samples = []
    for name, info in fileset.items():
        files = list(info.get("files", [])) if isinstance(info, dict) else list(info)
        try:
            xsec_pb = utilities.get_xs(name)
        except Exception:
            xsec_pb = None
        samples.append({
            "name": name,
            "n_files": len(files),
            "xsec_pb": xsec_pb,
            "metadata": _plain(info.get("metadata", {})) if isinstance(info, dict) else {},
            "files": [str(f) for f in files],
        })

    meta = {
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coffea_path": str(coffea_path),
        "n_samples": len(samples),
        "selections": [
            {"name": channel, "definition": _plain(selections.get(channel, "<not found>"))}
            for channel in channels
        ],
        "hist_collections": [
            {"name": collection, "hists": utilities.flatten(hist_menu.get(collection))}
            for collection in hist_collections
        ],
        "schema": schema if schema is None or isinstance(schema, str) else schema.__name__,
        "chunksize": chunksize,
        "unweighted_hist": unweighted_hist,
        "code_commit": _git_rev(Path(BASE_DIR).parent),
        "coffea_version": coffea.__version__,
        "samples": samples,
    }
    if extra:
        meta.update(_plain(extra))

    sidecar = sidecar_path(coffea_path)
    with open(sidecar, "w", encoding="utf8") as handle:
        yaml.safe_dump(meta, handle, sort_keys=False, default_flow_style=False, width=200)
    return sidecar


def write_merge_metadata(coffea_path, inputs, extra=None):
    """Write the sidecar of an output that was made by merging other outputs.

    coffea_path: path of the merged output
    inputs: paths of the outputs that were merged; the sidecar of each one is copied
        in when it exists, so the merged file still says what produced it
    extra: any further fields to store

    Returns the path of the sidecar.
    """
    import coffea

    merged_from = []
    for path in inputs:
        try:
            own = load_run_metadata(path)
        except Exception:
            own = None
        merged_from.append({"path": str(path), "metadata": own})
    meta = {
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coffea_path": str(coffea_path),
        "n_merged": len(merged_from),
        "code_commit": _git_rev(Path(BASE_DIR).parent),
        "coffea_version": coffea.__version__,
        "merged_from": merged_from,
    }
    if extra:
        meta.update(_plain(extra))

    sidecar = sidecar_path(coffea_path)
    with open(sidecar, "w", encoding="utf8") as handle:
        yaml.safe_dump(meta, handle, sort_keys=False, default_flow_style=False, width=200)
    return sidecar


def load_run_metadata(coffea_path):
    """Load the ``.meta.yaml`` sidecar that sits next to ``coffea_path``."""
    return utilities.load_yaml(sidecar_path(coffea_path))


def sidecar_path(coffea_path):
    """Path of the sidecar belonging to an output file."""
    path = str(coffea_path)
    if path.endswith(".coffea"):
        path = path[: -len(".coffea")]
    return path + ".meta.yaml"


def _git_rev(repo_root):
    """Current commit of the analysis repository, with '+dirty' if there are local edits."""
    try:
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root),
                             capture_output=True, text=True, timeout=5)
        if rev.returncode != 0:
            return None
        status = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo_root),
                                capture_output=True, text=True, timeout=5)
        dirty = "+dirty" if status.returncode == 0 and status.stdout.strip() else ""
        return rev.stdout.strip() + dirty
    except Exception:
        return None


def _plain(obj):
    """Copy a structure into plain dicts, lists and scalars that yaml can dump safely."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_plain(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)
