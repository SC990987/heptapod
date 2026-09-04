# When to use dask instead

The HTCondor path in this skill is a **batch** model: submit, poll, merge. It
suits production over a whole dataset, is resumable, and needs nothing
installed beyond what is already here.

A **dask** cluster suits a different job: interactive exploration where you want
a live cluster, a progress bar, and the ability to re-run a computation without
resubmitting.

## What it needs

dask, distributed and `lpcjobqueue` are **not** installed in the toolkit
environment:

```
import dask          -> ModuleNotFoundError
import lpcjobqueue   -> ModuleNotFoundError
```

`lpcjobqueue` is the LPC-specific wrapper around `HTCondorCluster`. It is
designed to run **inside** the coffea-dask container, launched from a login
node:

```bash
curl -OL https://raw.githubusercontent.com/CoffeaTeam/lpcjobqueue/main/bootstrap.sh
bash bootstrap.sh
./shell                       # drops you inside the container
```

then, in Python:

```python
from lpcjobqueue import LPCCondorCluster
from distributed import Client

cluster = LPCCondorCluster(cores=1, memory="4GB", disk="10GB")
cluster.adapt(minimum=1, maximum=50)
client = Client(cluster)
```

and drive coffea's dask-aware execution from there.

## Choosing

| Situation | Use |
|-----------|-----|
| Run a finished analysis over a full sample | HTCondor (`lpc_scaleout.py`) |
| Unattended, resumable, hours-long | HTCondor |
| Agent submits and polls without holding a session | HTCondor |
| Interactive exploration, live cluster, quick re-runs | dask |
| You need dask-aware coffea (`delayed=True`) | dask |

The batch path is the safer default for agentic use: nothing is lost if the
session ends, and progress is a file count on disk rather than cluster state
held in memory.
