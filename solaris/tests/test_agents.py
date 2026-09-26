# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.agents (renamable primary persona + role briefs) and the revs hooks behind it."""

from __future__ import annotations

import json

import pytest

from solaris.tools import agents as A
from solaris.tools import revs as R

TEMPLATE_DIR = R.TEMPLATE_DIR


def _project(tmp_path, primary=None):
    """A pack rendered from the real templates (so placeholder handling is tested for real)."""
    proj = tmp_path / "proj"
    (proj / "ai").mkdir(parents=True)
    manifest = {
        "project": {"name": "Todo", "slug": "todo", "type": "python-cli", "mode": "local",
                    "description": "A todo app."},
        "framework_version": "0.34.0", "plugins": [], "revisions": {}, "created": "2026-09-26",
    }
    if primary:
        manifest["agents"] = {"primary": primary}
    (proj / "ai" / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    R.fast_forward(proj, template_dir=TEMPLATE_DIR, plugins_dir=tmp_path / "plugins")
    role = primary or "engineer"
    instr = (TEMPLATE_DIR / "ai" / "engineer.instructions.md").read_text(encoding="utf-8")
    (proj / "ai" / f"{role}.instructions.md").write_text(
        instr.replace("{{NAME}}", "Todo").replace("{{PRIMARY}}", role), encoding="utf-8")
    return proj


def _brief(proj, name, desc="Reviews things before they count.", tier="high", access="read-only",
           body="**Owns:** review.\n"):
    d = proj / "ai" / "agents"
    d.mkdir(exist_ok=True)
    fm = f"---\ndescription: {desc}\n"
    if tier:
        fm += f"tier: {tier}\n"
    if access:
        fm += f"access: {access}\n"
    (d / f"{name}.agent.md").write_text(fm + "---\n\n" + body, encoding="utf-8")


def test_primary_role_default_and_validation():
    assert R.primary_role({}) == "engineer"
    assert R.primary_role({"agents": {}}) == "engineer"
    assert R.primary_role({"agents": {"primary": "master"}}) == "master"
    with pytest.raises(ValueError):
        R.primary_role({"agents": []})
    with pytest.raises(ValueError):
        R.primary_role({"agents": {"primary": "Bad Name"}})
    with pytest.raises(ValueError):
        R.primary_role({"agents": {"roles": ["x"]}})
    subs = R._placeholder_subs({"agents": {"primary": "ml-lead"}}, R.REPO_ROOT)
    assert (subs["{{PRIMARY}}"], subs["{{PRIMARY_TITLE}}"]) == ("ml-lead", "Ml Lead")


def test_default_pack_still_renders_the_engineer(tmp_path):
    proj = _project(tmp_path)
    persona = proj / "ai" / "engineer.agent.md"
    assert persona.exists()
    assert "# Todo - Engineer Agent" in persona.read_text(encoding="utf-8")
    assert "ai/engineer.agent.md" in (proj / "AGENTS.md").read_text(encoding="utf-8")
    assert "{{" not in (proj / "ai" / "README.md").read_text(encoding="utf-8")
    assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, tmp_path / "plugins")} == {"in-sync"}


def test_materialized_map_and_render_follow_the_primary(tmp_path):
    proj = _project(tmp_path, primary="master")
    rels = {rel for _, _, rel in R.materialized_map(proj, TEMPLATE_DIR, tmp_path / "plugins")}
    assert "ai/master.agent.md" in rels and "ai/engineer.agent.md" not in rels
    for rel in ("AGENTS.md", "ai/master.agent.md", "ai/README.md"):
        text = (proj / rel).read_text(encoding="utf-8")
        for stale in ("engineer.agent.md", "engineer.instructions.md", "Engineer Agent", "engineer persona"):
            assert stale not in text, f"{rel} still says {stale}"
    assert "# Todo - Master Agent" in (proj / "ai" / "master.agent.md").read_text(encoding="utf-8")
    assert "ai/master.instructions.md" in (proj / "AGENTS.md").read_text(encoding="utf-8")


def test_agents_block_lists_primary_and_roles(tmp_path):
    proj = _project(tmp_path)
    assert R._agents_block({}, proj).startswith("- `engineer`")
    _brief(proj, "reviewer", desc="Adversarial review before results count.")
    _brief(proj, "bad", desc="")            # invalid: flagged in the listing, never fatal for a render
    block = R._agents_block({}, proj)
    assert "- `reviewer` - Adversarial review before results count. (high tier, read-only;" in block
    assert "bad.agent.md" in block and "INVALID" in block
    R.fast_forward(proj, TEMPLATE_DIR, tmp_path / "plugins")
    assert "reviewer" in (proj / "ai" / "README.md").read_text(encoding="utf-8")


