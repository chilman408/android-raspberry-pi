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


CHECKER = REPO_ROOT / "tools" / "check-android15-build-cache.py"

VALID_WORKFLOW = """
env:
  ANDROID_BUILD_BASELINE: android-platform-15.0.0_r3-core-clean-v1
- name: Check out source
- name: Prepare Android build cache
  run: bash tools/android-build-cache.sh prepare
- name: Verify build host capacity
  run: |
    if [[ "${ANDROID_BUILD_MODE:?}" == "incremental" ]]; then
      required_disk_kib=$((150 * 1024 * 1024))
    else
      required_disk_kib=$((300 * 1024 * 1024))
    fi
- name: Sync and patch Android sources
- name: Build Raspberry Pi 4 image
- name: Package flashable artifacts
- name: Record successful Android build baseline
  run: bash tools/android-build-cache.sh record
- name: Upload image artifacts
"""

VALID_HELPER = r'''
workspace="$(realpath -m -- "$workspace_input")"
aosptree_root="$workspace/aosptree"
out_root="$workspace/aosptree/out"
stamp_file="$out_root/.android-build-baseline"
if [[ -L "$aosptree_root" ]]; then exit 1; fi
if [[ -L "$out_root" ]]; then exit 1; fi
cmp -s -- "$stamp_file"
rm -rf -- "$out_root"
printf 'ANDROID_BUILD_MODE=fresh\n' >> "$GITHUB_ENV"
printf 'ANDROID_BUILD_MODE=incremental\n' >> "$GITHUB_ENV"
temporary_stamp="$(mktemp "$out_root/.android-build-baseline.tmp.XXXXXX")"
mv -f -- "$temporary_stamp" "$stamp_file"
'''


class WorkflowContractTests(unittest.TestCase):
    def run_checker(self, workflow_text, helper_text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            workflow = path / "workflow.yml"
            helper = path / "helper.sh"
            workflow.write_text(workflow_text, encoding="utf-8")
            helper.write_text(helper_text, encoding="utf-8")
            return subprocess.run(
                ["python3", str(CHECKER), str(workflow), str(helper)],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_valid_contract_passes(self):
        result = self.run_checker(VALID_WORKFLOW, VALID_HELPER)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_required_workflow_fragments_are_enforced(self):
        mutations = (
            ("android-platform-15.0.0_r3-core-clean-v1", "wrong"),
            ("run: bash tools/android-build-cache.sh prepare", "run: true"),
            ("required_disk_kib=$((150 * 1024 * 1024))", "required_disk_kib=1"),
            ("required_disk_kib=$((300 * 1024 * 1024))", "required_disk_kib=2"),
            ("run: bash tools/android-build-cache.sh record", "run: true"),
        )
        for old, new in mutations:
            with self.subTest(fragment=old):
                result = self.run_checker(VALID_WORKFLOW.replace(old, new), VALID_HELPER)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ERROR:", result.stderr)

    def test_record_must_follow_packaging(self):
        mutated = VALID_WORKFLOW.replace(
            "- name: Package flashable artifacts\n"
            "- name: Record successful Android build baseline\n",
            "- name: Record successful Android build baseline\n"
            "- name: Package flashable artifacts\n",
        )
        result = self.run_checker(mutated, VALID_HELPER)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("workflow step order", result.stderr)

    def test_existence_only_classification_is_rejected(self):
        mutated = VALID_WORKFLOW + '\nif [[ -d "$GITHUB_WORKSPACE/aosptree/out" ]]; then true; fi\n'
        result = self.run_checker(mutated, VALID_HELPER)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existence-only", result.stderr)

    def test_runtime_safety_fragments_are_enforced(self):
        mutations = (
            (".android-build-baseline", ".wrong-stamp"),
            ("cmp -s --", "test -f"),
            ('realpath -m -- "$workspace_input"', "printf unsafe"),
            ('-L "$aosptree_root"', '-e "$aosptree_root"'),
            ('-L "$out_root"', '-e "$out_root"'),
            ('rm -rf -- "$out_root"', "true"),
            ("ANDROID_BUILD_MODE=fresh", "MODE=fresh"),
            ("ANDROID_BUILD_MODE=incremental", "MODE=incremental"),
            ('mktemp "$out_root/.android-build-baseline.tmp.XXXXXX"', "mktemp"),
            ('mv -f -- "$temporary_stamp" "$stamp_file"', "cp source destination"),
        )
        for old, new in mutations:
            with self.subTest(fragment=old):
                result = self.run_checker(VALID_WORKFLOW, VALID_HELPER.replace(old, new))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ERROR:", result.stderr)


    def test_declared_baseline_must_match_exactly(self):
        mutated = VALID_WORKFLOW.replace(
            "android-platform-15.0.0_r3-core-clean-v1",
            "android-platform-15.0.0_r3-core-clean-v1-wrong",
        )
        result = self.run_checker(mutated, VALID_HELPER)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("declared baseline", result.stderr)

    def test_cache_step_names_must_bind_to_their_commands(self):
        prepare = "run: bash tools/android-build-cache.sh prepare"
        record = "run: bash tools/android-build-cache.sh record"
        mutated = (
            VALID_WORKFLOW.replace(prepare, "run: temporary-command")
            .replace(record, prepare)
            .replace("run: temporary-command", record)
        )
        result = self.run_checker(mutated, VALID_HELPER)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prepare step", result.stderr)
        self.assertIn("record step", result.stderr)

    def test_record_step_must_use_default_success_condition(self):
        mutated = VALID_WORKFLOW.replace(
            "- name: Record successful Android build baseline\n"
            "  run: bash tools/android-build-cache.sh record",
            "- name: Record successful Android build baseline\n"
            "  if: always()\n"
            "  run: bash tools/android-build-cache.sh record",
        )
        result = self.run_checker(mutated, VALID_HELPER)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("record step must use the default success condition", result.stderr)

    def test_build_and_package_must_not_continue_after_errors(self):
        for step_name in (
            "Build Raspberry Pi 4 image",
            "Package flashable artifacts",
        ):
            with self.subTest(step=step_name):
                mutated = VALID_WORKFLOW.replace(
                    f"- name: {step_name}\n",
                    f"- name: {step_name}\n"
                    "  continue-on-error: true\n",
                )
                result = self.run_checker(mutated, VALID_HELPER)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    f"{step_name} must not continue on error",
                    result.stderr,
                )

if __name__ == "__main__":
    unittest.main()
