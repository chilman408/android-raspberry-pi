#!/bin/bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
lock="$repository_root/aosptree/vendor/devices-community/gd_rpi4/compatibility-store/aurora-store.lock.json"
destination="$repository_root/aosptree/vendor/devices-community/gd_rpi4/compatibility-store"
aosp_root="$repository_root/aosptree"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --lock)
            [[ $# -ge 2 ]] || die "--lock requires a path"
            lock="$2"
            shift 2
            ;;
        --destination)
            [[ $# -ge 2 ]] || die "--destination requires a directory"
            destination="$2"
            shift 2
            ;;
        --aosp-root)
            [[ $# -ge 2 ]] || die "--aosp-root requires a directory"
            aosp_root="$2"
            shift 2
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

[[ -e "$destination" ]] || die "destination does not exist: $destination"
[[ ! -L "$destination" ]] || die "destination must not be a symlink: $destination"
[[ -d "$destination" ]] || die "destination is not a directory: $destination"
[[ -e "$aosp_root" && -d "$aosp_root" ]] || die "AOSP root is not a directory: $aosp_root"

lock="$(realpath -- "$lock")"
destination="$(realpath -- "$destination")"
aosp_root="$(realpath -- "$aosp_root")"

case "$destination" in
    "$aosp_root"|"$aosp_root"/*) ;;
    *) die "destination is outside the AOSP checkout: $destination" ;;
esac

lock_values="$(python3 -c 'import importlib.util,pathlib,sys; spec=importlib.util.spec_from_file_location("check_aurora_store", sys.argv[1]); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); lock=module.AuroraLock.from_path(pathlib.Path(sys.argv[2])); sys.stdout.buffer.write((lock.filename+"\n"+lock.url+"\n").encode("utf-8"))' "$repository_root/tools/check-aurora-store.py" "$lock")"
filename="${lock_values%%$'\n'*}"
url="${lock_values#*$'\n'}"
[[ "$filename" != "$lock_values" && "$url" != *$'\n'* ]] || die "lock did not produce one filename and one URL"

final_apk="$destination/$filename"
[[ ! -L "$final_apk" ]] || die "final APK must not be a symlink: $final_apk"
if [[ -e "$final_apk" && ! -f "$final_apk" ]]; then
    die "final APK must be a regular file: $final_apk"
fi

if [[ -f "$final_apk" ]]; then
    if python3 "$repository_root/tools/check-aurora-store.py" \
        --lock "$lock" \
        --apk "$final_apk" \
        --apksigner "$aosp_root/prebuilts/sdk/tools/linux/bin/apksigner" \
        --aapt2 "$aosp_root/prebuilts/sdk/tools/linux/bin/aapt2"; then
        exit 0
    fi
fi

temporary_apk="$(mktemp "$destination/.${filename}.tmp.XXXXXX")"
cleanup() { [[ -z "${temporary_apk:-}" ]] || rm -f -- "$temporary_apk"; }
trap cleanup EXIT INT TERM
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 \
  --output "$temporary_apk" "$url"
python3 "$repository_root/tools/check-aurora-store.py" \
  --lock "$lock" \
  --apk "$temporary_apk" \
  --logical-filename "$filename" \
  --apksigner "$aosp_root/prebuilts/sdk/tools/linux/bin/apksigner" \
  --aapt2 "$aosp_root/prebuilts/sdk/tools/linux/bin/aapt2"
mv -f -- "$temporary_apk" "$destination/$filename"
temporary_apk=""
