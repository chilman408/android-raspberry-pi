# Android 15 Apple TV Playback Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine, with a reversible ADB experiment, whether Apple TV's repeated protected-playback restart is caused by the Raspberry Pi V4L2 AVC decoder path, while preserving the verified Netflix configuration and refusing to fake unsupported DRM/output capabilities.

**Architecture:** A Python harness isolates pure parsers and state models from an injected ADB command boundary. It captures a sanitized baseline, verifies one exact Raspberry Pi target, saves restorable persistent codec state, applies the narrow software-AVC probe, reboots the same serial, proves which decoder receives encrypted AVC, observes ten continuous minutes, and restores/verifies the original state in a `finally` path. The experiment produces evidence and a decision record; it does not guess a permanent framework patch before the selection call path and playback behavior are proven.

**Tech Stack:** Python 3 standard library and `unittest`, Android Debug Bridge, Android `getprop`, `dumpsys`, `cmd`, `logcat`, `uiautomator`, MediaMetrics/MediaCodec diagnostics, JSON evidence artifacts.

**Spec:** `docs/superpowers/specs/2026-09-13-android15-apple-tv-playback-compatibility-design.md`

## Global Constraints

- Target only ADB serial `10000000f93771d0` when it reports product `tesla_android_rpi4` and device `gd_rpi4`; never fall back to the first connected device.
- Preserve the working global Netflix AVC/AV1 policy. Probe properties are temporary and must not be added to `device.mk` by this plan.
- Do not clear app data, remove accounts, reprovision Widevine, log credentials/license payloads, bypass DRM, fake Widevine L1/HDCP/Play certification, modify Apple TV, or weaken Android security.
- Treat successful license-session creation, a few decoded frames, and process survival as insufficient. Positive playback requires the same encrypted AVC session to remain active for at least 600 continuous seconds.
- A negative or operationally unusable software-decoder result is a valid completion and must not block the separate Compatibility Store image.
- Do not add `[build-image]` to any commit in this plan. The Compatibility Store plan owns the final image trigger after this plan has recorded its result.

---

### Task 1: Build and Test Pure Diagnostic Models and Parsers

**Files:**

- Create: `tools/apple_tv_codec_probe.py`
- Create: `tools/tests/test_apple_tv_codec_probe.py`

**Interfaces:**

- `PropertyState(name: str, present: bool, value: str)`
- `CodecEvent(timestamp: float, package: str, mime: str, codec: str, encrypted: bool, event: str)`
- `PlaybackObservation(outcome: ProbeOutcome, codec: str | None, continuous_seconds: float, restart_count: int, reason: str)`
- `ProbeOutcome`: `NO_SOFTWARE_DECODER`, `NO_ENCRYPTED_AVC`, `TIMED_RESTART`, `UNUSABLE_LOAD`, `PLAYED_600_SECONDS`, `HARNESS_ERROR`
- Pure functions:
  - `parse_getprop_listing(text: str) -> dict[str, PropertyState]`
  - `parse_codec_inventory(text: str) -> Sequence[str]`
  - `parse_widevine_state(text: str) -> dict[str, str]`
  - `parse_codec_events(text: str) -> list[CodecEvent]`
  - `classify_observation(events: Sequence[CodecEvent], required_seconds: int = 600) -> PlaybackObservation`
  - `redact_diagnostic_text(text: str) -> str`

- [ ] **Step 1: Write failing parser and classification tests**

Create `tools/tests/test_apple_tv_codec_probe.py` and import every interface named above directly from `tools.apple_tv_codec_probe`. Add focused tests with small literal fixtures for:

- `test_getprop_parser_distinguishes_absent_empty_and_set_values`
- `test_codec_inventory_finds_v4l2_and_pure_ffmpeg_avc_components`
- `test_widevine_parser_reports_l3_oemcrypto_level3_and_unprotected_hdcp`
- `test_codec_event_parser_keeps_only_apple_encrypted_avc_events`
- `test_classifier_rejects_short_decode_burst`
- `test_classifier_reports_restart_at_thirty_second_boundary`
- `test_classifier_requires_one_continuous_six_hundred_second_session`
- `test_classifier_rejects_merged_duration_from_multiple_sessions`
- `test_redactor_removes_authorization_cookie_account_and_drm_payload_values`

