#!/usr/bin/env python3
import re
import sys
from pathlib import Path

BASELINE = "android-platform-15.0.0_r3-tesla-2026.22.1-runtime-v3"
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
    "Check out source",
    "Prepare Android build cache",
    "Verify build host capacity",
    "Sync and patch Android sources",
    "Build Raspberry Pi 4 image",
    "Package flashable artifacts",
    "Record successful Android build baseline",
    "Upload image artifacts",
)
EXISTENCE_ONLY = 'if [[ -d "$GITHUB_WORKSPACE/aosptree/out" ]]; then'
STEP_HEADER = re.compile(
    r"(?m)^(?P<indent>[ \t]*)-\s+name:\s*(?P<name>[^\n]+?)\s*$"
)


def step_blocks(workflow):
    matches = list(STEP_HEADER.finditer(workflow))
    return {
        match.group("name"): workflow[
            match.end() : matches[index + 1].start()
            if index + 1 < len(matches)
            else len(workflow)
        ]
        for index, match in enumerate(matches)
    }, matches


def validate_contract(workflow_path, helper_path):
    workflow = workflow_path.read_text(encoding="utf-8")
    helper = helper_path.read_text(encoding="utf-8")
    errors = []

    baseline_values = re.findall(
        r"(?m)^\s*ANDROID_BUILD_BASELINE:\s*(.*?)\s*$", workflow
    )
    if baseline_values != [BASELINE]:
        errors.append("workflow declared baseline must exactly match the required value")

    blocks, step_matches = step_blocks(workflow)
    positions = [workflow.find(f"- name: {step}") for step in ORDERED_STEPS]
    if any(position < 0 for position in positions):
        errors.append("workflow is missing one or more ordered cache/build steps")
    elif positions != sorted(positions):
        errors.append("workflow step order does not delay stamping until after packaging")

    prepare = blocks.get("Prepare Android build cache", "")
    if "run: bash tools/android-build-cache.sh prepare" not in prepare:
        errors.append("workflow prepare step must run the cache prepare command")

    capacity = blocks.get("Verify build host capacity", "")
    for description, fragment in (
        (
            "mode-based capacity",
            'if [[ "${ANDROID_BUILD_MODE:?}" == "incremental" ]]; then',
        ),
        ("150 GiB threshold", "required_disk_kib=$((150 * 1024 * 1024))"),
        ("300 GiB threshold", "required_disk_kib=$((300 * 1024 * 1024))"),
    ):
        if fragment not in capacity:
            errors.append(f"workflow capacity step is missing {description}")

    record = blocks.get("Record successful Android build baseline", "")
    if "run: bash tools/android-build-cache.sh record" not in record:
        errors.append("workflow record step must run the cache record command")
    if re.search(r"(?m)^\s*if\s*:", record):
        errors.append("workflow record step must use the default success condition")

    for step_name in (
        "Build Raspberry Pi 4 image",
        "Package flashable artifacts",
    ):
        if re.search(r"(?m)^\s*continue-on-error\s*:", blocks.get(step_name, "")):
            errors.append(f"workflow {step_name} must not continue on error")

    if EXISTENCE_ONLY in workflow:
        errors.append("workflow retains existence-only cache classification")

    for description, fragment in HELPER_REQUIREMENTS.items():
        if fragment not in helper:
            errors.append(f"cache helper is missing {description}")

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
