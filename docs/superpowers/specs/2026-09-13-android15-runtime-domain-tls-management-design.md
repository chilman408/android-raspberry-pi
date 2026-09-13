# Android 15 Runtime Domain and TLS Management Design

**Date:** 2026-09-13
**Status:** Approved in chat; pending written-spec review
**Target branch:** `android-15-bringup`

## Context

TeslaAndroid currently embeds the HTTPS certificate, private key, and hostname-specific lighttpd configuration in the read-only vendor image. The default endpoints are `device.teslaandroid.com` and `fullscreen.device.teslaandroid.com`. Replacing an expired certificate therefore requires building and flashing a complete image, even though the Android system itself has not changed.

The local Tesla browsing path also depends on more than lighttpd. The netd tethering patch currently adds hard-coded dnsmasq mappings for the TeslaAndroid hostnames and redirects the fixed interception address to the Raspberry Pi. A runtime hostname change that updates only lighttpd and its certificate would appear correct on the Pi but would not resolve correctly from the Tesla browser.

This design adds a native Android settings flow for selecting a domain profile, provisioning its TLS certificate, applying the hostname to both the web server and the tethered-client DNS path, monitoring expiry, and renewing DuckDNS certificates without rebuilding the image.

## Goals

- Keep the existing TeslaAndroid endpoints as the first-boot and factory-reset defaults.
- Let a user opt into a custom domain at runtime from Android Settings.
- Automatically obtain and renew certificates for DuckDNS domains by using ACME DNS-01.
- Let Synology DDNS users and other custom-domain users import a certificate manually.
- Validate the complete serving path before committing a profile.
- Roll back automatically if lighttpd, tether DNS, or local HTTPS validation fails.
- Keep DNS credentials and TLS private keys out of logs, browser-accessible APIs, backups, and ordinary app storage.
- Warn the user before a certificate expires, including profiles that require manual replacement.
- Preserve the currently working profile until a replacement is proven usable.

## Non-goals

- The device will not renew a certificate for `teslaandroid.com` unless it is given credentials controlled by that domain's owner. The default profile therefore remains manufacturer-managed rather than device-renewed.
- The first version will not automate Synology account login, DSM certificate export, or Synology DDNS DNS-01 updates.
- The first version will not support arbitrary DNS providers through user-authored scripts or plugins.
- The first version will not expose certificate, private-key, ACME, or provider-token operations through the existing browser-facing Configuration Manager REST service.
- This work does not replace TeslaAndroid's fixed interception IP or redesign its offline networking model.
- This work does not make a manually imported certificate renewable. Manual profiles receive expiry monitoring and replacement prompts only.

## User-visible behavior

Android Settings gains a `TeslaAndroid > Domain & TLS` entry. The entry launches a small privileged Tesla Domain Manager activity so the security-sensitive workflow is isolated from the upstream AOSP Settings codebase.

The page shows:

- active profile and primary URL;
- optional fullscreen URL;
- certificate trust state, issuer, subject names, and expiry date;
- last successful renewal or import time;
- next automatic renewal check, when applicable;
- a provider-specific status and actionable error;
- `Test configuration`, `Apply`, and `Restore TeslaAndroid Default` actions.

The available profiles are:

1. **TeslaAndroid Default**
   - Primary hostname: `device.teslaandroid.com`.
   - Fullscreen hostname: `fullscreen.device.teslaandroid.com`.
   - Uses the certificates shipped by the image.
   - Is never described as device-renewable unless domain-owner credentials are actually configured by a future release.

2. **DuckDNS Automatic**
   - Accepts a registered DuckDNS hostname and DuckDNS token.
   - Uses ACME DNS-01 to obtain and renew a publicly trusted certificate.
   - Stores the token encrypted and renews without later user interaction.

3. **Synology Manual Import**
   - Accepts a Synology DDNS hostname and certificate exported from DSM.
   - Supports PKCS#12 or PEM import.
   - Monitors expiry but requires the user to import a replacement certificate.

4. **Generic Manual Import**
   - Accepts another custom hostname and PKCS#12 or PEM certificate material.
   - Monitors expiry but requires manual replacement.

Switching profiles is opt-in. An upgrade does not silently replace the active hostname. Factory reset returns to the TeslaAndroid Default profile.

## Input rules

