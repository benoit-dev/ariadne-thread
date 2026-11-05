# Ariadne's Thread

n8n-style flows for your Hermes skills and crons.

Ariadne's Thread adds a page to Hermes Desktop that draws each cron job, webhook route, and skill as a chain of steps. A cron shows its script, prompt, attached skills, and delivery target. A skill shows the steps it describes, read from its `SKILL.md`. Open any step to read it, edit it right there, or draft a change request for it in a new chat.

<!-- Screenshot: Crons tab with a selected job and its flow -->
<!-- Screenshot: Skills tab with a parsed skill flow -->

## Install

Ariadne's Thread needs Hermes 0.22.0 or later.

Install it from the plugin catalog:

```sh
hermes plugins install ariadne-thread
```

Until the catalog entry exists, install it from its GitHub repository (the URL in the author line at the end of this file). Pin a full commit SHA:

```sh
hermes plugins install <owner>/ariadne-thread --ref <40-character commit sha>
```

Hermes installs plugins disabled. To enable it, answer yes to the prompt or run:

```sh
hermes plugins enable ariadne-thread
```

Restart Hermes Desktop, or rescan plugins, so that it loads the Desktop page. The backend API mounts when the Hermes backend starts.

## Use it

Open **Ariadne's Thread** in the Desktop sidebar. The page has three tabs.

- **Crons** lists the jobs in `cron/jobs.json` with their status and schedule. Select a job to see its flow. Click a step to expand it in place: a script or deterministic step widens to show the script file from `scripts/`, a prompt step shows the whole prompt, and the deliver step shows where each target in `deliver` resolves (platform, chat and thread, by the scheduler's own rules), or why it sends nothing, such as a platform with no home channel. A job that only saves its output ends in a **local** deliver step. Click it again to collapse it. A skill step does not expand; its **Go to skill** button opens the skill.
- **Webhooks** lists the routes in `webhook_subscriptions.json` with their events and flow. Its steps open the same way.
- **Skills** lists the skills in your skills folder and in any folder listed under `skills.external_dirs`, grouped by category folder, with a step count. A selected skill has two views: **Workflow** shows its flow, its related skills and the crons that use it, and **SKILL.md** shows the whole file.

Use the profile menu in the header to look at another profile. Other profiles are read-only. Skills from `skills.external_dirs` are also read-only and show an **external** badge. If a local skill and an external skill have the same name, the local one wins, as it does in Hermes.

### Edit in place

An expanded step has an **Edit** button. It turns the step into a text box; **Save** (or ⌘/Ctrl+Enter) writes it, **Cancel** (or Esc) drops it. What you can edit:

- a skill step: the lines of `SKILL.md` that step came from. The rest of the file is left as it was, and the whole file must still pass the frontmatter checks.
- the whole `SKILL.md`, from the **SKILL.md** view.
- a cron job's prompt.
- a webhook route's prompt template. Its secret and other fields are kept.
- a script step's file in `scripts/`, unless it is over 256 KB.
- a cron job's delivery targets. Pick from local, the origin chat and the platforms your gateway has set up (⚠ marks one with no home channel yet), or type the comma-separated `deliver` value, such as `telegram,discord:1234`. Enter saves.

Scripts show with line numbers and syntax colours (Python, shell and JavaScript, picked by extension or shebang), and edit in a code editor: Tab and Shift+Tab indent or dedent the selected lines, Enter keeps the line's indent (one level more after a Python `:` or an opening bracket), and Backspace in leading spaces removes one indent level.

Each save sends the text you started from. If the file changed since (a cron run, an agent, another window), the save is refused and nothing is written; reload and try again. Trigger steps and webhook deliver steps are read-only.

### Ask an agent

Every editable step also has an **Ask agent** button next to **Edit**. It docks a request box at the bottom of the step's visible area that stays in view while you scroll (Esc closes it). The skill has a box for the whole file under its views. Type the request (the box grows to fit it; Shift+Enter adds a line) and press Enter or click the send arrow. Ariadne's Thread opens a new chat with a prompt that names the file or job, quotes the step's current text, and adds your request. Nothing runs until you send that message. If the draft cannot be placed, the prompt goes to your clipboard.

### How skills become flows

The parser reads step headings (`## Step 1`), an ordered list under a heading such as "Procedure" or "Steps", or the first ordered list in the skill. If it finds none, it shows each `##` section as a step, or the whole skill as one step. The flow view says which rule it used. [docs/parsing-report.md](docs/parsing-report.md) shows the results on every skill that ships with Hermes and lists where the parser is weak.

## What this plugin reads and writes

Ariadne's Thread reads files that Hermes keeps in its home folder (`HERMES_HOME`, `~/.hermes` by default). It keeps no data of its own.

It reads:

- `cron/jobs.json`
- `webhook_subscriptions.json`. Webhook secrets are never sent to the UI.
- every `SKILL.md` under `skills/`, including skill folders that are symlinks to shared folders
- `config.yaml`, only to find `skills.external_dirs`, and every `SKILL.md` in those folders
- a script in `scripts/` when you click a script step, the same folder Hermes runs cron and webhook scripts from. It refuses paths that resolve outside that folder and shows at most the first 256 KB. The script is shown as it is, so anything written into it is visible on screen.
- the folder names in `profiles/`, and the same sources inside a profile when you pick it

It writes only when you click **Save**, and only in the active profile:

- a skill's `SKILL.md`, whole or one step's lines, through a temporary file in the same folder. Only skills whose real path is inside `skills/`; never external skill folders.
- a script in `scripts/`, through a temporary file, keeping its file mode. Paths outside `scripts/` are refused.
- a cron job's `prompt`, through Hermes' own cron store (`cron.jobs.update_job`), which takes the same lock as the scheduler.
- a webhook route's `prompt`, through Hermes' own webhook store, which takes the same lock as the gateway and keeps the route's secret.

Every save is refused if the text on disk no longer matches what you started editing.

It does not:

- run shell commands or start processes
- start agent sessions or send messages for you
- call any network service other than the Hermes backend it is installed in
- read credentials, `.env` files, or tokens
- collect telemetry

After a save, it sends the local event `plugin.ariadne-thread.skills.changed` to open Desktop windows so that they refresh.

[docs/risk-audit.md](docs/risk-audit.md) lists each of these with its file and line.

## Development

Run the tests with a Python that has Hermes installed:

```sh
python -m pytest tests
```

Check the package the same way catalog CI does:

```sh
hermes plugins validate . --install-deps
hermes plugins doctor . --ci
```

[docs/validation.md](docs/validation.md) has the output for this version.

### Try external skill folders by hand

1. Create a folder with one skill in it:

    ```sh
    mkdir -p ~/team-skills/ops/rotate-logs
    printf -- '---\nname: rotate-logs\ndescription: Rotate old logs\n---\n\n1. Find logs older than 7 days.\n2. Compress them.\n' > ~/team-skills/ops/rotate-logs/SKILL.md
    ```

2. Add the folder to `~/.hermes/config.yaml`:

    ```yaml
    skills:
      external_dirs:
        - ~/team-skills
    ```

3. Open the **Skills** tab. `rotate-logs` appears under `ops` with 2 steps and an **external** badge, and it has no **Edit** button.

4. Run `hermes skills list` and check that Hermes lists the same skill.

## License

MIT. See [LICENSE](LICENSE).

Author: benoit-dev (https://github.com/benoit-dev/ariadne-thread)
