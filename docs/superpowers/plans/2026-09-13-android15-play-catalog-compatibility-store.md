# Android 15 Play Catalog Compatibility Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the official Aurora Store 4.8.4 preload APK as a verified, non-privileged secondary store so users can install compatible free applications that Google Play hides from this uncertified Raspberry Pi image, without changing the working Google Play or Netflix configuration.

**Architecture:** A committed lock record is the sole source of Aurora artifact identity. A Python verifier validates bytes, Android package metadata, ABI, and upstream signer with AOSP-hosted tools; a shell materializer downloads into the destination directory and atomically exposes the APK only after verification. Soong imports the untouched presigned APK into the product partition. Repository tests enforce provenance, trust-boundary, workflow-ordering, documentation, and build-baseline contracts.

**Tech Stack:** Python 3 standard library and `unittest`, Bash, AOSP `apksigner` and `aapt2`, Android Soong (`android_app_import`), Android product makefiles, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-13-android15-play-catalog-compatibility-store-design.md`

## Global Constraints

- Keep Google Play, Google Play services, the current product identity, the disabled `ih8sn` runtime rewrite, and the working Netflix codec policy unchanged.
- Import the official APK under `com.aurora.store`; do not repackage, resign, platform-sign, privilege, rename, or pre-authorize it.
- Do not embed an Aurora session, Google account, token, credential, accepted terms state, or a global Pixel/Shield identity.
- Treat catalog visibility, installability, and protected-video playback as three separate outcomes.
- Never accept a floating `latest` URL, a digest-only check without signer validation, or a previously downloaded APK that does not match the active lock.
- Do not add `[build-image]` to intermediate commits. Execute the Apple TV experiment plan before the final image-trigger commit; a negative Apple result is acceptable and must not block the Compatibility Store image.

---

### Task 1: Define and Test the Locked Artifact Contract

**Files:**

- Create: `aosptree/vendor/devices-community/gd_rpi4/compatibility-store/aurora-store.lock.json`
- Create: `tools/check-aurora-store.py`
- Create: `tools/tests/test_android15_aurora_store.py`

**Interfaces:**

- `AuroraLock.from_path(path: pathlib.Path) -> AuroraLock`
- `verify_apk(lock: AuroraLock, apk: pathlib.Path, apksigner: pathlib.Path, aapt2: pathlib.Path, runner=subprocess.run, logical_filename: str | None = None) -> None`
- CLI: `python3 tools/check-aurora-store.py --lock LOCK --apk APK --apksigner APKSIGNER --aapt2 AAPT2 [--logical-filename NAME]`
- Success prints `Aurora Store artifact verification passed.` and exits 0. Every contract violation prints one `ERROR:` line to stderr and exits 1; invocation errors exit 2.

- [ ] **Step 1: Add failing lock-schema and parser tests**

Create `tools/tests/test_android15_aurora_store.py` with `unittest` cases that import `tools/check-aurora-store.py` via `importlib.util`. Use a temporary directory, a small fixture APK byte string, and an injected runner that returns `subprocess.CompletedProcess` objects. Cover these behaviors with named tests:

```python
EXPECTED_LOCK = {
    "package_name": "com.aurora.store",
    "version_name": "4.8.4-preload",
    "version_code": 76,
    "filename": "AuroraStore-preload-4.8.4.apk",
    "url": "https://auroraoss.com/downloads/AuroraStore/Release/preload/AuroraStore-preload-4.8.4.apk",
    "size": 9362717,
    "sha256": "d2c0cd2ecaf3211ac31328e20649c654666e391afc9335478d8360a633bb159b",
    "signer_sha256": "4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f",
    "source_commit": "ad3d349edce82e119ba239f3a07b3afe028abd19",
    "source_url": "https://gitlab.com/AuroraOSS/AuroraStore/-/tree/ad3d349edce82e119ba239f3a07b3afe028abd19",
    "license": "GPL-3.0-or-later",
    "license_url": "https://gitlab.com/AuroraOSS/AuroraStore/-/raw/ad3d349edce82e119ba239f3a07b3afe028abd19/LICENSE",
    "license_sha256": "e16780aff588719628230b714c3beb9735d018df2d69f24f1927114cec180c38"
}
```

Required tests:

- `test_repository_lock_has_exact_approved_release`
- `test_lock_rejects_missing_unknown_or_wrong_typed_fields`
- `test_lock_rejects_http_floating_or_wrong_host_url`
- `test_lock_rejects_non_hex_or_uppercase_digests`
- `test_verify_rejects_size_and_sha256_mismatch_before_tools_run`
- `test_verify_requires_apksigner_verified_output_and_exact_signer`
- `test_verify_requires_package_version_code_and_version_name`
- `test_verify_requires_arm64_v8a_native_code`
- `test_matching_fixture_and_tool_output_pass`

The successful fake outputs must use the formats produced by the real tools:

```text
Verified using v1 scheme (JAR signing): true
Verified using v2 scheme (APK Signature Scheme v2): true
Signer #1 certificate SHA-256 digest: 4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f
```

```text
package: name='com.aurora.store' versionCode='76' versionName='4.8.4-preload' compileSdkVersion='37'
native-code: 'arm64-v8a' 'armeabi-v7a' 'x86' 'x86_64'
```

- [ ] **Step 2: Run the new test module and confirm the red state**

Run:

```bash
python3 -m unittest -v tools/tests/test_android15_aurora_store.py
```

Expected: import or file-not-found failures because the verifier and lock do not yet exist. Failures must be caused by the missing implementation, not test syntax.

- [ ] **Step 3: Commit the exact release lock**

Create `aurora-store.lock.json` using `EXPECTED_LOCK` exactly, formatted as UTF-8 JSON with a trailing newline. This pins the official Aurora Store preload release built from upstream tag 4.8.4, version code 76. The URL is versioned and HTTPS; it is not a `latest` redirect.

- [ ] **Step 4: Implement the verifier domain model and checks**

Implement `tools/check-aurora-store.py` with:

```python
@dataclasses.dataclass(frozen=True)
class AuroraLock:
    package_name: str
    version_name: str
    version_code: int
    filename: str
    url: str
    size: int
    sha256: str
    signer_sha256: str
    source_commit: str
    source_url: str
    license: str
    license_url: str
    license_sha256: str