Hostnames are converted to lower-case ASCII using IDNA before storage. The manager rejects schemes, paths, ports, IP literals, wildcard input, underscores, trailing dots, empty labels, invalid DNS labels, and names longer than 90 ASCII bytes. The 90-byte first-version limit allows each active hostname to be transported through an Android property without truncation.

The primary and fullscreen hostnames must be different when both are supplied. The fullscreen hostname is optional. When omitted, fullscreen navigation uses the primary origin and its fullscreen route instead of a second host.

For DuckDNS Automatic, the primary hostname must be a registered `<label>.duckdns.org` name. The optional fullscreen name must be covered by the certificate requested for that profile. The manager verifies control with the supplied token before it creates an ACME order. Tokens are never accepted in a hostname or URL field.

Manual certificates must be currently valid, have a chain trusted by the Android system trust store, contain every configured hostname in subjectAltName, permit TLS server authentication, and match the supplied private key. Common-name-only certificates are rejected.

## Architecture

### Privileged Tesla Domain Manager

A new privileged system app owns the Android Settings UI, validation messages, Android document-picker import flow, renewal scheduling, notification channels, and ACME/DuckDNS network orchestration. A small patch to AOSP Settings adds only the entry point and availability logic.

The app uses a signature permission for all internal service calls. It does not export activities, receivers, providers, or services other than the explicitly exported Settings entry activity, which still requires the signature permission when invoked outside Settings.

The DuckDNS token and ACME account key are encrypted with a non-exportable Android Keystore key bound to the app UID. The app's scheduled renewal job decrypts the token only for an active renewal attempt. Passwords entered to open PKCS#12 files are held in memory for that import and are never persisted.

### Constrained TLS installer service

A dedicated, SELinux-confined native service performs the operations that must cross the Android app sandbox:

- create a TLS server private key and certificate-signing request;
- install a validated certificate chain into a staging generation;
- render and validate the lighttpd TLS include;
- atomically select an active generation;
- set the validated runtime hostname properties;
- request a lighttpd graceful reload;
- request a controlled tethering DNS reload;
- run the final loopback HTTPS health check;
- roll back to the preceding generation and hostname set.

The service has a signature-protected Binder interface and accepts requests only from the Tesla Domain Manager UID. It runs with the minimum Linux capabilities and SELinux permissions needed for these exact operations. The service does not make outbound network requests and never returns an exportable server private key. It returns a CSR to the manager and accepts only the resulting public certificate chain.

This separation keeps the DuckDNS token out of the root-capable service and keeps the TLS server key out of the Android UI process.

### Runtime TLS store

Mutable material lives under `/data/vendor/tesla-android/tls/`, not `/vendor`:

```text
/data/vendor/tesla-android/tls/
  state.json
  generations/
    <generation-id>/
      profile.json
      fullchain.pem
      privkey.pem
      lighttpd-tls.conf
  active -> generations/<generation-id>
  previous -> generations/<generation-id>
  staging/
```

The directory is not included in Android backup. Private keys are `0600`, owned by root, labeled only for the installer and lighttpd domains, and never copied into `state.json`. Certificate chains and non-secret profile metadata may be read by the manager through narrowly scoped Binder methods.

Only the active, previous, and incomplete staging generation are retained. Older generations are removed after the active profile passes its post-commit health window. Interrupted staging data is discarded at the next boot.

### Boot integration and vendor fallback

An init bootstrap action runs after `/data` is available and before lighttpd starts. On a new installation it creates a runtime generation whose configuration references the certificates shipped in `/vendor/tesla-android/lighttpd/certificates/` and selects the TeslaAndroid Default hostnames. On later boots it validates the selected generation and leaves it active.

If the active runtime state is missing, corrupt, or invalid, bootstrap restores the immutable TeslaAndroid Default configuration. If the image contains self-signed development certificates because release credentials were unavailable at build time, Settings clearly labels the default certificate as untrusted and shows its real expiry date; it does not claim that renewal is available.

The base lighttpd configuration includes the generated active TLS include after bootstrap has made that include available. lighttpd is not started against a partially written or absent runtime include.

### Tethered-client DNS integration

The active ASCII hostnames are also persisted in two SELinux-labeled Android properties:

- `persist.tesla-android.tls.primary_hostname`
- `persist.tesla-android.tls.fullscreen_hostname`

Only the installer service can change them. The patched netd tethering code reads and revalidates the properties whenever it builds the dnsmasq argument list. Empty or invalid values fall back to the two TeslaAndroid Default hostnames.

