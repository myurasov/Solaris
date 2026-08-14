_Rev. 3_

# Rule: Git Collaboration (Developer Branches) <!-- omit in toc -->

- [Switches](#switches)
- [Branch Guard (Before Every Commit)](#branch-guard-before-every-commit)
- [Commits and Pushes](#commits-and-pushes)
- [Back-Contribution (PR on Request)](#back-contribution-pr-on-request)
- [Feature Branches](#feature-branches)
- [Main-Developer Mode](#main-developer-mode)

Always-on git workflow for collaborating on this project: every developer works on a personal
branch, commits happen automatically there, nothing is ever pushed without an explicit ask, and
contributions return to `main` as pull requests. Standalone-first: needs nothing beyond this pack
and git itself (`gh` is optional - every step has a fallback).

## Switches

Read `ai/defaults.json` (committed team default), then `ai/.memory/config.json` (private
per-machine override; wins per key):

- `"git.developer_branches"` - `true`/`false`; absent means **true**. `false` =
  [main-developer mode](#main-developer-mode).
- `"git.feature_branches"` - `true`/`false`; absent means **true**.

Private state this rule caches in `ai/.memory/config.json`: `"git.branch"` (the developer's
personal branch name), `"git.feature_origin"` (the branch an open feature branched from).

## Branch Guard (Before Every Commit)

When `developer_branches` is on and HEAD is `main` or `develop` (or the repo's default branch,
when it is named differently, e.g. `master`), switch to the personal branch **before**
committing - create it if it does not exist. Never commit directly to those branches; the one
exception is the closing merge commit of a feature whose recorded origin is such a branch
(main-developer mode - see Feature Branches).

`"git.branch"` caches the **full personal branch name**. When it is unset, derive the name as
`<id>-develop`, where `<id>` is the first of these that resolves (slugified: lowercase,
non-alphanumerics to `-`):

1. GitHub login: `gh api user --jq .login` (only if `gh` is installed and authenticated);
2. the local-part of `git config user.email` (e.g. `dfinkel@example.com` -> `dfinkel`);
3. `git config user.name` slug.

Cache the derived name as `"git.branch"` and the email it was derived under as
`"git.branch_email"`; re-derive when the current `git config user.email` no longer matches
(shared checkouts). Uncommitted work carries over on the switch (`git switch -c` keeps the
working tree); nothing is lost.

Any **other** branch is outside this guard: on a colleague's branch, a review checkout, or a
detached HEAD, do not auto-commit - keep the standard confirmation posture and confirm where the
commit should land.

## Commits and Pushes

- On **your own** personal branch (the `"git.branch"` one) or a feature branch opened by this
  rule, **commits are automatic**: commit incrementally per the Commit Policy's format rules
  (single-line, imperative, ASCII, atomic) without per-message confirmation - the branch is
  private until an explicit publish, so committing is safe.
- **Pushes are NEVER automatic**, in any mode. No `git push`, no PR, no publishing of any kind
  unless the user explicitly asks.

## Back-Contribution (PR on Request)

Only on an explicit ask - "create a PR", "publish", "push upstream", "contribute back", "open a
merge request" - and never as a side effect:

1. Verify the publishing identity first - `git config user.email` (and `gh auth status` when
   using `gh`) must match the developer publishing; on machines with several git/GitHub
   identities the active one drifts. Then push the personal branch to the remote.
2. Open a PR against the repo's `main` (or its differently-named default branch):
   `gh pr create --base main` with a title/body following the Commit Policy. Without `gh`: push, then hand the user the ready-to-open compare URL
   (`https://github.com/<owner>/<repo>/compare/main...<branch>?expand=1`, or the forge's
   equivalent) - GitHub also prints a create-PR link in the push output; surface it.
3. If even the push fails (no remote access), report exactly that and stop - never work around
   missing permissions.

## Feature Branches

When `feature_branches` is on and the user asks for a distinct new feature: branch
`feature-<descr-slug>` from the current branch, record the originating branch as
`"git.feature_origin"`, and commit the feature's work there (automatically, per above). When
HEAD is `main`/`develop` at that moment and `developer_branches` is on, the branch guard applies
first: switch to the personal branch and record **it** as the origin (features merge back into
`main` directly only in main-developer mode). When the
user says the feature is done: `git merge --no-ff` back into the originating branch (one merge
commit, message per the Commit Policy), delete the feature branch, clear `"git.feature_origin"`.
The closing merge is automatic when the origin is your own personal branch (or `main` in
main-developer mode); onto any other origin (e.g. a colleague's branch) confirm it first.
If a feature branch is already open when a new feature starts, ask whether to finish, stack, or
abandon it first.

## Main-Developer Mode

`"git.developer_branches": false` (typically set in the project owner's private
`ai/.memory/config.json`) disables the branch guard: working directly on `main` is fine, and the
Commit Policy's standard confirmation posture applies to commits made **directly on `main`**.
Feature branches (if enabled) still apply and keep their automatic posture - including the
closing merge commit onto the originating branch, which only lands work already committed. The
explicit-push rule holds for everyone: pushes are never automatic.
