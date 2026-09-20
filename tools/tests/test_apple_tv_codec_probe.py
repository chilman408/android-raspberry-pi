#!/usr/bin/env python3

import unittest
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import io
import os
import re
from contextlib import redirect_stderr
from unittest.mock import patch

from tools import apple_tv_codec_probe as probe

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
                "".join(f"{100 + seconds} package={APPLE_PACKAGE} mime=video/avc "
                        f"component=c2.ffmpeg.avc.decoder crypto=1 session=one "
                        f"event={'start' if seconds == 0 else 'sample'} position_seconds={seconds}\n"
                        for seconds in range(0, 611, 10))
            )
        )

        self.assertEqual(observation.outcome, ProbeOutcome.PLAYED_600_SECONDS)
        self.assertGreaterEqual(observation.continuous_seconds, 600.0)
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

    def test_codec_parser_preserves_only_finite_nonnegative_explicit_media_positions(self):
        for value, expected in (("12.5", 12.5), ("0", 0.0), ("nan", None), ("inf", None), ("-1", None), ("unknown", None)):
            with self.subTest(value=value):
                events = parse_codec_events(f"100 package={APPLE_PACKAGE} mime=video/avc component=c2.ffmpeg.avc.decoder crypto=1 session=one event=sample position_seconds={value}")
                self.assertEqual(getattr(events[0], "position_seconds", "missing field"), expected)

    def test_classifier_rejects_sparse_samples_and_missing_or_stagnant_media_counters(self):
        for samples in (
            [(0, None), (601, None)],
            [(0, 0), (601, 0.033)],
            [(0, 0), (601, 601)],
            [(seconds, 0) for seconds in range(0, 611, 10)],
            [(seconds, None) for seconds in range(0, 611, 10)],
        ):
            with self.subTest(samples=samples[:2]):
                text = "".join(f"{100 + stamp} package={APPLE_PACKAGE} mime=video/avc component=c2.ffmpeg.avc.decoder crypto=1 session=one event={'start' if stamp == 0 else 'sample'}" + (f" position_seconds={position}" if position is not None else "") + "\n" for stamp, position in samples)
                self.assertEqual(classify_observation(parse_codec_events(text)).outcome, ProbeOutcome.UNUSABLE_LOAD)

    def test_classifier_cannot_borrow_media_progress_from_another_session_or_codec(self):
        for other_session, other_codec in (("other", "c2.ffmpeg.avc.decoder"), ("one", "c2.v4l2.avc.decoder")):
            with self.subTest(session=other_session, codec=other_codec):
                text = f"100 package={APPLE_PACKAGE} mime=video/avc component=c2.ffmpeg.avc.decoder crypto=1 session=one event=start position_seconds=0\n"
                for elapsed in range(10, 611, 10):
                    text += f"{100 + elapsed} package={APPLE_PACKAGE} mime=video/avc component=c2.ffmpeg.avc.decoder crypto=1 session=one event=sample position_seconds=0\n"
                    text += f"{100 + elapsed} package={APPLE_PACKAGE} mime=video/avc component={other_codec} crypto=1 session={other_session} event=sample position_seconds={elapsed}\n"
                self.assertEqual(classify_observation(parse_codec_events(text)).outcome, ProbeOutcome.UNUSABLE_LOAD)

    def test_classifier_requires_near_real_time_media_progress_without_counter_resets(self):
        for mode in ("slow", "jump", "reset", "no_session"):
            with self.subTest(mode=mode):
                text = ""
                for elapsed in range(0, 611, 10):
                    position = elapsed / 2 if mode == "slow" else elapsed * 2 if mode == "jump" else elapsed % 310 if mode == "reset" else elapsed
                    session = "" if mode == "no_session" else "one"
                    text += f"{100 + elapsed} package={APPLE_PACKAGE} mime=video/avc component=c2.ffmpeg.avc.decoder crypto=1 session={session} event={'start' if elapsed == 0 else 'sample'} position_seconds={position}\n"
                self.assertEqual(classify_observation(parse_codec_events(text)).outcome, ProbeOutcome.UNUSABLE_LOAD)

    def test_classifier_reports_explicit_six_hundred_second_media_progress(self):
        text = "".join(f"{100 + seconds} package={APPLE_PACKAGE} mime=video/avc component=c2.ffmpeg.avc.decoder crypto=1 session=one event={'start' if seconds == 0 else 'sample'} position_seconds={20 + seconds}\n" for seconds in range(0, 601, 10))
        observation = classify_observation(parse_codec_events(text))
        self.assertEqual(observation.outcome, ProbeOutcome.PLAYED_600_SECONDS)
        self.assertEqual(getattr(observation, "media_progress_seconds", None), 600)


