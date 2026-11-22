"""Offline tests for shared/tools/kaggle_lb.py (stdlib unittest; fixture pages, no network).

    python3 -m unittest discover -s plugins/kaggle/tests
"""

import contextlib
import csv
import gzip
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared" / "tools"))
import kaggle_lb as L  # noqa: E402

SLUG = "demo-competition"
FIELDS = ["teamId", "teamName", "submissionDate", "score"]
# what makes a folder an ai-pack, whatever its name: a manifest with framework_version and a project object
PACK_MANIFEST = json.dumps({"framework_version": "0.39.0", "project": {"name": "demo"}})


def team(tid, name, score, date="2026-09-25T00:00:57.303000"):
    return {"teamId": tid, "teamName": name, "submissionDate": date, "score": score}


def cli_json(rows, token=None, notice=None):
    # what `competitions leaderboard <slug> --show --format json` prints
    head = [notice] if notice else []
    head += [f"Next Page Token = {token}"] if token else []
    return "\n".join(head + [json.dumps(rows, indent=2)]) + "\n"


def cli_table(rows):
    # the CLI's print_table: padded columns over a dashed border, ints right-aligned
    fmts, borders = [], []
    for f in FIELDS:
        width = max(len(f), *(len(str(r[f])) for r in rows))
        fmts.append("{:" + (">" if isinstance(rows[0][f], int) else "<") + str(width + 2) + "}")
        borders.append("-" * width + "  ")
    fmt = "".join(fmts)
    lines = [fmt.format(*[f + "  " for f in FIELDS]), fmt.format(*borders)]
    lines += [fmt.format(*[str(r[f]) + "  " for f in FIELDS]) for r in rows]
    return "\n".join(lines) + "\n"


def cli_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(FIELDS)
    for r in rows:
        w.writerow([r[f] for f in FIELDS])
    return buf.getvalue()


DOWNLOAD_CSV = (
    "﻿Rank,TeamId,TeamName,LastSubmissionDate,Score,SubmissionCount,TeamMemberUserNames\n"
    '1,101,Alpha,"2026-09-25 00:00:57",0.425,50,alpha_user\n'
    '2,102,"Beta, Inc","2026-09-26 10:00:00",0.300,7,beta_user\n'
    '0,900,simple_benchmark,"2026-09-08 23:47:33",0.137,1,host_user\n'
    '3,103,Gamma,"2026-09-27 01:00:00",0.100,2,gamma_user\n'
)

PAGES = {
    None: cli_json([team(1, "Alpha", "0.425"), team(2, "Beta", "0.421"), team(3, "Gamma", "0.400")], "T1"),
    "T1": cli_json([team(4, "Delta", "0.380"), team(5, "Eps", "0.300"), team(6, "Zeta", "0.250")], "T2"),
    "T2": cli_json([team(7, "Eta", "0.100")]),
}


class FakeGateway:
    """Stands in for `python3 kaggle.py ...`: serves fixture pages by --page-token."""

    def __init__(self, pages, fail_at=None):
        self.pages, self.fail_at, self.calls = pages, fail_at, []

    def __call__(self, cmd, cwd):
        self.calls.append((cmd, cwd))
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            return 1, "429 Too Many Requests\n"
        token = cmd[cmd.index("--page-token") + 1] if "--page-token" in cmd else None
        return 0, self.pages[token]


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.base = self.tmp / "store"
        self.d = self.base / SLUG
        self.gateway = self.tmp / "kaggle.py"
        self.gateway.write_text("# stand-in gateway\n")
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in (L.ENV_DIR, L.ENV_RECORD, L.ENV_COMPETITION, L.ENV_PATH):
            os.environ.pop(name, None)

    def snap(self, **kw):
        kw = {"gateway": self.gateway, "directory": self.base, "root": self.tmp, "pause": 0, **kw}
        return L.take_snapshot(SLUG, **kw)


class ParseTests(unittest.TestCase):
    def test_json_after_notices_and_token(self):
        text = cli_json([team(1, "Alpha", "0.425"), team(2, "Beta", "0.4")], "tok-A1_b2",
                        notice="Warning: Looks like you're using an outdated `kaggle` version, please upgrade")
        rows, token, fmt, dropped = L.parse_output(text)
        self.assertEqual((token, fmt, dropped), ("tok-A1_b2", "json", 0))
        self.assertEqual(rows[0], {"rank": None, "team_id": 1, "team_name": "Alpha", "score": "0.425",
                                   "submission_date": "2026-09-25T00:00:57.303000"})

    def test_last_page_has_no_token(self):
        rows, token, _, _ = L.parse_output(cli_json([team(7, "Eta", "0.1")]))
        self.assertIsNone(token)
        self.assertEqual(len(rows), 1)

    def test_table_output_keeps_names_with_spaces_and_wide_characters(self):
        src = [team(900001, "Orion Labs", "0.425"), team(5, "two  spaces", "0.359"),
               team(900002, "测试队伍", "0.336")]
        rows, token, fmt, dropped = L.parse_output("Next Page Token = abc\n" + cli_table(src))
        self.assertEqual((token, fmt, dropped), ("abc", "table", 0))
        self.assertEqual([r["team_id"] for r in rows], [900001, 5, 900002])
        self.assertEqual([r["team_name"] for r in rows], [s["teamName"] for s in src])
        self.assertEqual(rows[1]["score"], "0.359")

    def test_cli_csv_output(self):
        rows, _, fmt, _ = L.parse_output(cli_csv([team(1, "A, the first", "0.5"), team(2, "B", "0.4")]))
        self.assertEqual(fmt, "csv")
        self.assertEqual([r["team_name"] for r in rows], ["A, the first", "B"])

    def test_download_csv_keeps_only_leaderboard_fields(self):
        rows, _, fmt, _ = L.parse_output(DOWNLOAD_CSV)
        self.assertEqual(fmt, "download-csv")
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertEqual(set(r), {"rank", "team_id", "team_name", "score", "submission_date"})
        self.assertNotIn("alpha_user", json.dumps(rows))
        self.assertEqual(rows[1]["team_name"], "Beta, Inc")

    def test_empty_board(self):
        self.assertEqual(L.parse_output("No results found\n"), ([], None, "empty", 0))

    def test_unreadable_output_raises(self):
        with self.assertRaises(L.LeaderboardError):
            L.parse_output("401 - Unauthorized\n")

    def test_rows_without_team_id_are_dropped(self):
        rows, _, _, dropped = L.parse_output(json.dumps([{"teamName": "A", "score": "0.1"}]))
        self.assertEqual((rows, dropped), ([], 1))


