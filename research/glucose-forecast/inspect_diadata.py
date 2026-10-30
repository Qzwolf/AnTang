"""One streaming pass over the DiaData MDB CSV.

Answers the questions the loader needs before it can trust the file:
  * is the file grouped by subject (one contiguous block per PtID)?
  * how many subjects, and how many 5-minute points does each carry?
  * what is the glucose range, how many values are missing?
  * what is the sampling step inside a subject; how big are the gaps?
"""

from __future__ import annotations

import sys
from collections import Counter

import numpy as np
import pandas as pd

from glucose_forecast.data.diadata import DEFAULT_CSV

CSV = DEFAULT_CSV
CHUNK = 4_000_000


def main() -> None:
    counts: Counter[str] = Counter()
    databases: dict[str, str] = {}
    blocks: list[str] = []          # subject order of appearance
    last: str | None = None
    grouped = True
    gmin = np.inf
    gmax = -np.inf
    missing = 0
    steps: Counter[int] = Counter()

    reader = pd.read_csv(
        CSV,
        usecols=["ts", "PtID", "Database", "GlucoseCGM"],
        dtype={"PtID": "string", "Database": "string", "GlucoseCGM": "float64"},
        chunksize=CHUNK,
        engine="c",
    )
    for i, chunk in enumerate(reader):
        ptid = chunk["PtID"].to_numpy()
        databases.update(zip(chunk["PtID"].unique(), chunk["Database"].unique()))
        for subject in chunk["PtID"].unique():
            counts[subject] += int((ptid == subject).sum())
        # grouping check: a subject may not reappear once we have moved on
        seen = set(blocks)
        for subject in pd.unique(ptid):
            if subject in seen and subject != last:
                grouped = False
            if subject not in seen:
                blocks.append(subject)
        last = ptid[-1]

        glucose = chunk["GlucoseCGM"].to_numpy()
        finite = np.isfinite(glucose)
        missing += int((~finite).sum())
        if finite.any():
            gmin = min(gmin, float(glucose[finite].min()))
            gmax = max(gmax, float(glucose[finite].max()))

        ts = pd.to_datetime(chunk["ts"], format="%Y-%m-%dT%H:%M:%S.%f", errors="coerce")
        delta = ts.diff().dt.total_seconds().div(60).dropna()
        step_vals = delta[delta > 0].round().astype(int)
        steps.update(step_vals.tolist())
        print(f"chunk {i:>3}: rows={len(chunk):,} subjects={len(set(blocks)):,}", flush=True)

    sizes = np.array(sorted(counts.values()))
    print("\n== summary ==")
    print(f"rows            : {sum(counts.values()):,}")
    print(f"subjects        : {len(counts):,}")
    print(f"grouped by PtID : {grouped}")
    print(f"databases       : {len(set(databases.values()))}")
    print(f"rows / subject  : min {sizes.min():,}  median {int(np.median(sizes)):,}  max {sizes.max():,}")
    print(f"glucose mg/dL   : {gmin:.1f} - {gmax:.1f}   missing {missing:,}")
    common = steps.most_common(6)
    print(f"step minutes    : {common}")
    print("\n== per database ==")
    per_db: Counter[str] = Counter()
    for subject, n in counts.items():
        per_db[databases.get(subject, "?")] += n
    for name, n in sorted(per_db.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<14} {n:>12,} rows")


if __name__ == "__main__":
    sys.exit(main())
