"""扫一遍 DiaData CSV 并落盘窗口缓存；评测时直接读缓存。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from glucose_forecast.data import diadata


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    started = time.perf_counter()
    cache = diadata.load_or_build(max_windows_per_subject=limit)
    path = diadata.cache_path(
        history_points=72, horizon_points=12, max_windows_per_subject=limit
    )
    diadata.save_cache(cache, path)
    elapsed = time.perf_counter() - started
    subjects = {str(item) for item in cache.subject}
    print(
        f"\nsubjects={len(subjects):,}  windows={len(cache.history):,}  "
        f"history={cache.history.shape}  elapsed={elapsed/60:.1f} min\ncache={path} "
        f"({Path(path).stat().st_size/1e6:.1f} MB)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
