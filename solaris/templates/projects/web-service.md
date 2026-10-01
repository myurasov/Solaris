_Rev. 1_

# Project type: web-service <!-- omit in toc -->

- [Shape (`source/`)](#shape-source)
- [Conventions](#conventions)
- [Deploy / run (local mode)](#deploy--run-local-mode)
- [Notes for the planner](#notes-for-the-planner)

A small HTTP service with a UI. Default stack for v0: **FastAPI** (JSON API) + a **single-page vanilla
HTML/JS** UI served as static files, run with **uvicorn**. This is a description that guides
`create-project` and the engineer agent - not a literal scaffold.

## Shape (`source/`)

```
source/
  pyproject.toml        # own uv project (fastapi, uvicorn[standard]); separate from Solaris's venv
  app/
    __init__.py
    main.py             # FastAPI app: JSON API + StaticFiles mount for the UI
    models.py           # request/response + storage models
  static/
    index.html          # single-page UI
    app.js              # vanilla JS calling the API (fetch)
    style.css
  tests/
    test_api.py         # pytest against the API (TestClient)
  README.md
```

## Conventions

- Own uv project under `source/` (`requires-python` per the app's needs; Solaris's `.venv` is separate).
- Run locally: `uv run uvicorn app.main:app --reload` from `source/`.
- API returns JSON; the UI is static and talks to the API via `fetch`. Keep storage simple for v0
  (in-memory or a single SQLite file) unless the spec says otherwise.
- Tests use FastAPI's `TestClient`; `uv run pytest` from `source/` is green before declaring done.

## Deploy / run (local mode)

- rsync `source/` to the deploy host in `<pack>/.memory/resources.md` (exclude `.venv`, `.git`, `__pycache__`;
  no `--delete` by default), then run uvicorn over `ssh`. Optional: a Dockerfile + `docker run` if the spec
  wants a container.

## Notes for the planner

Capture in `<pack>/spec.md`: the resource(s) the API exposes, the data model, the UI's screens/actions, the
storage choice, and the deploy target. Keep v0 minimal and runnable end to end.
