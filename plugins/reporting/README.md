# reporting - Solaris Plugin <!-- omit in toc -->

- [What It Is](#what-it-is)
- [Files](#files)
- [Install](#install)
- [Changelog](#changelog)

## What It Is

Findings reports for a project: one self-contained HTML source per report in `reports/html/` (git-ignored working
files), styled by a shared stylesheet with tokens, rendered to tracked PDFs in `reports/` with no npm dependencies
(the installed Google Chrome over the DevTools protocol, two header passes merged with poppler's `pdfunite`), then
an automatic layout check for half-empty pages and runts. The theme and the page furniture are the project's own
config: `reports/theme.css` and `reports/report.json`.

## Files

| File | Role |
|---|---|
| `shared/report.skill.md` | The skill (name `report`, triggers such as "create report", "render report"): files and naming, theming, document structure, content and writing rules, figures, the page layout check, rendering, the checklist before done. |
| `shared/assets/style.css` | The base stylesheet every report links, with tokens the project's `reports/theme.css` overrides. |
| `shared/assets/render.sh` | Renders every report in `reports/html/`, or the slugs given; `--live` re-renders on change, `--reap` closes report-render Chromes an interrupted render left. |
| `shared/assets/render.js` | The renderer: Chrome over the DevTools protocol (Node 22 or newer), page furniture from the HTML and `report.json`, the two passes merged, then `check.js`. |
| `shared/assets/check.js` | The layout check of a rendered PDF: half-empty pages and runts (exit 1 when anything is flagged). |
| `migrations/0.2.0.md` | Steps for copied installs from 0.1.x: the plugin renamed from `report` and its copy moved to `<pack>/plugins/reporting/`. |

`manifest.json` (its `setup` notes list the requirements and the working layout) and `revisions.json` (rev ledger,
managed by `solaris.tools.revs`) complete the plugin.

## Install

"install plugin reporting to `<project>`" copies `shared/` into `<pack>/plugins/reporting/`, where `<pack>` is the
project's ai-pack folder (default `aipack/`, `ai/` in older projects, any name). Then create `reports/`,
`reports/html/` (git-ignored), `reports/theme.css` and `reports/report.json` as the manifest's setup notes say.

## Changelog

- 0.4.2: the renderer's header comment and the skill's summary drop an internal name for the document style, now
  "the house document style" (`render.js` rev 7, `report.skill.md` rev 12); no change in behaviour. A plain copy
  update.
- 0.4.1: the manifest declares aisee and visual-qa as optional dependencies (the page-by-page visual check).
- 0.4.0: the renderer closes its Chrome on every exit and first clears any Chrome an interrupted render left
  (`render.sh --reap` does only that); tall rows of split tables break across pages; paragraphs and table cells
  are kept free of one-word last lines.
