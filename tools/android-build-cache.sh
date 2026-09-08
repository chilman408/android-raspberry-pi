#!/usr/bin/env bash
set -euo pipefail

action="${1:-}"
if [[ "$action" != "prepare" && "$action" != "record" ]]; then
  echo "Usage: $0 prepare|record" >&2
  exit 2
fi

: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE must be set}"
: "${ANDROID_BUILD_BASELINE:?ANDROID_BUILD_BASELINE must be set}"
if [[ ! "$ANDROID_BUILD_BASELINE" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "ERROR: invalid Android build baseline: $ANDROID_BUILD_BASELINE" >&2
  exit 1
fi

workspace_input="${GITHUB_WORKSPACE%/}"
if [[ -z "$workspace_input" || "$workspace_input" == "/" || "$workspace_input" != /* ]]; then
  echo "ERROR: unsafe GITHUB_WORKSPACE: $GITHUB_WORKSPACE" >&2
  exit 1
fi
workspace="$(realpath -m -- "$workspace_input")"
if [[ "$workspace" != "$workspace_input" ]]; then
  echo "ERROR: GITHUB_WORKSPACE must be a canonical path: $GITHUB_WORKSPACE" >&2
  exit 1
fi

aosptree_root="$workspace/aosptree"
out_root="$aosptree_root/out"
expected_out_root="$workspace/aosptree/out"
stamp_file="$out_root/.android-build-baseline"
if [[ "$out_root" != "$expected_out_root" ]]; then
  echo "ERROR: refusing unexpected Android output path: $out_root" >&2
  exit 1
fi
if [[ -L "$aosptree_root" || ( -e "$aosptree_root" && ! -d "$aosptree_root" ) ]]; then
  echo "ERROR: unsafe Android source tree path: $aosptree_root" >&2
  exit 1
fi
if [[ -L "$out_root" || ( -e "$out_root" && ! -d "$out_root" ) ]]; then
  echo "ERROR: unsafe Android output path: $out_root" >&2
  exit 1
fi

case "$action" in
  prepare)
    : "${GITHUB_ENV:?GITHUB_ENV must be set for prepare}"
    if [[ -d "$out_root" && -f "$stamp_file" && ! -L "$stamp_file" ]] &&
        cmp -s -- "$stamp_file" <(printf '%s\n' "$ANDROID_BUILD_BASELINE"); then
      echo "Android build cache baseline: $ANDROID_BUILD_BASELINE"
      echo "Android build cache mode: incremental"
      printf 'ANDROID_BUILD_MODE=incremental\n' >> "$GITHUB_ENV"
      exit 0
    fi
    previous_baseline="<missing>"
    if [[ -f "$stamp_file" && ! -L "$stamp_file" ]]; then
      previous_baseline="$(tr '\n' ' ' < "$stamp_file")"
    fi
    echo "Android build cache baseline: $ANDROID_BUILD_BASELINE"
    echo "Previous successful baseline: $previous_baseline"
    echo "Android build cache mode: fresh"
    rm -rf -- "$out_root"
    printf 'ANDROID_BUILD_MODE=fresh\n' >> "$GITHUB_ENV"
    ;;
  record)
    if [[ ! -d "$out_root" || -L "$out_root" ]]; then
      echo "ERROR: cannot record baseline without a safe Android output directory" >&2
      exit 1
    fi
    if [[ -L "$stamp_file" || ( -e "$stamp_file" && ! -f "$stamp_file" ) ]]; then
      echo "ERROR: unsafe Android build baseline stamp: $stamp_file" >&2
      exit 1
    fi
    temporary_stamp="$(mktemp "$out_root/.android-build-baseline.tmp.XXXXXX")"
    cleanup_temporary_stamp() {
      [[ -z "${temporary_stamp:-}" ]] || rm -f -- "$temporary_stamp"
    }
    trap cleanup_temporary_stamp EXIT
    printf '%s\n' "$ANDROID_BUILD_BASELINE" > "$temporary_stamp"
    mv -f -- "$temporary_stamp" "$stamp_file"
    temporary_stamp=""
    echo "Recorded successful Android build baseline: $ANDROID_BUILD_BASELINE"
    ;;
esac
