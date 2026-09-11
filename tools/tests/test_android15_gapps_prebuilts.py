#!/usr/bin/env python3

import pathlib
import subprocess
import sys
import tempfile
import unittest
import zipfile


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "tools" / "check-gapps-prebuilts.py"


class Android15GappsPrebuiltsTest(unittest.TestCase):
    def run_checker(self, apk: pathlib.Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), str(apk)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_accepts_materialized_apk_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            apk = pathlib.Path(temp_dir) / "GmsCore.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"fixture")

            result = self.run_checker(apk)

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_git_lfs_pointer_in_place_of_apk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            apk = pathlib.Path(temp_dir) / "GmsCore.apk"
            apk.write_text(
                "version https://git-lfs.github.com/spec/v1\n"
                "oid sha256:38eb121872076788b3e2cba8006032ca4eaee569514403cf159d49f47a526d4d\n"
                "size 168192577\n",
                encoding="utf-8",
            )

            result = self.run_checker(apk)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Git LFS pointer", result.stderr)


if __name__ == "__main__":
    unittest.main()
