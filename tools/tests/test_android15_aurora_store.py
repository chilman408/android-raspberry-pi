#!/usr/bin/env python3

import copy
import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "tools" / "check-aurora-store.py"
REPOSITORY_LOCK = (
    REPO_ROOT
    / "aosptree"
    / "vendor"
    / "devices-community"
    / "gd_rpi4"
    / "compatibility-store"
    / "aurora-store.lock.json"
)
EXPECTED_LOCK = {
    "package_name": "com.aurora.store",
    "version_name": "4.8.4-preload",
    "version_code": 76,
    "filename": "AuroraStore-preload-4.8.4.apk",
    "url": "https://auroraoss.com/downloads/AuroraStore/Release/preload/AuroraStore-preload-4.8.4.apk",
    "size": 9362717,
    "sha256": "d2c0cd2ecaf3211ac31328e20649c654666e391afc9335478d8360a633bb159b",
    "signer_sha256": "4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f",
    "source_commit": "ad3d349edce82e119ba239f3a07b3afe028abd19",
    "source_url": "https://gitlab.com/AuroraOSS/AuroraStore/-/tree/ad3d349edce82e119ba239f3a07b3afe028abd19",
    "license": "GPL-3.0-or-later",
    "license_url": "https://gitlab.com/AuroraOSS/AuroraStore/-/raw/ad3d349edce82e119ba239f3a07b3afe028abd19/LICENSE",
    "license_sha256": "e16780aff588719628230b714c3beb9735d018df2d69f24f1927114cec180c38",
}


