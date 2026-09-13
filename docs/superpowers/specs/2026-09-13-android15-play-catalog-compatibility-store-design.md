# Android 15 Play catalog compatibility store design

**Date:** 2026-09-13
**Status:** Approved
**Target branch:** `android-15-bringup`

## Context

The Raspberry Pi 4 image now boots reliably and Netflix plays video after the AV1 codec policy fix. Disney+ and Hulu remain absent from Google Play even after the device has finished first-boot work and Google Play's own **Fix device issue** action has completed. Google Play reports the device as **not certified** and the repair action reports that it cannot fix the certification issue.

This is a real distribution constraint rather than another missing feature declaration. The image is a Raspberry Pi `userdebug` build with test keys and an unlocked verified-boot state. Google Play certification and Play Integrity cannot be created by changing model, fingerprint, or feature properties. The former `ih8sn` runtime property rewrite must remain disabled because mutating initialized Android 15 read-only properties previously destabilized core system services.

The practical goal is therefore to add a separate, honest catalog-access path for free applications that publishers hide from this uncertified Google Play device, while preserving Google Play for applications that it already serves correctly.

## Goals

- Keep Google Play, Google Play services, Netflix, YouTube, and the current device identity unchanged.
- Ship Aurora Store 4.8.4 (`com.aurora.store`, version code 76) as a separate store that release documentation identifies as the **Compatibility Store** path.
- Allow users to discover and install free ARM64 applications, including Disney+ and Hulu when Google makes compatible packages available to the selected Aurora device profile.
- Support split APK installation through Android's normal package installer.
- Obtain the APK only from an immutable official AuroraOSS release location and verify both its SHA-256 digest and signing-certificate digest before every image build.
- Preserve Aurora application data across OTA updates and fail safely if the pinned artifact cannot be verified.
- Include the license, upstream source location, exact binary provenance, and redistribution notice required for the GPL-3.0 application.
- Add repository tests that prevent the compatibility store from becoming an unpinned or silently replaced binary.

## Non-goals

- Claiming or emulating Google Play Protect certification, Play Integrity verdicts, Widevine L1, or HDCP.
- Globally impersonating a Pixel, Shield, or other certified product through Android system properties.
- Re-enabling `ih8sn` or modifying initialized read-only properties at runtime.
- Replacing or disabling Google Play.
- Bundling a Google account, Aurora session, authentication token, or user credential.
- Supporting paid-app purchase or download, Play Asset Delivery-only applications, or applications for which Google does not expose a compatible package.
- Guaranteeing that an installed streaming application will license or play protected content on Widevine L3 hardware.
- Silently accepting Aurora's terms or choosing an account on behalf of the device owner.

## Architecture

### Secondary-store boundary

Aurora Store will be installed alongside Google Play under its upstream package name, launcher label, and signature. Documentation and release notes will call it **Compatibility Store (Aurora)** so its purpose is clear without modifying and resigning the upstream APK. Google Play remains the primary store for ordinary installs and updates.

The image will not grant Aurora privileged permissions, make it a device owner, install deprecated Aurora Services, or place it in the platform-signature trust boundary. Aurora will use Android's standard session/package installer. First launch will still show Aurora's own consent and anonymous-or-personal-account choice because the image must not pre-authorize a third-party service or embed shared credentials.

Aurora's built-in device-spoofing feature changes only the catalog identity used by Aurora's Google Play protocol client. It must not write global Android build properties. The supported profile must be ARM64 and close to Android 15 so that catalog results do not select incompatible ABIs or an impossible runtime floor. If Aurora 4.8.4 already contains a suitable current phone or Android TV profile, the release will use that upstream profile. If no suitable profile exists, the image will include an importable profile file whose ABI and SDK values match the image; it will not patch system identity.

### Reproducible prebuilt materialization

The repository will contain a small compatibility-store integration directory under:

```text
aosptree/vendor/devices-community/gd_rpi4/compatibility-store/
```

It will contain the Soong `android_app_import` definition, package metadata, user-facing provenance notice, GPL license text, and any importable catalog profile. The APK itself will be treated as a materialized build input rather than an unreviewed floating download.

A committed lock file will contain exactly one release record with:

- upstream version `4.8.4` and version code `76`;
- an immutable official AuroraOSS artifact URL;
- the expected APK byte size and SHA-256 digest;
- the expected APK signing-certificate SHA-256 digest;
- the corresponding AuroraOSS source revision URL;
- the upstream project and GPL-3.0 license URLs.