```

Add a `@classmethod` named `from_path` with parameter `path: pathlib.Path` and return type `AuroraLock`. The loader must require exactly the dataclass fields, reject booleans where integers are expected, require positive `version_code` and `size`, require lowercase 64-character SHA-256 strings, require the filename `AuroraStore-preload-4.8.4.apk`, and validate URLs with `urllib.parse.urlparse`. The binary URL must have scheme `https`, host `auroraoss.com`, the exact versioned path from the lock above, and no query or fragment. Source and license URLs must use `https://gitlab.com/AuroraOSS/AuroraStore/` and contain the exact 40-character `source_commit`.

`verify_apk` must perform checks in this order:

1. APK is a regular file. When `logical_filename` is omitted, its basename must equal `lock.filename`; when supplied for a temporary download, the supplied value must equal `lock.filename`.
2. `stat().st_size == lock.size`.
3. streaming SHA-256 equals `lock.sha256`.
4. `apksigner verify --verbose --print-certs APK` exits 0, reports a verified scheme, and emits exactly one signer certificate SHA-256 equal to `lock.signer_sha256` after colon/space normalization.
5. `aapt2 dump badging APK` exits 0 and reports exact package name, version code, version name, plus `arm64-v8a` in `native-code`.

Call the injected runner with argument lists, `text=True`, `capture_output=True`, and `check=False`; never invoke a shell. Convert missing tools, nonzero returns, malformed output, or duplicate signer lines to `VerificationError` with non-sensitive text.

- [ ] **Step 5: Run focused verification**

Run:

```bash
python3 -m unittest -v tools/tests/test_android15_aurora_store.py
python3 -m py_compile tools/check-aurora-store.py tools/tests/test_android15_aurora_store.py
```

Expected: all lock and verifier tests pass; Python compilation exits 0.

- [ ] **Step 6: Commit the contract unit**

```bash
git add \
  aosptree/vendor/devices-community/gd_rpi4/compatibility-store/aurora-store.lock.json \
  tools/check-aurora-store.py \
  tools/tests/test_android15_aurora_store.py
git commit -m "test: lock Aurora Store prebuilt provenance"
```