Use a timeline fixture in which `c2.v4l2.avc.decoder` starts at 100.0, stops at 131.5, and starts again at 133.0; expected outcome is `TIMED_RESTART`, continuous duration 31.5, and restart count 1. Use a second fixture in which `c2.ffmpeg.avc.decoder` starts with `crypto=1` at 100.0 and remains active through a sample at 701.0; expected outcome is `PLAYED_600_SECONDS` with at least 601 seconds.

Redaction must replace values, not merely delete field names, for case-insensitive patterns matching `Authorization:`, `Cookie:`, `Set-Cookie:`, `account`, `email`, `token`, `licenseRequest`, `licenseResponse`, `keySetId`, and JSON-like `drmPayload`. Preserve timestamps, package names, codec names, MIME types, numeric error codes, and thermal/CPU measurements.

- [ ] **Step 2: Confirm the test module is red**

```bash
python3 -m unittest -v tools/tests/test_apple_tv_codec_probe.py
```

Expected: import failure because `tools/apple_tv_codec_probe.py` does not exist.

- [ ] **Step 3: Implement immutable models and pure parsers**

Create the module with `dataclasses.dataclass(frozen=True)`, `enum.Enum`, `re`, `json`, and `typing`. Parsing rules must be deterministic:

- `getprop` listing accepts only lines shaped `[name]: [value]`; presence comes from the listing, while a requested name missing from the map is absent.
- Codec inventory returns unique component names in first-seen order.
- Widevine parsing recognizes both `securityLevel`/`security level`, `OEMCrypto`, current HDCP, and maximum HDCP labels without inventing defaults.
- Codec events require `package=com.apple.atve.androidtv.appletv`, MIME `video/avc`, an explicit component name, and `crypto=1` or equivalent `encrypted=true`. Associate start/sample/stop events by session ID so durations from restarted sessions cannot be summed.
- `classify_observation` accepts only a single uninterrupted software-AVC session (`c2.ffmpeg.avc.decoder`) meeting the duration. Missing software component, missing encrypted session, timed restart, and short/no-sample failure must return distinct outcomes.

Expose a `main()` stub that recognizes no commands yet and returns usage exit 2; orchestration is added in Task 2.

- [ ] **Step 4: Run the pure unit**

```bash
python3 -m unittest -v tools/tests/test_apple_tv_codec_probe.py
python3 -m py_compile tools/apple_tv_codec_probe.py tools/tests/test_apple_tv_codec_probe.py
```

Expected: all parser/model tests pass and compilation exits 0.

- [ ] **Step 5: Commit the parser unit**

```bash
git add tools/apple_tv_codec_probe.py tools/tests/test_apple_tv_codec_probe.py
git commit -m "test: model Apple TV codec probe evidence"
```

### Task 2: Implement Safe ADB Capture, Mutation, Reboot, and Restoration

**Files:**

- Modify: `tools/apple_tv_codec_probe.py`
- Modify: `tools/tests/test_apple_tv_codec_probe.py`

**Interfaces:**

- `AdbClient(serial: str, runner=subprocess.run)` with `run`, `shell`, `root`, `reboot`, `wait_for_boot`, and `list_devices` methods.
- `ProbeState(serial, product, device, boot_id, properties, captured_at)` serialized as versioned JSON.
- `capture_baseline(client: AdbClient, output_dir: pathlib.Path) -> ProbeState`
- `apply_software_avc_probe(client: AdbClient, state: ProbeState) -> None`
- `restore_probe_state(client: AdbClient, state: ProbeState) -> None`
- CLI:
  - `baseline --serial SERIAL --output DIR`
  - `probe --serial SERIAL --output DIR --duration-seconds 600`
  - `restore --serial SERIAL --state STATE_JSON`

- [ ] **Step 1: Add failing orchestration tests at the ADB boundary**

Extend the test module with a `RecordingRunner` that accepts subprocess argument lists and returns queued `CompletedProcess` values. Do not mock parser, state, classification, or restoration functions.

