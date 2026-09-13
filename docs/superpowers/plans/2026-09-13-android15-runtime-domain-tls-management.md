# Android 15 Runtime Domain and TLS Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure Android Settings workflow that switches TeslaAndroid to a user-owned hostname, imports or obtains its trusted certificate, renews DuckDNS certificates automatically, and updates the Tesla tether-DNS path without rebuilding the image.

**Architecture:** A platform-signed `TeslaDomainManager` app owns UI, credentials, ACME, imports, jobs, and notifications. A root but SELinux-confined native Binder service owns server keys, atomic profile generations, lighttpd validation/reload, hostname properties, tether-DNS restart, health checks, and rollback. Existing AOSP patch series gain runtime netd mappings and a Settings entry; the existing browser Configuration Manager exposes only a read-only public profile response.

**Tech Stack:** Android 15 Java, AIDL Java/NDK backends, C++17, BoringSSL/libcrypto, Android Keystore, JobScheduler, lighttpd 1.4.58, dnsmasq/netd, SELinux, Python 3 `unittest`, Bash, Git patches, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-09-13-android15-runtime-domain-tls-management-design.md`

## Global Constraints

- First boot, upgrade, factory reset, and `Restore TeslaAndroid Default` select `device.teslaandroid.com` and `fullscreen.device.teslaandroid.com`.
- The default profile is manufacturer-managed and must not claim device-side renewal.
- Automatic renewal supports registered `<label>.duckdns.org` profiles only; Synology and generic profiles use manual PKCS#12 or PEM replacement.
- Normalize hostnames to lower-case IDNA ASCII and reject values longer than 90 bytes, schemes, paths, ports, IP literals, wildcards, underscores, trailing dots, empty labels, and invalid labels.
- Persist the active names as `persist.tesla-android.tls.primary_hostname` and `persist.tesla-android.tls.fullscreen_hostname`; invalid or empty primary state falls back to the default names.
- Keep the interception address `104.248.101.213`, the YouTube launcher mapping, and all unrelated offline-mode mappings unchanged.
- Store mutable TLS generations under `/data/vendor/tesla-android/tls`; automatic server keys never leave the native installer.
- Store private keys root-owned at mode `0600`; prevent shell, browser Configuration Manager, ordinary apps, and unrelated services from reading them.
- Store the DuckDNS token and ACME account key behind an app-UID-bound Android Keystore key; never log tokens, passwords, ACME JWS bodies, TXT challenges, or private material.
- Require a system-trusted chain, SAN coverage, server-auth EKU, key match, five-minute clock-skew allowance, and at least 72 hours of remaining validity before activation.
- Treat lighttpd activation, hostname-property change, tether-DNS restart, redirected HTTPS health check, and rollback as one transaction.
- Check DuckDNS profiles at boot, every 24 hours, and on demand; renew at 30 days; warn at 14, 7, 3, and 0 days; retry between one and 24 hours with jitter and `Retry-After` compliance.
- Keep the existing TeslaAndroid 2026.22.1 vendor pin `6e139bf41585188308e053dcbb34855f644aedba` and apply local changes as deterministic patches.
- Do not add TLS mutation endpoints to the browser-accessible REST service.
- Put `[build-image]` only in the final repository-integration commit.

## File Structure

Repository-owned files:

- Create `tools/tests/test_android15_runtime_tls.py`: static cross-project contract and secret-boundary regression tests.
- Modify `tools/check-android15-port.sh`: run the runtime-TLS regression suite.
- Rename `patches-aosp/vendor/tesla-android/0001-Remove-duplicate-protobuf-vendorcompat-prebuilt.patch` to `0002-Remove-duplicate-protobuf-vendorcompat-prebuilt.patch`: make the vendor patch order deterministic after the existing ih8sn patch.
- Create `patches-aosp/vendor/tesla-android/0003-Add-runtime-TLS-profile-store.patch`: AIDL, native service, default bootstrap, data generations, lighttpd runtime includes, and native tests.
- Create `patches-aosp/vendor/tesla-android/0004-Add-transactional-domain-activation.patch`: property selection, tether restart, end-to-end health checking, and rollback.
- Create `patches-aosp/vendor/tesla-android/0005-Add-Tesla-Domain-Manager-manual-import.patch`: privileged app, Settings screen, certificate inspection, PKCS#12/PEM import, and app tests.
- Create `patches-aosp/vendor/tesla-android/0006-Make-frontend-hostnames-runtime-configurable.patch`: hostname-neutral frontend and fullscreen redirect.
- Create `patches-aosp/vendor/tesla-android/0007-Add-DuckDNS-ACME-certificate-issuance.patch`: ACME v2, DuckDNS TXT, authoritative-DNS polling, and protocol tests.
- Create `patches-aosp/vendor/tesla-android/0008-Add-certificate-renewal-and-expiry-alerts.patch`: Keystore secret persistence, jobs, retry state, boot scheduling, and notifications.
- Create `patches-aosp/system/netd/0006-Use-runtime-TeslaAndroid-hostnames.patch`: safe runtime dnsmasq address mappings and netd tests.
- Create `patches-aosp/system/sepolicy/0001-Confine-TeslaAndroid-domain-and-TLS-services.patch`: app/service domains, Binder/property contexts, data labels, and access rules.
- Create `patches-aosp/packages/apps/Settings/0006-Add-TeslaAndroid-domain-and-TLS-entry.patch`: platform Settings entry and signature permission use.
- Create `patches-aosp/external/tesla-android-configuration-manager/0001-Expose-public-domain-profile.patch`: read-only public hostname/profile endpoint.
- Modify `.github/workflows/build-android15-rpi4.yml`: invalidate retained Android output for the new system/runtime contract.

Files created inside the vendor patch series under `aosptree/vendor/tesla-android/domain-manager/`:

- `Android.bp`: app, AIDL, daemon, and test modules.
- `aidl/com/teslaandroid/tls/ITeslaTlsInstaller.aidl`: protected installer API.
- `aidl/com/teslaandroid/tls/PublicTlsProfile.aidl`: public active-state response.
- `aidl/com/teslaandroid/tls/PreparedCsr.aidl`: automatic-profile staging response.
- `aidl/com/teslaandroid/tls/ApplyResult.aidl`: stable success/error response.
- `native/HostnameRules.{h,cpp}`: canonical native hostname validation.
- `native/ProfileStore.{h,cpp}`: durable generations, active/previous selection, commit markers, and cleanup.
- `native/CertificateOps.{h,cpp}`: key generation, CSR, PEM parsing, SAN/key/chain validation, and fingerprints.
- `native/LighttpdController.{h,cpp}`: configuration render/check, isolated TLS probe, PID reload, and readiness wait.
- `native/TetherController.{h,cpp}`: exact `cmd wifi` restart and explicit tether-DNS probe.
- `native/ApplyTransaction.{h,cpp}`: stage/select/reload/verify/commit/rollback state machine.
- `native/TeslaTlsInstallerService.{h,cpp}` and `native/main.cpp`: AIDL enforcement and service registration.
- `native/tesla-tls-installer.rc`: post-fs-data bootstrap and service startup.
- `native/tests/*Test.cpp`: native unit tests with injected filesystem, property, process, and health fakes.
- `app/AndroidManifest.xml`: platform-signed privileged app, protected activity, boot receiver, and renewal job.
- `app/src/com/teslaandroid/domain/*`: activity, Binder client, import, ACME, DuckDNS, DNS, Keystore, scheduling, and notification classes.
- `app/res/layout/activity_domain_tls.xml`: profile form and status presentation.
- `app/res/values/{strings,arrays,colors,styles}.xml`: provider choices and user-facing states.
- `app/tests/src/com/teslaandroid/domain/*Test.java`: JVM protocol/policy tests.
- `app/androidTest/src/com/teslaandroid/domain/*Test.java`: Keystore, Binder-permission, picker, and job integration tests.

Files split from the existing lighttpd configuration:

- `services/lighttpd/lighttpd.conf`: immutable base configuration and active-profile include.
- `services/lighttpd/frontend-routes.conf`: WebSocket, stream, safe public API, headers, and cache rules shared by configured hostnames.
- `services/lighttpd/www-redirect/index.html`: read active fullscreen hostname before redirecting.

---

### Task 1: Lock the Repository Contract and Patch Order

**Files:**

- Rename: `patches-aosp/vendor/tesla-android/0001-Remove-duplicate-protobuf-vendorcompat-prebuilt.patch` -> `patches-aosp/vendor/tesla-android/0002-Remove-duplicate-protobuf-vendorcompat-prebuilt.patch`
- Create: `tools/tests/test_android15_runtime_tls.py`

**Interfaces:**

- Consumes: repository patch files, vendor manifest pin, workflow baseline, and validator shell script.
- Produces: `Android15RuntimeTlsContractTest`, which names every required cross-project artifact and rejects accidental secret exposure or hard-coded custom-profile hosts.

- [ ] **Step 1: Rename the existing second vendor patch**

Run:

```bash
git mv \
  patches-aosp/vendor/tesla-android/0001-Remove-duplicate-protobuf-vendorcompat-prebuilt.patch \
  patches-aosp/vendor/tesla-android/0002-Remove-duplicate-protobuf-vendorcompat-prebuilt.patch
```

Expected: lexical order is ih8sn `0001`, protobuf `0002`; patch contents do not change.

- [ ] **Step 2: Write the failing repository contract tests**

Create `tools/tests/test_android15_runtime_tls.py` with this structure and exact required fragments:

```python
#!/usr/bin/env python3
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / "patches-aosp"
VENDOR = PATCH / "vendor" / "tesla-android"

REQUIRED = {
    VENDOR / "0003-Add-runtime-TLS-profile-store.patch": (
        "ITeslaTlsInstaller.aidl",
        "/data/vendor/tesla-android/tls",
        "validateCandidate",
        '"-tt"',
        "0600",
    ),
    VENDOR / "0004-Add-transactional-domain-activation.patch": (
        "persist.tesla-android.tls.primary_hostname",
        "start-softap-with-existing-config",
        "rollback",
    ),
    VENDOR / "0005-Add-Tesla-Domain-Manager-manual-import.patch": (
        "TeslaDomainManager",
        "PKCS12",
        "ACTION_OPEN_DOCUMENT",
        'android:allowBackup="false"',
    ),
    VENDOR / "0006-Make-frontend-hostnames-runtime-configurable.patch": (
        "window.location.hostname",
        "/api/domainProfile",
    ),
    VENDOR / "0007-Add-DuckDNS-ACME-certificate-issuance.patch": (
        "dns-01",
        "badNonce",
        "Retry-After",
        "duckdns.org/update",
    ),
    VENDOR / "0008-Add-certificate-renewal-and-expiry-alerts.patch": (
        "RENEW_BEFORE_DAYS = 30",
        "WARNING_DAYS = new int[] {14, 7, 3, 0}",
        "setPersisted(true)",
    ),
    PATCH / "system" / "netd" / "0006-Use-runtime-TeslaAndroid-hostnames.patch": (
        "persist.tesla-android.tls.primary_hostname",
        "104.248.101.213",
        "device.teslaandroid.com",
    ),
    PATCH / "system" / "sepolicy" / "0001-Confine-TeslaAndroid-domain-and-TLS-services.patch": (
        "tesla_tls_installer",
        "tesla_lighttpd",
        "tesla_tls_private_file",
        "neverallow",
    ),
    PATCH / "packages" / "apps" / "Settings" / "0006-Add-TeslaAndroid-domain-and-TLS-entry.patch": (
        "com.teslaandroid.settings.DOMAIN_TLS",
        "com.teslaandroid.permission.MANAGE_DOMAIN_TLS",
    ),
    PATCH / "external" / "tesla-android-configuration-manager" / "0001-Expose-public-domain-profile.patch": (
        'server.Get("/api/domainProfile"',
        "primaryHostname",
        "fullscreenHostname",
    ),
}


class Android15RuntimeTlsContractTest(unittest.TestCase):
    def test_required_patch_contracts(self):
        for path, fragments in REQUIRED.items():
            with self.subTest(path=path):
                self.assertTrue(path.is_file(), f"missing runtime TLS patch: {path}")
                text = path.read_text(encoding="utf-8")
                for fragment in fragments:
                    self.assertIn(fragment, text)

    def test_patch_prefixes_are_unique_per_project(self):
        for directory in sorted(PATCH.rglob("*")):
            if not directory.is_dir():
                continue
            prefixes = {}
            for path in directory.glob("*.patch"):
                match = re.match(r"(\d{4})-", path.name)
                if match:
                    self.assertNotIn(match.group(1), prefixes, directory)
                    prefixes[match.group(1)] = path.name

    def test_public_endpoint_has_no_mutation_or_secret_fields(self):
        path = PATCH / "external" / "tesla-android-configuration-manager" / "0001-Expose-public-domain-profile.patch"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn('server.Post("/api/domainProfile"', text)
        for forbidden in ("token", "password", "privkey", "privateKey", "challenge"):
            self.assertNotIn(f'+    cJSON_AddStringToObject(json, "{forbidden}"', text)

    def test_image_baseline_covers_runtime_tls_contract(self):
        workflow = (ROOT / ".github/workflows/build-android15-rpi4.yml").read_text(encoding="utf-8")
        self.assertIn(
            "ANDROID_BUILD_BASELINE: android-platform-15.0.0_r3-tesla-2026.22.1-runtime-domain-tls-v1",
            workflow,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests and confirm the expected red state**

Run:

```bash
python3 -m unittest -v tools/tests/test_android15_runtime_tls.py
```

Expected: `test_patch_prefixes_are_unique_per_project` passes after the rename; required-patch, public-endpoint, and workflow-baseline tests fail because implementation artifacts are absent.

- [ ] **Step 4: Commit the contract unit**

```bash
git add patches-aosp/vendor/tesla-android tools/tests/test_android15_runtime_tls.py
git commit -m "test: define runtime domain and TLS contract"
```

### Task 2: Build the Native TLS Store and Default Bootstrap

**Files:**

- Create in staged vendor source: `domain-manager/Android.bp`
- Create in staged vendor source: `domain-manager/aidl/com/teslaandroid/tls/{ITeslaTlsInstaller,PublicTlsProfile,PreparedCsr,ApplyResult}.aidl`
- Create in staged vendor source: `domain-manager/native/{HostnameRules,ProfileStore,CertificateOps,LighttpdController,TeslaTlsInstallerService}.{h,cpp}`
- Create in staged vendor source: `domain-manager/native/{main.cpp,tesla-tls-installer.rc}`
- Create in staged vendor source: `domain-manager/native/tests/{HostnameRules,ProfileStore,CertificateOps,LighttpdController}Test.cpp`
- Create in staged vendor source: `services/lighttpd/frontend-routes.conf`
- Modify in staged vendor source: `services/lighttpd/lighttpd.conf`
- Modify in staged vendor source: `init/init.tesla-android.rc`
- Modify in staged vendor source: `vendor.mk`
- Create: `patches-aosp/vendor/tesla-android/0003-Add-runtime-TLS-profile-store.patch`

**Interfaces:**

- Produces AIDL service name `com.teslaandroid.tls.ITeslaTlsInstaller/default`.
- Produces `HostnameSet { std::string primary; std::optional<std::string> fullscreen; }`.
- Produces `ProfileStore::bootstrapDefault()`, `beginGeneration()`, `selectGeneration()`, `commitGeneration()`, `restorePrevious()`, and `discardIncomplete()`.
- Produces `LighttpdController::validateCandidate()`, `probeCandidate()`, `reloadActive()`, and `waitUntilReady()`.
- Defers custom-profile activation to Task 3; the default profile must boot independently at the end of this task.

- [ ] **Step 1: Write native tests for hostname and durable-store invariants**

Add focused gtests before implementation:

```cpp
TEST(HostnameRulesTest, NormalizesIdnaAndRejectsUnsafeInput) {
    EXPECT_EQ("car.duckdns.org", NormalizeHostname("CAR.DUCKDNS.ORG").value());
    EXPECT_FALSE(NormalizeHostname("https://car.duckdns.org").has_value());
    EXPECT_FALSE(NormalizeHostname("car_name.duckdns.org").has_value());
    EXPECT_FALSE(NormalizeHostname(std::string(91, 'a') + ".org").has_value());
}

TEST(ProfileStoreTest, InterruptedGenerationNeverReplacesCommittedActive) {
    FakeFileOps files;
    ProfileStore store("/data/vendor/tesla-android/tls", &files);
    ASSERT_TRUE(store.bootstrapDefault().ok());
    const std::string original = store.activeGenerationId();
    ASSERT_TRUE(store.beginGeneration("candidate").ok());
    files.simulatePowerLoss();
    ASSERT_TRUE(store.recover().ok());
    EXPECT_EQ(original, store.activeGenerationId());
    EXPECT_FALSE(files.exists("staging/candidate"));
}

TEST(CertificateOpsTest, GeneratedCsrContainsEveryRequestedSan) {
    FakeFileOps files;
    CertificateOps certificates(&files);
    auto csr = certificates.generateKeyAndCsr(
        "staging/request-1/privkey.pem",
        HostnameSet{"car.duckdns.org", "fullscreen.car.duckdns.org"});
    ASSERT_TRUE(csr.ok());
    EXPECT_THAT(ParseDnsSans(csr->der),
                UnorderedElementsAre("car.duckdns.org", "fullscreen.car.duckdns.org"));
    EXPECT_EQ(0600, files.mode("staging/request-1/privkey.pem"));
}
```

- [ ] **Step 2: Run native tests and verify they fail to build**

Run in a synced Android tree:

```bash
source build/envsetup.sh
lunch tesla_android_rpi4-trunk_staging-userdebug
m tesla_tls_installer_tests
```

Expected: build fails because the `tesla_tls_installer_tests` module and production types do not exist.

- [ ] **Step 3: Declare AIDL and build modules**

Use one unstable in-image AIDL interface with Java and NDK backends:

```bp
aidl_interface {
    name: "com.teslaandroid.tls",
    srcs: ["aidl/com/teslaandroid/tls/*.aidl"],
    unstable: true,
    backend: {
        java: { enabled: true, platform_apis: true },
        cpp: { enabled: false },
        ndk: { enabled: true },
    },
}

cc_binary {
    name: "tesla_tls_installer",
    system_ext_specific: true,
    init_rc: ["native/tesla-tls-installer.rc"],
    srcs: ["native/*.cpp"],
    shared_libs: [
        "com.teslaandroid.tls-ndk_platform",
        "libbase", "libbinder_ndk", "libcrypto", "libjsoncpp", "liblog",
    ],
    cflags: ["-Wall", "-Wextra", "-Werror"],
}
```

The AIDL surface is exactly:

```aidl
interface ITeslaTlsInstaller {
    PublicTlsProfile getActiveProfile();
    PreparedCsr prepareAutomaticProfile(String primaryHostname, String fullscreenHostname);
    ApplyResult installIssuedCertificate(String transactionId, in byte[] certificateChainPem);
    ApplyResult importAndApply(String mode, String primaryHostname, String fullscreenHostname,
            in byte[] certificateChainPem, in byte[] pkcs8PrivateKey);
    ApplyResult restoreDefault();
    ApplyResult rollbackLast();
}
```

- [ ] **Step 4: Implement strict normalization and certificate primitives**

Keep one native revalidation gate even though the app also validates:

```cpp
Result<HostnameSet> ValidateHostnames(std::string_view primary,
                                      std::string_view fullscreen) {
    OR_RETURN(auto normalizedPrimary, NormalizeHostname(primary));
    std::optional<std::string> normalizedFullscreen;
    if (!fullscreen.empty()) {
        OR_RETURN(auto value, NormalizeHostname(fullscreen));
        if (value == normalizedPrimary) return Error("TLS_HOST_DUPLICATE");
        normalizedFullscreen = std::move(value);
    }
    return HostnameSet{std::move(normalizedPrimary), std::move(normalizedFullscreen)};
}
```

`CertificateOps` generates an ECDSA P-256 key with mode `0600`, writes PKCS#8 PEM, creates a SHA-256 CSR with every hostname as a DNS SAN, parses all PEM certificates, uses `X509_check_private_key`, uses `X509_check_host` without common-name fallback, requires server-auth EKU, verifies the system chain, and returns a SHA-256 fingerprint. Imported RSA is limited to 2048/3072/4096; imported EC is limited to P-256/P-384.

- [ ] **Step 5: Implement atomic profile generations and boot recovery**

Use durable generation transitions:

```cpp
Result<void> ProfileStore::selectGeneration(const std::string& id) {
    OR_RETURN(fsyncTree(path("generations/" + id)));
    OR_RETURN(writeSymlinkAtomically("active.next", "generations/" + id));
    OR_RETURN(renamePath("active.next", "active"));
    OR_RETURN(fsyncDirectory(root_));
    return {};
}

Result<void> ProfileStore::commitGeneration(const std::string& id) {
    OR_RETURN(writeFileAtomically("generations/" + id + "/COMMITTED", "1\n", 0600));
    OR_RETURN(fsyncDirectory(path("generations/" + id)));
    return cleanupExceptActivePreviousAndStaging();
}
```

At service startup, `discardIncomplete()` removes an uncommitted staging directory. `bootstrapDefault()` creates a committed generation whose rendered include references the two immutable `/vendor/tesla-android/lighttpd/certificates/...` pairs and whose public profile mode is `DEFAULT`.

- [ ] **Step 6: Split lighttpd routes and gate startup on bootstrap**

Replace embedded TLS/host routing with:

```lighttpd
include "/data/vendor/tesla-android/tls/active/lighttpd-tls.conf"

$HTTP["url"] == "/api/domainProfile" {
    proxy.server = ( "" => (( "host" => "127.0.0.1", "port" => 8081 )))
}
```

The generated default include selects the shipped primary certificate, selects the shipped fullscreen certificate for the default fullscreen host, and includes `/vendor/tesla-android/lighttpd/frontend-routes.conf` for the two default names plus their existing `www` aliases.

Start bootstrap after `/data` is mounted and lighttpd only after readiness:

```rc
service tesla-tls-installer /system_ext/bin/tesla_tls_installer
    class core
    user root
    group root
    disabled

on post-fs-data
    start tesla-tls-installer

on property:tesla.tls.bootstrap_ready=1
    start lighttpd
```

- [ ] **Step 7: Run native and lighttpd-focused verification**

```bash
m tesla_tls_installer tesla_tls_installer_tests
atest tesla_tls_installer_tests
/vendor/bin/lighttpd -tt -f /vendor/tesla-android/lighttpd/lighttpd.conf
```

Expected: native tests pass; bootstrap creates a valid default generation; lighttpd configuration validation exits 0.

- [ ] **Step 8: Commit staged vendor source and export the patch**

In the staged `vendor/tesla-android` project:

```bash
git add domain-manager services/lighttpd init/init.tesla-android.rc vendor.mk
git commit -m "teslaandroid: add runtime TLS profile store"
git format-patch -1 --stdout > "$MAIN_REPO/patches-aosp/vendor/tesla-android/0003-Add-runtime-TLS-profile-store.patch"
```

In the main repository:

```bash
git add patches-aosp/vendor/tesla-android/0003-Add-runtime-TLS-profile-store.patch
git commit -m "android15: add runtime TLS profile foundation"
```

### Task 3: Make Domain Activation Transactional Across netd and lighttpd

**Files:**

- Create in staged vendor source: `domain-manager/native/{TetherController,ApplyTransaction}.{h,cpp}`
- Create in staged vendor source: `domain-manager/native/tests/{TetherController,ApplyTransaction}Test.cpp`
- Modify in staged vendor source: `domain-manager/native/TeslaTlsInstallerService.cpp`
- Modify in staged vendor source: `init/init.tesla-android.rc`
- Create: `patches-aosp/vendor/tesla-android/0004-Add-transactional-domain-activation.patch`
- Create in staged netd source: tests in `server/TetherControllerTest.cpp`
- Modify in staged netd source: `server/TetherController.cpp`
- Create: `patches-aosp/system/netd/0006-Use-runtime-TeslaAndroid-hostnames.patch`
- Modify in staged platform policy: `private/{file_contexts,property_contexts,service_contexts}` and new `private/{tesla_tls_installer,tesla_lighttpd}.te`
- Create: `patches-aosp/system/sepolicy/0001-Confine-TeslaAndroid-domain-and-TLS-services.patch`

**Interfaces:**

- Consumes: Task 2's `ProfileStore`, `CertificateOps`, `LighttpdController`, and AIDL result types.
- Produces `ApplyTransaction::apply(CandidateProfile)` and `ApplyTransaction::rollback()`.
- Produces property-backed netd functions `getTeslaAndroidHostnames()` and `appendTeslaAndroidDnsmasqMappings()`.
- Produces exact process commands `/system/bin/cmd wifi stop-softap` and `/system/bin/cmd wifi start-softap-with-existing-config` without a shell.

- [ ] **Step 1: Write transaction failure-injection tests**

```cpp
TEST_P(ApplyTransactionFailureTest, RestoresEveryResourceAfterCommitBoundaryFailure) {
    FakeProfileStore store = FakeProfileStore::WithDefaultActive();
    FakeProperties properties = FakeProperties::WithDefaultHostnames();
    FakeLighttpd lighttpd;
    FakeTether tether;
    FakeServingPath health;
    InjectFailureAt(GetParam(), &store, &properties, &lighttpd, &tether, &health);

    ApplyTransaction transaction(&store, &properties, &lighttpd, &tether, &health);
    ApplyResult result = transaction.apply(ValidDuckDnsCandidate());

    EXPECT_FALSE(result.success);
    EXPECT_EQ("device.teslaandroid.com", properties.primary());
    EXPECT_EQ("fullscreen.device.teslaandroid.com", properties.fullscreen());
    EXPECT_EQ("default-generation", store.activeGenerationId());
    EXPECT_TRUE(lighttpd.serves("device.teslaandroid.com"));
}

INSTANTIATE_TEST_SUITE_P(
    CommitBoundaries, ApplyTransactionFailureTest,
    Values(FailurePoint::kSelect, FailurePoint::kLighttpdReload,
           FailurePoint::kTetherStop, FailurePoint::kTetherStart,
           FailurePoint::kDnsProbe, FailurePoint::kHttpsProbe));
```

Add netd tests that set valid, empty, overlength, and delimiter-containing property values and assert the resulting dnsmasq arguments.

- [ ] **Step 2: Verify transaction and netd tests fail**

```bash
m tesla_tls_installer_tests netd_unit_test
atest tesla_tls_installer_tests netd_unit_test
```

Expected: new transaction and runtime-hostname tests fail because production code still uses fixed mappings and has no atomic apply state machine.

- [ ] **Step 3: Read and validate runtime hostnames in netd**

Implement a bounded fallback before composing dnsmasq arguments:

```cpp
constexpr char kPrimaryProperty[] = "persist.tesla-android.tls.primary_hostname";
constexpr char kFullscreenProperty[] = "persist.tesla-android.tls.fullscreen_hostname";
constexpr char kDefaultPrimary[] = "device.teslaandroid.com";
constexpr char kDefaultFullscreen[] = "fullscreen.device.teslaandroid.com";
constexpr char kInterceptAddress[] = "104.248.101.213";

const auto hosts = getTeslaAndroidHostnames(property_getter);
appendAddressArgument(&argVector, hosts.primary, kInterceptAddress);
if (hosts.fullscreen.has_value()) {
    appendAddressArgument(&argVector, *hosts.fullscreen, kInterceptAddress);
}
if (hosts.isDefault) {
    appendAddressArgument(&argVector, "www.device.teslaandroid.com", kInterceptAddress);
    appendAddressArgument(&argVector, "www.fullscreen.device.teslaandroid.com", kInterceptAddress);
}
```

The validation function rejects any byte outside lowercase ASCII letters, digits, dot, and hyphen after checking DNS label boundaries and the 90-byte limit. Leave YouTube, telemetry, firmware, and offline mappings byte-for-byte unchanged.

- [ ] **Step 4: Implement exact tether restart and serving-path probes**

`TetherController` uses `posix_spawn` with fixed argv arrays:

```cpp
Result<void> TetherController::restart() {
    OR_RETURN(commands_->run({"/system/bin/cmd", "wifi", "stop-softap"}));
    OR_RETURN(commands_->run({"/system/bin/cmd", "wifi", "start-softap-with-existing-config"}));
    return waitForDnsmasq(std::chrono::seconds(20));
}
```

The DNS probe sends an A query for the active primary hostname directly to `172.16.0.1:53` and requires `104.248.101.213`. The HTTPS probe connects to `172.16.0.1:443`, sends the active primary hostname as SNI, validates normal trust and hostname matching, requests `/api/health`, and requires HTTP 200.

- [ ] **Step 5: Implement the transaction and rollback ordering**

```cpp
ApplyResult ApplyTransaction::apply(const CandidateProfile& candidate) {
    OR_RETURN_RESULT(validateAndStage(candidate));
    OR_RETURN_RESULT(lighttpd_->validateCandidate(candidate.generation));
    OR_RETURN_RESULT(lighttpd_->probeCandidate(candidate.generation));
    Snapshot old = snapshotCurrent();
    return commitOrRollback(old, [&] {
        OR_RETURN(store_->selectGeneration(candidate.generation));
        OR_RETURN(properties_->setHostnames(candidate.hostnames));
        OR_RETURN(lighttpd_->reloadActive());
        OR_RETURN(tether_->restart());
        OR_RETURN(health_->verify(candidate.hostnames.primary));
        return store_->commitGeneration(candidate.generation);
    });
}
```

`commitOrRollback` restores the prior generation and both properties, reloads lighttpd, restarts tethering, and verifies the old route once. If that verification fails, it selects the default generation and emits error code `TLS_ROLLBACK_DEFAULTED`; it does not alternate repeatedly.

- [ ] **Step 6: Add SELinux identities and secret boundaries**

Define explicit domains and types:

```te
type tesla_tls_installer, domain, coredomain;
type tesla_tls_installer_exec, exec_type, system_file_type, file_type;
type tesla_lighttpd, domain;
type tesla_lighttpd_exec, exec_type, vendor_file_type, file_type;
type tesla_tls_public_file, file_type, data_file_type;
type tesla_tls_private_file, file_type, data_file_type;
system_private_prop(tesla_tls_hostname_prop)
system_internal_prop(tesla_tls_readiness_prop)
type tesla_tls_installer_service, service_manager_type;

neverallow { domain -tesla_tls_installer -tesla_lighttpd }
    tesla_tls_private_file:file { open read write append map execute };
```

Give the installer create/read/write/relabel access, give lighttpd read-only access to active private material, and give no TLS private-file access to shell or the Configuration Manager process. Label the service name, all three public properties (`primary_hostname`, `fullscreen_hostname`, `profile_mode`), and the internal readiness property `tesla.tls.bootstrap_ready`. Change the lighttpd init stanza to `seclabel u:r:tesla_lighttpd:s0`. Reject Binder caller UID 0 and 2000 before checking `com.teslaandroid.permission.MANAGE_DOMAIN_TLS`.

- [ ] **Step 7: Run focused native, netd, and policy verification**

```bash
m tesla_tls_installer_tests netd_unit_test selinux_policy
atest tesla_tls_installer_tests netd_unit_test
adb shell cat /data/vendor/tesla-android/tls/active/privkey.pem
adb shell run-as com.teslaandroid.domainmanager cat /data/vendor/tesla-android/tls/active/privkey.pem
```

Expected: unit tests and policy build pass; both direct key reads are denied outside installer/lighttpd test domains; active server key mode is `0600`.

- [ ] **Step 8: Export and commit the transaction patches**

```bash
git -C aosptree/vendor/tesla-android format-patch -1 --stdout > \
  patches-aosp/vendor/tesla-android/0004-Add-transactional-domain-activation.patch
git -C aosptree/system/netd format-patch -1 --stdout > \
  patches-aosp/system/netd/0006-Use-runtime-TeslaAndroid-hostnames.patch
git -C aosptree/system/sepolicy format-patch -1 --stdout > \
  patches-aosp/system/sepolicy/0001-Confine-TeslaAndroid-domain-and-TLS-services.patch
git add patches-aosp/vendor/tesla-android patches-aosp/system/netd patches-aosp/system/sepolicy
git commit -m "android15: activate runtime domains transactionally"
```

### Task 4: Add the Privileged Settings App and Manual Import

**Files:**

- Create in staged vendor source: `domain-manager/app/AndroidManifest.xml`
- Create in staged vendor source: `domain-manager/app/res/layout/activity_domain_tls.xml`
- Create in staged vendor source: `domain-manager/app/res/values/{strings,arrays,colors,styles}.xml`
- Create in staged vendor source: `domain-manager/app/src/com/teslaandroid/domain/{DomainTlsActivity,TlsInstallerClient,HostnameNormalizer,CertificateBundleValidator,CertificateImporter,ProfileDraft,UiState}.java`
- Create in staged vendor source: `domain-manager/app/tests/src/com/teslaandroid/domain/{HostnameNormalizer,CertificateBundleValidator,CertificateImporter}Test.java`
- Create in staged vendor source: `domain-manager/app/androidTest/src/com/teslaandroid/domain/{BinderPermission,DocumentImport}Test.java`
- Modify in staged vendor source: `domain-manager/Android.bp`, `vendor.mk`
- Create: `patches-aosp/vendor/tesla-android/0005-Add-Tesla-Domain-Manager-manual-import.patch`

**Interfaces:**

- Consumes: Task 2 AIDL and Task 3 transactional `importAndApply`/`restoreDefault` operations.
- Produces exported action `com.teslaandroid.settings.DOMAIN_TLS`, guarded by `com.teslaandroid.permission.MANAGE_DOMAIN_TLS`.
- Produces `CertificateImporter.readPkcs12()`, `readPemPair()`, and `CertificateBundleValidator.validate()`.
- Produces provider values `DEFAULT`, `DUCKDNS`, `SYNOLOGY_MANUAL`, and `GENERIC_MANUAL`.

- [ ] **Step 1: Write pure JVM tests for hostname and import policy**

```java
@Test public void pemWrongKeyIsRejectedBeforeBinderCall() throws Exception {
    CertificateBundle bundle = CertificateImporter.readPemPair(
            fixture("car-chain.pem"), fixture("other-key.pk8"));
    ValidationResult result = validator.validate(
            bundle, "car.synology.me", "fullscreen.car.synology.me", NOW);
    assertThat(result.code()).isEqualTo("TLS_KEY_MISMATCH");
}

@Test public void certificateMustCoverBothConfiguredNames() throws Exception {
    CertificateBundle bundle = fixtureBundle("car-only.p12", "secret".toCharArray());
    ValidationResult result = validator.validate(
            bundle, "car.synology.me", "fullscreen.car.synology.me", NOW);
    assertThat(result.code()).isEqualTo("TLS_SAN_MISSING");
}

@Test public void importRequiresAtLeastSeventyTwoHoursRemaining() throws Exception {
    ValidationResult result = validator.validate(
            fixtureBundle("expires-in-48h.p12", "secret".toCharArray()),
            "car.synology.me", "", NOW);
    assertThat(result.code()).isEqualTo("TLS_LIFETIME_TOO_SHORT");
}
```

- [ ] **Step 2: Verify app tests fail to build**

```bash
m TeslaDomainManagerTests TeslaDomainManagerDeviceTests
```

Expected: modules and production classes are absent.

- [ ] **Step 3: Declare the platform-signed privileged app**

```bp
android_app {
    name: "TeslaDomainManager",
    system_ext_specific: true,
    privileged: true,
    certificate: "platform",
    platform_apis: true,
    srcs: ["app/src/**/*.java"],
    resource_dirs: ["app/res"],
    static_libs: ["com.teslaandroid.tls-java"],
    optimize: { enabled: true },
}
```

The manifest sets `android:allowBackup="false"`, declares `INTERNET`, `RECEIVE_BOOT_COMPLETED`, and the signature permission, and exports only `DomainTlsActivity` with the protected action. The renewal service and boot receiver remain non-exported.

- [ ] **Step 4: Implement bounded document import and certificate validation**

Use `ACTION_OPEN_DOCUMENT`, never broad storage permission:

```java
private static final int MAX_IMPORT_BYTES = 512 * 1024;

