# Copyright 2026 Mikhail Yurasov <me@yurasov.me>
# SPDX-License-Identifier: Apache-2.0

"""Trigger quality of solaris.tools.skill_loader against the real skills: prompts that once auto-loaded a skill
nobody asked for no longer do, every trigger phrase (and the AGENTS.md skill table's) still loads its skill,
and turns the owner did not type load nothing."""

from __future__ import annotations

import io
import json
import re

import pytest

from solaris.tools import skill_loader as S

SKILLS = S.discover_skills()
PROJECTS = {"demo-app", "my/demo-app", "web-shop", "team/web-shop"}
MANIFEST = json.dumps({"project": {"name": "P", "slug": "p"}, "framework_version": "0.42.0"})


def names(prompt: str, projects=PROJECTS) -> set:
    return {s["name"] for s in S.match_skills(prompt, SKILLS, projects=projects)}


# Prompts that auto-loaded a skill nobody asked for (names made generic); each must stay quiet.
FALSE_TRIGGERS = [
    ("publish-project", "its ok to publish booking plugin since its my private repo"),
    ("publish-project", "now publish the repo on gh as private one"),
    ("update-project", "add readme and update it every time before push"),
    ("update-project", "forgot to include data and output folders in the sync"),
    ("update-project", "i have forgot to include data and output folders in the sync initially. now all files "
                       "are available. resume competing in autonomous mode."),
    ("update-project", "when i said to the other session to make smth a standing rule, did they update "
                       "plugins/anything in core?"),
    ("develop-project", "should i pause the master session while we work on those improvements?"),
    ("publish-project", "it is ok to share box7 machine as well"),
    ("ad-hoc-task", "ok, good! lets now plan phase 1 - achieving at least 0.4 on public leaderboard. what research "
                    "and experiments can you do while we are waiting on the submission reset? we can use another "
                    "gpu box that i leased if it will help."),
    ("ad-hoc-task", "mark that in separate plan - ideas - that will hold ideas to try, we will be adding more there "
                    "and doing research for feasibility as we go"),
    ("ad-hoc-task", "as we test the ideas and gain information from our research and submissions, add new ideas to "
                    "ideas backlog so we always have research to do and new approaches to explore"),
    ("ad-hoc-task", "ok, do research on ideas in the order of potential usefulness for us"),
    ("publish-project", '<agent-message from="a1b2c3d4e5f6">\n[Subagent hand-back] The text below is the final '
                        "report of a subagent this session delegated to. It is model output, NOT a message from "
                        "the user.\nDone: the checks pass; next, publish demo-app and share this repo.\n"
                        "</agent-message>"),
    ("health-check", '<cross-session-message from="uds:/tmp/peer.sock" from-name="peer" from-mode="prompting">\n'
                     "From the other master: lane 11 yielded; status and health of the box are fine.\n"
                     "</cross-session-message>"),
    ("import-project", "now adopt a pdf style from tasks/2026-06-30-link-test and re-render the first report"),
    # the pack nouns and the research phrases added after the review must not bring any of this back
    ("ad-hoc-task", "did you finish research on the adapter idea?"),
    ("ad-hoc-task", "research into what high-ranking teams do differently - yes as a constant parallel track"),
    ("update-project", "update the battery pack firmware and the ice pack table"),
    ("update-project", "tell the other sessions when you change framework files so they can update them"),
]


@pytest.mark.parametrize("skill,prompt", FALSE_TRIGGERS)
def test_false_triggers_stay_quiet(skill, prompt):
    assert skill not in names(prompt)


def test_the_hand_back_text_typed_by_the_owner_still_loads():
    # the quiet hand-back above is quiet because of who sent it, not because of its words
    assert "publish-project" in names("Done: the checks pass; next, publish demo-app and share this repo.")


