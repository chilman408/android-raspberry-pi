#!/usr/bin/env python3

import argparse
import pathlib
import sys
import zipfile


GIT_LFS_POINTER_HEADER = b"version https://git-lfs.github.com/spec/v1"


def check_apk(path: pathlib.Path) -> str | None:
    if not path.is_file():
        return f"missing required GApps APK: {path}"

    with path.open("rb") as apk:
        header = apk.read(len(GIT_LFS_POINTER_HEADER))
    if header == GIT_LFS_POINTER_HEADER:
        return f"Git LFS pointer found instead of an APK: {path}"
    if not zipfile.is_zipfile(path):
        return f"GApps prebuilt is not a valid APK archive: {path}"

    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail if required GApps APKs were not materialized by Git LFS."
    )
    parser.add_argument("apk", nargs="+", type=pathlib.Path)
    args = parser.parse_args()

    errors = [error for path in args.apk if (error := check_apk(path))]
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"Verified {len(args.apk)} materialized GApps APK(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