static byte[] readBounded(ContentResolver resolver, Uri uri) throws IOException {
    try (InputStream input = resolver.openInputStream(uri);
         ByteArrayOutputStream output = new ByteArrayOutputStream()) {
        byte[] buffer = new byte[8192];
        int total = 0;
        for (int read; (read = input.read(buffer)) != -1; ) {
            total += read;
            if (total > MAX_IMPORT_BYTES) throw new IOException("TLS_IMPORT_TOO_LARGE");
            output.write(buffer, 0, read);
        }
        return output.toByteArray();
    }
}
```

PKCS#12 uses `KeyStore.getInstance("PKCS12")`; PEM accepts one certificate chain and one unencrypted PKCS#8 key. Validate trust with the default `TrustManagerFactory`, exact/wildcard SAN rules without common-name fallback, server-auth EKU, key match by sign/verify, algorithm allowlist, five-minute skew, and 72-hour remaining lifetime. Clear password char arrays and imported key byte arrays in `finally` blocks.

- [ ] **Step 5: Implement the manual-provider UI state machine**

The activity enables `Apply` only from `READY_TO_APPLY`:

```java
enum UiState {
    LOADING_ACTIVE, EDITING, VALIDATING, READY_TO_APPLY,
    APPLYING, ACTIVE, FAILED, WAITING_FOR_TIME
}

