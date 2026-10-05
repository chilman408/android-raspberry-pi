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
EXPORTER_PATCH = (
    REPO_ROOT
    / "patches-aosp"
    / "glodroid"
    / "configuration"
    / "0028-base-Export-GSF-ID-to-Settings.patch"
)
PORT_GATE = REPO_ROOT / "tools" / "check-android15-port.sh"

REGISTRATION_URL = "https://www.google.com/android/uncertified/"
GSF_ID_PROPERTY = "sys.tesla_android.gsf_id"
GSF_ID_READY_PROPERTY = "sys.tesla_android.gsf_id_ready"
GSF_ID_REFRESH_PROPERTY = "sys.tesla_android.gsf_id_refresh"
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
            "Refresh GSF Android ID",
            "Copy GSF Android ID",
            "Open Google registration page",
            REGISTRATION_URL,
            GSF_ID_PROPERTY,
            GSF_ID_READY_PROPERTY,
            GSF_ID_REFRESH_PROPERTY,
            "GsfIdReader",
            "GsfIdReaderTest",
            "Not available yet",
            "Refreshing GSF Android ID",
            "SystemProperties",
            "isRefreshComplete",
        ):
            with self.subTest(value=value):
                self.assertIn(value, patch)

        self.assertIn("GsfRegistrationFragment", patch)
        self.assertNotIn("Checkin.xml", patch)
        self.assertNotIn("/data/user/", patch)
        self.assertNotIn("/data/data/", patch)
        self.assertNotIn("content://com.google.android.gsf.gservices", patch)
        self.assertNotIn(
            "com.google.android.providers.gsf.permission.READ_GSERVICES", patch
        )
        self.assertNotIn(ANDROID_ID_ACTION, patch)
        self.assertNotIn('setPackage("com.google.android.gms")', patch)
        self.assertIn("Google\\'s registration page", patch)
        self.assertIn("app\\'s App info page", patch)

    def test_exporter_is_hard_coded_read_only_and_one_shot(self):
        patch = self.read_patch(EXPORTER_PATCH)

        for value in (
            "gsf_id_exporter",
            "gsf-id-exporter",
            "/data/user/0/com.google.android.gms/databases/gservices.db",
            '"android_id"',
            "SQLITE_OPEN_READONLY",
            'SELECT value FROM \\"main\\" WHERE name = ?1 LIMIT 1',
            "libsqlite",
            "system_ext_specific: true",
            "disabled",
            "oneshot",
            "sys.boot_completed=1",
            GSF_ID_PROPERTY,
            GSF_ID_READY_PROPERTY,
            GSF_ID_REFRESH_PROPERTY,
            "init_daemon_domain(gsf_id_exporter)",
            "mlstrustedsubject",
            "allow gsf_id_exporter privapp_data_file:dir r_dir_perms",
            "allow gsf_id_exporter privapp_data_file:file r_file_perms",
            "set_prop(gsf_id_exporter, tesla_gsf_id_prop)",
            "get_prop(system_app, tesla_gsf_id_prop)",
            "set_prop(system_app, tesla_gsf_id_refresh_prop)",
        ):
            with self.subTest(value=value):
                self.assertIn(value, patch)

        self.assertRegex(patch, r"(?m)^\+int main\(\) \{$")
        self.assertNotRegex(patch, r"(?m)^\+int main\([^)]*(?:argc|argv)")
        self.assertNotIn("SQLITE_OPEN_READWRITE", patch)
        self.assertNotIn("SQLITE_OPEN_CREATE", patch)
        self.assertNotIn("privapp_data_file:lnk_file", patch)
        self.assertNotIn("persist.tesla_android.gsf", patch)
        self.assertNotRegex(patch, r"(?m)^\+.*(?:popen|system\(|/system/bin/sh)")

        changed_paths = set(
            re.findall(r"(?m)^diff --git a/(\S+) b/\S+$", patch)
        )
        self.assertTrue(changed_paths)
        self.assertTrue(all(path.startswith("common/base/") for path in changed_paths))
        self.assertFalse(any("lighttpd" in path for path in changed_paths))

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
            "Copy GSF Android ID",
            "Refresh GSF Android ID",
            "Not available yet",
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
            ("main.dart.js", "7a5ceb3cad983af6f4636d9875b16437"),
            (
                "beta/js/release-notes/release-notes-data.js",
                "c9d75d45629edf891192e6e8dab08652",
            ),
        ):
            with self.subTest(asset=asset):
                self.assertIn(f'"{asset}": "{digest}"', patch)

    def test_port_gate_runs_registration_contract(self):
        gate = PORT_GATE.read_text(encoding="utf-8")
        self.assertIn("test_android15_gsf_registration_ui.py", gate)
        self.assertIn(SETTINGS_PATCH.relative_to(REPO_ROOT).as_posix(), gate)
        self.assertIn(RELEASE_NOTES_PATCH.relative_to(REPO_ROOT).as_posix(), gate)
        self.assertIn(EXPORTER_PATCH.relative_to(REPO_ROOT).as_posix(), gate)


if __name__ == "__main__":
    unittest.main()
