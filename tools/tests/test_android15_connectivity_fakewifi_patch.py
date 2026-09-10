#!/usr/bin/env python3

import pathlib
import re
import subprocess
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = (
    REPO_ROOT
    / "patches-aosp"
    / "packages"
    / "modules"
    / "Connectivity"
    / "0004-Connectivity-keep-FakeWifi-helper-inside-module.patch"
)

UPSTREAM_CONNECTIVITY_MANAGER = """package android.net;

import android.net.TetheringManager.StartTetheringCallback;
import android.net.TetheringManager.TetheringEventCallback;
import android.net.TetheringManager.TetheringRequest;
import android.net.wifi.FakeWifi;
import android.os.Binder;
import android.os.Build;
import android.os.Build.VERSION_CODES;
"""


class Android15ConnectivityFakeWifiPatchTest(unittest.TestCase):
    def test_relocated_fakewifi_entry_points_are_public(self):
        """Jarjar may relocate FakeWifi away from ConnectivityManager's package."""
        self.assertTrue(PATCH.is_file(), f"missing Android 15 FakeWifi patch: {PATCH}")

        with tempfile.TemporaryDirectory() as temp_dir:
            checkout = pathlib.Path(temp_dir)
            connectivity_manager = (
                checkout
                / "framework"
                / "src"
                / "android"
                / "net"
                / "ConnectivityManager.java"
            )
            connectivity_manager.parent.mkdir(parents=True)
            connectivity_manager.write_text(
                UPSTREAM_CONNECTIVITY_MANAGER, encoding="utf-8"
            )

            result = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", str(PATCH)],
                cwd=checkout,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"FakeWifi patch does not apply to the Android 15 fixture:\n{result.stderr}",
            )

            helper = connectivity_manager.with_name("FakeWifi.java").read_text(
                encoding="utf-8"
            )
            self.assertRegex(
                helper,
                r"(?m)^public final class FakeWifi\s*\{",
                "FakeWifi must be public because Android 15 jarjar relocates it",
            )

            for method in ("isHackEnabled", "getFakeNetworkInfo", "maybeOverwrite"):
                with self.subTest(method=method):
                    self.assertRegex(
                        helper,
                        rf"(?m)^\s+public static [^\n]+\b{re.escape(method)}\(",
                        f"FakeWifi.{method} must remain callable after jarjar relocation",
                    )


if __name__ == "__main__":
    unittest.main()
