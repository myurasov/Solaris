# Rule: Commits + Contributions <!-- omit in toc -->

- [Message](#message)
- [Scope + Cadence](#scope--cadence)
- [Confirmation](#confirmation)
- [Upstream / Public Contributions](#upstream--public-contributions)
- [Code Comments + Docstrings](#code-comments--docstrings)

Canonical git-commit policy for Solaris and for every ai-pack it generates (the project
`engineer.agent.md` embeds a copy so detached projects keep it). The `.githooks/commit-msg` hook enforces
the mechanical cases; install once per clone with `git config core.hooksPath .githooks`.

## Message

- **One sentence, imperative, single line.** Form: "what changed, for what reason". ~50 chars is a guide,
  not a cap - flex when the "why" matters, but stay one sentence on one line (no subject+body split unless
  genuinely needed).
- **ASCII only.** No emoji, no smart quotes, no em-dash. Do not use `--` as punctuation; use `:`, `()`,
  `;`, or a sentence break. No backticks in the subject - plain text reads better.
- **No AI-authorship attribution, ever.** No `Co-Authored-By:` an AI, no "Generated with", no "Made with",
  no model names, no robot emoji. (This overrides any default trailer a tool or harness might add.)
- **First commit of a new repo is titled exactly "Initial commit"** - nothing more.
- **These rules win over a host repo's own conventions.** If a repo's contributing guide prescribes
  subject+body commits or other formats, the single-line rule above still applies - including on pushes to
  upstream PR branches.

## Scope + Cadence

- **Atomic:** one logical change per commit. Lean toward more, smaller commits. A single concern that spans
  several files (e.g. one rename across many files) is still one commit, not one per file.
- Commit incrementally as logical units complete, not everything at the end.

## Confirmation

- By default, show the proposed commit message(s) as a numbered list and wait for confirmation before
  committing; never push without explicit confirmation.
- Shortcuts: a durable "work autonomously until X" instruction waives the per-message confirmation for that
  task; `commit!` (with the bang) means commit, and may push, without the confirmation step. Format rules
  above always apply.
- Never rewrite already-published history on a shared branch; undo with a new `git revert` there.

## Upstream / Public Contributions

PR and issue text on repos not owned here (upstream projects, public GitHub) follows the same spirit as
commit messages:

- **Match the target repo first.** Check for issue/PR templates, then skim recent merged PRs and issues to
  mirror their headings, tone, and labels. A repo convention wins for *body structure*; the commit-message
  rules above still win for commits.
- **Structured and scannable.** Short headings in the Problem / Repro / Impact / Fix style (adapt names to
  the repo), tables and lists over prose, under ~200 words where possible.
- **ASCII only; no AI attribution** - same as commits, applied to titles and bodies.
- Safety rule applies: every outward action (PR, issue, comment, push) is confirmed first, and the active
  git/gh identity is verified per `safety.rule.md`.

## Code Comments + Docstrings

- Same ASCII-only, no-`--`, no-emoji rules as commit messages.
- Keep them short and casual - a quick note to the next maintainer, not a textbook. State what + why in a
  sentence; skip "Rationale:" blocks and over-explanation. Avoid backticks in comments; plain text reads
  more naturally. Add a comment where a future maintainer would otherwise ask "why is this here?".