class RepositoryIntegrationTests(unittest.TestCase):
    """Repository contracts that keep the Apple diagnostics fixture-only."""

    repo_root = Path(__file__).resolve().parents[2]
    harness_command = [
        "python3",
        "-m",
        "unittest",
        "-v",
        "tools/tests/test_apple_tv_codec_probe.py",
    ]

    @staticmethod
    def _workflow_commands(path):
        lines = path.read_text(encoding="utf-8").splitlines()
        commands = []
        index = 0
        while index < len(lines):
            line = lines[index]
            indentation = len(line) - len(line.lstrip())
            stripped = line.strip()
            if not stripped.startswith("run:"):
                index += 1
                continue
            value = stripped.removeprefix("run:").strip()
            if value in ("|", ">"):
                block = []
                index += 1
                while index < len(lines):
                    nested = lines[index]
                    if nested.strip() and len(nested) - len(nested.lstrip()) <= indentation:
                        break
                    block.append(nested.strip())
                    index += 1
                commands.extend(command for command in block if command)
                continue
            if value:
                commands.append(value)
            index += 1
        return [shlex.split(command) for command in commands]

    @staticmethod
    def _make_recipe_commands(path):
        commands = []
        pending = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            pending += line.rstrip()
            if pending.endswith("\\"):
                pending = pending[:-1] + " "
                continue
            if pending.startswith("\t"):
                commands.append(shlex.split(pending.strip()))
            pending = ""
        return commands

    @staticmethod
    def _vendor_properties(path):
        properties = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            for word in shlex.split(line.rstrip("\\").strip()):
                if "=" in word and not word.startswith("$("):
                    name, value = word.split("=", 1)
                    properties[name] = value
        return properties

    @staticmethod
    def _guarded_unittest_commands(path):
        lines = path.read_text(encoding="utf-8").splitlines()
        guarded_commands = []
        index = 0
        while index < len(lines):
            condition = lines[index].strip()
            if not condition.startswith("if ! ") or not condition.endswith("; then"):
                index += 1
                continue
            command = shlex.split(condition[5:-6].strip())
            body = []
            index += 1
            while index < len(lines) and lines[index].strip() != "fi":
                body.append(lines[index])
                index += 1
            guarded_commands.append((command, "\n".join(body)))
            index += 1
        return guarded_commands

    def test_port_validator_guards_harness_fixture_with_failure_counter(self):
        """Catches removal of the fixture module or its failure-counter handling."""
        guarded_commands = self._guarded_unittest_commands(
            self.repo_root / "tools/check-android15-port.sh"
        )
        matching_bodies = [
            body for command, body in guarded_commands if command == self.harness_command
        ]

        self.assertEqual(len(matching_bodies), 1)
        self.assertTrue(
            any(
                re.search(
                    r"failures\s*=\s*\$\(\(\s*failures\s*\+\s*1\s*\)\)",
                    body,
                )
                for body in matching_bodies
            )
        )

    def test_workflow_schedules_the_harness_fixture_module(self):
        """Catches CI dropping the fixture module while the local validator still runs it."""
        commands = self._workflow_commands(
            self.repo_root / ".github/workflows/android15-port-validation.yml"
        )

        self.assertIn(self.harness_command, commands)

    def test_repository_validation_never_schedules_a_live_probe(self):
        """Catches a workflow or product recipe invoking the live probe action."""
        paths_and_commands = [
            (
                ".github/workflows/android15-port-validation.yml",
                self._workflow_commands(
                    self.repo_root / ".github/workflows/android15-port-validation.yml"
                ),
            )
        ]
        product_root = self.repo_root / "aosptree/vendor/devices-community/gd_rpi4"
        paths_and_commands.extend(
            (
                str(path.relative_to(self.repo_root)),
                self._make_recipe_commands(path),
            )
            for path in product_root.rglob("*.mk")
        )

        for path, commands in paths_and_commands:
            for command in commands:
                with self.subTest(path=path, command=command):
                    probe_index = next(
                        (
                            index
                            for index, argument in enumerate(command)
                            if argument.endswith("tools/apple_tv_codec_probe.py")
                        ),
                        None,
                    )
                    if probe_index is not None:
                        self.assertNotIn("probe", command[probe_index + 1 :])

    def test_product_codec_contract_remains_hardware_scoped_without_framework_hook(self):
        """Catches a global software AVC override or Apple-specific framework behavior."""
        device_mk = self.repo_root / "aosptree/vendor/devices-community/gd_rpi4/device.mk"
        properties = self._vendor_properties(device_mk)

        self.assertEqual(properties.get("persist.ffmpeg_codec2.v4l2.h264"), "true")
        self.assertEqual(properties.get("persist.ffmpeg_codec2.rank.video"), "128")
        self.assertNotIn("c2.ffmpeg.avc.decoder", device_mk.read_text(encoding="utf-8"))

        framework_patches = self.repo_root / "patches-aosp/frameworks"
        for patch in framework_patches.rglob("*.patch"):
            with self.subTest(patch=patch):
                self.assertNotIn(APPLE_PACKAGE, patch.read_text(encoding="utf-8"))


