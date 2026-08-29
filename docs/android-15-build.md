# Android 15 Raspberry Pi 4 build

The `android-15-bringup` branch tracks Tesla Android's Android 15 port on the
validated AOSP `android-platform-15.0.0_r3` and Raspberry Pi Linux 6.6
baseline.

## Validation

`.github/workflows/android15-port-validation.yml` performs two checks:

1. A fast manifest/BSP compatibility check.
2. A shallow checkout of every project that has a patch series, followed by
   `git am` of all patches.

The second check catches stale patches without downloading the complete Android
tree.

## Image builder

Run the **Build Android 15 Raspberry Pi 4 image** workflow manually. It requires
a self-hosted Linux x64 runner with the label `android-build`, Ubuntu 22.04,
at least 32 GiB RAM, 8 GiB swap, and 300 GiB free workspace storage.

Successful runs upload:

- `*-flashable.img.zst`: the compressed raw Raspberry Pi 4 SD-card image.
- `*-fastboot-images.tar.gz`: individual fastboot images.
- `*-ota.zip`: the OTA package.
- `SHA256SUMS`: integrity hashes.

The workflow can optionally publish the same files as a GitHub prerelease.

## Credentials

Without repository secrets, the workflow intentionally creates a development
image using public AOSP test signing keys and short-lived self-signed TLS
certificates. The image can boot and be flashed, but the Tesla browser will not
trust its HTTPS endpoint.

Production builds require these base64-encoded GitHub Actions secrets:

- `TA_RELEASEKEY_PK8_B64`
- `TA_RELEASEKEY_X509_B64`
- `TA_PLATFORM_PK8_B64`
- `TA_PLATFORM_X509_B64`
- `TA_DEVICE_TLS_FULLCHAIN_B64`
- `TA_DEVICE_TLS_PRIVKEY_B64`
- `TA_FULLSCREEN_TLS_FULLCHAIN_B64`
- `TA_FULLSCREEN_TLS_PRIVKEY_B64`

Never commit private signing keys or TLS private keys to the repository.
