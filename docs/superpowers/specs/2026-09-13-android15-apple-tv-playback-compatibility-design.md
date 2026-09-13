# Android 15 Apple TV playback compatibility design

**Date:** 2026-09-13
**Status:** Approved
**Target branch:** `android-15-bringup`

## Context

Apple TV 2.5.2 (`com.apple.atve.androidtv.appletv`, version code 2052089) installs from Google Play but reports an error during protected-video use. Live ADB evidence shows that the application does not crash. It opens Widevine sessions, starts encrypted AVC plus AAC playback, and repeatedly tears down and recreates the playback pipeline after roughly 30 to 33 seconds.

The platform reports Widevine security level L3, `OEMCrypto Level3`, current and maximum HDCP level `Unprotected`, and no L1 protected decode/output path. The Apple stream uses `c2.v4l2.avc.decoder` at 864x486 with encrypted input. Netflix now plays successfully through the same V4L2 AVC component, so globally disabling that decoder would discard a verified working result without isolating the Apple failure.

Several alarming-looking log messages are not the root cause by themselves. The legacy HIDL DRM lookup fails and then the AIDL Widevine factory opens successfully. SELinux denials are logged while the current diagnostic image is permissive. Codec2 reports unsupported query indices and a consumer-usage `BAD_INDEX`, yet surface configuration completes and decoded buffers continue for tens of seconds. The design therefore requires controlled runtime experiments before any permanent media-framework change.

## Goals

- Determine whether Apple TV's repeated playback restart is caused by the Raspberry Pi V4L2 AVC path, by protected-output capability, or by an application/server policy outside the image's control.
- Run the experiment over ADB without requiring the user to operate the Raspberry Pi.
- Preserve the working Netflix AVC/AV1 policy throughout diagnosis and in the final image.
- Change one codec variable at a time and restore the device's original persistent properties after every experiment, including failure paths.
- Promote a change to the image only when a repeatable ten-minute Apple TV playback test passes and Netflix still passes afterward.
- Record a clear unsupported result when the remaining failure requires Widevine L1 or protected HDCP output that Raspberry Pi 4 does not provide.

## Non-goals

- Faking Widevine L1, OEMCrypto hardware security, HDCP, Play certification, or Play Integrity.
- Extracting, weakening, bypassing, or redistributing protected content or DRM keys.
- Modifying, repackaging, hooking, or resigning the Apple TV application.
- Globally preferring software AVC in a released image.
- Treating HIDL fallback messages, permissive SELinux audit logs, or harmless Codec2 query warnings as proof of failure without an observed behavioral correlation.
- Claiming Apple TV support based only on app installation, license-session creation, or a short burst of decoded frames.

## Experiment architecture

### Baseline capture

Before changing runtime state, the experiment will capture:

- the device serial, boot ID, build fingerprint, product, build type, and verified-boot state;
- all `persist.ffmpeg_codec2.*` and relevant V4L2 Codec2 properties;
- the advertised AVC decoder list and ranks;
- Widevine security level, OEMCrypto mode, and current/maximum HDCP level;
- Apple TV and Netflix package/version information;
- Apple TV MediaDrm, MediaCodec, audio-focus, SurfaceFlinger, and process-crash metrics;
- a timestamped filtered logcat and screenshot of the user-visible Apple error when Android permits capture.

The capture will be stored as a diagnostic artifact, not committed application data. Account identifiers, authorization headers, cookies, DRM request/response payloads, and user credentials must be excluded.

### Controlled software-AVC probe

The first probe will test the narrow hypothesis that Apple's stream or repeated surface transition is incompatible with the current V4L2 AVC component. The probe will:

1. Save the exact current values and whether each property was originally set.
2. Disable FFmpeg's V4L2-backed H.264 path for the probe and temporarily rank the pure software AVC component ahead of `c2.v4l2.avc.decoder`.
3. Restart only the services required for codec enumeration, or reboot once if Android caches the codec list across service restarts.
4. Confirm through MediaCodec metrics that Apple TV actually selected the intended software component with encrypted input.
5. Observe one retained Apple stream for ten continuous minutes, recording decoder lifetime, playback duration, errors, restarts, CPU load, dropped frames, thermal state, and audio continuity.
6. Restore every property to its original value and reboot/restart media services even if selection or playback fails.
7. Confirm that the normal V4L2 AVC component is selected again after restoration.

The probe is diagnostic. Its settings will not be encoded in `device.mk` merely because the application opens. A software decoder that cannot accept encrypted buffers, exceeds sustainable Raspberry Pi CPU/thermal limits, drops excessive frames, or still restarts at the same boundary falsifies the codec-path hypothesis.

### Decision boundary

There are three possible outcomes:

