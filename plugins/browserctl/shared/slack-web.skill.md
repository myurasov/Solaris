---
name: slack-web
triggers: ["slack web", "sweep slack in the browser", "capture slack channel", "read slack via browser", "post to slack via browser", "react to slack messages", "slack thread capture", "download slack attachments"]
summary: Operate the Slack web client through browserctl - capture channels and threads (scroll-and-snapshot), search, download attachments, and (on explicit request) react or post - with the field-tested selectors, composer techniques, and pitfalls.
---
_Rev. 2_

# Skill: slack-web - Operating Slack Through the Browser <!-- omit in toc -->

- [When to Use](#when-to-use)
- [Session Prelude](#session-prelude)
- [Finding Activity: Search](#finding-activity-search)
- [Capturing a Channel: Scroll-and-Snapshot](#capturing-a-channel-scroll-and-snapshot)
- [Capturing Threads](#capturing-threads)
- [Reading Captured Data: Pitfalls](#reading-captured-data-pitfalls)
- [Downloading Attachments](#downloading-attachments)
- [Write Actions: React and Post (On Request Only)](#write-actions-react-and-post-on-request-only)
- [Composer Technique Notes](#composer-technique-notes)
- [Guardrails](#guardrails)

## When to Use

Any task that must read from or act on Slack through its **web client** - capturing channel or
thread history, checking for new activity, downloading shared files, adding a reaction, or
posting - when no API/bot access is available or the content is only reachable as the signed-in
user (e.g. Slack Connect channels). All browser work runs through **browserctl**
([`browserctl.skill.md`](browserctl.skill.md) has the command reference); Slack-specific
mechanics live here.

## Session Prelude

1. **Pick a profile** per the browserctl conventions: a purpose-named profile (e.g. `slack`)
   the first time, reused afterward - the login persists in it. Unattended runs launch
   `--headless`; interactive ones `--headed --minimized` (then `show` on demand).

   ```bash
   uv run <overlay>/browserctl.py launch --profile slack --headless \
       --url https://app.slack.com/client
   uv run <overlay>/browserctl.py eval --profile slack \
       --js "({title: document.title, url: location.href})"
   ```

2. **Login check:** if the eval lands on a sign-in page, relaunch `--headed`, `show` the
   window, and ask the user to log in once - the session persists in the profile afterward.
3. **Blank-shell recovery:** the Slack client sometimes hangs on a blank shell (title "Slack",
   no message list). Reload once (`eval --js "location.reload()"`); if still blank, `navigate`
   to the client URL again - that recovers it reliably.
4. Channel URLs are `https://app.slack.com/client/<team_id>/<channel_id>`; message permalinks
   are `https://<workspace>.slack.com/archives/<channel_id>/p<ts>` (add `?thread_ts=<parent_ts>`
   for thread replies). Some enterprise workspaces interpose a "Redirecting..." interstitial on
   permalinks - click its "open this link in your browser" link to land in the web client.

## Finding Activity: Search

- Open `https://app.slack.com/client/<team_id>/search`. **Filters like `from:` must be
  tokenized via the typeahead**: type `from:me` (or `from:@Name`), wait for the suggestion
  list, and click the suggested option. Typing the filter and pressing Enter searches it as
  literal text and returns "Nothing turned up". Set **Sort: Newest**.
- Page through results ("Next page") until timestamps cross the window of interest; collect
  the distinct conversations as capture targets.
- The left-sidebar snapshot is a cheap channel inventory (starred channels; Slack Connect
  channels are often prefixed, e.g. `ext-*`).

## Capturing a Channel: Scroll-and-Snapshot

Slack's message list is **virtualized** - only rendered rows exist in the DOM, so a single
snapshot holds one viewport's worth. To capture a window of history:

1. `navigate --profile slack --url https://app.slack.com/client/<team_id>/<channel_id>`.
2. `snapshot --profile slack --out <channel>-s0.yml`. **Verify non-zero size** - a snapshot
   taken while the page was still loading can be near-empty.
3. **Scroll up and snapshot after every scroll step.** Content that scrolled past without a
   snapshot is lost. Scroll via `eval` on the message pane's scroller:
   - The page has several `.c-scrollbar__hider` elements - pick the message pane's by
     comparing `clientHeight`/`scrollHeight` (the sidebar one is small), and **verify
     `scrollTop` actually moves** between steps.
   - browserctl's headless window is 1440x2400, so each step covers a real page; if the pane
     reads only ~150 px tall the session predates that default - relaunch, or
     `page.set_viewport_size()` via `attach()`.
4. Continue until the capture window is covered (a date horizon, or content already known).
5. Keep a running list of snapshot files; screenshots are for the agent's own verification
   only - the parsed record of truth is the aria-snapshot YAML.

In `attach()` scripts, always `page.goto(url, wait_until="domcontentloaded")` - Slack never
fires `load` and the default wait times out.

## Capturing Threads

Thread replies live in a separate pane and do not appear in the channel scroll.

- **Find threads via reply bars.** Reply-bar text comes in **three formats** - a filter must
  handle all of them or it silently skips threads:
  - `N replies | Last reply X ago` (multi-reply, relative time)
  - `1 reply | X ago` - single-reply bars **drop the "Last reply" prefix**
  - `N repl(y|ies) | Today/Yesterday at H:MM AM/PM` (very recent, absolute time)
- **Re-query after every click.** Opening the thread pane narrows the channel pane, the
  virtualized list re-lays out, and stale locator indexes skip the trailing (most recent)
  bars. Pattern: find the *first* unseen in-window reply bar, click it, snapshot, close the
  pane, re-enumerate from scratch; track seen parents by permalink timestamp.
- The thread pane is `[data-qa="threads_flexpane"]`; it virtualizes too - scroll it the same
  way. Jump straight to its top with:
  `eval --js "document.querySelector('[data-qa=\"threads_flexpane\"] .c-scrollbar__hider').scrollTop = 0"`.
- **Old-parent threads** (parent message outside the visible window) are reached via a reply's
  permalink with `?thread_ts=<parent_ts>` - the web client opens with the thread pane up.
- Reply-count detection: parse `N repl(y|ies)` from the message row's `innerText` - the
  reply-bar count selector often resolves but returns empty; text-matching is reliable.

## Reading Captured Data: Pitfalls

- **Exact timestamps and identity come from permalinks and row HTML**, not visible text: each
  row's permalink carries the canonical `p<ts>`; mentions carry `data-member-id="U..."`.
- **Sender attribution:** consecutive messages from one sender render condensed (no sender
  button on the later rows), and thread replies whose sender fell outside the snapshot can
  appear to inherit the previous sender. Verify senders against the visible pane before
  relying on them; mark anything unresolvable as unattributed rather than guessing. Never map
  a bare handle to a real person by name resemblance.
- Rich message content renders as separate aria nodes - `@mention` and link-anchor text can
  read as detached from the surrounding sentence; reconstruct from the snapshot structure.
- **Watch for `(edited)`:** a message can change after first capture (e.g. a mention appended)
  - re-read the full row text on later passes; do not trust the first capture.
- **Member list:** click the channel-header members button (`data-qa="channel_members_button"`),
  then collect `[role="listitem"]` innerTexts from the dialog, scrolling its container to load
  the full list.

## Downloading Attachments

File bytes require the authenticated session - fetch them through the browser with a download
event in an `attach()` script:

```python
with bctl.attach("slack") as (pw, browser):
    page = browser.contexts[0].pages[-1]        # any app.slack.com view
    with page.expect_download(timeout=25000) as dl_info:
        page.evaluate("""(u) => { const a = document.createElement('a');
          a.href = u; a.download = ''; document.body.appendChild(a);
          a.click(); a.remove(); }""", url)
    dl_info.value.save_as(dest)
```

- Use the file's exact `url_private_download` URL (from the row HTML / captured metadata) -
  constructed URLs 404.
- Slack canvases (`application/vnd.slack-docs`) and Google-Docs links
  (`application/vnd.google-apps.*`) are documents, not downloadable files - skip them.
- Downloaded filenames can carry invisible Unicode (e.g. U+202F from macOS screenshot names) -
  locate the saved file with a case-insensitive prefix glob, not a literal path.

**Thread-reply attachments (invisible to fetch tools).** Slack's search/fetch layers return
thread-reply messages **without their `files` array**, so a file shared in a thread never
surfaces outside the browser. The working path is the logged-in session's own client-side Web
API (works on Enterprise Grid external workspaces too):

1. `attach()` and open the **client URL** (`https://app.slack.com/client/<team_id>/<channel_id>`),
   then wait ~15-20 s for the client to boot. Do **not** use the message permalink - the
   workspace-subdomain permalink page never boots the web client headless, leaving
   `localStorage` empty.
2. In `page.evaluate`, read the session token from `localStorage.localConfig_v2` -
   `JSON.parse(...).teams[...]`, picking the team whose `id` matches the target workspace -
   and POST `token`/`channel`/`ts` as FormData to
   `https://app.slack.com/api/conversations.replies` with `credentials: 'include'`. The
   **app.slack.com** API host is the one that accepts the token; the workspace-subdomain host
   fails (CORS / `invalid_auth`) even with credentials.
3. Collect `url_private_download` (fallback `url_private`) from each reply's `files`, fetch
   with `ctx.request.get(url)` - the browser context carries the session cookies - and write
   `resp.body()` to the destination.

The same pattern generalizes to any Slack Web API method the client itself may call.

## Write Actions: React and Post (On Request Only)

Capture is read-only. React/post **only** on an explicit user request or a standing rule
naming the channel and purpose, honoring the project's safety policy (outward action -
confirm first).

**Add an emoji reaction:**

1. Hover the target message row (`page.hover(...)`) so the hover toolbar appears - identify
   the row by sender + permalink timestamp, never by text alone (texts repeat).
2. Click the "Find another reaction" (smiley) button, type the emoji name in the picker
   (e.g. `white_check_mark`), click the match (or Enter on the highlighted one).
3. Re-snapshot and verify the reaction chip shows; retry once if not. Skip messages that
   already carry the reaction - never duplicate (the chip doubles as a processed marker).

**Post a message (channel or threaded reply):**

1. Compose and clear the text against the user's outbound-text conventions first.
2. Threaded reply: hover the parent and click "Reply in thread" (`data-qa="start_thread"`);
   if replies exist, click the reply bar (`data-qa="reply_bar_count"`). Type into the thread
   pane's `.ql-editor` - **scope the composer and send button to
   `[data-qa="threads_flexpane"]`**, otherwise the channel-level selectors match the main
   composer and the reply lands in the wrong place. Channel message: the main composer.
3. **Send with the send button (`data-qa="texty_send_button"`), not Enter** - Enter
   frequently leaves the text as an unsent draft in the web client.
4. Verify: a thread parent's reply bar must show `N repl(y|ies)`, **not `1 draft`** (a draft
   means it did NOT send - reopen, focus the composer, click send). For channel messages,
   re-snapshot and confirm the row exists; report the permalink if needed.

## Composer Technique Notes

Field-tested against the Slack web composer (a Quill `.ql-editor`):

- **@mentions** via typeahead: type `@Name`, wait ~2 s, Enter picks the top match - then type
  an **explicit space** before continuing (the typeahead swallows a trailing space). Verify
  `name + space + word` in the composer text before sending.
- **Mention identity check** (common names!): after the typeahead inserts a mention, read the
  composer `innerHTML` and check the `ts-mention` `data-id` against the intended user id
  (from the source row's `data-member-id="U..."`); on mismatch clear (Cmd+A, Backspace) and
  retry by unique handle.
- Slack markup typed with `pressSequentially` converts live (`_italic_`, `*bold*`).
- **Italic/bold on composed text**: type plain, then Cmd+A, Cmd+I (or Cmd+B). Beware
  Cmd+Shift+X - it toggles ~strikethrough~, not italic. Whole-message italic goes **last**,
  after all typing - mention pills are unaffected.
- **Hyperlinking words**: select them (Shift+Alt+Left per word from the end), Cmd+Shift+U,
  fill the Link field, Save. Pasting a URL over selected text also links it.
- **Slash commands** (e.g. `/invite @Name`): compose like a message (typeahead for the
  mention) and submit with the **send button** - Enter just newlines and strands the command
  as a draft. `/invite` takes **one user per command** (multiple mentions error out and
  nobody is added) - loop one invite + send per person.
- **Bullet lists**: type `- ` at line start - the composer converts to a real `<ul><li>`;
  Shift+Enter continues the next bullet automatically. `innerText` drops the bullet markers -
  verify list structure via `innerHTML`.
- **Typeahead options carry presence/membership hints** (`Not in channel`, OOO status) - free
  signal at compose time for invite targets and do-not-nudge checks.
- **Pre-send verification, two layers**: `innerText` for wording (mention spacing, the exact
  expected string - abort and clear on mismatch rather than sending broken text), `innerHTML`
  for structure (list tags, `<em>`, mention `data-id`s).

## Guardrails

- **Capture never writes**: no posting, reacting, draft-editing, or marking channels read
  during a sweep. If a compose box holds an unsent user draft, do not touch it - and mention
  it to the user.
- Posting requires explicit per-message user approval unless a standing rule grants autonomy
  for a specific channel + purpose; reactions likewise run only on explicit instruction or
  standing rule, only with the emoji it names. A reaction asserts that a human's agent read
  the message - never imply attention that was not given.
- Respect confidentiality on captured content - shared channels contain external
  participants; anything reused outside the project goes through the usual scrub.