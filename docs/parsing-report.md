# Parsing report

Ariadne's Thread turns each `SKILL.md` into a flow with `parse_skill_flow` in `dashboard/plugin_api.py`. This report shows what the parser finds in the skills that ship with Hermes v0.22.7 (commit `b200f5bc60e3`, 44 skills in `skills/` and 149 in `optional-skills/`).

To regenerate the table, run this command with a Python that has Hermes installed:

```sh
python scripts/parse_skills_report.py <hermes-agent>/skills <hermes-agent>/optional-skills --markdown
```

## How the parser picks steps

The parser tries five strategies in order and keeps the first one that gives enough steps. It ignores headings and lists inside fenced code blocks.

| Order | Strategy | What counts as a step | Fallback |
|---|---|---|---|
| 1 | `step-headings` | Headings such as `## Step 1`, `### Phase 2: Build`, or `## 1. Install` (at least 2 at one level) | no |
| 2 | `procedure-list` | The ordered list under a heading like Procedure, Steps, Workflow, Instructions, How to, Usage, Process, or Quick start | no |
| 3 | `first-ordered-list` | The first ordered list with 2 or more items, unless it sits under Rules, Prerequisites, Pitfalls, or a similar heading | no |
| 4 | `sections` | Each `##` heading | yes |
| 5 | `single` | One step for the whole skill, from its `name` and `description` | yes |

Each step gets a kind. `skill` means the step names another skill (`skill_view(name='x')` or "use the `x` skill"). `script` means the step has a code block or a shell command in backticks. Everything else is `agent`.

## Results

| Set | Skills | Fell back |
|---|---|---|
| `skills/` | 44 | 12 (27%) |
| `optional-skills/` | 149 | 41 (28%) |
| Total | 193 | 53 (27%) |

| Strategy | Skills |
|---|---|
| `step-headings` | 77 |
| `procedure-list` | 43 |
| `first-ordered-list` | 20 |
| `sections` (fallback) | 49 |
| `single` (fallback) | 4 |

An empty skills folder gives `skills parsed: 0`. Against an empty `HERMES_HOME`, every endpoint returns an empty result (`/crons`, `/webhooks` and `/skills` return empty lists with status 200). See [validation.md](validation.md) for that run.

## Where parsing is weak

- **Ordered lists that are not procedures.** `first-ordered-list` also picks up lists that rank or enumerate, such as the settings hierarchy in `claude-code` and the decision list in `ascii-art`. Skipping lists under Rules, Prerequisites, Pitfalls and similar headings moved 13 skills out of this bucket. That heading filter only knows English words.
- **Numbered catalogues read as flows.** `humanizer` has 34 numbered pattern headings and becomes a 34-step flow. `code-wiki` and some command-reference skills behave the same way.
- **Too many `script` steps.** 502 of 1228 steps are `script`, because any code block or shell-looking inline command counts. Tool-reference skills look like all-script flows.
- **Skill references are rare.** Only 8 steps across 193 skills are `skill` steps. Plain prose such as "load himalaya" is not detected.
- **Unnumbered procedures fall back.** `test-driven-development` is a real procedure (RED, GREEN, REFACTOR as `###` headings) but lands in `sections`. The `sections` fallback reads `##` only, so a skill with only `#` or `###` headings becomes `single`. Four optional skills (`archify`, `brag`, `brag-slim`, `impeccable`) are `single`.
- **Name collisions.** Detail views resolve a skill by its folder name. If two folders share a name, the shallowest path wins.

## Full table

