---
description: One line on when to use this persona - a delegator picks it by this line.
tier: mid
access: full
---

<!-- Role brief for `<role>` - project content: no _Rev. marker, never re-rendered by Solaris.
     Frontmatter: description (required); tier cheap|mid|high|frontier (the model tier to run it at,
     names in ai/info/model-tiers.md); access read-only|full. Use it by telling a model to act as this
     file. It inherits the primary persona's commit, safety, memory, and interaction policies. -->

**Owns:** what this persona is responsible for delivering.

**Works from:** the inputs it reads (spec sections, ledgers, the brief it receives from the primary persona).

**Returns:** the shape of its report (named facts, file:line pointers, a verdict, a table - never raw dumps).

**Never:** what it hands back instead of doing (outward or remote-mutating actions, anything outside its access).
