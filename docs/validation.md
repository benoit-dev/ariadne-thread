# Validation

These runs used a fresh empty `HERMES_HOME` for each command. The package passes on the current release (Hermes v0.22.7) and on the declared floor (Hermes v0.22.0).

## Hermes v0.22.7

```text
$ hermes --version
Hermes Agent v0.22.7 (2026.9.24) · stable · local b200f5bc60e3

$ hermes plugins validate . --install-deps
Preparing the isolated Hermes runtime…
  → Installing Python dependencies…
  ✓ Installing Python dependencies
  → Installing Python dependencies…
  ✓ Installing Python dependencies

✓ manifest — plugin.yaml parses
✓ manifest fields — name, version, description present
✓ requires_hermes — spec '>=0.22.0' parses
✓ config schema — not declared
✓ requires_env — all entries UPPER_SNAKE
✓ oauth — not declared
✓ loadable — entry: __init__.py, desktop/plugin.js
✓ python dependencies — none declared
✓ capability probe — register() ran in isolation
✓ declared tools — matches registrations
✓ declared hooks — matches registrations
✓ declared middleware — matches registrations
✓ built-in tool collisions — no tools to check
✓ security scan — safe
✓ no core override — no runtime rebinds of Hermes core
✓ desktop surface — stays inside the plugin SDK surface
◆ Isolation — runs in the plugin host (plugins.isolation: host)
  · dashboard backend API is served through the plugin host (buffered responses)

Validation passed.
(exit code 0)

$ hermes plugins doctor . --ci
Plugin Doctor: .
  manifest: ariadne-thread 0.1.0 (standalone)
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 0 tool(s), 0 hook(s)
(exit code 0)
```

## Hermes v0.22.0

```text
$ hermes --version
Hermes Agent v0.22.0 (2026.9.24) · stable · local e06df9c2eae6

$ hermes plugins validate . --install-deps
Preparing the isolated Hermes runtime…
  → Installing Python dependencies…
  ✓ Installing Python dependencies
  → Installing Python dependencies…
  ✓ Installing Python dependencies

✓ manifest — plugin.yaml parses
✓ manifest fields — name, version, description present
✓ requires_hermes — spec '>=0.22.0' parses
✓ config schema — not declared
✓ requires_env — all entries UPPER_SNAKE
✓ loadable — entry: __init__.py, desktop/plugin.js
✓ python dependencies — none declared
✓ capability probe — register() ran in isolation
✓ declared tools — matches registrations
✓ declared hooks — matches registrations
✓ declared middleware — matches registrations
✓ built-in tool collisions — no tools to check
✓ security scan — safe
✓ no core override — no runtime rebinds of Hermes core
✓ desktop surface — stays inside the plugin SDK surface
◆ Isolation — runs in the plugin host (plugins.isolation: host)
  · dashboard backend API is served through the plugin host (buffered responses)

Validation passed.
(exit code 0)

$ hermes plugins doctor . --ci
Plugin Doctor: .
  manifest: ariadne-thread 0.1.0 (standalone)
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 0 tool(s), 0 hook(s)
(exit code 0)
```

## Warnings

The final runs print no warnings. Before `__init__.py` existed, `validate` warned `no __init__.py — capability probe skipped (manifest-only plugin)`, and `doctor --ci` reported `ERROR: Plugin registration failed: No __init__.py`. The empty `register(ctx)` in `__init__.py` fixes both.

## Tests

```text
$ python -m pytest -q tests

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
47 passed, 1 warning in 1.33s
(exit code 0)
```

## API against an empty HERMES_HOME

This run mounts the router in a test app and calls each endpoint with an empty home folder.

```text
GET /health -> 200 {"ok":true,"name":"ariadne-thread","version":"0.1.0","sources":{"crons":false,"webhooks":false,"skills_root":false}}
GET /profiles -> 200 {"profiles":["default"]}
GET /crons -> 200 {"jobs":[],"total":0,"categories":{},"updated_at":""}
GET /webhooks -> 200 {"routes":[]}
GET /skills -> 200 {"skills":[],"count":0}
GET /skills/missing -> 404 {"detail":"skill not found: missing"}
GET /crons?profile=nope -> 404 {"detail":"profile not found: nope"}
GET /crons?profile=Bad! -> 400 {"detail":"invalid profile name: 'Bad!'"}
```
