"""List the members of the DiaData 5-minute archive without extracting everything."""

from __future__ import annotations

import io
import sys
import zipfile

from glucose_forecast.paths import data_root

ARCHIVE = data_root() / "DiaData-5min.zip"


def main() -> None:
    with zipfile.ZipFile(ARCHIVE) as outer:
        print("== outer members ==")
        for info in outer.infolist():
            print(f"{info.file_size:>14,}  {info.filename}")
        nested = [n for n in outer.namelist() if n.endswith(".zip")]
        for name in nested:
            print(f"\n==  {name} ==")
            data = outer.read(name)
            with zipfile.ZipFile(io.BytesIO(data)) as inner:
                total = 0
                for info in inner.infolist():
                    if info.is_dir():
                        continue
                    total += info.file_size
                    if info.file_size > 200_000 or len(inner.infolist()) <= 60:
                        print(f"{info.file_size:>14,}  {info.filename}")
                print(f"{total:>14,}  <sum of {len([i for i in inner.infolist() if not i.is_dir()])} files>")
    print(f"\narchive bytes: {ARCHIVE.stat().st_size:,}", file=sys.stderr)


if __name__ == "__main__":
    main()
