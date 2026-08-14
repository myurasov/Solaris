---
name: report
triggers:
  - "create report" / "new report" / "findings report"
  - "render report" / "regenerate report pdf" / "report pdf"
summary: Author and render a project findings report - HTML source in
  reports/html/ (gitignored) in the Co-SA-derived document style, tracked PDF in
  reports/ rendered zero-dependency (Chrome over the DevTools protocol, two passes
  merged with poppler pdfunite). Styling/rendering tooling is this plugin's assets/;
  theme and page furniture are project-owned config (reports/theme.css + reports/report.json).
---
_Rev. 4_

# Skill: report - findings reports (HTML + PDF) <!-- omit in toc -->

- [When to Use](#when-to-use)
- [Files and Naming](#files-and-naming)
- [Source of Truth + Theming](#source-of-truth--theming)
- [Document Structure](#document-structure)
- [Content Rules](#content-rules)
- [Charts](#charts)
- [Rendering the PDF](#rendering-the-pdf)
- [Checklist Before Done](#checklist-before-done)

## When to Use

**A report is produced upon completing every experiment or milestone** (baseline
reproductions, optimization experiments, phase summaries, finals). **Living overview
documents** are updated the same turn new results land - refresh their
graphs/tables/status and bump their Rev. One self-contained HTML file per report; the PDF
is a render artifact of that HTML, never hand-edited.

## Files and Naming

- **Tracked deliverable**: `reports/<MMDD>-<slug>.pdf` (MMDD = run date in the project's
  timezone - matches the H1 title prefix; kebab-case slug), re-committed whenever
  re-rendered.
- **Working files** in `reports/html/` (gitignored):
  `<MMDD>-<slug>.html` - the report HTML source (authored/edited here). It links the
  shared stylesheet and the project theme:

  ```html
  <link rel="stylesheet" href="../../ai/plugins/reporting/assets/style.css">
  <link rel="stylesheet" href="../theme.css">
  ```

  Report-specific rules go in a small inline `<style>` after the links.
- **Project-owned config** (tracked, survives plugin updates):
  - `reports/theme.css` - CSS token overrides on `.viz-root` (see Theming below).
  - `reports/report.json` - page furniture: `{"prepared_by": "...", "watermark": "...",
    "furniture_font": "..."}` - all keys optional. `prepared_by` is a fixed-byline override;
    leave it unset so each report names its actual author (the rendering developer's
    `git config user.name <user.email>`).

## Source of Truth + Theming

The styling + rendering tooling is materialized from the `reporting` plugin into
`ai/plugins/reporting/assets/`; the plugin masters (`plugins/reporting/shared/assets/`
under a Solaris checkout) are the editing source of truth. Standalone, edit the materialized copies and
they fold back on the next plugin update (import-plugin Mode A picks up the higher revs).

| File | What it is |
|---|---|
| `style.css` | The stylesheet every report links. Organized in commented sections (tokens, typography, headings, TOC, tables, diagrams, print, screen). **Defaults: Helvetica Neue body font, Solaris purple accent (#6A1B9A)** |
| `render.js` | Zero-npm-dep PDF renderer: drives installed Chrome over the DevTools protocol (plain Node >= 22, built-in WebSocket; `$CHROME` overrides the binary path), merges two passes with poppler `pdfunite`. Owns page geometry (Letter, margins) and the page furniture: page-1 header "Prepared by ... on <render date, HH:MM TZ>" (byline: `report.json` `prepared_by` override, else the rendering developer's git identity, else plain "Rendered on ..."), pages-2+ header "<H1 title> - <subtitle>", footer watermark (from `report.json`; none by default) + date parsed from the H1's MMDD prefix |
| `render.sh` | The single render entry point, run from the project root or `reports/`: `render.sh` (all), `render.sh <slug> ...` (some), `render.sh --live [slug ...]` (watch sources + theme, re-render on change) |

**Theming** is two-layer, so the plugin copy stays generic and project identity lives in
the project: `style.css` tokens define the defaults; `reports/theme.css` overrides tokens
per project on `.viz-root`, e.g. the NVIDIA look:

```css
.viz-root {
  --font-sans: "NVIDIA Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
  --accent: #76b900; /* NVIDIA green */
}
```

plus `"furniture_font": "NVIDIA Sans"` in `reports/report.json` (headers/footers render
outside the page CSS, so the furniture font is set there). The theme applies only where
linked: every report source must carry BOTH `<link>` lines from Files and Naming above -
a source that omits the `../theme.css` link silently renders with plugin defaults. Keep
every project-specific value (watermark text, byline, fonts, colors) in `theme.css` /
`report.json`, never in the plugin-managed assets.

## Document Structure

In order, inside `<body class="viz-root"><main>`:

1. **H1**: `<MMDD> <Title><br><span class="h1-subtitle"><Document Type> — Rev. N</span>` -
   run date prefix (e.g. `0804`), Title Case, subtitle on its own line carrying the report's
   **revision number**: increment Rev. N (subtitle + `<title>`) every time the report gets
   updated experiment content or a substantial formatting/wording change; the filename never
   changes. The render script parses the MMDD prefix (footer date) and the H1 + subtitle
   (pages-2+ header, so the rev shows on every page).
2. **`.sub` paragraph**: run provenance one-liner - when/where/what hardware, framework +
   seed, code state, what the reference rows are.
3. **Inline TOC**: `.toc` div with `--toc-rows: ceil(items/N)` and a `.toc-list` OL of
   anchor links to the `h2` ids.
4. **Headline table** (`table.headline`): Metric | Value | Note rows - never stat
   tiles/boxes. May sit inside its own first `<section>` with an H2 when titled.
5. **Sections**: `<section><h2 id="...">` - H2s carry NO hardcoded numbers (a CSS
   counter numbers them in the accent color); Title Case headings. **Every experiment report
   ends with a Conclusion section** - the LAST section, after environment/provenance: the
   verdict in a few plain sentences, closing with one sentence that summarizes the results
   and the acceptance decision.
6. **`.foot` paragraph**: sources/provenance line. No "Prepared by" in the body -
   attribution lives in the page-1 PDF header (render script).

Every table's header row lives in `<thead>` (repeats when a table splits across pages;
never hangs alone at a page bottom). Tables that may split get `class="split"`; all
others move to the next page whole.

## Content Rules

- **Title Case** for the title and all headings/TOC entries.
- **Tables**: cells are horizontally and vertically centered by default; long prose cells and
  the first (row-label) column get `class="lft"`; give header cells explicit
  `style="width:N%"` hints so no column starves or balloons. Comment/note cells are sentence
  case (start with a capital) - except cells that are literal identifiers (mode names, flags,
  code). `class="hl"` on a `<tr>` marks the one attention row (highlighter yellow).
- **Bold sparingly and only for good news**: positive speedup numbers and important/positive
  outcome words ("unchanged", "accepted", "identical", "match to the digit"). Never bold whole
  cells or sentences, and never bold negative/neutral outcomes ("rejected", "split").
- Em dashes for spaced hyphens, en dashes for numeric ranges, in prose only (NEVER
  touch JavaScript - arithmetic minus signs break).
- Every technical term gets a ~10-15-word parenthetical explanation in plain words at first
  use; no buzzwords ("lever", "story", "headroom").
- Timestamps in the project's timezone, labeled.
- **Benchmark/experiment reports: tables only (no charts in the PDF) and no cost/pricing
  mentions in partner-shareable reports** (costs live in local-only ledgers).
- All numbers measured, none extrapolated; the `.foot` line states sources.
- Partner-shareable documents carry no internal file paths, instance names, or ops detail.

## Charts

Charts are for the interactive HTML only; the PDF carries tables. When a report includes
charts, wrap each chart + legend in `<div class="chart-block">`, put its styles in the
report's inline `<style>`, and hide `.chart-block` under `@media print` there. Diagrams
(`.diagram`, e.g. a dependency graph SVG) are different: they ARE the deliverable and DO
print - their styles live in the shared stylesheet. Series colors follow the validated
dataviz palette; the accent color is decorative only, never a data encoding.

## Rendering the PDF

```bash
ai/plugins/reporting/assets/render.sh <MMDD>-<slug>       # render one (no arg: render all)
ai/plugins/reporting/assets/render.sh --live [slug ...]   # watch sources and re-render on save
# or directly:
node ai/plugins/reporting/assets/render.js reports/html/<slug>.html reports/<slug>.pdf [--watermark "TEXT"]
```

Chrome cannot vary headers per page, so the script renders page 1 and pages 2+ as two
passes over the same layout and merges them (identical pagination is why the HTML must
not carry per-page margin exceptions). Do not put headers/footers/attribution in the HTML
body - the render script owns all page furniture.

## Checklist Before Done

1. HTML opens correctly in a browser (TOC anchors jump; any diagrams render).
2. PDF re-rendered from the final HTML; open it and eyeball every page: header/footer
   slots, no blank half-pages, no orphaned headings, no hanging table headers.
3. PDF committed (with this skill rev-bumped if a rule changed);
   report referenced from the project README's results section when it is a milestone.
4. Log the interaction; update the project's living plan/status doc with the milestone.