"""Tests for shared/tools/wakes.py (stdlib unittest, offline).

Each case writes a schedule into a temporary project folder and reads the wakes the tool makes of it, at a fixed
time: through the module, and through its command line where the output is the matter.

    python3 -m unittest discover -s plugins/autocode/tests -v
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "shared", "tools", "wakes.py")
NOW = datetime(2026, 11, 22, 4, 0, tzinfo=timezone.utc)


def load():
    spec = importlib.util.spec_from_file_location("wakes", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = load()

try:
    import zoneinfo  # noqa: F401 - only whether this Python has it
    ZONES = True
except ImportError:
    ZONES = False
needs_zones = unittest.skipUnless(ZONES, "zones other than UTC need Python 3.9 or later")


class WakesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wakes-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = os.path.join(self.tmp, "project")
        self.pack = os.path.join(self.project, "aipack")
        os.makedirs(os.path.join(self.pack, ".memory"))
        with open(os.path.join(self.pack, "manifest.json"), "w") as f:
            json.dump({"framework_version": "0.48.0", "project": {"name": "x"}}, f)

    def schedule(self, events):
        with open(os.path.join(self.pack, ".memory", "schedule.json"), "w") as f:
            json.dump(events, f)

    def specs(self, events, **kw):
        self.schedule(events)
        kw.setdefault("zone", "UTC")
        rows, hints = W.specs(self.project, now=NOW, **kw)
        return rows, hints

    def triggers(self, events, **kw):
        return {r["name"]: r["trigger"] for r in self.specs(events, **kw)[0]}

    def run_tool(self, *args, env=None):
        e = dict(os.environ)
        e.update(env or {})
        p = subprocess.run([sys.executable, TOOL] + list(args), cwd=self.project, env=e,
                           capture_output=True, text=True, timeout=30)
        return p.returncode, p.stdout, p.stderr


class TestTriggers(WakesCase):
    def test_recurring_events_keep_their_instants_as_utc_crons(self):
        got = self.triggers([
            {"name": "daily", "at": "2026-10-01T00:05:00+00:00", "every": 1440},
            {"name": "half-hour", "at": "2026-10-01T00:10:00+00:00", "every": 30},
            {"name": "six-hours", "at": "2026-10-01T01:30:00+00:00", "every": 360},
            {"name": "weekly", "at": "2026-10-05T17:00:00+00:00", "every": 10080},
            {"name": "hourly-no-anchor", "every": 60},
            {"name": "offset", "at": "2026-10-01T09:00:00-07:00", "every": 1440},
        ])
        self.assertEqual(got, {
            "daily": 'cron "5 0 * * *" UTC',
            "half-hour": 'cron "10,40 * * * *" UTC',
            "six-hours": 'cron "30 1,7,13,19 * * *" UTC',
            "weekly": 'cron "0 17 * * 1" UTC',
            "hourly-no-anchor": 'cron "0 * * * *" UTC',
            "offset": 'cron "0 16 * * *" UTC',
        })

    def test_a_period_cron_cannot_say_is_an_every_trigger_with_a_note(self):
        rows, _ = self.specs([{"name": "ninety", "at": "2026-10-01T00:00:00+00:00", "every": 90}])
        self.assertEqual(rows[0]["trigger"], "every 90m")
        self.assertIn("counted from when the wake is set", rows[0]["caveat"])

    def test_one_time_events_ahead_are_at_triggers_and_past_ones_are_left_out(self):
        got = self.triggers([
            {"name": "ahead", "at": "2026-12-01T18:00:00-08:00", "note": "A one-shot."},
            {"name": "past", "at": "2026-10-01T18:00:00-08:00"},
            {"name": "zulu", "at": "2027-01-01T09:00:00Z"},
        ])
        self.assertEqual(got, {"ahead": "at 2026-12-01T18:00:00-08:00", "zulu": "at 2027-01-01T09:00:00+00:00"})

    def test_a_time_with_z_is_utc_on_every_python(self):
        got = self.triggers([{"name": "zr", "at": "2026-10-01T09:30:00Z", "every": 1440}])
        self.assertEqual(got, {"zr": 'cron "30 9 * * *" UTC'})

    def test_what_the_session_clock_refuses_is_refused_here_with_the_events_name(self):
        bad = [
            ({"name": "e1", "every": 1.5}, "every"),
            ({"name": "e2", "every": True}, "every"),
            ({"name": "e3", "every": -60}, "every"),
            ({"name": "e4", "every": "1h"}, "every"),
            ({"name": "e5", "every": [60]}, "every"),
            ({"name": "e6", "every": 1e400}, "every"),
            ({"name": "e7", "every": 0}, "every"),
            ({"name": "e8", "at": "tomorrow", "every": 60}, "at"),
            ({"name": "e9", "at": 1795000000, "every": 60}, "at"),
            ({"name": "e10", "at": "2026-12-01T18:00:00"}, "at"),
            ({"name": "e11"}, "at"),
            ({"name": "e12", "at": "2027-01-01T09:00:00z"}, "at"),
            ({"name": "e13", "at": "0001-01-01T00:00:00+05:00", "every": 1440}, "edge"),
            ({"name": ""}, "name"),
            ("not an event", "event 1"),
        ]
        for event, word in bad:
            self.schedule([event])
            with self.assertRaises(ValueError) as c:
                W.specs(self.project, now=NOW, zone="UTC")
            text = str(c.exception)
            self.assertIn(word, text, event)
            if isinstance(event, dict) and event.get("name"):
                self.assertIn(event["name"], text)
        # a whole number written as a float is one
        self.assertEqual(self.triggers([{"name": "ok", "every": 60.0}]), {"ok": 'cron "0 * * * *" UTC'})

    def test_a_time_at_the_calendars_edge_is_refused_without_a_traceback(self):
        for event in ({"name": "far", "at": "9999-12-31T23:59:59-12:00"},
                      {"name": "far", "at": "9999-12-31T23:59:59-12:00", "every": 1440},
                      {"name": "far", "at": "0001-01-01T00:00:00+05:00", "every": 1440}):
            self.schedule([event])
            p = subprocess.run([sys.executable, TOOL, "--dir", ".", "--zone", "UTC"], cwd=self.project,
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 2, event)
            self.assertIn("far", p.stderr)
            self.assertIn("edge", p.stderr)
            self.assertNotIn("Traceback", p.stderr)
        # a time far ahead that every zone can still count from is taken
        rows, _ = self.specs([{"name": "later", "at": "9000-01-01T00:00:00+00:00"}])
        self.assertEqual(rows[0]["trigger"], "at 9000-01-01T00:00:00+00:00")

    def test_a_recurring_event_that_starts_later_says_so(self):
        rows, _ = self.specs([{"name": "later", "at": "2027-03-01T09:00:00+00:00", "every": 1440}])
        self.assertEqual(rows[0]["trigger"], 'cron "0 9 * * *" UTC')
        # the cron runs from tomorrow on, and the line says what the schedule meant
        self.assertEqual(rows[0]["next"], "Nov 22 9:00 AM UTC")
        self.assertIn("2027-03-01", rows[0]["caveat"])
        self.assertIn("before", rows[0]["caveat"])

    def test_skip_prefix_leaves_out_another_agents_events(self):
        got = self.triggers([
            {"name": "fleet-audit", "every": 60},
            {"name": "hourly", "every": 60},
        ], skip=["fleet-"])
        self.assertEqual(list(got), ["hourly"])


@needs_zones
class TestLocalTime(WakesCase):
    def test_an_event_with_a_zone_keeps_its_local_time(self):
        # written in summer time as 9:00 Pacific: 16:00 UTC then, 17:00 UTC in winter
        got = self.triggers([
            {"name": "standup", "at": "2026-10-01T09:00:00-07:00", "every": 1440, "zone": "America/Los_Angeles"},
        ])
        self.assertEqual(got, {"standup": 'cron "0 9 * * *" America/Los_Angeles'})

    def test_local_prefix_ties_events_to_the_zone_named(self):
        events = [
            {"name": "team-sync", "at": "2026-07-01T09:30:00+02:00", "every": 1440},
            {"name": "board-reset", "at": "2026-07-01T00:05:00+00:00", "every": 1440},
        ]
        got = self.triggers(events, zone="Europe/Berlin", local=["team-"])
        self.assertEqual(got, {
            "team-sync": 'cron "30 9 * * *" Europe/Berlin',
            "board-reset": 'cron "5 0 * * *" UTC',
        })

    def test_the_weekday_is_the_local_one(self):
        # Monday 17:00 Pacific is Tuesday 00:00 UTC
        event = {"name": "weekly", "at": "2026-10-05T17:00:00-07:00", "every": 10080}
        self.assertEqual(self.triggers([event]), {"weekly": 'cron "0 0 * * 2" UTC'})
        event["zone"] = "America/Los_Angeles"
        self.assertEqual(self.triggers([event]), {"weekly": 'cron "0 17 * * 1" America/Los_Angeles'})

    def test_minutes_follow_a_zone_with_a_half_hour(self):
        event = {"name": "tick", "at": "2026-10-01T09:15:00+05:30", "every": 60}
        self.assertEqual(self.triggers([event]), {"tick": 'cron "45 * * * *" UTC'})
        event["zone"] = "Asia/Kolkata"
        self.assertEqual(self.triggers([event]), {"tick": 'cron "15 * * * *" Asia/Kolkata'})

    def test_a_one_time_event_stays_an_instant(self):
        got = self.triggers([{"name": "once", "at": "2026-12-01T18:00:00-08:00", "zone": "America/Los_Angeles"}])
        self.assertEqual(got, {"once": "at 2026-12-01T18:00:00-08:00"})

    def test_a_zone_that_does_not_exist_is_refused(self):
        self.schedule([{"name": "x", "every": 60, "zone": "Mars/Olympus"}])
        with self.assertRaises(ValueError) as c:
            W.specs(self.project, now=NOW, zone="UTC")
        self.assertIn("Mars/Olympus", str(c.exception))
        self.schedule([{"name": "x", "every": 60}])
        with self.assertRaises(ValueError):
            W.specs(self.project, now=NOW, zone="Nowhere/At_All")

    def test_recurring_events_with_a_local_offset_and_no_zone_are_pointed_out(self):
        rows, hints = self.specs([
            {"name": "standup", "at": "2026-10-01T09:00:00-07:00", "every": 1440},
            {"name": "review", "at": "2026-10-01T15:00:00-07:00", "every": 10080},
            {"name": "reset", "at": "2026-10-01T00:05:00+00:00", "every": 1440},
            {"name": "poll", "at": "2026-10-01T09:10:00-07:00", "every": 30},
            {"name": "tied", "at": "2026-10-01T09:00:00-07:00", "every": 1440, "zone": "America/Los_Angeles"},
        ])
        self.assertEqual(len(hints), 1)
        self.assertIn("standup, review", hints[0])
        self.assertIn("--local", hints[0])
        self.assertIn('"zone"', hints[0])
        # a poll within the hour reads the same in every whole-hour zone
        self.assertNotIn("poll", hints[0])
        self.assertNotIn("reset", hints[0])
        self.assertNotIn("tied", hints[0])
        _, hints = self.specs([{"name": "reset", "at": "2026-10-01T00:05:00+00:00", "every": 1440}])
        self.assertEqual(hints, [])

    def test_the_next_time_is_shown_in_the_zone_named(self):
        event = [{"name": "daily", "at": "2026-10-01T00:05:00+00:00", "every": 1440}]
        self.assertEqual(self.specs(event)[0][0]["next"], "Nov 23 12:05 AM UTC")
        self.assertEqual(self.specs(event, zone="America/Los_Angeles")[0][0]["next"], "Nov 22 4:05 PM PST")

    def test_the_next_time_is_the_triggers_own(self):
        # written in summer time: the cron keeps 9:00 local, so its next run is 9:00, not 8:00
        event = [{"name": "standup", "at": "2026-10-01T09:00:00-07:00", "every": 1440,
                  "zone": "America/Los_Angeles"}]
        row = self.specs(event, zone="America/Los_Angeles")[0][0]
        self.assertEqual((row["trigger"], row["next"]),
                         ('cron "0 9 * * *" America/Los_Angeles', "Nov 22 9:00 AM PST"))
        # a weekly one on its weekday, a list of hours at the next of them
        rows, _ = self.specs([
            {"name": "weekly", "at": "2026-10-05T17:00:00-07:00", "every": 10080, "zone": "America/Los_Angeles"},
            {"name": "six-hours", "at": "2026-10-01T01:30:00+00:00", "every": 360},
            {"name": "ninety", "at": "2026-10-01T00:00:00+00:00", "every": 90},
        ], zone="America/Los_Angeles")
        self.assertEqual([r["next"] for r in rows],
                         ["Nov 23 5:00 PM PST", "Nov 21 11:30 PM PST", "Nov 21 9:30 PM PST"])

    def test_a_time_the_clocks_repeat_runs_at_its_first_occurrence_whatever_zone_now_is_in(self):
        from zoneinfo import ZoneInfo
        la = ZoneInfo("America/Los_Angeles")
        # 1:45 is there twice on the day the clocks go back; now is 1:30 the second time
        now = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)
        want = datetime(2026, 11, 2, 9, 45, tzinfo=timezone.utc)
        for given in (now, now.astimezone(la)):
            self.assertEqual(W.next_run(([45], [1], None), la, given), want)

    def test_a_time_the_clocks_skip_runs_at_the_next_minute_that_exists(self):
        # 2:30 does not exist on the day the clocks go forward: autocode runs it at 3:00
        self.schedule([{"name": "night", "at": "2026-01-10T02:30:00-08:00", "every": 1440,
                        "zone": "America/Los_Angeles"}])
        rows, _ = W.specs(self.project, now=datetime(2027, 3, 14, 9, 0, tzinfo=timezone.utc),
                          zone="America/Los_Angeles")
        self.assertEqual((rows[0]["trigger"], rows[0]["next"]),
                         ('cron "30 2 * * *" America/Los_Angeles', "Mar 14 3:00 AM PDT"))

    def test_an_event_with_a_zone_and_no_time_stays_on_utc(self):
        rows, _ = self.specs([{"name": "tick", "every": 1440, "zone": "Europe/London"}])
        self.assertEqual(rows[0]["trigger"], 'cron "0 0 * * *" UTC')
        self.assertIn("no at", rows[0]["caveat"])

    def test_without_a_zone_named_the_machines_own_is_used(self):
        self.assertEqual(W.local_zone({"TZ": "Europe/Berlin"}), "Europe/Berlin")
        self.assertEqual(W.local_zone({"TZ": ":Europe/Berlin"}), "Europe/Berlin")
        self.assertEqual(W.local_zone({"TZ": "Nowhere/At_All"}), W.local_zone({}))
        # whatever this machine has, it is a zone that exists
        W.zone_of(W.local_zone({}))


class TestNames(WakesCase):
    def test_a_name_that_is_no_wake_name_gets_one(self):
        rows, _ = self.specs([
            {"name": "with space", "every": 60, "note": "A name with a space."},
            {"name": "ok.name:1_x-y", "every": 60},
            {"name": "caf\u00e9 / lunch!", "every": 60},
            {"name": "...", "every": 60},
            {"name": "nl\n", "every": 60},
            {"name": "-dash", "every": 60},
            {"name": "guard", "every": 60},
        ])
        self.assertEqual([r["wake"] for r in rows],
                         ["with-space", "ok.name:1_x-y", "caf-lunch", "event", "nl", "-dash", "guard-2"])
        self.assertEqual([r["name"] for r in rows],
                         ["with space", "ok.name:1_x-y", "caf\u00e9 / lunch!", "...", "nl", "-dash", "guard"])
        self.assertIn("with-space", rows[0]["caveat"])
        self.assertEqual(rows[1]["caveat"], "")
        # the idle guard's own wake is not taken
        self.assertIn("guard-2", rows[6]["caveat"])
        # the text still finds the whole note by the event's own name, whatever it starts with
        self.assertIn("--show='with space'", rows[0]["text"])
        self.assertIn("--show=ok.name:1_x-y", rows[1]["text"])
        self.assertIn("--show=-dash", rows[5]["text"])
        self.schedule([{"name": "-dash", "every": 60, "note": "N"}])
        code, out, _ = self.run_tool("--dir", ".", "--show=-dash")
        self.assertEqual(json.loads(out), [{"name": "-dash", "every": 60, "note": "N"}])

    def test_two_events_never_share_a_wake_name(self):
        rows, _ = self.specs([
            {"name": "a b", "every": 60},
            {"name": "a-b", "every": 60},
            {"name": "a/b", "every": 60},
        ])
        self.assertEqual([r["wake"] for r in rows], ["a-b-2", "a-b", "a-b-3"])

    def test_the_text_names_the_event_and_where_its_note_is(self):
        rows, _ = self.specs([{"name": "daily", "every": 1440, "note": "Look  at\nthe board. " + "x" * 300}])
        text = rows[0]["text"]
        self.assertTrue(text.startswith('Scheduled event "daily" (every 1440 min): Look at the board. xxx'), text)
        self.assertIn("... Whole note: python3 ", text)
        self.assertTrue(text.endswith("--dir . --show=daily"), text)


class TestCommand(WakesCase):
    EVENTS = [
        {"name": "daily", "at": "2026-10-01T00:05:00+00:00", "every": 1440, "note": "After the reset."},
        {"name": "standup", "at": "2026-10-01T09:00:00-07:00", "every": 1440, "note": "People."},
        {"name": "with space", "every": 90},
    ]

    def test_a_schedule_that_is_wrong_is_exit_2_with_the_events_name(self):
        for event in ({"name": "odd", "every": "1h"}, {"name": "odd", "every": [60]}, {"name": "odd", "at": "x"}):
            self.schedule([event])
            code, out, err = self.run_tool("--dir", ".", "--zone", "UTC")
            self.assertEqual((code, out), (2, ""), event)
            self.assertIn("odd", err)
            self.assertNotIn("Traceback", err)

    def test_lines(self):
        self.schedule(self.EVENTS)
        code, out, err = self.run_tool("--dir", ".", "--zone", "UTC")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith('daily | cron "5 0 * * *" UTC | next '), lines)
        self.assertTrue(lines[1].startswith('standup | cron "0 16 * * *" UTC | next '), lines)
        self.assertTrue(lines[2].startswith("with-space | every 90m | next "), lines)
        self.assertIn("NOTE", lines[2])
        self.assertIn("3 wake(s)", out)
        self.assertIn("standup", lines[-1])
        self.assertIn("--local", lines[-1])

    @needs_zones
    def test_local_and_zone_flags(self):
        self.schedule(self.EVENTS)
        code, out, _ = self.run_tool("--dir", ".", "--zone", "America/Los_Angeles", "--local", "stand")
        self.assertEqual(code, 0)
        self.assertRegex(out, r'standup \| cron "0 9 \* \* \*" America/Los_Angeles \| next \w+ \d+ 9:00 AM P[SD]T')
        self.assertNotIn("--local", out)

    def test_json(self):
        self.schedule(self.EVENTS)
        code, out, err = self.run_tool("--dir", ".", "--zone", "UTC", "--json")
        self.assertEqual(code, 0)
        # one list, as before; what is not a wake goes to stderr
        data = json.loads(out)
        self.assertEqual([r["wake"] for r in data], ["daily", "standup", "with-space"])
        self.assertEqual(sorted(data[0]), ["caveat", "name", "next", "text", "trigger", "wake"])
        self.assertIn("--local", err)

    def test_show_prints_one_events_whole_entry(self):
        self.schedule(self.EVENTS)
        code, out, _ = self.run_tool("--dir", ".", "--show", "with space")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [{"name": "with space", "every": 90}])
        self.assertIn("no event named none", self.run_tool("--dir", ".", "--show", "none")[1])

    def test_what_cannot_be_read_is_exit_2(self):
        self.assertEqual(self.run_tool("--dir", ".")[0], 2)
        self.schedule({"not": "a list"})
        code, _, err = self.run_tool("--dir", ".")
        self.assertEqual(code, 2)
        self.assertIn("not a JSON list", err)
        self.schedule([{"name": "x", "every": 60}])
        code, _, err = self.run_tool("--dir", ".", "--zone", "Nowhere/At_All")
        self.assertEqual(code, 2)
        self.assertIn("Nowhere/At_All", err)
        with open(os.path.join(self.pack, ".memory", "schedule.json"), "w") as f:
            f.write("[ not json")
        code, _, err = self.run_tool("--dir", ".")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.run_tool("--dir", os.path.join(self.tmp, "none"))[0], 2)

    def test_no_project_is_named_in_the_tool(self):
        with open(TOOL, encoding="utf-8") as f:
            source = f.read()
        for word in ("Kaggle", "kaggle", "Los_Angeles", "Pacific", "PACIFIC"):
            self.assertNotIn(word, source)


if __name__ == "__main__":
    unittest.main()
