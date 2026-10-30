"""Where the datasets live.

Nothing in this package ships data: every loader takes an explicit ``--root`` or
falls back to ``GLUCOSE_DATA_ROOT`` (default ``./data/glucose``). The datasets
(T1D-UOM, DiaData, HUPA-UCM, OhioT1DM) each carry their own licence and are
downloaded or applied for separately — see README.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_root() -> Path:
    """Root directory holding the downloaded datasets."""
    return Path(os.environ.get("GLUCOSE_DATA_ROOT", "data/glucose"))


def dataset_dir(name: str) -> Path:
    """A named dataset under :func:`data_root`."""
    return data_root() / name
