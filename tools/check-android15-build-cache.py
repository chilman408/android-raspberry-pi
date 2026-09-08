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
    "normalized workspace": 'realpath -m -- "$workspace_input"',
    "source-tree symlink guard": '-L "$aosptree_root"',
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
