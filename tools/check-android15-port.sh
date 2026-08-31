#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

failures=0

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  failures=$((failures + 1))
}

require_file() {
  local path="$1"
  local reason="$2"
  [[ -f "$path" ]] || fail "$reason (missing: $path)"
}

require_match() {
  local path="$1"
  local pattern="$2"
  local reason="$3"
  if [[ ! -f "$path" ]] || ! grep -Eq "$pattern" "$path"; then
    fail "$reason"
  fi
}

reject_match() {
  local path="$1"
  local pattern="$2"
  local reason="$3"
  if [[ -f "$path" ]] && grep -Eq "$pattern" "$path"; then
    fail "$reason"
  fi
}

require_match "unfold_aosp.sh" 'android-platform-15\.0\.0_r3' \
  "AOSP must be pinned to the validated Android 15 r3 baseline"
require_match "manifests/glodroid.xml" 'kernel-broadcom-2024w50' \
  "The manifest must use the Raspberry Pi Linux 6.6 baseline"
require_match "build_rpi4.sh" 'tesla_android_rpi4-trunk_staging-userdebug' \
  "The Raspberry Pi 4 build must use the Android 15 release configuration"

require_match "manifests/glodroid.xml" 'revision="45f05f681224d88d1b170063001b59edc8fc24cf"' \
  "Android 15 requires the maintained Raspberry Pi U-Boot baseline"
require_match "manifests/tesla-android.xml" 'revision="c82b898997a47b699869e9650920eacecc4fd14d"' \
  "Android 15 requires the init_boot-capable GloDroid configuration"
require_file "patches-aosp/glodroid/configuration/0011-bootscript-Account-for-ab_select-command-u-boot-chan.patch" \
  "The boot script must use the current U-Boot A/B selection command"
require_file "patches-aosp/glodroid/bootloader/u-boot/0002-abootcmd-Add-load-subcommand.patch" \
  "U-Boot must load Android 15 boot, init_boot, and vendor_boot images"
require_match ".github/workflows/build-android15-rpi4.yml" \
  'UBOOT_OBJ' \
  "The build must discard stale U-Boot intermediates after bootloader revision changes"
require_file "patches-aosp/glodroid/bootloader/u-boot/0006-abootcmd-Trace-Android-partition-loading.patch" \
  "Bring-up images must expose the exact U-Boot partition-loading failure stage"
require_file "patches-aosp/glodroid/configuration/0026-bootscript-Preserve-A-B-retries-during-bring-up.patch" \
  "Bring-up images must not exhaust both A/B slots while diagnosing boot resets"
require_match "patches-aosp/glodroid/configuration/0025-Install-kernel-build-outputs-directly.patch" \
  'cp \$\(OUT_BUILD_DIR\)/arch/arm64/boot/Image \$\(OUT_INSTALL_DIR\)/vmlinux' \
  "The ARM64 boot payload must start from an uncompressed Linux Image"
require_match "patches-aosp/glodroid/configuration/0025-Install-kernel-build-outputs-directly.patch" \
  '41524d64' \
  "The kernel install must verify the ARM64 Image magic before LZ4 compression"
reject_match "patches-aosp/glodroid/configuration/0025-Install-kernel-build-outputs-directly.patch" \
  'cp \$\(OUT_BUILD_DIR\)/\$\$\{KERNEL_IMAGE\} \$\(OUT_INSTALL_DIR\)/vmlinux' \
  "Do not wrap an image_name-selected Image.gz inside LZ4"

require_file "patches-aosp/glodroid/configuration/0006-graphics-drm_hwcomposer-Transition-from-HWC2-to-HWC3.patch" \
  "Android 15 requires the HWC3 transition"
require_file "patches-aosp/glodroid/configuration/0017-common-Switch-Bluetooth-HAL-to-AIDL-default.patch" \
  "Android 15 requires the Bluetooth AIDL HAL"
require_file "patches-aosp/glodroid/configuration/0018-kconfig-Set-localversion-config-to-android15-0.patch" \
  "Kernel modules must use the Android 15 local version"
require_file "patches-aosp/glodroid/configuration/0019-common-graphics-Remove-legacy-mapper4-in-a-favour-of.patch" \
  "Android 15 graphics must use the current gralloc mapper"
require_file "patches-aosp/hardware/interfaces/0002-HCI-Fix-improper-rfkill-handling.patch" \
  "The Raspberry Pi Bluetooth rfkill fix is required"
require_file "patches-aosp/packages/modules/Wifi/0005-Fix-UnflaggedApi-build-errors-for-android.net.wifi.T.patch" \
  "The Tesla Android Wi-Fi API patch must be rebased for Android 15"

reject_match "manifests/tesla-android.xml" 'revision="tau"' \
  "MindTheGapps tau targets Android 13; Android 15 requires the vic branch"
reject_match "manifests/tesla-android.xml" 'revision="lineage-20\.0"' \
  "Jelly lineage-20.0 targets Android 13; use the Android 15-compatible revision"
reject_match "unfold_aosp.sh" 'android-platform-14\.' \
  "An Android 14 platform pin remains in the active build script"

if grep -REn '^(<<<<<<< |=======$|>>>>>>> )' \
    --exclude-dir=.git --exclude-dir=aosptree --exclude-dir=artifacts --exclude='*.md' .; then
  fail "Unresolved Git merge markers remain in active source or patch files"
fi

python3 - <<'PY' || failures=$((failures + 1))
import collections
import pathlib
import re

for directory in sorted(pathlib.Path("patches-aosp").rglob("*")):
    if not directory.is_dir():
        continue
    prefixes = collections.defaultdict(list)
    for patch in directory.glob("*.patch"):
        match = re.match(r"(\\d{4})-", patch.name)
        if match:
            prefixes[match.group(1)].append(patch.name)
    duplicates = {key: names for key, names in prefixes.items() if len(names) > 1}
    if duplicates:
        for key, names in duplicates.items():
            print(
                f"ERROR: duplicate patch sequence {key} in {directory}: "
                + ", ".join(sorted(names))
            )
        raise SystemExit(1)

print("Patch sequence prefixes are unique.")
PY

python3 - <<'PY' || failures=$((failures + 1))
import pathlib
import xml.etree.ElementTree as ET

paths = {}
for manifest_name in ("manifests/default_aosp.xml", "manifests/glodroid.xml", "manifests/tesla-android.xml"):
    root = ET.parse(manifest_name).getroot()
    for project in root.findall("project"):
        path = project.get("path")
        if not path:
            continue
        previous = paths.get(path)
        if previous:
            raise SystemExit(
                f"ERROR: duplicate manifest path {path!r} in {previous} and {manifest_name}"
            )
        paths[path] = manifest_name

print(f"Validated {len(paths)} unique manifest project paths.")
PY

if (( failures > 0 )); then
  printf '\nAndroid 15 port validation failed with %d problem(s).\n' "$failures" >&2
  exit 1
fi

printf 'Android 15 Raspberry Pi baseline validation passed.\n'
