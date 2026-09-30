// rev. 1

// Layout check for a rendered report PDF - ZERO npm deps; needs poppler's
// pdftoppm + pdftotext (brew install poppler, which the renderer already
// needs for pdfunite). render.js runs it after every render; standalone:
//
//   node check.js <report.pdf> [--json]      exit 1 when anything is flagged
//
// Two checks:
//   half-empty pages - the page body ends more than 10% above the bottom
//           margin (usually a figure or table that moved to the next page);
//           the last page is exempt. Measured on a 40 dpi grayscale raster of
//           the body area inside render.js's page margins.
//   runts  - wrapped text (paragraph, list item, caption, table cell) whose
//           last line is one word or at most 10 letters. Found in pdftotext's
//           word boxes: a short line right after a full-width one.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const GAP_LIMIT = 0.10;
const DPI = 40;
const PAGE_IN = { w: 8.5, h: 11 };
// body area inside render.js's margins (0.65in top/bottom, 0.45in sides)
const BODY_IN = { top: 0.70, bottom: 10.30, side: 0.45 };
// word boxes shorter than this (pt) are chart labels, not prose
const MIN_TEXT_H = 8.0;

const isSpace = b => b === 0x20 || b === 0x0a || b === 0x0d || b === 0x09;

function readPgm(file) {
  const buf = fs.readFileSync(file);
  const fields = [];
  let i = 0;
  while (fields.length < 4) {
    while (isSpace(buf[i])) i++;
    if (buf[i] === 0x23) { while (buf[i] !== 0x0a) i++; continue; }
    const s = i;
    while (!isSpace(buf[i])) i++;
    fields.push(buf.toString('ascii', s, i));
  }
  if (fields[0] !== 'P5') throw new Error(`${file}: not a binary PGM`);
  return { w: +fields[1], h: +fields[2], px: buf.subarray(i + 1) };
}

// Empty fraction at the bottom of each page body, in page order.
function pageGaps(pdf) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'report-check-'));
  try {
    execFileSync('pdftoppm', ['-gray', '-r', String(DPI), pdf, path.join(tmp, 'p')]);
    return fs.readdirSync(tmp).filter(f => f.endsWith('.pgm')).sort().map(f => {
      const { w, h, px } = readPgm(path.join(tmp, f));
      const top = Math.round(BODY_IN.top / PAGE_IN.h * h);
      const bot = Math.round(BODY_IN.bottom / PAGE_IN.h * h);
      const x0 = Math.round(BODY_IN.side / PAGE_IN.w * w), x1 = w - x0;
      let last = top;
      scan: for (let y = bot - 1; y >= top; y--) {
        for (let x = x0; x < x1; x++) if (px[y * w + x] < 200) { last = y; break scan; }
      }
      return (bot - last) / (bot - top);
    });
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

const decode = s => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
  .replace(/&#39;|&apos;/g, "'").replace(/&amp;/g, '&');
const num = (attrs, k) => parseFloat((attrs.match(new RegExp(`${k}="([-\\d.]+)"`)) || [0, 'NaN'])[1]);
const box = attrs => ({ x0: num(attrs, 'xMin'), y0: num(attrs, 'yMin'), x1: num(attrs, 'xMax'), y1: num(attrs, 'yMax') });

// Short last lines of wrapped text: [{page, line, context}].
function runts(pdf) {
  const xml = execFileSync('pdftotext', ['-bbox-layout', pdf, '-'], { encoding: 'utf8', maxBuffer: 1 << 28 });
  const found = [];
  xml.split(/<page\b/).slice(1).forEach((pageXml, pi) => {
    for (const bm of pageXml.matchAll(/<block\b([^>]*)>([\s\S]*?)<\/block>/g)) {
      const b = box(bm[1]);
      const bw = b.x1 - b.x0;
      const lines = [...bm[2].matchAll(/<line\b([^>]*)>([\s\S]*?)<\/line>/g)].map(lm => ({
        ...box(lm[1]),
        words: [...lm[2].matchAll(/<word\b[^>]*>([^<]*)<\/word>/g)].map(wm => decode(wm[1])),
      }));
      for (let i = 1; i < lines.length; i++) {
        const cur = lines[i], prev = lines[i - 1];
        const h = cur.y1 - cur.y0;
        if (h < MIN_TEXT_H) continue;
        if (cur.y0 - prev.y1 > 0.8 * h) continue;
        const text = cur.words.join(' ');
        const letters = (text.match(/[\p{L}\p{N}]/gu) || []).length;
        if (!(cur.words.length === 1 || letters <= 10)) continue;
        if (prev.x1 < b.x1 - 0.2 * bw) continue;
        const left = (Math.abs(cur.x0 - prev.x0) < 3 || prev.x0 < cur.x0) && cur.x1 < b.x1 - 0.25 * bw;
        const centered = Math.abs((cur.x0 + cur.x1) / 2 - (b.x0 + b.x1) / 2) < 3 &&
          (cur.x1 - cur.x0) < 0.6 * bw;
        if (!(left || centered)) continue;
        const context = lines.slice(Math.max(0, i - 2), i + 1).map(l => l.words.join(' ')).join(' | ');
        found.push({ page: pi + 1, line: text, context });
      }
    }
  });
  return found;
}

function check(pdf) {
  const gaps = pageGaps(pdf);
  const halfEmpty = gaps.map((g, i) => ({ page: i + 1, empty: g }))
    .filter(p => p.empty > GAP_LIMIT && p.page < gaps.length);
  return { pages: gaps.length, halfEmpty, runts: runts(pdf) };
}

function summary(r) {
  const bad = r.halfEmpty.length + r.runts.length;
  const out = [`layout check: ${r.halfEmpty.length} half-empty page(s), ${r.runts.length} runt(s)` +
    (bad ? '' : ' - ok')];
  for (const p of r.halfEmpty) {
    out.push(`  page ${p.page}: ${Math.round(p.empty * 100)}% empty at the bottom ` +
      '(something moved to the next page)');
  }
  for (const t of r.runts) out.push(`  runt, page ${t.page}: "${t.line}"  <- ${t.context.slice(0, 110)}`);
  return out.join('\n');
}

module.exports = { check, summary, pageGaps, runts };

if (require.main === module) {
  const args = process.argv.slice(2);
  const pdf = args.find(a => !a.startsWith('--'));
  if (!pdf) { console.error('usage: node check.js <report.pdf> [--json]'); process.exit(2); }
  const r = check(path.resolve(pdf));
  console.log(args.includes('--json') ? JSON.stringify(r, null, 2) : summary(r));
  process.exit(r.halfEmpty.length || r.runts.length ? 1 : 0);
}
