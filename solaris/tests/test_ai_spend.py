# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.ai_spend (estimated AI spend), on synthetic Claude Code transcripts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from solaris.tools import ai_spend as A

PACK = {"project": {"name": "x", "slug": "x"}, "framework_version": "0.40.0"}
NOW = datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc).timestamp()   # 13:00 on Oct 1 in Los Angeles
SECRET = "TOP-SECRET-PROMPT-TEXT"
M = 1_000_000


def _project(root, pack="aipack", config=None, defaults=None):
    (root / pack).mkdir(parents=True)
    (root / pack / "manifest.json").write_text(json.dumps(PACK), encoding="utf-8")
    if config is not None:
        (root / pack / ".memory").mkdir()
        (root / pack / ".memory" / "config.json").write_text(json.dumps(config), encoding="utf-8")
    if defaults is not None:
        (root / pack / "defaults.json").write_text(json.dumps(defaults), encoding="utf-8")
    return root


def _line(rid, ts, cwd, session, model="claude-opus-5-5", inp=0, w5=0, w1=0, read=0, out=0):
    usage = {"input_tokens": inp, "cache_creation_input_tokens": w5 + w1, "cache_read_input_tokens": read,
             "output_tokens": out, "service_tier": "standard",
             "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}}
    return json.dumps({"parentUuid": None, "isSidechain": False, "type": "assistant", "timestamp": ts,
                       "cwd": str(cwd), "sessionId": session, "requestId": "req_" + rid,
                       "message": {"id": rid, "role": "assistant", "model": model, "usage": usage,
                                   "content": [{"type": "text", "text": SECRET}]}})


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two projects under a fake Solaris root, and transcripts: an alpha session (with a subagent working at the
    root, and a copy of one response in a resumed session), a framework session at the root and a beta session."""
    root = tmp_path / "solaris"
    alpha = _project(root / "projects" / "my" / "alpha", config={A.BUDGET_KEY: 10}, defaults={A.BUDGET_KEY: 50})
    beta = _project(root / "projects" / "my" / "beta", pack="brain", defaults={A.BUDGET_KEY: 100})
    (alpha / "source").mkdir()
    fw = tmp_path / "fw-config.json"
    fw.write_text(json.dumps({A.TZ_KEY: "America/Los_Angeles"}), encoding="utf-8")
    monkeypatch.setattr(A, "FRAMEWORK_CONFIG", fw)
    monkeypatch.setattr(A, "REPO_ROOT", root)
    monkeypatch.setattr(A, "_now", lambda: NOW)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    tx = tmp_path / "claude" / "projects" / "-solaris"
    _write(tx / "s1.jsonl", [
        # r1 logged once per content block, its output still growing: counted once, with the final usage
        _line("r1", "2026-10-01T16:00:00.000Z", alpha / "source", "s1", inp=M, out=100),
        _line("r1", "2026-10-01T16:00:01.000Z", alpha / "source", "s1", inp=M, out=1000),
        # 23:30 on Sep 30 in Los Angeles: yesterday's
        _line("r2", "2026-10-01T06:30:00.000Z", alpha, "s1", out=M),
        # made at the Solaris root, in an alpha session: alpha's
        _line("r3", "2026-10-01T17:00:00.000Z", root, "s1", w5=M),
        json.dumps({"type": "assistant", "timestamp": "2026-10-01T17:00:00Z", "cwd": str(alpha), "sessionId": "s1",
                    "message": {"id": "e1", "model": "<synthetic>", "usage": {"input_tokens": 0}}}),
        json.dumps({"type": "user", "cwd": str(alpha), "toolUseResult": {"usage": {"input_tokens": 5 * M}}}),
        '{"type": "assistant", "usage": broken',
    ])
    _write(tx / "s1" / "subagents" / "agent-a1.jsonl", [
        _line("r4", "2026-10-01T18:00:00.000Z", root, "s1", model="claude-haiku-4-5-20251001", inp=M)])
    _write(tx / "s3.jsonl", [_line("r1", "2026-10-01T16:00:00.000Z", alpha / "source", "s3", inp=M, out=1000)])
    _write(tx / "s2.jsonl", [_line("r5", "2026-10-01T19:00:00.000Z", root, "s2", model="claude-mystery-9", inp=M)])
    _write(tx / "s4.jsonl", [_line("r6", "2026-10-01T19:30:00.000Z", beta, "s4", model="claude-fable-5-1",
                                   read=M, w1=M)])
    return {"root": root, "alpha": alpha, "beta": beta, "tx": tx}


def _run(capsys, *argv):
    code = A.main([str(a) for a in argv])
    out = capsys.readouterr().out
    assert SECRET not in out   # message content is never printed
    return code, out


def test_price_keys_and_costs():
    assert A.price_key("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert A.price_key("claude-opus-5-5[1m]") == "claude-opus-5-5"
    assert A.price_key("claude-opus-5-20260101") == "claude-opus-5"
    assert A.price_key("us.anthropic.claude-fable-5-1-v1:0") == "claude-fable-5-1"
    assert A.price_key("gpt-6-luna") is None
    assert A.cost("claude-opus-5-5", (M, 0, 0, 0, 0)) == pytest.approx(4.00)
    assert A.cost("claude-opus-5-5", (0, M, M, M, M)) == pytest.approx(5.00 + 8.00 + 0.20 + 20.00)
    assert A.cost("claude-fable-5", (0, 0, 0, M, 0)) == pytest.approx(1.00)
    assert A.cost("claude-mystery-9", (M, 0, 0, 0, 0)) is None
    assert A.PRICES_AS_OF and all(len(p) == 3 for p in A.PRICES.values())


def test_all_projects_json(world, capsys):
    code, out = _run(capsys, "--json")
    assert code == A.OVER   # alpha spent $10.02 today against its $10 limit
    rep = json.loads(out)
    assert rep["timezone"] == "America/Los_Angeles" and rep["since"] is None
    rows = {(r["day"], r["project"]): r for r in rep["rows"]}
    assert set(rows) == {("2026-09-30", "projects/my/alpha"), ("2026-10-01", "projects/my/alpha"),
                         ("2026-10-01", "projects/my/beta"), ("2026-10-01", None)}
    today = rows["2026-10-01", "projects/my/alpha"]
    assert today["requests"] == 3   # r1 once (despite its copy in s3), r3 and the subagent's r4
    assert (today["input_tokens"], today["cache_write_tokens"], today["output_tokens"]) == (2 * M, M, 1000)
    assert today["usd"] == pytest.approx(4.02 + 5.00 + 1.00)
    assert rows["2026-09-30", "projects/my/alpha"]["usd"] == pytest.approx(20.00)
    assert rows["2026-10-01", "projects/my/beta"]["usd"] == pytest.approx(0.25 + 20.00)
    assert rows["2026-10-01", None]["usd"] == 0 and rep["unpriced_models"] == {"claude-mystery-9": 1}
    budgets = {b["project"]: b for b in rep["budgets"]}
    assert budgets["projects/my/alpha"] == {"project": "projects/my/alpha", "daily_budget_usd": 10.0,
                                            "today_usd": pytest.approx(10.02), "over": True,
                                            "today_unpriced_requests": 0, "lower_bound": False}
    assert budgets["projects/my/beta"]["daily_budget_usd"] == 100.0 and not budgets["projects/my/beta"]["over"]


def test_one_project_today_table(world, capsys):
    code, out = _run(capsys, "--dir", world["alpha"], "--today")
    assert code == A.OVER
    assert "since 2026-10-01 00:00 PDT (days in America/Los_Angeles)" in out
    assert "projects/my/alpha" in out and "beta" not in out and "unattributed" not in out and "total" not in out
    assert "10.02" in out and "20.00" not in out   # yesterday is outside the window
    assert "daily limit projects/my/alpha: $10.02 today of about $10.00 (100%), OVER" in out
    code, out = _run(capsys, "--dir", world["beta"], "--today")
    assert code == 0 and "$20.25 today of about $100.00 (20%)" in out and "OVER" not in out


def test_table_has_day_totals_and_flags_unpriced_models(world, capsys):
    code, out = _run(capsys, "--since", "2026-10-01")
    assert code == A.OVER
    lines = out.splitlines()
    assert any(line.startswith("2026-10-01") and "(unattributed)" in line for line in lines)
    total = next(line for line in lines if line.startswith("2026-10-01") and " total " in line)
    assert total.split()[-1] == ">=30.27"   # 10.02 + 20.25 + 0 unpriced: the day's total is a lower bound
    assert not any(line.startswith("2026-09-30") for line in lines)
    assert "not priced: claude-mystery-9 (1 request; tokens counted)" in out


def test_days_follow_the_owner_timezone(world, capsys, monkeypatch):
    A.FRAMEWORK_CONFIG.write_text(json.dumps({A.TZ_KEY: "UTC"}), encoding="utf-8")
    code, out = _run(capsys, "--dir", world["alpha"], "--today", "--json")
    rep = json.loads(out)
    assert [r["usd"] for r in rep["rows"]] == [pytest.approx(30.02)]   # 06:30 UTC is today in UTC
    A.FRAMEWORK_CONFIG.write_text("{}", encoding="utf-8")
    assert A.owner_zone() is None   # no owner.timezone: the machine's zone


def test_a_project_timezone_comes_first(world, capsys):
    alpha, beta = world["alpha"] / "aipack", world["beta"] / "brain"

    def zone(*argv):
        return json.loads(_run(capsys, *argv, "--json")[1])["timezone"]

    assert zone("--dir", world["beta"]) == "America/Los_Angeles"   # the project sets none: the framework's
    (alpha / ".memory" / "config.json").write_text(json.dumps({A.BUDGET_KEY: 10, A.TZ_KEY: "UTC"}), encoding="utf-8")
    (alpha / "defaults.json").write_text(json.dumps({A.BUDGET_KEY: 50, A.TZ_KEY: "Asia/Tokyo"}), encoding="utf-8")
    (beta / "defaults.json").write_text(json.dumps({A.BUDGET_KEY: 100, A.TZ_KEY: "Asia/Tokyo"}), encoding="utf-8")
    code, out = _run(capsys, "--dir", world["alpha"], "--today", "--json")
    rep = json.loads(out)
    assert rep["timezone"] == "UTC"   # the pack's .memory/config.json wins over its defaults.json
    assert [r["usd"] for r in rep["rows"]] == [pytest.approx(30.02)]   # 06:30 UTC is today in UTC
    assert zone("--dir", world["beta"]) == "Asia/Tokyo"   # else the pack's defaults.json
    assert zone() == "America/Los_Angeles"   # every project at once: the framework's
    A.FRAMEWORK_CONFIG.write_text("{}", encoding="utf-8")
    (beta / "defaults.json").write_text(json.dumps({A.BUDGET_KEY: 100}), encoding="utf-8")
    assert A.owner_zone(beta) is None   # set nowhere: the machine's zone
    (alpha / ".memory" / "config.json").write_text(json.dumps({A.TZ_KEY: "Mars/Olympus_Mons"}), encoding="utf-8")
    code, out = _run(capsys, "--dir", world["alpha"], "--today")
    assert code == 1 and f"{alpha / '.memory' / 'config.json'}: owner.timezone 'Mars/Olympus_Mons'" in out


def test_old_files_are_skipped_for_a_recent_window(world, capsys):
    old = NOW - 3 * 86400
    os.utime(world["tx"] / "s4.jsonl", (old, old))   # last written days ago: nothing in it can be today's
    code, out = _run(capsys, "--today", "--json")
    assert all(r["project"] != "projects/my/beta" for r in json.loads(out)["rows"])


def test_config_and_argument_errors(world, capsys):
    A.FRAMEWORK_CONFIG.write_text(json.dumps({A.TZ_KEY: "Mars/Olympus_Mons"}), encoding="utf-8")
    code, out = _run(capsys, "--today")
    assert code == 1 and "owner.timezone 'Mars/Olympus_Mons' is not a time zone" in out
    A.FRAMEWORK_CONFIG.write_text("{", encoding="utf-8")
    assert _run(capsys, "--today")[0] == 1
    A.FRAMEWORK_CONFIG.write_text("{}", encoding="utf-8")
    code, out = _run(capsys, "--dir", world["root"])
    assert code == 1 and "no ai-pack" in out
    code, out = _run(capsys, "--dir", world["root"] / "missing")
    assert code == 1 and "--dir must be a project root" in out
    for argv in (["--since", "last week"], ["--since", "2027-01-01"], ["--today", "--since", "2026-10-01"]):
        with pytest.raises(SystemExit) as exc:
            A.main(argv)
        assert exc.value.code == 2


@pytest.mark.parametrize("value", [0, -5, "ten", True, None])
def test_daily_limit_values(world, capsys, value):
    (world["alpha"] / "aipack" / ".memory" / "config.json").write_text(json.dumps({A.BUDGET_KEY: value}),
                                                                      encoding="utf-8")
    code, out = _run(capsys, "--dir", world["alpha"], "--today")
    if value is None:   # null in config.json overrides defaults.json: no limit
        assert code == 0 and "daily limit" not in out
    else:
        assert code == 1 and "ai.daily_budget_usd must be a positive number" in out and "10.02" in out


def test_no_transcripts(tmp_path, monkeypatch, capsys, world):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "empty"))
    code, out = _run(capsys, "--dir", world["beta"], "--today")
    assert code == 0 and "no Claude Code usage in that window" in out and "$0.00 today of about $100.00" in out


MAIN = "5e55a0b1-1111-2222-3333-444455556666"


def _turn(rid, ts, cwd, session=MAIN, model="claude-opus-5-5", effort=None, agent=None, side=False, out=M):
    """An assistant line as Claude Code writes it: effort at the top, a sidechain mark, a worker's agent id."""
    d = json.loads(_line(rid, ts, cwd, session, model=model, out=out))
    d["isSidechain"] = side
    if effort is not None:
        d["effort"] = effort
    if agent is not None:
        d["agentId"] = agent
    return json.dumps(d)


def _hook(uuid, ts, cwd, kind="hook_success", event="UserPromptSubmit", session=MAIN, **fields):
    a = {"type": kind, "hookName": event, "hookEvent": event, "toolUseID": "t", "command": "uv run -m x",
         "exitCode": 0 if kind == "hook_success" else 1, "durationMs": 40, "stdout": "", "stderr": "", **fields}
    return json.dumps({"parentUuid": None, "isSidechain": False, "type": "attachment", "attachment": a,
                       "timestamp": ts, "cwd": str(cwd), "sessionId": session, "uuid": uuid})


@pytest.fixture
def crew(world):
    """Alpha's master (effort max, then high, and one line from an older harness without effort), two workers on
    different models (a subagent and a workflow agent), hook runs with one failure, and noise that must not count."""
    tx, alpha, beta = world["tx"], world["alpha"], world["beta"]
    long_err = "Traceback: hook failed " + "x" * 200
    _write(tx / f"{MAIN}.jsonl", [
        _turn("m1", "2026-10-01T18:40:00.000Z", alpha, effort="max"),
        _turn("m1", "2026-10-01T18:40:01.000Z", alpha, effort="max"),   # the same response, a later block
        _turn("m2", "2026-10-01T18:50:00.000Z", alpha, effort="max"),
        _turn("m3", "2026-10-01T19:00:00.000Z", alpha, effort="high"),
        _turn("m4", "2026-10-01T19:10:00.000Z", alpha),                 # no effort recorded: not guessed
        _turn("m0", "2026-10-01T16:00:00.000Z", alpha, effort="low"),   # before the window
        _hook("h1", "2026-10-01T18:40:00.000Z", alpha),
        _hook("h2", "2026-10-01T18:45:00.000Z", alpha, event="SessionStart"),
        _hook("h3", "2026-10-01T18:46:00.000Z", alpha, kind="hook_non_blocking_error", stderr=long_err,
              stdout="ignored: stderr comes first"),
        _hook("h4", "2026-10-01T18:47:00.000Z", alpha, kind="hook_additional_context", content="context"),
        _hook("h0", "2026-10-01T16:00:00.000Z", alpha, kind="hook_blocking_error", stderr="old"),
    ])
    _write(tx / MAIN / "subagents" / "agent-a1.jsonl", [
        _turn("w1", "2026-10-01T18:41:00.000Z", world["root"], model="claude-sonnet-5-5", effort="max", agent="a1",
              side=True),
        _turn("w2", "2026-10-01T18:42:00.000Z", world["root"], model="claude-sonnet-5-5", effort="max", agent="a1",
              side=True)])
    _write(tx / MAIN / "subagents" / "workflows" / "wf_1" / "agent-b2.jsonl", [
        _turn("w3", "2026-10-01T18:43:00.000Z", alpha, model="claude-haiku-4-5-20251001", side=True),
        _turn("w0", "2026-10-01T16:30:00.000Z", alpha, model="claude-haiku-4-5-20251001", side=True)])
    # a resumed session holding a copy of a hook line, and another project's session
    _write(tx / "resumed.jsonl", [_hook("h1", "2026-10-01T18:40:00.000Z", alpha, session="resumed")])
    _write(tx / "beta.jsonl", [_turn("b1", "2026-10-01T18:40:00.000Z", beta, session="beta", effort="medium"),
                               _hook("hb", "2026-10-01T18:40:00.000Z", beta, session="beta",
                                     kind="hook_non_blocking_error", stderr="beta's")])
    return world


# 11:30 in Los Angeles, 18:30 UTC: after the world's own responses of the morning
WINDOW = ("--since", "2026-10-01T11:30:00-07:00")


def test_detail_reports_who_answered(crew, capsys):
    code, out = _run(capsys, "--dir", crew["alpha"], *WINDOW, "--json", "--detail")
    assert code == A.OVER   # the exit code is the spend report's: alpha is over its $10 limit today
    d = json.loads(out)["detail"]
    assert d["main"] == {"turns": 4, "models": {"claude-opus-5-5": 4}, "effort": {"max": 2, "high": 1}}
    assert d["workers"] == [
        {"session": "5e55a0b1", "agent": "a1", "first": "2026-10-01T11:41:00-07:00", "model": "claude-sonnet-5-5",
         "effort": "max", "usd": pytest.approx(20.00), "unpriced_requests": 0, "lower_bound": False},
        {"session": "5e55a0b1", "agent": "b2", "first": "2026-10-01T11:43:00-07:00",
         "model": "claude-haiku-4-5-20251001", "effort": None, "usd": pytest.approx(5.00), "unpriced_requests": 0,
         "lower_bound": False}]
    hooks = d["hooks"]
    assert (hooks["runs"], hooks["failed"]) == (3, 1)
    assert hooks["failures"] == [{"ts": "2026-10-01T11:46:00-07:00", "event": "UserPromptSubmit",
                                  "text": ("Traceback: hook failed " + "x" * 200)[:120]}]
    # the spend report itself is unchanged by --detail
    plain = json.loads(_run(capsys, "--dir", crew["alpha"], *WINDOW, "--json")[1])
    assert "detail" not in plain and plain["rows"] == json.loads(out)["rows"]


def test_detail_follows_the_window_and_the_project(crew, capsys):
    late = ["--since", "2026-10-01T11:55:00-07:00", "--json", "--detail"]
    d = json.loads(_run(capsys, "--dir", crew["alpha"], *late)[1])["detail"]
    assert d["main"] == {"turns": 2, "models": {"claude-opus-5-5": 2}, "effort": {"high": 1}}
    assert d["workers"] == [] and d["hooks"] == {"runs": 0, "failed": 0, "failures": []}
    d = json.loads(_run(capsys, "--dir", crew["beta"], *late)[1])["detail"]
    assert d["main"]["effort"] == {} and d["hooks"]["runs"] == 0
    d = json.loads(_run(capsys, "--dir", crew["beta"], "--since", "2026-10-01", "--json", "--detail")[1])["detail"]
    assert d["main"]["effort"] == {"medium": 1} and d["workers"] == []
    assert [f["text"] for f in d["hooks"]["failures"]] == ["beta's"]


def test_detail_as_a_table(crew, capsys):
    code, out = _run(capsys, "--dir", crew["alpha"], *WINDOW, "--detail")
    assert code == A.OVER
    assert "main thread: 4 responses; models claude-opus-5-5 4; effort max 2, high 1" in out
    assert "workers: 2" in out
    assert "  5e55a0b1 a1  from 2026-10-01T11:41:00-07:00  claude-sonnet-5-5  effort max  $20.00" in out
    assert "  5e55a0b1 b2  from 2026-10-01T11:43:00-07:00  claude-haiku-4-5-20251001  effort -  $5.00" in out
    assert "hooks: 3 runs, 1 failed" in out and "11:46:00-07:00 UserPromptSubmit: Traceback: hook failed" in out


# ----------------------------------------------------------------- lower bounds (requests with no list price)

def test_price_rows_read_from_a_model_catalog():
    assert A.cost("claude-fable-5-2", (M, 0, 0, M, M)) == pytest.approx(8.00 + 0.20 + 40.00)
    assert A.cost("claude-fable-5-2", (0, M, M, 0, 0)) == pytest.approx(10.00 + 16.00)   # 1.25x and 2x input
    assert A.price_key("claude-haiku-5-5-20261101") == "claude-haiku-5-5"
    # a prompt of up to 100,000 tokens at the base price, a longer one priced as a whole at the long-prompt price
    assert A.cost("claude-haiku-5-5", (40_000, 0, 0, 60_000, M)) == pytest.approx(0.004 + 0.0006 + 0.50)
    assert A.cost("claude-haiku-5-5", (40_000, 1, 0, 60_000, M)) == pytest.approx(0.02 + 0.000000625 + 0.003 + 2.50)
    assert A.cost("claude-haiku-5-5", (0, 0, 0, 200_000, 0)) == pytest.approx(0.01)
    assert A.cost("claude-opus-5-5", (0, 0, 0, 200_000, 0)) == pytest.approx(0.04)   # no long-prompt price: flat
    assert A.cost("claude-sonnet-5-5", (M, 0, 0, M, M)) == pytest.approx(2.00 + 0.10 + 10.00)   # cache reads at 0.10
    assert A.PRICES_AS_OF == "2026-11-17"   # the date of the list prices the rows were checked against
    assert set(A.LONG_PROMPT) <= set(A.PRICES) and all(len(p) == 3 for _n, p in A.LONG_PROMPT.values())


def test_json_says_which_figures_are_lower_bounds(world, capsys):
    rep = json.loads(_run(capsys, "--json")[1])
    assert rep["lower_bound"] is True and rep["unpriced_requests"] == 1
    rows = {(r["day"], r["project"]): r for r in rep["rows"]}
    loose = rows["2026-10-01", None]   # the framework session's response, on a model with no price
    assert (loose["usd"], loose["unpriced_requests"], loose["lower_bound"]) == (0, 1, True)
    alpha = rows["2026-10-01", "projects/my/alpha"]
    assert (alpha["unpriced_requests"], alpha["lower_bound"]) == (0, False)
    assert rep["budgets"] and all(not b["lower_bound"] for b in rep["budgets"])
    # one project whose requests all have a price: no lower bound anywhere
    rep = json.loads(_run(capsys, "--dir", world["beta"], "--today", "--json")[1])
    assert rep["lower_bound"] is False and rep["unpriced_requests"] == 0 and rep["unpriced_models"] == {}
    assert [r["lower_bound"] for r in rep["rows"]] == [False]


def test_a_daily_limit_under_a_lower_bound_does_not_pass_silently(world, capsys):
    _write(world["tx"] / "s5.jsonl", [
        _line("u1", "2026-10-01T19:40:00.000Z", world["beta"], "s5", model="claude-mystery-9", inp=5 * M),
        _line("u2", "2026-10-01T19:41:00.000Z", world["beta"], "s5", model="claude-mystery-9", out=M)])
    code, out = _run(capsys, "--dir", world["beta"], "--today")
    assert code == 0   # the priced part is under the limit: the exit code stays, the line says what is missing
    assert ("daily limit projects/my/beta: at least $20.25 today of about $100.00 (20% or more); 2 requests "
            "unpriced, so this is a lower bound and the limit may be passed") in out
    row = next(line for line in out.splitlines() if line.startswith("2026-10-01"))
    assert row.split()[-1] == ">=20.25" and row.split()[2] == "3"   # the requests and their tokens still count
    assert "lower bound: at least $20.25; 2 requests unpriced in this window" in out
    assert "not priced: claude-mystery-9 (2 requests; tokens counted)" in out
    rep = json.loads(_run(capsys, "--dir", world["beta"], "--today", "--json")[1])
    assert rep["lower_bound"] is True and rep["unpriced_requests"] == 2
    assert rep["budgets"] == [{"project": "projects/my/beta", "daily_budget_usd": 100.0,
                               "today_usd": pytest.approx(20.25), "over": False, "today_unpriced_requests": 2,
                               "lower_bound": True}]
    assert rep["rows"][0]["usd"] == pytest.approx(20.25) and rep["rows"][0]["unpriced_requests"] == 2


def test_an_over_limit_lower_bound_is_still_over(world, capsys):
    _write(world["tx"] / "s6.jsonl", [
        _line("u3", "2026-10-01T19:45:00.000Z", world["alpha"], "s6", model="claude-mystery-9", inp=M)])
    code, out = _run(capsys, "--dir", world["alpha"], "--today")
    assert code == A.OVER
    assert ("daily limit projects/my/alpha: at least $10.02 today of about $10.00 (100% or more), OVER; 1 request "
            "unpriced, so this is a lower bound") in out
    assert "the limit may be passed" not in out


def test_figures_without_unpriced_requests_read_as_before(world, capsys):
    code, out = _run(capsys, "--dir", world["beta"], "--today")
    assert code == 0 and "$20.25 today of about $100.00 (20%)" in out
    assert "at least" not in out and ">=" not in out and "lower bound" not in out and "not priced" not in out


def test_a_day_total_is_a_lower_bound_when_a_row_is(world, capsys):
    code, out = _run(capsys, "--since", "2026-10-01")
    lines = out.splitlines()
    loose = next(line for line in lines if "(unattributed)" in line)
    assert loose.split()[-1] == ">=0.00"
    assert next(line for line in lines if "projects/my/alpha" in line).split()[-1] == "10.02"   # priced in full
    assert "lower bound: at least $30.27; 1 request unpriced in this window (>= marks" in out


def test_detail_marks_a_worker_whose_spend_is_a_lower_bound(crew, capsys):
    tx, alpha = crew["tx"], crew["alpha"]
    _write(tx / MAIN / "subagents" / "agent-c3.jsonl", [
        _turn("x1", "2026-10-01T18:44:00.000Z", alpha, model="claude-sonnet-5-5", agent="c3", side=True),
        _turn("x2", "2026-10-01T18:45:00.000Z", alpha, model="claude-mystery-9", agent="c3", side=True)])
    _write(tx / MAIN / "subagents" / "agent-d4.jsonl", [
        _turn("x3", "2026-10-01T18:48:00.000Z", alpha, model="claude-mystery-9", agent="d4", side=True)])
    workers = {w["agent"]: w for w in json.loads(
        _run(capsys, "--dir", alpha, *WINDOW, "--json", "--detail")[1])["detail"]["workers"]}
    assert (workers["a1"]["usd"], workers["a1"]["unpriced_requests"], workers["a1"]["lower_bound"]) == (
        pytest.approx(20.00), 0, False)
    assert (workers["c3"]["usd"], workers["c3"]["unpriced_requests"], workers["c3"]["lower_bound"]) == (
        pytest.approx(10.00), 1, True)
    assert (workers["d4"]["usd"], workers["d4"]["unpriced_requests"], workers["d4"]["lower_bound"]) == (None, 1, True)
    out = _run(capsys, "--dir", alpha, *WINDOW, "--detail")[1]
    assert "claude-sonnet-5-5  effort -  at least $10.00; 1 request unpriced" in out
    assert "claude-mystery-9  effort -  n/a" in out
    assert "claude-sonnet-5-5  effort max  $20.00" in out
