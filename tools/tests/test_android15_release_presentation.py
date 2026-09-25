#!/usr/bin/env python3

import html
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RELEASE_PATCH = (
    REPO_ROOT
    / "patches-aosp"
    / "vendor"
    / "tesla-android"
    / "0002-release-Brand-Android-15-work-week-39.patch"
)
MANIFEST = REPO_ROOT / "manifests" / "tesla-android.xml"
PORT_GATE = REPO_ROOT / "tools" / "check-android15-port.sh"
JENKINS_PIPELINE = REPO_ROOT / "jenkins" / "multi-branch-ci.groovy"

VENDOR_REVISION = "6e139bf41585188308e053dcbb34855f644aedba"
RELEASE_VERSION = "2026.39.a15.1"
PROJECT_URL = "https://github.com/chilman408/TeslaAndroid-R15-Rpi4"
DONATION_COPY = (
    "This is a self funded project, your contribution means the world to me!"
)
DONATION_URL = (
    "https://www.paypal.com/donate/?business=TFWNLUC7ZGSPS&no_recurring=0"
    "&item_name=Thank+you+for+your+support%21++I+appreciate+it."
    "&currency_code=USD"
)
OLD_ABOUT = "Tesla Android (2nd generation) hardware is now available."
OLD_DONATION_COPY = (
    "Thank you for considering a donation to support the ongoing development "
    "of Tesla Android. As a community-founded project, your contribution can "
    "truly make a difference!"
)


