
#!/bin/bash
set -euo pipefail
set -x

export GIT_AUTHOR_NAME="Tesla Android CI"
export GIT_AUTHOR_EMAIL="ci@teslaandroid.local"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME"
export GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"

LOCAL_PATH=$(pwd)

echo Init repo tree using AOSP manifest
pushd aosptree
repo init --depth=2 -u https://android.googlesource.com/platform/manifest -b refs/tags/android-platform-15.0.0_r3 ${GD_REPO_INIT_ARGS:-}
cd .repo/manifests
mv default.xml aosp.xml
cp ${LOCAL_PATH}/manifests/tesla-android.xml tesla-android.xml
cp ${LOCAL_PATH}/manifests/glodroid.xml glodroid.xml
cp ${LOCAL_PATH}/manifests/default_aosp.xml default.xml
git add --all
if ! git diff --cached --quiet; then
    git commit -m "Add GloDroid Project" --no-edit
fi
popd

echo Clean interrupted Git operations from cached source checkouts
pushd aosptree
repo forall -c 'git am --abort >/dev/null 2>&1 || true; git rebase --abort >/dev/null 2>&1 || true; git cherry-pick --abort >/dev/null 2>&1 || true; true'
popd

echo Sync repo tree
pushd aosptree
repo sync --no-clone-bundle --no-tags -j$(nproc --all) -v

# Source checkout is retained between CI runs. Projects removed from the manifest
# may remain on disk, and Soong scans every Android.bp it finds.
AOSP_TREE=$(realpath .)
for stale_project in external/drm_hwcomposer device/amlogic/yukawa device/linaro/hikey
do
    stale_path=$(realpath -m "${AOSP_TREE}/${stale_project}")
    case "${stale_path}" in
        "${AOSP_TREE}"/*)
            if [[ -e "${stale_path}" ]]; then
                echo "Removing stale project excluded by the RPi manifest: ${stale_project}"
                rm -rf -- "${stale_path}"
            fi
            ;;
        *)
            echo "Refusing to remove path outside AOSP tree: ${stale_path}" >&2
            exit 1
            ;;
    esac
done
popd

echo Patch AOSP tree
patch_dir() {
    pushd aosptree/$1
    repo sync -l .
    git am ${LOCAL_PATH}/patches-aosp/$1/*.patch
    popd
}

pushd patches-aosp
directories=$(find -name *patch | xargs dirname | uniq)
popd

for dir in ${directories}
do
    echo "Patching: $dir"
    patch_dir $dir
done

# Hack to avoid rebuilding AOSP from scratch
touch -c -t 200101010101 aosptree/external/libcxx/include/chrono

echo -e "\n\033[32m   Done   \033[0m"
