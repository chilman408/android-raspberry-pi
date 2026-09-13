#!/usr/bin/env python3
"""Pure evidence parsing for the Apple TV codec playback probe."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import re
from typing import Sequence


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
            )
        )
    return events


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
    if software_duration >= required_seconds and software_sampled:
        return PlaybackObservation(
            ProbeOutcome.PLAYED_600_SECONDS,
            software_codec,
            software_duration,
            0,
            "one uninterrupted software AVC session met the duration requirement",
        )
    return PlaybackObservation(
        ProbeOutcome.UNUSABLE_LOAD,
        software_codec,
        software_duration,
        0,
        "software AVC did not produce a long enough continuous session",
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


def main() -> int:
    """Task 1 deliberately exposes no orchestration commands."""
    print("usage: apple_tv_codec_probe.py <command>")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