void applyManualProfile(ProfileDraft draft, CertificateBundle bundle) {
    render(UiState.APPLYING, "Testing certificate and local HTTPS…");
    executor.execute(() -> {
        ApplyResult result = installer.importAndApply(
                draft.mode(), draft.primary(), draft.fullscreen(),
                bundle.chainPem(), bundle.pkcs8PrivateKey());
        main.execute(() -> renderResult(result));
    });
}
```

Show issuer, SAN names, fingerprint, expiry, trust state, last import time, and `Manual renewal`. `Restore TeslaAndroid Default` calls only the protected Binder method and displays the returned transaction result.

- [ ] **Step 6: Run app, Binder, and import tests**

```bash
m TeslaDomainManager TeslaDomainManagerTests TeslaDomainManagerDeviceTests
atest TeslaDomainManagerTests TeslaDomainManagerDeviceTests
```

Expected: pure JVM tests and device tests pass; an unprivileged test package cannot resolve the protected activity or call the installer; valid PKCS#12 and PEM fixtures activate through a fake installer.

- [ ] **Step 7: Export and commit the manual-profile patch**

```bash
git -C aosptree/vendor/tesla-android format-patch -1 --stdout > \
  patches-aosp/vendor/tesla-android/0005-Add-Tesla-Domain-Manager-manual-import.patch
