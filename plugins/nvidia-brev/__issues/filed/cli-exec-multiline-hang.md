# cli: brev exec hangs and never executes multi-line / heredoc payloads

**Target:** bug report (or PR) to `brevdev/brev-cli`

## Problem

`brev exec` silently fails on non-trivial payloads: the call hangs indefinitely (no output, no
timeout) and **nothing executes on the instance**. Two repro shapes, both observed on
massedcompute L40S with CLI v0.6.326 (macOS):

1. Inline multi-line command:
   ```sh
   brev exec <name> -- bash -c 'cat > ~/x.sh << "EOF"
   echo hi
   EOF
   chmod +x ~/x.sh
   echo done'
   ```
   Hangs; `~/x.sh` is never created. A later single-line `brev exec <name> -- bash -c '...'`
   on the same instance returned `exit status 255`.

2. `@file` where the script contains a heredoc + `setsid ... & disown`:
   ```sh
   brev exec <name> @launch.sh
   ```
   Hangs; the script's first statement (a heredoc write) never runs. Meanwhile stray processes
   whose command line contains the script text are visible on the instance - the payload appears
   to be passed through a shell-quoting layer that breaks on heredocs/backgrounding.

A plain **linear** `@file` script (venv setup, pip install, echo markers - no heredocs, no `&`)
works fine on the same instance.

## Impact

Agents cannot use `brev exec` to stage-and-detach long jobs (the natural pattern for training
runs); they must fall back to plain ssh against the instance endpoint.

## Suggested fix

Pass `@file` scripts to the remote shell verbatim (e.g. pipe the file to `bash -s` over the ssh
channel) instead of re-quoting the content; error out loudly on payloads the transport cannot
represent rather than hanging.

**Filed:** https://github.com/brevdev/brev-cli/issues/429 (2026-07-26)