For a custom profile, netd maps the exact configured hostname or hostnames to TeslaAndroid's existing interception address, `104.248.101.213`. The existing wlan0 destination-NAT path then redirects that address to the Raspberry Pi. Custom `www` aliases are not created implicitly because they would not necessarily be covered by the certificate. The default profile retains its existing compatibility aliases.

Applying a changed hostname requests a controlled restart of the tether DNS component. Existing hotspot clients may experience a brief DNS interruption, but the hotspot configuration and saved Wi-Fi state are preserved. Failure to start the new DNS configuration is an apply failure and triggers full profile rollback.

### Frontend hostname discovery

Web code must not embed either TeslaAndroid hostname. Primary navigation uses the current origin. Code that needs the fullscreen origin reads a read-only runtime profile endpoint containing only the active public hostnames and profile mode. This endpoint never exposes paths, tokens, key material, imported filenames, or provider credentials.

The public endpoint is deliberately separate from all mutation APIs. Certificate and domain changes remain possible only from the native Settings activity.

## DuckDNS automatic certificate lifecycle

The user enters the DuckDNS hostname and token, then selects `Test configuration`. The manager calls the DuckDNS API over a system-trusted HTTPS connection and performs a disposable TXT ownership probe, waits until the authoritative answer contains the probe, then clears it. The probe never changes the DuckDNS A or AAAA records and does not change the active TeslaAndroid profile. Because DuckDNS provides a single TXT value per registration, the UI warns that enabling automatic renewal reserves that TXT value for ACME challenges.

For issuance, the manager:

1. asks the installer service to generate a new server key and CSR for the normalized hostname set in a staging generation;
2. creates or reuses the profile's ACME account key from Android Keystore;
3. creates an ACME order against the configured public certificate authority;
4. publishes one required DNS-01 TXT value through the DuckDNS API;
5. waits for authoritative DNS to return the expected TXT value before asking ACME to validate that authorization;
6. clears the TXT value and repeats steps 4 and 5 serially for every remaining authorization, because one DuckDNS registration exposes a single TXT value across its sub-subdomains;
7. finalizes the order with the CSR;
8. sends the returned certificate chain and public metadata to the installer service;
9. invokes the common apply transaction.

ACME protocol behavior is implemented behind an internal provider interface and covered by deterministic protocol tests. The implementation plan may select a pinned Java/Kotlin dependency or a small vendored client, but the selected implementation must support modern ACME v2, DNS-01, nonce retry, badNonce handling, Retry-After, alternate chains, account-key rollover safety, and certificate revocation. The dependency choice does not alter the interfaces or security boundaries in this design.

Production ACME is used only after configuration validation. Automated tests and the UI's preflight path use a local or staging ACME environment so repeated failures cannot exhaust production rate limits.

## Manual certificate import

Synology Manual Import and Generic Manual Import use Android's document picker, so the manager receives scoped access only to files the user selected.

PKCS#12 (`.p12` or `.pfx`) is preferred because it packages the leaf certificate, chain, and key. The manager prompts for its password, validates and extracts the material in memory, sends the chain and key through a one-shot protected import call, then clears its temporary buffers. The password is not stored.

PEM import accepts one full-chain file and one unencrypted PKCS#8 private-key file. Encrypted standalone PEM keys are rejected in the first version; users can convert them to PKCS#12 instead. Files containing unrelated keys, multiple ambiguous leaf certificates, unsupported key algorithms, weak keys, malformed chains, or extra non-certificate content are rejected with a precise error.

Before enabling `Apply`, the manager and installer jointly verify:

- key and leaf-certificate public keys match;
- all configured hostnames are present as exact or standards-compliant wildcard SAN matches;
- the leaf is valid for TLS server authentication;
- the chain builds to the Android system trust store;
- the certificate is currently valid with a five-minute clock-skew allowance;
- the certificate has at least 72 hours of remaining validity;
- lighttpd can parse the resulting certificate, key, and configuration.

Imported profiles are labeled `Manual renewal`. Replacing their certificate repeats the complete staging and apply transaction. Importing a new certificate never overwrites the currently active working generation in place.

## Common apply transaction

All profile types use one transaction:

1. **Normalize and validate inputs.** Reject invalid hostnames, provider mismatches, missing SANs, untrusted chains, expired material, and key mismatches.
2. **Stage immutable material.** Create a new generation with restrictive ownership, modes, and SELinux labels. Flush files and the parent directory before proceeding.
3. **Validate lighttpd offline.** Render a complete candidate configuration and run `lighttpd -tt` against it without disturbing the running server.
4. **Test candidate HTTPS locally.** Start an isolated loopback-only validation listener, connect with the candidate hostname as SNI, verify the certificate chain and hostname, request the health resource, and stop the listener.
5. **Select the generation.** Save the old active generation and hostname properties as `previous`, atomically select the new generation, and set the validated hostname properties.
6. **Reload lighttpd.** Send the supported graceful reload signal and require the service to remain alive with the new certificate and host routing.
7. **Reload tether DNS.** Rebuild dnsmasq arguments from the new properties and confirm the tether DNS component is healthy.
8. **Verify the serving path.** Resolve the new hostname through the local tether DNS path, connect to the redirected HTTPS endpoint with SNI and normal trust validation, and require a successful health response.
9. **Commit.** Record the profile metadata and successful check time, then retain the preceding generation for the rollback window.

Steps 5 through 8 are one logical commit. If any step fails, the installer restores the preceding generation and hostname properties, gracefully reloads lighttpd again, restores tether DNS, verifies the preceding HTTPS path, and records the failed candidate without exposing secret material.

If rollback verification also fails, bootstrap selects the TeslaAndroid Default profile and raises a persistent high-priority notification. The service never loops indefinitely between two broken generations.

## Renewal scheduling and expiry handling

DuckDNS profiles are checked:

- after boot once time synchronization and network connectivity are available;
- once every 24 hours through JobScheduler with network and battery constraints appropriate for an externally powered Raspberry Pi;
- when the user opens Domain & TLS or selects `Check now`.

Renewal begins when the certificate has 30 days or less remaining. A successful renewal uses a new server private key and the common apply transaction. Failure keeps the existing certificate and schedules exponential retry with jitter, bounded between one hour and 24 hours. Rate-limit responses honor `Retry-After` and are not retried aggressively.

The manager posts warnings at 14, 7, and 3 days before expiry and a persistent critical warning at expiry. Automatic-profile warnings include the last concrete provider or ACME error. Manual-profile warnings tell the user to export and import a replacement certificate; they never say that an automatic renewal is underway.

Certificate validity decisions wait for trusted network time after boot. If wall-clock time is implausible or moves backward, issuance and activation pause, the current known-good profile remains selected, and Settings reports `Waiting for correct date and time`.

Changing either custom hostname is treated as a new certificate request or import. The old profile continues serving until a certificate covering the new name passes the full transaction.

## Failure handling

- Loss of internet connectivity does not disable a still-valid active certificate.
- A DuckDNS token failure leaves the active profile untouched and prompts for a replacement token without displaying the old token.
- DNS propagation timeout abandons the ACME attempt and retries later; it never activates an unvalidated certificate.
- Production ACME rate limiting is surfaced with the next permitted retry time.
- A missing or malformed imported chain is rejected before any server reload.
- A lighttpd syntax or key-loading failure is rejected during offline validation.
- A tether DNS reload failure rolls back both DNS properties and TLS configuration.
- An unexpected reboot during staging discards staging. A reboot during selection chooses the last generation whose commit marker and fsync completed.
- If the active certificate expires, lighttpd is not silently switched to an unrelated certificate. The active profile remains visible as expired so the user can restore default or import a valid replacement.

Logs use stable error codes and certificate fingerprints. They may include public hostnames, issuer names, expiry dates, ACME directory hostnames, and HTTP status classes. They must redact provider tokens, PKCS#12 passwords, ACME JWS bodies, DNS TXT challenge values, private-key paths supplied by users, private keys, and certificate file contents.

## Security requirements

- All profile mutation requires the TeslaAndroid signature permission.
- The browser-facing Configuration Manager cannot call or proxy the mutation Binder interface.
- Server private keys are generated on-device for automatic profiles and never leave the installer service.
- Imported keys become root-owned `0600` files immediately and temporary copies are destroyed after import.
- DuckDNS tokens and ACME account keys are encrypted using Android Keystore and excluded from backup.
- TLS and provider traffic uses normal system trust validation; certificate-validation bypasses are prohibited.
- Hostnames are normalized once and passed to lighttpd and dnsmasq through structured renderers that reject control characters and configuration delimiters.
- The installer uses fixed directories, `openat`-style path confinement, no user-controlled filenames, no shell interpolation, and atomic rename/fsync operations.
- SELinux denies other apps, shell, the browser server, and unrelated vendor services access to tokens and private keys.
- Imported server keys are limited to RSA 2048, 3072, or 4096 bits and ECDSA P-256 or P-384. Certificate signatures must use SHA-256 or stronger; MD5, SHA-1, DSA, and unsupported curves are rejected.

