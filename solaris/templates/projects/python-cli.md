# Project type: python-cli <!-- omit in toc -->

- [Shape (`source/`)](#shape-source)
- [Conventions](#conventions)
- [Run](#run)
- [Notes for the planner](#notes-for-the-planner)

A standalone, publishable Python command-line tool, uv-based. Modeled on sealed-CLI conventions from
a prior framework. A description that guides `create-project` and the engineer agent.

## Shape (`source/`)

```
source/
  pyproject.toml          # own uv project; [project.scripts] <slug> = "<pkg>.cli:main"
  README.md
  <package_name>/
    __init__.py
    __main__.py           # enables `python -m <package_name>`
    cli.py                # argparse; dispatches to commands; returns an exit code
    result.py             # Result + ExitCode helpers (agent-friendly JSON envelope)
  tests/
    test_cli.py
    test_result.py
```

## Conventions

- Own uv project under `source/`. Console entry point via `[project.scripts]`.
- **Agent-friendly output:** a `--json` flag prints a `{ "ok", "result"/"error", "exit_code" }` envelope;
  without it, human-readable text. Commands return an `ExitCode`; `main()` returns it to the shell.
- Type hints; modern syntax (PEP 604 unions); Google-style docstrings kept short.
- `uv run pytest` and (if used) `uv run ruff check` are green before done.
- Apache-2.0 `LICENSE` + short per-file header if the tool will be published; `.gitignore` covering venvs
  and caches.

## Run

- `uv run <slug> <command> [args]` from `source/` (or `uv run python -m <package_name> ...`).

## Notes for the planner

Capture in `<pack>/spec.md`: the commands, their flags, the envelope shape, and whether it will be published
(license/headers). Keep the first cut trivial but clean - tests + a working `hello`-style command.
