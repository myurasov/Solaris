---
name: kaggle-leaderboard
triggers: ["leaderboard", "leaderboard history", "competitor progress", "monitor the leaderboard", "leaderboard snapshot", "who is climbing"]
summary: Leaderboard history for any Kaggle competition - every leaderboard read is saved as a local snapshot of the public leaderboard fields, so each team's progress can be followed over time. Covers kaggle_lb.py (snapshot, show, history, movers, new-teams, summary, record-raw, import), the storage layout, an hourly cadence, privacy, and reading progress over time.
---
_Rev. 2_

# Skill: kaggle-leaderboard - Leaderboard History <!-- omit in toc -->

- [When to Use](#when-to-use)
- [What Is Stored and Why](#what-is-stored-and-why)
- [Commands](#commands)
- [Cadence](#cadence)
- [Privacy](#privacy)
- [Reading Progress Over Time](#reading-progress-over-time)

## When to Use

Whenever an agent reads a competition's leaderboard, and whenever the question is how the field, a
rival, or our own team is moving. Read the leaderboard with `kaggle_lb.py show` rather than a bare
`competitions leaderboard` call: it walks every page and saves the read, so no read is lost. Kaggle
access and sign-in belong to the `kaggle-cli` skill; this tool calls the same gateway.

## What Is Stored and Why

A leaderboard shows only the present. Saved reads show who is climbing and how fast, when the top
moves, when a public notebook floods the board with copies (many new teams at one score), and how the
gap to the leaders changes - evidence for planning, and for reading our own scores against the field.

Each read becomes one snapshot:

- **Header:** `fetched_at` (UTC, when the read started), `slug`, `row_count`, `pages`, `source`
  (`snapshot`, `gateway`, `record-raw`, `import`), `format`, and two flags: `partial` (not the whole
  board - a page failed, the walk was capped, more pages were not fetched, or a later page whose ranks
  are unknown; `note` says which) and `imported` (backfilled from an earlier dump).
- **Rows**, as the leaderboard returns them: `rank`, `team_id`, `team_name`, `score` (the text Kaggle
  sent), `submission_date` (UTC, as sent). The API sends no rank, so `rank` is the 1-based position in
  Kaggle's order; a host benchmark row takes a position too, so teams below one read one place lower
  than the website shows.

Layout (local-only and git-ignored, under Solaris's `__*/` convention):

```text
<context>/__data/kaggle/<slug>/leaderboard/
    index.jsonl                         one line per snapshot: time, rows, flags, top score
    20260929T022131Z.json.gz            a full read (gzip JSON), named by its UTC time
    20260927T113302Z-imported.json.gz   a backfilled read; -partial marks an incomplete one
```

`<context>` is the project root or task folder. `--dir <base>` or `KAGGLE_LB_DIR=<base>` moves the
store to `<base>/<slug>/`. Snapshots are never overwritten (a second read in the same second gets
`-2`) and never deleted; a lost `index.jsonl` is rebuilt from the files. A full read of about 1,700
teams takes about 45 KB.

## Commands

Run from the project root or task folder:

| Context | Command |
|---|---|
| Project, plugin copied | `python3 <pack>/plugins/kaggle/tools/kaggle_lb.py <command> <slug> ...` |
| Project linked, or ad-hoc task | `python3 <solaris>/plugins/kaggle/shared/tools/kaggle_lb.py <command> <slug> ...` |

| Command | Does |
|---|---|
| `snapshot <slug>` | Reads the whole board through the gateway (`--format json`, 200 rows per page, following the page tokens) and saves it. |
| `show <slug> [--top N]` | Takes a snapshot, then prints the top N (default 20) with each team's move since the previous full snapshot. |
| `history <slug> --team <name or id>` | One team across all snapshots: rank, score, submission date, flags. A name matches exactly, then by part; an ambiguous name lists the team ids. |
| `movers <slug> --since <hours>` | Largest score improvements and rank climbs from the board `<hours>` ago (the newest full snapshot at or before then) to the latest full one. |
| `new-teams <slug> --since <hours>` | Teams on the latest full board that the board `<hours>` ago lacked, with when each was first seen, and how many teams left (merged or removed). |
| `summary <slug> [--top N]` | Snapshot count and time span, then the current top N's rank and score across up to six full snapshots, with the field's row count and #1, #10 and #100 scores. |
| `record-raw <slug> --file <path>` | Saves a leaderboard response fetched another way (any CLI output format; `-` reads stdin; `--later-page` for a `--page-token` response, whose ranks are unknown). |
| `import <slug> <file>...` | Backfills earlier reads as imported snapshots: a downloaded board (the `competitions leaderboard <slug> --download` zip or its CSV, timed by the UTC stamp in the CSV name) or saved CLI pages in page order (timed by the earliest file). `--fetched-at` overrides the time; a read already imported at that time is skipped. |

Only `snapshot` and `show` call Kaggle, read-only, about one call per 200 teams. They take
`--gateway <path>` (default: the `kaggle.py` beside the tool), `--page-size` and `--max-pages`. A
failed page ends the walk and the read is saved partial; a failed first page saves nothing. After a
429, wait - never loop. `tee_leaderboard()` in the tool is a hook for the gateway: it passes a raw
`competitions leaderboard` call through unchanged and saves the read (`KAGGLE_LB_RECORD=0` skips the
save):

- **`--show`:** the rows it printed (one page: partial when Kaggle offers a next page).
- **`--download`:** the zip it wrote, as a full read timed by the UTC stamp in its CSV name; the same
  board file is saved once. The zip is looked for in the `-p` folder, else in
  `$KAGGLE_PATH/competitions/<slug>/`, else in the working folder.
- **No slug given:** the CLI's default competition, named on its `Using competition:` line, else
  `KAGGLE_COMPETITION`.

The hook reads no credential or config file, so it saves nothing (and says so on stderr) for a call
at the framework root unless `KAGGLE_LB_DIR` names a store, for a `--quiet` call without a slug and
without `KAGGLE_COMPETITION`, and for a download that went to a download folder set in the CLI's
config. Name the competition, pass `-p`, and call from the project or task folder.

## Cadence

- **Every read:** read the board with `show` (or `snapshot`), so every read is saved.
- **At least hourly while the competition runs.** A scheduled `snapshot` needs no agent - cron,
  launchd or the harness scheduler, started in the context folder. For example (crontab, a minute off
  the hour):

  ```text
  17 * * * * cd <project> && PATH=<uv dir>:$PATH python3 <pack>/plugins/kaggle/tools/kaggle_lb.py snapshot <slug> >> __out/kaggle-lb.log 2>&1
  ```

  The gateway needs `uv` on PATH. The OAuth login expires after about 12 hours, so unattended
  snapshots need the long-lived API token (the `kaggle-cli` skill's Signing In).
- **More often around events** - the final days, a notable public notebook, the daily submission
  reset - while reads stay at a few per hour.
- **At the end:** a snapshot of the final public board before the deadline, and one after the final
  standings are published.

## Privacy

- Public leaderboard fields only - what anyone sees on the competition's leaderboard page. No profile
  lookups, no team-member lists, no scraping of other pages, no joining the history with other data
  about people. On import, a downloaded board's member usernames and submission counts are dropped.
- The history stays local in `__data/` (git-ignored): never commit, publish or paste it outside the
  project. Reports quote team names and scores as the board shows them, or aggregates.

## Reading Progress Over Time

- **Start with `summary`:** the span, the top's trajectory, and the field's #1, #10 and #100 scores
  over time show how fast the competition moves.
- **`movers --since 24` once a day:** who improved most. A big climb on a small score gain means a
  crowded score band.
- **`new-teams --since 24`:** a burst of new teams at one score usually means a public notebook was
  published or updated.
- **`history --team <name or id>`** for a rival or our own team; follow a team by id, since names
  change.
- **Noise:** public scores come from a small public split. A move smaller than the noise band
  (estimate it per competition - the `how-to-kaggle` skill) is not progress, and the private split can
  reorder the board.
- **Which snapshots count:** `movers`, `new-teams` and `summary` use full snapshots only; `history`
  shows partial and imported ones too, flagged. Whether a higher or a lower score is better is read
  from the board's order.
- **Custom analysis:** each snapshot is plain gzip JSON (`json.load(gzip.open(path, "rt"))`), and
  `index.jsonl` is the catalogue.