H264 = "persist.ffmpeg_codec2.v4l2.h264"
RANK = "persist.ffmpeg_codec2.rank.video"


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class RecordingRunner:
    """An external Android boundary, with explicit commands and queued failures."""
    def __init__(self, clock):
        self.clock = clock
        self.commands = []
        self.queued = {}
        self.serial = "10000000f93771d0"
        self.devices = [(self.serial, "device")]
        self.props = {
            H264: "true", RANK: "128", "persist.ffmpeg_codec2.empty": "",
            "ro.product.name": "tesla_android_rpi4", "ro.product.device": "gd_rpi4",
            "ro.product.model": "Pi 4", "ro.build.fingerprint": "test/fingerprint",
            "ro.build.type": "userdebug", "ro.boot.verifiedbootstate": "orange",
            "ro.vendor.v4l2_codec2.enabled": "true", "unrelated.secret": "private-value",
            "sys.boot_completed": "1",
        }
        self.boot = 1
        self.software = True
        self.hardware = True
        self.started = False
        self.started_at = 0
        self.mode = "success"
        self.ui = '<hierarchy><node package="com.apple.atve.androidtv.appletv" text="Play" focused="true" enabled="true" bounds="[10,20][110,80]" /></hierarchy>'
        self.thermal = "Thermal Status: 0\nTemperature{mValue=49.0, mType=0, mName=cpu, mStatus=0}"
        self.ignore_restore = False
        self.command_seconds = 0

    def queue(self, command, *responses):
        self.queued.setdefault(tuple(command), []).extend(responses)

    def __call__(self, args, **kwargs):
        assert isinstance(args, list), args
        assert kwargs == dict(text=True, capture_output=True, timeout=kwargs["timeout"], check=False)
        self.commands.append(args)
        self.clock.now += self.command_seconds
        if args == ["adb", "devices", "-l"]:
            return subprocess.CompletedProcess(args, 0, "List of devices attached\n" + "\n".join(f"{s}\t{state} product:tesla_android_rpi4" for s, state in self.devices), "")
        assert args[:3] == ["adb", "-s", self.serial], args
        cmd = tuple(shlex.split(" ".join(args[4:]))) if args[3] == "shell" else tuple(args[3:])
        if self.queued.get(cmd):
            response = self.queued[cmd].pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        output = ""
        if cmd == ("root",):
            output = "restarting adbd as root"
        elif cmd == ("wait-for-device",):
            pass
        elif cmd == ("reboot",):
            self.boot += 1
            self.started = False
        elif cmd == ("id", "-u"):
            output = "0"
        elif cmd == ("getprop",):
            output = "\n".join(f"[{k}]: [{v}]" for k, v in self.props.items())
        elif cmd[0] == "getprop":
            output = self.props.get(cmd[1], "")
        elif cmd[0] == "setprop":
            if not self.ignore_restore or cmd[2] in ("false", "16"):
                self.props[cmd[1]] = cmd[2]
        elif cmd == ("cat", "/proc/sys/kernel/random/boot_id"):
            output = f"boot-{self.boot}"
        elif cmd == ("cmd", "package", "list", "packages", APPLE_PACKAGE):
            output = f"package:{APPLE_PACKAGE}"
        elif cmd == ("dumpsys", "media.player"):
            output = ("name: c2.ffmpeg.avc.decoder rank: 16\n" if self.software else "") + ("name: c2.v4l2.avc.decoder rank: 128\n" if self.hardware else "")
        elif cmd == ("dumpsys", "media.drm"):
            output = 'security level: L3\nOEMCrypto: Level 3\nCurrent HDCP level: Unprotected\nlicenseResponse={"secret":"never-persist"}'
        elif cmd[:2] == ("dumpsys", "package"):
            output = "versionCode=101\nversionName=1.2.3\naccount=private-account"
        elif cmd == ("dumpsys", "media.metrics"):
            output = self.events()
        elif cmd[:2] == ("logcat", "-d"):
            output = self.events()
        elif cmd == ("logcat", "-c"):
            pass
        elif cmd[:2] == ("am", "start"):
            self.started = True
            self.started_at = self.clock.now
            output = "Starting: Intent"
        elif cmd == ("uiautomator", "dump", "/dev/tty"):
            output = self.ui
        elif cmd == ("input", "tap", "60", "50"):
            pass
        elif cmd == ("pidof", APPLE_PACKAGE):
            output = "123" if self.mode != "dead" else ""
        elif cmd == ("dumpsys", "activity", "processes"):
            output = f"ProcessRecord 123:{APPLE_PACKAGE}"
        elif cmd == ("top", "-b", "-n", "1"):
            output = f"123 10 20 180% S {APPLE_PACKAGE}\naccount=cpu-private"
        elif cmd == ("dumpsys", "thermalservice"):
            output = self.thermal
        else:
            raise AssertionError(f"Unexpected ADB command: {cmd}")
        return subprocess.CompletedProcess(args, 0, output, "")

    def events(self):
        if not self.started or self.mode in ("none", "needs_tap"):
            return ""
        elapsed = self.clock.now - self.started_at
        codec = "c2.v4l2.avc.decoder" if self.mode == "restart" else "c2.ffmpeg.avc.decoder"
        def line(stamp, session, event):
            position = max(0, stamp - (140 if session == "two" else 100))
            if self.mode == "stagnant_progress":
                position = 0
            elif self.mode == "sparse_frames":
                position /= 1000
            if self.mode == "wrong_session_progress" and event == "sample":
                session = "other"
            sample_codec = "c2.v4l2.avc.decoder" if self.mode == "wrong_codec_progress" and event == "sample" else codec
            progress = "" if self.mode == "missing_progress" else f" position_seconds={position}"
            return f"{stamp} package={APPLE_PACKAGE} mime=video/avc component={sample_codec} crypto=1 session={session} event={event}{progress}\n"
        output = line(100, "one", "start")
        if self.mode == "restart" and elapsed >= 40:
            return output + line(130, "one", "stop") + line(140, "two", "start") + line(100 + elapsed, "two", "sample")
        if elapsed > 0 and self.mode != "stalled":
            output += line(100 + elapsed, "one", "sample")
        return output


class AdbOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name) / "evidence"
        self.clock = Clock()
        self.runner = RecordingRunner(self.clock)
        self.addCleanup(patch.stopall)
        patch("time.monotonic", self.clock.monotonic).start()
        patch("time.sleep", self.clock.sleep).start()

    def cli(self, action="probe", duration=20):
        # Missing CLI orchestration is an assertion failure, not an import error.
        self.assertTrue(hasattr(probe, "AdbClient"), "safe ADB orchestration is missing")
        with patch.object(probe.subprocess, "run", self.runner), redirect_stderr(io.StringIO()):
            return probe.main([action, "--serial", self.runner.serial, "--output", str(self.output), "--duration-seconds", str(duration)] if action != "baseline" else [action, "--serial", self.runner.serial, "--output", str(self.output)])

    def writes(self):
        return [shlex.split(" ".join(c[4:])) for c in self.runner.commands if len(c) > 4 and c[4] == "setprop"]

    def assert_no_mutation(self):
        self.assertEqual(self.writes(), [])
        self.assertFalse(any(c[3] in ("root", "reboot") for c in self.runner.commands if len(c) > 3))

    # Each test catches removal of the named safety branch or observable effect.
    def test_aborts_before_root_or_setprop_when_multiple_devices_are_connected(self):
        self.runner.devices.append(("other", "device"))
        self.assertNotEqual(self.cli(), 0)
        self.assert_no_mutation()

    def test_aborts_before_mutation_for_wrong_product_or_device(self):
        for key in ("ro.product.name", "ro.product.device"):
            with self.subTest(key=key):
                original = self.runner.props[key]
                self.runner.props[key] = "wrong"
                self.assertNotEqual(self.cli(), 0)
                self.assert_no_mutation()
                self.runner.props[key] = original

    def test_aborts_before_mutation_when_target_property_is_absent(self):
        del self.runner.props[H264]
        self.assertNotEqual(self.cli(), 0)
        self.assert_no_mutation()
        state = json.loads((self.output / "state.json").read_text())
        self.assertEqual(state["properties"][H264], {"name": H264, "present": False, "value": ""})

    def test_capture_records_exact_property_presence_values_serial_and_boot_id(self):
        self.assertEqual(self.cli("baseline"), 0)
        state = json.loads((self.output / "state.json").read_text())
        self.assertEqual(state["version"], 1)
        self.assertEqual(state["serial"], "10000000f93771d0")
        self.assertEqual(state["boot_id"], "boot-1")
        self.assertEqual(state["properties"][H264]["value"], "true")
        self.assertEqual(state["properties"][RANK]["value"], "128")
        self.assertEqual(state["properties"]["persist.ffmpeg_codec2.empty"]["value"], "")
        self.assertNotIn("unrelated.secret", state["properties"])
        self.assertTrue(state["captured_at"])
        self.assert_no_mutation()

    def test_probe_sets_only_h264_backend_and_video_rank_properties(self):
        self.assertEqual(self.cli(), 0)
        self.assertEqual(self.writes(), [["setprop", H264, "false"], ["setprop", RANK, "16"], ["setprop", H264, "true"], ["setprop", RANK, "128"]])

    def test_reboot_waits_for_same_serial_boot_completed_and_package_manager(self):
        self.runner.queue(("getprop", "sys.boot_completed"), subprocess.CompletedProcess([], 0, "0", ""))
        self.runner.queue(("cmd", "package", "list", "packages", APPLE_PACKAGE), subprocess.CompletedProcess([], 1, "", "not ready"))
        self.assertEqual(self.cli(), 0)
        calls = self.runner.commands
        first_reboot = calls.index(["adb", "-s", self.runner.serial, "reboot"])
        launch = next(i for i, c in enumerate(calls) if c[3:5] == ["shell", "am"])
        waiting = calls[first_reboot:launch]
        self.assertIn(["adb", "-s", self.runner.serial, "shell", "getprop", "sys.boot_completed"], waiting)
        self.assertIn(["adb", "-s", self.runner.serial, "shell", "cmd", "package", "list", "packages", APPLE_PACKAGE], waiting)
        self.assertNotIn(["adb", "devices", "-l"], waiting)

    def test_probe_confirms_software_avc_is_advertised_before_launching_apple(self):
        self.runner.software = False
        self.assertEqual(self.cli(), 0)
        self.assertFalse(any(c[3:5] == ["shell", "am"] for c in self.runner.commands))
        self.assertEqual(json.loads((self.output / "result.json").read_text())["observation"]["outcome"], "NO_SOFTWARE_DECODER")
        self.assertEqual(self.runner.props[H264], "true")

    def test_probe_restores_exact_values_after_success(self):
        for backend, rank in (("true", "128"), ("TRUE", "77"), ("", "a b'$")):
            with self.subTest(backend=backend, rank=rank):
                self.output = Path(self.tmp.name) / str(self.runner.boot)
                self.runner.props.update({H264: backend, RANK: rank})
                self.assertEqual(self.cli(), 0)
                self.assertEqual(self.runner.props[H264], backend)
                self.assertEqual(self.runner.props[RANK], rank)
                self.assertEqual(self.writes()[-2:], [["setprop", H264, backend], ["setprop", RANK, rank]])

    def test_probe_restores_and_reboots_after_timeout_exception(self):
        self.runner.queue(("am", "start", "-n", APPLE_PACKAGE + "/com.apple.android.tv.MainActivity"), subprocess.TimeoutExpired("adb", 30))
        self.assertNotEqual(self.cli(), 0)
        self.assertEqual(self.runner.props[H264], "true")
        self.assertEqual(self.runner.props[RANK], "128")
        self.assertEqual(self.runner.boot, 3)

    def test_partial_mutation_failure_also_restores(self):
        self.runner.queue(("setprop", RANK, "16"), subprocess.CompletedProcess([], 1, "", "write failed"))
        self.assertNotEqual(self.cli(), 0)
        self.assertEqual(self.runner.props[H264], "true")
        self.assertEqual(self.runner.props[RANK], "128")
        self.assertEqual(self.runner.boot, 2)

    def test_restore_verification_failure_reports_exact_residual_properties(self):
        self.runner.ignore_restore = True
        self.assertNotEqual(self.cli(), 0)
        failure = json.loads((self.output / "RESTORE_FAILED.json").read_text())
        self.assertEqual(failure["expected"], {H264: "true", RANK: "128"})
        self.assertEqual(failure["observed"], {H264: "false", RANK: "16"})

    def test_probe_never_clears_package_data_accounts_or_drm_provisioning(self):
        self.assertEqual(self.cli(), 0)
        for call in self.runner.commands:
            self.assertFalse(any(word in call for word in ("clear", "uninstall", "rm", "provision", "accounts")))
        self.assertIn(["adb", "-s", self.runner.serial, "shell", "logcat", "-c"], self.runner.commands)

    def test_evidence_writer_redacts_before_persisting_text(self):
        self.runner.queue(("dumpsys", "media.player"), subprocess.CompletedProcess([], 0, "name: c2.ffmpeg.avc.decoder account=secret-person cpu=67C", ""))
        self.assertEqual(self.cli("baseline"), 0)
        artifacts = "\n".join(p.read_text() for p in self.output.iterdir() if p.is_file())
        for secret in ("secret-person", "private-value", "never-persist", "private-account", "cpu-private"):
            self.assertNotIn(secret, artifacts)
        self.assertIn("c2.ffmpeg.avc.decoder", artifacts)

    def test_observe_cannot_mutate_or_reboot_and_records_v4l2_restart(self):
        self.runner.mode = "restart"
        self.assertEqual(self.cli("observe", 90), 0)
        self.assert_no_mutation()
        self.assertNotIn(["adb", "-s", self.runner.serial, "shell", "logcat", "-c"], self.runner.commands)
        result = json.loads((self.output / "result.json").read_text())
        self.assertEqual(result["observation"]["outcome"], "TIMED_RESTART")
        self.assertEqual(result["observation"]["restart_count"], 1)
        self.assertEqual(result["observation"]["codec"], "c2.v4l2.avc.decoder")

    def test_no_encrypted_event_stops_after_sixty_seconds(self):
        self.runner.mode = "none"
        self.assertEqual(self.cli(duration=600), 0)
        self.assertLessEqual(self.clock.now, 70)
        self.assertEqual(json.loads((self.output / "result.json").read_text())["observation"]["outcome"], "NO_ENCRYPTED_AVC")
        self.assertEqual(self.runner.props[H264], "true")

    def test_playback_automatic_resume_never_taps(self):
        self.assertEqual(self.cli(), 0)
        self.assertFalse(any(c[3:5] == ["shell", "input"] for c in self.runner.commands))

    def test_safe_focused_play_control_is_tapped_only_once(self):
        self.runner.mode = "needs_tap"
        self.assertEqual(self.cli(), 0)
        self.assertEqual([c[5:] for c in self.runner.commands if c[3:5] == ["shell", "input"]], [["tap", "60", "50"]])

    def test_account_purchase_and_unfocused_controls_are_never_tapped(self):
        self.runner.mode = "none"
        for text, focused in (("Subscribe", "true"), ("Sign In", "true"), ("Play", "false")):
            self.runner.ui = f'<hierarchy><node package="{APPLE_PACKAGE}" text="{text}" focused="{focused}" enabled="true" bounds="[10,20][110,80]" /></hierarchy>'
            self.output = Path(self.tmp.name) / str(self.runner.boot)
            self.assertEqual(self.cli(), 0)
        self.assertFalse(any(c[3:5] == ["shell", "input"] for c in self.runner.commands))

    def test_sustained_thermal_severe_retains_numeric_observations(self):
        self.runner.thermal = "Thermal Status: 3\nTemperature{mValue=89.0, mType=0, mName=cpu, mStatus=3}"
        self.assertEqual(self.cli(duration=600), 0)
        result = json.loads((self.output / "result.json").read_text())
        self.assertEqual(result["observation"]["outcome"], "UNUSABLE_LOAD")
        self.assertEqual(result["samples"][-1]["thermal_status"], 3)
        self.assertIn(89.0, result["samples"][-1]["temperatures_c"])
        self.assertIn(180.0, result["samples"][-1]["cpu_percent"])
        self.assertLess(self.clock.now, 600)

    def test_process_death_and_no_progress_are_unusable(self):
        for mode in ("dead", "stalled"):
            self.runner.mode = mode
            self.output = Path(self.tmp.name) / str(self.runner.boot)
            self.assertEqual(self.cli(duration=600), 0)
            self.assertEqual(json.loads((self.output / "result.json").read_text())["observation"]["outcome"], "UNUSABLE_LOAD")
            self.assertEqual(self.runner.props[H264], "true")

    def test_non_directory_output_and_existing_state_are_rejected_before_mutation(self):
        self.output.write_text("existing")
        self.assertNotEqual(self.cli(), 0)
        self.assert_no_mutation()

    def test_existing_recovery_state_is_preserved(self):
        self.assertEqual(self.cli("baseline"), 0)
        before = (self.output / "state.json").read_bytes()
        self.assertNotEqual(self.cli(), 0)
        self.assertEqual((self.output / "state.json").read_bytes(), before)
        self.assert_no_mutation()

    def test_baseline_parses_numeric_codec_ranks_and_filters_crash_logs(self):
        self.runner.queue(("logcat", "-d", "-v", "monotonic", "-s", "MediaMetrics:I", "MediaCodec:I", "CCodec:I", "AndroidRuntime:E", "ActivityManager:I", "*:S"), subprocess.CompletedProcess([], 0, f"FATAL EXCEPTION in {APPLE_PACKAGE} account=crash-secret\nAuthorization: auth-secret", ""))
        self.assertEqual(self.cli("baseline"), 0)
        baseline = json.loads((self.output / "baseline.json").read_text())
        self.assertEqual(baseline.get("codec_ranks"), {"c2.ffmpeg.avc.decoder": 16, "c2.v4l2.avc.decoder": 128})
        self.assertTrue((self.output / "baseline-codec-log.txt").exists())
        text = (self.output / "baseline-codec-log.txt").read_text()
        self.assertIn("FATAL EXCEPTION", text)
        self.assertNotIn("crash-secret", text)
        self.assertNotIn("auth-secret", text)

    def test_polling_accounts_for_command_time_in_ten_second_interval(self):
        self.runner.command_seconds = 0.2
        self.assertEqual(self.cli(duration=30), 0)
        samples = json.loads((self.output / "result.json").read_text())["samples"]
        intervals = [b["elapsed_seconds"] - a["elapsed_seconds"] for a, b in zip(samples, samples[1:])]
        self.assertTrue(intervals)
        self.assertLessEqual(max(intervals), 10.01)

    def test_numeric_cpu_from_android_top_header_is_retained(self):
        self.runner.queue(("top", "-b", "-n", "1"),
                          subprocess.CompletedProcess([], 0, "", ""),
                          subprocess.CompletedProcess([], 0, "PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ ARGS\n123 u0_a1 20 0 1G 200M 10M S 175.5 2.0 0:01 " + APPLE_PACKAGE, ""))
        self.assertEqual(self.cli(), 0)
        result = json.loads((self.output / "result.json").read_text())
        self.assertIn(175.5, result["samples"][0]["cpu_percent"])

    def test_harness_timeout_records_classified_error_before_restore(self):
        self.runner.queue(("am", "start", "-n", APPLE_PACKAGE + "/com.apple.android.tv.MainActivity"), subprocess.TimeoutExpired("adb", 30))
        self.assertNotEqual(self.cli(), 0)
        self.assertTrue((self.output / "result.json").exists(), "harness failure must leave a classified result")
        result = json.loads((self.output / "result.json").read_text())
        self.assertEqual(result["observation"]["outcome"], "HARNESS_ERROR")
        self.assertEqual(self.runner.props[H264], "true")

    def test_ui_with_account_overlay_never_taps_underlying_play(self):
        self.runner.mode = "none"
        self.runner.ui = f'<hierarchy><node package="{APPLE_PACKAGE}" text="Play" focused="true" enabled="true" bounds="[10,20][110,80]"/><node package="android" text="Choose an account" /></hierarchy>'
        self.assertEqual(self.cli(), 0)
        self.assertFalse(any(c[3:5] == ["shell", "input"] for c in self.runner.commands))

    def test_restore_reboot_failure_still_reports_actual_residual_values(self):
        self.runner.queue(("reboot",), subprocess.CompletedProcess([], 0, "", ""), subprocess.CompletedProcess([], 1, "", "reboot failed"))
        self.assertNotEqual(self.cli(), 0)
        failure = json.loads((self.output / "RESTORE_FAILED.json").read_text())
        self.assertEqual(failure["observed"], {H264: "true", RANK: "128"})

    def test_state_json_redacts_diagnostics_without_invalid_json(self):
        self.assertTrue(hasattr(probe, "write_json"))
        probe.write_json(self.output, "test.json", {"detail": "account=secret cpu=80", "other": "safe", "tail": "token=secret-token"})
        raw = (self.output / "test.json").read_text()
        self.assertNotIn("secret", raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.fail("redacted evidence is not valid JSON")
        self.assertEqual(data["detail"], "account=[REDACTED] cpu=80")
        self.assertEqual(data["other"], "safe")
        self.assertEqual(data["tail"], "token=[REDACTED]")

    def test_emergency_restore_rejects_boolean_version_before_writes(self):
        self.assertEqual(self.cli("baseline"), 0)
        state_path = self.output / "state.json"
        state = json.loads(state_path.read_text())
        state["version"] = True
        state_path.write_text(json.dumps(state))
        with patch.object(probe.subprocess, "run", self.runner), redirect_stderr(io.StringIO()):
            self.assertNotEqual(probe.main(["restore", "--serial", self.runner.serial, "--state", str(state_path)]), 0)
        self.assert_no_mutation()

    def test_emergency_restore_rejects_malformed_property_schema(self):
        self.assertEqual(self.cli("baseline"), 0)
        state_path = self.output / "state.json"
        saved = json.loads(state_path.read_text())
        for value in (None, [], "true", {"name": H264, "present": "true", "value": "true"}):
            saved["properties"][H264] = value
            state_path.write_text(json.dumps(saved))
            with patch.object(probe.subprocess, "run", self.runner), redirect_stderr(io.StringIO()):
                self.assertNotEqual(probe.main(["restore", "--serial", self.runner.serial, "--state", str(state_path)]), 0)
        self.assert_no_mutation()

    def test_emergency_restore_uses_saved_values_and_checks_identity(self):
        self.runner.props.update({H264: "TRUE", RANK: "79"})
        self.assertEqual(self.cli("baseline"), 0)
        state_path = self.output / "state.json"
        self.runner.props.update({H264: "false", RANK: "16"})
        with patch.object(probe.subprocess, "run", self.runner), redirect_stderr(io.StringIO()):
            self.assertEqual(probe.main(["restore", "--serial", self.runner.serial, "--state", str(state_path)]), 0)
        self.assertEqual(self.runner.props[H264], "TRUE")
        self.assertEqual(self.runner.props[RANK], "79")

    def test_wait_for_boot_timeout_is_bounded_without_device_reselection(self):
        self.runner.props["sys.boot_completed"] = "0"
        client = probe.AdbClient(self.runner.serial, self.runner)
        with self.assertRaises(probe.ProbeError):
            client.wait_for_boot(timeout_seconds=7)
        self.assertEqual(self.clock.now, 7)
        self.assertNotIn(["adb", "devices", "-l"], self.runner.commands)

    def test_restore_attempts_second_property_when_first_restore_write_fails(self):
        self.runner.queue(("setprop", H264, "true"), subprocess.CompletedProcess([], 1, "", "failed"))
        self.assertNotEqual(self.cli(), 0)
        self.assertEqual(self.runner.props[RANK], "128")
        failure = json.loads((self.output / "RESTORE_FAILED.json").read_text())
        self.assertEqual(failure["observed"], {H264: "false", RANK: "128"})

    def test_restore_loader_rejects_wrong_saved_identity_before_adb(self):
        self.assertEqual(self.cli("baseline"), 0)
        state_path = self.output / "state.json"
        data = json.loads(state_path.read_text())
        data["serial"] = "other"
        state_path.write_text(json.dumps(data))
        with self.assertRaises(probe.ProbeError):
            probe.load_state(state_path)
        self.assert_no_mutation()

    def test_observe_failure_is_classified_without_mutation(self):
        self.runner.queue(("am", "start", "-n", APPLE_PACKAGE + "/com.apple.android.tv.MainActivity"), subprocess.TimeoutExpired("adb", 30))
        self.assertNotEqual(self.cli("observe"), 0)
        self.assert_no_mutation()
        self.assertTrue((self.output / "result.json").exists(), "observe failure needs a classified artifact")
        self.assertEqual(json.loads((self.output / "result.json").read_text())["observation"]["outcome"], "HARNESS_ERROR")

    def test_unavailable_ui_is_no_encrypted_avc_and_restores(self):
        self.runner.mode = "none"
        self.runner.queue(("uiautomator", "dump", "/dev/tty"), subprocess.CompletedProcess([], 1, "", "UI unavailable"))
        self.assertEqual(self.cli(), 0)
        self.assertEqual(json.loads((self.output / "result.json").read_text())["observation"]["outcome"], "NO_ENCRYPTED_AVC")
        self.assertEqual(self.runner.props[H264], "true")

    def test_restoration_distinguishes_absent_from_saved_empty_value(self):
        self.runner.props[H264] = ""
        baseline = "\n".join(f"[{k}]: [{v}]" for k, v in self.runner.props.items())
        self.runner.queue(("getprop",), subprocess.CompletedProcess([], 0, baseline, ""), subprocess.CompletedProcess([], 0, f"[{RANK}]: [128]", ""))
        self.assertNotEqual(self.cli(), 0)
        failure = json.loads((self.output / "RESTORE_FAILED.json").read_text())
        self.assertEqual(failure["observed"][H264], None)

    def test_all_mutation_and_launch_error_boundaries_restore_exact_values(self):
        for command in (("setprop", H264, "false"), ("setprop", RANK, "16"), ("reboot",), ("getprop", H264), ("getprop", RANK), ("am", "start", "-n", APPLE_PACKAGE + "/com.apple.android.tv.MainActivity")):
            for failure in (subprocess.CompletedProcess([], 1, "", "failure"), subprocess.TimeoutExpired("adb", 30), KeyboardInterrupt()):
                with self.subTest(command=command, failure=type(failure).__name__):
                    self.runner = RecordingRunner(self.clock)
                    self.output = Path(self.tmp.name) / f"case-{len(list(Path(self.tmp.name).iterdir()))}"
                    self.runner.queue(command, failure)
                    self.assertNotEqual(self.cli(), 0)
                    self.assertEqual(self.runner.props[H264], "true")
                    self.assertEqual(self.runner.props[RANK], "128")
                    self.assertGreaterEqual(self.runner.boot, 2)

    def test_result_write_failure_still_restores_and_removes_temporary_files(self):
        self.output.mkdir()
        (self.output / "result.json").mkdir()
        self.assertNotEqual(self.cli(), 0)
        self.assertEqual(self.runner.props[H264], "true")
        self.assertEqual(self.runner.props[RANK], "128")
        self.assertFalse(any(path.name.startswith("tmp") for path in self.output.iterdir()))

    def test_symlink_output_rejected_before_any_mutation(self):
        real = Path(self.tmp.name) / "real"
        real.mkdir()
        try:
            os.symlink(real, self.output, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        self.assertNotEqual(self.cli(), 0)
        self.assert_no_mutation()
        self.assertEqual(list(real.iterdir()), [])

    def test_changed_serial_and_offline_target_abort_before_mutation(self):
        for devices in ([("other", "device")], [(self.runner.serial, "offline")]):
            self.runner.devices = devices
            self.assertNotEqual(self.cli(), 0)
            self.assert_no_mutation()

    def test_root_denial_aborts_before_property_changes(self):
        self.runner.queue(("id", "-u"), subprocess.CompletedProcess([], 0, "2000", ""))
        self.assertNotEqual(self.cli(), 0)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.runner.boot, 1)

    def test_hardware_inventory_must_return_after_restoration(self):
        self.runner.hardware = False
        self.assertNotEqual(self.cli(), 0)
        failure = json.loads((self.output / "RESTORE_FAILED.json").read_text())
        self.assertIn("hardware AVC", failure["error"])
        self.assertEqual(failure["observed"], {H264: "true", RANK: "128"})

    def test_full_six_hundred_second_run_uses_continuous_session_evidence(self):
        self.assertEqual(self.cli(duration=600), 0)
        result = json.loads((self.output / "result.json").read_text())
        self.assertEqual(result["observation"]["outcome"], "PLAYED_600_SECONDS")
        self.assertEqual(result["observation"]["continuous_seconds"], 600)
        self.assertEqual(result["observation"]["restart_count"], 0)
        self.assertEqual(self.runner.props[H264], "true")

    def test_result_records_requested_duration_and_observation_mode(self):
        self.assertEqual(self.cli("observe", 90), 0)
        result = json.loads((self.output / "result.json").read_text())
        self.assertEqual(result.get("requested_duration_seconds"), 90)
        self.assertEqual(result.get("mode"), "observe")

    def test_probe_boot_timeout_restores_after_bounded_wait(self):
        self.runner.queue(("getprop", "sys.boot_completed"), *(subprocess.CompletedProcess([], 0, "0", "") for _ in range(90)))
        self.assertNotEqual(self.cli(), 0)
        self.assertEqual(self.clock.now, 180)
        self.assertEqual(self.runner.props[H264], "true")
        self.assertEqual(self.runner.props[RANK], "128")
        self.assertEqual(self.runner.boot, 3)

    def test_refuses_mutation_when_redaction_would_change_recovery_values(self):
        self.runner.props[RANK] = "account=private-value"
        self.assertNotEqual(self.cli(), 0)
        self.assert_no_mutation()
        self.assertEqual(self.runner.props[RANK], "account=private-value")

    def test_unauthorized_cli_serial_is_rejected_before_any_device_operation(self):
        for action in ("baseline", "probe", "observe"):
            with self.subTest(action=action):
                self.runner.serial = "192.0.2.4:5555"
                self.runner.devices = [(self.runner.serial, "device")]
                self.output = Path(self.tmp.name) / action
                self.assertNotEqual(self.cli(action), 0)
                self.assertEqual(self.runner.commands, [])

    def test_unauthorized_recovery_serial_is_rejected_before_device_operations(self):
        self.assertEqual(self.cli("baseline"), 0)
        state_path = self.output / "state.json"
        data = json.loads(state_path.read_text())
        data["serial"] = "192.0.2.4:5555"
        state_path.write_text(json.dumps(data))
        self.runner.commands.clear()
        with patch.object(probe.subprocess, "run", self.runner), redirect_stderr(io.StringIO()):
            self.assertNotEqual(probe.main(["restore", "--serial", "10000000f93771d0", "--state", str(state_path)]), 0)
        self.assertEqual(self.runner.commands, [])

    def test_interruptions_during_either_restore_write_complete_remaining_cleanup(self):
        for name, value in ((H264, "true"), (RANK, "128")):
            for interruption in (KeyboardInterrupt(), SystemExit(9), BaseException("cancelled")):
                with self.subTest(property=name, interruption=type(interruption).__name__):
                    self.runner = RecordingRunner(self.clock)
                    self.output = Path(self.tmp.name) / f"interrupt-{len(list(Path(self.tmp.name).iterdir()))}"
                    self.runner.queue(("setprop", name, value), interruption)
                    try:
                        status = self.cli()
                    except BaseException as exc:
                        self.fail(f"restoration interruption escaped cleanup: {type(exc).__name__}")
                    self.assertNotEqual(status, 0)
                    self.assertEqual(self.writes()[-2:], [["setprop", H264, "true"], ["setprop", RANK, "128"]])
                    self.assertEqual(self.runner.boot, 3)
                    self.assertTrue((self.output / "RESTORE_FAILED.json").exists(), "cleanup interruption must leave residual evidence")
                    failure = json.loads((self.output / "RESTORE_FAILED.json").read_text())
                    self.assertEqual(failure["observed"], {H264: "false" if name == H264 else "true", RANK: "16" if name == RANK else "128"})
                    self.assertIn(type(interruption).__name__, failure["error"])
                    self.assertIn(["adb", "-s", "10000000f93771d0", "shell", "cmd", "package", "list", "packages", APPLE_PACKAGE], self.runner.commands)

    def test_ninety_second_run_never_claims_six_hundred_second_success(self):
        for action in ("probe", "observe"):
            with self.subTest(action=action):
                self.runner = RecordingRunner(self.clock)
                self.output = Path(self.tmp.name) / action
                self.assertEqual(self.cli(action, 90), 0)
                result = json.loads((self.output / "result.json").read_text())
                self.assertNotEqual(result["observation"]["outcome"], "PLAYED_600_SECONDS")

    def test_watchdog_requires_real_time_media_progress_from_qualifying_session(self):
        for mode in ("missing_progress", "stagnant_progress", "sparse_frames", "wrong_session_progress", "wrong_codec_progress"):
            with self.subTest(mode=mode):
                self.runner = RecordingRunner(self.clock)
                self.runner.mode = mode
                self.output = Path(self.tmp.name) / mode
                began = self.clock.now
                self.assertEqual(self.cli(duration=600), 0)
                result = json.loads((self.output / "result.json").read_text())
                self.assertEqual(result["observation"]["outcome"], "UNUSABLE_LOAD")
                self.assertLessEqual(self.clock.now - began, 45, "event timestamp advancement must not reset the media-progress watchdog")
                self.assertEqual(self.runner.props[H264], "true")


if __name__ == "__main__":
    unittest.main()
