# Ariadne's Thread

n8n-style flows for your Hermes skills and crons.

Ariadne's Thread adds a page to Hermes Desktop that draws each cron job, webhook route, and skill as a chain of steps. A cron shows its script, prompt, attached skills, and delivery target. A skill shows the steps it describes, read from its `SKILL.md`. You can click a skill step to open that skill, edit your own skills in place, or draft a change request in a new chat.

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

- **Crons** lists the jobs in `cron/jobs.json` with their status and schedule. Select a job to see its flow. Click a skill step to open the skill.
- **Webhooks** lists the routes in `webhook_subscriptions.json` with their events and flow.
- **Skills** lists the skills in your skills folder and in any folder listed under `skills.external_dirs`, grouped by category folder, with a step count. Select a skill to see its flow, its related skills, the crons that use it, and its `SKILL.md`.

Use the profile menu in the header to look at another profile. Other profiles are read-only. Skills from `skills.external_dirs` are also read-only and show an **external** badge. If a local skill and an external skill have the same name, the local one wins, as it does in Hermes.

To change a skill yourself, click **Edit**, change the `SKILL.md`, and click **Save**. The plugin checks the frontmatter before it writes.

To ask an agent for the change, type the request under **Ask an agent to change it** and click **Draft in new chat**. Ariadne's Thread opens a new chat with a prompt that names the skill file and your request. Nothing runs until you send that message. If the draft cannot be placed, the prompt goes to your clipboard.

### How skills become flows

The parser reads step headings (`## Step 1`), an ordered list under a heading such as "Procedure" or "Steps", or the first ordered list in the skill. If it finds none, it shows each `##` section as a step, or the whole skill as one step. The flow view says which rule it used. [docs/parsing-report.md](docs/parsing-report.md) shows the results on every skill that ships with Hermes and lists where the parser is weak.

## What this plugin reads and writes

Ariadne's Thread reads files that Hermes keeps in its home folder (`HERMES_HOME`, `~/.hermes` by default). It keeps no data of its own.

It reads:

- `cron/jobs.json`
- `webhook_subscriptions.json`. Webhook secrets are never sent to the UI.
- every `SKILL.md` under `skills/`, including skill folders that are symlinks to shared folders
- `config.yaml`, only to find `skills.external_dirs`, and every `SKILL.md` in those folders
- the folder names in `profiles/`, and the same sources inside a profile when you pick it

It writes only one thing. When you click **Save** in the editor, it replaces that skill's `SKILL.md` through a temporary file in the same folder. It writes only skills of the active profile whose real path is inside `skills/`. It never writes to external skill folders.

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
