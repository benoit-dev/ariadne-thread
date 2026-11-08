# Risk audit

This audit covers catalog rules 8 (Desktop SDK only), 11 (credentials), 12 (approvals) and 13 (disclosure). It lists every file write, network call, subprocess, and agent session in the source plugin and in Ariadne's Thread 0.2.0. The findings table records what changed from the source to 0.1.0. The sections after it describe 0.2.0.

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

## Behaviour of 0.2.0

### Filesystem reads

Reads stay inside Hermes's own home, plus any skill folders that the user lists under `skills.external_dirs` in `config.yaml`. The only data the plugin keeps is its edit history in `<home>/plugin-data/ariadne-thread/history/`, the per-plugin folder Hermes defines in `plugins/plugin_storage.py`.

| Location | Reads |
|---|---|
| `dashboard/plugin_api.py:847`, `:996-1009`, `:1447` | `<home>/cron/jobs.json`. The job payload shows only the keys in `_CONFIG_KEYS` (`:806`); `base_url` is never returned, and `_looks_like_credential` (`:815-818`) drops any config value that looks like a key, a token, or a URL with a password. |
| `dashboard/plugin_api.py:949`, `:996-1009` | `<home>/webhook_subscriptions.json`. The `secret` field is never returned. |
| `dashboard/plugin_api.py:190`, `:1024`, `:1062` | Every `SKILL.md` under `<home>/skills/` and under each external skill folder, following symlinked folders |
| `dashboard/plugin_api.py:1288-1351` | One file under `<home>/scripts/`, when the user clicks a script step. Paths resolve the way Hermes resolves cron and webhook scripts; anything that resolves outside `<home>/scripts/` (`..`, absolute paths, symlinks) gets HTTP 403. At most 256 KB is returned. |
| `dashboard/plugin_api.py:1488-1534` | Run output files in `<home>/cron/output/<job id>/`, when the user opens **Runs**. The job id and run id must match fixed patterns (HTTP 400 otherwise, so no `..` or separators). The listing takes only regular files named `YYYY-MM-DD_HH-MM-SS.md` and skips symlinks. A read that resolves outside the job's folder gets HTTP 403. At most 256 KB is returned. Works for every profile and never writes. |
| `dashboard/plugin_api.py:1661-1702` | The plugin's own history files under `<home>/plugin-data/ariadne-thread/history/`, when the user opens **History**. Reads create no folders. |
| `dashboard/plugin_api.py:134-142` | `<home>/config.yaml`, for the `skills.external_dirs` list only. Entries expand `~` and `${VAR}` the way Hermes does. |
| `dashboard/plugin_api.py:118`, `:1727` | The list of folders in `<hermes root>/profiles/`, and the sources above for a profile picked with `?profile=` |

### Filesystem writes

| Location | Writes | Guard |
|---|---|---|
| `dashboard/plugin_api.py:1215` (through `_atomic_write` at `:1116`) | One `SKILL.md` that the user edited, whole or the lines of one flow step, by temp file and rename in the same folder | Active profile only. Skills from external folders are read-only. The real path must be inside `<home>/skills/`. The resulting file must pass the same frontmatter checks as Hermes's skill manager. |
| `dashboard/plugin_api.py:1351` | One script under `<home>/scripts/` that the user edited, by temp file and rename, keeping its mode | Active profile only. Same path rules as the script read: anything resolving outside `<home>/scripts/` gets HTTP 403. Scripts over 256 KB or not UTF-8 are refused. |
| `dashboard/plugin_api.py:1549` | The `prompt` of one cron job, through `cron.jobs.update_job` | Active profile only. Hermes takes its cron store lock, so the write cannot interleave with the scheduler. |
| `dashboard/plugin_api.py:1570` | The `deliver` of one cron job, through `cron.jobs.update_job` | Same as the cron prompt. |
| `dashboard/plugin_api.py:1590` | The `prompt` of one webhook route, through `hermes_cli.webhook._replace_subscription` | Active profile only. Hermes takes its subscriptions lock and compares the whole route, so a concurrent change is refused. The secret is copied through unchanged and never returned. |
| `dashboard/plugin_api.py:1142-1178`, called at `:1214`, `:1350`, `:1547`, `:1568`, `:1588` | Before each write above, one history file `<home>/plugin-data/ariadne-thread/history/<kind>/<sha256 of the target, 24 hex>/<20-digit id>.json` holding the replaced text, then deletion of all but the newest 10 files in that folder | Active profile only. Written after the conflict check and before the target, by temp file and rename, mode `0600`, folders `0700`. Nothing is recorded when the text is unchanged. If the history file cannot be written, the save answers HTTP 500 and the target is not touched. For a webhook only the prompt text is stored, never the route or its secret. |
| `dashboard/plugin_api.py:1713-1721` | A revert writes a stored version back by calling the matching handler above (`_HISTORY_KINDS`, `:1630`) | Active profile only (HTTP 403 otherwise). It carries the text the user previewed as `expected`, so the same HTTP 409 check applies, and it records the text it replaces like any save. |

Every write carries the text the user started editing from and is refused with HTTP 409 if the file no longer matches. Nothing is written without a **Save** or **Revert** click.

#### Core imports

Hermes has no public seam for editing a cron job's prompt (`cron.manage` lists, adds, removes, pauses and resumes only) or a webhook route. The cron and webhook writes therefore import `cron.jobs` and private functions of `hermes_cli.webhook`. `hermes plugins validate` passes and lists these as rule 9 warnings. If a later Hermes drops the webhook functions, that endpoint answers HTTP 501 and the UI shows the error; reads are unaffected.

### Network

| Location | Call |
|---|---|
| `dashboard/plugin_api.py:1185` | `broadcast_plugin_event` pushes `plugin.ariadne-thread.skills.changed` to connected Desktop windows. This is local and goes through Hermes. |
| `desktop/plugin.js` (all `ctx.rest` calls) | The plugin's own API at `/api/plugins/ariadne-thread/` through the Desktop SDK |

The plugin calls no third-party service and sends no telemetry.

### Subprocesses, shell, and agent sessions

There are none. The plugin does not call `subprocess`, `os.system`, `session.create`, `prompt.submit`, or `composer.submit`. It cannot run, pause, or resume a cron job; **Runs** only reads output files that Hermes already wrote.

### Desktop SDK use

`desktop/plugin.js` imports only `@hermes/plugin-sdk`, `react`, and `react/jsx-runtime`. It uses `ctx.rest`, `ctx.onEvent`, `ctx.setTimeout`, `ctx.addEventListener`, `ctx.os.writeClipboard`, `host.navigate`, `host.composer.setDraft`, and `host.notify`. The script editor calls `document.execCommand('insertText')` on its own textarea so that indenting stays undoable. It does not use `eval`, `new Function`, dynamic `import()`, `window.hermesDesktop`, or queries against the app's own markup. `hermes plugins validate` reports `desktop surface: stays inside the plugin SDK surface`.

### Credentials

The plugin reads no secrets, `.env` files, or tokens. It never returns a cron job's `base_url` and drops any displayed config value that looks like a credential. It shows the names of a skill's declared credentials (`credentials`, `required_environment_variables`) and never their values.