### Task 2: Materialize the Verified APK and License Atomically

**Files:**

- Create: `aosptree/vendor/devices-community/gd_rpi4/compatibility-store/.gitignore`
- Create: `aosptree/vendor/devices-community/gd_rpi4/compatibility-store/LICENSE`
- Create: `aosptree/vendor/devices-community/gd_rpi4/compatibility-store/NOTICE.md`
- Create: `tools/prepare-aurora-store.sh`
- Modify: `tools/tests/test_android15_aurora_store.py`
- Modify: `unfold_aosp.sh`

**Interfaces:**

- CLI: `bash tools/prepare-aurora-store.sh [--lock PATH] [--destination DIR] [--aosp-root DIR]`
- Default lock and destination are the committed compatibility-store directory; default AOSP root is `aosptree`.
- On success the exact locked APK exists at `DESTINATION/AuroraStore-preload-4.8.4.apk`. A failure deletes only its same-directory temporary download and leaves no unverified final file.

- [ ] **Step 1: Add failing materializer tests**

Extend `test_android15_aurora_store.py` with a `MaterializerTests` class. Each test creates a temporary lock whose `size` and `sha256` match a local fixture, fake executable `curl`, `apksigner`, and `aapt2` programs, and an empty destination. Prepend the fake-command directory to `PATH`; pass explicit `--lock`, `--destination`, and `--aosp-root` arguments.

Required tests:

- `test_materializer_downloads_verifies_and_atomically_installs`
- `test_materializer_removes_temporary_file_when_download_fails`
- `test_materializer_does_not_replace_existing_verified_apk_on_bad_download`
- `test_materializer_reverifies_matching_existing_apk_without_network`
- `test_materializer_rejects_symlink_destination_or_final_apk`
- `test_unfold_invokes_materializer_after_repo_sync_before_build_inputs_are_used`

For the successful test, the fake `curl` must parse `--output` and copy the fixture there; fake inspection tools must return the exact signer/package outputs from Task 1. Assert the final bytes match, no `.tmp.*` file remains, and the final file was never present when the verifier was asked to reject the fixture.

- [ ] **Step 2: Confirm materializer tests fail for the missing script**

```bash
python3 -m unittest -v tools.tests.test_android15_aurora_store.MaterializerTests
```

Expected: failures identify the absent materializer and absent `unfold_aosp.sh` invocation.

- [ ] **Step 3: Add redistribution files**

Add `.gitignore` containing only:

```gitignore
/AuroraStore-preload-4.8.4.apk
/.AuroraStore-preload-4.8.4.apk.tmp.*
```

Add the exact upstream GPL-3.0-or-later text from the lock's immutable `license_url` as `LICENSE`. Before committing, verify:

```bash
printf '%s  %s\n' \
  e16780aff588719628230b714c3beb9735d018df2d69f24f1927114cec180c38 \
  aosptree/vendor/devices-community/gd_rpi4/compatibility-store/LICENSE | sha256sum --check
```

Create `NOTICE.md` identifying AuroraOSS, package/version/version code, the exact artifact SHA-256 and signer SHA-256, source commit and source URL, GPL-3.0-or-later, the unmodified/presigned redistribution status, and these limitations: third-party reverse-engineered service, free applications only, no Play Asset Delivery guarantee, catalog visibility varies by region/profile, and installability does not imply DRM playback support.

- [ ] **Step 4: Implement the safe materializer**

Create `tools/prepare-aurora-store.sh` with `set -euo pipefail`. Resolve all paths, reject a destination that is missing, outside the repository/AOSP checkout, a symlink, or not a directory, and reject a final path that is a symlink or non-regular file. Read `filename` and `url` from JSON with a short `python3 -c` invocation that imports `AuroraLock` from the checker, so schema validation is not duplicated.

If the final APK exists, call the verifier and return without downloading when it matches. Otherwise create a same-directory temporary file using:

```bash
temporary_apk="$(mktemp "$destination/.${filename}.tmp.XXXXXX")"
cleanup() { [[ -z "${temporary_apk:-}" ]] || rm -f -- "$temporary_apk"; }
trap cleanup EXIT INT TERM
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
  --output "$temporary_apk" "$url"
python3 "$repository_root/tools/check-aurora-store.py" \
  --lock "$lock" \
  --apk "$temporary_apk" \
  --logical-filename "$filename" \
  --apksigner "$aosp_root/prebuilts/sdk/tools/linux/bin/apksigner" \
  --aapt2 "$aosp_root/prebuilts/sdk/tools/linux/bin/aapt2"
mv -f -- "$temporary_apk" "$destination/$filename"
temporary_apk=""
```

The verifier CLI needs `--logical-filename` solely because a same-directory temporary download has a randomized basename; keep `verify_apk`'s physical basename check enabled by default and require the logical override to equal the lock's filename. Do not use `eval`, download through a shell-composed URL, or reuse a failed file.

- [ ] **Step 5: Attach materialization to source preparation**

In `unfold_aosp.sh`, call:

```bash
bash tools/prepare-aurora-store.sh --aosp-root aosptree
```

Place it after `repo sync` and the existing GApps LFS/verification step, when `prebuilts/sdk` is present, and before patch application or any build invocation. A verifier failure must propagate through the script's existing `set -e` behavior.

- [ ] **Step 6: Run focused tests and shell validation**

```bash
python3 -m unittest -v tools/tests/test_android15_aurora_store.py
bash -n tools/prepare-aurora-store.sh unfold_aosp.sh
python3 -m py_compile tools/check-aurora-store.py tools/tests/test_android15_aurora_store.py
git diff --check
```

Expected: all tests pass and syntax/diff checks exit 0.

- [ ] **Step 7: Commit the materialization unit**

```bash
git add \
  aosptree/vendor/devices-community/gd_rpi4/compatibility-store/.gitignore \
  aosptree/vendor/devices-community/gd_rpi4/compatibility-store/LICENSE \
  aosptree/vendor/devices-community/gd_rpi4/compatibility-store/NOTICE.md \
  tools/prepare-aurora-store.sh \
  tools/tests/test_android15_aurora_store.py \
  unfold_aosp.sh
git commit -m "build: verify and materialize Aurora Store"
```

### Task 3: Add the Presigned Product App and Enforce Its Trust Boundary

**Files:**

- Create: `aosptree/vendor/devices-community/gd_rpi4/compatibility-store/Android.bp`
- Modify: `aosptree/vendor/devices-community/gd_rpi4/device.mk`
- Modify: `tools/tests/test_android15_aurora_store.py`
- Modify: `tools/check-android15-port.sh`
- Modify: `.github/workflows/android15-port-validation.yml`
- Modify: `.github/workflows/build-android15-rpi4.yml`
- Modify: `tools/check-android15-build-cache.py`
- Modify: `tools/tests/test_android15_build_cache.py`
- Modify: `tools/tests/test_android15_runtime_performance.py`
- Modify: `docs/android-15-build.md`

**Interfaces:**

- Soong module: `AuroraStorePreload`
- Product inclusion: exactly one `PRODUCT_PACKAGES += AuroraStorePreload`
- Build baseline: `android-platform-15.0.0_r3-tesla-2026.22.1-runtime-v4`
- Repository gate: `tools/check-android15-port.sh` runs the full Aurora test module.

- [ ] **Step 1: Add failing integration and policy tests**

Extend `test_android15_aurora_store.py` with repository tests that assert:

1. `Android.bp` declares one `android_app_import` named `AuroraStorePreload` with `apk: "AuroraStore-preload-4.8.4.apk"`, `presigned: true`, `preprocessed: true`, `product_specific: true`, and disabled dex preopt.
2. The module contains none of `certificate:`, `privileged: true`, `system_ext_specific: true`, `vendor: true`, or an Aurora Services dependency.
3. `device.mk` adds `AuroraStorePreload` exactly once and introduces no `ro.product`, `ro.build.fingerprint`, `ih8sn`, Play certification, permission allowlist, or platform-signature change.
4. `unfold_aosp.sh` materializes before any `build_rpi4.sh` call.
5. `tools/check-android15-port.sh` and the validation workflow run the Aurora tests.
6. The build workflow and all baseline validators contain `runtime-v4` and no live `runtime-v3` contract remains.
7. `docs/android-15-build.md` names both stores, describes upstream first-run consent, and explicitly separates catalog/install success from Widevine/HDCP playback.

