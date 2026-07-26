_Rev. 1_

# nvidia-brev - Solaris Plugin

Gives a project's engineer agent the ability to run workloads on [NVIDIA Brev](https://developer.nvidia.com/brev)
cloud GPU instances **completely autonomously**: from installing the CLI and getting the user
registered, through instance sizing/creation/provisioning/payload shipping/monitoring, to the
non-negotiable teardown and cost ledger entry.

## Contents

- `shared/brev-setup.skill.md` - install the `brev` CLI, guide account registration, log in
  (browser flow; no secrets in chat), pick the org, verify access.
- `shared/brev-run.skill.md` - the full run lifecycle: size and create an instance, ship the
  payload (direct datacenter-to-datacenter push preferred), set up, launch detached, monitor,
  collect results, ALWAYS tear down, and append the mandatory row to `ai/.memory/brev-costs.md`.
  Includes field gotchas learned on real runs (Blackwell torch matrices, `pkill -f` self-match,
  `brev exec` detach semantics, provider quirks).
- `brev-cli/` - **pristine mirror** of the upstream NVIDIA brev-cli agent files
  (SKILL.md, INSTALLATION.md, prompts/, reference/, examples/) from
  `github.com/brevdev/brev-cli` `.agents/skills/brev-cli/`. Never edited locally;
  refresh procedure in `brev-cli/UPSTREAM.md`. The shared skills cite it for deep CLI
  reference (command/flag details, search filters, canned prompts).

## Layout note

`brev-cli/` at the plugin top level deviates from the flat-plugin rule (precedent:
`visual-qa/serving/`). Only `shared/` is materialized into a project on copy-install; the
mirror stays with the plugin source, and the shared skills degrade gracefully when it is
not resolvable (they carry the essentials inline).

## Status

Developed against the `nv-penske-avi-poc` project (link mode). Intended for contribution to
core Solaris once polished.
