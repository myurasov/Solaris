# rev. 1

"""kaggle_sdk: reads through Kaggle's Python SDK, of data the Kaggle CLI drops or reads one process per call.

The gateway runs this, read-only, in the pinned CLI's own environment (the
context venv, or the throwaway uv environment at the framework root) when its
first argument is --sdk. It imports the CLI's library, so it does not run on
a plain python3:

    python3 <plugin-dir>/tools/kaggle.py --sdk topic <id>
    python3 <plugin-dir>/tools/kaggle.py --sdk notebooks <competition-slug> [--max N]
    python3 <plugin-dir>/tools/kaggle.py --sdk account [--hours H] [--page-size N] [--done REF=TIME ...]

It signs in as the CLI does (OAuth, access token or kaggle.json). Each read
prints one JSON document on stdout:

topic <id>: the topic with its opening post, and every comment with its
replies nested: {"topic": {...}, "comments": [{..., "replies": [...]}]}.
content is the HTML Kaggle returns; dates print as `forums topics show <id>
--format json` prints them. No author profile paths, usernames or tiers.

notebooks <slug>: up to N notebooks (default 100, 1..1000) in the order of
`kernels list --competition <slug> --sort-by scoreDescending`, each with its
best public score from Kaggle's search, or null when it has none. complete is
false, with a note, when the list stopped at N or a score could not be read
(a failed call, a notebook the search does not find, or more misses than the
single lookups cover).

account: the signed-in account's quota and its own kernels, with the run
state of the recent ones, in one process: {"fetched_at", "since", "quota",
"kernels", "calls", "errors"}. quota is {"gpu": {"used", "remaining",
"total", "refresh"}, "tpu": {...}}, hours to 2 decimals and the reset time as
`quota --format json` prints them ({} when Kaggle reports none). kernels is
one page of N (default 50, 1..100) in the order of `kernels list --mine
--sort-by dateRun`, latest run first, each {"ref", "lastRunTime", "status"},
ref and lastRunTime as that command prints them. status is the run state
`kernels status` reads (QUEUED, RUNNING, COMPLETE, ERROR, CANCEL_REQUESTED,
CANCEL_ACKNOWLEDGED, NEW_SCRIPT) of each of the first 25 kernels last run at
or after since (fetched_at less H hours, default 12), except a run a --done
names (a finished run the caller already knows, by ref and lastRunTime); null
when not read. The list carries no machine fields, so a kernel's accelerator
takes a `kernels pull -m`. A failed call leaves its part null (quota, kernels
or one status), adds one line to errors, and the read goes on; calls counts
the calls made.

Call budget, on top of signing in: topic makes 2 calls plus one per further
page of comments, as the CLI does; notebooks makes one call per 100 notebooks
listed, at most 3 search pages and at most 20 single lookups (at most 24 calls
at the default N, 33 at N=1000), and makes no more calls after a failed one;
account makes 2 calls plus one per status read (at most 27).

A failure exits 1 with one stderr line, `kaggle_sdk: <status> <reason>:
<message>` when Kaggle answered with an HTTP error, else `kaggle_sdk:
<message>` (account fails only when signing in fails: it reports a failed
call in errors); bad arguments exit 2 with a usage line.
"""

import argparse
import contextlib
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from http import HTTPStatus

MAX_NOTEBOOKS = 1000
DEFAULT_NOTEBOOKS = 100
# kernels_list never returns more than 100 a page
LIST_PAGE = 100
SEARCH_PAGE = 100
SEARCH_PAGES = 3
LOOKUPS = 20
# the account read's defaults are kaggle_share's: scan_hours, LIST_PAGE and MAX_STATUS
DEFAULT_HOURS = 12
MAX_HOURS = 8760
DEFAULT_PAGE = 50
STATUSES = 25
ISO = "%Y-%m-%dT%H:%M:%SZ"
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
ID_RE = re.compile(r"[0-9]+")
# a kernel ref passed back as Kaggle listed it: owner/slug
REF_RE = re.compile(r"[^/=\s]+/[^/=\s]+")


class SdkError(Exception):
    pass


def topic_id(text):
    if not ID_RE.fullmatch(text) or int(text) < 1:
        raise argparse.ArgumentTypeError(f"not a topic id: {text!r}")
    return int(text)


def slug(text):
    if not SLUG_RE.fullmatch(text):
        raise argparse.ArgumentTypeError(f"not a competition slug: {text!r}")
    return text


def notebook_count(text):
    if not ID_RE.fullmatch(text) or not 1 <= int(text) <= MAX_NOTEBOOKS:
        raise argparse.ArgumentTypeError(f"not a count from 1 to {MAX_NOTEBOOKS}: {text!r}")
    return int(text)


def hour_count(text):
    try:
        hours = float(text)
    except ValueError:
        hours = -1.0
    # NaN fails this test too
    if not 0 <= hours <= MAX_HOURS:
        raise argparse.ArgumentTypeError(f"not a number of hours from 0 to {MAX_HOURS}: {text!r}")
    return hours


