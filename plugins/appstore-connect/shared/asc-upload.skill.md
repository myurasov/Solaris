---
name: asc-upload
triggers: ["upload build", "upload to app store connect", "archive and upload", "xcodebuild archive", "new build for the app store", "upload the app"]
summary: Get a build from an Xcode project into App Store Connect from the command line - archive with archive-time signing overrides, export with destination upload, -allowProvisioningUpdates for automatic distribution signing - with the field-tested config pattern and pitfalls (macOS-verified).
---
_Rev. 1_

# Skill: asc-upload - Archive and Upload a Build <!-- omit in toc -->

- [When to Use](#when-to-use)
- [Prerequisites](#prerequisites)
- [The Config Pattern](#the-config-pattern)
- [Archive and Upload](#archive-and-upload)
- [After the Upload](#after-the-upload)
- [Pitfalls](#pitfalls)

## When to Use

Uploading an app build to App Store Connect from the command line (CI or agent-driven; no
Xcode Organizer). Field-tested on a macOS app; the same pipeline applies to iOS. Versioning,
attach, and submission are separate steps (`asc-api.skill.md`).

## Prerequisites

- Xcode is signed into the Apple ID (Xcode > Settings > Accounts) -
  `-allowProvisioningUpdates` then mints/refreshes distribution signing automatically; no
  manual certificate or profile management needed.
- The ASC app record and bundle id exist (app id and team id: `ai/.memory/`).
- App Store binaries must be **sandboxed**; keep entitlements minimal - drop anything the
  app does not strictly need (unused entitlements invite review questions and can fail
  signing validation).

## The Config Pattern

Keep the App Store build configuration **ad-hoc signed locally** (`CODE_SIGN_IDENTITY: "-"`)
and override signing **only at archive time**. Day-to-day builds then never require a
distribution certificate ("No signing certificate ..." build failures disappear), and the
override lives in one place - the archive command.

## Archive and Upload

```bash
xcodebuild archive -scheme <AppStoreScheme> -archivePath <out>.xcarchive \
  -allowProvisioningUpdates \
  CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=<team_id> CODE_SIGN_IDENTITY="Apple Distribution"

xcodebuild -exportArchive -archivePath <out>.xcarchive \
  -exportOptionsPlist ExportOptions.plist -allowProvisioningUpdates
```

`ExportOptions.plist` (this uploads directly - no `.pkg`/`.ipa` handling, no altool):

```xml
<dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>upload</string>
  <key>teamID</key><string><team_id></string>
  <key>signingStyle</key><string>automatic</string>
</dict>
```

## After the Upload

Apple processes the build for typically 15-60 minutes; it becomes attachable (Add Build
dialog / API) when ready - the build row gains its app icon. Attach + submit per
`asc-api.skill.md` (or the browser skill's Add Build flow).

## Pitfalls

- **CFBundleVersion must increase on every upload** and is never reused, even for builds
  that were rejected or never attached - keep a continuous integer build number.
- A wrong `DEVELOPMENT_TEAM` fails late and confusingly - when the account has had multiple
  teams (e.g. an old personal team), verify the team id against `ai/.memory/` first.
- Archive from a clean tree state you can tag: the uploaded binary is immutable, and
  debugging "which code is build N" later is expensive.
- Building inside a cloud-synced directory (e.g. iCloud) can break CodeSign with xattr
  errors - point DerivedData at a local path if that bites.