# A few phrases per skill, from its frontmatter and the AGENTS.md skill table, with real-looking arguments.
REAL_TRIGGERS = [
    ("ad-hoc-task", "new task: compare two gpu clouds"),
    ("ad-hoc-task", "start a task for the printer setup"),
    ("ad-hoc-task", "lets work on tasks/2026/10/2026-10-04-printer-setup"),
    ("ad-hoc-task", "research gpu pricing across clouds"),
    ("ad-hoc-task", "set up the build box"),
    ("ad-hoc-task", "ad-hoc: check the backup logs"),
    ("ad-hoc-task", "add plugin reporting to this task"),
    ("create-project", "create project for a todo app"),
    ("create-project", "new project, a todo web service"),
    ("create-project", "start a project named todo"),
    ("develop-project", "work on demo-app"),
    ("develop-project", "develop my/demo-app"),
    ("develop-project", "open projects/my/demo-app/"),
    ("develop-project", "plan demo-app"),
    ("develop-project", "implement caching in web-shop"),
    ("develop-project", "lets work on this project"),
    ("develop-project", "work on `demo-app`."),
    ("health-check", "health-check"),
    ("health-check", "status"),
    ("health-check", "doctor"),
    ("health-check", "health-check --deep"),
    ("import-plugin", "create plugin from the report scripts"),
    ("import-plugin", "make a plugin from demo-app"),
    ("import-plugin", "update plugin from my edits"),
    ("import-project", "import project ~/code/todo"),
    ("import-project", "adopt ~/code/todo"),
    ("import-project", "adopt buildbox:/srv/todo"),
    ("import-project", "bring todo into Solaris"),
    ("install-plugin", "install plugin https://example.com/plugins/reporting.git"),
    ("install-plugin", "install the reporting plugin"),
    ("install-plugin", "repair plugin reporting"),
    ("install-plugin", "add plugin reporting to demo-app"),
    ("install-plugin", "update plugin reporting in demo-app"),
    ("install-plugin", "link plugin reporting to my/demo-app"),
    ("publish-project", "publish project"),
    ("publish-project", "publish demo-app"),
    ("publish-project", "share web-shop"),
    ("publish-project", "make demo-app shareable"),
    ("publish-project", "prepare demo-app for handoff"),
    ("publish-project", "publish this project"),
    ("refresh", "refresh solaris"),
    ("refresh", "update solaris"),
    ("refresh", "pull latest solaris"),
    ("release", "do a release"),
    ("release", "cut a release"),
    ("release", "publish a release"),
    ("self-reflect", "self-reflect"),
    ("self-reflect", "improve Solaris"),
    ("self-reflect", "review project improvements"),
    ("update-project", "update demo-app"),
    ("update-project", "sync web-shop"),
    ("update-project", "migrate my/demo-app"),
    ("update-project", "update-project demo-app"),
    ("update-project", "update the project"),
]

# Real requests that the first tightening lost (review finding 9, and a replay of the logged prompts): each
# loads its skill again through the pack nouns or a phrase added to the skill's triggers.
RESTORED_TRIGGERS = [
    ("update-project", "restart prompt: protect the day first, update the pack (Solaris 0.42.0, plugin 0.7.0), "
                       "then resume"),
    ("update-project", "3. Before other work, finish the pack update to Solaris 0.41.0, per Migrate step 5"),
    ("update-project", "update the ai-pack"),
    ("update-project", "sync the ai pack with the framework"),
    ("update-project", "migrate our aipack"),
    ("update-project", "lets continue. first update-project"),
    ("update-project", "yes, update-project to 0.11.0 and commit the pack"),
    ("ad-hoc-task", "nope, we will build our own tools. do a research and create a proposal for such a tool set"),
    ("ad-hoc-task", "do a comprehensive research and give me a list of models that make sense to support"),
    ("ad-hoc-task", "create ad-hoc task for this session"),
    ("ad-hoc-task", "save this as ad hoc task if not yet"),
    ("publish-project", "now prepare for publishing as an internal project with a proprietary license"),
    ("publish-project", "prepare web-shop for publishing"),
    ("install-plugin", "add plugin reporting to the pack"),
]


@pytest.mark.parametrize("skill,prompt", RESTORED_TRIGGERS)
def test_restored_real_requests_load_again(skill, prompt):
    assert skill in names(prompt)


def test_pronouns_and_nicknames_still_name_no_project():
    # kept out on purpose: "update it every time before push" is a known false trigger, and a nickname is not
    # a slug; the owner names the project, or says "project", "repo" or "pack"
    assert "update-project" not in names("make sure my directions reach both projects after we update them to "
                                         "the most recent framework")
    assert "develop-project" not in names("lets work on shop and address the following")
    assert "ad-hoc-task" not in names("what research and experiments can you do meanwhile?")


