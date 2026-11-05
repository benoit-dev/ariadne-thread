"""Behavior tests for the Ariadne's Thread backend, driven through HTTP.

Run with the Python that has Hermes installed:
    python -m pytest tests/test_plugin_api.py
"""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("hermes_constants")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

API_PATH = Path(__file__).resolve().parent.parent / "dashboard" / "plugin_api.py"

VALID_SKILL = dedent(
    """\
    ---
    name: {name}
    description: {description}
    ---

    # {name}

    Do the thing.
    """
)


@pytest.fixture(scope="session")
def api():
    spec = importlib.util.spec_from_file_location("ariadne_plugin_api", API_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(api, home):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/plugins/ariadne-thread")
    return TestClient(app, base_url="http://testserver/api/plugins/ariadne-thread")


def write_skill(skills_root: Path, rel: str, description: str = "A test skill", body: str | None = None) -> Path:
    folder = skills_root / rel
    folder.mkdir(parents=True, exist_ok=True)
    md = folder / "SKILL.md"
    md.write_text(body if body is not None else VALID_SKILL.format(name=folder.name, description=description))
    return md


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def names(response) -> list[str]:
    return [s["name"] for s in response.json()["skills"]]


def test_empty_home_has_empty_lists_and_no_errors(client):
    assert client.get("/crons").json() == {"jobs": [], "total": 0, "categories": {}, "updated_at": ""}
    assert client.get("/webhooks").json() == {"routes": []}
    assert client.get("/skills").json() == {"skills": [], "count": 0}
    assert client.get("/profiles").json() == {"profiles": ["default"]}
    health = client.get("/health").json()
    assert health["name"] == "ariadne-thread"
    assert health["version"] == "0.1.0"
    assert health["sources"] == {"crons": False, "webhooks": False, "skills_root": False}


def test_paths_follow_the_home_on_every_request(client, home, tmp_path_factory, monkeypatch):
    write_skill(home / "skills", "alpha")
    assert names(client.get("/skills")) == ["alpha"]

    other = tmp_path_factory.mktemp("other-home")
    write_skill(other / "skills", "beta")
    monkeypatch.setenv("HERMES_HOME", str(other))
    assert names(client.get("/skills")) == ["beta"]


def test_skill_tree_lists_nested_categories_and_symlinked_shared_skills(client, home, tmp_path_factory):
    skills = home / "skills"
    write_skill(skills, "devops/ci-pipeline")
    write_skill(skills, "top-level")
    shared = tmp_path_factory.mktemp("shared-skills")
    write_skill(shared, "team-review")
    (skills / "shared").symlink_to(shared, target_is_directory=True)

    listing = {s["name"]: s for s in client.get("/skills").json()["skills"]}

    assert set(listing) == {"ci-pipeline", "top-level", "team-review"}
    assert listing["ci-pipeline"]["category"] == "devops"
    assert listing["top-level"]["category"] == ""
    assert listing["team-review"]["category"] == "shared"
    assert listing["ci-pipeline"]["editable"] is True
    assert listing["team-review"]["editable"] is False


def test_symlink_loops_terminate_without_duplicates(client, home):
    skills = home / "skills"
    write_skill(skills, "devops/ci-pipeline")
    (skills / "devops" / "again").symlink_to(skills, target_is_directory=True)

    assert names(client.get("/skills")) == ["ci-pipeline"]


def test_discovery_skips_support_folders_and_tooling_dirs(client, home):
    skills = home / "skills"
    write_skill(skills, "writer")
    write_skill(skills / "writer", "templates/example")
    write_skill(skills, ".git/hooks-skill")
    write_skill(skills, "node_modules/pkg")

    assert names(client.get("/skills")) == ["writer"]


def test_get_skill_returns_raw_markdown_and_flow(client, home):
    write_skill(
        home / "skills",
        "research/digest",
        body=dedent(
            """\
            ---
            name: digest
            description: Summarize feeds.
            metadata:
              hermes:
                related_skills: [feed-reader]
            ---

            ## Step 1: Collect
            Pull the feeds.

            ## Step 2: Summarize
            Write the digest.
            """
        ),
    )

    detail = client.get("/skills/digest").json()

    assert detail["name"] == "digest"
    assert detail["category"] == "research"
    assert detail["description"] == "Summarize feeds."
    assert detail["related_skills"] == ["feed-reader"]
    assert detail["editable"] is True
    assert detail["raw"].startswith("---\nname: digest")
    assert detail["flow"]["strategy"] == "step-headings"
    assert [s["label"] for s in detail["flow"]["steps"]] == ["Collect", "Summarize"]


def test_listing_reports_step_counts_and_fallback(client, home):
    skills = home / "skills"
    write_skill(skills, "stepped", body="---\nname: stepped\ndescription: d\n---\n\n## Step 1: a\n\nx\n\n## Step 2: b\n\ny\n")
    write_skill(skills, "plain", body="---\nname: plain\ndescription: d\n---\n\nJust prose.\n")

    listing = {s["name"]: s for s in client.get("/skills").json()["skills"]}

    assert (listing["stepped"]["steps"], listing["stepped"]["fallback"]) == (2, False)
    assert (listing["plain"]["steps"], listing["plain"]["fallback"]) == (1, True)


def test_unknown_and_malformed_skill_names(client, home):
    write_skill(home / "skills", "real")

    assert client.get("/skills/missing").status_code == 404
    assert client.get("/skills/.hidden").status_code == 400


def test_profile_name_is_validated_and_must_exist(client, home):
    assert client.get("/skills", params={"profile": "Bad Name"}).status_code == 400
    assert client.get("/crons", params={"profile": "../escape"}).status_code == 400
    assert client.get("/webhooks", params={"profile": "ghost"}).status_code == 404


def test_other_profiles_are_listed_and_read_only(client, home):
    work = home / "profiles" / "work"
    write_skill(work / "skills", "work-skill")
    write_json(work / "cron" / "jobs.json", {"jobs": [{"name": "nightly", "skills": ["work-skill"]}]})
    (home / "profiles" / ".deleted").mkdir(parents=True)
    (home / "profiles" / "Not_Valid").mkdir()

    assert client.get("/profiles").json() == {"profiles": ["default", "work"]}

    skills = client.get("/skills", params={"profile": "work"}).json()["skills"]
    assert [(s["name"], s["editable"]) for s in skills] == [("work-skill", False)]
    assert client.get("/skills/work-skill", params={"profile": "work"}).json()["editable"] is False
    assert [j["name"] for j in client.get("/crons", params={"profile": "work"}).json()["jobs"]] == ["nightly"]
    assert client.get("/skills", params={"profile": "default"}).json()["skills"] == []

    put = client.put("/skills/work-skill", params={"profile": "work"}, json={"content": VALID_SKILL.format(name="work-skill", description="x")})
    assert put.status_code == 403


def write_config(home: Path, text: str) -> None:
    (home / "config.yaml").write_text(dedent(text))


def test_external_skill_dirs_are_listed_read_only(client, home, tmp_path_factory, monkeypatch):
    team = tmp_path_factory.mktemp("team-skills")
    write_skill(team, "review/code-review")
    by_var = tmp_path_factory.mktemp("var-skills")
    write_skill(by_var, "release-notes")
    write_skill(home / "shared-relative", "triage")
    monkeypatch.setenv("ARIADNE_TEST_SKILLS", str(by_var))
    write_config(home, f"""\
        skills:
          external_dirs:
            - {team}
            - ${{ARIADNE_TEST_SKILLS}}
            - shared-relative
            - {home / "does-not-exist"}
            - {home / "skills"}
        """)
    write_skill(home / "skills", "local-one")

    listing = {s["name"]: s for s in client.get("/skills").json()["skills"]}

    assert set(listing) == {"local-one", "code-review", "release-notes", "triage"}
    assert (listing["local-one"]["source"], listing["local-one"]["editable"]) == ("local", True)
    for name in ("code-review", "release-notes", "triage"):
        assert (listing[name]["source"], listing[name]["editable"]) == ("external", False)
    assert listing["code-review"]["category"] == "review"

    detail = client.get("/skills/code-review").json()
    assert (detail["source"], detail["editable"]) == ("external", False)
    assert detail["path"] == str(team / "review" / "code-review" / "SKILL.md")

    put = client.put("/skills/code-review", json={"content": VALID_SKILL.format(name="code-review", description="x")})
    assert put.status_code == 403
    assert "external_dirs" in put.json()["detail"]


def test_local_skills_shadow_external_ones_like_hermes(client, home, tmp_path_factory):
    first = tmp_path_factory.mktemp("first")
    second = tmp_path_factory.mktemp("second")
    write_skill(first, "deploy", description="from first")
    write_skill(second, "deploy", description="from second")
    write_skill(second, "lint", description="from second")
    write_skill(home / "skills", "lint", description="local")
    write_config(home, f"""\
        skills:
          external_dirs: [{first}, {second}]
        """)

    listing = {s["name"]: s for s in client.get("/skills").json()["skills"]}

    assert client.get("/skills").json()["count"] == 2
    assert (listing["lint"]["description"], listing["lint"]["source"]) == ("local", "local")
    assert listing["deploy"]["description"] == "from first"


def test_a_single_external_dir_string_is_accepted(client, home, tmp_path_factory):
    team = tmp_path_factory.mktemp("team")
    write_skill(team, "solo")
    write_config(home, f"skills:\n  external_dirs: {team}\n")

    assert names(client.get("/skills")) == ["solo"]


def test_broken_config_does_not_hide_local_skills(client, home):
    write_skill(home / "skills", "still-here")
    write_config(home, "skills: [unclosed\n")

    assert names(client.get("/skills")) == ["still-here"]


def test_external_dirs_follow_the_selected_profile_config(client, home, tmp_path_factory):
    work_shared = tmp_path_factory.mktemp("work-shared")
    write_skill(work_shared, "work-external")
    work = home / "profiles" / "work"
    work.mkdir(parents=True)
    write_config(work, f"skills:\n  external_dirs: [{work_shared}]\n")
    write_json(work / "cron" / "jobs.json", {"jobs": [{"name": "nightly", "skills": ["work-external"]}]})

    assert names(client.get("/skills")) == []
    assert names(client.get("/skills", params={"profile": "work"})) == ["work-external"]
    assert client.get("/skills/work-external", params={"profile": "work"}).json()["used_by_crons"] == ["nightly"]


def test_cron_category_can_come_from_an_external_skill(client, home, tmp_path_factory):
    team = tmp_path_factory.mktemp("team")
    write_skill(team, "ops/rotate-logs")
    write_config(home, f"skills:\n  external_dirs: [{team}]\n")
    write_json(home / "cron" / "jobs.json", {"jobs": [{"name": "rotate", "skills": ["rotate-logs"]}]})

    assert client.get("/crons").json()["jobs"][0]["category"] == "ops"


@pytest.mark.parametrize(
    "content, fragment",
    [
        ("   ", "empty"),
        ("# no frontmatter\n\nbody", "frontmatter"),
        ("---\nname: x\ndescription: y\nbody without closing", "not closed"),
        ("---\ndescription: y\n---\n\nbody\n", "'name'"),
        ("---\nname: x\n---\n\nbody\n", "'description'"),
        ("---\nname: x\ndescription: " + "d" * 1025 + "\n---\n\nbody\n", "1024"),
        ("---\nname: x\ndescription: y\n---\n\n   \n", "content after"),
        ("---\n- not\n- a mapping\n---\n\nbody\n", "mapping"),
    ],
)
def test_put_rejects_invalid_skill_content(client, home, content, fragment):
    md = write_skill(home / "skills", "target")
    before = md.read_text()

    response = client.put("/skills/target", json={"content": content})

    assert response.status_code == 422
    assert fragment in response.json()["detail"]
    assert md.read_text() == before


def test_put_writes_atomically_and_announces_the_change(client, home, monkeypatch):
    md = write_skill(home / "skills", "ops/target")
    md.chmod(0o640)
    sent = []
    monkeypatch.setattr("hermes_cli.plugin_events.broadcast_plugin_event", lambda *a: sent.append(a))
    new_content = "---\nname: target\ndescription: Rewritten.\n---\n\n1. First\n2. Second\n"

    response = client.put("/skills/target", json={"content": new_content})

    assert response.status_code == 200
    assert response.json()["description"] == "Rewritten."
    assert md.read_text() == new_content
    assert stat.S_IMODE(md.stat().st_mode) == 0o640
    assert sorted(p.name for p in md.parent.iterdir()) == ["SKILL.md"]
    assert sent == [("ariadne-thread", "skills.changed", {"name": "target"})]
    assert client.get("/skills/target").json()["flow"]["strategy"] == "first-ordered-list"


def test_put_succeeds_when_the_broadcaster_is_unavailable(client, home, monkeypatch):
    write_skill(home / "skills", "target")

    def broken(*args):
        raise RuntimeError("no gateway in this process")

    monkeypatch.setattr("hermes_cli.plugin_events.broadcast_plugin_event", broken)

    response = client.put("/skills/target", json={"content": VALID_SKILL.format(name="target", description="ok")})

    assert response.status_code == 200


def test_put_refuses_shared_skills_that_resolve_outside_the_skills_root(client, home, tmp_path_factory):
    shared = tmp_path_factory.mktemp("shared-skills")
    shared_md = write_skill(shared, "team-review")
    (home / "skills").mkdir()
    (home / "skills" / "shared").symlink_to(shared, target_is_directory=True)
    original = shared_md.read_text()

    response = client.put("/skills/team-review", json={"content": VALID_SKILL.format(name="team-review", description="hacked")})

    assert response.status_code == 403
    assert shared_md.read_text() == original


def test_put_refuses_a_skill_file_symlinked_to_an_outside_file(client, home, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "notes.md"
    outside.write_text(VALID_SKILL.format(name="linked", description="outside"))
    folder = home / "skills" / "linked"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").symlink_to(outside)
    original = outside.read_text()

    response = client.put("/skills/linked", json={"content": VALID_SKILL.format(name="linked", description="hacked")})

    assert response.status_code == 403
    assert outside.read_text() == original
    assert client.get("/skills/linked").json()["editable"] is False


def test_cron_category_comes_from_the_first_attached_skill_that_exists(client, home):
    skills = home / "skills"
    write_skill(skills, "research/arxiv")
    write_skill(skills, "devops/release/changelog")
    write_skill(skills, "standalone")
    write_json(
        home / "cron" / "jobs.json",
        {
            "updated_at": "2026-01-01T00:00:00Z",
            "jobs": [
                {"name": "papers", "skills": ["ghost", "arxiv"], "schedule": {"expr": "0 9 * * 1-5"}, "prompt": "Summarize."},
                {"name": "notes", "skills": ["changelog"], "deliver": "telegram"},
                {"name": "flat", "skills": ["standalone"]},
                {"name": "both", "skills": ["changelog", "arxiv"]},
                {"name": "orphan", "skills": ["ghost"], "enabled": False},
                {"name": "bare", "script": "check.py", "no_agent": True, "last_status": "error"},
            ],
        },
    )

    body = client.get("/crons").json()
    jobs = {j["name"]: j for j in body["jobs"]}

    assert body["total"] == 6
    assert body["updated_at"] == "2026-01-01T00:00:00Z"
    assert jobs["papers"]["category"] == "research"
    assert jobs["notes"]["category"] == "devops/release"
    assert jobs["flat"]["category"] == ""
    assert jobs["both"]["category"] == "devops/release"
    assert jobs["orphan"]["category"] == ""
    assert jobs["bare"]["category"] == ""
    assert body["categories"] == {"research": 1, "devops/release": 2, "": 3}
    assert "project" not in jobs["papers"]
    assert jobs["papers"]["schedule_human"] == "Weekdays at 9am"
    assert [s["name"] for s in jobs["papers"]["sequence"] if s["type"] == "skill"] == ["ghost", "arxiv"]
    assert {(s["kind"], s["kindLabel"]) for s in jobs["papers"]["sequence"] if s["type"] == "skill"} == {("skill", "skill")}
    assert jobs["orphan"]["status"] == "paused"
    assert jobs["bare"]["status"] == "error"
    assert jobs["notes"]["sequence"][-1]["label"] == "telegram"


def test_skill_detail_lists_the_crons_that_use_it(client, home):
    write_skill(home / "skills", "research/arxiv")
    write_json(home / "cron" / "jobs.json", {"jobs": [{"name": "b-job", "skills": ["arxiv"]}, {"name": "a-job", "skill": "arxiv"}, {"name": "x", "skills": ["other"]}]})

    assert client.get("/skills/arxiv").json()["used_by_crons"] == ["a-job", "b-job"]


def test_unreadable_cron_file_is_reported_not_swallowed(client, home):
    (home / "cron").mkdir()
    (home / "cron" / "jobs.json").write_text("{not json")

    response = client.get("/crons")

    assert response.status_code == 500
    assert "jobs.json" in response.json()["detail"]


def test_webhooks_never_expose_the_secret_or_a_host(client, home):
    secret = "s3cr3t-hmac-value-1234"
    write_json(
        home / "webhook_subscriptions.json",
        {
            "github-push": {
                "secret": secret,
                "description": "Repo pushes",
                "events": ["push"],
                "prompt": "Review {repository.name}",
                "skills": ["code-review"],
                "deliver": "telegram",
                "deliver_extra": {"chat_id": "42"},
                "script": "filter.py",
            },
            "broken": "not-a-route",
        },
    )

    response = client.get("/webhooks")
    route = response.json()["routes"][0]

    assert [r["name"] for r in response.json()["routes"]] == ["github-push"]
    assert secret not in response.text
    assert "secret" not in route
    assert route["path"] == "/webhooks/github-push"
    assert "127.0.0.1" not in response.text and "http://" not in response.text
    trigger, script, prompt, skill, deliver = route["sequence"]
    assert trigger["detail"] == "/webhooks/github-push"
    assert (script["label"], script["detail"]) == ("Script", "filter.py")
    assert skill["name"] == "code-review"
    assert deliver["label"] == "telegram"


def flow(api, body: str, **fm):
    return api.parse_skill_flow(fm, dedent(body))


def labels(result) -> list[str]:
    return [s["label"] for s in result["steps"]]


def test_flow_from_step_and_phase_headings(api):
    result = flow(
        api,
        """\
        # Skill

        ## Step 1: Gather inputs
        Collect what the user gave you.

        ## Phase 2 - Build
        Write the thing.

        ## Stage 3
        Ship it.

        ## Notes
        Ignored.
        """,
    )

    assert result["strategy"] == "step-headings"
    assert result["fallback"] is False
    assert labels(result) == ["Gather inputs", "Build", "Stage 3"]
    assert result["steps"][0]["detail"] == "Collect what the user gave you."
    assert result["steps"][0]["type"] == result["steps"][0]["kind"] == "prompt"


def test_flow_from_numbered_headings_prefers_the_outermost_level(api):
    result = flow(
        api,
        """\
        ## Phase 1: Investigate
        ### 1. Read the error
        text
        ### 2. Reproduce
        text
        ## Phase 2: Fix
        ### 1. Write the test
        text
        ### 2. Patch
        text
        """,
    )

    assert result["strategy"] == "step-headings"
    assert labels(result) == ["Investigate", "Fix"]


def test_flow_from_numbered_headings_without_phase_labels(api):
    result = flow(api, "### 1) Open the file\ntext\n### 2) Edit it\ntext\n")

    assert result["strategy"] == "step-headings"
    assert labels(result) == ["Open the file", "Edit it"]


def test_flow_from_procedure_section_list(api):
    result = flow(
        api,
        """\
        ## Overview
        1. Not this list
        2. Nor this one

        ## Procedure
        1. **Fetch the data.** Pull it from the API.
        2. Transform it
        3. Publish the report
        """,
    )

    assert result["strategy"] == "procedure-list"
    assert labels(result) == ["Fetch the data", "Transform it", "Publish the report"]
    assert result["steps"][0]["detail"] == "Pull it from the API."


def test_flow_from_the_first_ordered_list_anywhere(api):
    result = flow(
        api,
        """\
        Intro paragraph.

        1) Plan the work
        2) Do the work
        """,
    )

    assert result["strategy"] == "first-ordered-list"
    assert labels(result) == ["Plan the work", "Do the work"]


def test_lists_under_rules_headings_are_not_a_flow(api):
    result = flow(
        api,
        """\
        ## Rules
        1. Always confirm
        2. Never guess

        ## Other
        text
        """,
    )

    assert result["fallback"] is True


def test_headings_and_lists_inside_code_fences_are_ignored(api):
    result = flow(
        api,
        """\
        ## Setup

        ```markdown
        ## Step 1: not real
        ## Step 2: not real either
        1. fake item
        2. fake item
        ```

        ~~~
        1. tilde fenced
        2. tilde fenced
        ~~~

        Plain text only.
        """,
    )

    assert result["strategy"] == "single"
    assert result["fallback"] is True


def test_nested_ordered_lists_are_not_top_level(api):
    result = flow(api, "- group\n  1. inner one\n  2. inner two\n\nNothing else.\n")

    assert result["strategy"] == "single"


def test_loose_lists_and_fenced_blocks_between_items_stay_one_list(api):
    result = flow(
        api,
        """\
        ## Workflow
        1. Install the tool:
        ```bash
        pip install thing
        ```
        2. Run it
        3. Check the output
        """,
    )

    assert result["strategy"] == "procedure-list"
    assert labels(result) == ["Install the tool", "Run it", "Check the output"]
    assert result["steps"][0]["kind"] == "script"


def test_fallback_to_h2_sections(api):
    result = flow(api, "## When to use\nA.\n\n## Commands\nB.\n\n## Pitfalls\nC.\n")

    assert (result["strategy"], result["fallback"]) == ("sections", True)
    assert labels(result) == ["When to use", "Commands", "Pitfalls"]


def test_fallback_to_a_single_step_named_after_the_skill(api):
    result = flow(api, "Just a paragraph.\n", name="lonely", description="Does one thing.")

    assert (result["strategy"], result["fallback"]) == ("single", True)
    assert result["steps"] == [
        {"type": "prompt", "kind": "prompt", "kindLabel": "prompt", "label": "lonely", "detail": "Does one thing."}
    ]
    assert api.parse_skill_flow({}, "")["steps"][0]["label"] == "Skill"


def test_step_kinds(api):
    result = flow(
        api,
        """\
        ## Procedure
        1. Load the playbook with skill_view(name='release-notes') first
        2. Run `git log --oneline -20` and read it
        3. Build the bundle:
           ```bash
           make bundle
           ```
        4. Save a sample as JSON:
           ```json
           {"ok": true}
           ```
        5. Open `settings.yaml` and review it
        6. Use the `triage` skill to sort the results
        """,
        name="current-skill",
    )

    kinds = [(s["kind"], s.get("name")) for s in result["steps"]]
    assert kinds == [
        ("skill", "release-notes"),
        ("script", None),
        ("script", None),
        ("prompt", None),
        ("prompt", None),
        ("skill", "triage"),
    ]


def test_a_skill_does_not_reference_itself(api):
    result = flow(api, '## Step 1: a\nskill_view(name="me", file_path="references/x.md")\n## Step 2: b\ntext\n', name="me")

    assert [s["kind"] for s in result["steps"]] == ["prompt", "prompt"]


def test_labels_and_details_are_cleaned_and_bounded(api):
    long_title = "A very long title " * 8
    result = flow(
        api,
        f"""\
        ## Step 1: **Bold** and [linked](https://example.com) and `code`
        {"detail words " * 40}

        ## Step 2: {long_title}
        text
        """,
    )

    first, second = result["steps"]
    assert first["label"] == "Bold and linked and code"
    assert len(first["detail"]) <= 160 and first["detail"].endswith("…")
    assert len(second["label"]) <= 80 and second["label"].endswith("…")


def test_step_shape_matches_what_the_pipeline_renders(api):
    for body in ("## Step 1: a\nx\n## Step 2: b\ny\n", "1. one\n2. two\n", "## A\nx\n## B\ny\n", "prose"):
        for step in api.parse_skill_flow({}, body)["steps"]:
            assert set(step) >= {"type", "kind", "kindLabel", "label", "detail"}
            assert step["kind"] in {"prompt", "script", "skill"}


def test_script_steps_name_the_script_and_prompts_carry_their_full_text(client, home):
    long_prompt = "Summarize everything. " * 20
    write_json(
        home / "cron" / "jobs.json",
        {"jobs": [
            {"name": "snap", "script": "snapshot.py", "no_agent": True},
            {"name": "gate", "script": "gate.sh", "prompt": long_prompt},
        ]},
    )

    jobs = {j["name"]: j for j in client.get("/crons").json()["jobs"]}

    snap, _ = jobs["snap"]["sequence"]
    assert (snap["kind"], snap["script"]) == ("script", "snapshot.py")
    script, prompt, _ = jobs["gate"]["sequence"]
    assert (script["kind"], script["script"]) == ("script", "gate.sh")
    assert prompt["body"] == long_prompt and len(prompt["detail"]) == 80


def test_scripts_are_read_from_the_profile_scripts_folder(client, home):
    scripts = home / "scripts"
    (scripts / "sub").mkdir(parents=True)
    (scripts / "snapshot.py").write_text("print('hi')\n")
    (scripts / "sub" / "x.sh").write_text("echo x\n")

    body = client.get("/scripts", params={"path": "snapshot.py"}).json()
    assert (body["name"], body["content"], body["truncated"]) == ("snapshot.py", "print('hi')\n", False)
    assert client.get("/scripts", params={"path": "sub/x.sh"}).json()["content"] == "echo x\n"
    assert client.get("/scripts", params={"path": "~/.hermes/scripts/snapshot.py"}).status_code == 200
    assert client.get("/scripts", params={"path": str(scripts / "snapshot.py")}).status_code == 200
    assert client.get("/scripts", params={"path": "missing.py"}).status_code == 404


def test_scripts_outside_the_scripts_folder_are_refused(client, home, tmp_path_factory):
    (home / "scripts").mkdir()
    (home / ".env").write_text("TOKEN=x")
    outside = tmp_path_factory.mktemp("outside") / "evil.py"
    outside.write_text("secret")
    (home / "scripts" / "link.py").symlink_to(outside)

    for path in ("../.env", str(outside), "link.py", "~/.hermes/.env"):
        response = client.get("/scripts", params={"path": path})
        assert response.status_code == 403, path
        assert "secret" not in response.text and "TOKEN" not in response.text


def test_large_scripts_are_truncated(client, home, api):
    (home / "scripts").mkdir()
    (home / "scripts" / "big.py").write_text("x" * (api.MAX_SCRIPT_BYTES + 10))

    body = client.get("/scripts", params={"path": "big.py"}).json()

    assert body["truncated"] is True
    assert len(body["content"]) == api.MAX_SCRIPT_BYTES
    assert body["size"] == api.MAX_SCRIPT_BYTES + 10


def test_flow_steps_carry_their_section_text(api):
    result = flow(api, "## Step 1: Fetch\nRun this:\n\n```bash\ncurl x\n```\n## Step 2: Report\nDone.\n")

    first, second = result["steps"]
    assert "curl x" in first["body"]
    assert second["body"] == "Done."


STEP_SKILL = "---\r\nname: steps\r\ndescription: Two steps\r\n---\r\n\r\n# steps\r\n\r\n## Step 1: Fetch\r\n\r\nGet it.\r\n\r\n## Step 2: Report\r\n\r\nSay it.\r\n"


def step_of(client, name, index):
    return client.get(f"/skills/{name}").json()["flow"]["steps"][index]


def test_a_step_is_edited_in_place_and_the_rest_of_the_file_is_kept(client, home):
    md = write_skill(home / "skills", "steps", body=STEP_SKILL)
    step = step_of(client, "steps", 0)

    response = client.put("/skills/steps/span", json={"start": step["span"][0], "end": step["span"][1], "expected": step["body"], "content": "Get it twice.\nThen stop."})

    assert response.status_code == 200, response.text
    assert md.read_bytes().decode() == STEP_SKILL.replace("Get it.", "Get it twice.\r\nThen stop.")
    assert step_of(client, "steps", 1)["body"] == "Say it."


def test_a_step_edit_is_refused_when_the_step_changed_on_disk(client, home):
    md = write_skill(home / "skills", "steps", body=STEP_SKILL)
    step = step_of(client, "steps", 1)
    md.write_text(STEP_SKILL.replace("Say it.", "Someone else said it."))

    response = client.put("/skills/steps/span", json={"start": step["span"][0], "end": step["span"][1], "expected": step["body"], "content": "Mine."})

    assert response.status_code == 409
    assert "Someone else said it." in md.read_text()


def test_a_step_edit_that_breaks_the_skill_is_refused(client, home):
    write_skill(home / "skills", "steps", body=STEP_SKILL)
    step = step_of(client, "steps", 0)

    response = client.put("/skills/steps/span", json={"start": 999, "end": 1000, "expected": step["body"], "content": "x"})

    assert response.status_code == 409


def test_steps_of_external_skills_and_other_profiles_are_read_only(client, home, tmp_path_factory):
    external = tmp_path_factory.mktemp("external")
    write_skill(external, "steps", body=STEP_SKILL)
    (home / "config.yaml").write_text(f"skills:\n  external_dirs:\n    - {external}\n")
    (home / "profiles" / "work").mkdir(parents=True)
    step = step_of(client, "steps", 0)
    edit = {"start": step["span"][0], "end": step["span"][1], "expected": step["body"], "content": "x"}

    assert client.put("/skills/steps/span", json=edit).status_code == 403
    assert client.put("/skills/steps/span", params={"profile": "work"}, json=edit).status_code == 403


def test_a_script_is_saved_with_its_mode_and_only_from_what_was_read(client, home):
    script = home / "scripts" / "job.sh"
    script.parent.mkdir()
    script.write_text("echo old\n")
    script.chmod(0o755)

    stale = client.put("/scripts", json={"path": "job.sh", "expected": "echo older\n", "content": "echo new\n"})
    saved = client.put("/scripts", json={"path": "job.sh", "expected": "echo old\n", "content": "echo new\n"})

    assert stale.status_code == 409
    assert saved.status_code == 200, saved.text
    assert script.read_text() == "echo new\n"
    assert stat.S_IMODE(script.stat().st_mode) == 0o755


def test_scripts_outside_the_scripts_folder_cannot_be_written(client, home):
    (home / "scripts").mkdir()
    (home / ".env").write_text("TOKEN=x")

    response = client.put("/scripts", json={"path": "../.env", "expected": "TOKEN=x", "content": "TOKEN=y"})

    assert response.status_code == 403
    assert (home / ".env").read_text() == "TOKEN=x"


def test_a_cron_prompt_is_saved_through_the_hermes_cron_store(client, home):
    cron_jobs = pytest.importorskip("cron.jobs")
    job = cron_jobs.create_job(prompt="Old prompt", schedule="every 1h", name="hourly")
    listed = client.get("/crons").json()["jobs"][0]
    assert listed["id"] == job["id"]

    stale = client.put(f"/crons/{job['id']}/prompt", json={"expected": "Older prompt", "content": "New"})
    saved = client.put(f"/crons/{job['id']}/prompt", json={"expected": "Old prompt", "content": "New prompt"})

    assert stale.status_code == 409
    assert saved.status_code == 200, saved.text
    assert cron_jobs.get_job(job["id"])["prompt"] == "New prompt"
    assert client.put("/crons/missing/prompt", json={"expected": "", "content": "x"}).status_code == 404


def test_every_cron_ends_in_a_deliver_step_even_when_it_only_saves(client, home):
    write_json(
        home / "cron" / "jobs.json",
        {"jobs": [
            {"id": "a", "name": "quiet", "prompt": "x"},
            {"id": "b", "name": "loud", "prompt": "x", "deliver": ["telegram", "discord:123"], "failure_deliver": "local"},
        ]},
    )

    jobs = {j["name"]: j for j in client.get("/crons").json()["jobs"]}

    quiet, loud = jobs["quiet"]["sequence"][-1], jobs["loud"]["sequence"][-1]
    assert (quiet["type"], quiet["deliver"], quiet["job_id"]) == ("deliver", "local", "a")
    assert quiet["detail"] == "Output is saved, not sent"
    assert loud["deliver"] == loud["label"] == jobs["loud"]["deliver"] == "telegram,discord:123"
    assert loud["detail"] == "failures \u2192 local"


def test_delivery_resolves_each_target_the_way_the_scheduler_does(client, home, monkeypatch):
    pytest.importorskip("cron.scheduler_delivery")
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "777")
    write_json(home / "channel_directory.json", {"platforms": {"telegram": [{"id": "777", "name": "Ops room"}]}})
    write_json(
        home / "cron" / "jobs.json",
        {"jobs": [{
            "id": "j1", "name": "n", "prompt": "x", "deliver": "origin,telegram,nowhere,local",
            "origin": {"platform": "discord", "chat_id": "55", "chat_name": "General"},
        }]},
    )

    body = client.get("/crons/j1/delivery").json()

    origin, tg, bad = body["targets"]
    assert origin["targets"] == [{"platform": "discord", "chat_id": "55", "name": "General", "via": "origin", "thread_id": None}]
    assert tg["targets"][0]["chat_id"] == "777" and tg["targets"][0]["name"] == "Ops room"
    assert bad["token"] == "nowhere" and not bad["targets"] and "not a delivery platform" in bad["error"]
    assert [o["id"] for o in body["options"]][:2] == ["local", "origin"]
    assert client.get("/crons/missing/delivery").status_code == 404


def test_a_cron_delivery_target_is_saved_through_the_hermes_cron_store(client, home):
    cron_jobs = pytest.importorskip("cron.jobs")
    job = cron_jobs.create_job(prompt="p", schedule="every 1h", name="hourly", deliver="local")
    path = f"/crons/{job['id']}/deliver"

    stale = client.put(path, json={"expected": "telegram", "content": "discord"})
    saved = client.put(path, json={"expected": "local", "content": " local, telegram ,discord:9,telegram,"})

    assert stale.status_code == 409
    assert saved.status_code == 200, saved.text
    assert cron_jobs.get_job(job["id"])["deliver"] == "telegram,discord:9"
    cleared = client.put(path, json={"expected": "telegram,discord:9", "content": "  "})
    assert cleared.json()["deliver"] == cron_jobs.get_job(job["id"])["deliver"] == "local"
    assert client.put(path + "?profile=other", json={"expected": "local", "content": "x"}).status_code in (403, 404)


def test_a_webhook_prompt_is_saved_and_the_secret_is_kept(client, home):
    pytest.importorskip("hermes_cli.webhook")
    write_json(home / "webhook_subscriptions.json", {"gh": {"prompt": "Old", "secret": "s3cret", "events": ["push"]}})

    stale = client.put("/webhooks/gh/prompt", json={"expected": "Older", "content": "New"})
    saved = client.put("/webhooks/gh/prompt", json={"expected": "Old", "content": "New"})

    assert stale.status_code == 409
    assert saved.status_code == 200, saved.text
    assert "s3cret" not in saved.text
    stored = json.loads((home / "webhook_subscriptions.json").read_text())
    assert stored["gh"] == {"prompt": "New", "secret": "s3cret", "events": ["push"]}


def test_a_whole_skill_save_is_refused_when_the_file_changed_since_it_was_read(client, home):
    md = write_skill(home / "skills", "target")
    read = client.get("/skills/target").json()["raw"]
    md.write_text(VALID_SKILL.format(name="target", description="changed elsewhere"))

    stale = client.put("/skills/target", json={"content": read.replace("Do the thing.", "Mine."), "expected": read})

    assert stale.status_code == 409
    assert "changed elsewhere" in md.read_text()
