# Android 15 Apple TV playback decision

Date: 2026-09-21. Evidence reviewed at harness revision `6ef5caf`.

Apple TV protected playback is **unsupported/inconclusive on this image**. The read-only observation returned `NO_ENCRYPTED_AVC`: no qualifying explicit encrypted Apple AVC session was established. The reversible software-AVC probe was not authorized by the evidence gate and was not run. This result releases the negative-result dependency to Task 4 of the [Compatibility Store plan](superpowers/plans/2026-09-13-android15-play-catalog-compatibility-store.md); it does not establish playback support or authorize a codec/framework change.

## Baseline and evidence

The saved baseline passed the sole-target check for authorized serial `10000000f93771d0`, product `tesla_android_rpi4`, device `gd_rpi4` (model `rpi4`). Build type is `userdebug`, verified boot state `orange`, and fingerprint:

```text
RaspberryPI/tesla_android_rpi4/gd_rpi4:VanillaIceCream/APM1.240918.010/eng.gha-ru.00000000.000000:userdebug/test-keys
```

| Baseline fact | Measured value |
| --- | --- |
| Apple TV | `com.apple.atve.androidtv.appletv`, 2.5.2, version code 2052089 |
| Netflix | `com.netflix.mediaclient`, 9.83.0 build 6 74540, version code 74540 |
| Widevine | L3 |
| OEMCrypto | Level3 |
| Current / maximum HDCP | Unprotected / Unprotected |
| AVC inventory | `c2.v4l2.avc.decoder` advertised |
| `persist.ffmpeg_codec2.v4l2.h264` | `true` |
| `persist.ffmpeg_codec2.rank.video` | `128` |
| `ro.vendor.ffmpeg_codec2.rank.video.av1` | `4294967295` (existing exclusion policy) |

The image exposes only Widevine L3 and unprotected HDCP. This work does not create or emulate stronger DRM, output protection, certification, or entitlement capabilities.

Sanitized local artifacts are retained, uncommitted, under `aosptree/out/target/product/gd_rpi4/apple-tv-probe/20260921-075823/`. This is the Windows physical mapping of logical `out/apple-tv-probe/20260921-075823/`; the tracked `out` link-file remains unchanged. `baseline/` holds immutable state and metadata. The final observation evidence is in `observe-after-redaction/result.json`, `events.json`, and `playback.txt`, with its own immutable baseline. Artifacts are ignored and are not distributed with this document.

## Observation and decision boundary

The read-only `observe` action requested 90 seconds, exited 0, and stopped at the acquisition deadline with the exact reason `no matching encrypted AVC event within 60 seconds`. Eight diagnostic samples cover approximately 5.03-65.67 seconds after observation start; polling accounts for the deadline overshoot. It did not complete a 90-second qualifying playback reproduction.

| Recorded observation field | Result |
| --- | --- |
| `ProbeOutcome` | `NO_ENCRYPTED_AVC` |
| Mode | `observe` (not a software probe) |
| Selected qualifying codec | `null` / unproven |
| Normalized qualifying events | 0 |
| Continuous playback / media progress | 0 / 0 seconds established |
| Recorded restart count | 0; no qualifying session from which to measure restarts |
| Diagnostic samples | 8 |

Sanitized diagnostics contain Apple DRM-session activity, V4L2 AVC component creation, and MediaCodec errors, but no Apple-owned explicit `video/avc` aggregate satisfying the encryption/session requirements. Separate DRM, PID/UID, component and timestamp records cannot be combined to assert encrypted Apple AVC selection. The original 30-33-second encrypted-AVC restart signature was not established by this observation. The result does not prove that the stream is clear, that no playback was attempted, that V4L2 caused the failure, or that software fallback cannot service the stream.

No software-decoder probe was run, and actual software selection was not established. There is no 600-second software playback measurement, software restart measurement, or software load verdict. CPU percentage arrays were empty. All eight samples reported thermal status 0, but the filtered thermal dump identifies a test temperature sensor; the collected numeric values are not evidence of sustained software-decoding load or usable real-device thermal headroom. Process presence likewise does not establish playback progress.

The failed evidence gate prevents root/property mutation/reboot: baseline reproduction, explicit encrypted AVC selection and session progress were prerequisites. No permanent media policy change or Apple-specific ordering patch is supported by these measurements. Future investigation must first obtain direct qualifying native evidence and pass the same gate.

## Configuration preservation and privacy

Restoration was **not required**, because no `probe`, root, property mutation, reboot or `restore` occurred. Independent post-observation readback confirmed the original H.264 V4L2 `true`, video rank `128`, and AV1 rank `4294967295`; the device remained online. The existing Netflix configuration was preserved. Netflix protected playback and decoder selection were not reverified in this observation, so no playback regression-pass claim is made.

The first observation exposed an unredacted camelCase `logSessionId` field and was manually sanitized. The test-first fix at `6ef5caf` completed scoped review before the repeated observation. The repeat artifact audit found 228 single redaction markers and 154 values shaped exactly `[REDACTED]sessionId=[REDACTED]`, with zero unexpected/raw values. The second shape contains adjacent redacted fields with collapsed whitespace, not leaked identifier data. Separator readability remains a deferred minor. No credentials, DRM payloads, cookies, emails, account identifiers or raw session identifiers are included in this decision record.

## Host verification and handoff

The focused Apple suite ran 103 tests: 102 passed and one symlink-privilege test was skipped on this Windows host. Document whitespace checks passed with `git diff --check` and `git diff --cached --check`. The full port validator was not rerun for this documentation step; earlier runs remain limited by the Windows Store `python3` alias, WSL without a distribution, mixed-shell subprocess behavior and missing symlink privilege. Existing Linux CI/full validation remains required before release.

This is the final negative/inconclusive decision record for the Apple experiment, not a successful software-decoder trial. The Compatibility Store plan may proceed through its own remaining verification and release gates while retaining Apple TV protected playback as unsupported/inconclusive. No image build is triggered by this record.
