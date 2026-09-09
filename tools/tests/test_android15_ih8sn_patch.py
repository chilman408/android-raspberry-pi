#!/usr/bin/env python3

import pathlib
import subprocess
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = (
    REPO_ROOT
    / "patches-aosp"
    / "vendor"
    / "tesla-android"
    / "0001-ih8sn-Disable-unsafe-property-spoof-on-Android-15.patch"
)

UPSTREAM_INIT_RC = """on init
    exec u:r:su:s0 root root -- /system/bin/ih8sn init
    exec u:r:sysinit:s0 root root -- /system/bin/ih8sn init

on property:sys.boot_completed=1
    exec u:r:su:s0 root root -- /system/bin/ih8sn boot_completed
    exec u:r:sysinit:s0 root root -- /system/bin/ih8sn boot_completed
"""


class Android15Ih8snPatchTest(unittest.TestCase):
    def test_patch_removes_all_boot_time_ih8sn_executions(self):
        self.assertTrue(PATCH.is_file(), f"missing Android 15 ih8sn patch: {PATCH}")

        with tempfile.TemporaryDirectory() as temp_dir:
            checkout = pathlib.Path(temp_dir)
            init_rc = checkout / "services" / "ih8sn" / "etc" / "init" / "ih8sn.rc"
            init_rc.parent.mkdir(parents=True)
            init_rc.write_text(UPSTREAM_INIT_RC, encoding="utf-8")

            result = subprocess.run(
                ["git", "apply", "--whitespace=error", str(PATCH)],
                cwd=checkout,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                f"ih8sn patch does not apply to the pinned vendor source:\n{result.stderr}",
            )

            active_lines = [
                line.strip()
                for line in init_rc.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            self.assertFalse(
                any("/system/bin/ih8sn" in line for line in active_lines),
                f"ih8sn still executes during Android boot: {active_lines}",
            )


if __name__ == "__main__":
    unittest.main()