class Android15ReleasePresentationTest(unittest.TestCase):
    def release_patch(self):
        self.assertTrue(
            RELEASE_PATCH.is_file(),
            f"missing Android 15 release presentation patch: {RELEASE_PATCH}",
        )
        return RELEASE_PATCH.read_text(encoding="utf-8")

    def test_manifest_pins_release_patch_vendor_revision(self):
        projects = {
            project.get("path"): project.get("revision")
            for project in ET.parse(MANIFEST).getroot().findall("project")
        }
        self.assertEqual(projects.get("vendor/tesla-android"), VENDOR_REVISION)

    def test_release_patch_covers_both_frontends(self):
        patch = self.release_patch()
        for value in (
            RELEASE_VERSION,
            PROJECT_URL,
            DONATION_COPY,
            DONATION_URL,
            "Android 15 Release",
            "Streaming Compatibility",
            (
                "Apple TV protected playback remains unvalidated and unsupported "
                "on this Widevine L3, unprotected-output device."
            ),
        ):
            with self.subTest(value=value):
                self.assertIn(value, patch)

        changed_paths = set(
            re.findall(r"(?m)^diff --git a/(\S+) b/\S+$", patch)
        )
        self.assertTrue(
            {
                "vendor.mk",
                "services/lighttpd/www-default/version.json",
                "services/lighttpd/www-default/main.dart.js",
                "services/lighttpd/www-default/flutter_service_worker.js",
                "services/lighttpd/www-default/beta/index.html",
                "services/lighttpd/www-default/beta/js/core/shared.js",
                "services/lighttpd/www-default/beta/js/release-notes/release-notes-data.js",
                "services/lighttpd/www-default/beta/assets/donations-qr.svg",
            }.issubset(changed_paths)
        )

    def test_new_release_precedes_preserved_2026_22_history(self):
        patch = self.release_patch()
        sections = {}
        for path in (
            "services/lighttpd/www-default/main.dart.js",
            "services/lighttpd/www-default/beta/js/release-notes/release-notes-data.js",
        ):
            start = patch.index(f"diff --git a/{path} b/{path}")
            end = patch.find("\ndiff --git ", start + 1)
            sections[path] = patch[start:] if end == -1 else patch[start:end]

        beta = sections[
            "services/lighttpd/www-default/beta/js/release-notes/release-notes-data.js"
        ]
        self.assertRegex(beta, rf'(?m)^\+\s+"versionName": "{re.escape(RELEASE_VERSION)}"')
        self.assertRegex(beta, r'(?m)^\s+"versionName": "2026\.22\.1"')
        self.assertNotRegex(beta, r'(?m)^-.*2026\.22\.1')
        self.assertLess(beta.index(RELEASE_VERSION), beta.index("2026.22.1"))

        compiled = sections["services/lighttpd/www-default/main.dart.js"]
        self.assertRegex(compiled, r'(?m)^ B\.CN=.*"Frontend Beta"')
        self.assertRegex(compiled, r'(?m)^ B\.CU=.*"Bluetooth Audio"')
        self.assertNotRegex(compiled, r'(?m)^-B\.(?:CN|CU)=')
        self.assertIn("B.Android15Version,B.a02", compiled)

    def test_public_version_changes_without_forcing_boot_partition_rewrite(self):
        patch = self.release_patch()
        self.assertGreaterEqual(
            len(re.findall(rf"^\+.*{re.escape(RELEASE_VERSION)}", patch, re.MULTILINE)),
            4,
            "product property, version JSON, beta About, and release notes must agree",
        )
        self.assertNotIn("services/updateBootFiles/updateBootFiles.sh", patch)

    def test_service_worker_uses_canonical_patched_blob_hashes(self):
        patch = self.release_patch()
        expected_hashes = {
            "main.dart.js": "8eb476b14046985de2b57e6a8d56c911",
            "version.json": "ffbf02a1282eba29fc659aa4bfe8e635",
            "beta/index.html": "52e8d76061b987fc8c9bcea7aed92389",
            "beta/js/core/shared.js": "acf1abfb8169d3e5f12078f5ff1f9532",
            "beta/js/release-notes/release-notes-data.js": (
                "89ee64bdef9c6b88d675543cb96c214a"
            ),
            "beta/assets/donations-qr.svg": "3450073a38e6d616d6ce6cdcaf9d0c22",
        }
        for asset, digest in expected_hashes.items():
            with self.subTest(asset=asset):
                self.assertIn(f'"{asset}": "{digest}"', patch)

    def test_current_about_and_donation_copy_are_replaced(self):
        patch = self.release_patch()
        self.assertRegex(patch, rf"(?m)^-.*{re.escape(OLD_ABOUT)}")
        self.assertRegex(patch, rf"(?m)^-.*{re.escape(OLD_DONATION_COPY)}")
        self.assertRegex(patch, rf"(?m)^\+.*{re.escape(PROJECT_URL)}")
        self.assertRegex(patch, rf"(?m)^\+.*{re.escape(DONATION_COPY)}")

    def test_beta_qr_and_production_generator_share_exact_payload(self):
        patch = self.release_patch()
        escaped_url = html.escape(DONATION_URL, quote=True)
        self.assertIn(f"<desc>{escaped_url}</desc>", patch)
        self.assertRegex(
            patch,
            rf'(?m)^\+.*new A\.yN\("{re.escape(DONATION_URL)}",-1,s\)',
        )
        self.assertRegex(
            patch,
            r"(?m)^\+.*<path\b[^>]*\bd=",
            "beta donation SVG must contain a generated QR path",
        )

    def test_port_gate_requires_release_contract(self):
        gate = PORT_GATE.read_text(encoding="utf-8")
        self.assertIn(RELEASE_PATCH.relative_to(REPO_ROOT).as_posix(), gate)
        self.assertIn("test_android15_release_presentation.py", gate)

    def test_release_version_pattern_accepts_android_platform_segment(self):
        pipeline = JENKINS_PIPELINE.read_text(encoding="utf-8")
        declaration = re.search(r"def version = file =~ /(.+?)/;", pipeline)
        self.assertIsNotNone(declaration, "missing Jenkins release-version pattern")
        version_pattern = re.compile(declaration.group(1))

        def parse(value):
            match = version_pattern.search(
                f"ro.tesla-android.build.version={value}"
            )
            return match.group(1) if match else None

        self.assertEqual(parse("2026.39.a15.1"), "2026.39.a15.1")
        self.assertEqual(parse("2026.22.1"), "2026.22.1")


if __name__ == "__main__":
    unittest.main()
