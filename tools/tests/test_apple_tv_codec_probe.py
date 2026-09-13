#!/usr/bin/env python3

import unittest

from tools.apple_tv_codec_probe import (
    CodecEvent,
    PlaybackObservation,
    ProbeOutcome,
    PropertyState,
    classify_observation,
    parse_codec_events,
    parse_codec_inventory,
    parse_getprop_listing,
    parse_widevine_state,
    redact_diagnostic_text,
)


APPLE_PACKAGE = "com.apple.atve.androidtv.appletv"


class AppleTvCodecProbeTest(unittest.TestCase):
    def test_getprop_parser_distinguishes_absent_empty_and_set_values(self):
        properties = parse_getprop_listing(
            "[persist.vendor.empty]: []\n"
            "[ro.vendor.decoder]: [c2.ffmpeg.avc.decoder]\n"
            "not a getprop line\n"
        )

        self.assertEqual(
            properties["persist.vendor.empty"],
            PropertyState("persist.vendor.empty", True, ""),
        )
        self.assertEqual(
            properties["ro.vendor.decoder"],
            PropertyState("ro.vendor.decoder", True, "c2.ffmpeg.avc.decoder"),
        )
        self.assertNotIn("persist.vendor.absent", properties)

    def test_codec_inventory_finds_v4l2_and_pure_ffmpeg_avc_components(self):
        inventory = parse_codec_inventory(
            "name: c2.v4l2.avc.decoder\n"
            "name: c2.ffmpeg.avc.decoder\n"
            "name: c2.v4l2.avc.decoder\n"
        )

        self.assertEqual(
            inventory,
            ["c2.v4l2.avc.decoder", "c2.ffmpeg.avc.decoder"],
        )

    def test_widevine_parser_reports_l3_oemcrypto_level3_and_unprotected_hdcp(self):
        state = parse_widevine_state(
            "security level: L3\n"
            "OEMCrypto: Level 3\n"
            "Current HDCP level: Unprotected\n"
            "Maximum HDCP level: Unprotected\n"
        )

        self.assertEqual(
            state,
            {
                "securityLevel": "L3",
                "OEMCrypto": "Level 3",
                "currentHdcpLevel": "Unprotected",
                "maximumHdcpLevel": "Unprotected",
            },
        )

    def test_codec_event_parser_keeps_only_apple_encrypted_avc_events(self):
        events = parse_codec_events(
            "100.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
            "component=c2.ffmpeg.avc.decoder crypto=1 session=one event=start\n"
            "101.0 package=com.apple.atve.androidtv.appletv mime=video/hevc "
            "component=c2.ffmpeg.hevc.decoder crypto=1 session=two event=start\n"
            "102.0 package=com.example.other mime=video/avc "
            "component=c2.ffmpeg.avc.decoder crypto=1 session=three event=start\n"
            "103.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
            "component=c2.ffmpeg.avc.decoder crypto=0 session=four event=start\n"
        )

        self.assertEqual(
            events,
            [
                CodecEvent(
                    100.0,
                    APPLE_PACKAGE,
                    "video/avc",
                    "c2.ffmpeg.avc.decoder",
                    True,
                    "start",
                )
            ],
        )

    def test_codec_event_parser_preserves_session_ids_for_classification(self):
        events = parse_codec_events(
            "100.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
            "component=c2.ffmpeg.avc.decoder crypto=1 session=first event=start\n"
            "101.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
            "component=c2.ffmpeg.avc.decoder crypto=1 session=second event=start\n"
        )

        self.assertEqual([event.session_id for event in events], ["first", "second"])

    def test_codec_event_parser_rejects_contradictory_clear_crypto_flags(self):
        events = parse_codec_events(
            "100.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
            "component=c2.ffmpeg.avc.decoder crypto=0 secure=true session=one event=start\n"
        )

        self.assertEqual(events, [])

    def test_classifier_reports_missing_encrypted_avc(self):
        observation = classify_observation(
            [
                CodecEvent(
                    100.0,
                    APPLE_PACKAGE,
                    "video/avc",
                    "c2.ffmpeg.avc.decoder",
                    False,
                    "start",
                )
            ]
        )

        self.assertEqual(observation.outcome, ProbeOutcome.NO_ENCRYPTED_AVC)

    def test_classifier_rejects_unrelated_package_events(self):
        observation = classify_observation(
            [
                CodecEvent(
                    100.0,
                    "com.example.other",
                    "video/avc",
                    "c2.ffmpeg.avc.decoder",
                    True,
                    "start",
                ),
                CodecEvent(
                    701.0,
                    "com.example.other",
                    "video/avc",
                    "c2.ffmpeg.avc.decoder",
                    True,
                    "sample",
                ),
            ]
        )

        self.assertEqual(observation.outcome, ProbeOutcome.NO_ENCRYPTED_AVC)

    def test_classifier_rejects_hevc_events(self):
        observation = classify_observation(
            [
                CodecEvent(
                    100.0,
                    APPLE_PACKAGE,
                    "video/hevc",
                    "c2.ffmpeg.avc.decoder",
                    True,
                    "start",
                ),
                CodecEvent(
                    701.0,
                    APPLE_PACKAGE,
                    "video/hevc",
                    "c2.ffmpeg.avc.decoder",
                    True,
                    "sample",
                ),
            ]
        )

        self.assertEqual(observation.outcome, ProbeOutcome.NO_ENCRYPTED_AVC)

    def test_classifier_rejects_short_decode_burst(self):
        observation = classify_observation(
            parse_codec_events(
                "100.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=one event=start\n"
                "121.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=one event=sample\n"
                "122.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=one event=stop\n"
            )
        )

        self.assertEqual(observation.outcome, ProbeOutcome.UNUSABLE_LOAD)
        self.assertEqual(observation.continuous_seconds, 22.0)
        self.assertEqual(observation.restart_count, 0)

    def test_classifier_rejects_long_lifetime_without_a_sample(self):
        observation = classify_observation(
            parse_codec_events(
                "100.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=one event=start\n"
                "701.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=one event=stop\n"
            )
        )

        self.assertEqual(observation.outcome, ProbeOutcome.UNUSABLE_LOAD)
        self.assertEqual(observation.continuous_seconds, 601.0)
        self.assertEqual(observation.restart_count, 0)

    def test_classifier_reports_restart_at_thirty_second_boundary(self):
        observation = classify_observation(
            parse_codec_events(
                "100.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.v4l2.avc.decoder crypto=1 session=one event=start\n"
                "131.5 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.v4l2.avc.decoder crypto=1 session=one event=stop\n"
                "133.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.v4l2.avc.decoder crypto=1 session=two event=start\n"
            )
        )

        self.assertEqual(observation.outcome, ProbeOutcome.TIMED_RESTART)
        self.assertEqual(observation.continuous_seconds, 31.5)
        self.assertEqual(observation.restart_count, 1)

    def test_classifier_requires_one_continuous_six_hundred_second_session(self):
        observation = classify_observation(
            parse_codec_events(
                "100.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=one event=start\n"
                "701.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=one event=sample\n"
            )
        )

        self.assertEqual(observation.outcome, ProbeOutcome.PLAYED_600_SECONDS)
        self.assertGreaterEqual(observation.continuous_seconds, 601.0)
        self.assertEqual(observation.restart_count, 0)

    def test_classifier_rejects_merged_duration_from_multiple_sessions(self):
        observation = classify_observation(
            parse_codec_events(
                "100.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=one event=start\n"
                "500.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=one event=stop\n"
                "501.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=two event=start\n"
                "901.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
                "component=c2.ffmpeg.avc.decoder crypto=1 session=two event=sample\n"
            )
        )

        self.assertEqual(observation.outcome, ProbeOutcome.TIMED_RESTART)
        self.assertEqual(observation.continuous_seconds, 400.0)
        self.assertEqual(observation.restart_count, 1)

    def test_redactor_removes_authorization_cookie_account_and_drm_payload_values(self):
        redacted = redact_diagnostic_text(
            "100.0 Authorization: Bearer secret-token\n"
            "Cookie: session=secret-cookie\n"
            "Set-Cookie: renewed=secret-value\n"
            "account=alice@example.com email: alice@example.com token=abc123\n"
            'licenseRequest: "request-bytes" licenseResponse="response-bytes" '
            'keySetId: "keyset-bytes" drmPayload={"kid":"secret"}\n'
            "101.0 package=com.apple.atve.androidtv.appletv mime=video/avc "
            "component=c2.ffmpeg.avc.decoder error=42 cpu=67C thermal=3\n"
        )

        for secret in (
            "secret-token",
            "secret-cookie",
            "secret-value",
            "alice@example.com",
            "abc123",
            "request-bytes",
            "response-bytes",
            "keyset-bytes",
            '"kid":"secret"',
        ):
            self.assertNotIn(secret, redacted)
        self.assertIn("Authorization: [REDACTED]", redacted)
        self.assertIn("Cookie: [REDACTED]", redacted)
        self.assertIn("Set-Cookie: [REDACTED]", redacted)
        self.assertIn("account=[REDACTED]", redacted)
        self.assertIn("licenseRequest: [REDACTED]", redacted)
        self.assertIn("drmPayload=[REDACTED]", redacted)
        self.assertIn("100.0", redacted)
        self.assertIn("package=com.apple.atve.androidtv.appletv", redacted)
        self.assertIn("mime=video/avc", redacted)
        self.assertIn("component=c2.ffmpeg.avc.decoder", redacted)
        self.assertIn("error=42", redacted)
        self.assertIn("cpu=67C", redacted)
        self.assertIn("thermal=3", redacted)

    def test_redactor_masks_nested_json_and_malformed_sensitive_values(self):
        redacted = redact_diagnostic_text(
            '{"account":"quoted-account","drmPayload":{"kid":"nested-kid"'
            ',"opaque":"leak-after-brace"}} error=42 cpu=67C thermal=3\n'
            'licenseResponse={"blob":"unterminated secret-value cpu=68C thermal=4\n'
        )

        for secret in (
            "quoted-account",
            "nested-kid",
            "leak-after-brace",
            "secret-value",
        ):
            self.assertNotIn(secret, redacted)
        self.assertIn('"account":[REDACTED]', redacted)
        self.assertIn('"drmPayload":[REDACTED]', redacted)
        self.assertIn("error=42", redacted)
        self.assertIn("cpu=67C", redacted)
        self.assertIn("thermal=3", redacted)
        self.assertIn("cpu=68C", redacted)
        self.assertIn("thermal=4", redacted)

    def test_redactor_preserves_diagnostics_after_authorization_value(self):
        redacted = redact_diagnostic_text(
            "100.0 Authorization: Bearer auth-secret cpu=67C thermal=3 error=42\n"
        )

        self.assertNotIn("auth-secret", redacted)
        self.assertIn("Authorization: [REDACTED]", redacted)
        self.assertIn("100.0", redacted)
        self.assertIn("cpu=67C", redacted)
        self.assertIn("thermal=3", redacted)
        self.assertIn("error=42", redacted)

    def test_redactor_masks_unquoted_sensitive_tail_before_diagnostics(self):
        redacted = redact_diagnostic_text(
            "drmPayload=opaque secret-tail cpu=67C thermal=3 error=42\n"
        )

        self.assertNotIn("opaque", redacted)
        self.assertNotIn("secret-tail", redacted)
        self.assertIn("drmPayload=[REDACTED]", redacted)
        self.assertIn("cpu=67C", redacted)
        self.assertIn("thermal=3", redacted)
        self.assertIn("error=42", redacted)


if __name__ == "__main__":
    unittest.main()
