# Android 15 Build-Baseline Cache Invalidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse retained Android output only after the exact Android 15 baseline completes a successful build and packaging run.

**Architecture:** A Bash helper owns cache classification, guarded cleanup, and atomic stamping through `prepare` and `record` commands. A standard-library Python checker validates the helper/workflow contract and order, while Python `unittest` exercises runtime states and mutation regressions in temporary directories.

**Tech Stack:** Bash, Python 3 standard library, GitHub Actions YAML, Git

**Spec:** `docs/superpowers/specs/2026-09-07-android15-build-baseline-cache-invalidation-design.md`

## Global Constraints

- Declare exactly `android-platform-15.0.0_r3-core-clean-v1`.
- Store the stamp at `$GITHUB_WORKSPACE/aosptree/out/.android-build-baseline`.
- Permit incremental reuse only for an exact matching regular stamp in a real output directory.
- Treat missing or mismatched state as fresh; fail closed for unsafe workspace/output types.
- Limit cleanup to the exact quoted path `$GITHUB_WORKSPACE/aosptree/out`.
- Require 300 GiB free disk for fresh builds, 150 GiB for incremental builds, and 40 GiB RAM plus swap in both modes.
- Record the stamp atomically only after build and packaging succeed.
- Keep the init fingerprint workaround, source pins, and four-directory external intermediate refresh unchanged.
- Put `[build-image]` only in the final workflow-integration commit.

## File Structure

- Create `tools/android-build-cache.sh`: sole owner of cache lifecycle mutations.
- Create `tools/check-android15-build-cache.py`: static helper/workflow contract validation.
- Create `tools/tests/test_android15_build_cache.py`: isolated runtime and mutation tests.
- Modify `.github/workflows/build-android15-rpi4.yml`: declare, prepare, classify capacity, and record.
- Modify `tools/check-android15-port.sh`: invoke the checker and regression suite.

---

### Task 1: Safe Runtime Cache Lifecycle

**Files:**

- Create: `tools/android-build-cache.sh`
- Create: `tools/tests/test_android15_build_cache.py`

**Interfaces:**

- Consumes: command `prepare` or `record`; `GITHUB_WORKSPACE`; `ANDROID_BUILD_BASELINE`; and, for `prepare`, `GITHUB_ENV`.
- Produces: `ANDROID_BUILD_MODE=fresh|incremental` in `GITHUB_ENV`, or an atomically replaced success stamp.

- [ ] **Step 1: Write the failing runtime tests**

Create `tools/tests/test_android15_build_cache.py`:

```python
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

    def test_invalid_baseline_is_rejected(self):
        result = self.run_helper("prepare", "bad\nANDROID_BUILD_MODE=incremental")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid Android build baseline", result.stderr)
        self.assertEqual(self.modes(), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest -v tools/tests/test_android15_build_cache.py`

Expected: 9 errors/failures because `tools/android-build-cache.sh` is absent.

- [ ] **Step 3: Implement the runtime helper**