git add patches-aosp/vendor/tesla-android/0005-Add-Tesla-Domain-Manager-manual-import.patch
git commit -m "android15: add manual custom-domain profiles"
```

### Task 5: Add the Settings Entry and Runtime Frontend Discovery

**Files:**

- Modify in staged Settings source: `AndroidManifest.xml`, `res/xml/top_level_settings.xml`, `res/xml/top_level_settings_v2.xml`, `res/values/strings.xml`
- Create in staged Settings source: `res/drawable/ic_tesla_android_domain.xml`
- Create: `patches-aosp/packages/apps/Settings/0006-Add-TeslaAndroid-domain-and-TLS-entry.patch`
- Modify in staged Configuration Manager source: `tesla-android-configuration-manager.cpp`
- Create: `patches-aosp/external/tesla-android-configuration-manager/0001-Expose-public-domain-profile.patch`
- Modify in staged vendor source: `services/lighttpd/www-default/beta/js/core/shared.js`, `services/lighttpd/www-redirect/index.html`
- Create: `patches-aosp/vendor/tesla-android/0006-Make-frontend-hostnames-runtime-configurable.patch`

**Interfaces:**

- Consumes active public properties from Task 3.
- Produces `GET /api/domainProfile` JSON with only `profileMode`, `primaryHostname`, and `fullscreenHostname`.
- Produces a top-level Settings item titled `TeslaAndroid`, summary `Domain & TLS`, launching the protected activity.
- Produces hostname-neutral beta WebSocket/API URLs and runtime fullscreen redirect with default fallback.

- [ ] **Step 1: Extend repository tests for endpoint and frontend behavior**

Add these assertions to `tools/tests/test_android15_runtime_tls.py`:

```python
    def test_runtime_frontend_uses_current_origin_and_safe_profile_endpoint(self):
        vendor = (VENDOR / "0006-Make-frontend-hostnames-runtime-configurable.patch").read_text(encoding="utf-8")
        endpoint = (PATCH / "external" / "tesla-android-configuration-manager" / "0001-Expose-public-domain-profile.patch").read_text(encoding="utf-8")
        self.assertIn("window.location.origin", vendor)
        self.assertIn('fetch("/api/domainProfile"', vendor)
        self.assertIn('server.Get("/api/domainProfile"', endpoint)
        self.assertNotIn('server.Post("/api/domainProfile"', endpoint)
