#!/usr/bin/env python3

import pathlib
import re
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SETTINGS_PATCH = (
    REPO_ROOT
    / "patches-aosp"
    / "packages"
    / "apps"
    / "Settings"
    / "0006-Add-Google-device-registration-page.patch"
)
RELEASE_NOTES_PATCH = (
    REPO_ROOT
    / "patches-aosp"
    / "vendor"
    / "tesla-android"
    / "0003-Document-GSF-device-registration.patch"
)
PORT_GATE = REPO_ROOT / "tools" / "check-android15-port.sh"

REGISTRATION_URL = "https://www.google.com/android/uncertified/"
ANDROID_ID_ACTION = "com.google.android.gms.permissions.ANDROID_ID_DEBUG_ACTIVITY"


class Android15GsfRegistrationUiTest(unittest.TestCase):
    def read_patch(self, path):
        self.assertTrue(path.is_file(), f"missing required patch: {path}")
        return path.read_text(encoding="utf-8")

    def test_settings_adds_local_registration_page(self):
        patch = self.read_patch(SETTINGS_PATCH)

        for value in (
            "Google Play registration",
            "GSF Android ID",
            "Show GSF Android ID",
            "Open Google registration page",
            REGISTRATION_URL,
            ANDROID_ID_ACTION,
            'setPackage("com.google.android.gms")',
            "Google Play services ID screen is unavailable",
        ):
            with self.subTest(value=value):
                self.assertIn(value, patch)

        self.assertIn("GsfRegistrationFragment", patch)
        self.assertNotIn("Checkin.xml", patch)
        self.assertNotIn("/data/user/", patch)
        self.assertNotIn("/data/data/", patch)
        self.assertNotIn("READ_GSERVICES", patch)

    def test_id_is_not_exposed_through_teslaandroid_web_assets(self):
        settings_paths = set(
            re.findall(r"(?m)^diff --git a/(\S+) b/\S+$", self.read_patch(SETTINGS_PATCH))
        )
        for path in settings_paths:
            with self.subTest(path=path):
                self.assertNotIn("services/lighttpd/www-default", path)

    def test_release_notes_include_observed_registration_recovery_steps(self):
        patch = self.read_patch(RELEASE_NOTES_PATCH)

        for value in (
            "Google Device Registration",
            "Settings > About device > Google Play registration",
            REGISTRATION_URL,
            "Wait 24 hours",
            "Storage & cache > Clear storage",
            "Google Play Store or YouTube",
            "Do not clear Google Play services or Google Services Framework",
            "new GSF Android ID",
        ):
            with self.subTest(value=value):
                self.assertIn(value, patch)

        changed_paths = set(
            re.findall(r"(?m)^diff --git a/(\S+) b/\S+$", patch)
        )
        self.assertTrue(
            {
                "services/lighttpd/www-default/main.dart.js",
                "services/lighttpd/www-default/beta/js/release-notes/release-notes-data.js",
                "services/lighttpd/www-default/flutter_service_worker.js",
            }.issubset(changed_paths)
        )
        self.assertNotRegex(patch, r"(?m)^[-+].*ro\.(?:product|build)\.")

        for asset, digest in (
            ("main.dart.js", "ac7f90a4b15cad4fe0dbd056fc3a637a"),
            (
                "beta/js/release-notes/release-notes-data.js",
                "16cdbcb445b1292783243a1bcda67934",
            ),
        ):
            with self.subTest(asset=asset):
                self.assertIn(f'"{asset}": "{digest}"', patch)

    def test_port_gate_runs_registration_contract(self):
        gate = PORT_GATE.read_text(encoding="utf-8")
        self.assertIn("test_android15_gsf_registration_ui.py", gate)
        self.assertIn(SETTINGS_PATCH.relative_to(REPO_ROOT).as_posix(), gate)
        self.assertIn(RELEASE_NOTES_PATCH.relative_to(REPO_ROOT).as_posix(), gate)


if __name__ == "__main__":
    unittest.main()
