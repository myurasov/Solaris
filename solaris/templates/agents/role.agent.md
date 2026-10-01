---
description: One line on when to use this persona - a delegator picks it by this line.
tier: mid
access: full
---

<!-- Role brief for `<role>` - lives beside the primary persona as `<pack>/<role>.agent.md`; project content:
     no _Rev. marker, never re-rendered by Solaris. Frontmatter: description (required); tier
     cheap|mid|high|frontier (the model tier to run it at, names in <pack>/info/model-tiers.md); access
     read-only|full. Use it by telling a model to act as this file. It inherits the primary persona's
     commit, safety, memory, and interaction policies, and shares the pack's one `<pack>/instructions.md`
     (know-how) and `<pack>/.memory/` (short-term, machine-local state) - a role has no store of its own. -->

**Owns:** what this persona is responsible for delivering.

**Works from:** `instructions.md` (the shared know-how, read on start - its `## <Role> ...` section if the
role has one) plus the inputs it reads (spec sections, ledgers, the brief it receives from the primary
persona).

**Returns:** the shape of its report (named facts, file:line pointers, a verdict, a table - never raw dumps),
plus any lesson worth keeping - written into `instructions.md`, or handed back in the report when running
without write access.

**Never:** what it hands back instead of doing (outward or remote-mutating actions, anything outside its access).