```

- [ ] **Step 2: Run the new test and verify failure**

```bash
python3 -m unittest -v \
  tools.tests.test_android15_runtime_tls.Android15RuntimeTlsContractTest.test_runtime_frontend_uses_current_origin_and_safe_profile_endpoint
```

Expected: fails because all three patches are absent.

- [ ] **Step 3: Add the read-only Configuration Manager endpoint**

```cpp
server.Get("/api/domainProfile", [](const httplib::Request&, httplib::Response& res) {
    cJSON* json = cJSON_CreateObject();
    cJSON_AddStringToObject(json, "profileMode", safe_property(PROFILE_MODE, "DEFAULT"));
    cJSON_AddStringToObject(json, "primaryHostname",
                            safe_property(PRIMARY_HOSTNAME, "device.teslaandroid.com"));
    cJSON_AddStringToObject(json, "fullscreenHostname",
                            safe_property(FULLSCREEN_HOSTNAME,
                                          "fullscreen.device.teslaandroid.com"));
    char* body = cJSON_PrintUnformatted(json);
    res.set_content(body, "application/json");
    free(body);
    cJSON_Delete(json);
});
```

Do not register POST, PUT, PATCH, or DELETE handlers for this path.

- [ ] **Step 4: Make frontend origins runtime-derived**

In beta `shared.js`, build primary URLs from the current origin:

```javascript
TAHtml.createFlavor = function createFlavor() {
  const httpOrigin = window.location.origin;
  const wsOrigin = httpOrigin.replace(/^http/, "ws");
  return {
    domain: window.location.hostname,
    apiBaseUrl: httpOrigin + "/api",
    audioWebSocket: wsOrigin + "/sockets/audio",
    displayWebSocket: wsOrigin + "/sockets/display",
    gpsWebSocket: wsOrigin + "/sockets/gps",
    touchscreenWebSocket: wsOrigin + "/sockets/touchscreen",
  };
};
```

In `www-redirect/index.html`, fetch `/api/domainProfile`, select `fullscreenHostname || primaryHostname`, require it to match the same strict ASCII hostname grammar, and redirect to `https://` plus that name. On fetch, parse, or validation failure, use `fullscreen.device.teslaandroid.com`.

