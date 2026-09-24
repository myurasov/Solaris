---
name: asc-api
triggers: ["app store connect", "asc api", "app store connect api", "app store listing", "submit for review", "app store screenshots", "attach build", "app store pricing", "testflight testers", "age rating"]
summary: Operate App Store Connect over the ASC REST API (team key + short-lived JWT) - the default path for listings, screenshots, builds, pricing, age rating, review submission, TestFlight - with field-tested endpoints, schema pitfalls, review-time editability, and the policy quirks that gate submissions.
---
_Rev. 4_

# Skill: asc-api - App Store Connect Over the REST API <!-- omit in toc -->

- [When to Use](#when-to-use)
- [Auth](#auth)
- [Field-Tested Endpoints](#field-tested-endpoints)
- [What the API Cannot Reach](#what-the-api-cannot-reach)
- [What Stays Editable During Review](#what-stays-editable-during-review)
- [Policy Quirks](#policy-quirks)
- [Guardrails](#guardrails)
- [Maintenance (Standing Duty)](#maintenance-standing-duty)

## When to Use

The **default entry point** for any App Store Connect chore, iOS or macOS: nearly all ASC
state is faster and more reliable over the REST API than through the web UI. Reach for the
browser skill ([`browserctl.asc.skill.md`](browserctl.asc.skill.md)) only for the flows
listed under "What the API Cannot Reach" - including the bootstrap case where no API key
exists yet (creating one is itself a browser flow).

## Auth

- Team key: key id, issuer id, and `.p8` path live in `ai/.memory/credentials.md`; staged
  resource ids (app, version, submission) in `ai/.memory/`.
- JWT ES256 with `aud=appstoreconnect-v1` and **exp <= 20 minutes - longer and every call
  fails as an auth error** (the message does not say why; the expiry is the reason).
- Role **App Manager** suffices for uploads and full listing staging.

## Field-Tested Endpoints

- **Version records + localizations**: description, keywords, promo text, support URL,
  copyright.
- **App info**: subtitle, privacy policy URL, category, content rights.
- **Age rating**: the 2026 schema is a moving target - use an adaptive retry loop that reads
  "You must provide a value for the attribute 'X'" from the error and fills booleans /
  "NONE" enums until the PATCH sticks.
- **Pricing**: free = 0-price point + `appPriceSchedules` with a `${price1}` included
  resource.
- **Screenshots**: POST to reserve -> PUT the upload operations -> PATCH `uploaded` + MD5.
  The 6.9-inch iPhone display type is `APP_IPHONE_67` (exact 1320x2868 PNGs); mac
  screenshots 2880x1800.
- **Build attach** to a version.
- **Review submission chain**: `reviewSubmissions` -> `reviewSubmissionItems` -> PATCH
  `submitted:true`. A 409 carries `meta.associatedErrors` naming the missing field (e.g.
  `copyright`) - read it instead of guessing.
- **TestFlight groups/testers**: the web UI can add a tester without ever sending the
  invite - state stays NOT_INVITED; `POST /v1/betaTesterInvitations` actually sends it.

## What the API Cannot Reach

Browser-only (see [`browserctl.asc.skill.md`](browserctl.asc.skill.md)):

- App Privacy questionnaire + publish (`appDataUsages` was removed from the 2026 API).
- EU DSA trader status (Business > Compliance) and agreement acceptance.
- In-app purchase setup when no API key exists yet (price, localization, review info).
- ASC API key creation and the one-time `.p8` download (Users and Access > Integrations).
- Visual verification (listing preview, screenshot thumbnails) and tester-side debugging.

## What Stays Editable During Review

Field-tested while a version sat in WAITING_FOR_REVIEW: **version localization edits
(supportUrl, description) and app-info edits (privacyPolicyUrl) PATCH fine in place** - no
cancel/resubmit needed, and the submission state is untouched. Do not preemptively cancel a
submission to edit metadata; try the PATCH first and fall back to
cancel (`reviewSubmissions` PATCH `canceled:true`) -> patch -> new submission -> item ->
`submitted:true` only on a real lock error. Structural changes (build swap, screenshots)
are the ones that need the version editable.

**After a rejection** the API is state-only: `reviewSubmissions/<id>?include=items` ->
`state: UNRESOLVED_ISSUES`, item `state: REJECTED`, `appStoreVersions` `appVersionState:
REJECTED`. The reviewer's text (guideline, what they want) is **not** in the API and not in
the notification email either - read it in the browser (`browserctl.asc.skill.md`, "Reading
a rejection"). The rejected version is editable again (metadata, review notes, build) and
the existing submission is reused. Fixes PATCH fine over the API (review notes via
`appStoreReviewDetails`; a demo video via `appStoreReviewAttachments`: POST reserve with
`fileName`/`fileSize` + the `appStoreReviewDetail` relationship -> PUT the upload operations ->
PATCH `uploaded:true` + MD5 `sourceFileChecksum` -> poll `assetDeliveryState` to COMPLETE; a
35 MB .mov took ~10 s; DELETE the stale attachment first so the reviewer sees one video).
**Resubmit over the API is two PATCHes**: first `reviewSubmissionItems/<item id>` with
`resolved:true` (the API twin of the web `Update Review` button; item goes REJECTED ->
READY_FOR_REVIEW), then `reviewSubmissions/<id>` `submitted:true`. Skipping the first PATCH
makes the second return 409 `STATE_ERROR` "Version is not ready to be submitted yet, please
try again later" indefinitely - it is not a propagation delay. Confirm: `state:
WAITING_FOR_REVIEW`, item `READY_FOR_REVIEW`, same submission id, new `submittedDate`. This
works while the web session is expired; only the reply thread still needs the browser, and
the review notes are a reviewer-facing channel for the same answer. A first-submission rejection
typically lands within ~20 min of "In Review" - poll the state, not the inbox.

## Policy Quirks

- **Paid apps / IAP gate on the Paid Applications agreement.** Accounts whose legal address
  is in a region where Apple suspended paid apps (e.g. Russia since 2022) are not offered
  the agreement at all - no UI error, it is simply absent. A country/region change is an
  Apple Developer Support case (Membership and Account), not self-serve.
- **A first IAP must be submitted together with an app version** - it cannot go for review
  alone, and a version whose app relies on the IAP should not be submitted before the paid
  agreement exists (the purchase would be dead on arrival).
- **External support/privacy URLs are referenced live** by the listing - renaming the repo
  or host behind them (e.g. a GitHub Pages repo rename, which does not redirect) requires
  re-pointing the ASC Support URL and Privacy Policy URL fields.
- Builds appear attachable only after Apple finishes processing an upload (typically 15-60
  minutes; the build row gains its app icon when ready).

## Guardrails

- Every mutating call is **outward** - the safety rule applies: confirm first unless a
  standing autonomy grant covers the task; genuinely irreversible steps (Submit for Review,
  agreements) still get a one-line heads-up.
- Keys and ids stay in `ai/.memory/` - never in shareable files, logs, or commits; reference
  secrets by name, never by value.

## Maintenance (Standing Duty)

This plugin's skills are **living documents** (owner directive, 2026-08): fold every newly
field-verified App Store lesson - API schema change, endpoint behavior, review/TestFlight
behavior, policy quirk - into the matching skill (API material here, browser technique in
`browserctl.asc.skill.md`) the same session it is learned. Rewrite in place, keep
identifiers out, bump the file's rev (`revs bump` + `revs ledger`), commit the framework
repo. A lesson that lives only in memory files or a chat transcript is considered lost.