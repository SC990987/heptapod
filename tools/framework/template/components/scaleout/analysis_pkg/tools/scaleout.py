"""Scaling out: dask clients for a laptop, an existing cluster, or HTCondor at the LPC.

The processor itself does not change with scale. What changes is the executor handed
to coffea's Runner:

    processor.IterativeExecutor()                      one process, for debugging
    processor.FuturesExecutor(workers=8)               local processes
    processor.DaskExecutor(client=client)              a dask cluster, from one of:

        client = scaleout.make_local_client(n_workers=8)
        client = scaleout.make_dask_client("tls://scheduler:8786")
        cluster, client = scaleout.make_lpc_client(max_workers=50)

Workers must be able to import this package. Local processes share your environment.
Remote workers get the package directory shipped to them by ``upload_package``, which
``make_lpc_client`` and ``make_dask_client`` call for you: your working tree, including
uncommitted edits, is what the workers run.

What is shipped is the tree as it is at that moment. After editing the package while
a cluster is up, ship it again and have the workers start afresh, since python does
not re-import what it has already loaded:

    scaleout.upload_package(client, restart=True)
"""

import glob
import io
import os
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path

from analysis_pkg import BASE_DIR

_PACKAGE_DIR = Path(BASE_DIR)
_REPO_ROOT = _PACKAGE_DIR.parent
_DEFAULT_LPC_CONFIG = _REPO_ROOT / "condor" / "lpc_condor_config"
_LPC_IMAGE_DIR = "/cvmfs/unpacked.cern.ch/registry.hub.docker.com/coffeateam"
_PROXY_RENEW_CMD = "voms-proxy-init --valid 192:00 -voms cms"

# What is NOT shipped to workers. They import tools/, definitions/ and scripts/ and
# read configs/ and data/; they never open a notebook. The upload is zipped in memory
# and the scheduler, which runs in *this* process, keeps copies and sends one to every
# worker, so the peak cost here is roughly (2 + n_workers) x payload. Notebooks with
# stored outputs are easily hundreds of MB.
#
# distributed matches these words against every component of the ABSOLUTE path, so a
# checkout that lives under a directory called "studies" or "docs" would match every
# file. upload_package therefore checks what actually ended up in the payload.
_UPLOAD_SKIP_WORDS = (
    ".git", ".github", ".pytest_cache", "tests", "docs",
    "studies", "test_notebooks", "__pycache__", ".ipynb_checkpoints",
)
_UPLOAD_SKIP_EXTS = (".pyc", ".ipynb")
# Saved outputs (.coffea) are not shipped either, wherever they lie, except under
# data/: that is where a lookup table in that format would be kept.
_UPLOAD_OUTPUT_EXT = ".coffea"
_UPLOAD_PEAK_BUDGET_BYTES = 512 * 1024 ** 2
_UPLOAD_SENTINEL = "tools/processor.py"


def make_local_client(n_workers=4, threads_per_worker=1, memory_limit="4GB", **kwargs):
    """A dask cluster of local processes. Returns the Client; ``client.close()`` when done."""
    from dask.distributed import Client

    return Client(n_workers=n_workers, threads_per_worker=threads_per_worker,
                  memory_limit=memory_limit, **kwargs)


def _skip_upload(filename, package_dir=_PACKAGE_DIR):
    """Should this file (given by its absolute path) be left out of the upload?"""
    extension = os.path.splitext(filename)[1]
    if extension in _UPLOAD_SKIP_EXTS:
        return True
    if extension == _UPLOAD_OUTPUT_EXT:
        data_dir = os.path.join(os.path.abspath(str(package_dir)), "data") + os.sep
        return not os.path.abspath(filename).startswith(data_dir)
    return False