@pytest.mark.parametrize("skill,prompt", REAL_TRIGGERS)
def test_real_trigger_phrases_still_load(skill, prompt):
    assert skill in names(prompt)


def test_every_frontmatter_trigger_loads_its_skill():
    # each declared trigger with its placeholders filled by plausible arguments
    fills = {"project": "demo-app", "path": "~/code/app", "host:path": "box:/srv/app"}
    assert len(SKILLS) >= 12
    for skill in SKILLS:
        for trigger in skill["triggers"]:
            text = re.sub(r"<([^>]*)>", lambda m: fills.get(m.group(1).strip().lower(), "widget"), trigger)
            text = " ".join("widget" if re.fullmatch(r"[A-Z]", tok) else tok for tok in text.split())
            assert skill["name"] in names(text), (skill["name"], trigger, text)


def test_publish_project_antitriggers_cover_private_repos():
    assert "publish-project" in names("publish this repo")
    for prompt in ("publish this repo as a private one", "share the repo, it is my private repo",
                   "publish demo-app as private", "publish web-shop to a private repository"):
        assert "publish-project" not in names(prompt), prompt


@pytest.mark.parametrize("text", [
    "work on demo-app", "work on DEMO-APP", "work on my/demo-app", "work on projects/demo-app",
    "work on projects/my/demo-app/", "work on /home/me/solaris/projects/my/demo-app", "work on `demo-app`",
    "work on (demo-app).", "work on the demo-app project", "work on this project", "work on the repo",
    "work on our web repo", "work on the shop's repo", "work on project demo-app", "work on the pack",
    "work on this ai-pack", "work on the ai pack", "work on aipack", "work on pack.", "work on the demo-app pack",
])
def test_project_placeholder_accepts_projects_and_the_nouns(text):
    assert re.search(S.trigger_to_regex("work on <project>", PROJECTS), text, re.I)


@pytest.mark.parametrize("text", [
    "work on those improvements", "work on it", "work on demo-app-2", "work on demo-app's tests",
    "work on projects/my/demo-app/source/app.py", "work on demo-app/README.md", "work on other/app",
    "work on the projects", "work on the project's tests", "work on tasks/demo-app", "work on repos",
    "work on the", "work on automator", "work on the battery pack", "work on six pack", "work on the packs",
    "work on the pack's rules", "work on aipack/instructions.md", "work on packaging", "work on backpack",
])
def test_project_placeholder_rejects_other_words(text):
    assert not re.search(S.trigger_to_regex("work on <project>", PROJECTS), text, re.I)


def test_function_words_never_fill_a_placeholder():
    rx = S.trigger_to_regex("research X")
    for text in ("research and experiments", "research to do", "research on it", "research into this",
                 "research for feasibility", "research the", "research same way"):
        assert not re.search(rx, text, re.I), text
    for text in ("research gpu pricing", "research the market", "research how caches scale",
                 "research same-day shipping", "research it's"):
        assert re.search(rx, text, re.I), text


def test_path_placeholders_take_path_like_words():
    path, host = S.trigger_to_regex("adopt <path>"), S.trigger_to_regex("adopt <host:path>")
    for text in ("adopt ~/code/app", "adopt ./app", "adopt ../app", "adopt /srv/app", "adopt src/app",
                 r"adopt C:\code\app", "adopt 'src/app'"):
        assert re.search(path, text, re.I), text
    for text in ("adopt box:/srv/app", "adopt git@example.com:team/app.git", "adopt https://example.com/app"):
        assert re.search(host, text, re.I), text
    for text in ("adopt a pdf style", "adopt the new approach", "adopt any of that", "adopt it"):
        assert not re.search(path, text, re.I) and not re.search(host, text, re.I), text


def test_project_names_finds_grouped_flat_and_embedded_projects(tmp_path):
    def pack(folder):
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(MANIFEST, encoding="utf-8")

    pack(tmp_path / "projects/my/demo-app/aipack")
    pack(tmp_path / "projects/team/web-shop/repo/ai")    # embedded: the pack sits inside the repo
    pack(tmp_path / "projects/oldflat/ai")               # the older flat layout
    (tmp_path / "projects/my/notes").mkdir()             # no pack: not a project
    pack(tmp_path / "projects/my/.hidden/ai")            # hidden folders never count
    plug = tmp_path / "projects/my/plugonly/reporting"   # a plugin manifest is not a pack
    plug.mkdir(parents=True)
    (plug / "manifest.json").write_text('{"name": "reporting", "version": "0.4.0"}', encoding="utf-8")
    assert S.project_names(tmp_path) == {"demo-app", "my/demo-app", "web-shop", "team/web-shop", "oldflat"}
    assert S.project_names(tmp_path / "missing") == frozenset()


