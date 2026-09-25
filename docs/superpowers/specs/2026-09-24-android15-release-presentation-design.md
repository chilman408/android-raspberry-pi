# Android 15 Release Presentation Design

## Goal

Present the Android 15 fork consistently in both TeslaAndroid frontends for
the release made during ISO work week 39 of 2026.

## Release Identity

- Public version: `2026.39.a15.1`.
- Project name: `Tesla Android for Android 15`.
- Project URL: `https://github.com/chilman408/TeslaAndroid-R15-Rpi4`.
- Donation copy: `This is a self funded project, your contribution means the world to me!`
- Donation URL:
  `https://www.paypal.com/donate/?business=TFWNLUC7ZGSPS&no_recurring=0&item_name=Thank+you+for+your+support%21++I+appreciate+it.&currency_code=USD`

## Frontend Coverage

The pinned TeslaAndroid vendor payload contains a compiled Flutter frontend at
the root and a readable HTML frontend under `/beta`.  A vendor patch updates
both so users never see different versions, ownership, notes, or donation
targets depending on which frontend they open.

The public version is updated in `vendor.mk`, `version.json`, the beta
constant, and both release-note datasets.  The boot-files migration marker is
not a public version and is deliberately left at `2026.22.1`; changing it
would rewrite the boot partition even though this release does not change boot
assets.

## Release Notes

The current release contains two intentionally broad entries:

1. **Android 15 Release — Platform and app compatibility.** Android 15 platform
   migration, the verified Compatibility Store (Aurora) for Play-catalog gaps,
   and TeslaAndroid display/audio/touch reliability improvements.
2. **Streaming Compatibility — Netflix and Disney+ playback fixes.** Netflix
   video playback is restored and Disney+ adaptive-resolution playback is
   repaired.  The same entry states that Apple TV protected playback remains
   unvalidated/unsupported on this Widevine L3, unprotected-output device.

Historical notes remain unchanged.

## Donations

The compiled frontend's runtime-generated QR input changes to the exact PayPal
URL.  The beta frontend receives a freshly generated SVG QR for the same URL
and exposes the URL as a normal link/fallback.  The SVG includes metadata with
the canonical payload and is decoded during verification to prove that the
visual QR and displayed target agree.

## Build Compatibility

The Jenkins version parser accepts both the existing three-part versions and
the new `YYYY.WW.a15.N` format.  Repository tests pin all public copy,
versions, both frontends, the QR payload, and the decision not to touch the
boot-files migration marker.  Linux patch-series validation remains mandatory
before building the release image.

