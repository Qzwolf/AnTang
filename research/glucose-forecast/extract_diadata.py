"""Unpack the DiaData 5-minute archive one level at a time.

Level 1: DiaData-5min.zip            -> 5_min_sampling/preprocessed.zip
Level 2: preprocessed.zip            -> preprocessed/MDB_5min_sampling_preprocessed.zip
Level 3: MDB_..._preprocessed.zip    -> the per-subject CSV files

Only the MDB bundle (all 1720 subjects, columns ts / PtID / GlucoseCGM / Database)
is unpacked; the other bundles stay inside their zip until we need them.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from glucose_forecast.paths import data_root

ROOT = data_root()
ARCHIVE = ROOT / "DiaData-5min.zip"
STAGE = ROOT / "5min"
PREPROCESSED = STAGE / "preprocessed.zip"
MDB_ZIP = STAGE / "MDB_5min_sampling_preprocessed.zip"
MDB_DIR = STAGE / "MDB"

PREPROCESSED_MEMBER = "5_min_sampling/preprocessed.zip"
MDB_MEMBER = "preprocessed/MDB_5min_sampling_preprocessed.zip"


def extract_member(archive: Path, member: str, target: Path) -> Path:
    if target.exists() and target.stat().st_size > 0:
        print(f"skip (exists): {target}  {target.stat().st_size:,} bytes", flush=True)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"extracting {member} -> {target}", flush=True)
    with zipfile.ZipFile(archive) as zf:
        with zf.open(member) as src, target.open("wb") as dst:
            while True:
                block = src.read(8 << 20)
                if not block:
                    break
                dst.write(block)
    print(f"done: {target}  {target.stat().st_size:,} bytes", flush=True)
    return target


def main() -> None:
    extract_member(ARCHIVE, PREPROCESSED_MEMBER, PREPROCESSED)
    extract_member(PREPROCESSED, MDB_MEMBER, MDB_ZIP)

    with zipfile.ZipFile(MDB_ZIP) as zf:
        members = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX")]
        print(f"\nMDB bundle: {len(members)} files, {sum(i.file_size for i in members):,} bytes total")
        for info in members[:5]:
            print(f"  {info.file_size:>14,}  {info.filename}")
        if len(members) > 5:
            print(f"  ... {len(members) - 5} more")

        MDB_DIR.mkdir(parents=True, exist_ok=True)
        unpacked = 0
        for info in members:
            name = Path(info.filename).name
            if not name or name.startswith("."):
                continue
            target = MDB_DIR / name
            if target.exists() and target.stat().st_size == info.file_size:
                continue
            zf.extract(info, MDB_DIR)
            unpacked += 1
        print(f"\nunpacked {unpacked} files into {MDB_DIR}")


if __name__ == "__main__":
    main()