Create `tools/android-build-cache.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

action="${1:-}"
if [[ "$action" != "prepare" && "$action" != "record" ]]; then
  echo "Usage: $0 prepare|record" >&2
  exit 2
fi

: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE must be set}"
: "${ANDROID_BUILD_BASELINE:?ANDROID_BUILD_BASELINE must be set}"
if [[ ! "$ANDROID_BUILD_BASELINE" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "ERROR: invalid Android build baseline: $ANDROID_BUILD_BASELINE" >&2
  exit 1
fi

workspace="${GITHUB_WORKSPACE%/}"
if [[ -z "$workspace" || "$workspace" == "/" || "$workspace" != /* ]]; then
  echo "ERROR: unsafe GITHUB_WORKSPACE: $GITHUB_WORKSPACE" >&2
  exit 1
fi

out_root="$workspace/aosptree/out"
expected_out_root="$workspace/aosptree/out"
stamp_file="$out_root/.android-build-baseline"
if [[ "$out_root" != "$expected_out_root" ]]; then
  echo "ERROR: refusing unexpected Android output path: $out_root" >&2
  exit 1
fi
if [[ -L "$out_root" || ( -e "$out_root" && ! -d "$out_root" ) ]]; then
  echo "ERROR: unsafe Android output path: $out_root" >&2
  exit 1
fi

case "$action" in
  prepare)
    : "${GITHUB_ENV:?GITHUB_ENV must be set for prepare}"
    if [[ -d "$out_root" && -f "$stamp_file" && ! -L "$stamp_file" ]] &&
        cmp -s -- "$stamp_file" <(printf '%s\n' "$ANDROID_BUILD_BASELINE"); then
      echo "Android build cache baseline: $ANDROID_BUILD_BASELINE"
      echo "Android build cache mode: incremental"
      printf 'ANDROID_BUILD_MODE=incremental\n' >> "$GITHUB_ENV"
      exit 0
    fi
    previous_baseline="<missing>"
    if [[ -f "$stamp_file" && ! -L "$stamp_file" ]]; then
      previous_baseline="$(tr '\n' ' ' < "$stamp_file")"
    fi
    echo "Android build cache baseline: $ANDROID_BUILD_BASELINE"
    echo "Previous successful baseline: $previous_baseline"
    echo "Android build cache mode: fresh"
    rm -rf -- "$out_root"
    printf 'ANDROID_BUILD_MODE=fresh\n' >> "$GITHUB_ENV"
    ;;
  record)
    if [[ ! -d "$out_root" || -L "$out_root" ]]; then
      echo "ERROR: cannot record baseline without a safe Android output directory" >&2
      exit 1
    fi
    if [[ -L "$stamp_file" || ( -e "$stamp_file" && ! -f "$stamp_file" ) ]]; then
      echo "ERROR: unsafe Android build baseline stamp: $stamp_file" >&2
      exit 1
    fi
    temporary_stamp="$(mktemp "$out_root/.android-build-baseline.tmp.XXXXXX")"
    cleanup_temporary_stamp() {
      [[ -z "${temporary_stamp:-}" ]] || rm -f -- "$temporary_stamp"
    }
    trap cleanup_temporary_stamp EXIT
    printf '%s\n' "$ANDROID_BUILD_BASELINE" > "$temporary_stamp"
    mv -f -- "$temporary_stamp" "$stamp_file"
    temporary_stamp=""
    echo "Recorded successful Android build baseline: $ANDROID_BUILD_BASELINE"
    ;;
esac
```

- [ ] **Step 4: Verify runtime behavior and syntax**

Run:

```bash
python3 -m unittest -v tools/tests/test_android15_build_cache.py
bash -n tools/android-build-cache.sh
```

Expected: 9 tests pass; shell syntax exits 0.

- [ ] **Step 5: Commit the runtime unit**

```bash
git add tools/android-build-cache.sh tools/tests/test_android15_build_cache.py
git commit -m "ci: add safe Android build cache lifecycle"
```

### Task 2: Static Workflow Contract Checker

**Files:**

- Create: `tools/check-android15-build-cache.py`
- Modify: `tools/tests/test_android15_build_cache.py`

**Interfaces:**

- Consumes: positional paths `WORKFLOW` and `HELPER`.
- Produces: exit 0 plus a pass message, or exit 1 with one `ERROR:` per violation.

- [ ] **Step 1: Append failing contract tests**

Before the existing main block, add:

```python
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
out_root="$workspace/aosptree/out"
stamp_file="$out_root/.android-build-baseline"
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
```

- [ ] **Step 2: Run tests and verify checker absence**

Run: `python3 -m unittest -v tools/tests/test_android15_build_cache.py`

Expected: 9 runtime tests pass and 5 contract tests fail because the checker is absent.

- [ ] **Step 3: Implement the checker**

Create `tools/check-android15-build-cache.py`:

```python
#!/usr/bin/env python3
import sys
from pathlib import Path

WORKFLOW_REQUIREMENTS = {
    "declared baseline": "ANDROID_BUILD_BASELINE: android-platform-15.0.0_r3-core-clean-v1",
    "prepare invocation": "run: bash tools/android-build-cache.sh prepare",
    "mode-based capacity": 'if [[ "${ANDROID_BUILD_MODE:?}" == "incremental" ]]; then',
    "150 GiB threshold": "required_disk_kib=$((150 * 1024 * 1024))",
    "300 GiB threshold": "required_disk_kib=$((300 * 1024 * 1024))",
    "record invocation": "run: bash tools/android-build-cache.sh record",
}
HELPER_REQUIREMENTS = {
    "workspace output": 'out_root="$workspace/aosptree/out"',
    "stamp location": 'stamp_file="$out_root/.android-build-baseline"',
    "symlink guard": '-L "$out_root"',
    "exact comparison": 'cmp -s -- "$stamp_file"',
    "bounded cleanup": 'rm -rf -- "$out_root"',
    "fresh export": "ANDROID_BUILD_MODE=fresh",
    "incremental export": "ANDROID_BUILD_MODE=incremental",
    "same-directory temporary stamp": 'mktemp "$out_root/.android-build-baseline.tmp.XXXXXX"',
    "atomic replacement": 'mv -f -- "$temporary_stamp" "$stamp_file"',
}
ORDERED_STEPS = (
    "- name: Check out source",
    "- name: Prepare Android build cache",
    "- name: Verify build host capacity",
    "- name: Sync and patch Android sources",
    "- name: Build Raspberry Pi 4 image",
    "- name: Package flashable artifacts",
    "- name: Record successful Android build baseline",
    "- name: Upload image artifacts",
)
EXISTENCE_ONLY = 'if [[ -d "$GITHUB_WORKSPACE/aosptree/out" ]]; then'


def validate_contract(workflow_path, helper_path):
    workflow = workflow_path.read_text(encoding="utf-8")
    helper = helper_path.read_text(encoding="utf-8")
    errors = []
    for description, fragment in WORKFLOW_REQUIREMENTS.items():
        if fragment not in workflow:
            errors.append(f"workflow is missing {description}")
    if EXISTENCE_ONLY in workflow:
        errors.append("workflow retains existence-only cache classification")
    for description, fragment in HELPER_REQUIREMENTS.items():
        if fragment not in helper:
            errors.append(f"cache helper is missing {description}")
    positions = [workflow.find(step) for step in ORDERED_STEPS]
    if any(position < 0 for position in positions):
        errors.append("workflow is missing one or more ordered cache/build steps")
    elif positions != sorted(positions):
        errors.append("workflow step order does not delay stamping until after packaging")
    return errors


def main(arguments):
    if len(arguments) != 3:
        print(f"Usage: {arguments[0]} WORKFLOW HELPER", file=sys.stderr)
        return 2
    errors = validate_contract(Path(arguments[1]), Path(arguments[2]))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Android build cache contract validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Verify the checker unit**

Run:

```bash
python3 -m unittest -v tools/tests/test_android15_build_cache.py
python3 -m py_compile tools/check-android15-build-cache.py tools/tests/test_android15_build_cache.py
```

Expected: 14 tests pass; compilation exits 0.

- [ ] **Step 5: Commit the checker unit**

```bash
git add tools/check-android15-build-cache.py tools/tests/test_android15_build_cache.py
git commit -m "test: enforce Android build cache contract"
```

### Task 3: Integrate the Baseline Lifecycle into CI

**Files:**

- Modify: `tools/tests/test_android15_build_cache.py`
- Modify: `.github/workflows/build-android15-rpi4.yml:30-75`
- Modify: `.github/workflows/build-android15-rpi4.yml:121-166`
- Modify: `tools/check-android15-port.sh:56-70`

**Interfaces:**

- Consumes: `ANDROID_BUILD_MODE` exported by Task 1.
- Produces: prepare-before-capacity/sync, record-after-package, and enforcement through the existing validator.

- [ ] **Step 1: Add a failing repository-integration test**

Add to `WorkflowContractTests`:

```python
    def test_repository_workflow_and_helper_satisfy_contract(self):
        result = subprocess.run(
            [
                "python3",
                str(CHECKER),
                str(REPO_ROOT / ".github/workflows/build-android15-rpi4.yml"),
                str(HELPER),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
```

- [ ] **Step 2: Verify current-workflow failure**

Run:

```bash
python3 -m unittest -v \
  tools.tests.test_android15_build_cache.WorkflowContractTests.test_repository_workflow_and_helper_satisfy_contract
```

Expected: FAIL for missing baseline, helper calls, mode-based capacity, and ordered steps.

- [ ] **Step 3: Declare and prepare the baseline**

Add to the job-level `env:` block before credentials:

```yaml
      ANDROID_BUILD_BASELINE: android-platform-15.0.0_r3-core-clean-v1
```

Immediately after `Check out source`, add:

```yaml
      - name: Prepare Android build cache
        shell: bash
        run: bash tools/android-build-cache.sh prepare
```

- [ ] **Step 4: Select capacity only from the exported mode**

Replace the output-directory test with:

```bash
          if [[ "${ANDROID_BUILD_MODE:?}" == "incremental" ]]; then
            required_disk_kib=$((150 * 1024 * 1024))
            disk_mode="same-baseline incremental build"
          elif [[ "$ANDROID_BUILD_MODE" == "fresh" ]]; then
            required_disk_kib=$((300 * 1024 * 1024))
            disk_mode="fresh Android build"
          else
            echo "ERROR: unexpected Android build mode: $ANDROID_BUILD_MODE" >&2
            exit 1
          fi
```

Keep the existing free-disk/memory calculations and checks.

- [ ] **Step 5: Record only after packaging**

Between `Package flashable artifacts` and `Upload image artifacts`, add:

```yaml
      - name: Record successful Android build baseline
        shell: bash
        run: bash tools/android-build-cache.sh record
```

- [ ] **Step 6: Attach validation to the existing gate**

After the current workflow assertions in `tools/check-android15-port.sh`, add:

```bash
if ! python3 tools/check-android15-build-cache.py \
    .github/workflows/build-android15-rpi4.yml \
    tools/android-build-cache.sh; then
  failures=$((failures + 1))
fi

if ! python3 -m unittest -v tools/tests/test_android15_build_cache.py; then
  failures=$((failures + 1))
fi
```

Leave fingerprint-workaround assertions unchanged.

- [ ] **Step 7: Run focused and full verification**

```bash
python3 -m unittest -v tools/tests/test_android15_build_cache.py
bash -n tools/android-build-cache.sh tools/check-android15-port.sh
python3 -m py_compile tools/check-android15-build-cache.py tools/tests/test_android15_build_cache.py
bash tools/check-android15-port.sh
ruby -e 'require "yaml"; YAML.load_file(ARGV[0], aliases: true)' \
  .github/workflows/build-android15-rpi4.yml
git diff --check
```

Expected: 15 tests pass; shell/Python/YAML checks exit 0; the validator ends with `Android 15 Raspberry Pi baseline validation passed.`

- [ ] **Step 8: Review the single-variable diff**

```bash
git diff --stat
git diff -- \
  .github/workflows/build-android15-rpi4.yml \
  tools/android-build-cache.sh \
  tools/check-android15-build-cache.py \
  tools/check-android15-port.sh \
  tools/tests/test_android15_build_cache.py
```

Expected: only these five implementation files differ; source pins and fingerprint workaround remain unchanged.

- [ ] **Step 9: Commit and trigger one image build**

```bash
git add \
  .github/workflows/build-android15-rpi4.yml \
  tools/check-android15-port.sh \
  tools/tests/test_android15_build_cache.py
git commit -m "[build-image] ci: invalidate incompatible Android build output"
git push origin android-15-bringup
```

Expected: validation starts, and exactly this commit triggers the image build.

### Task 4: Verify CI and Hand Off Hardware Testing

**Files:**

- Verify only: no repository files change.

**Interfaces:**

- Consumes: the pushed integration commit and its GitHub Actions runs.
- Produces: passing validation, a logged fresh 300 GiB build, recorded baseline, and flashable artifact.

- [ ] **Step 1: Watch validation**

```bash
validation_run_id="$(gh run list --workflow android15-port-validation.yml \
  --branch android-15-bringup --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$validation_run_id" --exit-status
```

Expected: baseline and patch-series jobs succeed.

- [ ] **Step 2: Watch the image build**

```bash
build_run_id="$(gh run list --workflow build-android15-rpi4.yml \
  --branch android-15-bringup --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$build_run_id" --exit-status
```

Expected: build, packaging, and upload succeed.

- [ ] **Step 3: Verify fresh-mode evidence**

```bash
gh run view "$build_run_id" --log > /tmp/android15-clean-build.log
grep -F "Android build cache mode: fresh" /tmp/android15-clean-build.log
grep -F "Required workspace disk for fresh Android build: 300 GiB" \
  /tmp/android15-clean-build.log
grep -F "Recorded successful Android build baseline: android-platform-15.0.0_r3-core-clean-v1" \
  /tmp/android15-clean-build.log
```

Expected: all three lines exist. A pre-record failure must leave the next run in fresh mode.

- [ ] **Step 4: Confirm artifact inventory without downloading it**

```bash
gh api "repos/chilman408/android-raspberry-pi/actions/runs/$build_run_id/artifacts" \
  --jq '.artifacts[] | [.name, .expired, .size_in_bytes] | @tsv'
```

Expected: a non-expired, nonempty bundle named `TeslaAndroid-15-rpi4-` plus the 12-character commit prefix.

- [ ] **Step 5: Perform the hardware checkpoint**

Flash the generated `*-flashable.img.zst` and capture serial output through either `sys.boot_completed=1` or the first repeated userspace crash loop.

Acceptance evidence:

- `system_server` starts.
- Boot animation exits.
- Both zygotes stop repeatedly receiving signal 11.
- `update_engine` and `crash_dump64` stop repeatedly receiving signal 11.

If all conditions hold, remove the fingerprint workaround in a separate planned change. Otherwise preserve this clean baseline and diagnose its tombstones/crash output without changing cache policy.
