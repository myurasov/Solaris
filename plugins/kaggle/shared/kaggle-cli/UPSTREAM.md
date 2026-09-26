_Rev. 1_

# Vendored Upstream: Kaggle CLI Agent Skill

This folder is a pristine copy of Kaggle's official agent skill for its CLI. Do not edit anything here
except this file and the rev markers the Solaris revs tool adds - Solaris-specific procedures live in
`../kaggle.skill.md`, which routes into this tree.

- **Source:** https://github.com/Kaggle/kaggle-cli - path `skills/` (Apache-2.0)
- **Mirrored ref:** tag `v2.2.4`, commit `f0afa32699d28c97f82691728ada3ed8c16c5abf` - the release the
  gateway (`../kaggle.py`) pins.

## Refresh Procedure

Move it together with the gateway pins (`PIN` and `SDK_PIN` in `../kaggle.py`), as one change:

1. Fetch the new tag:
   `git clone -q --depth 1 --branch v<X.Y.Z> --filter=blob:none --sparse https://github.com/Kaggle/kaggle-cli <scratch>/kc`,
   then `git -C <scratch>/kc sparse-checkout set skills`.
2. For each file whose content changed (`uv run -m solaris.tools.revs hash <file>` ignores the marker, so
   compare its hash on both copies): copy the new file over ours, put back our `_Rev. N_` line (line 1,
   or right after the frontmatter in `SKILL.md`), then `revs bump` it. Add new files (then `revs bump`
   them); delete removed ones (never this file - it has no upstream counterpart).
3. Update the mirrored ref above, skim the upstream diff for changes `../kaggle.skill.md` depends on (its
   Corrections list especially), `revs bump` the edited files, and run `revs ledger`.
