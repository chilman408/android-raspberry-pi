import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "tools" / "android-build-cache.sh"
BASELINE = "android-platform-15.0.0_r3-core-clean-v1"


class RuntimeCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "workspace"
        self.workspace.mkdir()
        self.github_env = Path(self.temp.name) / "github-env"
        self.github_env.touch()

    @property
    def output(self):
        return self.workspace / "aosptree" / "out"

    @property
    def stamp(self):
        return self.output / ".android-build-baseline"

    def run_helper(self, action, baseline=BASELINE):
        environment = os.environ.copy()
        environment.update(
            GITHUB_WORKSPACE=str(self.workspace),
            GITHUB_ENV=str(self.github_env),
            ANDROID_BUILD_BASELINE=baseline,
        )
        return subprocess.run(
            ["bash", str(HELPER), action],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def modes(self):
        return self.github_env.read_text(encoding="utf-8").splitlines()

    def test_missing_output_selects_fresh_without_stamp(self):
        result = self.run_helper("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.modes(), ["ANDROID_BUILD_MODE=fresh"])
        self.assertFalse(self.output.exists())
        self.assertFalse(self.stamp.exists())

    def test_unstamped_output_is_removed(self):
        self.output.mkdir(parents=True)
        (self.output / "stale-object").write_text("stale", encoding="utf-8")
        result = self.run_helper("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.modes(), ["ANDROID_BUILD_MODE=fresh"])
        self.assertFalse(self.output.exists())

    def test_exact_stamp_preserves_incremental_output(self):
        self.output.mkdir(parents=True)
        retained = self.output / "valid-object"
        retained.write_text("valid", encoding="utf-8")
        self.stamp.write_text(f"{BASELINE}\n", encoding="utf-8")
        result = self.run_helper("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.modes(), ["ANDROID_BUILD_MODE=incremental"])
        self.assertEqual(retained.read_text(encoding="utf-8"), "valid")

    def test_extra_stamp_content_forces_fresh(self):
        self.output.mkdir(parents=True)
        self.stamp.write_text(f"{BASELINE}\nextra\n", encoding="utf-8")
        result = self.run_helper("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.modes(), ["ANDROID_BUILD_MODE=fresh"])
        self.assertFalse(self.output.exists())

    def test_mismatched_stamp_forces_fresh(self):
        self.output.mkdir(parents=True)
        self.stamp.write_text("android-platform-14-old\n", encoding="utf-8")
        result = self.run_helper("prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.modes(), ["ANDROID_BUILD_MODE=fresh"])
        self.assertFalse(self.output.exists())

    def test_output_symlink_fails_closed_and_preserves_target(self):
        external = Path(self.temp.name) / "external"
        external.mkdir()
        sentinel = external / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        (self.workspace / "aosptree").mkdir()
        self.output.symlink_to(external, target_is_directory=True)
        result = self.run_helper("prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe Android output path", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        self.assertTrue(self.output.is_symlink())
        self.assertEqual(self.modes(), [])

    def test_aosptree_symlink_fails_closed_and_preserves_target(self):
        external = Path(self.temp.name) / "external-aosptree"
        (external / "out").mkdir(parents=True)
        sentinel = external / "out" / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        (self.workspace / "aosptree").symlink_to(external, target_is_directory=True)
        result = self.run_helper("prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe Android source tree path", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
        self.assertTrue((self.workspace / "aosptree").is_symlink())
        self.assertEqual(self.modes(), [])

    def test_record_writes_exact_stamp_without_temp_file(self):
        self.output.mkdir(parents=True)
        result = self.run_helper("record")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.stamp.read_bytes(), f"{BASELINE}\n".encode())
        self.assertEqual(list(self.output.glob(".android-build-baseline.tmp.*")), [])

    def test_record_refuses_missing_output(self):
        result = self.run_helper("record")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot record baseline", result.stderr)
        self.assertFalse(self.stamp.exists())

    def test_nonexistent_workspace_fails_closed(self):
        self.workspace = Path(self.temp.name) / "missing-workspace"
        result = self.run_helper("prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe GITHUB_WORKSPACE", result.stderr)
        self.assertEqual(self.modes(), [])

    def test_regular_file_workspace_fails_closed(self):
        self.workspace = Path(self.temp.name) / "workspace-file"
        self.workspace.write_text("not a directory", encoding="utf-8")
        result = self.run_helper("prepare")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe GITHUB_WORKSPACE", result.stderr)
        self.assertEqual(self.modes(), [])

    def test_invalid_baseline_is_rejected(self):
        result = self.run_helper("prepare", "bad\nANDROID_BUILD_MODE=incremental")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid Android build baseline", result.stderr)
        self.assertEqual(self.modes(), [])


if __name__ == "__main__":
    unittest.main()