def page_size(text):
    if not ID_RE.fullmatch(text) or not 1 <= int(text) <= LIST_PAGE:
        raise argparse.ArgumentTypeError(f"not a page size from 1 to {LIST_PAGE}: {text!r}")
    return int(text)


def done_run(text):
    ref, _, last = text.partition("=")
    if not REF_RE.fullmatch(ref) or not last:
        raise argparse.ArgumentTypeError(f"not REF=TIME, a kernel ref and its lastRunTime: {text!r}")
    return ref.lower(), last


def error_text(e):
    """A failure on one line: `<status> <reason>: <message>` when Kaggle answered with an HTTP error."""
    text = str(e) or type(e).__name__
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status:
        try:
            reason = HTTPStatus(status).phrase
        except ValueError:
            reason = getattr(e.response, "reason", None) or "HTTP error"
        text = f"{status} {reason}: {text}"
    return " ".join(text.split())


def when(d):
    # as the CLI's --format json prints a date: isoformat() of the datetime the SDK returns
    return d.isoformat() if isinstance(d, datetime) else d


def sign_in():
    """The CLI's own KaggleApi, signed in the way the CLI signs in."""
    try:
        import kaggle  # signs in on import, as for the CLI
    except ImportError as e:
        raise SdkError(f"{e}: run this through the gateway (kaggle.py --sdk ...)") from None
    api = kaggle.api
    if not api._authenticated:
        try:
            api.authenticate()
        except SystemExit:
            # the CLI prints its sign-in help and exits here
            raise SdkError("not signed in to Kaggle: sign in with the gateway's `auth login`, "
                           "or set up an API token") from None
    return api


def comment(c):
    return {"id": c.id, "authorName": c.author_name, "postDate": when(c.post_date), "votes": c.votes,
            "content": c.content, "replies": [comment(r) for r in c.replies or []]}


def read_topic(api, a):
    topic, comments, _ = api.forums_topic_show(a.id)
    if topic is None:
        raise SdkError(f"topic {a.id} not found")
    return {"topic": {"id": topic.id, "title": topic.title, "authorName": topic.author_name,
                      "postDate": when(topic.post_date), "votes": topic.votes,
                      "commentCount": topic.comment_count, "content": topic.content},
            "comments": [comment(c) for c in comments or []]}


def list_notebooks(api, competition, n):
    """Up to n notebooks, best public score first, and notes on what the list misses."""
    size = min(n, LIST_PAGE)
    kernels, page = [], 1
    while True:
        try:
            batch = api.kernels_list(page=page, page_size=size, competition=competition,
                                     sort_by="scoreDescending") or []
        except Exception as e:
            if page == 1:
                raise
            return kernels, [f"notebook list page {page} failed: {error_text(e)}"]
        kernels += batch
        if len(batch) < size and len(kernels) <= n:
            return kernels, []
        if len(batch) < size or len(kernels) >= n:
            return kernels[:n], [f"the list stopped at --max {n}"]
        page += 1


def search(client, query, token=""):
    """One page of Kaggle's search over notebooks: (documents, next page token)."""
    from kagglesdk.search.types.search_api_service import ListEntitiesFilters, ListEntitiesRequest
    from kagglesdk.search.types.search_enums import DocumentType
    req = ListEntitiesRequest()
    req.filters = ListEntitiesFilters()
    req.filters.query = query
    req.filters.document_types = [DocumentType.KERNEL]
    req.page_size = SEARCH_PAGE
    req.page_token = token
    res = client.search.search_api_client.list_entities(req)
    return res.documents or [], res.next_page_token


def doc_ref(d):
    """The owner/slug ref of a notebook search document, or None."""
    parts = [p for p in (d.slug or "").split("/") if p]
    if len(parts) > 1:
        return "/".join(parts[-2:])
    owner = getattr(d.owner_user, "user_name", None) or getattr(d.owner_organization, "slug", None)
    return f"{owner}/{parts[0]}" if owner and parts else None


def doc_score(d):
    k = d.kernel_document
    # Kaggle leaves out a missing score, which the SDK reads as 0.0: never report that default.
    # has_linked_submission is no presence flag: most scored notebooks come without it
    if k is not None and k.best_public_score:
        return k.best_public_score
    return None


def scored(docs):
    """{lowercased ref: score or None} for the notebooks among search documents."""
    out = {}
    for d in docs:
        ref = doc_ref(d)
        if ref:
            out[ref.lower()] = doc_score(d)
    return out


def score_notebooks(api, competition, refs):
    """Best public scores by lowercased ref, and notes on the scores left unread."""
    found, looked = {}, []
    if not refs:
        return found, []
    step = "search page 1"
    try:
        with api.build_kaggle_client() as client:
            token = ""
            for page in range(1, SEARCH_PAGES + 1):
                step = f"search page {page}"
                docs, token = search(client, competition, token)
                found.update(scored(docs))
                if not token or all(r.lower() in found for r in refs):
                    break
            for ref in refs:
                if ref.lower() in found:
                    continue
                if len(looked) == LOOKUPS:
                    break
                step = f"lookup of {ref}"
                looked.append(ref)
                found.update(scored(search(client, ref)[0]))
    except Exception as e:
        return found, [f"{step} failed, no scores read after it: {error_text(e)}"]
    notes = []
    gone = [r for r in looked if r.lower() not in found]
    if gone:
        notes.append(f"not found by the search, score unknown: {', '.join(gone)}")
    left = [r for r in refs if r.lower() not in found and r not in looked]
    if left:
        notes.append(f"{LOOKUPS} single lookups made, scores left unread: {len(left)}")
    return found, notes


