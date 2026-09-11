#!/usr/bin/env python3

import pathlib
import subprocess
import sys
import tempfile
import unittest
import zipfile


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "tools" / "check-gapps-prebuilts.py"
GAPPS_PATCH_DIR = REPO_ROOT / "patches-aosp" / "vendor" / "gapps"

GAPPS_ANDROID_BP_FIXTURE = """\
android_app_import {
    name: "GmsCore",
    owner: "gapps",
    apk: "proprietary/product/priv-app/GmsCore/GmsCore.apk",
    preprocessed: true,
    presigned: true,
    dex_preopt: {
        enabled: false,
    },
    privileged: true,
    product_specific: true,
}

android_app_import {
    name: "Velvet",
    owner: "gapps",
    apk: "proprietary/product/priv-app/Velvet/Velvet.apk",
    preprocessed: true,
    presigned: true,
    dex_preopt: {
        enabled: false,
    },
    privileged: true,
    product_specific: true,
}
"""


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

    def test_gapps_patch_stack_keeps_default_soong_apk_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            checkout = pathlib.Path(temp_dir)
            android_bp = checkout / "arm64" / "Android.bp"
            android_bp.parent.mkdir(parents=True)
            android_bp.write_text(GAPPS_ANDROID_BP_FIXTURE, encoding="utf-8")

            for patch in sorted(GAPPS_PATCH_DIR.glob("*.patch")):
                result = subprocess.run(
                    ["git", "apply", str(patch)],
                    cwd=checkout,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            patched_android_bp = android_bp.read_text(encoding="utf-8")

        self.assertNotIn(
            "skip_preprocessed_apk_checks: true",
            patched_android_bp,
            "Materialized GApps APKs must use Soong's default preprocessed APK validation.",
        )


if __name__ == "__main__":
    unittest.main()
