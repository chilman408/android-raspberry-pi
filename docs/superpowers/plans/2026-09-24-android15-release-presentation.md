# Android 15 Release Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish consistent `2026.39.a15.1` About, Release Notes, and Donations content in both TeslaAndroid frontends.

**Architecture:** Apply one patch to the pinned vendor payload, covering the compiled Flutter bundle and readable beta frontend.  Add a focused repository contract for exact identity and donation data, and extend the legacy Jenkins parser to accept the Android-platform version segment.

**Tech Stack:** HTML, JavaScript, SVG QR, Android make, Groovy, Python unittest, AOSP patch overlays.

**Spec:** `docs/superpowers/specs/2026-09-24-android15-release-presentation-design.md`

## Global Constraints

- Version is exactly `2026.39.a15.1`.
- Project URL and PayPal URL are exact user-supplied values.
- Both production and beta frontends must agree.
- Historical release notes remain unchanged.
- `services/updateBootFiles/updateBootFiles.sh` remains untouched.

## Review Focus

- An ampersand or percent-encoding change must not alter the PayPal payload.
- The SVG must decode to the same URL used by the compiled frontend.
- The default compiled frontend must not retain the old About or donation copy.
- The beta update comparator must tolerate the `a15` segment.
- Jenkins must accept both old three-part and new Android-platform versions.

---

### Task 1: Release identity and frontend content

**Files:**
- Create: `tools/tests/test_android15_release_presentation.py`
- Create: `patches-aosp/vendor/tesla-android/0002-release-Brand-Android-15-work-week-39.patch`
- Modify: `tools/check-android15-port.sh`

**Interfaces:**
- Consumes: vendor payload revision `6e139bf41585188308e053dcbb34855f644aedba`.
- Produces: identical version, About, release-note, donation copy, and donation target in both frontends.

- [ ] **Step 1: Write the failing release contract**

```python
def test_release_patch_covers_both_frontends(self):
    patch = RELEASE_PATCH.read_text(encoding="utf-8")
    self.assertIn(RELEASE_VERSION, patch)
    self.assertIn(PROJECT_URL, patch)
    self.assertIn(DONATION_URL, patch)
    self.assertIn("services/lighttpd/www-default/main.dart.js", patch)
    self.assertIn("services/lighttpd/www-default/beta/index.html", patch)
```

Add tests for two major release-note entries, the Apple TV limitation, QR
metadata, the pinned vendor revision, removal of current About/donation copy,
and absence of `updateBootFiles.sh` from the patch.

- [ ] **Step 2: Run the focused test and observe RED**

Run: `python -m unittest tools.tests.test_android15_release_presentation -v`

Expected: FAIL because vendor patch 0002 and its gate do not exist.

- [ ] **Step 3: Generate and verify the beta QR**

Generate an SVG QR from the exact donation URL, add canonical payload metadata,
and decode the rendered QR locally.

Run:

```powershell
python -m pip install --target .superpowers/sdd/2026-09-24-android15-release-presentation/qr-tools segno opencv-python-headless
$env:PYTHONPATH = ".superpowers/sdd/2026-09-24-android15-release-presentation/qr-tools"
python -c "import segno; qr=segno.make('https://www.paypal.com/donate/?business=TFWNLUC7ZGSPS&no_recurring=0&item_name=Thank+you+for+your+support%21++I+appreciate+it.&currency_code=USD', error='m'); qr.save('.superpowers/sdd/2026-09-24-android15-release-presentation/donations-qr.svg', scale=6, border=4); qr.save('.superpowers/sdd/2026-09-24-android15-release-presentation/donations-qr.png', scale=6, border=4)"
python -c "import cv2; expected='https://www.paypal.com/donate/?business=TFWNLUC7ZGSPS&no_recurring=0&item_name=Thank+you+for+your+support%21++I+appreciate+it.&currency_code=USD'; image=cv2.imread('.superpowers/sdd/2026-09-24-android15-release-presentation/donations-qr.png'); value,_,_=cv2.QRCodeDetector().detectAndDecode(image); assert value == expected, (value, expected)"
```

Add the verified SVG to the vendor patch using `apply_patch`.

Expected: decoder output equals the exact donation URL byte-for-byte.

- [ ] **Step 4: Implement the vendor patch**

Update `vendor.mk`, `version.json`, beta `APP_VERSION`, About and donation
copy, both release-note datasets, compiled donation QR input, and beta SVG.
Leave the boot-files migration marker untouched.

- [ ] **Step 5: Run the focused test**

Run: `python -m unittest tools.tests.test_android15_release_presentation -v`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/tests/test_android15_release_presentation.py tools/check-android15-port.sh patches-aosp/vendor/tesla-android/0002-release-Brand-Android-15-work-week-39.patch
git commit -m "feat: brand Android 15 work-week release"
```

### Task 2: Release tooling compatibility

**Files:**
- Modify: `jenkins/multi-branch-ci.groovy`
- Modify: `tools/tests/test_android15_release_presentation.py`

**Interfaces:**
- Consumes: `ro.tesla-android.build.version=2026.39.a15.1`.
- Produces: `getVersion(file)` compatibility with both `2026.39.a15.1` and `2026.22.1`.

- [ ] **Step 1: Add failing parser examples**

```python
def test_release_version_pattern_accepts_android_platform_segment(self):
    self.assertEqual(parse_with_jenkins_pattern("2026.39.a15.1"), "2026.39.a15.1")
    self.assertEqual(parse_with_jenkins_pattern("2026.22.1"), "2026.22.1")
```

- [ ] **Step 2: Run the parser test and observe RED**

Run: `python -m unittest tools.tests.test_android15_release_presentation.Android15ReleasePresentationTest.test_release_version_pattern_accepts_android_platform_segment -v`

Expected: FAIL because the existing pattern stops before `a15`.

- [ ] **Step 3: Extend the Groovy regex minimally**

```groovy
/ro\.tesla-android\.build\.version\s*=\s*([0-9]+\.[0-9]+(?:\.a[0-9]+)?\.[0-9]+)/
```

- [ ] **Step 4: Run focused and adjacent suites**

Run: `python -m unittest tools.tests.test_android15_release_presentation tools.tests.test_android15_runtime_performance -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jenkins/multi-branch-ci.groovy tools/tests/test_android15_release_presentation.py
git commit -m "build: accept Android platform release versions"
```
