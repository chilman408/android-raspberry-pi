# Android 15 Disney+ Resolution-Change Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve Raspberry Pi NV12 CAPTURE format across V4L2 dynamic resolution changes so Disney+ playback does not abort.

**Architecture:** Add one overlay patch to the exact pinned AOSP v4l2_codec2 revision and a repository regression gate.  The patch skips only the redundant S_FMT call when G_FMT already reports a supported format; every queue-lifecycle step and fallback remains intact.

**Tech Stack:** C++17, Linux V4L2 stateful decoder API, Python unittest, Bash repository gates.

**Spec:** `docs/superpowers/specs/2026-09-24-android15-disney-plus-resolution-change-design.md`

## Global Constraints

- Preserve hardware AVC decoding and the existing NV12-only Raspberry Pi policy.
- Do not alter Widevine, HDCP, Play Protect, decoder rank, or software fallback.
- The patch must apply after `0012-Explicitly-specify-base.patch`.
- Do not build until focused tests and the full Linux patch gate pass.

## Review Focus

- Initial decoder setup must still call the existing format-selection path.
- A supported driver-selected NV12 format must skip S_FMT only during source change.
- An unsupported driver-selected format must retain the current fallback and error path.
- CAPTURE streamoff/deallocation/reallocation/streamon ordering must not change.
- The new patch must apply to manifest revision `6a3ebb9202e41f53c2826fb757b869ade5b1e77f`.

---

### Task 1: V4L2 dynamic-resolution regression

**Files:**
- Create: `tools/tests/test_android15_disney_plus_decoder.py`
- Create: `patches-aosp/external/v4l2_codec2/0013-RPI4-v4l2_codec2-decoder-Preserve-supported-capture.patch`
- Modify: `tools/check-android15-port.sh`

**Interfaces:**
- Consumes: pinned `components/V4L2Decoder.cpp` and existing `kSupportedOutputFourccs`.
- Produces: a source-change guard that returns to `setupOutputFormat(codedSize)` only for unsupported G_FMT formats.

- [ ] **Step 1: Write the failing repository test**

```python
def test_resolution_change_preserves_supported_driver_format(self):
    patch = DECODER_PATCH.read_text(encoding="utf-8")
    self.assertIn("format->fmt.pix_mp.pixelformat", patch)
    self.assertIn("kSupportedOutputFourccs", patch)
    self.assertIn("setupOutputFormat(codedSize)", patch)
```

Also assert the manifest revision, patch-series gate entry, unsupported-format
fallback, and unchanged queue teardown sequence.

- [ ] **Step 2: Run the focused test and observe RED**

Run: `python -m unittest tools.tests.test_android15_disney_plus_decoder -v`

Expected: FAIL because patch 0013 and its port-gate requirement do not exist.

- [ ] **Step 3: Implement the minimal decoder patch**

```cpp
const uint32_t pixfmt = format->fmt.pix_mp.pixelformat;
const bool outputFormatSupported =
        std::find(kSupportedOutputFourccs.begin(), kSupportedOutputFourccs.end(), pixfmt) !=
        kSupportedOutputFourccs.end();
if (!outputFormatSupported && !setupOutputFormat(codedSize)) {
    return false;
}
```

Generate the patch from the exact pinned upstream source and add its file and
behavioral markers to `check-android15-port.sh`.

- [ ] **Step 4: Run focused and adjacent tests**

Run: `python -m unittest tools.tests.test_android15_disney_plus_decoder tools.tests.test_android15_runtime_performance -v`

Expected: all tests PASS.

- [ ] **Step 5: Validate patch application**

Run: `bash tools/check-patches.sh`

Expected: the complete patch series applies without rejects on Linux CI.

- [ ] **Step 6: Commit**

```bash
git add tools/tests/test_android15_disney_plus_decoder.py tools/check-android15-port.sh patches-aosp/external/v4l2_codec2/0013-RPI4-v4l2_codec2-decoder-Preserve-supported-capture.patch
git commit -m "fix: preserve V4L2 format on resolution change"
```