Required tests:

- `test_aborts_before_root_or_setprop_when_multiple_devices_are_connected`
- `test_aborts_before_mutation_for_wrong_product_or_device`
- `test_aborts_before_mutation_when_target_property_is_absent`
- `test_capture_records_exact_property_presence_values_serial_and_boot_id`
- `test_probe_sets_only_h264_backend_and_video_rank_properties`
- `test_reboot_waits_for_same_serial_boot_completed_and_package_manager`
- `test_probe_confirms_software_avc_is_advertised_before_launching_apple`
- `test_probe_restores_exact_values_after_success`
- `test_probe_restores_and_reboots_after_timeout_exception`
- `test_restore_verification_failure_reports_exact_residual_properties`
- `test_probe_never_clears_package_data_accounts_or_drm_provisioning`
- `test_evidence_writer_redacts_before_persisting_text`

The exact temporary mutations are:

```text
persist.ffmpeg_codec2.v4l2.h264=false
persist.ffmpeg_codec2.rank.video=16
```

The test baseline must contain the current known values `true` and `128`, then assert restoration uses those saved values rather than constants. Also run the test with different saved values to prove the implementation is data-driven.

Android's public `setprop` interface cannot reliably delete a persistent property. Therefore, the harness must abort before mutation if either targeted property was originally absent. This makes the restoration promise exact on this image, where both properties are product-defined, instead of turning an absent property into a guessed or empty persistent value.

- [ ] **Step 2: Confirm new tests fail for missing orchestration**

```bash
python3 -m unittest -v tools/tests/test_apple_tv_codec_probe.py
```

Expected: parser tests pass; new ADB/state tests fail because the interfaces are absent.

- [ ] **Step 3: Implement the ADB client and target guard**

All host commands must be lists beginning `adb`, `-s`, and the explicit serial, except `adb devices -l`. Call `subprocess.run(command, text=True, capture_output=True, timeout=timeout_seconds, check=False)` and turn nonzero exit, timeout, or a changed serial into `ProbeError`.

Before `adb root` or any write:

1. Parse `adb devices -l` and require exactly one state=`device` entry.
2. Require its serial equals the CLI serial.
3. Read `ro.product.name` and require `tesla_android_rpi4`.
4. Read `ro.product.device` and require `gd_rpi4`.
5. Capture `/proc/sys/kernel/random/boot_id`.

`wait_for_boot` must repeatedly target the same serial, require `getprop sys.boot_completed` equals `1`, and require `cmd package list packages com.apple.atve.androidtv.appletv` succeeds. Use `time.monotonic()` and a bounded timeout; it must never discover/reselect a device during the wait.

- [ ] **Step 4: Implement sanitized baseline capture**

Write only under the caller-provided output directory after rejecting a symlink or non-directory. Use atomic JSON/text writes through same-directory temporary files and `os.replace`.

Capture:

- serial, product/device/model, boot ID, build fingerprint/type, verified-boot state;
- full `getprop` output filtered in memory to `persist.ffmpeg_codec2.*`, `ro.vendor.v4l2_codec2.*`, and `ro.vendor.ffmpeg_codec2.*`;
- `dumpsys media.player`, parsed AVC component inventory and ranks;
- `dumpsys media.drm`, parsed Widevine/OEMCrypto/HDCP fields;
- `dumpsys package` version lines for Apple TV and Netflix;
- filtered MediaMetrics/MediaCodec, activity/process crash, CPU, and thermal text.

Every raw text artifact must pass through `redact_diagnostic_text` before writing. Do not persist unfiltered network, account, or DRM request/response logs.

- [ ] **Step 5: Implement mutation and unconditional restoration**

The `probe` command must execute this structure:

```python
state = capture_baseline(client, output_dir)
mutated = False
try:
    require_restorable_target_properties(state)
    client.root()
    apply_software_avc_probe(client, state)
    mutated = True
    client.reboot()
    client.wait_for_boot()
    verify_probe_properties(client)
    verify_software_avc_advertised(client)
    observation = run_playback_observation(client, output_dir, duration_seconds)
    write_result(output_dir, state, observation)
finally:
    if mutated:
        restore_probe_state(client, state)
```