class SnapshotTests(Tmp):
    def test_walks_every_page_with_the_token_line(self):
        fake = FakeGateway(PAGES)
        path, snap = self.snap(run=fake)
        self.assertEqual([r["rank"] for r in snap["rows"]], list(range(1, 8)))
        self.assertEqual([r["team_id"] for r in snap["rows"]], list(range(1, 8)))
        self.assertEqual((snap["pages"], snap["row_count"], snap["partial"]), (3, 7, False))
        cmds = [c for c, _ in fake.calls]
        self.assertNotIn("--page-token", cmds[0])
        self.assertEqual([c[c.index("--page-token") + 1] for c in cmds[1:]], ["T1", "T2"])
        for c, cwd in fake.calls:
            self.assertEqual(c[:2], [sys.executable, str(self.gateway.resolve())])
            self.assertEqual(c[2:9], ["competitions", "leaderboard", SLUG, "--show", "--format", "json",
                                      "--page-size"])
            self.assertEqual(c[9], "200")
            self.assertEqual(cwd, self.tmp)
        self.assertEqual(L.read_snapshot(path), snap)
        self.assertTrue(path.name.endswith("Z.json.gz"))

    def test_failure_mid_walk_is_saved_partial(self):
        path, snap = self.snap(run=FakeGateway(PAGES, fail_at=2))
        self.assertTrue(snap["partial"])
        self.assertIn("page 2 failed", snap["note"])
        self.assertEqual(snap["row_count"], 3)
        self.assertIn("-partial", path.name)

    def test_failure_on_the_first_page_saves_nothing(self):
        with self.assertRaises(L.LeaderboardError):
            self.snap(run=FakeGateway(PAGES, fail_at=1))
        self.assertEqual(L.load_index(self.d), [])

    def test_repeated_token_stops_the_walk(self):
        pages = {None: cli_json([team(1, "A", "0.5")], "T1"), "T1": cli_json([team(2, "B", "0.4")], "T1")}
        _, snap = self.snap(run=FakeGateway(pages))
        self.assertTrue(snap["partial"])
        self.assertIn("stalled", snap["note"])
        self.assertEqual(snap["row_count"], 2)

    def test_page_cap_marks_partial(self):
        _, snap = self.snap(run=FakeGateway(PAGES), max_pages=2)
        self.assertEqual((snap["pages"], snap["partial"]), (2, True))

    def test_board_moving_between_pages_drops_the_duplicate(self):
        pages = {None: cli_json([team(1, "A", "0.5"), team(2, "B", "0.4")], "T1"),
                 "T1": cli_json([team(2, "B", "0.4"), team(3, "C", "0.3")])}
        path, snap = self.snap(run=FakeGateway(pages))
        self.assertEqual([(r["team_id"], r["rank"]) for r in snap["rows"]], [(1, 1), (2, 2), (3, 4)])
        # one row that came twice is the board moving: the read stays a board, with the count in "moved" and the note
        self.assertEqual((snap["moved"], snap["partial"], snap["unreliable"]), (1, False, False))
        self.assertNotIn("duplicates", snap)
        self.assertEqual(snap["note"], "1 duplicate row as the board moved during the read (ranks off by at most "
                                       "that many)")
        self.assertTrue(path.name.endswith("Z.json.gz"), path.name)
        self.assertEqual([e["unreliable"] for e in L.load_index(self.d)], [False])
        self.assertFalse(L.unreliable(L.read_snapshot(path)))

    def test_rows_that_came_twice_make_a_read_unreliable_from_three_or_a_whole_page(self):
        def read(*pages):
            snap = L.build_snapshot(SLUG, [[L.normalize(team(n, f"T{n}", "0.5")) for n in page] for page in pages],
                                    fetched_at="2026-09-28T10:00:00Z", source="snapshot", fmt="json")
            return snap["unreliable"], snap.get("duplicates"), snap.get("moved")

        # under the limit: the board moved
        self.assertEqual(read([1, 2, 3], [3, 4, 5], [5, 6]), (False, None, 2))
        # three rows on a small board
        self.assertEqual(read([1, 2, 3, 4], [2, 3, 4, 5]), (True, 3, None))
        # a whole page of teams read before is Kaggle serving a page again, however few rows it holds
        self.assertEqual(read([1, 2], [1, 2], [3]), (True, 2, None))
        self.assertEqual(read([1], [1]), (True, 1, None))
        # on a larger board the limit is one row in a thousand: 5 of 5,752 pass, 6 do not
        big = list(range(1, 5001))
        self.assertEqual(read(big, list(range(4996, 5753))), (False, None, 5))
        self.assertEqual(read(big, list(range(4995, 5753))), (True, 6, None))
        # "duplicates" above 0 means unreliable to every reader: a count under the limit is never saved there
        snap = L.build_snapshot(SLUG, [[L.normalize(team(1, "A", "0.5"))], [L.normalize(team(1, "A", "0.5"))]],
                                fetched_at="2026-09-28T10:00:00Z", source="snapshot", fmt="json")
        self.assertEqual(snap["note"], "unreliable: 1 duplicate row, 2 pages for 1 teams (ranks not trusted)")
        self.assertEqual((L.DUP_LIMIT, L.DUP_PER), (3, 1000))

    def test_a_board_that_moved_says_so_after_the_snapshot_line(self):
        pages = {None: cli_json([team(1, "A", "0.5"), team(2, "B", "0.4")], "T1"),
                 "T1": cli_json([team(2, "B", "0.4"), team(3, "C", "0.3")])}
        fixtures = self.tmp / "pages.json"
        fixtures.write_text(json.dumps({"": pages[None], "T1": pages["T1"]}))
        self.gateway.write_text(textwrap.dedent(f"""\
            import json, sys
            a = sys.argv[1:]
            t = a[a.index("--page-token") + 1] if "--page-token" in a else ""
            sys.stdout.write(json.load(open({str(fixtures)!r}))[t])
        """))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(L.main(["snapshot", SLUG, "--dir", str(self.base), "--gateway", str(self.gateway)]), 0)
        line = out.getvalue().strip()
        self.assertIn("3 rows, 2 pages (full), ", line)
        self.assertTrue(line.endswith("; note: 1 duplicate row as the board moved during the read (ranks off by at "
                                      "most that many)"), line)

    def test_a_clean_walk_is_reliable(self):
        path, snap = self.snap(run=FakeGateway(PAGES))
        self.assertEqual((snap["partial"], snap["unreliable"]), (False, False))
        self.assertNotIn("note", snap)
        self.assertNotIn("unreliable", path.name)
        # the flag sits beside "partial" in the saved file
        keys = list(L.read_snapshot(path))
        self.assertEqual(keys[keys.index("partial") + 1], "unreliable")
        self.assertEqual([e["unreliable"] for e in L.load_index(self.d)], [False])

    def test_a_page_with_no_new_team_stops_the_walk(self):
        # Kaggle serves the first page again under ever new tokens: the walk must not go on to the page cap
        first = [team(1, "A", "0.5"), team(2, "B", "0.4")]
        pages = {None: cli_json(first, "T1"), "T1": cli_json([team(3, "C", "0.3"), team(4, "D", "0.2")], "T2"),
                 **{f"T{n}": cli_json(first, f"T{n + 1}") for n in range(2, 40)}}
        fake = FakeGateway(pages)
        path, snap = self.snap(run=fake)
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual((snap["pages"], snap["row_count"], snap["duplicates"]), (3, 4, 2))
        self.assertEqual((snap["partial"], snap["unreliable"]), (True, True))
        self.assertEqual(snap["note"], "page 3 brought no new team: Kaggle is serving pages again; unreliable: "
                                       "2 duplicate rows, 3 pages for 4 teams (ranks not trusted)")
        self.assertTrue(path.name.endswith("Z-partial-unreliable.json.gz"), path.name)

    def endless(self, per_page=2):
        """A board whose every page brings new teams and one more token: only a page cap ends its walk."""
        class Endless(dict):
            def __missing__(self, token):
                n = int(token[1:])
                return cli_json([team(n * per_page + i + 1, f"T{n}-{i}", "0.5") for i in range(per_page)],
                                f"T{n + 1}")
        pages = Endless()
        pages[None] = pages["T0"]
        return FakeGateway(pages)

    def test_the_page_cap_comes_from_the_most_teams_a_saved_snapshot_holds(self):
        # 7 teams known, 2 rows a page: 4 pages hold them, and 5 more are allowed
        self.snap(run=FakeGateway(PAGES))
        fake = self.endless()
        _, snap = self.snap(run=fake)
        self.assertEqual((len(fake.calls), snap["pages"], snap["row_count"]), (9, 9, 18))
        self.assertEqual((snap["partial"], snap["unreliable"]), (True, False))
        self.assertEqual(snap["note"], "stopped after 9 pages, the cap for the 7 teams known (--max-pages raises it)")
        # the cap rises with what was read, so a board that grew is read whole a walk or two later
        fake = self.endless()
        self.assertEqual(self.snap(run=fake)[1]["pages"], 9 + L.PAGE_MARGIN)
        # --max-pages replaces the automatic cap, upward as well as downward
        for given in (3, 20):
            fake = self.endless()
            _, snap = self.snap(run=fake, max_pages=given)
            self.assertEqual((len(fake.calls), snap["note"]), (given, f"stopped after {given} pages"))

    def test_without_a_saved_snapshot_the_cap_is_the_fixed_one(self):
        self.assertEqual([L.page_cap(*a) for a in ((0, 200), (4958, 0), (4958, 200), (1, 200), (10 ** 6, 20))],
                         [L.MAX_PAGES, L.MAX_PAGES, 25 + L.PAGE_MARGIN, 1 + L.PAGE_MARGIN, L.MAX_PAGES])
        with mock.patch.object(L, "MAX_PAGES", 4):
            fake = self.endless()
            _, snap = self.snap(run=fake)
        self.assertEqual((len(fake.calls), snap["note"]),
                         (4, "stopped after 4 pages, the cap for the 0 teams known (--max-pages raises it)"))

    def test_far_more_pages_than_the_rows_fill_is_unreliable(self):
        # one full page, then pages of one row each: 8 pages where 3 rows a page would need 4
        rows = [[L.normalize(team(n, f"T{n}", "0.5")) for n in (1, 2, 3)]]
        rows += [[L.normalize(team(n, f"T{n}", "0.5"))] for n in range(4, 4 + L.PAGE_MARGIN + 2)]
        snap = L.build_snapshot(SLUG, rows, fetched_at="2026-09-28T10:00:00Z", source="snapshot", fmt="json")
        self.assertEqual((snap["pages"], snap["row_count"], snap["unreliable"]), (8, 10, False))
        rows += [[L.normalize(team(n, f"T{n}", "0.5"))] for n in (98, 99)]
        snap = L.build_snapshot(SLUG, rows, fetched_at="2026-09-28T10:00:00Z", source="snapshot", fmt="json")
        self.assertEqual((snap["pages"], snap["row_count"], snap["unreliable"]), (10, 12, True))
        self.assertEqual(snap["note"], "unreliable: 10 pages for 12 teams (ranks not trusted)")
        self.assertNotIn("duplicates", snap)

    def test_max_pages_must_be_a_page_count(self):
        for bad in ("0", "-1", "many"):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                L.main(["snapshot", SLUG, "--max-pages", bad, "--dir", str(self.base)])

    def test_reads_are_marked_as_monitoring(self):
        # kaggle_share's activity stamp skips calls carrying KAGGLE_SHARE_QUIET=1
        with mock.patch.object(L.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=PAGES["T2"])
            L._run(["gw"], self.tmp)
        self.assertEqual(run.call_args.kwargs["env"]["KAGGLE_SHARE_QUIET"], "1")
        # the tool saves the whole board itself: the gateway's tee must not save each page again
        self.assertEqual(run.call_args.kwargs["env"]["KAGGLE_LB_RECORD"], "0")

    def test_real_subprocess_gateway(self):
        # an executable stand-in for kaggle.py, run the way the tool runs the real one
        fixtures = self.tmp / "pages.json"
        fixtures.write_text(json.dumps({"": PAGES[None], "T1": PAGES["T1"], "T2": PAGES["T2"]}))
        self.gateway.write_text(textwrap.dedent(f"""\
            import json, sys
            a = sys.argv[1:]
            t = a[a.index("--page-token") + 1] if "--page-token" in a else ""
            sys.stdout.write(json.load(open({str(fixtures)!r}))[t])
        """))
        _, snap = L.take_snapshot(SLUG, gateway=self.gateway, directory=self.base, root=self.tmp, pause=0)
        self.assertEqual((snap["row_count"], snap["partial"]), (7, False))