- [ ] **Step 2: Confirm the integration tests fail**

```bash
python3 -m unittest -v tools/tests/test_android15_aurora_store.py
```

Expected: only the new product, gate, baseline, and documentation assertions fail.

- [ ] **Step 3: Create the Soong module and product inclusion**

Create `compatibility-store/Android.bp`:

```bp
android_app_import {
    name: "AuroraStorePreload",
    owner: "auroraoss",
    apk: "AuroraStore-preload-4.8.4.apk",
    presigned: true,
    preprocessed: true,
    product_specific: true,
    dex_preopt: {
        enabled: false,
    },
}
```

Add to `aosptree/vendor/devices-community/gd_rpi4/device.mk` near other application packages:

```make
PRODUCT_PACKAGES += \
    AuroraStorePreload
```

Do not add a permission allowlist or product property.

- [ ] **Step 4: Increment every build-baseline contract together**

Replace the live baseline `android-platform-15.0.0_r3-tesla-2026.22.1-runtime-v3` with `android-platform-15.0.0_r3-tesla-2026.22.1-runtime-v4` in:

- `.github/workflows/build-android15-rpi4.yml`
- `tools/check-android15-build-cache.py`
- `tools/tests/test_android15_build_cache.py`
- `tools/tests/test_android15_runtime_performance.py`

Do not alter source revisions, cache mechanics, or codec properties.

- [ ] **Step 5: Add the repository gate and documentation**

In `tools/check-android15-port.sh`, add one failure-counted invocation:

```bash
if ! python3 -m unittest -v tools/tests/test_android15_aurora_store.py; then
  failures=$((failures + 1))
fi
```

Add the same test module to `.github/workflows/android15-port-validation.yml` beside the GApps prebuilt tests so pull-request validation catches binary-policy drift without downloading the APK.

Update `docs/android-15-build.md` with a **Compatibility Store (Aurora)** section that explains:

- Google Play remains installed and primary.
- Aurora's upstream first-run flow requires the user to accept its terms and choose anonymous or personal login.
- The store can select an ARM64 Android-compatible catalog profile without changing global Android identity.
- Paid applications and Play Asset Delivery-only applications are unsupported.
- Availability varies by profile, account region, and upstream service behavior.
- Installation does not create Play certification, Play Integrity, Widevine L1, or HDCP; protected playback is tested separately.
- Artifact version, source, signer, and license are recorded in `compatibility-store/NOTICE.md`.

- [ ] **Step 6: Run all local release gates**

```bash
python3 -m unittest -v tools/tests/test_android15_aurora_store.py
python3 -m unittest -v tools/tests/test_android15_build_cache.py
python3 -m unittest -v tools/tests/test_android15_runtime_performance.py
bash -n tools/prepare-aurora-store.sh unfold_aosp.sh tools/check-android15-port.sh
python3 -m py_compile \
  tools/check-aurora-store.py \
  tools/tests/test_android15_aurora_store.py
bash tools/check-android15-port.sh
git diff --check
```

Expected: every command exits 0 and the port validator ends with `Android 15 Raspberry Pi baseline validation passed.`

- [ ] **Step 7: Commit the product integration without triggering an image**

```bash
git add \
  aosptree/vendor/devices-community/gd_rpi4/compatibility-store/Android.bp \
  aosptree/vendor/devices-community/gd_rpi4/device.mk \
  tools/tests/test_android15_aurora_store.py \
  tools/check-android15-port.sh \
  .github/workflows/android15-port-validation.yml \
  .github/workflows/build-android15-rpi4.yml \
  tools/check-android15-build-cache.py \
  tools/tests/test_android15_build_cache.py \
  tools/tests/test_android15_runtime_performance.py \
  docs/android-15-build.md
git commit -m "feat: add verified compatibility store"
```

### Task 4: Verify the Real Artifact, Trigger One Image, and Validate Hardware

**Files:**

- Verify only before the trigger commit.
- The image-trigger commit contains no new behavior; it records the already verified release state.

**Interfaces:**

