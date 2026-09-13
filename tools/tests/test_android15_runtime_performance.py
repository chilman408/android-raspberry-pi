#!/usr/bin/env python3

import math
import pathlib
import re
import unittest
import xml.etree.ElementTree as ET


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TESLA_ANDROID_2026_22_1_VENDOR = "6e139bf41585188308e053dcbb34855f644aedba"
RUNTIME_BASELINE = "android-platform-15.0.0_r3-tesla-2026.22.1-runtime-v3"
USERDATA_PATCH = (
    REPO_ROOT
    / "patches-aosp"
    / "glodroid"
    / "configuration"
    / "0027-userdata-Avoid-background-inode-table-starvation.patch"
)
AUDIO_PATCH = (
    REPO_ROOT
    / "patches-aosp"
    / "frameworks"
    / "av"
    / "0001-Audioflinger-add-audio-capture-via-tesla-android-aud.patch"
)
FFMPEG_CODEC_PATCH = (
    REPO_ROOT
    / "patches-aosp"
    / "glodroid"
    / "vendor"
    / "ffmpeg_codec2"
    / "0004-RPI4-disable-unusable-FFmpeg-AV1-decoder.patch"
)
RPI4_DEVICE_MK = (
    REPO_ROOT
    / "aosptree"
    / "vendor"
    / "devices-community"
    / "gd_rpi4"
    / "device.mk"
)


class Android15RuntimePerformanceTest(unittest.TestCase):
    def test_manifest_pins_official_2026_22_1_vendor_payload(self):
        manifest = ET.parse(REPO_ROOT / "manifests" / "tesla-android.xml").getroot()
        projects = {
            project.get("path"): project.get("revision")
            for project in manifest.findall("project")
        }
        self.assertEqual(
            projects.get("vendor/tesla-android"),
            TESLA_ANDROID_2026_22_1_VENDOR,
            "the image must ship the official TeslaAndroid 2026.22.1 web/runtime payload",
        )

    def test_userdata_resize_cannot_create_multi_gibibyte_inode_tables(self):
        self.assertTrue(USERDATA_PATCH.is_file(), f"missing userdata fix: {USERDATA_PATCH}")
        patch = USERDATA_PATCH.read_text(encoding="utf-8")

        match = re.search(
            r"(?m)^\+BOARD_USERDATAIMAGE_EXTFS_INODE_COUNT\s*:=\s*(\d+)\s*$",
            patch,
        )
        self.assertIsNotNone(match, "userdata image must define an explicit inode count")
        inodes_per_group = int(match.group(1))

        # Geometry captured from the expanded 128 GB test card.  resize2fs keeps
        # the image's inodes-per-group ratio while adding block groups.
        final_blocks = 29_020_027
        blocks_per_group = 32_768
        inode_size = 512
        block_groups = math.ceil(final_blocks / blocks_per_group)
        final_inodes = block_groups * inodes_per_group
        inode_table_bytes = final_inodes * inode_size

        self.assertGreaterEqual(final_inodes, 400_000)
        self.assertLessEqual(
            inode_table_bytes,
            256 * 1024 * 1024,
            "expanded userdata inode tables must stay below 256 MiB",
        )
        self.assertIn(
            "+/dev/block/by-name/userdata         /data           ext4    noatime,nosuid,nodev,barrier=1,noinit_itable",
            patch,
            "Android must not run ext4 inode-table initialization in the background",
        )

    def test_audio_relay_cannot_block_audioflinger(self):
        patch = AUDIO_PATCH.read_text(encoding="utf-8")
        for fragment in (
            "O_NONBLOCK",
            "MSG_NOSIGNAL | MSG_DONTWAIT",
            'property_get_bool("persist.tesla-android.browser_audio.is_enabled"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, patch)

        self.assertNotIn(
            "TEMP_FAILURE_RETRY(write(fd, data, remaining))",
            patch,
            "the real-time AudioFlinger thread must never use blocking relay writes",
        )

    def test_rpi4_does_not_offer_netflix_the_unusable_ffmpeg_av1_decoder(self):
        self.assertTrue(
            FFMPEG_CODEC_PATCH.is_file(),
            f"missing Netflix AV1 codec policy patch: {FFMPEG_CODEC_PATCH}",
        )
        patch = FFMPEG_CODEC_PATCH.read_text(encoding="utf-8")
        device = RPI4_DEVICE_MK.read_text(encoding="utf-8")

        self.assertIn(
            "ro.vendor.ffmpeg_codec2.rank.video.av1=4294967295",
            device,
            "RPi4 must disable only the unusable FFmpeg AV1 component",
        )
        self.assertIn(
            'GetUintProperty("ro.vendor.ffmpeg_codec2.rank.video.av1", defaultRankVideo)',
            patch,
            "the Codec2 store must support a device-specific AV1 rank override",
        )
        self.assertIn(
            "if (info.codecID == AV_CODEC_ID_AV1 && av1Rank == RANK_DISABLED)",
            patch,
            "the disabled AV1 component must be omitted from the published codec list",
        )
        self.assertIn(
            "traits->rank = (info.codecID == AV_CODEC_ID_AV1) ? av1Rank : defaultRankVideo;",
            patch,
            "non-AV1 FFmpeg video codec ranks must remain unchanged",
        )

    def test_rpi4_touchscreen_also_advertises_basic_touch_compatibility(self):
        device = RPI4_DEVICE_MK.read_text(encoding="utf-8")
        self.assertIn(
            "frameworks/native/data/etc/android.hardware.faketouch.xml:"
            "$(TARGET_COPY_OUT_VENDOR)/etc/permissions/android.hardware.faketouch.xml",
            device,
            "touchscreen devices must also report android.hardware.faketouch",
        )

    def test_release_runtime_change_invalidates_cached_build_outputs(self):
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "build-android15-rpi4.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(f"ANDROID_BUILD_BASELINE: {RUNTIME_BASELINE}", workflow)

    def test_2026_boot_updater_receives_the_rpi_config_asset(self):
        product = (
            REPO_ROOT
            / "aosptree"
            / "vendor"
            / "devices-community"
            / "gd_rpi4"
            / "gd_rpi4.mk"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "$(LOCAL_PATH)/boot/config.txt:$(TARGET_COPY_OUT_VENDOR_DLKM)/boot/config.txt",
            product,
        )


if __name__ == "__main__":
    unittest.main()