`tools/prepare-aurora-store.sh` will read this record, download to a temporary file inside the intended compatibility-store directory, verify size and SHA-256, use the AOSP-hosted `apksigner` to verify APK signatures and the signer digest, inspect package/version/ABI metadata, and atomically rename the file only after every check succeeds. The helper will never consume a `latest` redirect. A verification failure removes the temporary file and aborts before Soong runs.

The normal source-materialization flow will invoke this helper after the AOSP checkout and host APK inspection tools exist, but before the Android image build. Unit tests will exercise the helper against local fixtures so repository validation does not depend on Aurora or Google network availability.

### Product integration

The imported module will be a presigned, product-specific application. `aosptree/vendor/devices-community/gd_rpi4/device.mk` will add only that module to `PRODUCT_PACKAGES`; it will not add certification properties, privileged permissions, or global identity overrides.

The application will be installed as part of the read-only product image. Its mutable account, session, profile-selection, download, and update state will remain under normal `/data` application storage. An OTA image therefore replaces the system APK only when the pinned build version changes while retaining user state under Android's usual same-signature package upgrade rules.

### Build cache and release behavior

Adding a product APK changes the image contents and must increment the Android build baseline from `android-platform-15.0.0_r3-tesla-2026.22.1-runtime-v3` to a new `runtime-v4` identifier. The validator and build-cache tests must require the same new value.

Documentation and test commits will not carry `[build-image]`. The implementation commit will request an image only after repository validation, local fixture verification, and patch-application validation pass. The image may include the catalog work even if the separate Apple TV experiment concludes that protected playback cannot be repaired on this hardware.

## Error handling and safety

- An unavailable upstream artifact, digest mismatch, signer mismatch, wrong package, wrong version, or incompatible ABI fails the build; the previous verified APK is not silently reused under a new lock record.
- A partially downloaded file never becomes the Soong input.
- If Aurora anonymous login is temporarily rate-limited or its reverse-engineered protocol breaks, Google Play and already installed applications remain unaffected.
- If a selected spoof profile produces an incompatible package, Android's package installer rejects it normally; the image does not bypass SDK, ABI, or signature checks.
- The compatibility-store UI and documentation must state that catalog visibility and installation do not imply Play certification or protected-video compatibility.
- The implementation must not introduce a service that stores Google credentials outside Aurora's standard application sandbox.

## Verification

Repository tests will verify:

1. The lock contains version `4.8.4`, version code `76`, a non-floating HTTPS AuroraOSS URL, one 64-hex-character APK SHA-256 value, and one 64-hex-character signer SHA-256 value.
2. The materializer rejects wrong size, content digest, signer digest, package name, version, and ABI, and accepts a matching local fixture.
3. The Soong module is `presigned`, product-specific, and included exactly once by the Raspberry Pi 4 product.
4. No global Pixel/Shield identity, certification property, `ih8sn` execution, platform certificate, privileged permission allowlist, or Aurora Services package is introduced.
5. The source-materialization/build workflow verifies the APK before `build_rpi4.sh`.
6. License and provenance files identify the exact upstream source revision that corresponds to the distributed binary.
7. The build workflow and cache tests agree on the `runtime-v4` baseline.

Hardware validation on the first image will verify:

1. Google Play still launches and the existing Netflix installation still plays protected video.
2. Compatibility Store launches, completes its upstream first-run flow, and can use an ARM64 Android-compatible spoof profile without changing `ro.product.*`, `ro.build.fingerprint`, verified-boot state, or Play Protect status.
3. Disney+ and Hulu appear in Compatibility Store when available for the account region/profile and their split packages install successfully.
4. Installed applications survive reboot and the store does not run a privileged background installer.
5. A failed or unavailable Aurora session has no effect on Google Play, TeslaAndroid streaming, or virtual touchscreen input.

Successful installation is recorded separately from protected-video playback. Disney+ or Hulu playback failure caused by Play Integrity, Widevine level, or HDCP is not reported as an installation regression.

## Alternatives considered

- **Global certified-device property spoof:** may influence superficial catalog matching but cannot create Google's server-side certification or hardware-backed integrity. It also repeats the class of Android 15 property mutation that previously crashed system services.
- **Replace Google Play with Aurora:** reduces clarity, removes a working supported path for Netflix and other applications, and makes Aurora protocol outages device-wide. Keeping both stores isolates the risk.
- **Manual APK downloads:** avoids image work but leaves split-package selection, provenance, updates, and device-profile compatibility to every user.
- **Build and modify Aurora from source:** could automate more of first launch, but creates a permanently divergent store client and signing identity. The first version will preserve the official signed binary and its update path.
