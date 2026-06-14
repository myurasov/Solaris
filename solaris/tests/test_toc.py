# Copyright 2026 Mihail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Tests for solaris.tools.toc."""

from __future__ import annotations

from solaris.tools import toc as T


def test_slug_matches_github_style():
    assert T.slug("Goal") == "goal"
    assert T.slug("2. Decisions locked (from Q&A)") == "2-decisions-locked-from-qa"
    assert T.slug("4.2 `mcp_sync.py` tool") == "42-mcp_syncpy-tool"
    assert T.slug("Read first (every session)") == "read-first-every-session"


def test_build_toc_nesting_and_dedupe():
    headers = [(1, "Title"), (2, "Alpha"), (3, "Sub"), (2, "Alpha"), (2, "Beta")]
    toc = T.build_toc(headers)
    assert toc == [
        "- [Alpha](#alpha)",
        "  - [Sub](#sub)",
        "- [Alpha](#alpha-1)",
        "- [Beta](#beta)",
    ]


def test_headers_in_code_fence_and_omit_are_ignored():
    body = [
        "# Title",
        "## Real",
        "```",
        "## NotAHeader",
        "```",
        "## Hidden <!-- omit in toc -->",
        "## AlsoReal",
    ]
    headers = T.parse_headers(body)
    texts = [t for _, t in headers]
    assert "Real" in texts and "AlsoReal" in texts
    assert "NotAHeader" not in texts
    assert all("Hidden" not in t for t in texts)


def test_render_inserts_marks_h1_and_is_idempotent():
    src = "# Title\n\nIntro paragraph.\n\n## One\n\nbody\n\n## Two\n\nmore\n"
    out1 = T.render(src)
    assert "# Title <!-- omit in toc -->" in out1
    assert "- [One](#one)" in out1 and "- [Two](#two)" in out1
    # TOC sits before the intro paragraph
    assert out1.index("- [One](#one)") < out1.index("Intro paragraph.")
    # idempotent
    assert T.render(out1) == out1


def test_render_no_level2_headers_is_unchanged():
    src = "# Only a title\n\nJust prose, no sections.\n"
    assert T.render(src) == src


def test_single_level2_header_gets_no_toc():
    src = "# Title\n\n## Only\n\nbody\n"
    assert T.render(src) == src


def test_render_refreshes_existing_toc():
    src = "# T <!-- omit in toc -->\n\n- [Old](#old)\n\n## New\n\nbody\n\n## Two\n\nx\n"
    out = T.render(src)
    assert "- [New](#new)" in out and "- [Two](#two)" in out
    assert "- [Old](#old)" not in out


def test_display_escapes_ampersand():
    assert T.build_toc([(2, "Q&A"), (2, "B")])[0] == "- [Q\\&A](#qa)"


def test_process_write_and_check(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# T\n\n## A\n\nx\n\n## B\n\ny\n", encoding="utf-8")
    assert T.process(f, write=False) == "stale"
    assert T.process(f, write=True) == "written"
    assert T.process(f, write=False) == "current"
