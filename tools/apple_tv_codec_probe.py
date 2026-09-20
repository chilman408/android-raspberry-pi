#!/usr/bin/env python3
"""Evidence parsing and a guarded, reversible Apple TV AVC playback experiment."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import argparse
from datetime import datetime, timezone
from enum import Enum
import json
import math
import re
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Sequence
import xml.etree.ElementTree as ET


APPLE_TV_PACKAGE = "com.apple.atve.androidtv.appletv"
SOFTWARE_AVC_DECODER = "c2.ffmpeg.avc.decoder"
REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class PropertyState:
    name: str
    present: bool
    value: str


@dataclass(frozen=True)
class CodecEvent:
    timestamp: float
    package: str
    mime: str
    codec: str
    encrypted: bool
    event: str
    session_id: str = field(default="", compare=False, repr=False)
    position_seconds: float | None = None


class ProbeOutcome(str, Enum):
    NO_SOFTWARE_DECODER = "NO_SOFTWARE_DECODER"
    NO_ENCRYPTED_AVC = "NO_ENCRYPTED_AVC"
    TIMED_RESTART = "TIMED_RESTART"
    UNUSABLE_LOAD = "UNUSABLE_LOAD"
    PLAYED_600_SECONDS = "PLAYED_600_SECONDS"
    HARNESS_ERROR = "HARNESS_ERROR"


@dataclass(frozen=True)
class PlaybackObservation:
    outcome: ProbeOutcome
    codec: str | None
    continuous_seconds: float
    restart_count: int
    reason: str
    media_progress_seconds: float = 0.0


_GETPROP_LINE = re.compile(r"^\s*\[([^\]]+)\]:\s*\[([^\]]*)\]\s*$")
_COMPONENT = re.compile(r"\b((?:c2|OMX)\.[A-Za-z0-9_.-]+)\b")
_FIELD = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|[^\s]+)")
_TIMESTAMP = re.compile(r"^\s*\[?(\d+(?:\.\d+)?)\]?")


def parse_getprop_listing(text: str) -> dict[str, PropertyState]:
    """Return only properties present in a conventional ``getprop`` listing."""
    properties: dict[str, PropertyState] = {}
    for line in text.splitlines():
        match = _GETPROP_LINE.match(line)
        if match:
            name, value = match.groups()
            properties[name] = PropertyState(name=name, present=True, value=value)
    return properties


def parse_codec_inventory(text: str) -> Sequence[str]:
    """Extract codec component names once, preserving their listed order."""
    inventory: list[str] = []
    seen: set[str] = set()
    for match in _COMPONENT.finditer(text):
        component = match.group(1)
        if component not in seen:
            seen.add(component)
            inventory.append(component)
    return inventory


def parse_widevine_state(text: str) -> dict[str, str]:
    """Read explicitly reported Widevine security and HDCP fields."""
    labels = (
        ("securityLevel", re.compile(r"^\s*security\s*level\s*[:=]\s*(.+?)\s*$", re.I)),
        ("OEMCrypto", re.compile(r"^\s*OEMCrypto\s*[:=]\s*(.+?)\s*$", re.I)),
        (
            "currentHdcpLevel",
            re.compile(r"^\s*(?:current\s+)?HDCP\s+level\s*[:=]\s*(.+?)\s*$", re.I),
        ),
        (
            "maximumHdcpLevel",
            re.compile(r"^\s*(?:maximum|max)\s+HDCP\s+level\s*[:=]\s*(.+?)\s*$", re.I),
        ),
    )
    state: dict[str, str] = {}
    for line in text.splitlines():
        for name, pattern in labels:
            match = pattern.match(line)
            if match:
                state[name] = match.group(1)
                break
    return state


def _fields_from_line(line: str) -> dict[str, str]:
    return {name.lower(): value.strip("\"'") for name, value in _FIELD.findall(line)}


def _is_encrypted(fields: dict[str, str]) -> bool:
    evidence = [
        fields[name].lower()
        for name in ("crypto", "encrypted", "secure")
        if name in fields
    ]
    if any(value in {"0", "false", "no"} for value in evidence):
        return False
    return any(value in {"1", "true", "yes"} for value in evidence)


def parse_codec_events(text: str) -> list[CodecEvent]:
    """Keep Apple TV encrypted AVC events having an explicit component name."""
    events: list[CodecEvent] = []
    for line in text.splitlines():
        timestamp = _TIMESTAMP.match(line)
        if not timestamp:
            continue
        fields = _fields_from_line(line)
        package = fields.get("package")
        mime = fields.get("mime")
        codec = fields.get("component") or fields.get("codec")
        event = fields.get("event", "").lower()
        if (
            package != APPLE_TV_PACKAGE
            or mime != "video/avc"
            or not codec
            or not event
            or not _is_encrypted(fields)
        ):
            continue
        events.append(
            CodecEvent(
                timestamp=float(timestamp.group(1)),
                package=package,
                mime=mime,
                codec=codec,
                encrypted=True,
                event=event,
                session_id=fields.get("session", ""),
                position_seconds=_explicit_position(fields.get("position_seconds")),
            )
        )
    return events


def _explicit_position(value):
    """Accept only the normalized counter, never infer progress from timestamps."""
    if value is None or not re.fullmatch(r"\d+(?:\.\d+)?", value):
        return None
    position = float(value)
    return position if math.isfinite(position) else None


class _MediaProgress:
    """Validated contiguous progress per explicit codec/session identity.

    At most 15 seconds may separate samples, allowing margin over 10s polling.
    Each media delta must be 90%-110% of elapsed event time. Missing counters,
    stalls, seeks, resets and gaps break the proven span instead of being joined.
    """
    def __init__(self):
        self.active = {}
        self.best = {}

    def add(self, event):
        if (event.package != APPLE_TV_PACKAGE or event.mime != "video/avc"
                or not event.encrypted or not event.session_id):
            return False
        key = (event.codec, event.session_id)
        position = event.position_seconds
        if position is not None and (not math.isfinite(position) or position < 0):
            position = None
        if event.event == "start":
            self.active[key] = (event.timestamp, position, 0.0, 0.0)
            return False
        if event.event == "stop":
            self.active.pop(key, None)
            return False
        if event.event != "sample" or key not in self.active:
            return False
        stamp, previous, elapsed_span, media_span = self.active[key]
        elapsed = event.timestamp - stamp
        delta = position - previous if position is not None and previous is not None else None
        progressing = (delta is not None and 0 < elapsed <= 15
                       and 0.9 <= delta / elapsed <= 1.1)
        if progressing:
            elapsed_span += elapsed
            media_span += delta
            self.best[key] = max(self.best.get(key, 0.0), min(elapsed_span, media_span))
        else:
            elapsed_span = media_span = 0.0
        self.active[key] = (event.timestamp, position, elapsed_span, media_span)
        return progressing

    def seconds(self, codec):
        return max((seconds for (component, _session), seconds in self.best.items()
                    if component == codec), default=0.0)


def _closed_sessions(
    events: Sequence[CodecEvent],
) -> tuple[list[tuple[str, float, bool]], int]:
    """Return independent session durations and explicit restart count."""
    active: dict[tuple[str, str], tuple[float, float, bool]] = {}
    sessions: list[tuple[str, float, bool]] = []
    starts: dict[str, int] = {}

    for event in sorted(events, key=lambda item: item.timestamp):
        session_key = (event.codec, event.session_id)
        if event.event == "start":
            previous = active.pop(session_key, None)
            if previous is not None:
                sessions.append(
                    (event.codec, max(0.0, previous[1] - previous[0]), previous[2])
                )
            starts[event.codec] = starts.get(event.codec, 0) + 1
            active[session_key] = (event.timestamp, event.timestamp, False)
        elif session_key in active and event.event in {"sample", "stop"}:
            started, _latest, sampled = active[session_key]
            sampled = sampled or event.event == "sample"
            active[session_key] = (started, event.timestamp, sampled)
            if event.event == "stop":
                sessions.append(
                    (event.codec, max(0.0, event.timestamp - started), sampled)
                )
                del active[session_key]

    for (codec, _session_id), (started, latest, sampled) in active.items():
        sessions.append((codec, max(0.0, latest - started), sampled))
    return sessions, sum(max(0, count - 1) for count in starts.values())


def classify_observation(
    events: Sequence[CodecEvent], required_seconds: int = 600
) -> PlaybackObservation:
    """Classify encrypted AVC evidence without joining separate sessions."""
    encrypted_events = [
        event
        for event in events
        if (
            event.encrypted
            and event.package == APPLE_TV_PACKAGE
            and event.mime == "video/avc"
        )
    ]
    if not encrypted_events:
        return PlaybackObservation(
            ProbeOutcome.NO_ENCRYPTED_AVC, None, 0.0, 0, "no encrypted AVC event"
        )

    sessions, restart_count = _closed_sessions(encrypted_events)
    best_codec, best_duration, _best_sampled = max(
        sessions, key=lambda item: item[1], default=(None, 0.0, False)
    )

    if restart_count:
        return PlaybackObservation(
            ProbeOutcome.TIMED_RESTART,
            best_codec,
            best_duration,
            restart_count,
            "encrypted AVC playback restarted",
        )

    software_sessions = [item for item in sessions if item[0] == SOFTWARE_AVC_DECODER]
    if not software_sessions:
        return PlaybackObservation(
            ProbeOutcome.NO_SOFTWARE_DECODER,
            best_codec,
            best_duration,
            0,
            "no c2.ffmpeg.avc.decoder session",
        )

    software_codec, software_duration, software_sampled = max(
        software_sessions, key=lambda item: item[1]
    )
    progress = _MediaProgress()
    for event in sorted(encrypted_events, key=lambda item: item.timestamp):
        progress.add(event)
    media_seconds = progress.seconds(SOFTWARE_AVC_DECODER)
    if media_seconds >= max(600, required_seconds) and software_sampled:
        return PlaybackObservation(
            ProbeOutcome.PLAYED_600_SECONDS,
            software_codec,
            software_duration,
            0,
            "one software AVC session proved at least 600 seconds of near-real-time media progress",
            media_seconds,
        )
    return PlaybackObservation(
        ProbeOutcome.UNUSABLE_LOAD,
        software_codec,
        software_duration,
        0,
        "software AVC lacks 600 continuous seconds of explicit near-real-time media progress",
        media_seconds,
    )


_HEADER_SECRET_PREFIX = re.compile(
    r"(?i)\b(?:authorization|set-cookie|cookie)\s*:\s*"
)
_FIELD_SECRET_PREFIX = re.compile(
    r"(?i)\b(?:account|email|token|licenseRequest|licenseResponse|keySetId|drmPayload)\b"
    r"(?:[\"']?\s*(?:=|:)\s*)"
)
_DIAGNOSTIC_FIELD = re.compile(
    r"\s+(?=(?:package|mime|component|codec|error|cpu|thermal)\s*=)", re.I
)


def _diagnostic_tail_or_line_end(text: str, start: int) -> int:
    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    tail = _DIAGNOSTIC_FIELD.search(text, start, line_end)
    return tail.start() if tail else line_end


def _next_sensitive_or_diagnostic_boundary(text: str, start: int) -> int:
    boundary = _diagnostic_tail_or_line_end(text, start)
    next_sensitive = _FIELD_SECRET_PREFIX.search(text, start, boundary)
    return next_sensitive.start() if next_sensitive else boundary


def _sensitive_value_end(text: str, start: int) -> int:
    if start >= len(text) or text[start] == "\n":
        return start
    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    value_start = text[start]
    if value_start in "\"'":
        escaped = False
        for index in range(start + 1, line_end):
            character = text[index]
            if character == value_start and not escaped:
                return index + 1
            escaped = character == "\\" and not escaped
            if character != "\\":
                escaped = False
        return _next_sensitive_or_diagnostic_boundary(text, start)
    if value_start in "{[":
        try:
            _value, consumed = json.JSONDecoder().raw_decode(text[start:line_end])
            return start + consumed
        except json.JSONDecodeError:
            return _next_sensitive_or_diagnostic_boundary(text, start)
    return _next_sensitive_or_diagnostic_boundary(text, start)


def _redact_sensitive_fields(text: str) -> str:
    redacted: list[str] = []
    cursor = 0
    for match in _FIELD_SECRET_PREFIX.finditer(text):
        if match.start() < cursor:
            continue
        redacted.append(text[cursor:match.end()])
        value_end = _sensitive_value_end(text, match.end())
        redacted.append(REDACTED)
        cursor = value_end
    redacted.append(text[cursor:])
    return "".join(redacted)


def _redact_header_values(text: str) -> str:
    redacted: list[str] = []
    cursor = 0
    for match in _HEADER_SECRET_PREFIX.finditer(text):
        if match.start() < cursor:
            continue
        redacted.append(text[cursor:match.end()])
        redacted.append(REDACTED)
        cursor = _diagnostic_tail_or_line_end(text, match.end())
    redacted.append(text[cursor:])
    return "".join(redacted)


def redact_diagnostic_text(text: str) -> str:
    """Replace sensitive diagnostic values while retaining non-secret evidence."""
    text = _redact_header_values(text)
    return _redact_sensitive_fields(text)


TARGET_PRODUCT = "tesla_android_rpi4"
AUTHORIZED_SERIAL = "10000000f93771d0"
TARGET_DEVICE = "gd_rpi4"
HARDWARE_AVC_DECODER = "c2.v4l2.avc.decoder"
PROBE_PROPERTIES = {
    "persist.ffmpeg_codec2.v4l2.h264": "false",
    "persist.ffmpeg_codec2.rank.video": "16",
}
PROPERTY_PREFIXES = ("persist.ffmpeg_codec2.", "ro.vendor.v4l2_codec2.", "ro.vendor.ffmpeg_codec2.")


class ProbeError(RuntimeError):
    """A bounded device operation or a safety precondition failed."""


class RestoreError(ProbeError):
    def __init__(self, message, expected, observed):
        super().__init__(message)
        self.expected = expected
        self.observed = observed


@dataclass(frozen=True)
class ProbeState:
    serial: str
    product: str
    device: str
    boot_id: str
    properties: dict[str, PropertyState]
    captured_at: str

    def to_dict(self):
        return {"version": 1, **asdict(self)}


class AdbClient:
    def __init__(self, serial: str, runner=subprocess.run):
        if serial != AUTHORIZED_SERIAL:
            raise ProbeError("only the explicitly authorized Raspberry Pi serial is permitted")
        self._serial = serial
        self.runner = runner

    @property
    def serial(self):
        return self._serial

    def _execute(self, command, timeout_seconds):
        if self.serial != AUTHORIZED_SERIAL:
            raise ProbeError("ADB serial changed from the authorized target")
        try:
            result = self.runner(command, text=True, capture_output=True,
                                 timeout=timeout_seconds, check=False)
        except (subprocess.TimeoutExpired, OSError) as exc:
            # Exception command/stdout/stderr may include sensitive payloads.
            raise ProbeError(f"ADB operation failed: {type(exc).__name__}") from exc
        if result.returncode:
            raise ProbeError(f"ADB operation exited {result.returncode}: " + redact_diagnostic_text(result.stderr))
        return result.stdout

    def run(self, *args, timeout_seconds=30):
        return self._execute(["adb", "-s", self.serial, *args], timeout_seconds)

    def shell(self, *args, timeout_seconds=30):
        # adb joins shell arguments on the remote side; quote every token there.
        return self.run("shell", *(shlex.quote(arg) for arg in args), timeout_seconds=timeout_seconds)

    def list_devices(self):
        text = self._execute(["adb", "devices", "-l"], 30)
        return [(parts[0], parts[1]) for line in text.splitlines()
                if len(parts := line.split()) >= 2 and not line.startswith(("List ", "*"))]

    def root(self):
        self.run("root")
        self.run("wait-for-device", timeout_seconds=60)
        if self.shell("id", "-u").strip() != "0":
            raise ProbeError("ADB root was not granted")

    def reboot(self):
        self.run("reboot")

    def wait_for_boot(self, timeout_seconds=180):
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                remaining = max(0.01, min(10, deadline - time.monotonic()))
                if self.shell("getprop", "sys.boot_completed", timeout_seconds=remaining).strip() == "1":
                    remaining = max(0.01, min(10, deadline - time.monotonic()))
                    packages = self.shell("cmd", "package", "list", "packages", APPLE_TV_PACKAGE, timeout_seconds=remaining)
                    if f"package:{APPLE_TV_PACKAGE}" in packages:
                        return
            except ProbeError:
                pass
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        raise ProbeError("same-serial boot/package-manager readiness timed out")


def guard_target(client, state=None):
    if client.serial != AUTHORIZED_SERIAL or (state and state.serial != AUTHORIZED_SERIAL):
        raise ProbeError("target/state serial is not the authorized Raspberry Pi")
    connected = [serial for serial, status in client.list_devices() if status == "device"]
    if connected != [client.serial]:
        raise ProbeError("require exactly one online device matching the explicit serial")
    product = client.shell("getprop", "ro.product.name").strip()
    device = client.shell("getprop", "ro.product.device").strip()
    if product != TARGET_PRODUCT or device != TARGET_DEVICE:
        raise ProbeError("target product/device does not match the Raspberry Pi image")
    if state and (state.serial != client.serial or state.product != product or state.device != device):
        raise ProbeError("saved target identity does not match connected target")
    boot_id = client.shell("cat", "/proc/sys/kernel/random/boot_id").strip()
    if not boot_id:
        raise ProbeError("target boot ID is missing")
    return product, device, boot_id


def prepare_output(output_dir):
    output_dir = Path(output_dir).absolute()
    for part in (output_dir, *output_dir.parents):
        if part.is_symlink():
            raise ProbeError("output path must not contain a symlink")
    if output_dir.exists() and not output_dir.is_dir():
        raise ProbeError("output path is not a directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def write_evidence(output_dir, name, text):
    _atomic_write(output_dir, name, redact_diagnostic_text(text))


def _atomic_write(output_dir, name, text):
    directory = prepare_output(output_dir)
    if Path(name).name != name or (directory / name).is_symlink():
        raise ProbeError("unsafe evidence filename")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, directory / name)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(output_dir, name, value):
    # Redact individual string values before encoding to keep valid JSON.
    def sanitized(item):
        if isinstance(item, str):
            return redact_diagnostic_text(item)
        if isinstance(item, dict):
            return {key: sanitized(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [sanitized(val) for val in item]
        return item
    _atomic_write(output_dir, name, json.dumps(sanitized(value), indent=2) + "\n")


def diagnostic_lines(text):
    """Persist only codec/process/load diagnostics, never complete service logs."""
    return "\n".join(line for line in text.splitlines() if re.search(
        r"c2\.|OMX\.|MediaCodec|MediaMetrics|CCodec|video/avc|" + re.escape(APPLE_TV_PACKAGE) +
        r"|FATAL EXCEPTION|Thermal Status|Temperature\{|\bcpu\s*=|\bthermal\s*=", line))


def capture_baseline(client: AdbClient, output_dir: Path) -> ProbeState:
    output_dir = prepare_output(output_dir)
    if (output_dir / "state.json").exists():
        raise ProbeError("state.json already exists; use a fresh evidence directory")
    product, device, boot_id = guard_target(client)
    listing = parse_getprop_listing(client.shell("getprop"))
    properties = {name: value for name, value in listing.items() if name.startswith(PROPERTY_PREFIXES)}
    for name in PROBE_PROPERTIES:
        properties.setdefault(name, PropertyState(name, False, ""))
    state = ProbeState(client.serial, product, device, boot_id, properties, datetime.now(timezone.utc).isoformat())
    write_json(output_dir, "state.json", state.to_dict())
    player = client.shell("dumpsys", "media.player")
    drm = client.shell("dumpsys", "media.drm")
    metadata = {key: listing[key].value if key in listing else None for key in (
        "ro.product.model", "ro.build.fingerprint", "ro.build.type", "ro.boot.verifiedbootstate")}
    metadata["avc_components"] = [c for c in parse_codec_inventory(player) if ".avc." in c]
    metadata["codec_rank_lines"] = [line for line in player.splitlines() if re.search(r"\brank\b", line, re.I)]
    metadata["codec_ranks"] = {}
    for line in player.splitlines():
        component = _COMPONENT.search(line)
        rank = re.search(r"\brank\s*[:=]\s*(\d+)", line, re.I)
        if component and rank:
            metadata["codec_ranks"][component.group(1)] = int(rank.group(1))
    metadata["widevine"] = parse_widevine_state(drm)
    write_json(output_dir, "baseline.json", metadata)
    write_evidence(output_dir, "codec-properties.txt", "\n".join(f"[{p.name}]: [{p.value}]" for p in properties.values() if p.present))
    write_evidence(output_dir, "media-player.txt", diagnostic_lines(player))
    for package, filename in ((APPLE_TV_PACKAGE, "apple-version.txt"), ("com.netflix.ninja", "netflix-version.txt")):
        package_dump = client.shell("dumpsys", "package", package)
        write_evidence(output_dir, filename, "\n".join(line for line in package_dump.splitlines() if re.search(r"\bversion(?:Code|Name)=", line)))
    for name, command in (
        ("metrics", ("dumpsys", "media.metrics")),
        ("processes", ("dumpsys", "activity", "processes")),
        ("cpu", ("top", "-b", "-n", "1")),
        ("thermal", ("dumpsys", "thermalservice")),
    ):
        write_evidence(output_dir, f"baseline-{name}.txt", diagnostic_lines(client.shell(*command)))
    write_evidence(output_dir, "baseline-codec-log.txt", diagnostic_lines(read_codec_evidence(client)))
    return state


def require_restorable_target_properties(state):
    for name in PROBE_PROPERTIES:
        prop = state.properties.get(name)
        if prop is None or not prop.present:
            raise ProbeError(f"cannot restore absent persistent property: {name}")
        if REDACTED in prop.value or redact_diagnostic_text(prop.value) != prop.value:
            raise ProbeError(f"cannot safely persist an exact recovery value: {name}")


def apply_software_avc_probe(client: AdbClient, state: ProbeState) -> None:
    require_restorable_target_properties(state)
    guard_target(client, state)
    for name, value in PROBE_PROPERTIES.items():
        client.shell("setprop", name, value)


def verify_probe_properties(client):
    for name, expected in PROBE_PROPERTIES.items():
        if client.shell("getprop", name).rstrip("\r\n") != expected:
            raise ProbeError(f"probe property verification failed: {name}")


def restore_probe_state(client: AdbClient, state: ProbeState) -> None:
    require_restorable_target_properties(state)
    expected = {name: state.properties[name].value for name in PROBE_PROPERTIES}
    observed = {name: None for name in PROBE_PROPERTIES}
    errors = []
    # Once identity is verified, every cleanup stage gets one bounded attempt.
    # Interruption is recorded, not allowed to skip the remaining saved writes.
    def attempt(operation, *args):
        try:
            return operation(*args)
        except BaseException as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            return None

    try:
        guard_target(client, state)
    except BaseException as exc:
        raise RestoreError(f"{type(exc).__name__}: {exc}", expected, observed) from exc
    attempt(client.root)
    for name, value in expected.items():
        attempt(client.shell, "setprop", name, value)
    attempt(client.reboot)
    attempt(client.wait_for_boot)
    listing = attempt(client.shell, "getprop")
    if listing is not None:
        restored = parse_getprop_listing(listing)
        observed.update({name: restored[name].value if name in restored else None for name in expected})
    inventory = attempt(client.shell, "dumpsys", "media.player")
    if inventory is not None and HARDWARE_AVC_DECODER not in parse_codec_inventory(inventory):
        errors.append("restored hardware AVC component is not advertised")
    if errors or observed != expected:
        raise RestoreError("; ".join(errors) or "restored property values differ", expected, observed)


def read_codec_evidence(client):
    metrics = client.shell("dumpsys", "media.metrics", timeout_seconds=1)
    logs = client.shell("logcat", "-d", "-v", "monotonic", "-s", "MediaMetrics:I", "MediaCodec:I", "CCodec:I", "AndroidRuntime:E", "ActivityManager:I", "*:S", timeout_seconds=1)
    return metrics + "\n" + logs


def safe_play_tap(client, ui):
    try:
        start = ui.index("<hierarchy")
        end = ui.index("</hierarchy>", start) + len("</hierarchy>")
        root = ET.fromstring(ui[start:end])
    except (ValueError, ET.ParseError):
        return
    if any(node.get("package") not in (None, "", APPLE_TV_PACKAGE) for node in root.iter("node")):
        return
    focused = [node for node in root.iter("node") if node.get("focused") == "true" and node.get("enabled") == "true"]
    if len(focused) != 1:
        return
    node = focused[0]
    if node.get("package") != APPLE_TV_PACKAGE:
        return
    if (node.get("text") or node.get("content-desc") or "").strip().casefold() not in {"play", "resume", "retry", "watch now"}:
        return
    bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    if bounds:
        left, top, right, bottom = map(int, bounds.groups())
        if right > left and bottom > top:
            client.shell("input", "tap", str((left + right) // 2), str((top + bottom) // 2))


def cpu_percentages(text):
    column = None
    values = []
    for line in text.splitlines():
        cells = line.split()
        if "%CPU" in cells:
            column = cells.index("%CPU")
        elif APPLE_TV_PACKAGE in line:
            if column is not None and len(cells) > column:
                try:
                    values.append(float(cells[column].rstrip("%")))
                except ValueError:
                    pass
            else:
                values.extend(float(value) for value in re.findall(r"(\d+(?:\.\d+)?)%", line))
    return values


def run_playback_observation(client, output_dir, duration_seconds, *, read_only=False):
    if not read_only:
        client.shell("logcat", "-c")
    # Ignore historical snapshots; repeated dumps must not manufacture restarts.
    seen = {event_key(event) for event in parse_codec_events(read_codec_evidence(client))}
    client.shell("am", "start", "-n", APPLE_TV_PACKAGE + "/com.apple.android.tv.MainActivity")
    started = time.monotonic()
    try:
        ui = client.shell("uiautomator", "dump", "/dev/tty", timeout_seconds=5)
    except ProbeError:
        ui = ""  # Unsafe/unavailable controls mean no input, not a guessed tap.
    initial = read_codec_evidence(client)
    if not any(event_key(event) not in seen for event in parse_codec_events(initial)):
        safe_play_tap(client, ui)
    events, samples, evidence = [], [], []
    first_event_at = None
    last_progress_at = started
    progress = _MediaProgress()
    watched_session = None
    severe_since = None
    load_reason = None
    current = initial
    while True:
        now = time.monotonic()
        for event in parse_codec_events(current):
            key = event_key(event)
            if key not in seen:
                seen.add(key)
                events.append(event)
                if first_event_at is None and now - started <= 60 and (read_only or event.codec == SOFTWARE_AVC_DECODER):
                    first_event_at = now
                qualifies = read_only or event.codec == SOFTWARE_AVC_DECODER
                key = (event.codec, event.session_id)
                if event.event == "start" and qualifies:
                    watched_session = key
                    last_progress_at = now
                if progress.add(event) and key == watched_session and qualifies:
                    last_progress_at = now
        evidence.append(diagnostic_lines(current))
        try:
            pid = client.shell("pidof", APPLE_TV_PACKAGE, timeout_seconds=1).strip()
        except ProbeError:
            pid = ""
        cpu = client.shell("top", "-b", "-n", "1", timeout_seconds=1)
        thermal = client.shell("dumpsys", "thermalservice", timeout_seconds=1)
        process = client.shell("dumpsys", "activity", "processes", timeout_seconds=1)
        status_match = re.search(r"Thermal Status:\s*(\d+)", thermal, re.I)
        status = int(status_match.group(1)) if status_match else None
        samples.append({"elapsed_seconds": now - started, "pid": pid,
                        "thermal_status": status,
                        "temperatures_c": [float(v) for v in re.findall(r"mValue=(-?\d+(?:\.\d+)?)", thermal)],
                        "cpu_percent": cpu_percentages(cpu)})
        evidence.extend(diagnostic_lines(text) for text in (cpu, thermal, process))
        if status is not None and status >= 3:
            if severe_since is None:
                severe_since = now
        else:
            severe_since = None
        if events and not pid:
            load_reason = "Apple process died during encrypted AVC playback"
        elif severe_since is not None and now - severe_since >= 20:
            load_reason = "thermal severe/critical state sustained for 20 seconds"
        elif first_event_at is not None and now - last_progress_at >= 30:
            load_reason = "no explicit near-real-time media progress in the watched session for 30 seconds"
        if load_reason or (first_event_at is None and now - started >= 60):
            break
        if first_event_at is not None and now - first_event_at >= duration_seconds:
            break
        # Reserve two one-second command budgets for the next codec snapshot.
        time.sleep(max(0, now + 8 - time.monotonic()))
        current = read_codec_evidence(client)
    observation = classify_observation(events)
    if first_event_at is None:
        observation = PlaybackObservation(ProbeOutcome.NO_ENCRYPTED_AVC, None, 0, 0, "no matching encrypted AVC event within 60 seconds")
    elif load_reason:
        observation = PlaybackObservation(ProbeOutcome.UNUSABLE_LOAD, observation.codec, observation.continuous_seconds, observation.restart_count, load_reason, observation.media_progress_seconds)
    write_evidence(output_dir, "playback.txt", "\n".join(evidence))
    write_json(output_dir, "events.json", [asdict(event) for event in events])
    # No screenshot: a UI hierarchy cannot prove that a frame contains no private overlay.
    return observation, samples


def event_key(event):
    return (event.timestamp, event.package, event.mime, event.codec, event.encrypted, event.event, event.session_id, event.position_seconds)


def write_result(output_dir, state, observation, samples=(), *, duration_seconds=600, mode="probe"):
    write_json(output_dir, "result.json", {"version": 1, "serial": state.serial,
               "requested_duration_seconds": duration_seconds, "mode": mode,
               "observation": asdict(observation), "samples": samples, "screenshot": "omitted: privacy cannot be established"})


def run_probe(client, output_dir, duration_seconds):
    state = capture_baseline(client, output_dir)
    mutated = False
    try:
        require_restorable_target_properties(state)
        guard_target(client, state)
        client.root()
        # A failed/uncertain write may already have changed the target.
        mutated = True
        apply_software_avc_probe(client, state)
        client.reboot()
        client.wait_for_boot()
        verify_probe_properties(client)
        if SOFTWARE_AVC_DECODER not in parse_codec_inventory(client.shell("dumpsys", "media.player")):
            observation = PlaybackObservation(ProbeOutcome.NO_SOFTWARE_DECODER, None, 0, 0, "software AVC is not advertised")
            write_result(output_dir, state, observation, duration_seconds=duration_seconds)
        else:
            observation, samples = run_playback_observation(client, output_dir, duration_seconds)
            write_result(output_dir, state, observation, samples, duration_seconds=duration_seconds)
    except (Exception, KeyboardInterrupt) as exc:
        write_result(output_dir, state, PlaybackObservation(ProbeOutcome.HARNESS_ERROR, None, 0, 0, f"{type(exc).__name__}: {exc}"), duration_seconds=duration_seconds)
        raise
    finally:
        if mutated:
            try:
                restore_probe_state(client, state)
            except RestoreError as exc:
                write_json(output_dir, "RESTORE_FAILED.json", {"expected": exc.expected, "observed": exc.observed, "error": str(exc)})
                raise


def load_state(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ProbeError("unsupported state schema/version")
    if data.get("serial") != AUTHORIZED_SERIAL:
        raise ProbeError("saved serial is not the authorized Raspberry Pi")
    required = ("serial", "product", "device", "boot_id", "captured_at")
    if not all(isinstance(data.get(name), str) and data[name] for name in required):
        raise ProbeError("invalid saved identity")
    if data["product"] != TARGET_PRODUCT or data["device"] != TARGET_DEVICE:
        raise ProbeError("saved state is not for the required product/device")
    properties = data.get("properties")
    if not isinstance(properties, dict):
        raise ProbeError("invalid saved properties")
    parsed = {}
    for name, prop in properties.items():
        if (not name.startswith(PROPERTY_PREFIXES) or not isinstance(prop, dict)
                or prop.get("name") != name or type(prop.get("present")) is not bool
                or not isinstance(prop.get("value"), str) or "\x00" in prop["value"]):
            raise ProbeError("invalid saved property")
        parsed[name] = PropertyState(name, prop["present"], prop["value"])
    state = ProbeState(*(data[key] for key in required[:4]), parsed, data["captured_at"])
    require_restorable_target_properties(state)
    return state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reversible Apple TV AVC experiment; use a fresh evidence directory")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("baseline", "probe", "observe", "restore"):
        command = commands.add_parser(action)
        command.add_argument("--serial", required=True)
        if action == "restore":
            command.add_argument("--state", required=True, type=Path)
        else:
            command.add_argument("--output", required=True, type=Path)
        if action in ("probe", "observe"):
            command.add_argument("--duration-seconds", type=int, default=600 if action == "probe" else 90)
    args = parser.parse_args(argv)
    try:
        if hasattr(args, "duration_seconds") and args.duration_seconds <= 0:
            raise ProbeError("duration must be positive")
        client = AdbClient(args.serial, runner=subprocess.run)
        if args.action == "restore":
            state = load_state(args.state)
            try:
                restore_probe_state(client, state)
            except RestoreError as exc:
                write_json(args.state.parent, "RESTORE_FAILED.json", {"expected": exc.expected, "observed": exc.observed, "error": str(exc)})
                raise
        elif args.action == "baseline":
            capture_baseline(client, args.output)
        elif args.action == "observe":
            state = capture_baseline(client, args.output)
            try:
                observation, samples = run_playback_observation(client, args.output, args.duration_seconds, read_only=True)
                write_result(args.output, state, observation, samples, duration_seconds=args.duration_seconds, mode="observe")
            except (Exception, KeyboardInterrupt) as exc:
                write_result(args.output, state, PlaybackObservation(ProbeOutcome.HARNESS_ERROR, None, 0, 0, f"{type(exc).__name__}: {exc}"), duration_seconds=args.duration_seconds, mode="observe")
                raise
        else:
            run_probe(client, args.output, args.duration_seconds)
        return 0
    except (ProbeError, OSError, ValueError, KeyboardInterrupt) as exc:
        print(redact_diagnostic_text(f"{type(exc).__name__}: {exc}"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
