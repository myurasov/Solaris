// rev. 3

// Render a report HTML to PDF in the Co-SA document style - ZERO npm deps.
// Drives the installed Google Chrome over the DevTools protocol using Node's
// built-in WebSocket/fetch (node >= 22), and merges the two passes with
// poppler's pdfunite (brew install poppler).
//
//   node render.js <input.html> <output.pdf> [--watermark "text"]
//
// Page furniture (all pages unless noted), parsed from the HTML + config:
//   header: page numbers top-right; top-left = italic "<prepared_by> on ..."
//           on page 1 ("Rendered on ..." when no prepared_by), "<H1 title> —
//           <subtitle>" on pages 2+
//   footer: watermark bottom-left (from config; none by default), report date
//           bottom-right as `Mon DD YYYY` (from the MMDD H1 prefix)
// Project-owned config `report.json` next to the OUTPUT pdf (all optional):
//   { "prepared_by": "...", "watermark": "...", "furniture_font": "..." }
// Chrome binary: $CHROME overrides the default macOS path.
// Chrome cannot vary headers per page, so page 1 and pages 2+ render as two
// passes over the same layout and are concatenated.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, execFileSync } = require('child_process');

const CHROME = process.env.CHROME ||
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
// Scratch space (Chrome profile + intermediate PDFs) - kept outside the project.
const TMP_ROOT = path.join(os.homedir(), '.solaris', 'tmp');

const args = process.argv.slice(2);
const wmIdx = args.indexOf('--watermark');
let watermark = null;
if (wmIdx !== -1) watermark = args.splice(wmIdx, 2)[1];
const [htmlPath, pdfPath] = args.map(p => path.resolve(p));

// Project-owned furniture config (next to the output PDF).
let cfg = {};
try {
  cfg = JSON.parse(fs.readFileSync(path.join(path.dirname(pdfPath), 'report.json'), 'utf8'));
} catch (e) { /* no config - use defaults */ }
if (watermark === null) watermark = cfg.watermark || '';
// Byline: explicit prepared_by override, else the rendering developer's git
// identity (so reports name their actual author), else none.
function gitByline() {
  try {
    const opts = { cwd: path.dirname(htmlPath), encoding: 'utf8' };
    const name = execFileSync('git', ['config', 'user.name'], opts).trim();
    const email = execFileSync('git', ['config', 'user.email'], opts).trim();
    if (name || email) return `Prepared by ${name}${email ? ` <${email}>` : ''}`.replace('  ', ' ').trim();
  } catch (e) { /* not a git repo or no identity configured */ }
  return '';
}
const preparedBy = cfg.prepared_by || gitByline();
const furnitureFont = cfg.furniture_font || 'Helvetica Neue';

const html = fs.readFileSync(htmlPath, 'utf8');

// Report date from the "0802 ..." title prefix; year from file mtime.
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const m = html.match(/<h1[^>]*>\s*(\d{2})(\d{2})\s/);
const year = new Date(fs.statSync(htmlPath).mtime).getFullYear();
const d = m ? new Date(year, parseInt(m[1], 10) - 1, parseInt(m[2], 10)) : new Date();
const date = `${MONTHS[d.getMonth()]} ${String(d.getDate()).padStart(2, '0')} ${d.getFullYear()}`;

// Pages-2+ header title: H1 main line + .h1-subtitle, em-dash joined.
const t = html.match(/<h1[^>]*>(.*?)(?:<br)/s) || html.match(/<h1[^>]*>(.*?)<\/h1>/s);
// decode entities the title/subtitle may carry (they render as raw text in
// Chrome's header template otherwise); &amp; last so &amp;lt; stays "&lt;"
const decode = s => s
  .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
  .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
  .replace(/&mdash;/g, '—').replace(/&ndash;/g, '–')
  .replace(/&nbsp;/g, ' ').replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const strip = s => decode(s.replace(/<[^>]+>/g, '')).trim();
const sub = html.match(/class="h1-subtitle"[^>]*>(.*?)<\/span>/s);
const title = (t ? strip(t[1]) : '') + (sub ? ` — ${strip(sub[1])}` : '');

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const fontCss = `font-family:'${furnitureFont}','Helvetica Neue',Helvetica,Arial,sans-serif;` +
  'font-size:7.59pt;color:#ccc;';

// Chrome's header/footer templates carry built-in root styles (default
// margins, 8px font) and don't reliably honor `in` units in shorthand
// padding, so the furniture drifted off the body's left edge. Fix: zero the
// root explicitly and use px insets (96px = 1in) matching the page margins.
const INSET_PX = Math.round(0.45 * 96); // = page marginLeft/Right
const boxCss =
  `width:100%;margin:0;display:flex;align-items:baseline;` +
  `padding-left:${INSET_PX}px;padding-right:${INSET_PX}px;${fontCss}`;

const footerTemplate =
  `<div style="${boxCss}padding-bottom:10px;">` +
  `<span style="flex:1;text-align:left;${fontCss}">${esc(watermark)}</span>` +
  `<span style="text-align:right;white-space:nowrap;${fontCss}">${esc(date)}</span></div>`;

