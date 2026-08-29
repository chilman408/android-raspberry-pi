#!/usr/bin/env bash
set -euo pipefail

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
check_root="$(mktemp -d -p "${RUNNER_TEMP:-/tmp}" android15-patches.XXXXXX)"
tree_root="$check_root/aosptree"
repo_bin="$check_root/repo"

curl --fail --location --retry 3 \
  https://storage.googleapis.com/git-repo-downloads/repo \
  --output "$repo_bin"
chmod +x "$repo_bin"
mkdir -p "$tree_root"

git config --global user.name "Android 15 patch validation"
git config --global user.email "android15-validation@users.noreply.github.com"

pushd "$tree_root" >/dev/null
"$repo_bin" init --depth=2 \
  -u https://android.googlesource.com/platform/manifest \
  -b refs/tags/android-platform-15.0.0_r3

pushd .repo/manifests >/dev/null
mv default.xml aosp.xml
cp "$source_root/manifests/tesla-android.xml" tesla-android.xml
cp "$source_root/manifests/glodroid.xml" glodroid.xml
cp "$source_root/manifests/default_aosp.xml" default.xml
git add --all
git commit -m "Add Tesla Android and GloDroid manifests" --no-edit
popd >/dev/null

mapfile -t manifest_projects < <("$repo_bin" list --all -p | sort -u)
popd >/dev/null

mapfile -t patch_dirs < <(
  find "$source_root/patches-aosp" -type f -name '*.patch' -printf '%h\n' |
    sed "s#^$source_root/patches-aosp/##" |
    sort -u
)

declare -A projects_to_sync=()
for patch_dir in "${patch_dirs[@]}"; do
  selected=""
  for candidate in "${manifest_projects[@]}"; do
    if [[ "$patch_dir" == "$candidate" || "$patch_dir" == "$candidate/"* ]]; then
      if (( ${#candidate} > ${#selected} )); then
        selected="$candidate"
      fi
    fi
  done

  if [[ -z "$selected" ]]; then
    printf 'ERROR: no manifest project owns patch directory %s\n' "$patch_dir" >&2
    exit 1
  fi
  projects_to_sync["$selected"]=1
done

mapfile -t sync_projects < <(printf '%s\n' "${!projects_to_sync[@]}" | sort)
printf 'Syncing %d projects needed by %d patch series.\n' \
  "${#sync_projects[@]}" "${#patch_dirs[@]}"

pushd "$tree_root" >/dev/null
"$repo_bin" sync --current-branch --force-sync --no-clone-bundle --no-tags \
  -j4 "${sync_projects[@]}"
popd >/dev/null

for patch_dir in "${patch_dirs[@]}"; do
  printf '\nApplying %s\n' "$patch_dir"
  for patch in "$source_root/patches-aosp/$patch_dir"/*.patch; do
    if git -C "$tree_root/$patch_dir" am "$patch"; then
      continue
    fi

    printf '\nPatch failed: %s\n' "$patch" >&2
    git -C "$tree_root/$patch_dir" am --abort
    git -C "$tree_root/$patch_dir" apply --reject --whitespace=nowarn "$patch" || true
    find "$tree_root/$patch_dir" -type f -name '*.rej' -print -exec sed -n '1,240p' {} \; >&2
    exit 1
  done
done

printf '\nAll Android 15 patch series applied cleanly.\n'
