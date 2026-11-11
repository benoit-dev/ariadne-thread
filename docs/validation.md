# Validation

These runs checked 0.2.2 with a fresh empty `HERMES_HOME` for each command. The package passes on the current release (Hermes v0.23.0) and on the declared floor (Hermes v0.22.0).

## Hermes v0.23.0

```text
$ hermes --version
Hermes Agent v0.23.0 (2026.9.24) · stable · local 2bd378ed1b8d

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
⚠ private core imports (catalog rule 9: public surfaces only; these can break on
any release): dashboard/plugin_api.py:1352 cron.jobs; 
dashboard/plugin_api.py:1370 cron.jobs. Use ctx.* / documented plugin APIs, or 
open an issue for the seam you need.
⚠ desktop surface review (catalog rule 8: SDK surface only): raw bridge 
window.hermesDesktop (desktop/plugin.js:111). Use ctx.files / ctx.os / 
host.request and SDK slots, or open an issue for the hook you need.
◆ Isolation — runs in the plugin host (plugins.isolation: host)
  · dashboard backend API is served through the plugin host (buffered responses)

Validation passed.
(exit code 0)

$ hermes plugins doctor --ci .
Plugin Doctor: .
  manifest: ariadne-thread 0.2.2 (standalone)
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
⚠ private core imports (catalog rule 9: public surfaces only; these can break on
any release): dashboard/plugin_api.py:1352 cron.jobs; 
dashboard/plugin_api.py:1370 cron.jobs. Use ctx.* / documented plugin APIs, or 
open an issue for the seam you need.
◆ Isolation — runs in the plugin host (plugins.isolation: host)
  · dashboard backend API is served through the plugin host (buffered responses)

Validation passed.
(exit code 0)

$ hermes plugins doctor --ci .
Plugin Doctor: .
  manifest: ariadne-thread 0.2.2 (standalone)
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 0 tool(s), 0 hook(s)
(exit code 0)
```

## Warnings

`validate` passes on both versions. Two warnings remain:

- **Rule 9, private core imports:** only `cron.jobs`, for its public `get_job` and `update_job`, which the cron prompt and delivery edits call. 0.2.1 also imported `cron.scheduler_delivery`, `gateway.channel_directory`, `hermes_cli.web_server_profiles._config_profile_scope` and three `hermes_cli.webhook` functions. 0.2.2 drops all of them (see [risk-audit.md](risk-audit.md#changes-from-the-catalog-review-022)).
- **Rule 8, raw bridge `window.hermesDesktop`** (v0.23.0 only; v0.22.0 does not check for it): `useCore` in `desktop/plugin.js` reads Hermes' own `GET /api/webhooks` and `GET /api/cron/delivery-targets` through `window.hermesDesktop.api`, because `ctx.rest` only reaches the plugin's own namespace. Both calls are read-only.

## Tests

```text
$ python -m pytest -q tests          # Hermes v0.23.0
148 passed, 1 warning
(exit code 0)

$ PYTHONPATH=<v0.22.0 checkout> python -m pytest -q tests          # Hermes v0.22.0 modules
148 passed, 1 warning
(exit code 0)
```

The one warning is Starlette's deprecation notice for `httpx` in its test client. 0.2.1 had 157 tests; the ones for the removed webhook and delivery endpoints were replaced by tests that the backend never reads or writes `webhook_subscriptions.json` and that a deliver step carries its stored targets.

## API against an empty HERMES_HOME

This run mounts the router in a test app and calls each endpoint with an empty home folder.

```text
GET /health -> 200 {"ok":true,"name":"ariadne-thread","version":"0.2.2","sources":{"crons":false,"skills_root":false}}
GET /profiles -> 200 {"profiles":["default"],"serving":"default"}
GET /crons -> 200 {"jobs":[],"total":0,"categories":{},"updated_at":""}
GET /skills -> 200 {"skills":[],"count":0}
GET /skills/missing -> 404 {"detail":"skill not found: missing"}
GET /crons?profile=nope -> 404 {"detail":"profile not found: nope"}
GET /crons?profile=Bad! -> 400 {"detail":"invalid profile name: 'Bad!'"}
GET /crons/job1/runs -> 200 {"job_id":"job1","runs":[],"total":0}
GET /crons/job1/runs/2026-11-08_09-00-00 -> 404 {"detail":"run not found: 2026-11-08_09-00-00"}
GET /crons/%2E%2E/runs -> 400 {"detail":"invalid job id: '..'"}
GET /crons/job1/runs/latest -> 400 {"detail":"invalid run id: 'latest'"}
GET /history?kind=skill&ref=demo -> 200 {"kind":"skill","ref":"demo","versions":[]}
GET /history?kind=bogus&ref=demo -> 400 {"detail":"unknown history kind: 'bogus'"}
GET /history?kind=webhook-prompt&ref=demo -> 400 {"detail":"unknown history kind: 'webhook-prompt'"}
GET /history/version?kind=skill&ref=demo&id=../x -> 400 {"detail":"invalid history id: '../x'"}
GET /webhooks -> 404 {"detail":"Not Found"}
GET /crons/job1/delivery -> 404 {"detail":"Not Found"}
POST /history/revert -> 404 {"detail":"no saved version 00000000000000000001"}
```