def build_upload_plugin(package_dir=_PACKAGE_DIR, max_workers=1, restart=False):
    """The dask plugin that ships the package directory to workers.

    restart: restart the workers once they have the files. Needed when shipping
        again after an edit; a new cluster has nothing loaded yet.

    Raises if the payload would not contain the analysis code, and warns when its
    projected memory cost in this process is large.
    """
    from distributed.diagnostics.plugin import UploadDirectory

    package_dir = Path(package_dir)
    plugin = UploadDirectory(
        str(package_dir),
        restart_workers=bool(restart),
        skip_words=_UPLOAD_SKIP_WORDS,
        skip=(lambda filename: _skip_upload(filename, package_dir),),
    )
    names = zipfile.ZipFile(io.BytesIO(plugin.data)).namelist()
    if f"{package_dir.name}/{_UPLOAD_SENTINEL}" not in names:
        raise RuntimeError(
            f"the upload of {package_dir} does not contain {package_dir.name}/{_UPLOAD_SENTINEL} "
            f"({len(names)} files): workers would fail with ModuleNotFoundError. Either the "
            f"path is wrong or one of its parent directories is named like an entry of "
            f"{_UPLOAD_SKIP_WORDS}, which distributed matches against the whole absolute path."
        )
    projected = len(plugin.data) * (max_workers + 2)
    if projected > _UPLOAD_PEAK_BUDGET_BYTES:
        warnings.warn(
            f"the upload of {package_dir} is {len(plugin.data) / 1024 ** 2:.0f} MB; with "
            f"max_workers={max_workers} the scheduler in this process will hold about "
            f"{projected / 1024 ** 2:.0f} MB. Remove large files from the package directory, "
            f"extend scaleout._UPLOAD_SKIP_WORDS, or lower max_workers.",
            RuntimeWarning,
            stacklevel=2,
        )
    return plugin


def upload_package(client, package_dir=_PACKAGE_DIR, max_workers=1, restart=False):
    """Ship the package directory to every current and future worker of a client.

    The tree is read when this is called. Call it again with ``restart=True`` after
    editing the package: the workers then get the new files and start afresh.
    """
    plugin = build_upload_plugin(package_dir, max_workers, restart)
    client.register_plugin(plugin)
    return plugin


def make_dask_client(address, upload=True, pip_packages=None, **kwargs):
    """Connect to an existing dask scheduler (an analysis facility, a cluster you started).

    upload: ship this package directory to the workers
    pip_packages: instead (or as well), have the workers pip install these, e.g.
        ``["git+https://github.com/you/your-analysis.git"]``
    """
    from dask.distributed import Client

    client = Client(address, **kwargs)
    if pip_packages:
        from dask.distributed import PipInstall
        client.register_plugin(PipInstall(packages=list(pip_packages),
                                          pip_options=["--upgrade", "--no-cache-dir"]))
    if upload:
        upload_package(client, max_workers=len(client.scheduler_info().get("workers", {})) or 1)
    return client


def check_voms_proxy(min_seconds_left=3600):
    """Check that a VOMS proxy exists and is not about to expire; return its path.

    Looks at $X509_USER_PROXY (or /tmp/x509up_u<UID>), raises RuntimeError with the
    renewal command if it is missing or has less than ``min_seconds_left`` to live, and
    exports X509_USER_PROXY so that the cluster and its jobs use the same file.
    """
    proxy = os.environ.get("X509_USER_PROXY") or f"/tmp/x509up_u{os.getuid()}"
    if not os.path.isfile(proxy):
        raise RuntimeError(f"no VOMS proxy at {proxy}. Create one with:\n  {_PROXY_RENEW_CMD}")
    result = subprocess.run(["voms-proxy-info", "-file", proxy, "-timeleft"],
                            capture_output=True, text=True, check=False)
    try:
        remaining = int(result.stdout.strip())
    except ValueError:
        raise RuntimeError(
            f"voms-proxy-info could not read {proxy} (stdout={result.stdout!r}, "
            f"stderr={result.stderr!r}). Renew it with:\n  {_PROXY_RENEW_CMD}"
        ) from None
    if remaining < min_seconds_left:
        raise RuntimeError(
            f"the VOMS proxy at {proxy} has {remaining / 3600.0:.1f} h left (need "
            f"{min_seconds_left / 3600.0:.1f} h). Renew it with:\n  {_PROXY_RENEW_CMD}"
        )
    os.environ["X509_USER_PROXY"] = proxy
    return proxy