def read_notebooks(api, a):
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    kernels, notes = list_notebooks(api, a.slug, a.max)
    scores, more = score_notebooks(api, a.slug, [k.ref for k in kernels])
    notes += more
    return {"competition": a.slug, "fetched_at": fetched_at,
            "notebooks": [{"ref": k.ref, "title": k.title, "lastRunTime": when(k.last_run_time),
                           "votes": k.total_votes, "score": scores.get(k.ref.lower())} for k in kernels],
            "complete": not notes, "note": "; ".join(notes) or None}


def quota_hours(res):
    """{"gpu"|"tpu": {used, remaining, total, refresh}} as `quota --format json` prints it: hours to 2 decimals."""
    quota = {}
    for name, q in (("gpu", res.gpu_quota), ("tpu", res.tpu_quota)):
        if q is not None:
            used, total = q.time_used.total_seconds() / 3600, q.total_time_allowed.total_seconds() / 3600
            quota[name] = {"used": round(used, 2), "remaining": round(max(0.0, total - used), 2),
                           "total": round(total, 2), "refresh": when(res.quota_refresh_time)}
    return quota


def read_account(api, a):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    since = (now - timedelta(hours=a.hours)).replace(microsecond=0)
    doc = {"fetched_at": now.strftime(ISO), "since": since.strftime(ISO), "quota": None, "kernels": None,
           "calls": 2, "errors": []}
    try:
        doc["quota"] = quota_hours(api.quota_view())
    except Exception as e:
        doc["errors"].append(f"quota read failed: {error_text(e)}")
    try:
        kernels = api.kernels_list(page=1, page_size=a.page_size, mine=True, sort_by="dateRun") or []
    except Exception as e:
        doc["errors"].append(f"kernel list failed: {error_text(e)}")
        return doc
    done, recent, rows = set(a.done), 0, []
    for k in kernels:
        row = {"ref": k.ref, "lastRunTime": when(k.last_run_time), "status": None}
        rows.append(row)
        ran = k.last_run_time
        # the SDK reads Kaggle's times as naive UTC
        if ran is None or (ran if ran.tzinfo else ran.replace(tzinfo=timezone.utc)) < since:
            continue
        recent += 1
        if recent > STATUSES or (k.ref.lower(), row["lastRunTime"]) in done:
            continue
        doc["calls"] += 1
        try:
            row["status"] = api.kernels_status(k.ref).status.name
        except Exception as e:
            doc["errors"].append(f"status read of {k.ref} failed: {error_text(e)}")
    doc["kernels"] = rows
    return doc


READS = {"topic": read_topic, "notebooks": read_notebooks, "account": read_account}


def parse(argv):
    p = argparse.ArgumentParser(prog="kaggle_sdk.py", description="Read-only reads through Kaggle's Python SDK, "
                                "of data the Kaggle CLI drops or reads one process per call; run as "
                                "`kaggle.py --sdk <read> ...`.")
    sub = p.add_subparsers(dest="read", required=True)
    s = sub.add_parser("topic", help="a forum topic: its opening post and every comment, replies nested")
    s.add_argument("id", type=topic_id, help="topic id")
    s = sub.add_parser("notebooks", help="a competition's notebooks with their best public scores")
    s.add_argument("slug", type=slug, help="competition slug")
    s.add_argument("--max", type=notebook_count, default=DEFAULT_NOTEBOOKS,
                   help=f"notebooks to list, 1..{MAX_NOTEBOOKS} (default {DEFAULT_NOTEBOOKS})")
    s = sub.add_parser("account", help="the account's quota and its own kernels, with the run state of the "
                       "recent ones")
    s.add_argument("--hours", type=hour_count, default=DEFAULT_HOURS,
                   help=f"read the run state of kernels run in the last H hours, 0..{MAX_HOURS} "
                   f"(default {DEFAULT_HOURS})")
    s.add_argument("--page-size", type=page_size, default=DEFAULT_PAGE,
                   help=f"kernels to list, 1..{LIST_PAGE} (default {DEFAULT_PAGE})")
    s.add_argument("--done", type=done_run, action="append", default=[], metavar="REF=TIME",
                   help="a finished run already known, by kernel ref and lastRunTime: its state is not read again")
    return p.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    try:
        # the CLI's library prints notices (an outdated-version warning, sign-in help) on stdout: keep them out
        with contextlib.redirect_stdout(io.StringIO()):
            doc = READS[a.read](sign_in(), a)
    except Exception as e:
        print(f"kaggle_sdk: {error_text(e)}", file=sys.stderr)
        return 1
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    # the gateway, kaggle.py, sits in this folder and would shadow the kaggle package
    here = os.path.dirname(os.path.realpath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.realpath(p or os.curdir) != here]
    sys.exit(main())
