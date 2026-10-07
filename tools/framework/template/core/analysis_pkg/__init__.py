"""__PROJECT_TITLE__

__PROJECT_DESCRIPTION__
"""

import os

# Absolute path of this package: configs/, data/ and friends are found relative to it,
# so the analysis runs the same from a notebook, a script or a batch worker.
BASE_DIR = os.path.dirname(os.path.realpath(__file__))

# Name of the TTree that holds the events in the input files.
TREE_NAME = "__TREE_NAME__"

__version__ = "0.1.0"
