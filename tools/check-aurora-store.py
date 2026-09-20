#!/usr/bin/env python3
"""Verify the provenance and Android identity of the pinned Aurora Store APK."""

import argparse
import dataclasses
import hashlib
import json
import pathlib
import re
import stat
import subprocess
import sys
import urllib.parse


APPROVED_FILENAME = "AuroraStore-preload-4.8.4.apk"
APPROVED_BINARY_URL = (
    "https://auroraoss.com/downloads/AuroraStore/Release/preload/"
    "AuroraStore-preload-4.8.4.apk"
)
DIGEST_FIELDS = ("sha256", "signer_sha256", "license_sha256")


class LockError(ValueError):
    """The local artifact lock does not meet its schema and provenance contract."""


class VerificationError(ValueError):
    """The downloaded APK does not meet the pinned artifact contract."""


@dataclasses.dataclass(frozen=True)
class AuroraLock:
    package_name: str
    version_name: str
    version_code: int
    filename: str
    url: str
    size: int
    sha256: str
    signer_sha256: str
    source_commit: str
    source_url: str
    license: str
    license_url: str
    license_sha256: str

    @classmethod
    def from_path(cls, path: pathlib.Path) -> "AuroraLock":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LockError("lock file could not be read as JSON") from error
        if not isinstance(data, dict):
            raise LockError("lock must be a JSON object")

        required = {field.name for field in dataclasses.fields(cls)}
        if set(data) != required:
            raise LockError("lock fields must exactly match the artifact schema")
        for field in required - {"version_code", "size"}:
            if not isinstance(data[field], str) or not data[field]:
                raise LockError(f"lock field {field} must be a non-empty string")
        for field in ("version_code", "size"):
            if isinstance(data[field], bool) or not isinstance(data[field], int) or data[field] <= 0:
                raise LockError(f"lock field {field} must be a positive integer")
        for field in DIGEST_FIELDS:
            if not re.fullmatch(r"[0-9a-f]{64}", data[field]):
                raise LockError(f"lock field {field} must be a lowercase SHA-256 digest")
        if data["filename"] != APPROVED_FILENAME:
            raise LockError("lock filename is not the approved Aurora Store preload")
        if data["url"] != APPROVED_BINARY_URL:
            raise LockError("lock binary URL is not the approved versioned Aurora Store URL")

        binary_url = urllib.parse.urlparse(data["url"])
        if (
            binary_url.scheme != "https"
            or binary_url.netloc != "auroraoss.com"
            or binary_url.path != urllib.parse.urlparse(APPROVED_BINARY_URL).path
            or binary_url.params
            or binary_url.query
            or binary_url.fragment
        ):
            raise LockError("lock binary URL must be HTTPS, pinned, and query-free")
        if not re.fullmatch(r"[0-9a-f]{40}", data["source_commit"]):
            raise LockError("lock source_commit must be a lowercase Git commit")
        for field in ("source_url", "license_url"):
            parsed = urllib.parse.urlparse(data[field])
            if (
                parsed.scheme != "https"
                or parsed.netloc != "gitlab.com"
                or not parsed.path.startswith("/AuroraOSS/AuroraStore/")
                or data["source_commit"] not in parsed.path
                or parsed.params
                or parsed.query
                or parsed.fragment
            ):
                raise LockError(f"lock field {field} must pin AuroraOSS/AuroraStore source")
        return cls(**data)


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as apk:
        for block in iter(lambda: apk.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_tool(runner, args: list[str], label: str) -> str:
    try:
        completed = runner(args, text=True, capture_output=True, check=False)
    except (FileNotFoundError, OSError) as error:
        raise VerificationError(f"{label} could not be executed") from error
    if completed.returncode != 0:
        raise VerificationError(f"{label} returned a nonzero exit status")
    if not isinstance(completed.stdout, str):
        raise VerificationError(f"{label} returned malformed output")
    return completed.stdout


def _normalise_digest(value: str) -> str:
    return re.sub(r"[:\s]", "", value)


def _verify_signature(lock: AuroraLock, apk: pathlib.Path, apksigner: pathlib.Path, runner) -> None:
    output = _run_tool(runner, [str(apksigner), "verify", "--verbose", "--print-certs", str(apk)], "apksigner")
    if not re.search(r"^Verified using .+?:\s*true\s*$", output, re.MULTILINE):
        raise VerificationError("apksigner did not report a verified signature scheme")
    signer_lines = re.findall(r"^Signer #\d+ certificate SHA-256 digest:\s*(.+?)\s*$", output, re.MULTILINE)
    if len(signer_lines) != 1:
        raise VerificationError("apksigner must report exactly one signer certificate digest")
    if _normalise_digest(signer_lines[0]) != lock.signer_sha256:
        raise VerificationError("APK signer certificate digest does not match the lock")


def _verify_badging(lock: AuroraLock, apk: pathlib.Path, aapt2: pathlib.Path, runner) -> None:
    output = _run_tool(runner, [str(aapt2), "dump", "badging", str(apk)], "aapt2")
    package_match = re.search(r"^package:\s+name='([^']+)'\s+versionCode='([^']+)'\s+versionName='([^']+)'", output, re.MULTILINE)
    if package_match is None:
        raise VerificationError("aapt2 did not report complete package identity")
    if package_match.groups() != (lock.package_name, str(lock.version_code), lock.version_name):
        raise VerificationError("APK package identity does not match the lock")
    native_match = re.search(r"^native-code:\s*(.*)$", output, re.MULTILINE)
    if native_match is None or "arm64-v8a" not in re.findall(r"'([^']+)'", native_match.group(1)):
        raise VerificationError("APK does not report arm64-v8a native code")


def verify_apk(
    lock: AuroraLock,
    apk: pathlib.Path,
    apksigner: pathlib.Path,
    aapt2: pathlib.Path,
    runner=subprocess.run,
    logical_filename: str | None = None,
) -> None:
    """Raise VerificationError unless APK, tools, and metadata satisfy ``lock``."""
    expected_filename = apk.name if logical_filename is None else logical_filename
    if expected_filename != lock.filename:
        raise VerificationError("APK filename does not match the lock")
    try:
        apk_status = apk.stat()
    except OSError as error:
        raise VerificationError("APK is not a readable regular file") from error
    if not stat.S_ISREG(apk_status.st_mode):
        raise VerificationError("APK is not a regular file")
    if apk_status.st_size != lock.size:
        raise VerificationError("APK size does not match the lock")
    try:
        digest = _sha256(apk)
    except OSError as error:
        raise VerificationError("APK could not be read") from error
    if digest != lock.sha256:
        raise VerificationError("APK SHA-256 does not match the lock")
    _verify_signature(lock, apk, apksigner, runner)
    _verify_badging(lock, apk, aapt2, runner)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=pathlib.Path, required=True)
    parser.add_argument("--apk", type=pathlib.Path, required=True)
    parser.add_argument("--apksigner", type=pathlib.Path, required=True)
    parser.add_argument("--aapt2", type=pathlib.Path, required=True)
    parser.add_argument("--logical-filename")
    args = parser.parse_args(argv)
    try:
        lock = AuroraLock.from_path(args.lock)
        verify_apk(lock, args.apk, args.apksigner, args.aapt2, logical_filename=args.logical_filename)
    except (LockError, VerificationError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Aurora Store artifact verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