Restoration must set each targeted property to its exact saved value, reboot, wait for the same serial, reread both properties, and require exact equality. It must then require `c2.v4l2.avc.decoder` is advertised again. If restoration fails, write a sanitized `RESTORE_FAILED.json` with the expected and observed property values and exit nonzero; do not start Netflix or another experiment.

The standalone `restore` command is an emergency re-entry using the already written `state.json`. Validate its schema/version, serial, product, and device before writing.

- [ ] **Step 6: Implement autonomous Apple playback start and observation**

After proving `c2.ffmpeg.avc.decoder` is advertised:

1. Clear logcat buffers only; do not clear application data.
2. Start `com.apple.atve.androidtv.appletv/com.apple.android.tv.MainActivity` with `am start`.
3. Dump the foreground UI with `uiautomator dump /dev/tty`. If the focused enabled node is labeled `Play`, `Resume`, `Retry`, or `Watch Now`, tap its bounds center once. Do not tap account, purchase, subscribe, sign-in, or destructive controls. If playback resumes automatically, send no input.
4. Within 60 seconds require a MediaMetrics/MediaCodec event for Apple, encrypted `video/avc`, and `c2.ffmpeg.avc.decoder`. If it never appears, classify `NO_ENCRYPTED_AVC` and restore.
5. Poll metrics, process state, `top -b -n 1`, and thermal services at intervals no longer than ten seconds for the requested duration. Maintain session identity; a teardown/recreate resets continuous duration and increments restart count.
6. Mark `UNUSABLE_LOAD` when sustained thermal severe/critical state, process death, or inability to maintain real-time progress occurs. Retain the numeric observations in the result.
7. Take a screenshot only through `screencap -p` and only when it contains no notification shade/account chooser; otherwise omit it.

The harness must complete and restore without asking the user to touch the Raspberry Pi. Inability to safely resume content is a recorded `NO_ENCRYPTED_AVC` result, not permission to automate account or purchase UI.

- [ ] **Step 7: Run the complete harness unit**

```bash
python3 -m unittest -v tools/tests/test_apple_tv_codec_probe.py
python3 -m py_compile tools/apple_tv_codec_probe.py tools/tests/test_apple_tv_codec_probe.py
```

Expected: all parser, command-boundary, cleanup, restoration, and redaction tests pass.

- [ ] **Step 8: Commit the safe harness**

```bash
git add tools/apple_tv_codec_probe.py tools/tests/test_apple_tv_codec_probe.py
git commit -m "tools: add reversible Apple TV codec probe"
```

### Task 3: Attach the Harness Tests to Repository Validation

**Files:**

- Modify: `tools/check-android15-port.sh`
- Modify: `.github/workflows/android15-port-validation.yml`
- Modify: `tools/tests/test_apple_tv_codec_probe.py`

**Interfaces:**

- Local port validation runs the unit module without requiring a connected device.
- GitHub Actions runs only fixtures/mocked ADB boundary; it never performs a live device probe.

- [ ] **Step 1: Add failing repository-wiring tests**

Add `RepositoryIntegrationTests` asserting:

- `tools/check-android15-port.sh` invokes `python3 -m unittest -v tools/tests/test_apple_tv_codec_probe.py` inside its failure counter.
- `.github/workflows/android15-port-validation.yml` runs the same module.
- Neither workflow nor any product makefile invokes `apple_tv_codec_probe.py probe`.
- `device.mk` still contains `persist.ffmpeg_codec2.v4l2.h264=true` and `persist.ffmpeg_codec2.rank.video=128`.
- No file changed by this plan adds Apple package-specific framework behavior or a global software AVC preference.

- [ ] **Step 2: Confirm only wiring tests fail**

```bash
python3 -m unittest -v tools/tests/test_apple_tv_codec_probe.py
```

Expected: repository-wiring assertions fail; all harness behavior tests pass.

- [ ] **Step 3: Add the unit-only validation calls**

In `tools/check-android15-port.sh` add:

```bash
if ! python3 -m unittest -v tools/tests/test_apple_tv_codec_probe.py; then
  failures=$((failures + 1))
fi
```

