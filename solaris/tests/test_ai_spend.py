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
                                            "today_usd": pytest.approx(10.02), "over": True}
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
    assert total.split()[-1] == "30.27"   # 10.02 + 20.25 + 0 unpriced
    assert not any(line.startswith("2026-09-30") for line in lines)
    assert "not priced: claude-mystery-9 (1 requests; tokens counted)" in out


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