- [ ] **Step 5: Add the AOSP Settings launcher**

Add the protected permission use to Settings and this item to both top-level XML variants:

```xml
<com.android.settings.widget.HomepagePreference
    android:key="top_level_tesla_android"
    android:order="5"
    android:title="@string/tesla_android_settings_title"
    android:summary="@string/tesla_android_domain_tls_summary"
    android:icon="@drawable/ic_tesla_android_domain">
    <intent
        android:action="com.teslaandroid.settings.DOMAIN_TLS"
        android:targetPackage="com.teslaandroid.domainmanager"
        android:targetClass="com.teslaandroid.domain.DomainTlsActivity" />
</com.android.settings.widget.HomepagePreference>
```

Use strings `TeslaAndroid` and `Domain & TLS`. The vector icon contains no trademarked Tesla vehicle/logo artwork; use a generic lock-and-display glyph.

- [ ] **Step 6: Run endpoint, frontend, Settings, and app-launch tests**

```bash
python3 -m unittest -v tools/tests/test_android15_runtime_tls.py
m Settings tesla-android-configuration-manager TeslaDomainManager
atest SettingsUnitTests TeslaDomainManagerDeviceTests
```

Expected: runtime frontend test passes; Settings resolves exactly one protected activity; the endpoint returns three public fields and no cacheable secrets.

- [ ] **Step 7: Export and commit the discovery patches**

```bash
git -C aosptree/packages/apps/Settings format-patch -1 --stdout > \
  patches-aosp/packages/apps/Settings/0006-Add-TeslaAndroid-domain-and-TLS-entry.patch
git -C aosptree/external/tesla-android-configuration-manager format-patch -1 --stdout > \
  patches-aosp/external/tesla-android-configuration-manager/0001-Expose-public-domain-profile.patch
git -C aosptree/vendor/tesla-android format-patch -1 --stdout > \
  patches-aosp/vendor/tesla-android/0006-Make-frontend-hostnames-runtime-configurable.patch
git add patches-aosp/packages/apps/Settings \
  patches-aosp/external/tesla-android-configuration-manager \
  patches-aosp/vendor/tesla-android
git commit -m "android15: expose runtime domain through Settings and frontend"
```

### Task 6: Implement DuckDNS ACME DNS-01 Issuance

**Files:**

- Create in staged vendor source: `domain-manager/app/src/com/teslaandroid/domain/acme/{AcmeClient,AcmeDirectory,AcmeOrderRunner,JoseSigner,JwkThumbprint,EcdsaJoseCodec}.java`
- Create in staged vendor source: `domain-manager/app/src/com/teslaandroid/domain/duckdns/{DuckDnsClient,DuckDnsProfile,DnsTxtProbe,DnsPacketCodec}.java`
- Create in staged vendor source: `domain-manager/app/tests/src/com/teslaandroid/domain/acme/{AcmeClient,JoseSigner,AcmeOrderRunner}Test.java`
- Create in staged vendor source: `domain-manager/app/tests/src/com/teslaandroid/domain/duckdns/{DuckDnsClient,DnsTxtProbe,DnsPacketCodec}Test.java`
- Modify in staged vendor source: `domain-manager/app/src/com/teslaandroid/domain/DomainTlsActivity.java`
- Create: `patches-aosp/vendor/tesla-android/0007-Add-DuckDNS-ACME-certificate-issuance.patch`

**Interfaces:**

- Consumes `prepareAutomaticProfile()` and `installIssuedCertificate()` from Task 2/3.
- Produces `DuckDnsClient.setTxt(registration, token, value)`, `clearTxt()`, and `verifyOwnership()`.
- Produces `AcmeOrderRunner.issue(hostnames, csrDer, accountKey, challengePublisher)` returning PEM chain bytes.
- Produces `AcmeClient.rolloverAccountKey(oldKey, newKey)` and `revokeCertificate(certificateDer, reason)` with failure-safe key retention.
- Produces error codes `DUCKDNS_TOKEN_REJECTED`, `DNS_PROPAGATION_TIMEOUT`, `ACME_BAD_NONCE_EXHAUSTED`, `ACME_RATE_LIMITED`, `ACME_AUTHORIZATION_FAILED`, and `ACME_ORDER_FAILED`.

- [ ] **Step 1: Write deterministic protocol tests with fake HTTP and DNS**

```java
@Test public void badNonceFetchesFreshNonceAndRetriesOnce() throws Exception {
    FakeHttp http = new FakeHttp()
            .enqueueAcmeProblem(400, "urn:ietf:params:acme:error:badNonce", "nonce-2")
            .enqueueJson(201, "nonce-3", "{\"status\":\"pending\"}");
    AcmeClient client = new AcmeClient(http, signer, clock);
    client.newOrder(List.of("car.duckdns.org"));
    assertThat(http.requestCount()).isEqualTo(2);
    assertThat(http.request(1).protectedNonce()).isEqualTo("nonce-2");
}

@Test public void dnsAuthorizationsArePublishedAndValidatedSerially() throws Exception {
    FakeChallenges challenges = FakeChallenges.forNames(
            "car.duckdns.org", "fullscreen.car.duckdns.org");
    runner.issue(challenges, csr, accountKey, publisher);
    assertThat(publisher.events()).containsExactly(
            "set:first", "visible:first", "validate:first", "clear",
            "set:second", "visible:second", "validate:second", "clear").inOrder();
}

@Test public void duckDnsUrlEncodingNeverLeaksTokenToLogs() throws Exception {
    client.setTxt("car", "secret-token", "challenge/value+");
    assertThat(http.lastUrl()).contains("domains=car");
    assertThat(http.lastUrl()).contains("txt=challenge%2Fvalue%2B");
    assertThat(logs.joined()).doesNotContain("secret-token");
}
```

- [ ] **Step 2: Run protocol tests and verify failure**

```bash
m TeslaDomainManagerTests
atest TeslaDomainManagerTests
```

Expected: ACME and DuckDNS test classes fail to compile because protocol classes are absent.

- [ ] **Step 3: Implement ACME JWS and account handling**

Use an Android Keystore EC P-256 account key. Encode protected headers with `jwk` before account creation and `kid` afterward. Convert Java DER ECDSA signatures to fixed 64-byte JOSE `R || S`. Compute the RFC 7638 thumbprint from canonical `{"crv":"P-256","kty":"EC","x":"…","y":"…"}` bytes.

```java
byte[] signRequest(URL url, String nonce, String kid, byte[] payload) {
    String protectedJson = headers.json(url, nonce, kid, accountPublicJwk);
    String protected64 = base64Url(protectedJson.getBytes(UTF_8));
    String payload64 = base64Url(payload);
    byte[] der = keystoreSigner.sign((protected64 + "." + payload64).getBytes(US_ASCII));
    return jwsJson(protected64, payload64, EcdsaJoseCodec.derToJose(der, 32));
}
```

Honor replay nonces, POST-as-GET, `badNonce`, `Retry-After`, order/authorization states, alternate chains, and bounded polling. Prefer the first alternate chain that passes the Android trust manager and hostname checks; if several pass, choose the shortest validated chain. Cap one issuance attempt at 15 minutes and five HTTP attempts per ACME resource transition.

Keep the old ACME account key selected until the signed key-change request succeeds, then atomically replace the active alias. Implement certificate revocation with reason `keyCompromise` or `cessationOfOperation`. Tests must prove a failed rollover retains the old key and that a revocation request contains only the certificate DER, reason, and signed ACME metadata.

- [ ] **Step 4: Implement DuckDNS TXT and authoritative visibility checks**

Send only HTTPS requests to `https://www.duckdns.org/update`. Derive registration `car` from `car.duckdns.org`; reject other suffixes in automatic mode. Set TXT with percent-encoded query parameters and require the response body `OK`.

`DnsPacketCodec` builds/parses bounded DNS packets. `DnsTxtProbe` obtains current `duckdns.org` NS records through the active network resolver, resolves those NS names, queries each authoritative server directly on UDP/TCP 53, and succeeds only when at least one authoritative answer contains exactly the expected TXT value. Poll every five seconds for up to two minutes.