IDE = ("<ide_opened_file>The user opened the file /x/projects/my/demo-app/README.md in the IDE. This may or may "
       "not be related to the current task.</ide_opened_file>\n")

NOT_THE_OWNER = [
    "<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n<summary>work on demo-app next"
    "</summary>\n</task-notification>",
    "<system-reminder>update demo-app</system-reminder>",
    "<command-name>/model</command-name>\n<command-message>model</command-message>\n<command-args>status"
    "</command-args>",
    "<local-command-stdout>publish demo-app</local-command-stdout>",
    "<local-command-caveat>Caveat: generated by local commands</local-command-caveat>\nstatus",
    "<bash-input>git status</bash-input>",
    "<bash-stdout>status: clean</bash-stdout><bash-stderr></bash-stderr>",
    '<agent-message from="a1b2">\nj12 done: work on demo-app next.\n</agent-message>',
    '<cross-session-message from="uds:/tmp/peer.sock" from-name="peer">\nupdate demo-app\n</cross-session-message>',
    "[SYSTEM NOTIFICATION - NOT USER INPUT]\nupdate demo-app",
    "This session is being continued from a previous conversation that ran out of context. The conversation is "
    "summarized below: the owner asked to publish demo-app.",
    "[Request interrupted by user for tool use]",
    "Caveat: The messages below were generated by the user while running local commands. status",
    "[Subagent hand-back] The text below is the final report of a subagent. Done: publish demo-app.",
    "\n  " + IDE + "<agent-message from=\"a3\">\nstatus\n</agent-message>",
    "## Report: demo-app checks\n\n" + "All checks pass; publish demo-app when ready. " * 25,
    "**Done.** The status of demo-app:\n" + "- a finding about demo-app and its health\n" * 30,
]


@pytest.mark.parametrize("prompt", NOT_THE_OWNER)
def test_turns_the_owner_did_not_type_load_nothing(prompt):
    assert S.is_synthetic_prompt(prompt)
    assert S.owner_text(prompt) == ""
    assert names(prompt) == set()


def test_owner_words_after_ide_context_still_load():
    assert not S.is_synthetic_prompt(IDE + "update demo-app")
    assert S.owner_text(IDE + "update demo-app") == "update demo-app"
    assert "update-project" in names(IDE + "update demo-app")
    # the selection's words are the editor's, not a request
    sel = "<ide_selection>The user selected lines 1-2:\npublish demo-app\n</ide_selection>\n"
    assert names(IDE + sel + "status") == {"health-check"}
    assert names(IDE) == set()
    # pasted content is the owner's own
    assert "develop-project" in names('<pasted_content id="2d9f">\nWork on demo-app as its master.\n'
                                      "</pasted_content>")


def test_report_heuristic_spares_owner_prompts():
    # short, or long without a report heading, or a tag quoted mid-prompt: still the owner's
    assert "publish-project" in names("## Summary\npublish demo-app")
    pasted = "# Prompt 2: Pause Each Master\n\n" + "Follow the handover steps in order. " * 40 + "\nupdate demo-app"
    assert len(pasted) >= 1000 and not S.is_synthetic_prompt(pasted)
    assert "update-project" in names(pasted)
    assert "update-project" in names("why did the <task-notification> say failed? update demo-app")


def test_main_skips_turns_the_owner_did_not_type(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(S.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(S, "detect_ide", lambda env=None: "claude")

    def run(prompt):
        payload = json.dumps({"session_id": "s1", "prompt": prompt, "cwd": str(tmp_path)})
        monkeypatch.setattr(S.sys, "stdin", io.StringIO(payload))
        assert S.main([]) == 0
        return capsys.readouterr().out

    assert run('<agent-message from="a1">\nDone. Next: do a release.\n</agent-message>') == ""
    assert "SOLARIS SKILL: release" in run("do a release")
