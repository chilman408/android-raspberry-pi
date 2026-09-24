#!/usr/bin/env python3

import ast
import copy
import contextlib
import dataclasses
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "tools" / "check-aurora-store.py"
MATERIALIZER = REPO_ROOT / "tools" / "prepare-aurora-store.sh"
UNFOLD = REPO_ROOT / "unfold_aosp.sh"
GIT_BASH = pathlib.Path(
    r"C:\Program Files\Git\bin\bash.exe"
    if os.name == "nt"
    else shutil.which("bash") or "/bin/bash"
)
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

    def test_verify_can_invoke_explicit_aosp_apksigner_jar(self):
        """AOSP's broken SDK launcher must not prevent direct jar verification."""
        with tempfile.TemporaryDirectory() as temp_dir:
            lock, apk = self.fixture_lock_and_apk(pathlib.Path(temp_dir))
            java = pathlib.Path("aosp-java")
            apksigner_jar = pathlib.Path("aosp-apksigner.jar")
            signature_command = [
                str(java), "-jar", str(apksigner_jar),
                "verify", "--verbose", "--print-certs", str(apk),
            ]

            def runner(args, **kwargs):
                if args == signature_command:
                    return self.successful_runner(apk)(
                        ["apksigner", *args[3:]], **kwargs
                    )
                return self.successful_runner(apk)(args, **kwargs)

            aurora_store.verify_apk(
                lock,
                apk,
                pathlib.Path("broken-aosp-launcher"),
                pathlib.Path("aapt2"),
                runner,
                java=java,
                apksigner_jar=apksigner_jar,
            )