def find_lpc_image(image_dir=_LPC_IMAGE_DIR):
    """Worker image on cvmfs whose coffea and python versions match this environment.

    Workers must run the same coffea as the process that submits to them. Raises with
    the list of available images when there is no match, so one can be passed explicitly.
    """
    import platform

    import coffea

    python = ".".join(platform.python_version_tuple()[:2])
    version = coffea.__version__
    # image tags spell release candidates as 2025.5.0.rc2, python packages as 2025.5.0rc2
    spellings = {version, version.replace("rc", ".rc")}
    # The images with coffea, dask and dask-awkward are called coffea-dak-<distro>;
    # coffea-dask-<distro> is their earlier name, still published for older tags.
    candidates = sorted(
        candidate for stem in ("coffea-dak-almalinux", "coffea-dask-almalinux")
        for candidate in glob.glob(os.path.join(image_dir, f"{stem}*:*-py{python}")))
    matches = [c for c in candidates
               if c.rsplit(":", 1)[1].rsplit("-py", 1)[0] in spellings]
    if not matches:
        available = [os.path.basename(c) for c in candidates] or ["(none found)"]
        raise RuntimeError(
            f"no worker image for coffea {version} / python {python} under {image_dir}. "
            f"Pass image=... explicitly. Images for this python: {available}"
        )
    # the current name before the earlier one, then the newest distribution; the plain
    # image sorts after its variants ("almalinux9:" > "almalinux9-eaf:")
    return max(matches, key=lambda c: (os.path.basename(c).startswith("coffea-dak-"), c))


def make_lpc_client(min_workers=1, max_workers=10, memory="4GB", disk="4GB", cores=1,
                    death_timeout=600, image=None, upload=True,
                    condor_config=_DEFAULT_LPC_CONFIG, dashboard_port=None, **cluster_kwargs):
    """An LPCCondorCluster and its Client, for scaling out from cmslpc.

    Workers are HTCondor jobs running inside a coffea-dask apptainer image; this
    process (a notebook, usually) stays in your own environment. Needs
    ``pip install "htcondor<25" git+https://github.com/CoffeaTeam/lpcjobqueue.git``
    and a valid VOMS proxy.

    min_workers, max_workers: bounds for the adaptive cluster
    memory, disk, cores: resources requested per worker
    death_timeout: seconds a worker waits for the scheduler before giving up; the
        condor queue can keep workers idle for minutes
    image: apptainer image for the workers. By default the image on cvmfs that matches
        the local coffea and python versions (see find_lpc_image)
    upload: ship this package directory to the workers (the image does not contain it)
    condor_config: CONDOR_CONFIG to use; the default is condor/lpc_condor_config in
        this repository
    dashboard_port: pin the dask dashboard to a port, to match an ssh tunnel
        (``ssh -L <port>:localhost:<port>``); if it is taken, dask picks another and
        ``cluster.dashboard_link`` shows which
    cluster_kwargs: passed on to LPCCondorCluster

    Returns ``(cluster, client)``. Call ``cluster.close()`` when done.
    """
    import dask
    from dask.distributed import Client

    check_voms_proxy()
    if image is None:
        image = find_lpc_image()

    if not Path(condor_config).is_file():
        raise FileNotFoundError(
            f"HTCondor configuration not found: {condor_config}. It is kept next to the "
            "package (condor/lpc_condor_config), so work from the repository with the "
            "package installed editable (pip install -e .), or pass condor_config=...")
    # htcondor reads its configuration when it is first imported, so the variable has
    # to be set before lpcjobqueue pulls it in
    if "htcondor" in sys.modules and os.environ.get("CONDOR_CONFIG") != str(condor_config):
        warnings.warn(
            "htcondor was imported before make_lpc_client could point it at "
            f"{condor_config}: it keeps the configuration it read then. If no workers "
            "arrive, restart python and call make_lpc_client first.",
            RuntimeWarning, stacklevel=2)
    os.environ["CONDOR_CONFIG"] = str(condor_config)
    from lpcjobqueue import LPCCondorCluster

    # lpcjobqueue points the dashboard link at a jupyterhub proxy route. Outside a
    # jupyterhub, make it match a plain ssh tunnel instead.
    if "JUPYTERHUB_SERVICE_PREFIX" not in os.environ:
        dask.config.set({"distributed.dashboard.link": "{scheme}://localhost:{port}/status"})
    if dashboard_port is not None:
        cluster_kwargs["scheduler_options"] = {
            "dashboard_address": f":{dashboard_port}",
            **(cluster_kwargs.pop("scheduler_options", None) or {}),
        }

    cluster = LPCCondorCluster(memory=memory, disk=disk, cores=cores,
                               death_timeout=death_timeout, image=image, ship_env=False,
                               **cluster_kwargs)
    cluster.adapt(minimum=min_workers, maximum=max_workers)
    client = Client(cluster)
    if upload:
        upload_package(client, max_workers=max_workers)
    return cluster, client