Add the same module beside the other Python unit modules in `.github/workflows/android15-port-validation.yml`. Do not add serials, ADB setup, or live-probe commands to CI.

- [ ] **Step 4: Run focused and full repository gates**

```bash
python3 -m unittest -v tools/tests/test_apple_tv_codec_probe.py
bash -n tools/check-android15-port.sh
bash tools/check-android15-port.sh
git diff --check
```

Expected: every command exits 0; the product codec properties remain unchanged.

- [ ] **Step 5: Commit validation wiring**

```bash
git add \
  tools/check-android15-port.sh \
  .github/workflows/android15-port-validation.yml \
  tools/tests/test_apple_tv_codec_probe.py
git commit -m "test: enforce reversible Apple TV diagnostics"
```

### Task 4: Run the Live Baseline and Ten-Minute Software-AVC Probe

**Files:**

- Create locally, do not commit: one timestamped directory under `out/apple-tv-probe/` containing the state, sanitized baseline, observations, result, and optional screenshot.
- Create after result: `docs/android-15-apple-tv-playback.md`

**Interfaces:**

- Target serial: `10000000f93771d0`
- Probe properties: only `persist.ffmpeg_codec2.v4l2.h264` and `persist.ffmpeg_codec2.rank.video`
- Required observation: one Apple encrypted AVC software-decoder session lasting at least 600 continuous seconds.

- [ ] **Step 1: Confirm ADB target and capture an immutable baseline**

From PowerShell or a shell with `adb` on `PATH`, create a timestamped output directory and run:

```powershell
$probeOutput = Join-Path (Resolve-Path .) ('out/apple-tv-probe/' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path $probeOutput | Out-Null
python tools/apple_tv_codec_probe.py baseline --serial 10000000f93771d0 --output $probeOutput
```

Expected baseline evidence:

- one online device with expected product/device;
- Apple TV version 2.5.2/version code 2052089 and current Netflix version captured;
- Widevine L3, OEMCrypto Level3, current/max HDCP `Unprotected`;
- `c2.v4l2.avc.decoder` advertised;
- saved property values include H.264 V4L2 `true` and video rank `128` on the current image.

If these differ, stop before mutation and diagnose the new baseline. Do not edit the harness to force acceptance.

- [ ] **Step 2: Reproduce and record the current V4L2 failure signature**

With the unmodified properties, relaunch Apple TV through ADB and use the harness baseline collection for at least 90 seconds. Confirm the existing signature: encrypted AVC selects `c2.v4l2.avc.decoder`, the pipeline restarts around 30–33 seconds, and the Apple process does not crash. Confirm Netflix protected playback remains successful on the same V4L2 component.

If the baseline no longer reproduces, do not run the probe; capture the changed behavior and classify the original report as non-reproducible on this image.

- [ ] **Step 3: Run the reversible 600-second probe**

```powershell
python tools/apple_tv_codec_probe.py probe `
  --serial 10000000f93771d0 `
  --output $probeOutput `
  --duration-seconds 600
```

Expected operational behavior:

- the harness saves state before `adb root`/mutation;
- the same device reappears after both probe and restoration reboots;
- the probe confirms actual `c2.ffmpeg.avc.decoder` selection with encrypted AVC, rather than inferring it from properties;
- the command restores exact original properties even for a negative result or exception;
- final property verification and restored V4L2 inventory pass.

If the host process is interrupted, resume only with:

```powershell
python tools/apple_tv_codec_probe.py restore `
  --serial 10000000f93771d0 `
  --state (Join-Path $probeOutput 'state.json')