- [ ] **Step 5: Implement serial DNS-01 order completion**

```java
for (Authorization authorization : order.authorizations()) {
    DnsChallenge challenge = authorization.requireDns01();
    String txt = base64Url(sha256(
            (challenge.token() + "." + accountThumbprint).getBytes(US_ASCII)));
    duckDns.setTxt(profile.registration(), token, txt);
    try {
        dnsTxtProbe.awaitVisible(challenge.recordName(), txt, Duration.ofMinutes(2));
        acme.accept(challenge);
        acme.awaitAuthorizationValid(authorization, Duration.ofMinutes(3));
    } finally {
        duckDns.clearTxt(profile.registration(), token);
    }
}
```

Finalize with the service-generated DER CSR, download the PEM chain, validate it in the app, and pass it to `installIssuedCertificate`. Keep the current profile active until that Binder call returns success.

- [ ] **Step 6: Run protocol and staging-ACME tests**

```bash
m TeslaDomainManagerTests
atest TeslaDomainManagerTests
```

Expected: JVM tests pass against in-process deterministic ACME and DNS servers; a test chain is issued; serial challenge history matches the two configured names; no production ACME request occurs.

- [ ] **Step 7: Export and commit the ACME patch**

```bash
git -C aosptree/vendor/tesla-android format-patch -1 --stdout > \
  patches-aosp/vendor/tesla-android/0007-Add-DuckDNS-ACME-certificate-issuance.patch
git add patches-aosp/vendor/tesla-android/0007-Add-DuckDNS-ACME-certificate-issuance.patch
git commit -m "android15: add automatic DuckDNS certificate issuance"
```

### Task 7: Add Secret Persistence, Renewal, Retry, and Expiry Warnings

**Files:**

- Create in staged vendor source: `domain-manager/app/src/com/teslaandroid/domain/{SecretStore,RenewalPolicy,RenewalScheduler,RenewalJobService,BootReceiver,TlsNotifications,TimeTrust}.java`
- Create in staged vendor source: `domain-manager/app/tests/src/com/teslaandroid/domain/{RenewalPolicy,RetryPolicy,TimeTrust}Test.java`
- Create in staged vendor source: `domain-manager/app/androidTest/src/com/teslaandroid/domain/{SecretStore,RenewalJobService,TlsNotifications}Test.java`
- Modify in staged vendor source: `domain-manager/app/AndroidManifest.xml`, `DomainTlsActivity.java`
- Create: `patches-aosp/vendor/tesla-android/0008-Add-certificate-renewal-and-expiry-alerts.patch`

**Interfaces:**

- Consumes Task 6 `AcmeOrderRunner` and DuckDNS profile credentials.
- Produces `SecretStore.saveDuckDnsToken()`, `loadDuckDnsToken()`, and `deleteProfileSecrets()`.
- Produces constants `RENEW_BEFORE_DAYS = 30`, `WARNING_DAYS = new int[] {14, 7, 3, 0}`, and `PERIODIC_JOB_ID = 0x5441534c`.
- Produces a persisted daily JobScheduler job plus bounded one-off retry jobs.

- [ ] **Step 1: Write renewal and retry policy tests**

```java
@Test public void renewalBeginsAtThirtyDays() {
    assertThat(policy.needsRenewal(now.plus(Duration.ofDays(31)))).isFalse();
    assertThat(policy.needsRenewal(now.plus(Duration.ofDays(30)))).isTrue();
}

@Test public void warningThresholdsAreFourteenSevenThreeAndExpired() {
    assertThat(policy.warningFor(Duration.ofDays(14))).isEqualTo(Warning.DAYS_14);
    assertThat(policy.warningFor(Duration.ofDays(7))).isEqualTo(Warning.DAYS_7);
    assertThat(policy.warningFor(Duration.ofDays(3))).isEqualTo(Warning.DAYS_3);
    assertThat(policy.warningFor(Duration.ZERO)).isEqualTo(Warning.EXPIRED);
}

@Test public void retryHonorsServerDelayAndCapsAtTwentyFourHours() {
    assertThat(policy.retryDelay(5, Duration.ofHours(30)))
            .isEqualTo(Duration.ofHours(24));
    assertThat(policy.retryDelay(0, Duration.ZERO).compareTo(Duration.ofHours(1)))
            .isAtLeast(0);
}
```

- [ ] **Step 2: Verify scheduling tests fail**

```bash
m TeslaDomainManagerTests TeslaDomainManagerDeviceTests
atest TeslaDomainManagerTests TeslaDomainManagerDeviceTests
```

Expected: renewal, Keystore, and notification tests fail because the production components are absent.

- [ ] **Step 3: Encrypt provider secrets with Android Keystore**

Generate non-exportable AES-256-GCM key alias `tesla-domain-manager-secrets-v1`, require app UID access, and store only IV+ciphertext in device-protected no-backup storage:

```java
KeyGenerator generator = KeyGenerator.getInstance(
        KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
generator.init(new KeyGenParameterSpec.Builder(
        KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
        .setKeySize(256)
        .build());
SecretKey key = generator.generateKey();
```

Bind associated data to package name, profile ID, and hostname. Delete ciphertext and account key aliases on profile deletion or Restore Default.

- [ ] **Step 4: Implement daily and boot scheduling**

```java
JobInfo periodic = new JobInfo.Builder(PERIODIC_JOB_ID,
        new ComponentName(context, RenewalJobService.class))
        .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
        .setPeriodic(Duration.ofHours(24).toMillis(), Duration.ofHours(2).toMillis())
        .setPersisted(true)
        .build();
jobScheduler.schedule(periodic);
```

`BootReceiver` schedules the job and an immediate check after `LOCKED_BOOT_COMPLETED`/`BOOT_COMPLETED`. `TimeTrust` requires automatic time enabled, wall clock at or after 2024-01-01 UTC, and active validated internet before issuance; otherwise the job exits with `WAITING_FOR_TIME` without touching the active profile.

- [ ] **Step 5: Implement renewal, backoff, and notifications**

The job reads the public active profile and evaluates expiry warnings for every profile. It makes no provider request for default or manual profiles and calls Task 6 issuance only for DuckDNS automatic profiles inside the 30-day window. Use exponential `2^attempt` hours plus 0-30 minutes cryptographic jitter, floor one hour, cap 24 hours, and take the greater of local backoff or ACME `Retry-After` before capping.

Create notification channels `TLS status` and `TLS action required`. Automatic errors show stable code plus next retry time. Manual profiles say `Import a replacement certificate`; expired profiles use an ongoing high-priority notification. Notification intents open `DomainTlsActivity` and never contain credentials.

- [ ] **Step 6: Run app scheduling and on-device secret tests**

```bash
m TeslaDomainManager TeslaDomainManagerTests TeslaDomainManagerDeviceTests
atest TeslaDomainManagerTests TeslaDomainManagerDeviceTests
adb shell cmd jobscheduler run -f com.teslaandroid.domainmanager 1413567308
```

Expected: all tests pass; forced default/manual jobs make no provider request; an automatic fake profile renews only inside 30 days; app backup output contains no token; logcat contains no test token or challenge.

- [ ] **Step 7: Export and commit the renewal patch**

```bash
git -C aosptree/vendor/tesla-android format-patch -1 --stdout > \
  patches-aosp/vendor/tesla-android/0008-Add-certificate-renewal-and-expiry-alerts.patch
git add patches-aosp/vendor/tesla-android/0008-Add-certificate-renewal-and-expiry-alerts.patch
git commit -m "android15: renew DuckDNS TLS certificates automatically"
```

### Task 8: Attach Repository Validation and Trigger One Clean Image Build

**Files:**

- Modify: `tools/check-android15-port.sh`
- Modify: `tools/tests/test_android15_runtime_tls.py`
- Modify: `.github/workflows/build-android15-rpi4.yml`

**Interfaces:**

- Consumes all implementation patches from Tasks 2-7.
- Produces a local validation gate and cache baseline `android-platform-15.0.0_r3-tesla-2026.22.1-runtime-domain-tls-v1`.
- Produces exactly one `[build-image]` commit after all host/static checks pass.

- [ ] **Step 1: Run the full repository test and observe only the baseline failure**

```bash
python3 -m unittest -v tools/tests/test_android15_runtime_tls.py
```

Expected: patch, secret-boundary, endpoint, and patch-prefix tests pass; only the workflow-baseline test fails.

- [ ] **Step 2: Add the suite to the existing validator**

Insert after the runtime-performance suite:

```bash
if ! python3 -m unittest -v tools/tests/test_android15_runtime_tls.py; then
  failures=$((failures + 1))
fi
```

