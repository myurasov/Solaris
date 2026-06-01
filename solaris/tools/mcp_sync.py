"""Detect/sync divergence between the two runtime MCP configs (stdlib only).

Claude Code reads ``.mcp.json``; Cursor reads ``.cursor/mcp.json``. They are meant to carry identical
``mcpServers`` content. This tool reports drift (``--check``, the default) or writes both from one source
(``--sync``). The committed ``mcp.json.example`` is the starting template.

Run::

    uv run -m solaris.tools.mcp_sync                  # check the repo root
    uv run -m solaris.tools.mcp_sync --dir projects/todo --check
    uv run -m solaris.tools.mcp_sync --sync           # write both from .mcp.json (or example if absent)
    uv run -m solaris.tools.mcp_sync --sync --from cursor
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _paths(base: "str | Path") -> tuple[Path, Path, Path]:
    base = Path(base)
    return base / ".mcp.json", base / ".cursor" / "mcp.json", base / "mcp.json.example"


def _load(path: Path) -> "dict | None":
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"mcp_sync: {path} is not valid JSON: {exc}")
    return data if isinstance(data, dict) else {"mcpServers": {}}


def servers(doc: "dict | None") -> dict:
    """The comparable payload: the mcpServers map (ignores _comment and formatting)."""
    return (doc or {}).get("mcpServers", {})


def cmd_check(base: "str | Path") -> int:
    claude, cursor, _ = _paths(base)
    c_doc, u_doc = _load(claude), _load(cursor)
    if c_doc is None and u_doc is None:
        print("neither .mcp.json nor .cursor/mcp.json exists - copy mcp.json.example to both (or run --sync)")
        return 1
    if c_doc is None:
        print(".mcp.json is missing while .cursor/mcp.json exists; run --sync --from cursor")
        return 1
    if u_doc is None:
        print(".cursor/mcp.json is missing while .mcp.json exists; run --sync --from claude")
        return 1
    if servers(c_doc) == servers(u_doc):
        print(f"in sync ({len(servers(c_doc))} server(s))")
        return 0
    only_claude = sorted(set(servers(c_doc)) - set(servers(u_doc)))
    only_cursor = sorted(set(servers(u_doc)) - set(servers(c_doc)))
    changed = sorted(
        name for name in set(servers(c_doc)) & set(servers(u_doc))
        if servers(c_doc)[name] != servers(u_doc)[name]
    )
    print("DRIFT between .mcp.json and .cursor/mcp.json:")
    if only_claude:
        print(f"  only in .mcp.json:        {', '.join(only_claude)}")
    if only_cursor:
        print(f"  only in .cursor/mcp.json: {', '.join(only_cursor)}")
    if changed:
        print(f"  differing config:         {', '.join(changed)}")
    print("  fix with: uv run -m solaris.tools.mcp_sync --sync [--from claude|cursor|example]")
    return 1


def cmd_sync(base: "str | Path", source: str) -> int:
    claude, cursor, example = _paths(base)
    chosen = {"claude": claude, "cursor": cursor, "example": example}[source]
    doc = _load(chosen)
    if doc is None:
        raise SystemExit(f"mcp_sync: source {chosen} does not exist")
    runtime = json.dumps({"mcpServers": servers(doc)}, indent=2) + "\n"
    for dst in (claude, cursor):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(runtime, encoding="utf-8")
    print(f"synced .mcp.json and .cursor/mcp.json from {source} ({len(servers(doc))} server(s))")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="solaris.tools.mcp_sync", description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=str(REPO_ROOT), help="directory holding .mcp.json + .cursor/ (default: repo root)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report drift (default)")
    mode.add_argument("--sync", action="store_true", help="write both runtime files from --from")
    parser.add_argument("--from", dest="source", choices=["claude", "cursor", "example"], default=None,
                        help="sync source; default = .mcp.json if present, else mcp.json.example")
    args = parser.parse_args(argv)

    if args.sync:
        source = args.source
        if source is None:
            claude, _, _ = _paths(args.dir)
            source = "claude" if claude.exists() else "example"
        return cmd_sync(args.dir, source)
    return cmd_check(args.dir)


if __name__ == "__main__":
    raise SystemExit(main())