class StorageTests(Tmp):
    def test_snapshots_are_never_overwritten(self):
        a = L.build_snapshot(SLUG, [[L.normalize(team(1, "A", "0.5"))]], fetched_at="2026-09-28T10:00:00Z",
                             source="snapshot", fmt="json")
        p1, p2 = L.save_snapshot(self.d, a), L.save_snapshot(self.d, a)
        self.assertNotEqual(p1, p2)
        self.assertEqual(p2.name, "20260928T100000Z-2.json.gz")
        self.assertEqual([e["file"] for e in L.load_index(self.d)], [p1.name, p2.name])

    def test_index_is_rebuilt_and_survives_a_torn_line(self):
        _, s1 = self.snap(run=FakeGateway(PAGES))
        (self.d / L.INDEX).unlink()
        entries = L.load_index(self.d)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["rows"], 7)
        self.assertEqual(entries[0]["top_score"], "0.425")
        self.assertTrue((self.d / L.INDEX).is_file())
        with open(self.d / L.INDEX, "a") as f:
            f.write('{"file": "torn')
        self.snap(run=FakeGateway(PAGES))
        self.assertEqual(len(L.load_index(self.d)), 2)

    def test_snapshot_file_is_gzip_json(self):
        path, snap = self.snap(run=FakeGateway(PAGES))
        with gzip.open(path, "rt", encoding="utf-8") as f:
            self.assertEqual(json.load(f)["rows"], snap["rows"])

    def test_default_store_is_under_the_project_root(self):
        proj = self.tmp / "proj"
        (proj / "aipack").mkdir(parents=True)
        (proj / "aipack" / "manifest.json").write_text(PACK_MANIFEST)
        (proj / "src" / "deep").mkdir(parents=True)
        self.assertEqual(L.find_root(proj / "src" / "deep"), proj.resolve())
        self.assertEqual(L.store_dir(SLUG, root=proj), proj / "__data" / "kaggle" / SLUG / "leaderboard")

    def test_project_root_for_any_pack_name_and_from_a_copied_install(self):
        tmp = self.tmp.resolve()
        (tmp / "elsewhere").mkdir()
        for pack in ("ai", "aipack", "mypack"):
            proj = tmp / f"p-{pack}"
            tools = proj / pack / "plugins" / "kaggle" / "tools"
            tools.mkdir(parents=True)
            (proj / pack / "manifest.json").write_text(PACK_MANIFEST)
            self.assertEqual((L.find_root(tools), L.pack_of(proj)), (proj, proj / pack))
            # a copied install finds its project from its own place
            with mock.patch.object(L, "__file__", str(tools / "kaggle_lb.py")):
                self.assertEqual(L.find_root(tmp / "elsewhere"), proj)
        # a plugin manifest and a hidden folder are no pack; two packs stop the walk with an error naming them
        odd = tmp / "odd"
        for name, text in (("plugin", '{"name": "kaggle"}'), (".hidden", PACK_MANIFEST), ("one", PACK_MANIFEST),
                           ("two", PACK_MANIFEST)):
            (odd / name).mkdir(parents=True)
            (odd / name / "manifest.json").write_text(text)
        with self.assertRaisesRegex(L.LeaderboardError, r"odd: more than one ai-pack \(one, two\)$"):
            L.find_root(odd / "plugin")
        (odd / "two" / "manifest.json").unlink()
        self.assertEqual(L.pack_of(odd), odd / "one")

    def test_task_folder_is_a_context(self):
        task = self.tmp / "task"
        task.mkdir()
        (task / "notes.md").write_text("# Task\nSkill: ad-hoc-task\n")
        self.assertEqual(L.find_root(task), task.resolve())

    def test_dir_flag_and_env_override(self):
        self.assertEqual(L.store_dir(SLUG, directory=str(self.tmp / "x")), self.tmp / "x" / SLUG)
        os.environ[L.ENV_DIR] = str(self.tmp / "env")
        self.assertEqual(L.store_dir(SLUG), self.tmp / "env" / SLUG)
        self.assertEqual(L.store_dir(SLUG, directory=str(self.tmp / "x")), self.tmp / "x" / SLUG)

    def test_no_context_and_no_dir_is_an_error(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        with mock.patch.object(L.os, "getcwd", return_value=str(bare)):
            with self.assertRaises(L.LeaderboardError):
                L.store_dir(SLUG)

    def test_slug_cannot_escape_the_store(self):
        for bad in ("../x", "a/b", "", ".hidden"):
            with self.assertRaises(L.LeaderboardError):
                L.store_dir(bad, directory=str(self.tmp))


def board(*teams):
    return [L.normalize(team(t, n, s)) for t, n, s in teams]


class ProgressTests(Tmp):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
        at = lambda h: (self.now - timedelta(hours=h)).strftime(L.ISO)  # noqa: E731
        boards = [
            (30, board((101, "Alpha Team", "0.40"), (102, "Beta", "0.30"), (103, "Gamma Ray", "0.20"))),
            (10, board((101, "Alpha Team", "0.40"), (103, "Gamma Ray", "0.35"), (102, "Beta", "0.30"),
                       (104, "Delta", "0.10"))),
            (1, board((103, "Gamma Ray", "0.45"), (101, "Alpha Team", "0.41"), (102, "Beta", "0.30"),
                      (105, "Alpha Two", "0.25"), (104, "Delta", "0.12"))),
        ]
        for h, rows in boards:
            L.save_snapshot(self.d, L.build_snapshot(SLUG, [rows], fetched_at=at(h), source="snapshot",
                                                     fmt="json"))
        # a newer partial read (top two only) must not count as a full board
        top2 = board((103, "Gamma Ray", "0.45"), (101, "Alpha Team", "0.41"))
        L.save_snapshot(self.d, L.build_snapshot(SLUG, [top2], fetched_at=at(0.5), source="gateway", fmt="json",
                                                 partial=True))

    def test_movers_against_the_board_a_day_ago(self):
        res = L.movers(self.d, 12, now=self.now)
        self.assertFalse(res["fallback"])
        self.assertEqual(res["base"]["fetched_at"], "2026-09-27T06:00:00Z")
        self.assertEqual(res["last"]["fetched_at"], "2026-09-28T11:00:00Z")
        self.assertEqual([m["team_id"] for m in res["gains"]], [103, 101])
        self.assertAlmostEqual(res["gains"][0]["change"], 0.25)
        self.assertEqual([(m["team_id"], m["climb"]) for m in res["climbs"]], [(103, 2)])

    def test_movers_over_a_shorter_window(self):
        res = L.movers(self.d, 5, now=self.now)
        self.assertEqual(res["base"]["fetched_at"], "2026-09-28T02:00:00Z")
        self.assertEqual([m["team_id"] for m in res["gains"]], [103, 104, 101])
        self.assertEqual([m["team_id"] for m in res["climbs"]], [103])

    def test_window_longer_than_history_falls_back_to_the_oldest(self):
        self.assertTrue(L.movers(self.d, 100, now=self.now)["fallback"])

    def test_lower_is_better_board(self):
        d = self.tmp / "rmse"
        for at, rows in (("2026-09-28T00:00:00Z", board((1, "A", "1.50"), (2, "B", "2.00"))),
                         ("2026-09-28T06:00:00Z", board((2, "B", "1.20"), (1, "A", "1.50")))):
            L.save_snapshot(d, L.build_snapshot(SLUG, [rows], fetched_at=at, source="snapshot", fmt="json"))
        res = L.movers(d, 3, now=datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc))
        self.assertEqual(res["sign"], -1)
        self.assertEqual([(m["team_id"], round(m["change"], 2)) for m in res["gains"]], [(2, -0.8)])

    def test_new_teams_with_first_seen_times(self):
        res = L.new_teams(self.d, 12, now=self.now)
        got = {r["team_id"]: seen for seen, r in res["new"]}
        self.assertEqual(got, {104: "2026-09-28T02:00:00Z", 105: "2026-09-28T11:00:00Z"})
        self.assertEqual(res["gone"], 0)
        self.assertEqual([r["team_id"] for _, r in L.new_teams(self.d, 5, now=self.now)["new"]], [105])

    def test_history_by_name_part_and_by_id(self):
        tid, name, rows = L.team_history(self.d, "gamma")
        self.assertEqual((tid, name), (103, "Gamma Ray"))
        self.assertEqual([(r["rank"], r["score"]) for _, r in rows], [(3, "0.20"), (2, "0.35"), (1, "0.45"),
                                                                      (1, "0.45")])
        self.assertEqual(L.team_history(self.d, "103")[0], 103)

    def test_history_shows_full_boards_without_the_team_and_skips_partial_ones(self):
        _, _, rows = L.team_history(self.d, "Delta")
        self.assertEqual([r["rank"] if r else None for _, r in rows], [None, 4, 5])

    def test_ambiguous_name_asks_for_the_id(self):
        with self.assertRaises(L.LeaderboardError) as cm:
            L.team_history(self.d, "alpha")
        self.assertIn("pass the team id", str(cm.exception))
        self.assertEqual(L.team_history(self.d, "Alpha Team")[0], 101)

    def test_summary_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(L.main(["summary", SLUG, "--dir", str(self.base), "--top", "3"]), 0)
        text = out.getvalue()
        self.assertIn("4 snapshots (3 full, 1 partial; 0 imported)", text)
        self.assertIn("Gamma Ray", text)
        self.assertIn("(#1 score)", text)

    def test_commands_print_without_errors(self):
        for argv in (["history", SLUG, "--team", "gamma"], ["movers", SLUG, "--since", "12"],
                     ["new-teams", SLUG, "--since", "12"]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(L.main(argv + ["--dir", str(self.base)]), 0, argv)

    def garbled(self, hours, old_file=False):
        """A newer whole-board read whose pages repeated a team, so its ranks are off: Gamma Ray shows as #5.
        With old_file, saved as a tool without the unreliable flag saved it: duplicates in the file, nothing else."""
        at = (self.now - timedelta(hours=hours)).strftime(L.ISO)
        pages = [board((101, "Alpha Team", "0.41"), (102, "Beta", "0.30")),
                 board((101, "Alpha Team", "0.41"), (102, "Beta", "0.30")), board((103, "Gamma Ray", "0.45"))]
        snap = L.build_snapshot(SLUG, pages, fetched_at=at, source="snapshot", fmt="json")
        self.assertEqual((snap["duplicates"], snap["unreliable"], snap["partial"]), (2, True, False))
        if not old_file:
            return L.save_snapshot(self.d, snap)
        del snap["unreliable"], snap["note"]
        path = self.d / (at.replace("-", "").replace(":", "") + ".json.gz")
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(snap, f)
        return path

    def test_readers_pass_by_an_unreliable_snapshot(self):
        before = L.movers(self.d, 12, now=self.now)
        self.garbled(0.2)
        res = L.movers(self.d, 12, now=self.now)
        # the newest reliable board stays the one compared, not the garbled read after it
        self.assertEqual(res["last"]["fetched_at"], "2026-09-28T11:00:00Z")
        self.assertEqual([m["team_id"] for m in res["climbs"]], [m["team_id"] for m in before["climbs"]])
        self.assertEqual(L.new_teams(self.d, 12, now=self.now)["last"]["fetched_at"], "2026-09-28T11:00:00Z")
        self.assertEqual(L.summarize(self.d)["cols"][-1]["fetched_at"], "2026-09-28T11:00:00Z")
        self.assertEqual(L.resolve_team(self.d, L.load_index(self.d), "gamma"), (103, "Gamma Ray"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(L.main(["summary", SLUG, "--dir", str(self.base)]), 0)
            self.assertEqual(L.main(["history", SLUG, "--team", "gamma", "--dir", str(self.base)]), 0)
        text = out.getvalue()
        self.assertIn("5 snapshots (3 full, 1 partial, 1 unreliable; 0 imported)", text)
        # the history keeps the read's score and hides its rank
        last = text.splitlines()[-1].split()
        self.assertEqual((last[-5], last[-4], last[-1]), ("?", "0.45", "unreliable"))

    def test_a_snapshot_saved_before_the_flag_is_unreliable_by_its_duplicates(self):
        # an index without the flag, as an earlier version wrote it, and one such file with duplicate rows
        lines = [json.loads(x) for x in (self.d / L.INDEX).read_text().splitlines()]
        for e in lines:
            del e["unreliable"]
        (self.d / L.INDEX).write_text("".join(json.dumps(e) + "\n" for e in lines))
        path = self.garbled(0.2, old_file=True)
        entries = L.load_index(self.d)
        self.assertEqual([e["unreliable"] for e in entries], [False, False, False, False, True])
        self.assertEqual(entries[-1]["file"], path.name)
        # the index was rewritten once with the flag: every line carries it, none came twice
        lines = [json.loads(x) for x in (self.d / L.INDEX).read_text().splitlines()]
        self.assertEqual(sorted(e["file"] for e in lines), sorted(e["file"] for e in entries))
        self.assertTrue(all(isinstance(e["unreliable"], bool) for e in lines))
        self.assertEqual(sorted(p.name for p in self.d.iterdir() if p.name.startswith(".")), [".lock"])
        self.assertEqual(L.movers(self.d, 12, now=self.now)["last"]["fetched_at"], "2026-09-28T11:00:00Z")

    def test_show_falls_back_to_the_newest_reliable_snapshot(self):
        # the walk meets the first page again: this read is unreliable, so show prints the board before it
        first = [team(103, "Gamma Ray", "0.46"), team(101, "Alpha Team", "0.41")]
        fixtures = self.tmp / "pages.json"
        fixtures.write_text(json.dumps({"": cli_json(first, "T1"), "T1": cli_json(first, "T2")}))
        # an executable stand-in for kaggle.py, run the way the tool runs the real one
        self.gateway.write_text(textwrap.dedent(f"""\
            import json, sys
            a = sys.argv[1:]
            t = a[a.index("--page-token") + 1] if "--page-token" in a else ""
            sys.stdout.write(json.load(open({str(fixtures)!r}))[t])
        """))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(L.main(["show", SLUG, "--dir", str(self.base), "--gateway", str(self.gateway)]), 0)
        lines = out.getvalue().splitlines()
        self.assertIn("2 rows, 2 pages (partial: page 2 brought no new team: Kaggle is serving pages again; "
                      "unreliable: 2 duplicate rows, 2 pages for 2 teams (ranks not trusted))", lines[0])
        self.assertTrue(lines[1].startswith("this read is unreliable: showing the newest reliable snapshot instead, "))
        # the reliable board of an hour ago, with its moves since the one before
        self.assertEqual([x.split()[:3] for x in lines[4:7]], [["1", "+1", "0.45"], ["2", "-1", "0.41"],
                                                               ["3", "=", "0.30"]])
        # with no reliable snapshot saved, the read is shown with a warning
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(L.main(["show", SLUG, "--dir", str(self.tmp / "empty"), "--gateway",
                                     str(self.gateway)]), 0)
        self.assertIn("this read is unreliable and no reliable snapshot is saved: the ranks below cannot be trusted",
                      out.getvalue())


class RecordRawTests(Tmp):
    def test_first_page_with_more_pages_is_partial(self):
        path = L.record_raw(SLUG, PAGES[None], directory=self.base)
        snap = L.read_snapshot(path)
        self.assertTrue(snap["partial"])
        self.assertEqual([r["rank"] for r in snap["rows"]], [1, 2, 3])
        self.assertEqual(snap["source"], "record-raw")

    def test_a_page_token_call_has_unknown_ranks(self):
        argv = ["competitions", "leaderboard", SLUG, "-s", "--page-token", "T1"]
        snap = L.read_snapshot(L.record_raw(SLUG, PAGES["T1"], argv=argv, directory=self.base))
        self.assertEqual([r["rank"] for r in snap["rows"]], [None, None, None])
        self.assertTrue(snap["partial"])

    def test_single_page_board_is_complete(self):
        snap = L.read_snapshot(L.record_raw(SLUG, cli_table([team(1, "A", "0.5")]), directory=self.base))
        self.assertFalse(snap["partial"])
        self.assertEqual(snap["format"], "table")

    def test_record_raw_command_reads_a_file(self):
        f = self.tmp / "out.txt"
        f.write_text(PAGES["T2"])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(L.main(["record-raw", SLUG, "--file", str(f), "--dir", str(self.base),
                                     "--fetched-at", "2026-09-28T01:02:03-07:00"]), 0)
        self.assertEqual(L.load_index(self.d)[0]["fetched_at"], "2026-09-28T08:02:03Z")

    def test_leaderboard_slug_detection(self):
        cases = {
            ("competitions", "leaderboard", "titanic", "--show"): "titanic",
            ("c", "leaderboard", "-s", "titanic", "--format", "json"): "titanic",
            ("competitions", "leaderboard", "--competition", "titanic", "-s"): "titanic",
            ("competitions", "leaderboard", "titanic", "-s", "--page-size", "200"): "titanic",
            ("competitions", "leaderboard", "titanic", "--download", "-p", "x"): None,
            ("competitions", "leaderboard", "--show"): None,
            ("competitions", "leaderboard", "titanic", "-s", "-h"): None,
            ("competitions", "leaderboard", "../x", "-s"): None,
            ("competitions", "list"): None,
            ("competitions", "submit", "titanic", "-f", "a.csv", "-m", "leaderboard --show"): None,
            ("leaderboard",): None,
        }
        for argv, want in cases.items():
            self.assertEqual(L.leaderboard_slug(list(argv)), want, argv)

    def _tee(self, script, argv):
        fake = self.tmp / "fake_cli.py"
        fake.write_text(script)
        out, err = io.BytesIO(), io.StringIO()
        wrapper = io.TextIOWrapper(out, encoding="utf-8")
        with mock.patch.object(sys, "stdout", wrapper), contextlib.redirect_stderr(err):
            code = L.tee_leaderboard([sys.executable, str(fake)], argv, self.tmp)
            wrapper.flush()
        return code, out.getvalue(), err.getvalue()

    def test_tee_passes_the_output_through_and_saves_it(self):
        text = PAGES[None]
        code, out, err = self._tee(f"import sys\nsys.stdout.write({text!r})\n",
                                   ["competitions", "leaderboard", SLUG, "--show", "--format", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(out.decode(), text)
        self.assertIn("saved", err)
        entries = L.load_index(self.tmp / "__data" / "kaggle" / SLUG / "leaderboard")
        self.assertEqual([(e["source"], e["rows"], e["partial"]) for e in entries], [("gateway", 3, True)])

    def test_tee_keeps_the_exit_code_and_saves_nothing_on_failure(self):
        code, _, _ = self._tee("import sys\nprint('403 - Forbidden')\nsys.exit(3)\n",
                               ["competitions", "leaderboard", SLUG, "--show"])
        self.assertEqual(code, 3)
        self.assertFalse((self.tmp / "__data").exists())

    def test_tee_leaves_other_commands_alone(self):
        self.assertIsNone(L.tee_leaderboard([sys.executable, "-c", "raise SystemExit(9)"],
                                            ["competitions", "list"], self.tmp))
        os.environ[L.ENV_RECORD] = "0"
        self.assertIsNone(L.tee_leaderboard([sys.executable, "-c", "raise SystemExit(9)"],
                                            ["competitions", "leaderboard", SLUG, "-s"], self.tmp))

    def test_unparseable_output_still_passes_through(self):
        code, out, err = self._tee("print('something else entirely')\n",
                                   ["competitions", "leaderboard", SLUG, "--show"])
        self.assertEqual((code, out.decode()), (0, "something else entirely\n"))
        self.assertIn("not saved", err)

    def test_leaderboard_read_detection(self):
        cases = {
            ("competitions", "leaderboard", "titanic", "--show"): True,
            ("competitions", "leaderboard", "--show"): True,  # the CLI's default competition
            ("c", "leaderboard", "-d", "-p", "x"): True,
            ("competitions", "leaderboard", "titanic", "--download", "--show"): True,
            ("competitions", "leaderboard", "titanic"): False,  # neither --show nor --download
            ("competitions", "leaderboard", "../x", "-d"): False,
            ("competitions", "leaderboard", "-s", "-h"): False,
            ("competitions", "list"): False,
        }
        for argv, want in cases.items():
            self.assertEqual(bool(L.leaderboard_read(list(argv))), want, argv)

    def board_dir(self):
        return self.tmp / "__data" / "kaggle" / SLUG / "leaderboard"

    def test_a_read_of_the_default_competition_is_saved_under_its_name(self):
        text = f"Using competition: {SLUG}\n" + PAGES["T2"]
        code, out, err = self._tee(f"import sys\nsys.stdout.write({text!r})\n", ["competitions", "leaderboard", "-s"])
        self.assertEqual((code, out.decode()), (0, text))
        self.assertEqual([e["rows"] for e in L.load_index(self.board_dir())], [1])

    def test_a_quiet_read_of_the_default_competition_needs_its_name_in_the_environment(self):
        argv, script = ["competitions", "leaderboard", "--show", "-q"], f"print({PAGES['T2']!r})\n"
        code, _, err = self._tee(script, argv)
        self.assertEqual(code, 0)
        self.assertIn("no competition named", err)
        self.assertFalse((self.tmp / "__data").exists())
        os.environ[L.ENV_COMPETITION] = SLUG
        self.assertEqual(self._tee(script, argv)[0], 0)
        self.assertEqual(len(L.load_index(self.board_dir())), 1)

    def test_a_downloaded_board_is_saved_once_and_a_stale_zip_never(self):
        folder = self.tmp / "dl"
        script = textwrap.dedent(f"""\
            import os, zipfile
            os.makedirs({str(folder)!r}, exist_ok=True)
            z = os.path.join({str(folder)!r}, {SLUG + ".zip"!r})
            with zipfile.ZipFile(z, "w") as f:
                f.writestr({SLUG + "-publicleaderboard-2026-09-27T22:54:20.csv"!r}, {DOWNLOAD_CSV!r})
            # the CLI dates the file by the server's clock
            os.utime(z, (1700000000, 1700000000))
            print("Downloading {SLUG}.zip to {folder}")
            """)
        argv = ["competitions", "leaderboard", SLUG, "--download", "-p", str(folder)]
        code, _, err = self._tee(script, argv)
        self.assertEqual(code, 0, err)
        (e,) = L.load_index(self.board_dir())
        self.assertEqual((e["source"], e["rows"], e["fetched_at"], e["imported"]),
                         ("gateway", 4, "2026-09-27T22:54:20Z", False))
        # the same board file again is not saved twice
        self.assertIn("saved before", self._tee(script, argv)[2])
        # a call that writes nothing leaves the old zip alone and saves nothing
        code, _, err = self._tee("pass\n", argv)
        self.assertEqual(code, 0)
        self.assertIn("download not saved", err)
        self.assertEqual(len(L.load_index(self.board_dir())), 1)


class ImportTests(Tmp):
    def test_saved_pages_import_as_one_backdated_read(self):
        p1, p2 = self.tmp / "p1.txt", self.tmp / "p2.txt"
        p1.write_text(PAGES[None].replace("T1", "X"))
        p2.write_text(PAGES["T2"])
        t = datetime(2026, 9, 27, 11, 33, 2, tzinfo=timezone.utc).timestamp()
        os.utime(p1, (t, t))
        os.utime(p2, (t + 12, t + 12))
        (path,) = L.import_files(SLUG, [p1, p2], directory=self.base)
        snap = L.read_snapshot(path)
        self.assertEqual((snap["fetched_at"], snap["imported"], snap["partial"]),
                         ("2026-09-27T11:33:02Z", True, False))
        self.assertEqual([r["rank"] for r in snap["rows"]], [1, 2, 3, 4])
        self.assertIn("-imported", path.name)
        self.assertEqual(L.import_files(SLUG, [p1, p2], directory=self.base), [])

    def test_pages_missing_the_end_are_partial(self):
        p1 = self.tmp / "p1.txt"
        p1.write_text(PAGES[None])
        (path,) = L.import_files(SLUG, [p1], directory=self.base)
        self.assertTrue(L.read_snapshot(path)["partial"])

    def test_downloaded_zip_is_timed_by_its_csv_name(self):
        z = self.tmp / f"{SLUG}.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr(f"{SLUG}-publicleaderboard-2026-09-27T22:54:20.csv", DOWNLOAD_CSV.encode("utf-8"))
        (path,) = L.import_files(SLUG, [z], directory=self.base)
        snap = L.read_snapshot(path)
        self.assertEqual((snap["fetched_at"], snap["format"], snap["partial"]),
                         ("2026-09-27T22:54:20Z", "download-csv", False))
        self.assertEqual([(r["team_id"], r["rank"]) for r in snap["rows"]], [(101, 1), (102, 2), (900, 3), (103, 4)])
        self.assertNotIn("user", json.dumps(snap["rows"]))
        self.assertEqual(snap["source_file"], f"{SLUG}-publicleaderboard-2026-09-27T22:54:20.csv")


def nb(ref, score, votes=3, last="2026-09-27T08:00:00"):
    return {"ref": ref, "title": f"Notebook {ref}", "lastRunTime": last, "votes": votes, "score": score}


def nb_read(notebooks, fetched_at="2026-09-28T10:00:00Z", complete=True, note=None):
    # what `kaggle.py --sdk notebooks <slug>` prints
    return json.dumps({"competition": SLUG, "fetched_at": fetched_at, "notebooks": notebooks,
                       "complete": complete, "note": note}, indent=2) + "\n"


class FakeSdk:
    """Stands in for `python3 kaggle.py --sdk notebooks ...`: answers each call with the next (exit code, stdout)."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, cmd, cwd):
        self.calls.append(cmd)
        return self.replies.pop(0)


class NotebooksTests(Tmp):
    def setUp(self):
        super().setUp()
        # an executable stand-in for kaggle.py: prints and exits as reply.json says
        self.reply = self.tmp / "reply.json"
        self.gateway.write_text(textwrap.dedent(f"""\
            import json, sys
            r = json.load(open({str(self.reply)!r}))
            sys.stderr.write(r["err"])
            sys.stdout.write(r["out"])
            sys.exit(r["code"])
        """))

    def answer(self, out="", err="", code=0):
        self.reply.write_text(json.dumps({"out": out, "err": err, "code": code}))

    def take(self, *replies, **kw):
        fake = FakeSdk(*replies)
        kw = {"gateway": self.gateway, "directory": self.base, "root": self.tmp, "run": fake, **kw}
        return (*L.take_notebooks(SLUG, **kw), fake)

    def command(self, read):
        # the command through the stand-in gateway, run the way the tool runs the real one
        self.answer(read)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = L.main(["notebooks", SLUG, "--dir", str(self.base), "--gateway", str(self.gateway)])
        return code, buf.getvalue().splitlines()

    def test_a_read_is_saved_as_gzip_json_named_by_its_time(self):
        path, doc, prev, fake = self.take((0, nb_read([nb("ann/fast-start", 0.71), nb("bo/eda", None)])),
                                          max_n=20)
        self.assertEqual(path, self.d / "notebooks" / "20260928T100000Z.json.gz")
        self.assertEqual(L.read_snapshot(path), doc)
        self.assertEqual([n["ref"] for n in doc["notebooks"]], ["ann/fast-start", "bo/eda"])
        self.assertIsNone(prev)
        self.assertEqual(fake.calls, [[sys.executable, str(self.gateway.resolve()), "--sdk", "notebooks", SLUG,
                                       "--max", "20"]])
        # a read in the same second gets a file of its own, and the first is its previous list
        path2, _, prev, fake = self.take((0, nb_read([nb("ann/fast-start", 0.71)])))
        self.assertEqual(path2.name, "20260928T100000Z-2.json.gz")
        self.assertEqual(prev, doc)
        self.assertNotIn("--max", fake.calls[0])

    def test_the_store_sits_beside_the_leaderboard(self):
        proj = self.tmp / "proj"
        self.assertEqual(L.store_dir(SLUG, root=proj, kind="notebooks"),
                         proj / "__data" / "kaggle" / SLUG / "notebooks")
        self.assertEqual(L.store_dir(SLUG, directory=str(self.base), kind="notebooks"), self.d / "notebooks")

    def test_the_table_marks_new_notebooks_and_score_changes_since_the_previous_list(self):
        code, lines = self.command(nb_read([nb("ann/fast-start", 0.71, votes=40), nb("bo/eda", None, votes=None),
                                            nb("cy/blend", 0.65)]))
        self.assertEqual(code, 0)
        self.assertIn(f"{SLUG}: 3 notebooks, 2 with a public score (complete)", lines[0])
        self.assertIn("no earlier list", lines[1])
        self.assertEqual([line.split() for line in lines[3:]],
                         [["0.71", "ann/fast-start", "40", "2026-09-27", "08:00"],
                          ["bo/eda", "2026-09-27", "08:00"], ["0.65", "cy/blend", "3", "2026-09-27", "08:00"]])
        _, lines = self.command(nb_read([nb("dee/new-idea", 0.74, last="2026-09-28T09:30:00.123000"),
                                         nb("ann/fast-start", 0.72, votes=41), nb("cy/blend", 0.65),
                                         nb("bo/eda", 0.6)], fetched_at="2026-09-28T11:00:00Z"))
        self.assertIn(": 1 new, 1 with a changed score", lines[1])
        self.assertEqual(lines[2].split(), ["score", "ref", "votes", "last", "run", "(UTC)", "change"])
        # Kaggle's order is kept; a score that was null is no change
        self.assertEqual([line.split() for line in lines[3:]],
                         [["0.74", "dee/new-idea", "3", "2026-09-28", "09:30", "new"],
                          ["0.72", "ann/fast-start", "41", "2026-09-27", "08:00", "0.71", "->", "0.72"],
                          ["0.65", "cy/blend", "3", "2026-09-27", "08:00"],
                          ["0.6", "bo/eda", "3", "2026-09-27", "08:00"]])

    def test_a_null_score_is_blank_and_never_a_change(self):
        self.command(nb_read([nb("ann/fast-start", 0.71), nb("bo/eda", None)]))
        _, lines = self.command(nb_read([nb("ann/fast-start", None), nb("bo/eda", None)],
                                        fetched_at="2026-09-28T11:00:00Z"))
        self.assertIn("2 notebooks, 0 with a public score", lines[0])
        self.assertIn(": 0 new, 0 with a changed score", lines[1])
        for line in lines[3:]:
            self.assertTrue(line.startswith(" "), line)
            self.assertEqual(len(line.split()), 4, line)
        latest = sorted((self.d / "notebooks").glob("*.json.gz"))[-1]
        self.assertEqual([n["score"] for n in L.read_snapshot(latest)["notebooks"]], [None, None])

    def test_an_incomplete_read_is_saved_and_flagged(self):
        code, lines = self.command(nb_read([nb("ann/fast-start", 0.71)], complete=False,
                                           note="the list stopped at --max 1"))
        self.assertEqual(code, 0)
        self.assertIn("(incomplete: the list stopped at --max 1)", lines[0])
        (path,) = (self.d / "notebooks").glob("*.json.gz")
        self.assertEqual(path.name, "20260928T100000Z-incomplete.json.gz")
        saved = L.read_snapshot(path)
        self.assertEqual((saved["complete"], saved["note"]), (False, "the list stopped at --max 1"))

    def test_a_failed_read_saves_nothing_and_exits_with_the_error_line(self):
        self.answer(err="kaggle_sdk: 403 Forbidden: Permission denied\n", code=1)
        p = subprocess.run([sys.executable, L.__file__, "notebooks", SLUG, "--dir", str(self.base),
                            "--gateway", str(self.gateway)], capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual((p.returncode, p.stdout), (1, ""))
        self.assertEqual(p.stderr.splitlines(), ["kaggle_sdk: 403 Forbidden: Permission denied",
                                                 "kaggle_lb: notebooks read failed (exit 1); nothing saved"])
        self.assertFalse(self.base.exists())

    def test_output_off_the_contract_saves_nothing(self):
        good = nb_read([nb("ann/fast-start", 0.71)])
        for out in ("", "not json", "[]", good.replace(SLUG, "other-competition"),
                    nb_read([nb("ann/fast-start", "0.71")]), nb_read([nb("no-owner", 0.71)])):
            with self.assertRaises(L.LeaderboardError, msg=out[:60]):
                self.take((0, out))
        self.assertFalse(self.base.exists())
        # fields beyond the contract are not kept
        path, *_ = self.take((0, nb_read([dict(nb("ann/fast-start", 0.71), authorName="Someone")])))
        self.assertNotIn("authorName", json.dumps(L.read_snapshot(path)))


if __name__ == "__main__":
    unittest.main()