def test_load_role_validation(tmp_path):
    proj = _project(tmp_path)
    d = proj / "ai" / "agents"
    d.mkdir()
    cases = {
        "Bad.agent.md": ("---\ndescription: x\n---\nbody\n", "must match"),
        "engineer.agent.md": ("---\ndescription: x\n---\nbody\n", "primary persona"),
        "nodesc.agent.md": ("---\ntier: mid\n---\nbody\n", "description"),
        "unknown.agent.md": ("---\ndescription: x\nmode: primary\n---\nbody\n", "unknown frontmatter key"),
        "tier.agent.md": ("---\ndescription: x\ntier: huge\n---\nbody\n", "tier must be"),
        "access.agent.md": ("---\ndescription: x\naccess: rw\n---\nbody\n", "access must be"),
        "empty.agent.md": ("---\ndescription: x\n---\n\n", "empty"),
        "nofm.agent.md": ("# just a heading\n", "missing frontmatter"),
        "open.agent.md": ("---\ndescription: x\nbody\n", "unterminated"),
        "nested.agent.md": ("---\ndescription: x\nmodel:\n  claude: opus\n---\nbody\n", "top-level"),
    }
    for fname, (text, needle) in cases.items():
        (d / fname).write_text(text, encoding="utf-8")
        with pytest.raises(ValueError, match=needle):
            A.load_role(d / fname, "engineer")
    (d / "ok.agent.md").write_text('---\n# a comment\ndescription: "Quoted: yes"\ntier: mid\n---\n\n**Owns:** x\n',
                                   encoding="utf-8")
    r = A.load_role(d / "ok.agent.md", "engineer")
    assert (r.name, r.description, r.tier, r.access) == ("ok", "Quoted: yes", "mid", "full")
    assert r.body.startswith("**Owns:**")


def test_check_cli(tmp_path, capsys):
    proj = _project(tmp_path)
    assert A.main(["--dir", str(proj)]) == 0
    assert "single persona (engineer)" in capsys.readouterr().out
    _brief(proj, "reader", desc="Reads external material.", tier="mid", access="read-only")
    (proj / "ai" / "agents" / "notes.md").write_text("scratch\n", encoding="utf-8")
    assert A.main(["--dir", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "1 role brief(s)" in out and "reader" in out and "notes.md" in out
    _brief(proj, "broken", desc="")
    assert A.main(["--dir", str(proj)]) == 1
    assert "broken.agent.md" in capsys.readouterr().out
    assert A.main(["--dir", str(tmp_path / "nope")]) == 1       # clean error, no traceback
    assert "not found" in capsys.readouterr().out


def test_rename_primary_round_trip(tmp_path):
    proj = _project(tmp_path)
    _brief(proj, "reviewer")
    with pytest.raises(ValueError, match="must match"):
        A.rename_primary(proj, "Master")
    with pytest.raises(ValueError, match="role brief"):
        A.rename_primary(proj, "reviewer")
    log, warnings = A.rename_primary(proj, "master")
    assert not warnings
    assert any("moved ai/engineer.agent.md -> ai/master.agent.md" in line for line in log)
    ai = proj / "ai"
    assert (ai / "master.agent.md").exists() and not (ai / "engineer.agent.md").exists()
    assert (ai / "master.instructions.md").exists() and not (ai / "engineer.instructions.md").exists()
    man = json.loads((ai / "manifest.json").read_text(encoding="utf-8"))
    assert man["agents"]["primary"] == "master"
    assert "ai/master.agent.md" in man["revisions"] and "ai/engineer.agent.md" not in man["revisions"]
    assert "# Todo - Master Agent" in (ai / "master.agent.md").read_text(encoding="utf-8")
    assert "ai/master.agent.md" in (proj / "AGENTS.md").read_text(encoding="utf-8")
    instr = (ai / "master.instructions.md").read_text(encoding="utf-8")
    assert "master.agent.md" in instr and "engineer.agent.md" not in instr
    verdicts = {r["rel"]: r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, tmp_path / "plugins")}
    assert (verdicts["AGENTS.md"], verdicts["ai/master.agent.md"], verdicts["ai/README.md"]) == ("in-sync",) * 3
    assert A.rename_primary(proj, "master") == (["the primary persona is already 'master'; nothing to do"], [])
    A.rename_primary(proj, "engineer")
    assert (ai / "engineer.agent.md").exists() and (ai / "engineer.instructions.md").exists()
    assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, tmp_path / "plugins")} == {"in-sync"}