class MaterializerTests(unittest.TestCase):
    """Behavior tests for downloading and installing the pinned APK safely."""

    fixture_apk_bytes = b"Aurora Store materializer fixture APK\n"
    bad_apk_bytes = b"rejected Aurora Store materializer fixture\n"

    @staticmethod
    def bash_path(path: pathlib.Path) -> str:
        path = path.resolve()
        if os.name != "nt":
            return path.as_posix()
        drive = path.drive.rstrip(":").lower()
        return f"/{drive}{path.as_posix()[2:]}"

    @staticmethod
    def write_executable(path: pathlib.Path, contents: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(contents).lstrip(), encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def create_bash_symlink(self, target: pathlib.Path, link: pathlib.Path) -> None:
        environment = os.environ.copy()
        if os.name == "nt":
            environment["MSYS"] = "winsymlinks:sys"
        result = subprocess.run(
            [
                str(GIT_BASH),
                "-c",
                'ln -s -- "$1" "$2"',
                "symlink",
                self.bash_path(target),
                self.bash_path(link),
            ],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def write_lock(self, directory: pathlib.Path, apk_bytes: bytes) -> pathlib.Path:
        values = copy.deepcopy(EXPECTED_LOCK)
        values["size"] = len(apk_bytes)
        values["sha256"] = hashlib.sha256(apk_bytes).hexdigest()
        lock = directory / "aurora-store.lock.json"
        lock.write_text(json.dumps(values), encoding="utf-8")
        return lock

    def make_fixture_environment(
        self, directory: pathlib.Path, download_bytes: bytes | None = None
    ) -> tuple[pathlib.Path, pathlib.Path, dict[str, str]]:
        fixture = directory / "fixture.apk"
        fixture.write_bytes(self.fixture_apk_bytes if download_bytes is None else download_bytes)
        aosp_root = directory / "aosptree"
        destination = aosp_root / "vendor" / "devices-community" / "gd_rpi4" / "compatibility-store"
        destination.mkdir(parents=True)
        tool_root = aosp_root / "prebuilts" / "sdk" / "tools" / "linux" / "bin"
        apksigner_jar = aosp_root / "prebuilts" / "sdk" / "tools" / "linux" / "lib" / "apksigner.jar"
        jdk_bin = aosp_root / "prebuilts" / "jdk" / "jdk17" / "linux-x86" / "bin"
        fake_bin = directory / "fake-bin"
        fake_bin.mkdir()

        if os.name == "nt":
            python3_contents = f"""
                #!/bin/bash
                arguments=()
                for argument in "$@"; do
                    case "$argument" in
                        */apksigner|*/aapt2|*/java) argument="${{argument}}.cmd" ;;
                    esac
                    arguments+=("$argument")
                done
                exec {self.bash_path(pathlib.Path(sys.executable))!r} "${{arguments[@]}}"
            """
            apksigner_name = "apksigner.cmd"
            apksigner_contents = """
                @echo off
                if "%REQUIRE_AOSP_JAVA%"=="1" (
                    if exist "%JAVA_LOG%" del /q "%JAVA_LOG%"
                    call java --aurora-jdk-probe
                    if errorlevel 1 exit /b 94
                    findstr /x "aosp-jdk17" "%JAVA_LOG%" >nul || exit /b 94
                )
                if "%FAKE_APKSIGNER_LAUNCHER_FAIL%"=="1" exit /b 95
                echo apksigner %~4>>"%INSPECTION_LOG%"
                if "%REQUIRE_FINAL_ABSENT%"=="1" if exist "%EXPECTED_FINAL_APK%" exit /b 92
                if "%REJECT_FINAL_APK%"=="1" if "%~4"=="%EXPECTED_FINAL_APK%" exit /b 91
                if "%FAKE_APKSIGNER_FAIL%"=="1" exit /b 93
                echo Verified using v1 scheme (JAR signing): true
                echo Verified using v2 scheme (APK Signature Scheme v2): true
                echo Signer #1 certificate SHA-256 digest: 4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f
            """
            aapt2_name = "aapt2.cmd"
            aapt2_contents = """
                @echo off
                echo aapt2 %~3>>"%INSPECTION_LOG%"
                if "%REQUIRE_FINAL_ABSENT%"=="1" if exist "%EXPECTED_FINAL_APK%" exit /b 92
                echo package: name='com.aurora.store' versionCode='76' versionName='4.8.4-preload' compileSdkVersion='37'
                echo native-code: 'arm64-v8a' 'armeabi-v7a' 'x86' 'x86_64'
            """
        else:
            python3_contents = f"""
                #!/bin/sh
                exec {self.bash_path(pathlib.Path(sys.executable))!r} "$@"
            """
            apksigner_name = "apksigner"
            apksigner_contents = """
                #!/bin/sh
                if [ "${REQUIRE_AOSP_JAVA:-0}" = 1 ]; then
                    rm -f -- "$JAVA_LOG"
                    java --aurora-jdk-probe || exit 94
                    grep -qx 'aosp-jdk17' "$JAVA_LOG" || exit 94
                fi
                [ "${FAKE_APKSIGNER_LAUNCHER_FAIL:-0}" != 1 ] || exit 95
                printf 'apksigner %s\n' "$4" >> "$INSPECTION_LOG"
                if [ "${REQUIRE_FINAL_ABSENT:-0}" = 1 ] && [ -e "$EXPECTED_FINAL_APK" ]; then
                    exit 92
                fi
                if [ "${REJECT_FINAL_APK:-0}" = 1 ] && [ "$4" = "$EXPECTED_FINAL_APK" ]; then
                    exit 91
                fi
                [ "${FAKE_APKSIGNER_FAIL:-0}" != 1 ] || exit 93
                printf '%s\n' \
                  'Verified using v1 scheme (JAR signing): true' \
                  'Verified using v2 scheme (APK Signature Scheme v2): true' \
                  'Signer #1 certificate SHA-256 digest: 4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f'
            """
            aapt2_name = "aapt2"
            aapt2_contents = """
                #!/bin/sh
                printf 'aapt2 %s\n' "$3" >> "$INSPECTION_LOG"
                if [ "${REQUIRE_FINAL_ABSENT:-0}" = 1 ] && [ -e "$EXPECTED_FINAL_APK" ]; then
                    exit 92
                fi
                printf '%s\n' \
                  "package: name='com.aurora.store' versionCode='76' versionName='4.8.4-preload' compileSdkVersion='37'" \
                  "native-code: 'arm64-v8a' 'armeabi-v7a' 'x86' 'x86_64'"
            """
        self.write_executable(fake_bin / "python3", python3_contents)
        self.write_executable(
            fake_bin / "curl",
            """
            #!/bin/sh
            output=
            while [ "$#" -gt 0 ]; do
                case "$1" in
                    --output) output="$2"; shift 2 ;;
                    *) shift ;;
                esac
            done
            [ "${FAKE_CURL_FAIL:-0}" != 1 ] || exit 22
            cp -- "$FAKE_APK_SOURCE" "$output"
            """,
        )
        self.write_executable(
            tool_root / apksigner_name,
            apksigner_contents,
        )
        self.write_executable(
            tool_root / aapt2_name,
            aapt2_contents,
        )
        apksigner_jar.parent.mkdir(parents=True, exist_ok=True)
        apksigner_jar.write_bytes(b"fixture apksigner jar\n")
        self.write_executable(
            jdk_bin / "java",
            """
            #!/bin/sh
            if [ -n "${JAVA_LOG:-}" ]; then
                printf 'aosp-jdk17\n' > "$JAVA_LOG"
            fi
            if [ "${1:-}" = -jar ]; then
                [ "$2" = "$EXPECTED_APKSIGNER_JAR" ] || exit 96
                printf 'apksigner %s\n' "$6" >> "$INSPECTION_LOG"
                if [ "${REQUIRE_FINAL_ABSENT:-0}" = 1 ] && [ -e "$EXPECTED_FINAL_APK" ]; then
                    exit 92
                fi
                if [ "${REJECT_FINAL_APK:-0}" = 1 ] && [ "$6" = "$EXPECTED_FINAL_APK" ]; then
                    exit 91
                fi
                [ "${FAKE_APKSIGNER_FAIL:-0}" != 1 ] || exit 93
                printf '%s\n' \
                  'Verified using v1 scheme (JAR signing): true' \
                  'Verified using v2 scheme (APK Signature Scheme v2): true' \
                  'Signer #1 certificate SHA-256 digest: 4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f'
            fi
            """,
        )
        if os.name == "nt":
            self.write_executable(
                jdk_bin / "java.cmd",
                """
                @echo off
                if not "%JAVA_LOG%"=="" echo aosp-jdk17>"%JAVA_LOG%"
                if "%~1"=="-jar" (
                    for %%J in ("%EXPECTED_APKSIGNER_JAR%") do if /i not "%~f2"=="%%~fJ" exit /b 96
                    echo apksigner %~6>>"%INSPECTION_LOG%"
                    if "%REQUIRE_FINAL_ABSENT%"=="1" if exist "%EXPECTED_FINAL_APK%" exit /b 92
                    if "%REJECT_FINAL_APK%"=="1" if "%~6"=="%EXPECTED_FINAL_APK%" exit /b 91
                    if "%FAKE_APKSIGNER_FAIL%"=="1" exit /b 93
                    echo Verified using v1 scheme ^(JAR signing^): true
                    echo Verified using v2 scheme ^(APK Signature Scheme v2^): true
                    echo Signer #1 certificate SHA-256 digest: 4c626157ad02bda3401a7263555f68a79663fc3e13a4d4369a12570941aa280f
                )
                """,
            )

        environment = os.environ.copy()
        environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
        environment["FAKE_BIN_BASH"] = self.bash_path(fake_bin)
        environment["FAKE_APK_SOURCE"] = self.bash_path(fixture)
        environment["EXPECTED_FINAL_APK"] = str(destination / EXPECTED_LOCK["filename"])
        environment["EXPECTED_APKSIGNER_JAR"] = (
            str(apksigner_jar.resolve()) if os.name == "nt" else self.bash_path(apksigner_jar)
        )
        environment["INSPECTION_LOG"] = str(directory / "inspection.log")
        return destination, aosp_root, environment

    def run_materializer(
        self,
        lock: pathlib.Path,
        destination: pathlib.Path,
        aosp_root: pathlib.Path,
        environment: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        if not MATERIALIZER.is_file():
            self.fail(f"materializer script is missing: {MATERIALIZER}")
        return subprocess.run(
            [
                str(GIT_BASH),
                "-c",
                'export PATH="$FAKE_BIN_BASH:$PATH"; exec bash "$@"',
                "materializer",
                self.bash_path(MATERIALIZER),
                "--lock",
                self.bash_path(lock),
                "--destination",
                self.bash_path(destination),
                "--aosp-root",
                self.bash_path(aosp_root),
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_materializer_downloads_verifies_and_atomically_installs(self):
        """Installing before complete verification must not expose unverified bytes."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            inspection_log = directory / "inspection.log"
            lock = self.write_lock(directory, self.fixture_apk_bytes)
            destination, aosp_root, environment = self.make_fixture_environment(directory)
            environment["REQUIRE_FINAL_ABSENT"] = "1"
            environment["FAKE_APKSIGNER_FAIL"] = "1"

            rejected = self.run_materializer(lock, destination, aosp_root, environment)

            final_apk = destination / EXPECTED_LOCK["filename"]
            self.assertNotEqual(rejected.returncode, 0)
            self.assertFalse(final_apk.exists())
            self.assertEqual(list(destination.glob(".AuroraStore-preload-4.8.4.apk.tmp.*")), [])
            rejected_calls = inspection_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rejected_calls), 1)
            self.assertRegex(rejected_calls[0], r"^apksigner .+\.tmp\.[^\\/]+$")

            del environment["FAKE_APKSIGNER_FAIL"]
            inspection_log.unlink()

            result = self.run_materializer(lock, destination, aosp_root, environment)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(final_apk.read_bytes(), self.fixture_apk_bytes)
            self.assertEqual(list(destination.glob(".AuroraStore-preload-4.8.4.apk.tmp.*")), [])
            accepted_calls = inspection_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(accepted_calls), 2)
            self.assertTrue(accepted_calls[0].startswith("apksigner "))
            self.assertTrue(accepted_calls[1].startswith("aapt2 "))

    def test_materializer_supplies_synced_aosp_jdk_to_apksigner(self):
        """Verification must not depend on Java being installed on the build host."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            lock = self.write_lock(directory, self.fixture_apk_bytes)
            destination, aosp_root, environment = self.make_fixture_environment(directory)
            environment["REQUIRE_AOSP_JAVA"] = "1"
            environment["JAVA_LOG"] = str(directory / "java.log")

            result = self.run_materializer(lock, destination, aosp_root, environment)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((directory / "java.log").read_text(encoding="utf-8").strip(), "aosp-jdk17")

    def test_materializer_bypasses_broken_aosp_apksigner_launcher(self):
        """The verifier must invoke the working AOSP jar instead of its broken launcher."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            lock = self.write_lock(directory, self.fixture_apk_bytes)
            destination, aosp_root, environment = self.make_fixture_environment(directory)
            environment["FAKE_APKSIGNER_LAUNCHER_FAIL"] = "1"

            result = self.run_materializer(lock, destination, aosp_root, environment)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((destination / EXPECTED_LOCK["filename"]).is_file())

    def test_materializer_removes_temporary_file_when_download_fails(self):
        """A failed transfer must not leave reusable partial download state."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            lock = self.write_lock(directory, self.fixture_apk_bytes)
            destination, aosp_root, environment = self.make_fixture_environment(directory)
            environment["FAKE_CURL_FAIL"] = "1"

            result = self.run_materializer(lock, destination, aosp_root, environment)

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((destination / EXPECTED_LOCK["filename"]).exists())
            self.assertEqual(list(destination.glob(".AuroraStore-preload-4.8.4.apk.tmp.*")), [])

    def test_materializer_does_not_replace_existing_verified_apk_on_bad_download(self):
        """A rejected replacement must leave the previously installed bytes intact."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            lock = self.write_lock(directory, self.fixture_apk_bytes)
            destination, aosp_root, environment = self.make_fixture_environment(
                directory, self.bad_apk_bytes
            )
            final_apk = destination / EXPECTED_LOCK["filename"]
            previous_bytes = b"previously verified Aurora Store APK\n"
            final_apk.write_bytes(previous_bytes)

            result = self.run_materializer(lock, destination, aosp_root, environment)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(final_apk.read_bytes(), previous_bytes)
            self.assertEqual(list(destination.glob(".AuroraStore-preload-4.8.4.apk.tmp.*")), [])

    def test_materializer_reverifies_matching_existing_apk_without_network(self):
        """A matching installed APK must be inspected again without downloading."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            inspection_log = directory / "inspection.log"
            lock = self.write_lock(directory, self.fixture_apk_bytes)
            destination, aosp_root, environment = self.make_fixture_environment(directory)
            final_apk = destination / EXPECTED_LOCK["filename"]
            final_apk.write_bytes(self.fixture_apk_bytes)
            environment["FAKE_CURL_FAIL"] = "1"
            environment["FAKE_APKSIGNER_FAIL"] = "1"

            result = self.run_materializer(lock, destination, aosp_root, environment)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(final_apk.read_bytes(), self.fixture_apk_bytes)
            inspection_calls = inspection_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(inspection_calls), 1)
            tool, inspected_apk = inspection_calls[0].split(" ", 1)
            self.assertEqual(tool, "apksigner")
            self.assertEqual(os.path.normcase(inspected_apk), os.path.normcase(str(final_apk)))

    def test_materializer_rejects_symlink_destination_or_final_apk(self):
        """Symlinked write targets must not redirect materialization outside its tree."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            lock = self.write_lock(directory, self.fixture_apk_bytes)
            destination, aosp_root, environment = self.make_fixture_environment(directory)
            real_destination = directory / "real-destination"
            real_destination.mkdir()
            destination.rmdir()
            self.create_bash_symlink(real_destination, destination)

            destination_result = self.run_materializer(lock, destination, aosp_root, environment)

            self.assertNotEqual(destination_result.returncode, 0)
            self.assertEqual(list(real_destination.iterdir()), [])

            destination.unlink()
            destination.mkdir()
            outside_apk = directory / "outside.apk"
            outside_apk.write_bytes(b"outside bytes\n")
            self.create_bash_symlink(outside_apk, destination / EXPECTED_LOCK["filename"])

            final_result = self.run_materializer(lock, destination, aosp_root, environment)

            self.assertNotEqual(final_result.returncode, 0)
            self.assertEqual(outside_apk.read_bytes(), b"outside bytes\n")

    def test_unfold_invokes_materializer_after_repo_sync_before_build_inputs_are_used(self):
        """Source preparation must materialize only after sync and GApps verification."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            event_log = directory / "events.log"
            fake_bin = directory / "fake-bin"
            (directory / "aosptree" / ".repo" / "manifests").mkdir(parents=True)
            (directory / "aosptree" / ".repo" / "manifests" / "default.xml").write_text("old\n")
            (directory / "aosptree" / "foo").mkdir()
            (directory / "aosptree" / "external" / "libcxx" / "include").mkdir(parents=True)
            (directory / "aosptree" / "external" / "libcxx" / "include" / "chrono").touch()
            (directory / "manifests").mkdir()
            for manifest in ("tesla-android.xml", "glodroid.xml", "default_aosp.xml"):
                (directory / "manifests" / manifest).write_text("<manifest/>\n")
            (directory / "patches-aosp" / "foo").mkdir(parents=True)
            (directory / "patches-aosp" / "foo" / "0001-test.patch").write_text("fixture\n")
            (directory / "tools").mkdir()

            self.write_executable(
                fake_bin / "repo",
                """
                #!/bin/sh
                printf 'repo %s\n' "$*" >> "$EVENT_LOG"
                """,
            )
            self.write_executable(
                fake_bin / "git",
                """
                #!/bin/sh
                printf 'git %s\n' "$*" >> "$EVENT_LOG"
                exit 0
                """,
            )
            self.write_executable(
                fake_bin / "python3",
                """
                #!/bin/sh
                printf 'gapps-verify %s\n' "$*" >> "$EVENT_LOG"
                """,
            )
            self.write_executable(
                directory / "tools" / "prepare-aurora-store.sh",
                """
                #!/bin/sh
                printf 'materializer %s\n' "$*" >> "$EVENT_LOG"
                """,
            )
            environment = os.environ.copy()
            environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
            environment["FAKE_BIN_BASH"] = self.bash_path(fake_bin)
            environment["EVENT_LOG"] = self.bash_path(event_log)

            result = subprocess.run(
                [
                    str(GIT_BASH),
                    "-c",
                    'export PATH="$FAKE_BIN_BASH:$PATH"; exec bash "$1"',
                    "unfold",
                    self.bash_path(UNFOLD),
                ],
                cwd=directory,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            events = event_log.read_text(encoding="utf-8").splitlines()
            sync_index = next(index for index, event in enumerate(events) if event.startswith("repo sync --no-clone"))
            gapps_index = next(index for index, event in enumerate(events) if event.startswith("gapps-verify "))
            materializer_index = next(index for index, event in enumerate(events) if event.startswith("materializer "))
            patch_index = next(index for index, event in enumerate(events) if event.startswith("git am "))
            self.assertLess(sync_index, gapps_index)
            self.assertLess(gapps_index, materializer_index)
            self.assertLess(materializer_index, patch_index)
            self.assertEqual(events[materializer_index], "materializer --aosp-root aosptree")


class ProductIntegrationTests(unittest.TestCase):
    """Shipping policy: preserve the upstream signer and limit product authority."""

    baseline = "android-platform-15.0.0_r3-tesla-2026.22.1-runtime-v4"

    def app_properties(self):
        path = REPOSITORY_LOCK.with_name("Android.bp")
        self.assertTrue(path.is_file(), "missing Aurora product module")
        source = path.read_text(encoding="utf-8")
        source = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/',
                        lambda match: match[0] if match[0].startswith('"') else "", source,
                        flags=re.DOTALL)
        module = re.fullmatch(r"\s*android_app_import\s*(\{.*\})\s*", source, re.DOTALL)
        self.assertIsNotNone(module, "expected one android_app_import module")
        properties = re.sub(r"\b([A-Za-z_]\w*)\s*:", r'"\1":', module[1])
        properties = re.sub(r",\s*([}\]])", r"\1", properties)

        def unique_properties(pairs):
            result = {}
            for key, value in pairs:
                self.assertNotIn(key, result, f"duplicate Soong property: {key}")
                result[key] = value
            return result

        return json.loads(properties, object_pairs_hook=unique_properties)

    @staticmethod
    def workflow_runs(relative_path):
        """Read inline and literal/folded run scalars without depending on PyYAML."""
        lines = (REPO_ROOT / relative_path).read_text(encoding="utf-8").splitlines()
        runs = []
        for index, line in enumerate(lines):
            match = re.match(r"^(\s*)run:\s*(.*?)\s*$", line)
            if not match:
                continue
            value = match[2]
            if value in ("|", ">", "|-", ">-"):
                block = []
                for following in lines[index + 1:]:
                    if following.strip() and len(following) - len(following.lstrip()) <= len(match[1]):
                        break
                    block.append(following.strip())
                value = "\n".join(block)
            runs.append(value)
        return runs

    def test_product_import_preserves_signed_apk_without_preoptimization(self):
        """Re-signing, changing the binary, or transforming its dex breaks the preload."""
        properties = self.app_properties()
        for key, expected in {
            "name": "AuroraStorePreload",
            "apk": "AuroraStore-preload-4.8.4.apk",
            "presigned": True,
            "preprocessed": True,
            "product_specific": True,
            "dex_preopt": {"enabled": False},
        }.items():
            with self.subTest(property=key):
                self.assertEqual(properties.get(key), expected)

    def test_product_import_cannot_gain_platform_or_privileged_authority(self):
        """Signing overrides, privileged placement, and Aurora Services are forbidden."""
        properties = self.app_properties()
        self.assertNotIn("certificate", properties)
        for key in ("privileged", "system_ext_specific", "vendor"):
            self.assertFalse(properties.get(key, False), key)
        self.assertNotRegex(json.dumps(properties).lower(), r"aurora[._ -]*services")

    def test_product_includes_store_once_without_identity_or_permission_overrides(self):
        """Duplicate/absent inclusion or identity/grant changes violate the app boundary."""
        source = REPOSITORY_LOCK.parent.parent.joinpath("device.mk").read_text(encoding="utf-8")
        source = re.sub(r"\\\r?\n", " ", source)
        assignments = []
        for line in source.splitlines():
            line = line.split("#", 1)[0]
            match = re.match(r"\s*(\w+)\s*(\+=|:=|\?=|=)\s*(.*)", line)
            if match:
                assignments.append((match[1], match[2], match[3].split()))
        packages = [token for key, _, values in assignments if key == "PRODUCT_PACKAGES" for token in values]
        self.assertEqual(packages.count("AuroraStorePreload"), 1)
        for key, operator, values in assignments:
            if "AuroraStorePreload" in values:
                self.assertEqual((key, operator), ("PRODUCT_PACKAGES", "+="))
            for value in values:
                self.assertNotRegex(value.lower(), r"ro\.product[.=]|ro\.build\.fingerprint|ih8sn|aurora[._-]*services|privapp-permissions|default-permissions|play.*certif")
            self.assertNotIn(key, ("PRODUCT_DEFAULT_DEV_CERTIFICATE", "PRODUCT_CERTIFICATE_OVERRIDES"))

    def test_build_workflow_prepares_sources_before_every_image_build(self):
        """Building before source preparation bypasses the verified materializer."""
        commands = [match[1] for run in self.workflow_runs(".github/workflows/build-android15-rpi4.yml")
                    for match in re.finditer(r"(?:^|[;\n])\s*bash\s+([^\s;]+)", run)]
        preparations = [index for index, command in enumerate(commands) if command == "unfold_aosp.sh"]
        builds = [index for index, command in enumerate(commands) if command == "build_rpi4.sh"]
        self.assertTrue(preparations, "missing source preparation")
        self.assertTrue(builds, "missing image build")
        for index in builds:
            self.assertLess(preparations[0], index)

    def test_validation_workflow_runs_full_aurora_module(self):
        """Removing PR coverage must not let binary-policy drift through validation."""
        commands = [shlex.split(line, comments=True) for run in self.workflow_runs(
            ".github/workflows/android15-port-validation.yml") for line in run.splitlines()
            if re.match(r"\s*python3\s", line)]
        self.assertTrue(any(command[:3] == ["python3", "-m", "unittest"] and
                            any(argument in ("tools/tests/test_android15_aurora_store.py",
                                             "tools.tests.test_android15_aurora_store") for argument in command[3:])
                            for command in commands), "validation must run the full Aurora module")

    def test_port_gate_runs_aurora_and_counts_its_failure(self):
        """An Aurora failure must make the actual repository gate exit unsuccessfully."""
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = pathlib.Path(temp_dir)
            fake_bin = directory / "bin"
            log = directory / "python-calls.log"
            MaterializerTests.write_executable(fake_bin / "python3", """
                #!/bin/sh
                printf '%s\\n' "$*" >> "$GATE_CALLS"
                for argument in "$@"; do
                    case "$argument" in
                        tools/tests/test_android15_aurora_store.py|tools.tests.test_android15_aurora_store)
                            exit "$AURORA_STATUS" ;;
                    esac
                done
                cat >/dev/null
            """)
            environment = os.environ.copy()
            environment["FAKE_BIN_BASH"] = MaterializerTests.bash_path(fake_bin)
            environment["GATE_CALLS"] = MaterializerTests.bash_path(log)
            for status in ("0", "1"):
                with self.subTest(aurora_status=status):
                    environment["AURORA_STATUS"] = status
                    result = subprocess.run([
                        str(GIT_BASH), "-c",
                        'export PATH="$FAKE_BIN_BASH:$PATH"; exec bash tools/check-android15-port.sh',
                    ], cwd=REPO_ROOT, env=environment, input="", text=True,
                        capture_output=True, check=False)
                    calls = [shlex.split(line) for line in log.read_text(encoding="utf-8").splitlines()]
                    log.unlink()
                    aurora_calls = [call for call in calls if any(argument in (
                        "tools/tests/test_android15_aurora_store.py", "tools.tests.test_android15_aurora_store")
                        for argument in call)]
                    self.assertEqual(len(aurora_calls), 1, "gate must invoke the full Aurora module once")
                    self.assertEqual(aurora_calls[0][:2], ["-m", "unittest"])
                    self.assertEqual(result.returncode, int(status), result.stdout + result.stderr)

    def test_build_baseline_invalidates_previous_product_cache(self):
        """Reusing the old image cache can silently omit the new product app."""
        spec = importlib.util.spec_from_file_location("check_build_cache", REPO_ROOT / "tools/check-android15-build-cache.py")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        workflow = REPO_ROOT / ".github/workflows/build-android15-rpi4.yml"
        helper = REPO_ROOT / "tools/android-build-cache.sh"
        self.assertEqual(checker.validate_contract(workflow, helper), [])
        baseline_values = re.findall(r"(?m)^\s*ANDROID_BUILD_BASELINE:\s*(\S+)\s*$", workflow.read_text(encoding="utf-8"))
        self.assertEqual(baseline_values, [self.baseline])
        with tempfile.TemporaryDirectory() as temp_dir:
            stale = pathlib.Path(temp_dir) / "stale.yml"
            stale.write_text(workflow.read_text(encoding="utf-8").replace(self.baseline, self.baseline.removesuffix("4") + "3"), encoding="utf-8")
            self.assertTrue(checker.validate_contract(stale, helper), "validator accepted stale product baseline")
        for relative in ("tools/check-android15-build-cache.py", "tools/tests/test_android15_build_cache.py",
                         "tools/tests/test_android15_runtime_performance.py"):
            with self.subTest(consumer=relative):
                tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
                values = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id in ("BASELINE", "RUNTIME_BASELINE") for target in node.targets)]
                self.assertEqual(values, [self.baseline])


if __name__ == "__main__":
    unittest.main()
