#!/usr/bin/env bash
set -euo pipefail

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
aosp_root="$source_root/aosptree"
vendor_root="$aosp_root/vendor/tesla-android"
security_root="$aosp_root/build/make/target/product/security"

if [[ ! -d "$vendor_root" || ! -d "$security_root" ]]; then
  echo "ERROR: run unfold_aosp.sh before preparing build credentials" >&2
  exit 1
fi

decode_secret() {
  local value="$1"
  local target="$2"
  printf '%s' "$value" | base64 --decode > "$target"
}

signing_root="$vendor_root/signing"
mkdir -p "$signing_root"

if [[ -n "${TA_RELEASEKEY_PK8_B64:-}" && -n "${TA_RELEASEKEY_X509_B64:-}" ]]; then
  decode_secret "$TA_RELEASEKEY_PK8_B64" "$signing_root/releasekey.pk8"
  decode_secret "$TA_RELEASEKEY_X509_B64" "$signing_root/releasekey.x509.pem"
  cp "$signing_root/releasekey.pk8" "$security_root/testkey.pk8"
  cp "$signing_root/releasekey.x509.pem" "$security_root/testkey.x509.pem"
  echo "Using release signing credentials supplied through GitHub Actions secrets."
else
  cp "$security_root/testkey.pk8" "$signing_root/releasekey.pk8"
  cp "$security_root/testkey.x509.pem" "$signing_root/releasekey.x509.pem"
  echo "WARNING: release signing secrets are absent; this image will use public AOSP test keys."
fi

if [[ -n "${TA_PLATFORM_PK8_B64:-}" && -n "${TA_PLATFORM_X509_B64:-}" ]]; then
  decode_secret "$TA_PLATFORM_PK8_B64" "$signing_root/platform.pk8"
  decode_secret "$TA_PLATFORM_X509_B64" "$signing_root/platform.x509.pem"
else
  cp "$signing_root/releasekey.pk8" "$signing_root/platform.pk8"
  cp "$signing_root/releasekey.x509.pem" "$signing_root/platform.x509.pem"
fi

tls_root="$vendor_root/services/lighttpd/certificates"
device_root="$tls_root/device.teslaandroid.com"
fullscreen_root="$tls_root/fullscreen.device.teslaandroid.com"
mkdir -p "$device_root" "$fullscreen_root"

if [[ -n "${TA_DEVICE_TLS_FULLCHAIN_B64:-}" &&
      -n "${TA_DEVICE_TLS_PRIVKEY_B64:-}" &&
      -n "${TA_FULLSCREEN_TLS_FULLCHAIN_B64:-}" &&
      -n "${TA_FULLSCREEN_TLS_PRIVKEY_B64:-}" ]]; then
  decode_secret "$TA_DEVICE_TLS_FULLCHAIN_B64" "$device_root/fullchain.pem"
  decode_secret "$TA_DEVICE_TLS_PRIVKEY_B64" "$device_root/privkey.pem"
  decode_secret "$TA_FULLSCREEN_TLS_FULLCHAIN_B64" "$fullscreen_root/fullchain.pem"
  decode_secret "$TA_FULLSCREEN_TLS_PRIVKEY_B64" "$fullscreen_root/privkey.pem"
  echo "Using TLS credentials supplied through GitHub Actions secrets."
else
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 30 \
    -subj "/CN=device.teslaandroid.com" \
    -addext "subjectAltName=DNS:device.teslaandroid.com" \
    -keyout "$device_root/privkey.pem" \
    -out "$device_root/fullchain.pem"
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 30 \
    -subj "/CN=fullscreen.device.teslaandroid.com" \
    -addext "subjectAltName=DNS:fullscreen.device.teslaandroid.com" \
    -keyout "$fullscreen_root/privkey.pem" \
    -out "$fullscreen_root/fullchain.pem"
  echo "WARNING: TLS secrets are absent; generated self-signed development certificates."
  echo "The image can boot, but Tesla browser HTTPS integration requires trusted production certificates."
fi

chmod 600 "$signing_root"/*.pk8 "$device_root/privkey.pem" "$fullscreen_root/privkey.pem"
