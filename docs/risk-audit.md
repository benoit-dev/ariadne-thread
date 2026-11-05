# Risk audit

This audit covers catalog rules 8 (Desktop SDK only), 11 (credentials), 12 (approvals) and 13 (disclosure). It lists every file write, network call, subprocess, and agent session in the source plugin and in Ariadne's Thread 0.1.0.

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

## Behaviour of 0.1.0

### Filesystem reads

Reads stay inside Hermes's own home, plus any skill folders that the user lists under `skills.external_dirs` in `config.yaml`. The plugin keeps no data of its own.

| Location | Reads |
|---|---|
| `dashboard/plugin_api.py:701` | `<home>/cron/jobs.json` |
| `dashboard/plugin_api.py:801` | `<home>/webhook_subscriptions.json`. The `secret` field is never returned. |
| `dashboard/plugin_api.py:137`, `:870`, `:908` | Every `SKILL.md` under `<home>/skills/` and under each external skill folder, following symlinked folders |
| `dashboard/plugin_api.py:1023-1060` | One file under `<home>/scripts/`, when the user clicks a script step. Paths resolve the way Hermes resolves cron and webhook scripts; anything that resolves outside `<home>/scripts/` (`..`, absolute paths, symlinks) gets HTTP 403. At most 256 KB is returned. |
| `dashboard/plugin_api.py:102-110` | `<home>/config.yaml`, for the `skills.external_dirs` list only. Entries expand `~` and `${VAR}` the way Hermes does. |
| `dashboard/plugin_api.py:88`, `:1019` | The list of folders in `<hermes root>/profiles/`, and the sources above for a profile picked with `?profile=` |

### Filesystem writes

| Location | Writes | Guard |
|---|---|---|
| `dashboard/plugin_api.py:960-970` | One `SKILL.md` that the user edited, by temp file and rename in the same folder | Active profile only. Skills from external folders are read-only. The real path must be inside `<home>/skills/`. The content must pass the same frontmatter checks as Hermes's skill manager. |

### Network

| Location | Call |
|---|---|
| `dashboard/plugin_api.py:985` | `broadcast_plugin_event` pushes `plugin.ariadne-thread.skills.changed` to connected Desktop windows. This is local and goes through Hermes. |
| `desktop/plugin.js` (all `ctx.rest` calls) | The plugin's own API at `/api/plugins/ariadne-thread/` through the Desktop SDK |

The plugin calls no third-party service and sends no telemetry.

### Subprocesses, shell, and agent sessions

There are none. The plugin does not call `subprocess`, `os.system`, `session.create`, `prompt.submit`, or `composer.submit`.

### Desktop SDK use

`desktop/plugin.js` imports only `@hermes/plugin-sdk`, `react`, and `react/jsx-runtime`. It uses `ctx.rest`, `ctx.onEvent`, `ctx.setTimeout`, `ctx.addEventListener`, `ctx.os.writeClipboard`, `host.navigate`, `host.composer.setDraft`, and `host.notify`. It does not use `eval`, `new Function`, dynamic `import()`, `window.hermesDesktop`, or queries against the app's own markup. `hermes plugins validate` reports `desktop surface: stays inside the plugin SDK surface`.

### Credentials

The plugin reads no secrets, `.env` files, or tokens. It shows the names of a skill's declared credentials (`credentials`, `required_environment_variables`) and never their values.
