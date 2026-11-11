# Risk audit

This audit covers catalog rules 8 (Desktop SDK only), 11 (credentials), 12 (approvals) and 13 (disclosure). It lists every file write, network call, subprocess, and agent session in the source plugin and in Ariadne's Thread 0.2.2. The findings table records what changed from the source to 0.1.0, and the catalog review table what changed in 0.2.2. The sections after them describe 0.2.2.

"Source" means the personal plugin this package was ported from, at its HEAD commit `93d0c60`. Line numbers on the left refer to that tree.

## Findings in the source and what changed

| Source location | Behaviour | Rule | Action in 0.1.0 |
|---|---|---|---|
| `dashboard/plugin_api.py:91-93` | Imports `tui_gateway.server` and calls the private `_broadcast_global_event` | 9 | Replaced with the public `hermes_cli.plugin_events.broadcast_plugin_event`. The event is now `plugin.ariadne-thread.skills.changed`. |
| `dashboard/plugin_api.py:493-499` | Writes `SKILL.md` through a temp file and `os.replace` | 13 | Kept, scoped to the active profile, and disclosed in the README. Other profiles and symlinked shared skills are read-only (HTTP 403). The file mode is preserved. |
| `dashboard/plugin_api.py:74` | Hard-coded webhook URL `http://127.0.0.1:8644/webhooks/` | none | Removed. The UI shows the route path `/webhooks/<name>` because the port is user-configured. |
| `dashboard/plugin_api.py:67-70` | Paths fixed at import time | none | Paths now resolve per request with `hermes_constants.get_hermes_home()`, so a profile-scoped plugin host reads the right profile. |
| `desktop/plugin.js:500-511` | "Ask an agent" calls `session.create` and then `prompt.submit`. This starts an agent turn the user never reviewed, tells it to write the file, and pins `profile: 'default'`. | 12 | Removed. The button now opens a new chat with a pre-filled draft (`host.navigate('/')` and `host.composer.setDraft('new', …)`). The user reads it and sends it. Hermes approvals apply to that turn as to any chat. If the draft cannot be placed, the prompt goes to the clipboard. |
| `desktop/plugin.js:497` | The seeded prompt asked the agent to reply in French | none | The prompt is English. |
| `dashboard/plugin_api.py:143-168` | Project classifier with personal project names | none | Removed. Crons are grouped by the category folder of their first attached skill. |

The source had no subprocess or shell calls and no outbound network calls. It read no credentials.

## Changes from the catalog review (0.2.2)

| 0.2.1 location | Behaviour | Rule | Action in 0.2.2 |
|---|---|---|---|
| `dashboard/plugin_api.py:949`, `:1620-1626` | Read `webhook_subscriptions.json`, which holds every route's HMAC secret | 9, 11 | Removed. The Webhooks tab and **Used by** read Hermes' `GET /api/webhooks`, which returns only `secret_set`. The backend has no webhook endpoint. |
| `dashboard/plugin_api.py:1576-1592` | Rewrote a webhook prompt through the private `hermes_cli.webhook._load_subscriptions` / `_replace_subscription` | 9 | Removed, with its history kind. A webhook prompt is read-only; **Ask agent** drafts the change for a chat. It can come back once Hermes has a route to change one field of a webhook. |
| `dashboard/plugin_api.py:1360-1368` | Entered another profile's secret scope through the private `_config_profile_scope` | 9, 11 | Removed. The plugin enters no secret scope. |
| `dashboard/plugin_api.py:1393-1438` | Resolved delivery targets with private `cron.scheduler_delivery._*` helpers and `gateway.channel_directory` | 9 | Removed, with `GET /crons/{id}/delivery`. The deliver step shows the stored `deliver` text, and which platforms have a home channel comes from Hermes' `GET /api/cron/delivery-targets`. |

## Behaviour of 0.2.2

### Filesystem reads

Reads stay inside Hermes's own home, plus any skill folders that the user lists under `skills.external_dirs` in `config.yaml`. The only data the plugin keeps is its edit history in `<home>/plugin-data/ariadne-thread/history/`, the per-plugin folder Hermes defines in `plugins/plugin_storage.py`.

| Location | Reads |
|---|---|
| `dashboard/plugin_api.py:843`, `:931`, `:1285` | `<home>/cron/jobs.json`. The job payload shows only the keys in `_CONFIG_KEYS` (`:802`); `base_url` is never returned, and `_looks_like_credential` (`:811-814`) drops any config value that looks like a key, a token, or a URL with a password. |
| `dashboard/plugin_api.py:186`, `:970`, `:987` | Every `SKILL.md` under `<home>/skills/` and under each external skill folder, following symlinked folders |
| `dashboard/plugin_api.py:1236-1279` | One file under `<home>/scripts/`, when the user clicks a script step. Paths resolve the way Hermes resolves cron and webhook scripts; anything that resolves outside `<home>/scripts/` (`..`, absolute paths, symlinks) gets HTTP 403. At most 256 KB is returned. |
| `dashboard/plugin_api.py:1300-1345` | Run output files in `<home>/cron/output/<job id>/`, when the user opens **Runs**. The job id and run id must match fixed patterns (HTTP 400 otherwise, so no `..` or separators). The listing takes only regular files named `YYYY-MM-DD_HH-MM-SS.md` and skips symlinks. A read that resolves outside the job's folder gets HTTP 403. At most 256 KB is returned. Works for every profile and never writes. |
| `dashboard/plugin_api.py:1453-1490` | The plugin's own history files under `<home>/plugin-data/ariadne-thread/history/`, when the user opens **History**. Reads create no folders. |
| `dashboard/plugin_api.py:130-138` | `<home>/config.yaml`, for the `skills.external_dirs` list only. Entries expand `~` and `${VAR}` the way Hermes does. |
| `dashboard/plugin_api.py:115`, `:1506-1519` | The list of folders in `<hermes root>/profiles/`, the name of the profile this backend serves, and the sources above for a profile picked with `?profile=` |