Correct the existing patch-prefix regex to `r"(\d{4})-"` so the validator enforces the same rule as the new Python suite.

- [ ] **Step 3: Bump the Android output contract**

Replace the workflow value with:

```yaml
ANDROID_BUILD_BASELINE: android-platform-15.0.0_r3-tesla-2026.22.1-runtime-domain-tls-v1
```

Do not change capacity thresholds, build credentials, the 2026.22.1 manifest pin, or artifact packaging.

- [ ] **Step 4: Run complete host/static verification**

```bash
python3 -m unittest -v tools/tests/test_android15_build_cache.py
python3 -m unittest -v tools/tests/test_android15_ih8sn_patch.py
python3 -m unittest -v tools/tests/test_android15_connectivity_fakewifi_patch.py
python3 -m unittest -v tools/tests/test_android15_runtime_performance.py
python3 -m unittest -v tools/tests/test_android15_runtime_tls.py
bash -n tools/check-android15-port.sh
bash tools/check-android15-port.sh
git diff --check
```

Expected: all Python tests pass; shell syntax exits 0; validator ends `Android 15 Raspberry Pi baseline validation passed.`; Git whitespace check is clean.

- [ ] **Step 5: Check every patch against freshly synced sources**

```bash
bash unfold_aosp.sh
git -C aosptree/vendor/tesla-android status --short
git -C aosptree/system/netd status --short
git -C aosptree/system/sepolicy status --short
git -C aosptree/packages/apps/Settings status --short
git -C aosptree/external/tesla-android-configuration-manager status --short
```

Expected: `unfold_aosp.sh` applies every patch without rejects; each project shows only the intended committed patch changes; no `.rej` file exists.

- [ ] **Step 6: Build focused Android modules before the full image**

```bash
source aosptree/build/envsetup.sh
lunch tesla_android_rpi4-trunk_staging-userdebug
m TeslaDomainManager tesla_tls_installer tesla-android-configuration-manager \
  Settings netd tesla_tls_installer_tests TeslaDomainManagerTests
```

Expected: all modules compile and link under Android 15; AIDL Java/NDK backends, policy, privileged-app allowlist, and init service definitions pass build-time checks.

- [ ] **Step 7: Review the complete implementation diff**

```bash
git status --short
git diff --stat 6d2d37c
git diff 6d2d37c -- \
  patches-aosp/vendor/tesla-android \
  patches-aosp/system/netd \
  patches-aosp/system/sepolicy \
  patches-aosp/packages/apps/Settings \
  patches-aosp/external/tesla-android-configuration-manager \
  tools .github/workflows/build-android15-rpi4.yml
```

Expected: only planned patch series, tests, validator, and workflow baseline differ; GApps, media compatibility, touchscreen, audio, userdata, boot, and source-pin fixes remain unchanged.

- [ ] **Step 8: Commit and push the image-triggering integration**

```bash
git add tools/check-android15-port.sh tools/tests/test_android15_runtime_tls.py \
  .github/workflows/build-android15-rpi4.yml
git commit -m "[build-image] android15: enable runtime domain and TLS management"
git push origin android-15-bringup
```

Expected: validation and exactly one image build start for this commit.

### Task 9: Verify CI, Flash, and Exercise Hardware Acceptance

**Files:**

- Verify only: no repository changes unless a failing acceptance test identifies a specific defect.

**Interfaces:**

- Consumes the pushed integration commit, GitHub Actions artifact, an RPi4, ADB, and UART.
- Produces evidence for boot, default migration, Settings/manual import, DuckDNS renewal, DNS interception, rollback, secret confinement, streaming, touch latency, and media-app regression safety.

- [ ] **Step 1: Watch validation and image workflows**

```bash
validation_run_id="$(gh run list --workflow android15-port-validation.yml \
  --branch android-15-bringup --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$validation_run_id" --exit-status

build_run_id="$(gh run list --workflow build-android15-rpi4.yml \
  --branch android-15-bringup --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$build_run_id" --exit-status
```

Expected: both workflows succeed; build runs in fresh mode because the baseline changed.

- [ ] **Step 2: Verify and download the artifact**

```bash
gh api "repos/chilman408/android-raspberry-pi/actions/runs/$build_run_id/artifacts" \
  --jq '.artifacts[] | [.name, .expired, .size_in_bytes] | @tsv'
gh run download "$build_run_id" --dir artifacts/runtime-domain-tls
sha256sum artifacts/runtime-domain-tls/*
```

Expected: one non-expired, nonempty `TeslaAndroid-15-rpi4-<12-char-sha>` bundle and stable local checksums.

- [ ] **Step 3: Flash and capture first-boot evidence**

Flash the generated image, boot with UART attached, and then run:

```powershell
adb wait-for-device
adb shell getprop sys.boot_completed
adb shell getprop persist.tesla-android.tls.primary_hostname
adb shell getprop persist.tesla-android.tls.fullscreen_hostname
adb shell su 0 stat -c '%a %U %G %n' /data/vendor/tesla-android/tls/active/privkey.pem
adb shell curl -k --resolve device.teslaandroid.com:443:172.16.0.1 `
  https://device.teslaandroid.com/api/domainProfile
```

Expected: boot completes; default names are selected; key is `600 root root`; public profile reports `DEFAULT` and contains no secret fields.

- [ ] **Step 4: Exercise manual Synology and generic profiles**

Use test PKCS#12/PEM bundles whose names cover dedicated test hostnames. Confirm valid import activates, persists across reboot, and displays `Manual renewal`. Then try wrong-key, wrong-SAN, expired, untrusted, weak-key, encrypted-PEM, and oversized fixtures.

Expected: every invalid fixture is rejected before lighttpd/tether reload; the prior hostname continues serving; no password or key bytes appear in logcat.

- [ ] **Step 5: Exercise DuckDNS issuance and forced renewal**

Use a dedicated test DuckDNS registration/token stored only through the Settings UI. Record the initial fingerprint, force the job inside the 30-day test window, and record the replacement:

```powershell
adb shell cmd jobscheduler run -f com.teslaandroid.domainmanager 1413567308
adb shell dumpsys jobscheduler com.teslaandroid.domainmanager
adb shell dumpsys notification --noredact
adb logcat -d | Select-String 'TeslaDomainManager|tesla_tls_installer'
```

Expected: DNS-01 succeeds serially, the fingerprint changes, the new key remains `0600`, lighttpd and tether DNS recover automatically, and no full image update occurs.

- [ ] **Step 6: Verify Tesla-client DNS and HTTPS routing**

From a client connected to the TeslaAndroid hotspot, query and open the active custom hostname:

```bash
dig @172.16.0.1 active-test-hostname.duckdns.org A
curl --resolve active-test-hostname.duckdns.org:443:172.16.0.1 \
  https://active-test-hostname.duckdns.org/api/health
```

Expected: DNS returns `104.248.101.213`; redirected HTTPS validates the public chain and returns HTTP 200; the browser URL remains the custom hostname.

- [ ] **Step 7: Inject apply failures and verify rollback**

Run the engineering build's failure hooks one at a time for lighttpd reload, tether stop, tether start, DNS probe, and HTTPS probe. After each attempt, read the active profile and open its URL.

Expected: the old generation, properties, lighttpd configuration, and DNS mappings return together; a rollback-verification failure selects TeslaAndroid Default once and posts a persistent critical notification.

- [ ] **Step 8: Run regression acceptance for the project's primary functions**

Verify:

```text
Android Settings opens without a crash.
TeslaAndroid stable and beta frontends load from the active hostname.
Virtual display streams continuously for 30 minutes.
Virtual touchscreen drag tests sustain at least 55 delivered events/second, lose less than 1% of injected events, and stay below 50 ms median and 100 ms p95 browser-to-uinput latency.
YouTube playback works.
Netflix starts protected playback without the former AV1 black-screen loop.
Previously accepted Play Store compatibility for Netflix, Max, Disney+, Hulu, and Viki is unchanged.
Browser audio, GPS WebSocket, display WebSocket, and touchscreen WebSocket remain connected.
```

Expected: no regression versus commit `bf1e8b8`; UART and logcat show no new fatal loop, ANR loop, SELinux denial affecting the feature, or repeated lighttpd/tether restart.

- [ ] **Step 9: Record acceptance evidence**

Append the workflow URLs, artifact name/checksum, device build fingerprint, active-profile fingerprints, default/manual/DuckDNS results, rollback matrix, and regression results to the implementation task handoff. Do not commit tokens, passwords, imported keys, certificates, TXT values, or unredacted request URLs.

Expected: the evidence is sufficient to reproduce every success claim without exposing credentials.