def load_checker():
    spec = importlib.util.spec_from_file_location("check_aurora_store", CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load Aurora Store verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


aurora_store = load_checker()


class Android15AuroraStoreTest(unittest.TestCase):
    """Contract tests for the pinned Aurora Store preload artifact."""

    fixture_apk_bytes = b"Aurora Store verifier fixture APK\n"

    def write_lock(self, directory: pathlib.Path, **overrides) -> pathlib.Path:
        values = copy.deepcopy(EXPECTED_LOCK)
        values.update(overrides)
        path = directory / "aurora-store.lock.json"
        path.write_text(json.dumps(values), encoding="utf-8")
        return path

    def fixture_lock_and_apk(
        self, directory: pathlib.Path
    ) -> tuple[object, pathlib.Path]:
        apk = directory / EXPECTED_LOCK["filename"]
        apk.write_bytes(self.fixture_apk_bytes)
        lock_path = self.write_lock(
            directory,
            size=len(self.fixture_apk_bytes),
            sha256=hashlib.sha256(self.fixture_apk_bytes).hexdigest(),
        )
        return aurora_store.AuroraLock.from_path(lock_path), apk

    def successful_runner(self, apk: pathlib.Path):
        signature_command = [str(pathlib.Path("apksigner")), "verify", "--verbose", "--print-certs", str(apk)]
        badging_command = [str(pathlib.Path("aapt2")), "dump", "badging", str(apk)]

        def runner(args, **kwargs):
            self.assertEqual(kwargs, {"text": True, "capture_output": True, "check": False})
            if args == signature_command:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=(
                        "Verified using v1 scheme (JAR signing): true\n"
                        "Verified using v2 scheme (APK Signature Scheme v2): true\n"
                        "Signer #1 certificate SHA-256 digest: "
                        "4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f\n"
                    ),
                    stderr="",
                )
            if args == badging_command:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=(
                        "package: name='com.aurora.store' versionCode='76' "
                        "versionName='4.8.4-preload' compileSdkVersion='37'\n"
                        "native-code: 'arm64-v8a' 'armeabi-v7a' 'x86' 'x86_64'\n"
                    ),
                    stderr="",
                )
            self.fail(f"unexpected verifier command: {args}")

        return runner

    def cli_args(self, lock: pathlib.Path, apk: pathlib.Path) -> list[str]:
        return [
            "--lock", str(lock), "--apk", str(apk), "--apksigner", "apksigner", "--aapt2", "aapt2",
        ]

    def test_repository_lock_has_exact_approved_release(self):
        """Changing any approved provenance field must reject the repository lock."""
        self.assertEqual(json.loads(REPOSITORY_LOCK.read_text(encoding="utf-8")), EXPECTED_LOCK)
        lock = aurora_store.AuroraLock.from_path(REPOSITORY_LOCK)
        self.assertEqual(lock.filename, "AuroraStore-preload-4.8.4.apk")

    def test_lock_rejects_missing_unknown_or_wrong_typed_fields(self):
        """Schema drift must not silently weaken the artifact lock."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            for name, value in (("missing", None), ("unknown", None), ("type", "76"), ("boolean", True)):
                with self.subTest(case=name):
                    values = copy.deepcopy(EXPECTED_LOCK)
                    if name == "missing":
                        del values["license"]
                    elif name == "unknown":
                        values["unexpected"] = "field"
                    else:
                        values["version_code"] = value
                    path = directory / f"{name}.json"
                    path.write_text(json.dumps(values), encoding="utf-8")
                    with self.assertRaises(aurora_store.LockError):
                        aurora_store.AuroraLock.from_path(path)

    def test_lock_rejects_http_floating_or_wrong_host_url(self):
        """Transport, host, and pinned binary path mutations must be rejected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            bad_urls = (
                "http://auroraoss.com/downloads/AuroraStore/Release/preload/AuroraStore-preload-4.8.4.apk",
                "https://auroraoss.com/downloads/AuroraStore/Release/preload/latest.apk",
                "https://example.com/downloads/AuroraStore/Release/preload/AuroraStore-preload-4.8.4.apk",
            )
            for index, url in enumerate(bad_urls):
                with self.subTest(url=url):
                    with self.assertRaises(aurora_store.LockError):
                        aurora_store.AuroraLock.from_path(self.write_lock(directory, url=url))

    def test_lock_rejects_non_hex_or_uppercase_digests(self):
        """Digest normalization must not accept ambiguous provenance values."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            for field, value in (
                ("sha256", "g" * 64),
                ("signer_sha256", EXPECTED_LOCK["signer_sha256"].upper()),
                ("license_sha256", "f" * 63),
            ):
                with self.subTest(field=field):
                    with self.assertRaises(aurora_store.LockError):
                        aurora_store.AuroraLock.from_path(self.write_lock(directory, **{field: value}))

    def test_cli_invalid_utf8_lock_returns_one_contract_error(self):
        """Invalid lock bytes must produce the CLI's normal single-error contract."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            lock = directory / "invalid.lock.json"
            lock.write_bytes(b"\xff")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = aurora_store.main(self.cli_args(lock, directory / "missing.apk"))
            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertRegex(stderr.getvalue(), r"^ERROR: [^\n]+\n$")

    def test_cli_contract_failure_returns_one_error_line(self):
        """Schema violations must use exit 1 and one concise contract-error line."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = aurora_store.main(self.cli_args(self.write_lock(directory, version_code=0), directory / "missing.apk"))
            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertRegex(stderr.getvalue(), r"^ERROR: [^\n]+\n$")

    def test_cli_success_prints_message_and_returns_zero(self):
        """A successful verification must expose the documented CLI success result."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(aurora_store, "verify_apk"), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = aurora_store.main(self.cli_args(REPOSITORY_LOCK, pathlib.Path("fixture.apk")))
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "Aurora Store artifact verification passed.\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_cli_argparse_failure_exits_two(self):
        """Missing required invocation arguments must retain argparse's exit code."""
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            aurora_store.main([])
        self.assertEqual(error.exception.code, 2)

    def test_verify_requires_regular_file_before_filename_match(self):
        """A missing APK must fail the regular-file check before filename comparison."""
        with tempfile.TemporaryDirectory() as temp_dir:
            lock, _ = self.fixture_lock_and_apk(pathlib.Path(temp_dir))
            with self.assertRaisesRegex(aurora_store.VerificationError, "regular file"):
                aurora_store.verify_apk(
                    lock,
                    pathlib.Path(temp_dir) / "wrong-name.apk",
                    pathlib.Path("apksigner"),
                    pathlib.Path("aapt2"),
                )

    def test_verify_rejects_size_and_sha256_mismatch_before_tools_run(self):
        """A damaged download must fail before certificate or manifest inspection."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            lock, apk = self.fixture_lock_and_apk(directory)
            calls = []

            def runner(args, **kwargs):
                calls.append(args)
                return self.successful_runner(apk)(args, **kwargs)

            wrong_size = dataclasses.replace(lock, size=lock.size + 1)
            with self.assertRaises(aurora_store.VerificationError):
                aurora_store.verify_apk(wrong_size, apk, pathlib.Path("apksigner"), pathlib.Path("aapt2"), runner)
            wrong_digest = dataclasses.replace(lock, sha256="0" * 64)
            with self.assertRaises(aurora_store.VerificationError):
                aurora_store.verify_apk(wrong_digest, apk, pathlib.Path("apksigner"), pathlib.Path("aapt2"), runner)
            self.assertEqual(calls, [])

    def test_verify_requires_apksigner_verified_output_and_exact_signer(self):
        """Unsigned or differently signed APKs must not pass the pinned signer check."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            lock, apk = self.fixture_lock_and_apk(directory)

            def runner(args, **kwargs):
                self.assertEqual(kwargs, {"text": True, "capture_output": True, "check": False})
                if args == ["apksigner", "verify", "--verbose", "--print-certs", str(apk)]:
                    return subprocess.CompletedProcess(
                        args,
                        0,
                        stdout="Verified using v2 scheme (APK Signature Scheme v2): false\n",
                        stderr="",
                    )
                self.fail("aapt2 must not run after a signer failure")

            with self.assertRaises(aurora_store.VerificationError):
                aurora_store.verify_apk(lock, apk, pathlib.Path("apksigner"), pathlib.Path("aapt2"), runner)

            def wrong_signer_runner(args, **kwargs):
                self.assertEqual(kwargs, {"text": True, "capture_output": True, "check": False})
                if args == ["apksigner", "verify", "--verbose", "--print-certs", str(apk)]:
                    return subprocess.CompletedProcess(
                        args,
                        0,
                        stdout=(
                            "Verified using v2 scheme (APK Signature Scheme v2): true\n"
                            "Signer #1 certificate SHA-256 digest: " + "0" * 64 + "\n"
                        ),
                        stderr="",
                    )
                self.fail("aapt2 must not run after a signer mismatch")

            with self.assertRaises(aurora_store.VerificationError):
                aurora_store.verify_apk(lock, apk, pathlib.Path("apksigner"), pathlib.Path("aapt2"), wrong_signer_runner)

            def duplicate_signer_runner(args, **kwargs):
                self.assertEqual(kwargs, {"text": True, "capture_output": True, "check": False})
                if args == ["apksigner", "verify", "--verbose", "--print-certs", str(apk)]:
                    return subprocess.CompletedProcess(
                        args,
                        0,
                        stdout=(
                            "Verified using v2 scheme (APK Signature Scheme v2): true\n"
                            "Signer #1 certificate SHA-256 digest: "
                            "4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f\n"
                            "Signer #2 certificate SHA-256 digest: "
                            "4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f\n"
                        ),
                        stderr="",
                    )
                self.fail("aapt2 must not run after duplicate signer output")

            with self.assertRaises(aurora_store.VerificationError):
                aurora_store.verify_apk(lock, apk, pathlib.Path("apksigner"), pathlib.Path("aapt2"), duplicate_signer_runner)

    def test_verify_requires_package_version_code_and_version_name(self):
        """Package identity mutations must fail even with a valid signer."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            lock, apk = self.fixture_lock_and_apk(directory)

            def runner(args, **kwargs):
                self.assertEqual(kwargs, {"text": True, "capture_output": True, "check": False})
                if args == ["apksigner", "verify", "--verbose", "--print-certs", str(apk)]:
                    return self.successful_runner(apk)(args, **kwargs)
                self.assertEqual(args, ["aapt2", "dump", "badging", str(apk)])
                return subprocess.CompletedProcess(args, 0, stdout=runner.badging, stderr="")

            for name, badging in (
                ("package", "package: name='com.other.store' versionCode='76' versionName='4.8.4-preload' compileSdkVersion='37'\nnative-code: 'arm64-v8a'\n"),
                ("version code", "package: name='com.aurora.store' versionCode='77' versionName='4.8.4-preload' compileSdkVersion='37'\nnative-code: 'arm64-v8a'\n"),
                ("version name", "package: name='com.aurora.store' versionCode='76' versionName='other' compileSdkVersion='37'\nnative-code: 'arm64-v8a'\n"),
            ):
                with self.subTest(field=name):
                    runner.badging = badging
                    with self.assertRaises(aurora_store.VerificationError):
                        aurora_store.verify_apk(lock, apk, pathlib.Path("apksigner"), pathlib.Path("aapt2"), runner)

    def test_verify_requires_arm64_v8a_native_code(self):
        """An APK without Raspberry Pi's native ABI must be rejected."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            lock, apk = self.fixture_lock_and_apk(directory)

            def runner(args, **kwargs):
                self.assertEqual(kwargs, {"text": True, "capture_output": True, "check": False})
                if args == ["apksigner", "verify", "--verbose", "--print-certs", str(apk)]:
                    return self.successful_runner(apk)(args, **kwargs)
                self.assertEqual(args, ["aapt2", "dump", "badging", str(apk)])
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=(
                        "package: name='com.aurora.store' versionCode='76' "
                        "versionName='4.8.4-preload' compileSdkVersion='37'\n"
                        "native-code: 'armeabi-v7a' 'x86'\n"
                    ),
                    stderr="",
                )

            with self.assertRaises(aurora_store.VerificationError):
                aurora_store.verify_apk(lock, apk, pathlib.Path("apksigner"), pathlib.Path("aapt2"), runner)

    def test_matching_fixture_and_tool_output_pass(self):
        """The fully matching pinned artifact contract must verify successfully."""
        with tempfile.TemporaryDirectory() as temp_dir:
            lock, apk = self.fixture_lock_and_apk(pathlib.Path(temp_dir))
            aurora_store.verify_apk(
                lock,
                apk,
                pathlib.Path("apksigner"),
                pathlib.Path("aapt2"),
                self.successful_runner(apk),
            )


if __name__ == "__main__":
    unittest.main()