### Filesystem writes

| Location | Writes | Guard |
|---|---|---|
| `dashboard/plugin_api.py:1143` (through `_atomic_write` at `:1044`) | One `SKILL.md` that the user edited, whole or the lines of one flow step, by temp file and rename in the same folder | Active profile only. Skills from external folders are read-only. The real path must be inside `<home>/skills/`. The resulting file must pass the same frontmatter checks as Hermes's skill manager. |
| `dashboard/plugin_api.py:1279` | One script under `<home>/scripts/` that the user edited, by temp file and rename, keeping its mode | Active profile only. Same path rules as the script read: anything resolving outside `<home>/scripts/` gets HTTP 403. Scripts over 256 KB or not UTF-8 are refused. |
| `dashboard/plugin_api.py:1361` | The `prompt` of one cron job, through `cron.jobs.update_job` | Active profile only. Hermes takes its cron store lock, so the write cannot interleave with the scheduler. |
| `dashboard/plugin_api.py:1382` | The `deliver` of one cron job, through `cron.jobs.update_job` | Same as the cron prompt. |
| `dashboard/plugin_api.py:1070-1106`, called at `:1142`, `:1278`, `:1359`, `:1380` | Before each write above, one history file `<home>/plugin-data/ariadne-thread/history/<kind>/<sha256 of the target, 24 hex>/<20-digit id>.json` holding the replaced text, then deletion of all but the newest 10 files in that folder | Active profile only. Written after the conflict check and before the target, by temp file and rename, mode `0600`, folders `0700`. Nothing is recorded when the text is unchanged. If the history file cannot be written, the save answers HTTP 500 and the target is not touched. |
| `dashboard/plugin_api.py:1495-1503` | A revert writes a stored version back by calling the matching handler above (`_HISTORY_KINDS`, `:1416`) | Active profile only (HTTP 403 otherwise). It carries the text the user previewed as `expected`, so the same HTTP 409 check applies, and it records the text it replaces like any save. |

Every write carries the text the user started editing from and is refused with HTTP 409 if the file no longer matches. Nothing is written without a **Save** or **Revert** click.

#### Core imports

Hermes has no public plugin seam for editing a cron job's prompt or delivery (`cron.manage` lists, adds, removes, pauses and resumes only). The two cron writes therefore import `cron.jobs` and call its public `get_job` and `update_job`. That is the only core import `hermes plugins validate` lists as a rule 9 warning. If a later Hermes changes it, those two saves fail with an error in the UI; reads are unaffected.

### Network

| Location | Call |
|---|---|
| `dashboard/plugin_api.py:1185` | `broadcast_plugin_event` pushes `plugin.ariadne-thread.skills.changed` to connected Desktop windows. This is local and goes through Hermes. |
| `desktop/plugin.js` (all `ctx.rest` calls) | The plugin's own API at `/api/plugins/ariadne-thread/` through the Desktop SDK |
| `desktop/plugin.js:103-117` (`useCore`) | Hermes' own `GET /api/webhooks` and `GET /api/cron/delivery-targets`, through the Desktop bridge `window.hermesDesktop.api`, always with an explicit `?profile=`. Read-only GETs to the same Hermes backend. |

The plugin calls no third-party service and sends no telemetry.

### Subprocesses, shell, and agent sessions

There are none. The plugin does not call `subprocess`, `os.system`, `session.create`, `prompt.submit`, or `composer.submit`. It cannot run, pause, or resume a cron job; **Runs** only reads output files that Hermes already wrote.

### Desktop SDK use

`desktop/plugin.js` imports only `@hermes/plugin-sdk`, `react`, and `react/jsx-runtime`. It uses `ctx.rest`, `ctx.onEvent`, `ctx.setTimeout`, `ctx.addEventListener`, `ctx.os.writeClipboard`, `host.navigate`, `host.composer.setDraft`, and `host.notify`. The script editor calls `document.execCommand('insertText')` on its own textarea so that indenting stays undoable. It does not use `eval`, `new Function`, dynamic `import()`, or queries against the app's own markup. The one call outside the SDK is `window.hermesDesktop.api` in `useCore` (`desktop/plugin.js:103-117`), for two read-only core GETs: `ctx.rest` only reaches the plugin's own namespace, and the catalog review asked for these core routes. `hermes plugins validate` passes and lists it as a rule 8 warning for the reviewer.

### Credentials

The plugin reads no secrets, `.env` files, or tokens, and enters no profile's secret scope. Whether a platform has a home channel comes from Hermes' `/api/cron/delivery-targets`, which does that read inside its own profile scope. It never returns a cron job's `base_url` and drops any displayed config value that looks like a credential. It shows the names of a skill's declared credentials (`credentials`, `required_environment_variables`) and never their values.
