"""Smoke test: read the first few DiaData subjects and show what came out."""

from __future__ import annotations

import sys

import numpy as np

from glucose_forecast.data import diadata


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    total = 0
    for subject, database, frame in diadata.iter_subject_frames(
        limit_subjects=limit
    ):
        grid = diadata.grid_from_frame(subject, frame)
        values = grid.valid_values()
        print(
            f"{subject:<16} db={database:<10} rows={len(frame):>7,} "
            f"grid={len(grid):>7,} coverage={grid.coverage:.2f} "
            f"mgdl={values.min():.0f}-{values.max():.0f} mean={values.mean():.0f} "
            f"span={(grid.timestamps[-1] - grid.timestamps[0]) / np.timedelta64(1, 'D'):.1f}d"
        )
        total += len(frame)
    print(f"read {total:,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
