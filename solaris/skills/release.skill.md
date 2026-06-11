---
name: release
triggers: ["do a release", "release cycle", "cut a release", "ship a release", "publish a release", "new release", "bump the version and release"]
summary: Cut a Solaris framework release end-to-end: commit pending changes, bump pyproject.toml, author migration, bump revisions, update spec + README, tag + push, publish GitHub release, and backfill any missing GitHub releases so the Releases page mirrors the tag list.
---

# release <!-- omit in toc -->

- [When to use](#when-to-use)
- [Steps](#steps)
- [Choosing the version bump](#choosing-the-version-bump)
- [Backfilling missing GitHub releases](#backfilling-missing-github-releases)
- [Verify the end state](#verify-the-end-state)
- [Guardrails](#guardrails)
- [Reference paths](#reference-paths)

## When to use

The user says **"do a release"**, **"cut a release"**, **"publish a release"**, or similar. Run the full sequence below end-to-end — autonomous multi-step flow; don't stop to confirm each step, surface a consolidated summary at the end. Follows `rules/commits.rule.md` throughout.

## Steps

1. **Commit pending changes.** Stage and commit every uncommitted change with policy-compliant messages (imperative, ≤72 chars, ASCII only, no AI-attribution trailers). Group unrelated changes into separate atomic commits.

2. **Decide the version bump.** See [Choosing the version bump](#choosing-the-version-bump). Edit `pyproject.toml` `[project].version`.

3. **Author the migration.** Every **MINOR / MAJOR** bump needs a migration at `solaris/migrations/<to_version>.md` (copy `solaris/migrations/template.md`). Fill all frontmatter fields (`to_version`, `from_version`, `title`, `breaking`, `touches`). A **marker migration** (no file edits, just records the version step) is valid when only framework internals changed. **PATCH** bumps get **no** migration.

4. **Update docs to reflect the changes.** Always:
   - **Spec file** — add a "What changed in vX.Y.Z" sentence to the opening paragraph of the current spec (e.g. `solaris/spec/spec-v0.8.0.md`). For a **MINOR** bump, also create a new spec snapshot `solaris/spec/spec-v<version>.md` as a copy of the updated current spec, then update every spec link across the repo (`AGENTS.md`, `README.md`, `solaris/solaris.agent.md`) to point to the new file. Keep the old spec file in place (it is referenced by git history and migration notes).
   - Any skill, rule, template, or tools-reference doc that the change touched — keep them accurate.

5. **Bump revisions for every framework file you edited.** For each changed tracked file:
   ```bash
   uv run -m solaris.tools.revs bump <file>
   ```
   Then rebuild the ledger:
   ```bash
   uv run -m solaris.tools.revs ledger
   ```

6. **Run `uv sync`** so `uv.lock` reflects any dependency changes; stage `uv.lock`.

7. **Commit the release.** Message: `Release Solaris <version>` (version bump + migration + spec + doc refresh + revisions all in one commit, or preceded by a `... (<vX.Y.Z>)` feature commit — match recent history).

8. **Create the tag** `v<version>` (lightweight, on the release commit).

9. **Push** branch then tag — confirm with the user before pushing per `rules/safety.rule.md`:
   ```bash
   git push origin <branch>
   git push origin v<version>
   ```
   Strip any AI-attribution trailer from commits before pushing.

10. **Publish the GitHub release** and **backfill** — see [below](#backfilling-missing-github-releases):
    ```bash
    gh release create v<version> --title "Solaris <version>" --notes "<release notes>"
    ```
    Release notes: 1-3 sentences summarising what changed (from the spec "What changed" blurb and migration title).

## Choosing the version bump

- **MINOR** (`0.X.0`) — new skill, new feature, behavior change, new template/tool, significant doc restructure. The common case. Requires a migration (marker is fine).
- **PATCH** (`0.X.Y`) — bug fix, doc tweak, internal refactor only. No migration.
- **MAJOR** (`X.0.0`) — breaking ai-pack schema change. Migration with `breaking: true`.

When unsure between MINOR and PATCH, prefer **MINOR** for anything a user or project migration would notice — PATCH skips migrations entirely.

## Backfilling missing GitHub releases

Before (or right after) publishing the new release, ensure **every prior semver tag has a GitHub release**. Compute the gap reliably:

```bash
# tags that already have a release
gh release list --limit 100 --json tagName \
  | python3 -c "import json,sys; print('\n'.join(sorted(r['tagName'] for r in json.load(sys.stdin))))" \
  | sort > /tmp/has_release.txt
# all semver tags
git tag --sort=v:refname | grep -E "^v[0-9]+\.[0-9]+(\.[0-9]+)?$" | sort > /tmp/all_tags.txt
comm -23 /tmp/all_tags.txt /tmp/has_release.txt   # tags missing a release
```

For each missing tag, derive notes from the spec "What changed" paragraph or the tag's commit subject (`git log -1 --format=%s v<x>`):
```bash
gh release create v<x> --title "Solaris <x>" --notes "<notes>"
```

## Verify the end state

- `uv run -m solaris.tools.version current` matches the new version.
- `git status -sb` shows the branch clean and in sync with origin.
- The new tag points at HEAD and exists on origin (`git ls-remote --tags origin v<version>`).
- The GitHub release is live; the backfill diff (`comm -23` above) is empty for semver tags.
- `uv run -m solaris.tools.revs status` reports no unbumped tracked files.

## Guardrails

- Autonomous flow — the user expects all steps done without per-step prompts except the push/publish confirmation required by `rules/safety.rule.md`. Surface one summary at the end.
- **Never** put project/customer/personal data or local task IDs in commit messages, migration files, or release notes.
- Re-run `git log -1 --format=%B` after committing to confirm no AI-attribution trailer slipped in; strip it with `git commit --amend` before pushing if it did.
- If `uv run -m solaris.tools.revs status` reports unbumped files, bump them before the release commit.

## Reference paths

| File | Action |
|------|--------|
| `pyproject.toml` | Bump `[project].version` |
| `solaris/migrations/<to_version>.md` | Create (copy `template.md`) for MINOR/MAJOR |
| `solaris/revisions.json` | Rebuilt by `revs ledger` |
| `solaris/spec/spec-v<current>.md` | Add "What changed in vX.Y.Z" blurb |
| `solaris/spec/spec-v<version>.md` | New snapshot for MINOR+ bumps |
| `AGENTS.md`, `README.md`, `solaris/solaris.agent.md` | Update spec link for MINOR+ bumps |
| `uv.lock` | Refresh via `uv sync` |