const pagesRight =
  `<span style="text-align:right;white-space:nowrap;${fontCss}">` +
  `<span class="pageNumber" style="${fontCss}"></span>` +
  `<span style="${fontCss}"> / </span>` +
  `<span class="totalPages" style="${fontCss}"></span></span>`;
const headerBox = inner =>
  `<div style="${boxCss}padding-top:12px;">${inner}</div>`;
// Render timestamp for the page-1 header: "on Aug 04 2026, 16:20 PDT".
const now = new Date();
const nowParts = new Intl.DateTimeFormat('en-US', {
  month: 'short', day: '2-digit', year: 'numeric',
  hour: '2-digit', minute: '2-digit', hour12: false, timeZoneName: 'short',
}).formatToParts(now).reduce((o, p) => (o[p.type] = p.value, o), {});
const renderedAt =
  `${nowParts.month} ${nowParts.day} ${nowParts.year}, ` +
  `${nowParts.hour}:${nowParts.minute} ${nowParts.timeZoneName}`;

const firstLeft = preparedBy
  ? `${esc(preparedBy)} on ${esc(renderedAt)}`
  : `Rendered on ${esc(renderedAt)}`;
const headerFirst = headerBox(
  `<span style="flex:1;text-align:left;font-style:italic;${fontCss}">${firstLeft}</span>${pagesRight}`);
const headerRest = headerBox(
  `<span style="flex:1;text-align:left;${fontCss}">${esc(title)}</span>${pagesRight}`);

const common = {
  paperWidth: 8.5, paperHeight: 11,
  marginTop: 0.65, marginBottom: 0.65, marginLeft: 0.45, marginRight: 0.45,
  printBackground: true, displayHeaderFooter: true, footerTemplate,
};

// --- minimal CDP client over Node's built-in WebSocket ---------------------
function cdp(ws) {
  let id = 0;
  const waiting = new Map();
  const events = [];
  ws.onmessage = ev => {
    const msg = JSON.parse(ev.data);
    if (msg.id && waiting.has(msg.id)) {
      const { resolve, reject } = waiting.get(msg.id);
      waiting.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    } else if (msg.method) {
      events.forEach(fn => fn(msg));
    }
  };
  return {
    send: (method, params = {}, sessionId) => new Promise((resolve, reject) => {
      const msg = { id: ++id, method, params };
      if (sessionId) msg.sessionId = sessionId;
      waiting.set(msg.id, { resolve, reject });
      ws.send(JSON.stringify(msg));
    }),
    on: fn => events.push(fn),
  };
}

(async () => {
  fs.mkdirSync(TMP_ROOT, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(TMP_ROOT, 'render-'));
  const chrome = spawn(CHROME, [
    '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${tmp}`,
    '--no-first-run', '--disable-extensions', 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });

  const wsUrl = await new Promise((resolve, reject) => {
    let buf = '';
    const to = setTimeout(() => reject(new Error('Chrome did not start')), 20000);
    chrome.on('error', e => {
      clearTimeout(to);
      reject(new Error(`Chrome did not start: ${e.message} (set $CHROME to the Chrome binary)`));
    });
    chrome.stderr.on('data', c => {
      buf += c;
      const mm = buf.match(/DevTools listening on (ws:\/\/\S+)/);
      if (mm) { clearTimeout(to); resolve(mm[1]); }
    });
  });

  const ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const c = cdp(ws);

  const { targetId } = await c.send('Target.createTarget', { url: 'file://' + htmlPath });
  const { sessionId } = await c.send('Target.attachToTarget', { targetId, flatten: true });
  await c.send('Page.enable', {}, sessionId);
  await new Promise(res => {
    c.on(msg => { if (msg.method === 'Page.loadEventFired' && msg.sessionId === sessionId) res(); });
    setTimeout(res, 10000); // fallback
  });
  await new Promise(res => setTimeout(res, 500)); // let inline JS (charts) finish

  const print = async opts =>
    Buffer.from((await c.send('Page.printToPDF', { ...common, ...opts }, sessionId)).data, 'base64');

  const first = await print({ headerTemplate: headerFirst, pageRanges: '1' });
  let rest = null;
  try {
    rest = await print({ headerTemplate: headerRest, pageRanges: '2-' });
  } catch (e) { /* single-page document */ }

  ws.close();
  // wait for Chrome to actually exit - it keeps writing its profile after
  // kill(), and removing the dir under it intermittently throws ENOTEMPTY
  await new Promise(res => {
    chrome.once('exit', res);
    chrome.kill();
    setTimeout(res, 3000); // fallback: do not hang on a stuck Chrome
  });

  if (rest) {
    const f1 = path.join(tmp, 'p1.pdf'), f2 = path.join(tmp, 'rest.pdf');
    fs.writeFileSync(f1, first); fs.writeFileSync(f2, rest);
    execFileSync('pdfunite', [f1, f2, pdfPath]);
  } else {
    fs.writeFileSync(pdfPath, first);
  }
  try {
    fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  } catch (e) {
    console.error(`warning: could not remove scratch dir ${tmp}: ${e.message}`);
  }
  console.log('rendered', pdfPath, '| footer:', watermark || '(no watermark)', date, '| header 2+:', title);
})().catch(e => { console.error(e); process.exit(1); });