def test_rename_refuses_a_customized_persona(tmp_path):
    proj = _project(tmp_path)
    p = proj / "ai" / "engineer.agent.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nProject-specific tweak.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conflict"):
        A.rename_primary(proj, "master")
    assert p.exists() and not (proj / "ai" / "master.agent.md").exists()   # nothing moved


def test_rename_guards_every_file_that_carries_the_name(tmp_path):
    # a customized pack rule that mentions the primary would keep the old name after a re-render: refuse
    proj = _project(tmp_path)
    rule = proj / "ai" / "rules" / "subagents.rule.md"
    rule.write_text(rule.read_text(encoding="utf-8") + "\nlocal tweak\n", encoding="utf-8")
    with pytest.raises(ValueError, match="subagents.rule.md"):
        A.rename_primary(proj, "master")
    assert (proj / "ai" / "engineer.agent.md").exists()


def test_rename_without_a_revisions_map_still_rerenders(tmp_path):
    proj = _project(tmp_path)
    man_path = proj / "ai" / "manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    man.pop("revisions")
    man_path.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    log, warnings = A.rename_primary(proj, "master")
    assert not warnings
    assert "# Todo - Master Agent" in (proj / "ai" / "master.agent.md").read_text(encoding="utf-8")
    assert "ai/master.instructions.md" in (proj / "ai" / "rules" / "subagents.rule.md").read_text(encoding="utf-8")
    assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, tmp_path / "plugins")} == {"in-sync"}


def test_rename_warns_when_the_instructions_file_is_absent(tmp_path):
    proj = _project(tmp_path)
    (proj / "ai" / "engineer.instructions.md").unlink()
    log, warnings = A.rename_primary(proj, "master")
    assert warnings and "engineer.instructions.md was absent" in warnings[0]
    assert (proj / "ai" / "master.agent.md").exists()


def test_primary_role_rejects_empty_or_null_values():
    for bad in ("", None, 3):
        with pytest.raises(ValueError):
            R.primary_role({"agents": {"primary": bad}})


def test_cli_rejects_empty_rename_and_flags_stray_personas(tmp_path, capsys):
    proj = _project(tmp_path)
    assert A.main(["--dir", str(proj), "--rename-primary", ""]) == 1
    assert "must match" in capsys.readouterr().out
    (proj / "ai" / "other.agent.md").write_text("# stray\n", encoding="utf-8")
    assert A.main(["--dir", str(proj)]) == 1
    assert "stray persona" in capsys.readouterr().out


def test_brief_edge_cases(tmp_path):
    proj = _project(tmp_path)
    d = proj / "ai" / "agents"
    d.mkdir()
    (d / "crlf.agent.md").write_bytes(b"---\r\ndescription: Windows brief\r\ntier: mid\r\n---\r\n\r\n**Owns:** x\r\n")
    r = A.load_role(d / "crlf.agent.md", "engineer")
    assert (r.description, r.tier) == ("Windows brief", "mid")
    (d / "rule.agent.md").write_text("---\ndescription: Has a rule\n---\n\nabove\n\n---\n\nbelow\n", encoding="utf-8")
    assert "---" in A.load_role(d / "rule.agent.md", "engineer").body
    (d / "bom.agent.md").write_text("﻿---\ndescription: BOM brief\n---\n\nbody\n", encoding="utf-8")
    assert A.load_role(d / "bom.agent.md", "engineer").description == "BOM brief"
    (d / "esc.agent.md").write_text('---\ndescription: "Says \\"hi\\": ok"\n---\n\nbody\n', encoding="utf-8")
    assert A.load_role(d / "esc.agent.md", "engineer").description == 'Says "hi": ok'
    (d / "dir.agent.md").mkdir()
    with pytest.raises(ValueError, match="cannot read"):
        A.load_role(d / "dir.agent.md", "engineer")
    assert "INVALID" in R._agents_block({}, proj)   # a directory renders as an invalid brief, never a traceback


def test_revs_cli_reports_a_malformed_manifest_cleanly(tmp_path, capsys):
    proj = tmp_path / "p"
    (proj / "ai").mkdir(parents=True)
    (proj / "ai" / "manifest.json").write_text('{"agents": {"primary": ""}}', encoding="utf-8")
    assert R.main(["classify", "--dir", str(proj)]) == 1
    assert "agents.primary" in capsys.readouterr().out
    (proj / "ai" / "manifest.json").write_text("{not json", encoding="utf-8")
    assert R.main(["classify", "--dir", str(proj)]) == 1
    assert "not valid JSON" in capsys.readouterr().out
