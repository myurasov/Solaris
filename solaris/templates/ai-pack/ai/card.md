# {{NAME}} - Hard Rules Card

The few rules a session on {{NAME}} must have in mind after every compaction, and nothing else. Under a
Solaris checkout, the `read_first` hook loads this card whole at every session start and after every
compaction, ahead of the pack's other rules. Keep it short, at most about 3 KB: one line per rule, ending
with its registry tag (`<!-- ID -->`) where it has one. Everything else - procedures, conventions, lessons
and the longer standing rules - lives in `{{PACK}}/instructions.md`, which `{{PRIMARY}}.agent.md` loads.

Replace these examples with the project's own:

- Never paste secrets, tokens or credentials into chat, logs, reports or commits.
- Ask the owner before anything that spends money, deletes data or publishes outside this machine.
- Run the tests before every commit, and never commit a failing build.
