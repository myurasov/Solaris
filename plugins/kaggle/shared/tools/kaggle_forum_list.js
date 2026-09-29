// rev. 1
// Topic extractor for a Kaggle competition's discussion page: the fallback for when the CLI cannot list a forum.
// Run it in the page https://www.kaggle.com/competitions/<slug>/discussion?sort=recent-comments&page=<N>
// (for example: browserctl eval --js "$(cat kaggle_forum_list.js)"), save what it returns as one JSON file
// per page, and pass the files to: kaggle_forum.py list <slug> --from <files>.
// Rows: id, title (the author's name can trail it), comments, votes, when (posted or last comment, as the page
// says it), last_by. Rows without a date are the featured strip or recently viewed links; the tool drops them.
(() => {
  const rows = [...document.querySelectorAll('li')].filter(li => li.querySelector('a[href*="/discussion/"]'));
  const out = [];
  for (const li of rows) {
    const a = li.querySelector('a[href*="/discussion/"]');
    const m = a.href.match(/discussion\/(\d+)/); if (!m) continue;
    const t = (li.innerText || '').replace(/\s+/g, ' ').trim();
    const c = t.match(/(\d+) comments?/); const v = t.match(/arrow_drop_up (-?\d+)/);
    const meta = t.match(/· (Posted|Last comment) ([^·]*?)( by (.*?))? arrow_drop_up/);
    out.push({id: +m[1], pinned: t.startsWith('push_pin'), title: (a.innerText || '').replace(/\s+/g, ' ').replace(/^push_pin |^emoji_people /, '').split(' · ')[0].trim(),
              comments: c ? +c[1] : 0, votes: v ? +v[1] : null, when: meta ? meta[1] + ' ' + meta[2].trim() : null, last_by: meta && meta[4] ? meta[4].trim() : null});
  }
  const pag = [...document.querySelectorAll('nav, [class*=pagination], [aria-label*=page i]')].map(e => (e.innerText || e.getAttribute('aria-label') || '').replace(/\s+/g, ' ').slice(0, 120));
  return {url: location.href, n: out.length, pag, topics: out};
})()
