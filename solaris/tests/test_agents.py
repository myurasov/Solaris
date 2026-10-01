# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.agents (renamable primary persona, role briefs beside it in the ai-pack, the one
shared <pack>/instructions.md, the renamable pack folder) and the revs hooks behind it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from solaris.tools import agents as A
from solaris.tools import revs as R

TEMPLATE_DIR = R.TEMPLATE_DIR
PACK_MANIFEST = {"project": {"name": "P", "slug": "p"}, "framework_version": "0.39.0"}


def _project(tmp_path, primary=None, pack="ai"):
    """A pack rendered from the real templates (so placeholder handling is tested for real), in a pack folder
    named ``pack`` (``ai`` is the pre-0.39 name, ``aipack`` the default for new projects)."""
    proj = tmp_path / "proj"
    (proj / pack).mkdir(parents=True)
    manifest = {
        "project": {"name": "Todo", "slug": "todo", "type": "python-cli", "mode": "local",
                    "description": "A todo app."},
        "framework_version": "0.37.0", "plugins": [], "revisions": {}, "created": "2026-09-27",
    }
    if primary:
        manifest["agents"] = {"primary": primary}
    (proj / pack / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    R.fast_forward(proj, template_dir=TEMPLATE_DIR, plugins_dir=tmp_path / "plugins")
    role = primary or "engineer"
    instr = (TEMPLATE_DIR / "ai" / "instructions.md").read_text(encoding="utf-8")
    (proj / pack / "instructions.md").write_text(
        instr.replace("{{NAME}}", "Todo").replace("{{PRIMARY}}", role).replace("{{PACK}}", pack),
        encoding="utf-8")
    shutil.copy(TEMPLATE_DIR / "CLAUDE.md", proj / "CLAUDE.md")
    return proj


def _legacy_keys(proj, pack):
    """Give a pack renamed by hand the ai/... revisions keys it had before (like a pre-0.39 manual rename)."""
    path = proj / pack / "manifest.json"
    man = json.loads(path.read_text(encoding="utf-8"))
    man["revisions"] = {("ai/" + k[len(pack) + 1:] if k.startswith(f"{pack}/") else k): v
                        for k, v in man["revisions"].items()}
    path.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    return man


def _git_init(proj, tmp_path, monkeypatch):
    """Make ``proj`` its own git work tree, with the user's git config and global excludes kept out."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    subprocess.run(["git", "init", "-q", str(proj)], check=True)


def _ignored(proj, rel):
    return subprocess.run(["git", "-C", str(proj), "check-ignore", "-q", rel]).returncode == 0


def _brief(proj, name, desc="Reviews things before they count.", tier="high", access="read-only",
           body="**Owns:** review.\n", pack="ai"):
    fm = f"---\ndescription: {desc}\n"
    if tier:
        fm += f"tier: {tier}\n"
    if access:
        fm += f"access: {access}\n"
    (proj / pack / f"{name}.agent.md").write_text(fm + "---\n\n" + body, encoding="utf-8")


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
    agents_md = (proj / "AGENTS.md").read_text(encoding="utf-8")
    assert "ai/engineer.agent.md" in agents_md and "ai/instructions.md" in agents_md
    assert "{{" not in (proj / "ai" / "README.md").read_text(encoding="utf-8")
    assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, tmp_path / "plugins")} == {"in-sync"}


def test_materialized_map_and_render_follow_the_primary(tmp_path):
    proj = _project(tmp_path, primary="master")
    rels = {rel for _, _, rel in R.materialized_map(proj, TEMPLATE_DIR, tmp_path / "plugins")}
    assert "ai/master.agent.md" in rels and "ai/engineer.agent.md" not in rels
    for rel in ("AGENTS.md", "ai/master.agent.md", "ai/README.md", "ai/rules/subagents.rule.md"):
        text = (proj / rel).read_text(encoding="utf-8")
        for stale in ("engineer.agent.md", "engineer.instructions.md", "master.instructions.md",
                      "Engineer Agent", "engineer persona"):
            assert stale not in text, f"{rel} still says {stale}"
    assert "# Todo - Master Agent" in (proj / "ai" / "master.agent.md").read_text(encoding="utf-8")
    assert "ai/instructions.md" in (proj / "AGENTS.md").read_text(encoding="utf-8")


def test_agents_block_lists_primary_and_roles(tmp_path):
    proj = _project(tmp_path)
    assert R._agents_block({}, proj).startswith("- `engineer`")
    _brief(proj, "reviewer", desc="Adversarial review before results count.")
    _brief(proj, "bad", desc="")            # invalid: flagged in the listing, never fatal for a render
    block = R._agents_block({}, proj)
    assert ("- `reviewer` - Adversarial review before results count. (high tier, read-only; "
            "[`reviewer.agent.md`](reviewer.agent.md))") in block
    assert "bad.agent.md" in block and "INVALID" in block
    assert "instructions.md" not in block           # the shared store is described in prose, not per role
    R.fast_forward(proj, TEMPLATE_DIR, tmp_path / "plugins")
    assert "reviewer" in (proj / "ai" / "README.md").read_text(encoding="utf-8")


def test_load_role_validation(tmp_path):
    proj = _project(tmp_path)
    d = proj / "ai"
    cases = {
        "Bad.agent.md": ("---\ndescription: x\n---\nbody\n", "must match"),
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
    with pytest.raises(ValueError, match="primary persona"):
        A.load_role(d / "engineer.agent.md", "engineer")   # the rendered primary is not a role brief
    (d / "ok.agent.md").write_text('---\n# a comment\ndescription: "Quoted: yes"\ntier: mid\n---\n\n**Owns:** x\n',
                                   encoding="utf-8")
    r = A.load_role(d / "ok.agent.md", "engineer")
    assert (r.name, r.description, r.tier, r.access, r.source) == ("ok", "Quoted: yes", "mid", "full", "ai/ok.agent.md")
    assert r.body.startswith("**Owns:**")


def test_check_cli(tmp_path, capsys):
    proj = _project(tmp_path)
    assert A.main(["--dir", str(proj)]) == 0
    assert "single persona (engineer), all valid" in capsys.readouterr().out
    _brief(proj, "reader", desc="Reads external material.", tier="mid", access="read-only")
    assert A.main(["--dir", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "1 role persona(s) in ai/, all valid" in out and "reader" in out
    _brief(proj, "broken", desc="")
    assert A.main(["--dir", str(proj)]) == 1
    assert "broken.agent.md" in capsys.readouterr().out
    assert A.main(["--dir", str(tmp_path / "nope")]) == 1       # clean error, no traceback
    assert "not found" in capsys.readouterr().out


def test_check_reports_layout_problems(tmp_path, capsys):
    proj = _project(tmp_path)
    ai = proj / "ai"
    _brief(proj, "worker", desc="Runs one job.", access="full")
    (ai / "instructions.md").unlink()
    (ai / "engineer.instructions.md").write_text("# old\n", encoding="utf-8")       # pre-0.37 layout
    (ai / "agents").mkdir()
    (ai / "agents" / "old.agent.md").write_text("---\ndescription: x\n---\nbody\n", encoding="utf-8")
    assert A.main(["--dir", str(proj)]) == 1
    out = capsys.readouterr().out
    assert "1 role persona(s) in ai/" in out and "all valid" not in out and "worker" in out
    assert "PROBLEM: ai/instructions.md not found" in out
    assert "legacy per-persona instructions file(s) ai/engineer.instructions.md" in out
    assert "legacy ai/agents/ directory" in out
    assert "old.agent.md" not in out.split("PROBLEM")[0]   # briefs under ai/agents/ are not read as roles
    (ai / "instructions.md").write_text("\n", encoding="utf-8")
    assert A.main(["--dir", str(proj)]) == 1
    assert "ai/instructions.md is empty" in capsys.readouterr().out
    (ai / "instructions.md").unlink()
    (ai / "instructions.md").mkdir()
    assert A.main(["--dir", str(proj)]) == 1
    assert "ai/instructions.md is not a regular file" in capsys.readouterr().out
    (ai / "instructions.md").rmdir()
    (ai / "instructions.md").write_text("# Instructions - Todo\n\n- x\n", encoding="utf-8")
    (ai / "engineer.instructions.md").unlink()
    (ai / "agents" / "old.agent.md").unlink()
    (ai / "agents").rmdir()
    assert A.main(["--dir", str(proj)]) == 0
    assert "all valid" in capsys.readouterr().out
    assert "no usable" not in R._agents_block({}, proj) and "`worker`" in R._agents_block({}, proj)


def test_rename_primary_round_trip(tmp_path):
    proj = _project(tmp_path)
    _brief(proj, "reviewer")
    with pytest.raises(ValueError, match="must match"):
        A.rename_primary(proj, "Master")
    with pytest.raises(ValueError, match="reviewer.agent.md already exists"):
        A.rename_primary(proj, "reviewer")
    log, warnings = A.rename_primary(proj, "master")
    assert not warnings
    assert any("moved ai/engineer.agent.md -> ai/master.agent.md" in line for line in log)
    ai = proj / "ai"
    assert (ai / "master.agent.md").exists() and not (ai / "engineer.agent.md").exists()
    assert (ai / "instructions.md").exists()
    assert not list(ai.glob("*.instructions.md"))            # no per-persona file appeared
    man = json.loads((ai / "manifest.json").read_text(encoding="utf-8"))
    assert man["agents"]["primary"] == "master"
    assert "ai/master.agent.md" in man["revisions"] and "ai/engineer.agent.md" not in man["revisions"]
    assert "# Todo - Master Agent" in (ai / "master.agent.md").read_text(encoding="utf-8")
    assert "ai/master.agent.md" in (proj / "AGENTS.md").read_text(encoding="utf-8")
    instr = (ai / "instructions.md").read_text(encoding="utf-8")
    assert "master.agent.md" in instr and "engineer" not in instr    # prose says "the primary persona"
    assert any("instructions.md: references to engineer.agent.md now name master.agent.md" in l for l in log)
    verdicts = {r["rel"]: r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, tmp_path / "plugins")}
    assert (verdicts["AGENTS.md"], verdicts["ai/master.agent.md"], verdicts["ai/README.md"]) == ("in-sync",) * 3
    assert A.rename_primary(proj, "master") == (["the primary persona is already 'master'; nothing to do"], [])
    A.rename_primary(proj, "engineer")
    assert (ai / "engineer.agent.md").exists() and (ai / "instructions.md").exists()
    assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, tmp_path / "plugins")} == {"in-sync"}


def test_rename_refuses_a_customized_persona(tmp_path):
    proj = _project(tmp_path)
    p = proj / "ai" / "engineer.agent.md"
    p.write_text(p.read_text(encoding="utf-8") + "\nProject-specific tweak.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conflict"):
        A.rename_primary(proj, "master")
    assert p.exists() and not (proj / "ai" / "master.agent.md").exists()   # nothing moved


def test_rename_guards_every_file_that_carries_the_name(tmp_path):
    # a customized managed file that mentions the primary would keep the old name after a re-render: refuse
    proj = _project(tmp_path)
    readme = proj / "ai" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nlocal tweak\n", encoding="utf-8")
    with pytest.raises(ValueError, match="README.md"):
        A.rename_primary(proj, "master")
    assert (proj / "ai" / "engineer.agent.md").exists()
    # a customized file that no longer carries the name (the subagents rule points at the shared
    # instructions.md now) must not block the rename
    readme.write_text(readme.read_text(encoding="utf-8").replace("\nlocal tweak\n", ""), encoding="utf-8")
    rule = proj / "ai" / "rules" / "subagents.rule.md"
    rule.write_text(rule.read_text(encoding="utf-8") + "\nlocal tweak\n", encoding="utf-8")
    log, warnings = A.rename_primary(proj, "master")
    assert not warnings and (proj / "ai" / "master.agent.md").exists()


def test_rename_without_a_revisions_map_still_rerenders(tmp_path):
    proj = _project(tmp_path)
    man_path = proj / "ai" / "manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    man.pop("revisions")
    man_path.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    log, warnings = A.rename_primary(proj, "master")
    assert not warnings
    assert "# Todo - Master Agent" in (proj / "ai" / "master.agent.md").read_text(encoding="utf-8")
    assert "ai/instructions.md" in (proj / "ai" / "rules" / "subagents.rule.md").read_text(encoding="utf-8")
    assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, tmp_path / "plugins")} == {"in-sync"}


def test_rename_warns_when_the_instructions_file_is_absent(tmp_path):
    proj = _project(tmp_path)
    (proj / "ai" / "instructions.md").unlink()
    log, warnings = A.rename_primary(proj, "master")
    assert warnings and "ai/instructions.md is absent" in warnings[0]
    assert (proj / "ai" / "master.agent.md").exists()


def test_primary_role_rejects_empty_or_null_values():
    for bad in ("", None, 3):
        with pytest.raises(ValueError):
            R.primary_role({"agents": {"primary": bad}})


def test_cli_rejects_empty_rename_and_flags_invalid_briefs(tmp_path, capsys):
    proj = _project(tmp_path)
    assert A.main(["--dir", str(proj), "--rename-primary", ""]) == 1
    assert "must match" in capsys.readouterr().out
    (proj / "ai" / "other.agent.md").write_text("# stray\n", encoding="utf-8")   # beside the primary = a role
    assert A.main(["--dir", str(proj)]) == 1
    assert "ai/other.agent.md: missing frontmatter" in capsys.readouterr().out


def test_brief_edge_cases(tmp_path):
    proj = _project(tmp_path)
    d = proj / "ai"
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


def test_role_stub_validates(tmp_path):
    proj = _project(tmp_path)
    stub = (TEMPLATE_DIR.parent / "agents" / "role.agent.md").read_text(encoding="utf-8")
    (proj / "ai" / "scout.agent.md").write_text(stub, encoding="utf-8")
    prim, roles, problems = A.load_personas(proj)
    assert [r.name for r in roles] == ["scout"] and not problems
    assert A.main(["--dir", str(proj)]) == 0


def test_revs_cli_reports_a_malformed_manifest_cleanly(tmp_path, capsys):
    proj = tmp_path / "p"
    (proj / "ai").mkdir(parents=True)
    bad = {**PACK_MANIFEST, "agents": {"primary": ""}}
    (proj / "ai" / "manifest.json").write_text(json.dumps(bad), encoding="utf-8")
    assert R.main(["classify", "--dir", str(proj)]) == 1
    assert "agents.primary" in capsys.readouterr().out
    assert A.main(["--dir", str(proj)]) == 1
    assert "agents.primary" in capsys.readouterr().out
    # unreadable JSON is no ai-pack manifest at all, so the project has no pack: still one clean line each
    (proj / "ai" / "manifest.json").write_text("{not json", encoding="utf-8")
    assert R.main(["classify", "--dir", str(proj)]) == 1
    assert "no ai-pack" in capsys.readouterr().out
    assert A.main(["--dir", str(proj)]) == 1
    assert "no ai-pack" in capsys.readouterr().out


# ----------------------------------------------------------------- any pack folder name

@pytest.mark.parametrize("pack", ["aipack", "brain", "ai"])
def test_check_finds_the_pack_whatever_its_name(tmp_path, capsys, pack):
    proj = _project(tmp_path, pack=pack)
    (proj / "source").mkdir()
    assert f"{pack}/engineer.agent.md" in (proj / "AGENTS.md").read_text(encoding="utf-8")
    assert A.main(["--dir", str(proj)]) == 0
    assert f"single persona (engineer), all valid; role personas go beside it as {pack}/<role>.agent.md" \
        in capsys.readouterr().out
    _brief(proj, "reader", desc="Reads external material.", tier="mid", access="read-only", pack=pack)
    assert A.main(["--dir", str(proj)]) == 0
    assert f"1 role persona(s) in {pack}/, all valid" in capsys.readouterr().out
    _brief(proj, "broken", desc="", pack=pack)
    assert A.main(["--dir", str(proj)]) == 1
    assert f"{pack}/broken.agent.md: 'description' is required" in capsys.readouterr().out
    (proj / pack / "broken.agent.md").unlink()
    (proj / pack / "instructions.md").unlink()
    (proj / pack / "engineer.instructions.md").write_text("# old\n", encoding="utf-8")
    assert A.main(["--dir", str(proj)]) == 1
    out = capsys.readouterr().out
    assert f"PROBLEM: {pack}/instructions.md not found" in out
    assert f"legacy per-persona instructions file(s) {pack}/engineer.instructions.md" in out


def test_check_needs_exactly_one_pack(tmp_path, capsys):
    proj = _project(tmp_path)
    shutil.copytree(proj / "ai", proj / "aipack")
    assert A.main(["--dir", str(proj)]) == 1
    assert "more than one ai-pack (ai, aipack)" in capsys.readouterr().out
    shutil.rmtree(proj / "ai")
    shutil.move(str(proj / "aipack"), str(proj / ".aipack"))          # hidden folders are never packs
    (proj / "reporting").mkdir()
    (proj / "reporting" / "manifest.json").write_text('{"name": "reporting", "version": "0.4.0"}',
                                                      encoding="utf-8")   # nor is a plugin manifest
    assert A.main(["--dir", str(proj)]) == 1
    assert "no ai-pack" in capsys.readouterr().out


@pytest.mark.parametrize("pack", ["aipack", "brain"])
def test_rename_primary_in_any_pack(tmp_path, capsys, pack):
    proj = _project(tmp_path, pack=pack)
    d = proj / pack
    log, warnings = A.rename_primary(proj, "master")
    assert not warnings and f"moved {pack}/engineer.agent.md -> {pack}/master.agent.md" in log
    assert (d / "master.agent.md").exists() and not (d / "engineer.agent.md").exists()
    man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    assert man["agents"]["primary"] == "master"
    assert f"{pack}/master.agent.md" in man["revisions"] and f"{pack}/engineer.agent.md" not in man["revisions"]
    assert f"{pack}/master.agent.md" in (proj / "AGENTS.md").read_text(encoding="utf-8")
    assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, tmp_path / "plugins")} == {"in-sync"}
    assert A.main(["--dir", str(proj), "--rename-primary", "engineer"]) == 0
    assert f"agents: moved {pack}/master.agent.md -> {pack}/engineer.agent.md" in capsys.readouterr().out
    assert (d / "engineer.agent.md").exists()


# ----------------------------------------------------------------- --rename-pack

def test_rename_pack_round_trip(tmp_path, capsys):
    proj = _project(tmp_path)
    _brief(proj, "reviewer")
    plugins = tmp_path / "plugins"
    log, warnings = A.rename_pack(proj, "aipack")
    assert not warnings
    assert not (proj / "ai").exists()
    assert (proj / "aipack" / "engineer.agent.md").exists() and (proj / "aipack" / "reviewer.agent.md").exists()
    agents_md = (proj / "AGENTS.md").read_text(encoding="utf-8")
    assert "aipack/engineer.agent.md" in agents_md and not A._path_re("ai").search(agents_md)
    claude_md = (TEMPLATE_DIR / "CLAUDE.md").read_text(encoding="utf-8")
    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == claude_md   # names no pack: untouched
    man = json.loads((proj / "aipack" / "manifest.json").read_text(encoding="utf-8"))
    assert man["revisions"] and all(k == "AGENTS.md" or k.startswith("aipack/") for k in man["revisions"])
    assert log[0] == "moved ai/ -> aipack/"
    assert any(line.startswith("AGENTS.md: ") for line in log)
    assert any("the baseline of AGENTS.md follows the rewrite" in line for line in log)
    assert "  aipack/engineer.agent.md" in log      # a managed copy that still says ai/: listed, not edited
    assert log[-2:] == [f"next: uv run -m solaris.tools.revs ff --dir {proj}",
                        f"then: uv run -m solaris.tools.revs baseline --dir {proj} (once anything ff reports "
                        "is merged)"]
    # the pristine entry file is already in sync; the managed copies fast-forward onto the new name
    verdicts = {r["rel"]: r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, plugins)}
    assert verdicts["AGENTS.md"] == "in-sync" and verdicts["aipack/engineer.agent.md"] == "fast-forward"
    assert not R.fast_forward(proj, TEMPLATE_DIR, plugins)["skipped"]
    assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, plugins)} == {"in-sync"}
    assert "aipack/instructions.md" in (proj / "aipack" / "engineer.agent.md").read_text(encoding="utf-8")
    assert A.main(["--dir", str(proj)]) == 0
    assert "1 role persona(s) in aipack/, all valid" in capsys.readouterr().out
    # on to a custom name, then back to the legacy one, through the CLI
    for old, new in (("aipack", "brain"), ("brain", "ai")):
        assert A.main(["--dir", str(proj), "--rename-pack", new]) == 0
        assert f"agents: moved {old}/ -> {new}/" in capsys.readouterr().out
        R.fast_forward(proj, TEMPLATE_DIR, plugins)
        assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, plugins)} == {"in-sync"}
    assert A.rename_pack(proj, "ai") == (["the ai-pack folder is already ai/; nothing to do"], [])


def test_rename_pack_refuses_bad_names_and_taken_targets(tmp_path, capsys):
    proj = _project(tmp_path)
    for bad in ("", ".", "..", ".hidden", "a/b", "a\\b", " aipack", "aipack\n", "ai pack", "ai\tpack",
                "#pack", "!pack", "pack*", "pack?", "pack[1]"):     # gitignore and shells read these
        with pytest.raises(ValueError, match="plain folder name"):
            A.rename_pack(proj, bad)
    (proj / "source").mkdir()
    (proj / "notes.md").write_text("x\n", encoding="utf-8")
    for taken in ("source", "notes.md"):
        with pytest.raises(ValueError, match="already exists"):
            A.rename_pack(proj, taken)
    assert (proj / "ai" / "manifest.json").exists()               # nothing moved
    assert A.main(["--dir", str(proj), "--rename-pack", "../x"]) == 1
    assert "plain folder name" in capsys.readouterr().out
    assert A.main(["--dir", str(tmp_path / "nope"), "--rename-pack", "aipack"]) == 1
    assert "not found" in capsys.readouterr().out
    shutil.copytree(proj / "ai", proj / "brain")
    assert A.main(["--dir", str(proj), "--rename-pack", "aipack"]) == 1
    assert "more than one ai-pack (ai, brain)" in capsys.readouterr().out
    bare = tmp_path / "bare"
    (bare / "source").mkdir(parents=True)
    assert A.main(["--dir", str(bare), "--rename-pack", "aipack"]) == 1
    assert "no ai-pack" in capsys.readouterr().out
    # a pack with nothing to re-key and nothing naming it: just the move
    (bare / "ai").mkdir()
    raw = json.dumps(PACK_MANIFEST)
    (bare / "ai" / "manifest.json").write_text(raw, encoding="utf-8")
    log, warnings = A.rename_pack(bare, "aipack")
    assert not warnings and log[0] == "moved ai/ -> aipack/"
    assert f"no other file under {bare} names ai/ (hidden and __* folders not scanned)" in log
    assert (bare / "aipack" / "manifest.json").read_text(encoding="utf-8") == raw


def test_rename_pack_rewrites_only_path_starts_in_the_entry_files(tmp_path, capsys):
    proj = _project(tmp_path)
    (proj / ".gitignore").write_text("__*/\nai/.memory/\n", encoding="utf-8")   # no git work tree: no guard
    (proj / "source").mkdir()
    (proj / "source" / "README.md").write_text("See ../ai/spec.md first.\n", encoding="utf-8")
    never = "Not ours: openai/, nvidia-ai/, x.ai/, ai-pack/.\n"
    elsewhere = "Under other folders: `source/ai/model.py`, https://x/ai/guide, @scope/ai/x, ../ai/spec.md.\n"
    (proj / "notes.md").write_text(never, encoding="utf-8")
    (proj / "CLAUDE.md").write_text("@AGENTS.md\n@ai/engineer.agent.md\n" + never + elsewhere, encoding="utf-8")
    skipped = (".git/config", ".cache/notes.md", "__data/notes.md", "__out/run/log.md", "node_modules/x/README.md")
    for rel in skipped:   # hidden, data, output and dependency folders are never scanned
        (proj / rel).parent.mkdir(parents=True, exist_ok=True)
        (proj / rel).write_text("see ai/spec.md\n", encoding="utf-8")
    (proj / "data.bin").write_bytes(b"\x00\xff ai/ \xfe")
    assert A.main(["--dir", str(proj), "--rename-pack", "brain"]) == 0
    out = capsys.readouterr().out
    assert "agents: moved ai/ -> brain/" in out and "agents: CLAUDE.md: 1 reference(s)" in out
    # listed for review, never edited: the pack under other folders stays as written
    for listed in (".gitignore", "CLAUDE.md", "source/README.md", "brain/engineer.agent.md"):
        assert f"\n  {listed}\n" in out
    for rel in ("notes.md", "AGENTS.md", "data.bin") + skipped:
        assert f"\n  {rel}\n" not in out
    assert "WARNING" not in out
    assert (proj / ".gitignore").read_text(encoding="utf-8") == "__*/\nai/.memory/\n"
    assert (proj / "source" / "README.md").read_text(encoding="utf-8") == "See ../ai/spec.md first.\n"
    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == ("@AGENTS.md\n@brain/engineer.agent.md\n"
                                                                + never + elsewhere)


@pytest.mark.parametrize("where, rule, add", [
    (".gitignore", "ai", "aipack"),
    (".gitignore", "/ai", "/aipack"),
    (".gitignore", "ai/.memory/", "aipack/.memory/"),
    (".git/info/exclude", "ai/.memory/", "aipack/.memory/"),
])
def test_rename_pack_refuses_to_unignore_private_files(tmp_path, monkeypatch, capsys, where, rule, add):
    proj = _project(tmp_path)
    _git_init(proj, tmp_path, monkeypatch)
    (proj / "ai" / ".memory").mkdir()
    (proj / "ai" / ".memory" / "context.md").write_text("private\n", encoding="utf-8")
    (proj / where).write_text(f"__*/\n{rule}\n", encoding="utf-8")
    before = {rel: (proj / rel).read_bytes() for rel in ("AGENTS.md", "CLAUDE.md", "ai/manifest.json")}
    assert A.main(["--dir", str(proj), "--rename-pack", "aipack"]) == 1
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.startswith("agents: refusing to move ai/ to aipack/ (nothing moved): "
                                                    "git would stop ignoring ")
    assert f"add '{add}' to {where} (beside '{rule}', line 2)" in out
    assert not (proj / "aipack").exists() and (proj / "ai" / ".memory" / "context.md").exists()
    assert {rel: (proj / rel).read_bytes() for rel in before} == before
    # with the entry for the new name beside the old one, it goes through and the files stay ignored
    with open(proj / where, "a", encoding="utf-8") as fh:
        fh.write(f"{add}\n")
    log, warnings = A.rename_pack(proj, "aipack")
    assert not warnings and _ignored(proj, "aipack/.memory/context.md")
    assert log[-1] == f"last: remove the ignore entries for ai/ ({where} '{rule}')"


def test_rename_pack_guard_reads_git_like_git(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    _git_init(proj, tmp_path, monkeypatch)
    # the mirror case: a rule meant for other folders would drop the pack's files from git
    (proj / ".gitignore").write_text("build/\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"git would start ignoring \d+ file\(s\) it keeps now, such as build/\S+ "
                                         r"\(\.gitignore, line 1: 'build/'\) - narrow that entry or pick another"):
        A.rename_pack(proj, "build")
    # a fresh clone has no .memory/ yet: the files the pack writes there later must stay ignored too
    (proj / ".gitignore").write_text("ai/.memory/\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"such as aipack/\.memory/credentials\.md - add 'aipack/\.memory/'"):
        A.rename_pack(proj, "aipack")
    assert (proj / "ai").is_dir() and not (proj / "aipack").exists()
    # a rule in a .gitignore inside the pack moves with it, and a tracked file is not ignored whatever matches it
    (proj / ".gitignore").write_text("ai/notes.md\n", encoding="utf-8")
    (proj / "ai" / ".gitignore").write_text(".memory/\n", encoding="utf-8")
    (proj / "ai" / "notes.md").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(proj), "add", "-f", "ai/notes.md"], check=True)
    log, _ = A.rename_pack(proj, "aipack")
    assert log[0] == "moved ai/ -> aipack/" and not log[-1].startswith("last:")
    assert _ignored(proj, "aipack/.memory/credentials.md")


def test_rename_pack_needs_stignore_rules_for_the_new_name(tmp_path):
    proj = _project(tmp_path)
    # only the whole folder name counts: comments, longer names and other folders do not stop the rename
    (proj / ".stignore").write_text("// ai/ is the pack\n(?d)**/openai/cache\nai-pack/x\n", encoding="utf-8")
    A.rename_pack(proj, "aipack")
    (proj / ".stignore").write_text("(?d)**/.venv\n(?d)aipack/__scratch\n!/aipack/keep\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"\.stignore names aipack/ but not ai/ - add '\(\?d\)ai/__scratch', "
                                         r"'!/ai/keep' and keep the old ones until the rename is done; then rerun"):
        A.rename_pack(proj, "ai")
    assert (proj / "aipack").is_dir() and not (proj / "ai").exists()
    with open(proj / ".stignore", "a", encoding="utf-8") as fh:
        fh.write("(?d)ai/__scratch\n!/ai/keep\n")
    log, _ = A.rename_pack(proj, "ai")
    assert log[-1] == "last: remove the ignore entries for aipack/ (.stignore '(?d)aipack/__scratch', " \
                      ".stignore '!/aipack/keep')"


def test_rename_pack_puts_everything_back_when_it_cannot_finish(tmp_path, monkeypatch, capsys):
    proj = _project(tmp_path)
    (proj / "CLAUDE.md").write_text("@AGENTS.md\n@ai/spec.md\n", encoding="utf-8")
    paths = ("AGENTS.md", "CLAUDE.md", "ai/manifest.json")
    before = {rel: (proj / rel).read_bytes() for rel in paths}

    def untouched():
        return (not (proj / "aipack").exists()
                and {rel: (proj / rel).read_bytes() for rel in paths} == before)

    # a write that fails after the move (a read-only manifest), with both entry files already rewritten
    if getattr(os, "geteuid", lambda: 1)() != 0:   # root writes read-only files anyway
        man = proj / "ai" / "manifest.json"
        man.chmod(0o444)
        try:
            assert A.main(["--dir", str(proj), "--rename-pack", "aipack"]) == 1
        finally:
            man.chmod(0o644)
        out = capsys.readouterr().out
        assert out.startswith("agents: cannot finish the rename (") and out.count("\n") == 1
        assert "undone: ai/ and every file it wrote are back as they were" in out
        assert untouched()
    # a failure after every write, Ctrl-C included
    for exc in (OSError("disk gone"), KeyboardInterrupt()):
        def boom(*_args, exc=exc):
            raise exc
        monkeypatch.setattr(A, "_files_naming", boom)
        with pytest.raises(ValueError if isinstance(exc, OSError) else KeyboardInterrupt):
            A.rename_pack(proj, "aipack")
        assert untouched()
    monkeypatch.undo()
    # an entry file that cannot be read stops it before the move
    (proj / "CLAUDE.md").write_bytes(b"caf\xe9 ai/spec.md\n")
    with pytest.raises(ValueError, match="cannot read CLAUDE.md .*; nothing moved"):
        A.rename_pack(proj, "aipack")
    assert (proj / "ai").is_dir() and not (proj / "aipack").exists()
    (proj / "CLAUDE.md").write_bytes(before["CLAUDE.md"])
    log, warnings = A.rename_pack(proj, "aipack")
    keys = json.loads((proj / "aipack" / "manifest.json").read_text(encoding="utf-8"))["revisions"]
    assert not warnings and keys and all(k == "AGENTS.md" or k.startswith("aipack/") for k in keys)
    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n@aipack/spec.md\n"


def test_rename_pack_keeps_a_customized_entry_file_a_merge(tmp_path):
    proj = _project(tmp_path)
    agents_md = proj / "AGENTS.md"
    agents_md.write_text(agents_md.read_text(encoding="utf-8") + "\nLocal note: read ai/spec.md first.\n",
                         encoding="utf-8")
    base = json.loads((proj / "ai" / "manifest.json").read_text(encoding="utf-8"))["revisions"]["AGENTS.md"]
    log, warnings = A.rename_pack(proj, "aipack")
    assert not warnings and not any("baseline of AGENTS.md" in line for line in log)
    assert "Local note: read aipack/spec.md first." in agents_md.read_text(encoding="utf-8")
    man = json.loads((proj / "aipack" / "manifest.json").read_text(encoding="utf-8"))
    assert man["revisions"]["AGENTS.md"] == base          # a customization never passes for pristine
    verdicts = {r["rel"]: r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, tmp_path / "plugins")}
    assert verdicts["AGENTS.md"] == "conflict"            # so revs ff leaves it for a hand merge


def test_renames_carry_the_legacy_keys_of_a_hand_renamed_pack(tmp_path):
    # a pack renamed by hand before 0.39 (aipack/) may still record its baseline under ai/... keys; both renames
    # read them as <pack>/... (revs' rule) so the baseline keeps matching and nothing turns into a conflict
    proj = _project(tmp_path, pack="aipack")
    plugins = tmp_path / "plugins"
    man = _legacy_keys(proj, "aipack")
    assert "ai/engineer.agent.md" in man["revisions"]
    log, warnings = A.rename_primary(proj, "master")
    assert not warnings
    keys = json.loads((proj / "aipack" / "manifest.json").read_text(encoding="utf-8"))["revisions"]
    assert "aipack/master.agent.md" in keys and not any(k.startswith("ai/") for k in keys)
    assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, plugins)} == {"in-sync"}
    _legacy_keys(proj, "aipack")
    log, warnings = A.rename_pack(proj, "brain")
    assert not warnings
    keys = json.loads((proj / "brain" / "manifest.json").read_text(encoding="utf-8"))["revisions"]
    assert keys and all(k == "AGENTS.md" or k.startswith("brain/") for k in keys)
    assert "brain/master.agent.md" in keys
    assert not R.fast_forward(proj, TEMPLATE_DIR, plugins)["skipped"]
    assert {r["verdict"] for r in R.classify(proj, TEMPLATE_DIR, plugins)} == {"in-sync"}


def test_rename_pack_caps_the_leftover_list(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    monkeypatch.setattr(A, "LIST_MAX", 2)
    for i in range(3):
        (proj / f"note{i}.md").write_text("see ai/spec.md\n", encoding="utf-8")
    log, _ = A.rename_pack(proj, "aipack")
    listed = [line for line in log if line.startswith("  ") and not line.startswith("  ...")]
    assert listed == ["  note0.md", "  note1.md"]           # shallow first
    more = next(line for line in log if line.startswith("  ... and "))
    assert (f"grep -rlE '(^|[^[:alnum:]_.-])ai/' {proj} --exclude-dir='.?*' --exclude-dir='__*' "
            "--exclude-dir=node_modules") in more