| skill | source dir | steps | fell back | strategy / notes |
|---|---|---|---|---|
| apple/apple-notes | skills | 6 | yes | sections (2 agent, 2 script, 2 skill) |
| apple/apple-reminders | skills | 6 | yes | sections (4 agent, 2 script) |
| apple/findmy | skills | 7 | yes | sections (3 agent, 4 script) |
| apple/imessage | skills | 7 | yes | sections (4 agent, 3 script) |
| autonomous-ai-agents/claude-code | skills | 4 | no | first-ordered-list (4 agent) |
| autonomous-ai-agents/codex | skills | 10 | yes | sections (3 agent, 7 script) |
| autonomous-ai-agents/computer-use | skills | 5 | no | first-ordered-list (4 agent, 1 script) |
| autonomous-ai-agents/hermes-agent | skills | 7 | yes | sections (7 script) |
| creative/architecture-diagram | skills | 4 | no | procedure-list (4 agent) |
| creative/ascii-video | skills | 4 | no | step-headings (4 agent) |
| creative/baoyu-infographic | skills | 7 | no | step-headings (7 agent) |
| creative/claude-design | skills | 8 | no | procedure-list (8 agent) |
| creative/humanizer | skills | 34 | no | step-headings (34 agent) |
| creative/manim-video | skills | 5 | no | step-headings (1 agent, 4 script) |
| creative/p5js | skills | 6 | no | step-headings (4 agent, 2 script) |
| creative/popular-web-designs | skills | 4 | no | procedure-list (4 agent) |
| creative/songwriting-and-ai-music | skills | 10 | no | step-headings (7 agent, 3 script) |
| devops/sdlc-review | skills | 4 | no | step-headings (4 agent) |
| email/email-inbox-triage | skills | 6 | no | step-headings (6 agent) |
| email/himalaya | skills | 10 | yes | sections (2 agent, 8 script) |
| media/youtube-content | skills | 5 | no | procedure-list (5 agent) |
| note-taking/obsidian | skills | 8 | yes | sections (8 agent) |
| productivity/document-to-action-items | skills | 7 | no | step-headings (7 agent) |
| productivity/google-workspace | skills | 6 | no | step-headings (5 script, 1 skill) |
| productivity/maps | skills | 3 | no | procedure-list (3 agent) |
| productivity/meeting-action-items | skills | 6 | no | step-headings (6 agent) |
| productivity/microsoft-office | skills | 4 | no | procedure-list (4 agent) |
| productivity/pdf | skills | 9 | no | procedure-list (9 agent) |
| productivity/product-price-monitor | skills | 6 | no | step-headings (5 agent, 1 script) |
| productivity/teams-meeting-pipeline | skills | 7 | yes | sections (4 agent, 3 script) |
| productivity/weekly-review-planning | skills | 7 | no | step-headings (7 agent) |
| research/arxiv | skills | 7 | no | procedure-list (3 agent, 4 script) |
| research/competitor-news-monitor | skills | 6 | no | step-headings (5 agent, 1 script) |
| research/grounded-citations | skills | 9 | yes | sections (4 agent, 5 script) |
| social-media/xurl | skills | 9 | no | procedure-list (9 agent) |
| software-development/codebase-inspection | skills | 6 | no | step-headings (1 agent, 5 script) |
| software-development/dogfood | skills | 5 | no | step-headings (3 agent, 2 script) |
| software-development/github | skills | 3 | yes | sections (2 agent, 1 script) |
| software-development/requesting-code-review | skills | 8 | no | step-headings (1 agent, 7 script) |
| software-development/simplify-code | skills | 3 | no | step-headings (1 agent, 2 script) |
| software-development/spike | skills | 5 | no | step-headings (4 agent, 1 script) |
| software-development/systematic-debugging | skills | 4 | no | step-headings (1 agent, 2 script, 1 skill) |
| software-development/test-driven-development | skills | 13 | yes | sections (9 agent, 4 script) |
| web/blocked-page-recovery | skills | 4 | no | step-headings (1 agent, 3 script) |
| autonomous-ai-agents/agent-merge-conflict-arbiter | optional-skills | 5 | no | step-headings (2 agent, 3 script) |
| autonomous-ai-agents/antigravity-cli | optional-skills | 13 | yes | sections (11 agent, 2 script) |
| autonomous-ai-agents/blackbox | optional-skills | 12 | yes | sections (6 agent, 6 script) |
| autonomous-ai-agents/dynamic-workflow | optional-skills | 5 | no | procedure-list (5 agent) |
| autonomous-ai-agents/grok | optional-skills | 11 | yes | sections (3 agent, 8 script) |
| autonomous-ai-agents/honcho | optional-skills | 3 | no | first-ordered-list (3 agent) |
| autonomous-ai-agents/opencode | optional-skills | 7 | no | procedure-list (7 agent) |
| autonomous-ai-agents/openhands | optional-skills | 8 | yes | sections (4 agent, 4 script) |
| blockchain/evm | optional-skills | 10 | no | step-headings (10 script) |
| blockchain/hyperliquid | optional-skills | 7 | no | step-headings (7 script) |
| blockchain/solana | optional-skills | 9 | no | step-headings (9 script) |
| communication/one-three-one-rule | optional-skills | 5 | no | procedure-list (5 agent) |
| creative/ai-presenter-video | optional-skills | 6 | no | procedure-list (3 agent, 3 script) |
| creative/anatomy | optional-skills | 10 | no | procedure-list (10 agent) |
| creative/archify | optional-skills | 1 | yes | single (1 agent) |
| creative/ascii-art | optional-skills | 9 | no | first-ordered-list (9 agent) |
| creative/auteur | optional-skills | 4 | no | procedure-list (4 agent) |
| creative/baoyu-article-illustrator | optional-skills | 7 | no | step-headings (4 agent, 3 script) |
| creative/baoyu-comic | optional-skills | 3 | no | procedure-list (2 agent, 1 script) |
| creative/brag | optional-skills | 1 | yes | single (1 agent) |
| creative/brag-slim | optional-skills | 1 | yes | single (1 agent) |
| creative/comfyui | optional-skills | 6 | no | step-headings (3 agent, 3 script) |
| creative/concept-diagrams | optional-skills | 5 | no | procedure-list (5 agent) |
| creative/creative-ideation | optional-skills | 4 | no | step-headings (4 agent) |
| creative/design-md | optional-skills | 5 | no | procedure-list (5 agent) |
| creative/draw-your-font | optional-skills | 7 | yes | sections (3 agent, 4 script) |
| creative/dream-loop | optional-skills | 8 | no | step-headings (6 agent, 1 script, 1 skill) |
| creative/excalidraw | optional-skills | 4 | no | procedure-list (4 agent) |
| creative/heartmula | optional-skills | 5 | no | step-headings (5 script) |
| creative/hyperframes | optional-skills | 8 | no | step-headings (4 agent, 4 script) |
| creative/impeccable | optional-skills | 1 | yes | single (1 agent) |
| creative/ip-as-logo | optional-skills | 13 | no | procedure-list (12 agent, 1 script) |
| creative/kanban-video-orchestrator | optional-skills | 6 | no | step-headings (4 agent, 2 script) |
| creative/meme-generation | optional-skills | 6 | no | procedure-list (4 agent, 2 script) |
| creative/mono-color | optional-skills | 6 | no | procedure-list (6 agent) |
| creative/pixel-art | optional-skills | 3 | no | step-headings (1 agent, 2 script) |
| creative/pretext | optional-skills | 7 | no | procedure-list (6 agent, 1 script) |
| creative/simple-english | optional-skills | 3 | no | procedure-list (3 agent) |
| creative/sketch | optional-skills | 5 | no | step-headings (3 agent, 2 script) |
| creative/social-media-content-calendar | optional-skills | 7 | no | step-headings (7 agent) |
| creative/system-atlas | optional-skills | 9 | no | procedure-list (9 agent) |
| creative/tldraw-offline | optional-skills | 6 | no | procedure-list (6 agent) |
| creative/unreal-mcp | optional-skills | 4 | no | first-ordered-list (4 agent) |
| data-science/jupyter-notebook | optional-skills | 5 | no | step-headings (5 script) |
| devops/actual-setup | optional-skills | 4 | no | procedure-list (1 agent, 3 script) |
| devops/docker-management | optional-skills | 6 | no | step-headings (1 agent, 5 script) |
| devops/hermes-s6-container-supervision | optional-skills | 2 | no | first-ordered-list (1 agent, 1 script) |
| devops/inference-sh-cli | optional-skills | 3 | no | step-headings (1 agent, 2 script) |
| devops/pinggy-tunnel | optional-skills | 5 | no | step-headings (5 script) |
| devops/setup-wizard-generator | optional-skills | 4 | no | step-headings (3 agent, 1 script) |
| devops/watchers | optional-skills | 8 | yes | sections (6 agent, 2 script) |
| dogfood/adversarial-ux-test | optional-skills | 6 | no | step-headings (5 agent, 1 script) |
| email/agentmail | optional-skills | 4 | no | procedure-list (4 agent) |
| finance/3-statement-model | optional-skills | 6 | no | step-headings (6 agent) |
| finance/comps-analysis | optional-skills | 6 | no | procedure-list (6 agent) |
| finance/dcf-model | optional-skills | 10 | no | step-headings (2 agent, 8 script) |
| finance/excel-author | optional-skills | 3 | no | first-ordered-list (3 agent) |
| finance/lbo-model | optional-skills | 3 | no | step-headings (3 agent) |
| finance/merger-model | optional-skills | 7 | no | step-headings (7 agent) |
| finance/polymarket | optional-skills | 4 | no | procedure-list (4 agent) |
| finance/pptx-author | optional-skills | 8 | yes | sections (4 agent, 4 script) |
| finance/stocks | optional-skills | 7 | yes | sections (4 agent, 3 script) |
| gaming/minecraft-modpack-server | optional-skills | 9 | no | step-headings (9 script) |
| gaming/pokemon-player | optional-skills | 10 | no | step-headings (10 agent) |
| health/fitness-nutrition | optional-skills | 5 | yes | sections (3 agent, 2 script) |
| health/neuroskill-bci | optional-skills | 11 | no | step-headings (1 agent, 10 script) |
| mcp/fastmcp | optional-skills | 7 | no | step-headings (3 agent, 4 script) |
| mcp/mcp-oauth-remote-gateway | optional-skills | 11 | no | step-headings (6 agent, 5 script) |
| mcp/mcporter | optional-skills | 8 | yes | sections (1 agent, 7 script) |
| media/gif-search | optional-skills | 9 | yes | sections (5 agent, 4 script) |
| media/songsee | optional-skills | 5 | yes | sections (3 agent, 2 script) |
| mlops/accelerate | optional-skills | 7 | yes | sections (4 agent, 3 script) |
| mlops/chroma | optional-skills | 6 | no | step-headings (6 script) |
| mlops/evaluation/evaluating-llms-harness | optional-skills | 8 | yes | sections (5 agent, 3 script) |
| mlops/faiss | optional-skills | 4 | no | step-headings (4 script) |
| mlops/flash-attention | optional-skills | 7 | yes | sections (4 agent, 3 script) |
| mlops/inference/llama-cpp | optional-skills | 7 | no | procedure-list (7 agent) |
| mlops/inference/serving-llms-vllm | optional-skills | 8 | yes | sections (5 agent, 3 script) |
| mlops/lambda-labs | optional-skills | 4 | no | procedure-list (4 agent) |
| mlops/modal | optional-skills | 17 | yes | sections (5 agent, 12 script) |
| mlops/models/segment-anything-model | optional-skills | 13 | yes | sections (4 agent, 9 script) |
| mlops/nemo-curator | optional-skills | 4 | no | step-headings (4 script) |
| mlops/obliteratus | optional-skills | 7 | no | step-headings (2 agent, 5 script) |
| mlops/peft | optional-skills | 11 | yes | sections (5 agent, 6 script) |
| mlops/pytorch-fsdp | optional-skills | 2 | no | first-ordered-list (2 agent) |
| mlops/pytorch-lightning | optional-skills | 7 | yes | sections (4 agent, 3 script) |
| mlops/research/dspy | optional-skills | 9 | no | step-headings (9 script) |
| mlops/saelens | optional-skills | 13 | yes | sections (5 agent, 8 script) |
| mlops/simpo | optional-skills | 7 | yes | sections (5 agent, 2 script) |
| mlops/slime | optional-skills | 6 | no | step-headings (1 agent, 5 script) |
| mlops/tensorrt-llm | optional-skills | 8 | yes | sections (6 agent, 2 script) |
| mlops/torchtitan | optional-skills | 8 | yes | sections (5 agent, 3 script) |
| mlops/training/axolotl | optional-skills | 2 | no | first-ordered-list (2 agent) |
| mlops/training/unsloth | optional-skills | 2 | no | first-ordered-list (2 agent) |
| payments/mpp-agent | optional-skills | 4 | no | step-headings (4 script) |
| payments/stripe-link-cli | optional-skills | 7 | no | step-headings (7 script) |
| payments/stripe-projects | optional-skills | 5 | no | step-headings (5 script) |
| productivity/airtable | optional-skills | 6 | no | procedure-list (5 agent, 1 script) |
| productivity/box | optional-skills | 3 | no | first-ordered-list (3 agent) |
| productivity/canvas | optional-skills | 5 | no | first-ordered-list (4 agent, 1 script) |
| productivity/decision-questionnaire | optional-skills | 5 | yes | sections (5 agent) |
| productivity/here-now | optional-skills | 3 | no | first-ordered-list (3 agent) |
| productivity/live-dashboard | optional-skills | 8 | no | step-headings (7 agent, 1 script) |
| productivity/memento-flashcards | optional-skills | 3 | no | procedure-list (3 agent) |
| productivity/notion | optional-skills | 3 | no | step-headings (3 script) |
| productivity/property-listings | optional-skills | 4 | no | procedure-list (3 agent, 1 script) |
| productivity/shop | optional-skills | 6 | no | first-ordered-list (6 agent) |
| productivity/shopify | optional-skills | 13 | yes | sections (1 agent, 12 script) |
| productivity/siyuan | optional-skills | 7 | yes | sections (4 agent, 3 script) |
| productivity/telephony | optional-skills | 4 | no | step-headings (4 agent) |
| research/bioinformatics | optional-skills | 4 | no | procedure-list (2 agent, 2 script) |
| research/blogwatcher | optional-skills | 6 | yes | sections (4 agent, 2 script) |
| research/darwinian-evolver | optional-skills | 3 | no | first-ordered-list (3 agent) |
| research/domain-intel | optional-skills | 6 | yes | sections (4 agent, 2 script) |
| research/drug-discovery | optional-skills | 4 | yes | sections (3 agent, 1 script) |
| research/duckduckgo-search | optional-skills | 4 | no | first-ordered-list (4 agent) |
| research/gitnexus-explorer | optional-skills | 7 | no | step-headings (7 script) |
| research/llm-wiki | optional-skills | 3 | no | step-headings (2 agent, 1 script) |
| research/osint-investigation | optional-skills | 5 | no | step-headings (5 script) |
| research/parallel-cli | optional-skills | 3 | no | procedure-list (3 agent) |
| research/qmd | optional-skills | 4 | no | step-headings (4 script) |
| research/research-paper-writing | optional-skills | 9 | no | step-headings (2 agent, 6 script, 1 skill) |
| research/rss-feeds | optional-skills | 7 | yes | sections (5 agent, 2 script) |
| research/scrapling | optional-skills | 10 | yes | sections (3 agent, 7 script) |
| research/searxng-search | optional-skills | 3 | no | first-ordered-list (3 agent) |
| security/1password | optional-skills | 3 | no | first-ordered-list (3 agent) |
| security/godmode | optional-skills | 7 | no | step-headings (2 agent, 5 script) |
| security/oss-forensics | optional-skills | 8 | no | step-headings (4 agent, 4 script) |
| security/sherlock | optional-skills | 5 | no | step-headings (2 agent, 3 script) |
| security/unbroker | optional-skills | 8 | no | procedure-list (6 agent, 1 script, 1 skill) |
| security/web-pentest | optional-skills | 6 | no | step-headings (4 agent, 2 script) |
| smart-home/openhue | optional-skills | 5 | yes | sections (2 agent, 3 script) |
| social-media/reddit-reading | optional-skills | 7 | yes | sections (4 agent, 3 script) |
| software-development/ast-grep | optional-skills | 3 | no | step-headings (2 agent, 1 script) |
| software-development/code-wiki | optional-skills | 12 | no | step-headings (8 agent, 4 script) |
| software-development/grill-me | optional-skills | 3 | no | first-ordered-list (3 agent) |
| software-development/hermes-agent-skill-authoring | optional-skills | 7 | no | procedure-list (6 agent, 1 script) |
| software-development/inspecting-hermes-desktop-dom | optional-skills | 7 | yes | sections (2 agent, 5 script) |
| software-development/node-inspect-debugger | optional-skills | 11 | yes | sections (1 agent, 9 script, 1 skill) |
| software-development/pr-lens | optional-skills | 6 | no | procedure-list (1 agent, 5 script) |
| software-development/python-debugpy | optional-skills | 12 | yes | sections (1 agent, 11 script) |
| software-development/rest-graphql-debug | optional-skills | 7 | no | step-headings (1 agent, 6 script) |
| software-development/subagent-driven-development | optional-skills | 4 | no | step-headings (4 script) |
| web-development/cloudflare-temporary-deploy | optional-skills | 6 | no | procedure-list (1 agent, 5 script) |
| web-development/har-derived-api-client | optional-skills | 7 | no | procedure-list (7 agent) |
| web-development/page-agent | optional-skills | 4 | no | first-ordered-list (4 agent) |
| web-development/publish-site | optional-skills | 5 | no | step-headings (5 script) |
| web-development/scrollcraft | optional-skills | 6 | no | step-headings (3 agent, 3 script) |
| yuanbao | optional-skills | 3 | no | procedure-list (3 agent) |

