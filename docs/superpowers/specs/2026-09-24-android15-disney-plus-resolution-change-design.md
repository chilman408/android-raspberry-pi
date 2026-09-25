# Android 15 Disney+ Resolution-Change Design

## Goal

Keep Disney+ playback running when an encrypted AVC stream adapts from its
initial 640x360 representation to 1280x720 on Raspberry Pi 4.

## Evidence

Live ADB evidence from Disney+ 26.16.0 shows a valid Widevine L3 session and
successful playback before adaptation.  At the source-change event,
`c2.v4l2.avc.decoder` reads the driver's new NV12 CAPTURE format and then
issues a redundant `VIDIOC_S_FMT` while the CAPTURE queue is still allocated
and streaming.  The Pi driver rejects the operation, Codec2 reports error 14,
and ExoPlayer reports `MediaCodecVideoRenderer` failure.

## Design

Add one Raspberry Pi-specific patch after the existing v4l2_codec2 patch
series.  In `V4L2Decoder::changeResolution()`, inspect the format returned by
`VIDIOC_G_FMT`.  When its pixel format is already one of
`kSupportedOutputFourccs` (NV12 on this product), preserve it and skip the
redundant `setupOutputFormat()` / `VIDIOC_S_FMT` call.  If the driver reports
an unsupported format, retain the existing format-selection path.

The existing adjusted-format query and CAPTURE
streamoff/deallocate/reallocate/streamon sequence remain unchanged.  This
matches the Linux stateful decoder contract, under which setting CAPTURE
format after a source-change event is optional when the format returned by the
driver is acceptable.

## Boundaries

- Do not force software decoding.
- Do not change decoder rank, Widevine, HDCP, Play Protect, or device identity.
- Do not change OUTPUT queue streaming.
- Preserve the initial format-selection path and unsupported-format fallback.
- Keep Netflix, YouTube, browser display, audio, and virtual-touch behavior
  unchanged.

## Verification

A focused repository test requires the new patch, its supported-format guard,
and the unchanged fallback/error behavior.  The complete AOSP patch series
must apply on Linux before an image is built.

Hardware acceptance requires Disney+ to cross the 640x360 to 1280x720
adaptation and play for at least ten minutes without a V4L2 S_FMT,
`MediaCodecVideoRenderer`, or decoder-restart failure.  Netflix and YouTube
playback plus TeslaAndroid display/touch responsiveness are regression checks.