- Real prebuilt: `aosptree/vendor/devices-community/gd_rpi4/compatibility-store/AuroraStore-preload-4.8.4.apk`
- CI artifact: `TeslaAndroid-15-rpi4-<commit>` containing the flashable image.
- Hardware acceptance separates store discovery/install from DRM playback.

- [ ] **Step 1: Materialize and independently verify the official APK**

After `aosptree/prebuilts/sdk/tools/linux/bin/apksigner` and `aapt2` exist, run:

```bash
bash tools/prepare-aurora-store.sh --aosp-root aosptree
python3 tools/check-aurora-store.py \
  --lock aosptree/vendor/devices-community/gd_rpi4/compatibility-store/aurora-store.lock.json \
  --apk aosptree/vendor/devices-community/gd_rpi4/compatibility-store/AuroraStore-preload-4.8.4.apk \
  --apksigner aosptree/prebuilts/sdk/tools/linux/bin/apksigner \
  --aapt2 aosptree/prebuilts/sdk/tools/linux/bin/aapt2
```

Expected: download succeeds or an already matching file is reused; verifier prints its pass message. Confirm `git status --short` does not list the ignored APK.

- [ ] **Step 2: Complete the Apple TV experiment plan**

Execute `docs/superpowers/plans/2026-09-13-android15-apple-tv-playback-experiment.md` through its live decision record. A negative result is valid and does not block this image. A positive result must not add an unplanned global codec change; any production patch requires the follow-up evidence-specific plan described there.

- [ ] **Step 3: Perform final diff and trust-boundary review**

```bash
git status --short --branch
git log --oneline --decorate -8
git diff origin/android-15-bringup..HEAD --check
rg -n "certificate:|privileged: true|ih8sn|ro\.build\.fingerprint|ro\.product" \
  aosptree/vendor/devices-community/gd_rpi4/compatibility-store \
  aosptree/vendor/devices-community/gd_rpi4/device.mk
bash tools/check-android15-port.sh
```

Expected: only the approved compatibility-store and diagnostic work is present; the search produces no new trust-boundary violation; the full validator passes.

- [ ] **Step 4: Create and push the sole image-trigger commit**

Create an empty trigger commit only after all prior gates pass:

```bash
git commit --allow-empty -m "[build-image] build: validate streaming app compatibility"
git push origin android-15-bringup
```

Expected: validation and exactly one Android image workflow start for this commit.

- [ ] **Step 5: Watch CI and inspect artifact inventory**

```bash
validation_run_id="$(gh run list --workflow android15-port-validation.yml \
  --branch android-15-bringup --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$validation_run_id" --exit-status
build_run_id="$(gh run list --workflow build-android15-rpi4.yml \
  --branch android-15-bringup --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$build_run_id" --exit-status
gh api "repos/chilman408/android-raspberry-pi/actions/runs/$build_run_id/artifacts" \
  --jq '.artifacts[] | [.name, .expired, .size_in_bytes] | @tsv'
```

Expected: both workflows pass and a non-expired, nonempty `TeslaAndroid-15-rpi4-` artifact is listed.

- [ ] **Step 6: Perform the flashed-image acceptance test**

After flashing, capture these independent results:

1. `adb shell pm path com.aurora.store` resolves to the product partition and `dumpsys package` reports version code 76/version name `4.8.4-preload` with the pinned signer.
2. Google Play still launches; its certification status is not misrepresented.
3. Netflix still completes protected playback on the existing V4L2 AVC path.
4. Compatibility Store launches and shows its own consent/login flow; no account is preconfigured.
5. With an ARM64 Android-compatible Aurora profile, Disney+ and Hulu are discoverable when upstream exposes them for the selected region/profile, and split-package installation completes.
6. Reboot preserves installed applications and Aurora state under `/data`.
7. `dumpsys package com.aurora.store` shows no platform signature, privileged permission grant, device-owner role, or silent-install service.
8. TeslaAndroid browser streaming and virtual touchscreen latency remain at the prior validated level.

Record catalog visibility and installation separately from playback. If Disney+ or Hulu installs but refuses protected playback because the platform exposes Widevine L3 and unprotected HDCP, report that as a hardware/DRM limitation, not a Compatibility Store installation regression.
