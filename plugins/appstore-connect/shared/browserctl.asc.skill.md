---
name: browserctl.asc
triggers: ["asc web", "drive app store connect", "app store connect browser", "app privacy", "trader status", "app store agreements", "manage the iap", "apple developer site"]
summary: Operate App Store Connect (and developer.apple.com) through browserctl - for the flows the ASC API cannot reach: App Privacy questionnaire, EU DSA trader status, agreements, IAP setup, API-key creation, visual verification - with the field-tested drive loop, dialog technique, and upload pitfalls.
---
_Rev. 2_

# Skill: browserctl.asc - Driving App Store Connect in the Browser <!-- omit in toc -->

- [When to Use](#when-to-use)
- [Session Prelude](#session-prelude)
- [Two Drive Modes](#two-drive-modes)
- [Reading Pages](#reading-pages)
- [Deep-Link Map](#deep-link-map)
- [ASC Dialogs (the House Style)](#asc-dialogs-the-house-style)
- [Field-Tested Flows](#field-tested-flows)
- [Forms, Saving, Uploads](#forms-saving-uploads)
- [Guardrails](#guardrails)
- [Maintenance (Standing Duty)](#maintenance-standing-duty)

## When to Use

Browser work on App Store Connect / developer.apple.com. **Check
[`asc-api.skill.md`](asc-api.skill.md) first** - most ASC chores are faster over the REST
API; the browser is for what the API cannot reach (App Privacy questionnaire, EU DSA trader
status, agreements, IAP setup, API-key creation, visual verification) and for the bootstrap
case where no API key exists yet. Requires the base **browserctl** plugin in the same
project (its `browserctl.skill.md` is the command reference); this skill adds the
ASC-specific technique. The overlay path below is written as
`ai/plugins/browserctl/browserctl.py` - adjust if the base plugin is linked rather than
copied.

## Session Prelude

1. **Profile:** the signed-in Apple session persists in the browserctl profile named in
   `ai/.memory/resources.md`.

   ```bash
   uv run ai/plugins/browserctl/browserctl.py launch --profile <name> --headless --url <deep link>
   ```

   Headless is fine once the session exists; for a (re)login launch `--headed` and ask the
   owner - 2FA needs them. The session lives only in the profile: never launch that profile
   outside browserctl, never export cookies. `stop` the profile when the chore is done.
2. **Scripts:** if the project env has no playwright, run attach() scripts with
   `uv run --with playwright python <script>.py` from the project root.
3. **Identifiers** (app id, version/submission ids, team id) come from `ai/.memory/` -
   never hard-code them in shareable files.

## Two Drive Modes

**CLI quick loop** - reads and single clicks; no script file needed:

```bash
uv run ai/plugins/browserctl/browserctl.py navigate --profile <name> --url <url>
uv run ai/plugins/browserctl/browserctl.py eval --profile <name> \
  --js "new Promise(r=>setTimeout(()=>r(document.readyState+' | '+location.href),6000))"   # ASC is a slow SPA - always give it ~6 s
uv run ai/plugins/browserctl/browserctl.py snapshot --profile <name> --out step.yaml        # then grep the YAML
uv run ai/plugins/browserctl/browserctl.py eval --profile <name> \
  --js "(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='Done');if(!b)return 'no button';b.click();return 'clicked'})()"
```

**attach() scripts** - multi-step flows (questionnaires, fills, uploads). Small
single-purpose scripts in the session scratchpad, so a failed step is patched and re-run
without redoing the rest:

```python
import importlib.util
spec = importlib.util.spec_from_file_location(
    "browserctl", "<abs project path>/ai/plugins/browserctl/browserctl.py")
bctl = importlib.util.module_from_spec(spec); spec.loader.exec_module(bctl)

with bctl.attach("<profile>") as (pw, browser):
    ctx = browser.contexts[0]
    page = ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded")
    ...
```

- **Always `wait_until="domcontentloaded"`** plus a ~6-10 s wait - ASC never settles `load`
  and renders long after domcontentloaded; early snapshots are near-empty.
- **Tab discipline (the #1 failure mode):** `ctx.pages[-1]` drifts across scripts - open a
  fresh `ctx.new_page()` per step, or select by URL substring:
  `next(p for p in ctx.pages if "privacy" in p.url)`.
- **TargetClosedError** = the browser was restarted; `launch` again (the session survives)
  and re-run the step.
- Print state after every action (clicked what, dialog text) - the run log is the debugging
  trail when a step needs a patch.

## Reading Pages

Navigate by **aria snapshot**, not screenshots: `snapshot --out x.yaml` (CLI) or
`page.locator("body").aria_snapshot()` (script), then grep for the heading/button/dialog
and read a bounded slice. Roles in the snapshot map 1:1 to `get_by_role` locators.
Screenshot only to verify visuals (icons, screenshot thumbnails, badges).

## Deep-Link Map

ASC routes are stable - deep-link instead of clicking through menus. `<platform>` is `ios`
or `macos`; ids from `ai/.memory/`:

| Page | URL |
|---|---|
| App overview | `https://appstoreconnect.apple.com/apps/<app_id>` |
| Version (editable) | `https://appstoreconnect.apple.com/apps/<app_id>/distribution/<platform>/version/inflight` |
| App Privacy | `https://appstoreconnect.apple.com/apps/<app_id>/distribution/privacy` |
| Pricing | `https://appstoreconnect.apple.com/apps/<app_id>/distribution/pricing` |
| Availability | `https://appstoreconnect.apple.com/apps/<app_id>/distribution/availability` |
| IAP detail | `https://appstoreconnect.apple.com/apps/<app_id>/distribution/iaps/<iap_id>` |
| TestFlight builds/groups | `https://appstoreconnect.apple.com/apps/<app_id>/testflight` |
| Business (agreements + DSA compliance) | `https://appstoreconnect.apple.com/business` |
| API keys | `https://appstoreconnect.apple.com/access/integrations/api` |
| Bundle ids | `https://developer.apple.com/account/resources/identifiers/list` |

## ASC Dialogs (the House Style)

Nearly every mutation happens in a `role=dialog` overlay:

- **Scope every locator to the dialog** (`page.get_by_role("dialog").first`, or the last
  one when stacked) - page-level text matches hit the background.
- **Snapshot the dialog before acting**: radios can arrive pre-checked (the DSA non-trader
  option did) - then the only needed click is the commit button.
- Commit buttons are `Cancel` / `Save` / `Done` / `Publish`; guard with
  `btn.count() and btn.first.is_enabled()`. A disabled commit after "filling" means the fill
  did not register (wrong tab, wrong field, failed upload).
- **ASC dialogs often ship unnamed textboxes** (e.g. the IAP localization "Display Name") -
  scope to the dialog and fill by index: `dlg.get_by_role("textbox").nth(0).fill(...)`.
- **Expect chained confirm dialogs**: publishing App Privacy pops a second "Publish Your App
  Privacy Responses?" dialog - ending on the first Publish leaves it unpublished.
- Verify by **re-reading, never by the click**: fresh snapshot, look for the persisted
  value ("Published a few seconds ago", the compliance row's status, the saved URL).

## Field-Tested Flows

- **App Privacy, no data collected** (browser-only; publish required before app review):
  privacy page -> `Get Started` -> dialog "Data Collection" -> click the text
  "No, we do not collect data from this app" -> `Save` -> page-level `Publish` (enables
  after the answer) -> confirm dialog `Publish` -> verify "Published ... ago".
- **EU DSA trader status** (browser-only; account-level, needed for EU listing):
  `/business` -> Compliance table -> button `Digital Services Act` -> dialog with
  "I'm a trader" / "I'm not a trader" radios (non-trader = no contact info displayed; right
  for a free non-commercial app) -> `Done` -> verify the dialog closed and the row shows
  the updated date + Active.
- **API key creation**: Users and Access > Integrations > App Store Connect API ->
  Generate; role App Manager suffices for uploads + full listing staging. Capture the
  one-time `.p8` with `page.expect_download()`; store per `ai/.memory/credentials.md`.
- **Attach a build** (browser path; also fine over the API): version page -> `Add Build` ->
  the dialog lists processed builds (a build appears with its icon once Apple finishes
  processing, typically 15-60 min after upload) -> check the radio (`Done` stays disabled
  until one is selected) -> `Done` -> `Save` -> verify the Build section shows the version
  string with a Delete control.
- **IAP setup** (browser; needed before any API key exists): the IAP page drives price,
  localization (Display Name is an unnamed textbox - fill by index), availability, and the
  review screenshot (see Uploads); a first IAP is submitted together with the app version,
  not on its own.

## Forms, Saving, Uploads

- Fill by accessible name: `page.get_by_role("textbox", name="Support URL").fill(...)`;
  unnamed dialog textboxes by index (see the dialogs section).
- **Wizard walker** (age rating and co, when done by hand): per step pick the No/None radio
  in each row group, `Next`; watch for a trailing **Confirm** step - pricing ends on one,
  and stopping at the last `Next` leaves the change unapplied. Verify the computed result
  (rating badge, "Schedule a price change") in a fresh snapshot. (Prefer the API for age
  rating.)
- Uploads: `expect_file_chooser` can silently fail on ASC uploaders (Save never enables, no
  error) - drive the hidden input directly:
  `page.locator("input[type=file]").last.set_input_files([...])`, then verify a thumbnail
  (with its Delete control) appears before saving. Upload order = display order - to
  reorder, Delete All and re-upload one file at a time. Size limits fail silently (the file
  just does not take) - resize first (`sips -z <h> <w> shot.png`). Field-verified sizes:
  mac screenshots 2880x1800; iPhone 6.9-inch 1320x2868; IAP review screenshot exactly
  1280x800. (Prefer the API for app screenshots.)

## Guardrails

- Every mutating ASC action is **outward** - the safety rule applies: confirm first unless
  a standing autonomy grant covers the task; genuinely irreversible steps (Submit for
  Review, publishing App Privacy, agreements, trader status) still get a one-line heads-up.
- The Apple session lives **only** in the browserctl profile - never export cookies or
  tokens, never launch the profile outside browserctl (a foreign launch purges cookies),
  never paste session material into logs or commits.
- Keep account identifiers, contact details, and resource ids out of shareable files -
  they live in `ai/.memory/`.

## Maintenance (Standing Duty)

This plugin's skills are **living documents** (owner directive, 2026-08): fold every newly
field-verified App Store lesson into the matching skill (browser technique here, API
material in [`asc-api.skill.md`](asc-api.skill.md)) the same session it is learned. Rewrite
in place, keep identifiers out, bump the file's rev (`revs bump` + `revs ledger`), commit
the framework repo. A lesson that lives only in memory files or a chat transcript is
considered lost.