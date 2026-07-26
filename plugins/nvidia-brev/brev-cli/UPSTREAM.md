# Vendored Upstream: brev-cli agent skill

This directory is a **pristine local mirror** of NVIDIA's canonical Brev CLI agent files.
Do not edit anything here except this file - Solaris-specific procedures live in the
plugin's `shared/` skills, which reference this tree.

- **Source:** https://github.com/brevdev/brev-cli - path `.agents/skills/brev-cli/`
- **Raw base (what `brev agent-skill install` fetches):**
  `https://raw.githubusercontent.com/brevdev/brev-cli/<ref>/.agents/skills/brev-cli/`
- **Mirrored ref:** commit `6b111e1f041d354d682541cc489375431f195fcf` (main, 2026-07-25)

## Refresh procedure

```sh
git clone --depth 1 --filter=blob:none --sparse https://github.com/brevdev/brev-cli /tmp/brev-cli-upstream
git -C /tmp/brev-cli-upstream sparse-checkout set .agents/skills/brev-cli
rsync -a --delete --exclude UPSTREAM.md /tmp/brev-cli-upstream/.agents/skills/brev-cli/ plugins/brev/brev-cli/
```

Then update the mirrored ref above (`git -C /tmp/brev-cli-upstream rev-parse HEAD`) and
skim the diff for changes that affect `shared/brev-setup.skill.md` / `shared/brev-run.skill.md`.