```

Do not continue until restoration reports success.

- [ ] **Step 4: Verify the working Netflix configuration after restoration**

After the harness restores and reboots, use ADB metrics/logcat to verify:

1. `persist.ffmpeg_codec2.v4l2.h264=true` and `persist.ffmpeg_codec2.rank.video=128` (or the exact captured originals if the baseline legitimately differed).
2. `c2.v4l2.avc.decoder` is advertised and selected by Netflix.
3. Netflix protected playback completes without the black-screen regression.
4. The unusable FFmpeg AV1 path remains excluded by the existing AV1 policy.

Any restoration or Netflix failure is a harness failure requiring repair before the image work proceeds, regardless of the Apple result.

- [ ] **Step 5: Write the evidence-backed decision document**

Create `docs/android-15-apple-tv-playback.md` with:

- date, build fingerprint, Apple/Netflix versions, and sanitized artifact directory;
- baseline codec, timed-restart duration, Widevine/OEMCrypto/HDCP facts;
- software probe codec actually selected, continuous duration, restart count, CPU/thermal observations, and exact `ProbeOutcome`;
- restoration verification and Netflix regression result;
- one of these conclusions, selected only from measured evidence:
  - `NO_SOFTWARE_DECODER` or `NO_ENCRYPTED_AVC`: software fallback cannot service this encrypted stream;
  - `TIMED_RESTART`: failure survives decoder change and points to DRM/output/application policy;
  - `UNUSABLE_LOAD`: software path changes behavior but is not releasable on Raspberry Pi 4;
  - `PLAYED_600_SECONDS`: app-scoped selection is worth a separate production patch plan.

State plainly that the image exposes only Widevine L3 and unprotected HDCP and that the work does not create or emulate stronger DRM capabilities.

- [ ] **Step 6: Run final validation and commit only the decision record**

```bash
python3 -m unittest -v tools/tests/test_apple_tv_codec_probe.py
bash tools/check-android15-port.sh
git diff --check
git status --short
git add docs/android-15-apple-tv-playback.md
git commit -m "docs: record Apple TV codec probe result"
```

Expected: diagnostic files under `out/` are not committed; the decision document contains no credentials, DRM payloads, cookies, emails, or account IDs.

### Task 5: Enforce the Evidence Decision Boundary

**Files:**

- Inspect: `docs/android-15-apple-tv-playback.md`
- No product/framework files change for a negative or unusable outcome.
- For `PLAYED_600_SECONDS` only, create a new implementation plan before code changes.

**Interfaces:**

- Input: the exact `ProbeOutcome` from Task 4.
- Output: either a documented unsupported result or a new evidence-specific plan; never an untested global codec change.

- [ ] **Step 1: Handle a negative or unusable result**

For `NO_SOFTWARE_DECODER`, `NO_ENCRYPTED_AVC`, `TIMED_RESTART`, or `UNUSABLE_LOAD`, verify:

```bash
git diff origin/android-15-bringup..HEAD -- \
  aosptree/vendor/devices-community/gd_rpi4/device.mk \
  patches-aosp/glodroid/vendor/ffmpeg_codec2 \
  patches-aosp/platform/frameworks/av \
  patches-aosp/platform/frameworks/base
```

Expected: this plan introduced no released codec/media change. Mark Apple TV protected playback unsupported on this Widevine L3/unprotected-output platform and proceed with the Compatibility Store image plan.

- [ ] **Step 2: Handle a positive result without guessing the production call path**

Only for `PLAYED_600_SECONDS`, inspect live evidence to determine whether Apple enumerated codec candidates through framework `MediaCodecList` or explicitly named the V4L2 component. If it explicitly named the component, document that an ordering rule cannot help and make no patch.

If framework enumeration is proven, use `superpowers:brainstorming` and `superpowers:writing-plans` to create a separate plan named:

```text
docs/superpowers/plans/2026-09-13-android15-apple-tv-scoped-avc-ordering.md
```

That plan must name the exact proven source/patch location and include tests that match only package `com.apple.atve.androidtv.appletv` plus MIME `video/avc`, preserve ordering for every other package, preserve AV1 policy, fall back unchanged when package identity is unavailable, and rerun ten-minute Apple plus Netflix/YouTube/TeslaAndroid regressions. Do not implement that patch within this diagnostic plan.

- [ ] **Step 3: Hand the completed experiment to the image release gate**

Return to Task 4 of `docs/superpowers/plans/2026-09-13-android15-play-catalog-compatibility-store.md`. A negative result satisfies this dependency. A positive result satisfies it only after either the evidence proves explicit component selection (no patch possible) or the new scoped-ordering plan is approved and executed.
