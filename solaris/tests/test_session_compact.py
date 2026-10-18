# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""solaris.tools.session_compact: measurement, settings, gates, and the OpenCode 2 API contract (a local fake)."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from solaris.tools import interactions as I
from solaris.tools import session_compact as S

SID = "ses_main"
NOW = 1_800_000_000.0
STATE = ("Status line kept for size.\n" * 20)
SESSION_CHECKPOINT = "# Session Checkpoint\n\n## Now\n- Ship it.\n\n## Next Actions\n1. Run the suite.\n" + STATE
MANIFEST = json.dumps({"project": {"name": "demo"}, "framework_version": "0.45.0"})


class Server:
    """A fake OpenCode 2 service: sessions, active map, messages, inbox, and the compact route."""

    def __init__(self):
        self.password = "pw"
        self.version = "2.0.29"
        self.sessions = {SID: {"id": SID}, "ses_kid": {"id": "ses_kid", "parentID": SID},
                         "ses_grand": {"id": "ses_grand", "parentID": "ses_kid"}, "ses_other": {"id": "ses_other"}}
        self.active = {SID: {"type": "running"}}
        self.assistant = [{"id": "m3", "time": {"created": 3}},  # still streaming: no tokens yet
                          {"id": "m2", "time": {"created": (NOW - 60) * 1000},
                           "tokens": {"input": 2, "output": 300, "cache": {"read": 590_000, "write": 9_998}}}]
        self.compactions: list = []
        self.inbox: list = []
        self.posts: list = []
        self.queries: list = []
        self.bad_auth = 0
        self.hits = 0
        self.pid = os.getpid()
        self.slow = 0.0          # seconds every message-list reply waits
        self.lose_reply = False  # queue the compaction, then answer 500

    def handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def reply(self, data, code=200):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                want = "Basic " + base64.b64encode(f"opencode:{srv.password}".encode()).decode()
                if self.headers.get("Authorization") != want:
                    srv.bad_auth += 1
                    self.reply({"_tag": "Unauthorized"}, 401)
                    return False
                return True

            def do_GET(self):
                srv.hits += 1
                if not self.authorized():
                    return
                url = urllib.parse.urlparse(self.path)
                query = dict(urllib.parse.parse_qsl(url.query))
                parts = url.path.split("/")
                if url.path == "/api/info":
                    return self.reply({"version": srv.version, "pid": srv.pid})
                if url.path == "/api/moved":
                    self.send_response(302)
                    self.send_header("Location", "http://example.com/")
                    self.end_headers()
                    return
                if url.path == "/api/session/active":
                    return self.reply({"data": srv.active})
                if url.path.endswith("/message"):
                    time.sleep(srv.slow)
                    srv.queries.append(query)
                    items = srv.assistant if query.get("type") == "assistant" else srv.compactions
                    return self.reply({"data": items[:int(query["limit"])]})
                if url.path.endswith("/inbox"):
                    return self.reply({"data": srv.inbox})
                if len(parts) == 4 and parts[3] in srv.sessions:
                    return self.reply({"data": srv.sessions[parts[3]]})
                self.reply({"_tag": "NotFound"}, 404)

            def do_POST(self):
                srv.hits += 1
                if not self.authorized():
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                srv.posts.append((self.path, body))
                item = {"id": "msg_c", "sessionID": SID, "type": "compaction", "payload": {},
                        "delivery": body.get("delivery"), "time": {"created": 9}}
                srv.inbox.append(item)
                if srv.lose_reply:
                    return self.reply({"_tag": "UnknownError"}, 500)
                self.reply({"data": item})

        return H


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A Solaris root in tmp_path, a demo project, and a fake OpenCode 2 service recorded in service.json."""
    fake = Server()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    root = tmp_path / "solaris"
    (root / ".memory").mkdir(parents=True)
    pack = root / "projects" / "my" / "demo" / "aipack"
    (pack / ".memory").mkdir(parents=True)
    (pack / "manifest.json").write_text(MANIFEST, encoding="utf-8")
    state = tmp_path / "state" / "opencode"
    state.mkdir(parents=True)
    (state / "service.json").write_text(json.dumps({"url": f"http://127.0.0.1:{httpd.server_port}",
                                                    "pid": fake.pid, "password": fake.password}), encoding="utf-8")
    monkeypatch.setattr(S, "REPO_ROOT", root)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv(S.SESSION_ENV, SID)
    for var in ("CLAUDECODE", "OPENCODE_SERVER_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(root)
    try:
        yield root, fake
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def run(capsys, *argv):
    code = S.main([str(a) for a in argv])
    out = capsys.readouterr().out
    try:
        return code, json.loads(out)
    except ValueError:
        return code, out


def write_checkpoint(path: Path, text: str, age: float = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    t = os.path.getmtime(path) - age
    os.utime(path, (t, t))


@pytest.mark.parametrize("value,expected", [
    (500000, 500_000), ("500k", 500_000), ("1M", 1_000_000), ("1.5m", 1_500_000), ("250_000", 250_000),
    (" 750K ", 750_000), (2.5e5, 250_000), ("300 k", 300_000),
])
def test_parse_tokens(value, expected):
    assert S.parse_tokens(value) == expected


@pytest.mark.parametrize("value", [True, 0, -5, "abc", "5g", "", "0k", float("inf"), "nan", None, "1e5"])
def test_parse_tokens_rejects(value):
    with pytest.raises((ValueError, TypeError)):
        S.parse_tokens(value)


def test_settings_default_and_project_files_win_key_by_key(env):
    root, _ = env
    pack = root / "projects" / "my" / "demo" / "aipack"
    assert S.load_config(pack) == {"enabled": True, "limit": 500_000, "min_interval_minutes": 30,
                                   "limit_source": "default", "error": None}
    (root / ".memory" / "config.json").write_text(json.dumps(
        {"selfcompact.limit": "800k", "selfcompact.min_interval_minutes": 10}), encoding="utf-8")
    (pack / "defaults.json").write_text(json.dumps({"selfcompact.limit": 300_000}), encoding="utf-8")
    (pack / ".memory" / "config.json").write_text(json.dumps({"selfcompact.enabled": "off"}), encoding="utf-8")
    cfg = S.load_config(pack)
    assert (cfg["enabled"], cfg["limit"], cfg["min_interval_minutes"], cfg["limit_source"]) == (False, 300_000, 10,
                                                                                               "config")
    assert S.load_config(None)["limit"] == 800_000   # no project: the framework file alone


@pytest.mark.parametrize("data,needle", [
    ({"selfcompact.limit": "lots"}, "selfcompact.limit"),
    ({"selfcompact.limit": 50_000}, "floor"),
    ({"selfcompact.enabled": "maybe"}, "selfcompact.enabled"),
    ({"selfcompact.min_interval_minutes": -1}, "selfcompact.min_interval_minutes"),
    ({"selfcompact.min_interval_minutes": True}, "selfcompact.min_interval_minutes"),
    ({"selfcompact.min_interval_minutes": [5]}, "selfcompact.min_interval_minutes"),
    ([1, 2], "not a JSON object"),
])
def test_bad_settings_fall_back_to_defaults(env, data, needle):
    root, _ = env
    (root / ".memory" / "config.json").write_text(json.dumps(data), encoding="utf-8")
    cfg = S.load_config(None)
    assert needle in cfg["error"]
    assert (cfg["enabled"], cfg["limit"], cfg["min_interval_minutes"]) == (True, 500_000, 30)


def test_unreadable_config_is_reported(env):
    root, _ = env
    (root / ".memory" / "config.json").write_text("{nope", encoding="utf-8")
    assert "cannot read" in S.load_config(None)["error"]


def test_check_measures_the_newest_finished_step(env, capsys):
    _, fake = env
    code, out = run(capsys, "check")
    assert code == 0 and out["applicable"] and out["due"] and out["context_tokens"] == 600_000
    assert out["checkpoint"] == f".memory/selfcompact/{SID}.md" and out["request"].endswith("session_compact request")
    assert fake.queries[0] == {"type": "assistant", "order": "desc", "limit": "5"}
    assert fake.bad_auth == 0
    code, line = run(capsys, "check", "--brief")
    assert "600k of 500k" in line and "DUE" in line and S.SKILL in line and "session_compact request" in line
    fake.assistant[1]["tokens"]["cache"]["read"] = 100_000
    code, line = run(capsys, "check", "--brief")
    assert code == 0 and "not due (under the limit)" in line


def test_check_with_a_project_names_its_context_file(env, capsys):
    code, out = run(capsys, "check", "--dir", "projects/my/demo")
    assert code == 0 and out["checkpoint"] == "projects/my/demo/aipack/.memory/context.md"
    assert out["request"] == "uv run -m solaris.tools.session_compact request --dir projects/my/demo"
    code, _ = run(capsys, "check", "--dir", "projects/my/none")
    assert code == 2


def test_assess_pending_stale_interval_and_disabled(env):
    _, fake = env
    client = S.connect()
    cfg = S.load_config(None)
    fake.inbox = [{"type": "prompt"}, {"type": "compaction"}]
    out = S.assess(client, SID, cfg, NOW)
    assert (out["due"], out["pending"]) == (False, True)
    fake.inbox = []
    fake.compactions = [{"id": "c1", "type": "compaction", "time": {"created": (NOW - 30) * 1000}}]
    assert "after the newest measured step" in S.assess(client, SID, cfg, NOW)["reason"]
    fake.compactions[0]["time"]["created"] = (NOW - 600) * 1000
    assert "minimum interval" in S.assess(client, SID, cfg, NOW)["reason"]
    assert S.assess(client, SID, {**cfg, "min_interval_minutes": 5}, NOW)["due"]
    off = S.assess(client, SID, {**cfg, "min_interval_minutes": 5, "enabled": False}, NOW)
    assert not off["due"] and "enabled is off" in off["reason"]
    fake.assistant = [{"id": "m9", "time": {"created": 1}}]
    assert S.assess(client, SID, cfg, NOW)["reason"].startswith("no finished model step")


@pytest.mark.parametrize("setup,needle", [
    (lambda m, f: m.delenv(S.SESSION_ENV), "no OPENCODE_SESSION_ID"),
    (lambda m, f: m.setenv(S.SESSION_ENV, "../escape"), "not a session id"),
    (lambda m, f: m.setenv("CLAUDECODE", "1"), "Claude Code"),
    (lambda m, f: m.setenv(S.SESSION_ENV, "ses_kid"), "subagent"),
    (lambda m, f: f.active.clear(), "not running"),
    (lambda m, f: m.setenv(S.SESSION_ENV, "ses_gone"), "HTTP 404"),
    (lambda m, f: setattr(f, "password", "other"), "authentication failed"),
    (lambda m, f: setattr(f, "version", "1.18.32"), "not OpenCode 2"),
    (lambda m, f: m.setenv("XDG_STATE_HOME", "/nonexistent-state"), "no OpenCode 2 background service"),
])
def test_not_applicable_outside_a_running_top_level_session(env, capsys, monkeypatch, setup, needle):
    _, fake = env
    setup(monkeypatch, fake)
    code, out = run(capsys, "check")
    assert code == 0 and out["applicable"] is False and needle in out["reason"]
    code, out = run(capsys, "request", "--force")
    assert code == 3 and out["requested"] is False and needle in out["reason"]
    assert fake.posts == []


def test_request_queues_after_the_turn(env, capsys):
    root, fake = env
    write_checkpoint(root / ".memory" / "selfcompact" / f"{SID}.md", SESSION_CHECKPOINT)
    code, out = run(capsys, "request")
    assert code == 0 and out["requested"] and out["delivery"] == "queue"
    assert fake.posts == [(f"/api/session/{SID}/compact", {"delivery": "queue"})]
    assert "read .memory/selfcompact" in out["next"] and out["forced"] is False
    code, out = run(capsys, "request")   # the queued item now sits in the inbox
    assert code == 0 and out["pending"] and not out["requested"] and len(fake.posts) == 1


def test_request_now_steers(env, capsys):
    root, fake = env
    write_checkpoint(root / ".memory" / "selfcompact" / f"{SID}.md", SESSION_CHECKPOINT)
    code, out = run(capsys, "request", "--now")
    assert code == 0 and fake.posts[-1][1] == {"delivery": "steer"} and "next step boundary" in out["compacts"]


def test_request_gates(env, capsys):
    root, fake = env
    checkpoint = root / ".memory" / "selfcompact" / f"{SID}.md"
    fake.assistant[1]["tokens"]["cache"]["read"] = 1_000
    code, out = run(capsys, "request")
    assert code == 5 and out["reason"] == "under the limit"
    code, out = run(capsys, "request", "--force")
    assert code == 4 and "write it first" in out["refused"][0]
    write_checkpoint(checkpoint, SESSION_CHECKPOINT)
    code, out = run(capsys, "request", "--force", "--dry-run")
    assert code == 0 and out["dry_run"] and fake.posts == []
    code, out = run(capsys, "request", "--force")
    assert code == 0 and out["forced"] is True and len(fake.posts) == 1


def test_checkpoint_gate_for_sessions(env):
    root, _ = env
    path = root / ".memory" / "selfcompact" / f"{SID}.md"
    write_checkpoint(path, SESSION_CHECKPOINT)
    assert S.checkpoint_problems(path, False, os.path.getmtime(path)) == []
    assert "minutes ago" in S.checkpoint_problems(path, False, os.path.getmtime(path) + 3600)[0]
    write_checkpoint(path, "## Now\n## Next Actions\n")
    assert S.checkpoint_problems(path, False, os.path.getmtime(path)) == [
        "the checkpoint holds under 400 bytes of state"]
    write_checkpoint(path, SESSION_CHECKPOINT.replace("## Next Actions", "## Later"))
    assert S.checkpoint_problems(path, False, os.path.getmtime(path)) == ["the checkpoint lacks '## Next Actions'"]


def test_checkpoint_gate_for_a_project_counts_its_session_context(env):
    root, _ = env
    path = root / "projects" / "my" / "demo" / "aipack" / ".memory" / "context.md"
    template = "# Demo - Context\n\n## How to Use This File\n\n" + STATE + "\n## Session Context\n\n"
    write_checkpoint(path, template + "<!-- rewritten in place at each save point -->\n" + "x" * 50)
    assert "under 400 bytes" in S.checkpoint_problems(path, True, os.path.getmtime(path))[0]
    write_checkpoint(path, template + "### Now\n" + STATE)
    assert S.checkpoint_problems(path, True, os.path.getmtime(path)) == []
    write_checkpoint(path, "## Where Things Stand\n" + STATE)   # older layouts carry no Session Context heading
    assert S.checkpoint_problems(path, True, os.path.getmtime(path)) == []


def test_request_with_a_project_uses_its_context_file(env, capsys):
    root, fake = env
    context = root / "projects" / "my" / "demo" / "aipack" / ".memory" / "context.md"
    write_checkpoint(context, "## Session Context\n" + STATE, age=21 * 60)
    code, out = run(capsys, "request", "--dir", "projects/my/demo")
    assert code == 4 and "21 minutes ago" in out["refused"][0]
    write_checkpoint(context, "## Session Context\n" + STATE)
    code, out = run(capsys, "request", "--dir", "projects/my/demo")
    assert code == 0 and out["checkpoint"] == "projects/my/demo/aipack/.memory/context.md"
    assert not (root / ".memory" / "selfcompact").exists()   # project work writes nothing in the framework


def test_running_subagents_must_be_named_and_block_now(env, capsys):
    root, fake = env
    fake.active.update({"ses_grand": {"type": "running"}, "ses_other": {"type": "running"}})
    path = root / ".memory" / "selfcompact" / f"{SID}.md"
    write_checkpoint(path, SESSION_CHECKPOINT)
    code, out = run(capsys, "check")
    assert out["due"] and "blocked" not in out and "ses_grand" in out["warnings"][0]
    code, out = run(capsys, "request")
    assert code == 4 and "ses_grand" in out["refused"][0] and "ses_other" not in json.dumps(out["refused"])
    write_checkpoint(path, SESSION_CHECKPOINT + "\n## Running Jobs\n- ses_grand: the import; read its result\n")
    code, out = run(capsys, "request", "--now")
    assert code == 4 and "--now waits" in out["refused"][0]
    code, out = run(capsys, "request")
    assert code == 0 and out["requested"] and "ses_grand" in out["warnings"][0]


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=60,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})


def test_git_gate(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("one\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "init")
    assert S.git_problems(repo) == []
    (repo / "a.txt").write_text("two\n")
    assert S.git_problems(repo) == []   # unstaged edits may be another session's
    git(repo, "add", "a.txt")
    assert S.git_problems(repo) == ["staged changes wait for a commit"]
    git(repo, "reset", "-q")
    (repo / ".git" / "MERGE_HEAD").write_text("0" * 40)
    (repo / ".git" / "index.lock").write_text("")
    assert S.git_problems(repo) == ["a git command holds index.lock", "a merge is in progress"]
    assert S.git_problems(tmp_path / "missing") == []


def test_git_state_warns_a_queued_request_and_refuses_now(env, capsys):
    root, fake = env
    project = root / "projects" / "my" / "demo"
    source = project / "source"
    source.mkdir()
    git(source, "init", "-q")
    (source / "a.txt").write_text("x\n")
    git(source, "add", "a.txt")
    context = project / "aipack" / ".memory" / "context.md"
    write_checkpoint(context, "## Session Context\n" + STATE)
    code, out = run(capsys, "request", "--dir", project, "--dry-run")
    assert code == 0 and "staged changes" in out["warnings"][0] and "projects/my/demo/source" in out["warnings"][0]
    code, out = run(capsys, "request", "--dir", project, "--now", "--dry-run")
    assert code == 4 and "staged changes" in out["refused"][0]
    (source / ".git" / "index.lock").write_text("")
    code, out = run(capsys, "request", "--dir", project, "--dry-run")
    assert code == 4 and "index.lock" in out["refused"][0]


def test_set_limit_applies_to_this_session_only(env, capsys):
    root, fake = env
    fake.assistant[1]["tokens"]["cache"]["read"] = 390_000   # 400k in all
    code, out = run(capsys, "check")
    assert (out["due"], out["limit"], out["limit_source"]) == (False, 500_000, "default")
    code, out = run(capsys, "set-limit", "333k")
    assert code == 0 and (out["limit"], out["limit_source"]) == (333_000, "session")
    code, out = run(capsys, "check")
    assert (out["due"], out["limit"], out["limit_source"]) == (True, 333_000, "session")
    assert S.session_config(None, "ses_other")["limit"] == 500_000
    code, out = run(capsys, "set-limit", "50k")
    assert code == 2 and "floor" in out["reason"]
    code, out = run(capsys, "set-limit", "--clear")
    assert code == 0 and (out["limit"], out["limit_source"]) == (500_000, "default")
    S.override_path(SID).write_text("{bad", encoding="utf-8")
    assert "ignored" in S.session_config(None, SID)["error"]
    assert S.main(["set-limit"]) == 2 and S.main(["set-limit", "300k", "--clear"]) == 2


def test_due_line_is_quiet_unless_due_and_never_raises(env, monkeypatch):
    _, fake = env
    assert S.SKILL in S.due_line()
    fake.assistant[1]["tokens"]["cache"]["read"] = 1_000
    assert S.due_line() is None
    monkeypatch.delenv(S.SESSION_ENV)
    assert S.due_line() is None
    monkeypatch.setenv(S.SESSION_ENV, SID)
    monkeypatch.setattr(S, "report", lambda *a, **k: 1 / 0)
    assert S.due_line() is None


def test_interactions_add_prints_the_due_line(env, monkeypatch, capsys):
    root, fake = env
    monkeypatch.setattr(I, "REPO_ROOT", root)
    monkeypatch.setenv("SOLARIS_MACHINE", "box1")
    base = ["add", "--project", "demo", "--prompt", "p", "--request", "r", "--outcome", "o"]
    assert I.main(base + ["--dir", str(root / "projects" / "my" / "demo")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("logged ") and "DUE" in out[1] and "--dir projects/my/demo" in out[1]
    fake.assistant[1]["tokens"]["cache"]["read"] = 1_000
    assert I.main(base) == 0
    assert len(capsys.readouterr().out.splitlines()) == 1
    monkeypatch.setattr(S, "due_line", lambda *_a: 1 / 0)
    assert I.main(base) == 0


def test_a_task_linked_to_a_project_keeps_its_checkpoint_in_the_framework(env, monkeypatch, capsys):
    root, _ = env
    monkeypatch.setattr(I, "REPO_ROOT", root)
    monkeypatch.setenv("SOLARIS_MACHINE", "box1")
    project = str(root / "projects" / "my" / "demo")
    assert S.is_project_session(project, "demo") and S.is_project_session(project, " Demo ")
    assert not S.is_project_session(project, "2026-10-18-printer-setup") and not S.is_project_session(None, "demo")
    argv = ["add", "--project", "2026-10-18-printer-setup", "--prompt", "p", "--request", "r", "--outcome", "o",
            "--dir", project]
    assert I.main(argv) == 0
    line = capsys.readouterr().out.splitlines()[1]
    assert f".memory/selfcompact/{SID}.md" in line and "--dir" not in line and "context.md" not in line


def test_a_project_request_never_touches_framework_files(env, capsys):
    root, _ = env
    old = root / ".memory" / "selfcompact" / "ses_old.md"
    write_checkpoint(old, "x", age=(S.KEEP_DAYS + 1) * 86400)
    write_checkpoint(root / "projects" / "my" / "demo" / "aipack" / ".memory" / "context.md",
                     "## Session Context\n" + STATE)
    code, out = run(capsys, "request", "--dir", "projects/my/demo")
    assert code == 0 and out["requested"] and old.exists()
    S.prune(old.parent, ".md", time.time())   # an orchestrator request does prune it
    assert not old.exists()


def test_the_solaris_checkout_counts_only_without_a_project(env, capsys):
    root, _ = env
    git(root, "init", "-q")
    (root / "f.txt").write_text("x\n")
    git(root, "add", "f.txt")
    write_checkpoint(root / ".memory" / "selfcompact" / f"{SID}.md", SESSION_CHECKPOINT)
    write_checkpoint(root / "projects" / "my" / "demo" / "aipack" / ".memory" / "context.md",
                     "## Session Context\n" + STATE)
    code, out = run(capsys, "request", "--dry-run")
    assert code == 0 and "the Solaris checkout" in out["warnings"][0]
    code, out = run(capsys, "request", "--dir", "projects/my/demo", "--now", "--dry-run")
    assert code == 0 and "warnings" not in out


def test_due_but_blocked_says_to_wait_not_to_delete(env, capsys):
    root, _ = env
    source = root / "projects" / "my" / "demo" / "source"
    source.mkdir()
    git(source, "init", "-q")
    (source / ".git" / "index.lock").write_text("")
    code, line = run(capsys, "check", "--brief", "--dir", "projects/my/demo")
    assert code == 0 and "DUE but blocked" in line and "never delete the lock" in line
    assert "Write no checkpoint yet" in line


def test_a_disabled_check_makes_no_api_calls(env):
    root, fake = env
    (root / ".memory" / "config.json").write_text(json.dumps({"selfcompact.enabled": False}), encoding="utf-8")
    assert S.due_line() is None and fake.hits == 0


def test_a_slow_server_cannot_hold_up_interactions_add(env, monkeypatch):
    _, fake = env
    monkeypatch.setattr(S, "HINT_TIMEOUT", 0.3)
    monkeypatch.setattr(S, "HINT_BUDGET", 0.6)
    fake.slow = 1.0
    start = time.monotonic()
    assert S.due_line() is None
    assert time.monotonic() - start < 1.0


def test_odd_api_shapes_and_crashes_do_not_break_check(env, capsys, monkeypatch):
    _, fake = env
    fake.assistant = [{"id": "a", "time": "yesterday", "tokens": ["x"]},
                      {"id": "b", "time": None, "tokens": {"input": "many", "cache": []}},
                      {"id": "c", "time": "now", "tokens": {"input": 600_000}}]
    fake.compactions = [{"id": "c1", "time": [1], "status": "failed"}]
    code, out = run(capsys, "check")
    assert code == 0 and out["context_tokens"] == 600_000 and out["due"]
    monkeypatch.setattr(S, "report", lambda *a, **k: {}.missing)
    code, line = run(capsys, "check", "--brief")
    assert code == 0 and line.startswith("self-compaction: check failed")


def test_a_failed_compaction_is_named(env):
    _, fake = env
    fake.compactions = [{"id": "c1", "type": "compaction", "status": "failed", "time": {"created": NOW * 1000}}]
    assert "(it failed)" in S.assess(S.connect(), SID, S.load_config(None), NOW)["reason"]


def test_a_lost_reply_still_reports_the_queued_compaction(env, capsys):
    root, fake = env
    write_checkpoint(root / ".memory" / "selfcompact" / f"{SID}.md", SESSION_CHECKPOINT)
    fake.lose_reply = True
    code, out = run(capsys, "request")
    assert code == 0 and out["requested"] and out["inbox_id"] == "msg_c" and "read" in out["next"]
    code, out = run(capsys, "request")
    assert code == 0 and out["pending"] and out["checkpoint"].endswith(f"{SID}.md") and "read" in out["next"]


def test_a_stale_service_record_gets_no_password(env, capsys, tmp_path):
    root, fake = env
    record = tmp_path / "state" / "opencode" / "service.json"
    data = json.loads(record.read_text())
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=30)
    record.write_text(json.dumps({**data, "pid": dead.pid}))
    code, out = run(capsys, "check")
    assert out["applicable"] is False and "stale" in out["reason"] and fake.hits == 0
    fake.pid = 1   # the address answers, but as another process than the recorded one
    record.write_text(json.dumps(data))
    code, out = run(capsys, "check")
    assert out["applicable"] is False and "not the recorded service" in out["reason"]


def test_server_url_with_a_password_from_the_environment(env, capsys, monkeypatch, tmp_path):
    _, fake = env
    url = json.loads((tmp_path / "state" / "opencode" / "service.json").read_text())["url"]
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "nothing"))
    code, out = run(capsys, "check", "--server-url", url)
    assert out["applicable"] is False and "authentication failed" in out["reason"]
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", fake.password)
    code, out = run(capsys, "check", "--server-url", url)
    assert out["applicable"] and out["due"]
    code, out = run(capsys, "check", "--server-url", "http://example.com:80")
    assert out["applicable"] is False and "not a local" in out["reason"]


def test_redirects_are_refused(env):
    with pytest.raises(S.ApiError, match="redirected"):
        S.connect().call("/api/moved")


def test_session_context_heading_may_carry_more_text(env):
    root, _ = env
    path = root / "projects" / "my" / "demo" / "aipack" / ".memory" / "context.md"
    write_checkpoint(path, "## How to Use This File\n" + STATE + "\n## Session Context (2026-10-18)\n\nshort\n")
    assert "under 400 bytes" in S.checkpoint_problems(path, True, os.path.getmtime(path))[0]


def test_local_url_only():
    assert S.local_url("http://127.0.0.1:4096/") == "http://127.0.0.1:4096"
    assert S.local_url("http://localhost:49374") == "http://localhost:49374"
    assert S.local_url("http://[::1]:4096") == "http://[::1]:4096"
    for bad in ("http://example.com:4096", "http://127.0.0.1", "ftp://127.0.0.1:1", "http://u:p@127.0.0.1:1",
                "http://127.0.0.1:1/api", "http://10.0.0.1:4096", ""):
        with pytest.raises(ValueError):
            S.local_url(bad)


def test_prune_keeps_recent_files(tmp_path):
    old, new = tmp_path / "old.md", tmp_path / "new.md"
    old.write_text("x")
    new.write_text("y")
    t = os.path.getmtime(old) - (S.KEEP_DAYS + 1) * 86400
    os.utime(old, (t, t))
    S.prune(tmp_path, ".md", os.path.getmtime(new))
    assert not old.exists() and new.exists()
    S.prune(tmp_path / "missing", ".md", 0)