## Migration and compatibility

On the first boot after upgrading, bootstrap converts the currently shipped hostnames and certificate paths into a TeslaAndroid Default runtime generation. No custom settings are assumed, and the web interface remains available at the same URLs.

The default netd mappings and their existing `www` aliases remain unchanged. Custom profiles add only their exact configured names. Restoring default reverses the lighttpd generation, the runtime profile endpoint, and netd properties in the same transaction.

The frontend change uses current-origin URLs wherever possible. Older cached frontend assets that still reference the default hostname continue to work only in the default profile; activation of a custom profile therefore includes cache-control/version changes that force the hostname-neutral frontend bundle to load.

The existing image-build certificate injection remains supported for official or private builds. It seeds the default generation but is no longer the storage location for an active custom profile.

## Verification strategy

### Host and unit tests

- IDNA normalization, length bounds, invalid-label rejection, and lighttpd/dnsmasq injection cases.
- Exact and wildcard SAN matching, EKU checks, expiry boundaries, chain building, and private-key matching.
- PKCS#12 and PEM acceptance, password failure, ambiguity, malformed material, and weak-key rejection.
- ACME v2 flows against a deterministic test server, including badNonce, Retry-After, challenge timeout, rejected authorization, alternate chain, and rate limiting.
- DuckDNS API success, wrong token, network timeout, TXT propagation delay, and redacted logging.
- Renewal threshold, clock-not-synchronized behavior, exponential backoff, and notification thresholds.
- Runtime profile endpoint output contains only public non-secret fields.

### Device integration tests

- First boot with release credentials and with generated self-signed development credentials.
- Upgrade migration from the embedded-certificate layout.
- Apply a DuckDNS profile, reboot, and confirm persistence.
- Import valid Synology PKCS#12 and PEM material.
- Reject a certificate with the wrong SAN, wrong key, expired date, or untrusted chain without disrupting the active profile.
- Confirm private-key modes, ownership, backup exclusion, Binder permission checks, and SELinux denials from shell, ordinary apps, and the Configuration Manager server.
- Confirm `lighttpd -tt`, graceful reload, existing-connection behavior, SNI selection, and loopback health validation.
- Confirm dnsmasq receives the runtime names, the Tesla client path resolves them to the interception address, NAT reaches the Pi, and HTTPS validates end to end.
- Force failures at every transaction boundary and confirm deterministic rollback after both normal failure and power loss.
- Confirm Restore TeslaAndroid Default restores lighttpd, DNS, frontend profile discovery, and aliases.
- Advance time through 30-, 14-, 7-, 3-, and 0-day boundaries and verify renewal or manual-import messaging.
- Confirm an automatically renewed certificate activates without rebuilding or reflashing the image.

### Real Raspberry Pi acceptance

A release candidate is accepted only after it passes on an RPi4 with the TeslaAndroid hotspot and a real browser client:

1. the default profile works after upgrade;
2. a DuckDNS profile obtains a publicly trusted certificate and becomes reachable through the tethered-client path;
3. a forced renewal changes the certificate fingerprint without an image update;
4. a valid Synology-exported certificate imports and survives reboot;
5. invalid replacement material leaves the previous site usable;
6. domain switching does not regress streaming, virtual-touch latency, Android Settings stability, or media-app compatibility.

## Rollout

The feature ships disabled only when bootstrap cannot establish a safe runtime store. Otherwise Domain & TLS is visible, with TeslaAndroid Default active. DuckDNS Automatic is marked automatic; Synology and Generic Import are marked manual everywhere in the UI.

The implementation is split so the immutable default migration, runtime lighttpd include, netd property support, native installer, Settings entry, provider UI, ACME flow, imports, and end-to-end tests can be reviewed independently. The implementation plan must preserve the transaction boundaries and security separation defined here.

No production image is published until the default-profile upgrade path, custom-profile rollback, secret redaction, and tethered-client DNS path have all passed on hardware.
