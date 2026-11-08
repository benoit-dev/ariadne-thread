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
⚠ private core imports (catalog rule 9: public surfaces only; these can break on
any release): dashboard/plugin_api.py:1364 
hermes_cli.web_server_profiles._config_profile_scope; 
dashboard/plugin_api.py:1396 cron.scheduler_delivery; 
dashboard/plugin_api.py:1400 gateway.channel_directory.load_directory; 
dashboard/plugin_api.py:1436 cron.scheduler_delivery.cron_delivery_targets; 
dashboard/plugin_api.py:1580 hermes_cli.webhook.SubscriptionMutationConflict; 
dashboard/plugin_api.py:1540 cron.jobs; dashboard/plugin_api.py:1558 cron.jobs; 
dashboard/plugin_api.py:1580 hermes_cli.webhook._load_subscriptions; 
dashboard/plugin_api.py:1580 hermes_cli.webhook._replace_subscription. Use ctx.*
/ documented plugin APIs, or open an issue for the seam you need.
◆ Isolation — runs in the plugin host (plugins.isolation: host)
  · dashboard backend API is served through the plugin host (buffered responses)

Validation passed.
(exit code 0)

$ hermes plugins doctor . --ci
Plugin Doctor: .
  manifest: ariadne-thread 0.2.0 (standalone)
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 0 tool(s), 0 hook(s)
(exit code 0)
```

## Hermes v0.22.0

The v0.22.0 checkout ran on the v0.22.7 virtual environment's Python, which has the dependencies installed.

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
any release): dashboard/plugin_api.py:1364 
hermes_cli.web_server_profiles._config_profile_scope; 
dashboard/plugin_api.py:1396 cron.scheduler_delivery; 
dashboard/plugin_api.py:1400 gateway.channel_directory.load_directory; 
dashboard/plugin_api.py:1436 cron.scheduler_delivery.cron_delivery_targets; 
dashboard/plugin_api.py:1580 hermes_cli.webhook.SubscriptionMutationConflict; 
dashboard/plugin_api.py:1540 cron.jobs; dashboard/plugin_api.py:1558 cron.jobs; 
dashboard/plugin_api.py:1580 hermes_cli.webhook._load_subscriptions; 
dashboard/plugin_api.py:1580 hermes_cli.webhook._replace_subscription. Use ctx.*
/ documented plugin APIs, or open an issue for the seam you need.
◆ Isolation — runs in the plugin host (plugins.isolation: host)
  · dashboard backend API is served through the plugin host (buffered responses)

Validation passed.
(exit code 0)

$ hermes plugins doctor . --ci
Plugin Doctor: .
  manifest: ariadne-thread 0.2.0 (standalone)
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 0 tool(s), 0 hook(s)
(exit code 0)
```

## Warnings

`validate` passes on both versions and prints one warning: catalog rule 9, private core imports. It lists `cron.jobs`, `cron.scheduler_delivery`, `gateway.channel_directory`, `hermes_cli.web_server_profiles._config_profile_scope` and three `hermes_cli.webhook` functions. The same warning, with the same imports, appears on 0.1.0 at `origin/main` `9f4ca3d`; 0.2.0 adds no new core import. [risk-audit.md](risk-audit.md#core-imports) explains why each one is needed.

Before `__init__.py` existed, `validate` warned `no __init__.py — capability probe skipped (manifest-only plugin)`, and `doctor --ci` reported `ERROR: Plugin registration failed: No __init__.py`. The empty `register(ctx)` in `__init__.py` fixes both.

## Tests

```text
$ python -m pytest -q tests          # Hermes v0.22.7
157 passed, 1 warning in 10.18s
(exit code 0)

$ PYTHONPATH=<v0.22.0 checkout> python -m pytest -q tests          # Hermes v0.22.0 modules
157 passed, 1 warning in 10.12s
(exit code 0)
```

The one warning is Starlette's deprecation notice for `httpx` in its test client.

## API against an empty HERMES_HOME

This run mounts the router in a test app and calls each endpoint with an empty home folder.

```text
GET /health -> 200 {"ok":true,"name":"ariadne-thread","version":"0.2.0","sources":{"crons":false,"webhooks":false,"skills_root":false}}
GET /profiles -> 200 {"profiles":["default"]}
GET /crons -> 200 {"jobs":[],"total":0,"categories":{},"updated_at":""}
GET /webhooks -> 200 {"routes":[]}
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
GET /history/version?kind=skill&ref=demo&id=../x -> 400 {"detail":"invalid history id: '../x'"}
POST /history/revert -> 404 {"detail":"no saved version 00000000000000000001"}
```
