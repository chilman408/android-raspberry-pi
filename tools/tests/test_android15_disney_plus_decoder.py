#!/usr/bin/env python3

import pathlib
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DECODER_PATCH = (
    REPO_ROOT
    / "patches-aosp"
    / "external"
    / "v4l2_codec2"
    / "0013-RPI4-v4l2_codec2-decoder-Preserve-supported-capture.patch"
)
PORT_GATE = REPO_ROOT / "tools" / "check-android15-port.sh"
MANIFEST = REPO_ROOT / "manifests" / "glodroid.xml"
PINNED_V4L2_CODEC2_REVISION = "6a3ebb9202e41f53c2826fb757b869ade5b1e77f"


UPSTREAM_DECODER = """bool V4L2Decoder::changeResolution() {
    ALOGV("%s()", __func__);
    ALOG_ASSERT(mTaskRunner->RunsTasksInCurrentSequence());

    const std::optional<struct v4l2_format> format = getFormatInfo();
    std::optional<size_t> numOutputBuffers = getNumOutputBuffers();
    if (!format || !numOutputBuffers) {
        return false;
    }
    *numOutputBuffers = std::max(*numOutputBuffers, mMinNumOutputBuffers);

    const ui::Size codedSize(format->fmt.pix_mp.width, format->fmt.pix_mp.height);
    if (!setupOutputFormat(codedSize)) {
        return false;
    }

    const std::optional<struct v4l2_format> adjustedFormat = getFormatInfo();
    if (!adjustedFormat) {
        return false;
    }
    mCodedSize.set(adjustedFormat->fmt.pix_mp.width, adjustedFormat->fmt.pix_mp.height);
    mVisibleRect = getVisibleRect(mCodedSize);

    ALOGI("Need %zu output buffers. coded size: %s, visible rect: %s", *numOutputBuffers,
          toString(mCodedSize).c_str(), toString(mVisibleRect).c_str());
    if (isEmpty(mCodedSize)) {
        ALOGE("Failed to get resolution from V4L2 driver.");
        return false;
    }

    mOutputQueue->streamoff();
    mOutputQueue->deallocateBuffers();
    mFrameAtDevice.clear();
    mBlockIdToV4L2Id.clear();

    const size_t adjustedNumOutputBuffers =
            mOutputQueue->allocateBuffers(*numOutputBuffers, V4L2_MEMORY_DMABUF);
    if (adjustedNumOutputBuffers == 0) {
        ALOGE("Failed to allocate output buffers.");
        return false;
    }
    ALOGV("Allocated %zu output buffers.", adjustedNumOutputBuffers);
    if (!mOutputQueue->streamon()) {
        ALOGE("Failed to streamon output queue.");
        return false;
    }

    mVideoFramePool.reset();
    mVideoFramePool =
            mGetPoolCb.Run(mCodedSize, HalPixelFormat::YCBCR_420_888, adjustedNumOutputBuffers);
    if (!mVideoFramePool) {
        ALOGE("Failed to get block pool with size: %s", toString(mCodedSize).c_str());
        return false;
    }

    tryFetchVideoFrame();
    return true;
}
"""


class Android15DisneyPlusDecoderTest(unittest.TestCase):
    def test_manifest_pins_diagnosed_decoder_revision(self):
        projects = {
            project.get("path"): project.get("revision")
            for project in ET.parse(MANIFEST).getroot().findall("project")
        }
        self.assertEqual(
            projects.get("external/v4l2_codec2"),
            PINNED_V4L2_CODEC2_REVISION,
        )

    def test_resolution_change_preserves_supported_driver_format(self):
        self.assertTrue(
            DECODER_PATCH.is_file(),
            f"missing Disney+ dynamic-resolution patch: {DECODER_PATCH}",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            checkout = pathlib.Path(temp_dir)
            decoder = checkout / "components" / "V4L2Decoder.cpp"
            decoder.parent.mkdir(parents=True)
            decoder.write_text(UPSTREAM_DECODER, encoding="utf-8")

            result = subprocess.run(
                ["git", "apply", "--whitespace=error", str(DECODER_PATCH)],
                cwd=checkout,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                result.returncode,
                0,
                "Disney+ patch does not apply to the pinned decoder fixture:"
                f"\n{result.stderr}",
            )

            source = decoder.read_text(encoding="utf-8")
            self.assertRegex(
                source,
                r"const uint32_t pixfmt = format->fmt\.pix_mp\.pixelformat;",
            )
            self.assertRegex(
                source,
                r"std::find\(kSupportedOutputFourccs\.begin\(\),"
                r" kSupportedOutputFourccs\.end\(\), pixfmt\)",
            )
            self.assertIn(
                "if (!outputFormatSupported && !setupOutputFormat(codedSize))",
                source,
                "unsupported driver formats must retain the existing selection fallback",
            )

            adjusted_format = source.index(
                "const std::optional<struct v4l2_format> adjustedFormat"
            )
            streamoff = source.index("mOutputQueue->streamoff();")
            deallocate = source.index("mOutputQueue->deallocateBuffers();")
            allocate = source.index("mOutputQueue->allocateBuffers(")
            streamon = source.index("mOutputQueue->streamon()")
            self.assertLess(adjusted_format, streamoff)
            self.assertLess(streamoff, deallocate)
            self.assertLess(deallocate, allocate)
            self.assertLess(allocate, streamon)

    def test_port_gate_requires_dynamic_resolution_patch(self):
        gate = PORT_GATE.read_text(encoding="utf-8")
        self.assertIn(DECODER_PATCH.relative_to(REPO_ROOT).as_posix(), gate)
        self.assertIn("outputFormatSupported", gate)


if __name__ == "__main__":
    unittest.main()