- **Software AVC fails before decode or reproduces the same timed restart:** classify the problem as DRM/output/application policy rather than a generally broken V4L2 AVC decoder. Ship no codec change and document that this hardware exposes only Widevine L3 with unprotected output.
- **Software AVC plays continuously but is not operationally usable:** retain the working global codec policy and investigate an upstream V4L2 surface/session fix separately. Do not ship a global software preference.
- **Software AVC plays continuously with acceptable load:** implement an Apple-package-scoped codec-ordering compatibility rule. The rule may reorder only AVC decoder candidates observed by `com.apple.atve.androidtv.appletv`; every other package keeps the existing order. The permanent rule is still gated by automated tests and Netflix/YouTube regression validation.

If the Apple application bypasses framework codec enumeration and names `c2.v4l2.avc.decoder` explicitly, an app-scoped ordering rule is ineffective and will not be implemented. The result will instead be treated as requiring an upstream V4L2 fix or unsupported protected-output hardware.

## Permanent-change boundary

Any evidence-backed compatibility patch will live in a focused `frameworks/av` or framework-media patch, depending on the selection call path proven by the experiment. It must:

- match exactly `com.apple.atve.androidtv.appletv` and MIME type `video/avc`;
- change ordering only, without hiding components from other packages;
- leave AV1 component publication and the existing Raspberry Pi AV1 disable property untouched;
- contain an explanatory comment with the captured failure signature and experiment result;
- fall back to the unmodified codec list when package identity is unavailable;
- avoid new privileged services, persistent daemons, or application hooks.

No permanent code is created if the runtime probe does not meet the decision boundary. A negative experiment result is a valid completion for this workstream and does not block the separate Compatibility Store image.

## Error handling and recovery

- The experiment aborts before mutation when more than one ADB device is connected or the target is not the expected Raspberry Pi product.
- Original property presence and values are recorded before writes. Cleanup restores unset properties as unset rather than substituting guessed defaults.
- A cleanup trap runs on normal completion, command failure, timeout, or interrupted local execution.
- If ADB is lost during reboot, the harness waits for the same serial, boot completion, and stable package manager before continuing; it never selects a newly connected device implicitly.
- If automatic restoration cannot be confirmed, the harness stops and reports the exact residual properties rather than proceeding to Netflix validation.
- The experiment never clears application data, removes accounts, changes DRM provisioning, or resets the device.

## Verification

The pre-change baseline must demonstrate:

1. Netflix protected AVC playback succeeds on `c2.v4l2.avc.decoder` and the unusable FFmpeg AV1 component remains absent.
2. Apple TV opens a Widevine L3 session, uses encrypted AVC input, and reproduces the timed playback restart without an application crash.
3. Widevine reports L3 and unprotected HDCP before any codec experiment.

The software probe passes only when:

1. Metrics prove selection of a non-V4L2 software AVC decoder for Apple TV.
2. One protected title produces at least 600 seconds of continuous video and audio without decoder recreation, fatal MediaCodec error, license-session loop, or user-visible error.
3. Average CPU and thermals remain sustainable and the device does not throttle into unusable playback.
4. Restoration returns all codec properties and advertised ranks to their captured baseline.

A permanent app-scoped rule, if warranted, must additionally pass:

1. Unit or host-side regression tests proving exact package/MIME scoping and unchanged ordering for Netflix, YouTube, unknown packages, non-AVC video, and calls without package identity.
2. Existing `tools/tests/test_android15_runtime_performance.py`, including the Netflix AV1 and touchscreen feature assertions.
3. Full `tools/check-android15-port.sh` and patch-application validation.
4. Ten-minute Netflix protected playback after ten-minute Apple TV playback on the candidate image.
5. Five-minute YouTube playback, TeslaAndroid browser streaming, and virtual-touchscreen responsiveness on the same boot.

The workstream is recorded as hardware-limited rather than fixed if Apple TV still rejects playback while L3/unprotected-output evidence and restored Netflix playback remain stable.

## Rollout

The runtime probe occurs before a build-triggering commit. If it proves a safe scoped fix, that patch and its tests can share the image build with the Compatibility Store only after both workstreams independently pass repository validation. If it does not, the Compatibility Store proceeds alone and the release notes accurately state the Apple TV protected-output limitation.

The custom-domain and runtime TLS design remains paused and is not part of this experiment or image.

## Alternatives considered

- **Globally demote V4L2 AVC:** simple but risks Netflix, YouTube, performance, and thermals even though Netflix already proves the component can decode encrypted AVC.
- **Advertise Widevine L1 or HDCP through properties:** misrepresents a hardware security boundary and cannot provide secure decode buffers or protected display output.
- **Patch or resign Apple TV:** breaks the application's trust/update model and is outside the platform compatibility boundary.
- **Treat the issue as immediately unfixable:** the L3/unprotected-output evidence is strong, but the repeatable surface/session transition leaves one bounded codec hypothesis worth testing before closing the issue.
