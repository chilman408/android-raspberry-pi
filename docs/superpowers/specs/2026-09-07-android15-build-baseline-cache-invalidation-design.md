# Android 15 build-baseline cache invalidation design

**Date:** 2026-09-07  
**Status:** Approved for implementation planning  
**Target branch:** `android-15-bringup`

## Context

The Android 15 Raspberry Pi 4 workflow runs on a persistent self-hosted runner and checks out the repository with `clean: false`. It currently treats the mere presence of `aosptree/out` as proof that a cached incremental build is safe, requests only 150 GiB of free space in that case, and removes only the FFmpeg, libcamera, kernel, and U-Boot intermediates before building.

Recent successful workflow logs reported `No need to regenerate ninja file` and reused retained output across the Android 15 transition. The resulting image gets past the earlier PID 1 fingerprint-expansion crash only because of the existing diagnostic workaround, then repeatedly crashes both zygotes, `update_engine`, and `crash_dump64` before `system_server` starts. The breadth of those failures is consistent with incompatible core AOSP intermediates being reused across a platform baseline change.

The next experiment must therefore produce a trustworthy clean Android 15 userspace image while changing no runtime workaround at the same time.

## Goals

- Force a full rebuild of core AOSP output whenever the declared Android build baseline changes or has never been recorded.
- Preserve the faster incremental path after a baseline has completed successfully.
- Make the selected build mode and baseline decision explicit in workflow logs.
- Restrict cleanup to the exact Android output directory.
- Ensure a failed clean build cannot bless partial output for future incremental reuse.
- Add repository validation so later workflow edits cannot silently revert to existence-only cache reuse.

## Non-goals

- Removing the current init fingerprint-expansion workaround in the same change.
- Claiming that stale output is the final runtime root cause before a clean image is tested on hardware.
- Cleaning the source checkout, downloaded repositories, credentials, artifacts, or unrelated runner state.
- Replacing the current external-component refresh policy.
- Changing AOSP, GloDroid, kernel, U-Boot, or application revision pins.

## Design

### Declared baseline

The build job will define one human-maintained baseline identifier, initially:

```text
android-platform-15.0.0_r3-core-clean-v1
```

The value represents compatibility of the retained core Android build graph, not merely the product version shown to users. Any change known to invalidate core AOSP intermediates must change this identifier. The `vN` suffix allows an intentional one-time rebuild even when source revision labels remain unchanged.

The last successfully built identifier will be stored in:

```text
$GITHUB_WORKSPACE/aosptree/out/.android-build-baseline
```

Keeping the stamp inside `out` ensures that deleting the output tree also removes its reuse authorization.

### Baseline decision and cleanup

Immediately after checkout, before source synchronization or compilation, the workflow will compare the declared identifier with the stamp.

A cached incremental build is allowed only when all of the following are true:

- `aosptree/out` is a real directory rather than a symbolic link.
- The stamp is a regular file.
- The stamp contains exactly the declared baseline identifier.

A missing output directory, missing stamp, unreadable stamp, or different value selects fresh-build mode. An unexpected output-path type is unsafe and fails closed before any cleanup.

For a fresh-build state, the workflow will:

1. Derive the output path only as `$GITHUB_WORKSPACE/aosptree/out`.
2. Verify that `GITHUB_WORKSPACE` is non-empty and not `/`.
3. Verify that the derived path equals the expected workspace-relative path.
4. Refuse cleanup if the output path is a symbolic link or otherwise unexpected.
5. Remove only that exact output path with a literal, quoted `rm -rf --`.
6. Export the selected build mode for later steps and log the old and requested baselines without exposing secrets.

The existing four-directory external intermediate refresh remains in place for same-baseline incremental builds and is harmless after a fresh cleanup.

### Capacity classification

The capacity check will use the baseline decision rather than directory existence:

- Fresh build: require at least 300 GiB of free workspace disk.
- Valid same-baseline incremental build: require at least 150 GiB.
- Both modes: retain the existing 40 GiB RAM-plus-swap requirement.

Cleanup occurs before the disk check in fresh mode so stale output does not count against the free-space measurement. The log must name the selected mode and its threshold.

### Recording success

The workflow will not create the stamp during preparation. It will write the declared identifier atomically only after both of these steps succeed:

- `Build Raspberry Pi 4 image`
- `Package flashable artifacts`

If synchronization, compilation, image generation, or packaging fails, no matching stamp is recorded. The next run therefore treats the retained partial output as untrusted, removes it, and retries from a clean state.

Artifact upload or optional prerelease publication occurs after the stamp is recorded. Failures in those transport-only steps do not invalidate a build that compiled and packaged successfully.

### Repository validation

`tools/check-android15-port.sh` will validate the workflow contract in addition to the existing Android 15 pins and workarounds. The validation must reject a workflow that lacks any of these invariants:

- A declared Android build-baseline identifier.
- The stamp under `aosptree/out`.
- Exact stamp comparison before incremental reuse.
- A guarded cleanup limited to `$GITHUB_WORKSPACE/aosptree/out`.
- Fresh and incremental disk thresholds of 300 GiB and 150 GiB.
- Stamp recording after the build and packaging steps.
- Continued presence of the init fingerprint workaround for this experiment.

The ordering check should inspect workflow text positions rather than only checking that the relevant strings exist.

## Failure handling

- Missing or mismatched stamp: log the reason and perform the guarded fresh cleanup.
- Unsafe workspace or output path: fail closed before deletion.
- Insufficient post-cleanup disk or memory: stop before synchronization/build.
- Failed clean build or packaging: leave no valid stamp, forcing another clean attempt.
- Matching stamp with damaged output: the build may fail incrementally; changing the baseline suffix provides an explicit recovery mechanism, while manual runner repair remains an operational option.

## Verification

Repository-level verification will cover:

1. The existing Android 15 port validator passes with the new workflow.
2. Validator regression checks fail when the baseline declaration, comparison, safety guard, either disk threshold, or delayed stamp write is removed or reordered.
3. Workflow syntax remains valid.
4. A fresh-mode run logs a missing or changed baseline, removes only `aosptree/out`, selects the 300 GiB threshold, rebuilds core AOSP output, packages all expected artifacts, and writes the stamp.
5. A subsequent unchanged run selects the 150 GiB same-baseline incremental path.
6. A deliberately changed baseline suffix forces fresh mode again.
7. A simulated build/package failure does not produce a matching stamp.

Hardware verification is a separate checkpoint. Flash the first clean artifact while retaining the fingerprint workaround and confirm whether `system_server` starts, boot animation exits, and recurring zygote/`update_engine` SIGSEGVs disappear. If they do, remove the fingerprint workaround in a later single-variable change. If they do not, collect the clean build's crash output or tombstones and continue diagnosis from that controlled baseline.

## Rollout

The documentation commit must not contain `[build-image]`, so it does not consume a runner build. The later implementation commit will include `[build-image]` once repository checks pass, producing the one required clean hardware-test artifact.

The first successful clean run establishes the stamp. Later commits reuse output only while the declared baseline remains unchanged.

## Alternatives considered

- **Clean every build:** strongest isolation, but repeatedly consumes substantial runner time and disk I/O after the baseline is known good.
- **Delete selected ART, Bionic, linker, and framework intermediates:** faster, but fragile because the observed cross-process failures do not identify a safe minimal invalidation boundary.
- **Existence-only reuse with more targeted directories:** retains the current ambiguity and cannot prove that core platform artifacts share one compatible baseline.

The baseline stamp provides a deterministic clean boundary while retaining incremental performance after the first verified build.
