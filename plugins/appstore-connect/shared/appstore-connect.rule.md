_Rev. 1_

# Rule: App Store Connect (Always-On) <!-- omit in toc -->

Always-on conventions for any project that talks to App Store Connect / developer.apple.com.
The how-to lives in this plugin's skills; this rule is what must hold even when no skill has
been triggered.

- **API-first routing.** ASC chores default to the REST API path
  (`asc-api.skill.md`); the browser (`browserctl.asc.skill.md`) is only for flows the API
  cannot reach (App Privacy, DSA trader status, agreements, IAP setup, API-key creation,
  visual checks). Do not hand-click what a PATCH can do.
- **Every mutating ASC / developer-portal action is outward** - the project safety rule
  applies in full: confirm first unless a standing autonomy grant covers the task; genuinely
  irreversible steps (Submit for Review, publishing App Privacy, agreements, trader status)
  get a one-line heads-up even under a grant.
- **Secrets and identity discipline.** The Apple web session lives only in a browserctl
  profile (never launched outside browserctl, never exported); API keys, app/version ids,
  team ids, and account details live in `ai/.memory/` - never in shareable files, logs, or
  commits. Reference secrets by name, never by value.
- **Verify by re-reading, never by the click/call.** After any mutation, confirm the
  persisted state (fresh snapshot or GET) before reporting it done - ASC fails silently in
  both the UI and some API flows.
- **Living documents.** New field-verified App Store lessons are folded into the matching
  skill of this plugin the same session they are learned (see the skills' Maintenance
  sections).
