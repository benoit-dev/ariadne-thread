"""Behavior tests for the Ariadne's Thread backend, driven through HTTP.

Run with the Python that has Hermes installed:
    python -m pytest tests/test_plugin_api.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import sys
from pathlib import Path
from textwrap import dedent
from typing import Callable, NamedTuple

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
    assert client.get("/skills").json() == {"skills": [], "count": 0}
    assert client.get("/profiles").json() == {"profiles": ["default"], "serving": "default"}
    health = client.get("/health").json()
    assert health["name"] == "ariadne-thread"
    assert health["version"] == "0.2.2"
    assert health["sources"] == {"crons": False, "skills_root": False}


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
    assert client.get("/skills", params={"profile": "ghost"}).status_code == 404


def test_profiles_name_the_profile_this_backend_serves(client, home, monkeypatch):
    work = home / "profiles" / "work"
    work.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(work))

    assert client.get("/profiles").json()["serving"] == "work"


def test_other_profiles_are_listed_and_read_only(client, home):
    work = home / "profiles" / "work"
    write_skill(work / "skills", "work-skill")
    write_json(work / "cron" / "jobs.json", {"jobs": [{"name": "nightly", "skills": ["work-skill"]}]})
    (home / "profiles" / ".deleted").mkdir(parents=True)
    (home / "profiles" / "Not_Valid").mkdir()

    assert client.get("/profiles").json() == {"profiles": ["default", "work"], "serving": "default"}

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
    used_by = client.get("/skills/work-external", params={"profile": "work"}).json()["used_by"]
    assert [(u["kind"], u["name"]) for u in used_by] == [("cron", "nightly")]


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
    write_json(
        home / "cron" / "jobs.json",
        {"jobs": [
            {"id": "jb", "name": "b-job", "skills": ["arxiv"], "deliver": ["telegram", "discord:9"], "last_status": "error"},
            {"id": "ja", "name": "a-job", "skill": "research/arxiv", "enabled": False},
            {"id": "jx", "name": "x", "skills": ["other"]},
        ]},
    )

    detail = client.get("/skills/arxiv").json()

    assert "used_by_crons" not in detail
    assert detail["used_by"] == [
        {"kind": "cron", "id": "ja", "name": "a-job", "deliver": "local", "status": "paused", "health": "paused"},
        {"kind": "cron", "id": "jb", "name": "b-job", "deliver": "telegram,discord:9", "status": "error", "health": "error"},
    ]


def test_unreadable_cron_file_is_reported_not_swallowed(client, home):
    (home / "cron").mkdir()
    (home / "cron" / "jobs.json").write_text("{not json")

    response = client.get("/crons")

    assert response.status_code == 500
    assert "jobs.json" in response.json()["detail"]


def test_the_backend_never_reads_or_writes_the_webhook_store(client, home):
    """Webhook routes hold their HMAC secrets, so the page reads them from core's /api/webhooks."""
    write_skill(home / "skills", "code-review")
    store = home / "webhook_subscriptions.json"
    write_json(store, {"gh": {"secret": "s3cr3t-hmac-value-1234", "prompt": "Old", "skills": ["code-review"]}})
    before = store.read_bytes()

    responses = [
        client.get("/webhooks"),
        client.get("/skills/code-review"),
        client.get("/health"),
        client.put("/webhooks/gh/prompt", json={"expected": "Old", "content": "New"}),
        client.get("/history", params={"kind": "webhook-prompt", "ref": "gh"}),
    ]

    assert [r.status_code for r in responses] == [404, 200, 200, 404, 400]
    assert not any("s3cr3t" in r.text for r in responses)
    assert responses[1].json()["used_by"] == []
    assert store.read_bytes() == before


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


def test_the_deliver_step_carries_the_stored_targets_unresolved(client, home):
    """Targets are shown as stored; the page asks core's /api/cron/delivery-targets which can receive."""
    write_json(
        home / "cron" / "jobs.json",
        {"jobs": [{"id": "j1", "name": "n", "prompt": "x", "deliver": "origin,telegram", "failure_deliver": "slack"}]},
    )

    (job,) = client.get("/crons").json()["jobs"]

    deliver = job["sequence"][-1]
    assert (deliver["deliver"], deliver["failure_deliver"], deliver["job_id"]) == ("origin,telegram", "slack", "j1")
    assert client.get("/crons/j1/delivery").status_code == 404


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


def test_a_whole_skill_save_is_refused_when_the_file_changed_since_it_was_read(client, home):
    md = write_skill(home / "skills", "target")
    read = client.get("/skills/target").json()["raw"]
    md.write_text(VALID_SKILL.format(name="target", description="changed elsewhere"))

    stale = client.put("/skills/target", json={"content": read.replace("Do the thing.", "Mine."), "expected": read})

    assert stale.status_code == 409
    assert "changed elsewhere" in md.read_text()


def cron_job(client, home, **fields):
    write_json(home / "cron" / "jobs.json", {"jobs": [{"id": "j1", "name": "job", "skills": ["s"], **fields}]})
    return client.get("/crons").json()["jobs"][0]


def test_a_healthy_run_is_ok(client, home):
    job = cron_job(client, home, last_status="ok", last_error=None, failure_streak=0, next_run_at="2026-11-09T09:00:00+00:00")

    assert (job["status"], job["health"], job["last_status"]) == ("ok", "ok", "ok")
    assert (job["last_error"], job["failure_streak"], job["unattached"]) == (None, 0, False)
    assert job["next_run_at"] == "2026-11-09T09:00:00+00:00"
    assert job["issues"] == []


def test_a_failed_run_is_an_error_with_its_message_and_streak(client, home):
    job = cron_job(client, home, last_status="error", last_error="boom: API 500", failure_streak=3)

    assert (job["status"], job["health"]) == ("error", "error")
    assert (job["last_error"], job["failure_streak"]) == ("boom: API 500", 3)
    assert job["issues"] == ["Last run error: boom: API 500", "Failed 3 runs in a row"]
    assert cron_job(client, home, last_status="error", failure_streak=1)["issues"] == ["Last run error", "Failed 1 run in a row"]


def test_a_job_blocked_on_config_is_blocked_not_ok(client, home):
    job = cron_job(client, home, last_status="blocked_config", last_error="no API key for provider")

    assert (job["status"], job["health"]) == ("blocked_config", "blocked")
    assert job["issues"] == ["Last run blocked_config: no API key for provider"]
    assert cron_job(client, home, last_status="blocked_config")["issues"] == ["Last run blocked_config"]


def test_a_paused_job_says_since_when_and_why(client, home):
    job = cron_job(client, home, enabled=False, state="paused", last_status="ok", paused_at="2026-11-08T09:00:00+00:00", paused_reason="operator hold")

    assert (job["status"], job["health"]) == ("paused", "paused")
    assert (job["paused_at"], job["paused_reason"]) == ("2026-11-08T09:00:00+00:00", "operator hold")
    assert job["issues"] == ["Paused since 2026-11-08T09:00:00+00:00: operator hold"]
    assert cron_job(client, home, enabled=False, paused_at="2026-11-08")["issues"] == ["Paused since 2026-11-08"]
    assert cron_job(client, home, enabled=False, paused_reason="hold")["issues"] == ["Paused: hold"]
    assert cron_job(client, home, enabled=False)["issues"] == ["Paused"]


def test_a_finished_repeat_is_completed_and_not_a_problem(client, home):
    job = cron_job(client, home, enabled=False, state="completed", last_status="ok", repeat={"times": 3, "completed": 3})

    assert (job["status"], job["health"]) == ("completed", "other")
    assert job["issues"] == []


def test_a_job_that_never_ran_is_never(client, home):
    for last_status in (None, ""):
        job = cron_job(client, home, last_status=last_status)
        assert (job["status"], job["health"], job["last_status"]) == ("never", "never", last_status)
        assert job["issues"] == []


def test_an_unknown_status_is_shown_verbatim_and_never_counts_as_ok(client, home):
    job = cron_job(client, home, last_status="quantum_flux")

    assert (job["status"], job["health"], job["last_status"]) == ("quantum_flux", "other", "quantum_flux")
    assert job["issues"] == ["Last run quantum_flux"]
    delivery = cron_job(client, home, last_status="delivery_failed", last_delivery_error="chat not found")
    assert (delivery["status"], delivery["health"]) == ("delivery_failed", "other")
    assert delivery["issues"] == ["Last run delivery_failed", "Delivery: chat not found"]
    for status in ("interrupted", "cancelled"):
        assert cron_job(client, home, last_status=status)["health"] == "other"


def test_issues_come_in_a_fixed_order_and_flag_jobs_with_nothing_attached(client, home):
    job = cron_job(client, home, skills=[], last_status="error", last_error="boom", failure_streak=2, last_delivery_error="403")

    assert job["unattached"] is True
    assert job["issues"] == ["Last run error: boom", "Failed 2 runs in a row", "Delivery: 403", "No skill or script attached"]
    assert cron_job(client, home, skills=[], script="x.py")["unattached"] is False
    assert cron_job(client, home, skills=[], no_agent=True)["unattached"] is False


def test_a_job_shows_its_config_without_credentials_or_base_url(client, home):
    job = cron_job(
        client,
        home,
        model="anthropic/claude-sonnet-4-5-20250929",
        provider="anthropic",
        base_url="https://llm.internal/v1",
        enabled_toolsets=["web", "terminal"],
        profile="work",
        workdir="/home/dev/projects/some-quite-long-nested-folder/app",
        context_from=["j0"],
        repeat={"times": 3, "completed": 1},
        no_agent=True,
        api_key="sk-live-should-never-appear",
        prompt="p",
    )

    assert job["config"] == {
        "model": "anthropic/claude-sonnet-4-5-20250929",
        "provider": "anthropic",
        "enabled_toolsets": ["web", "terminal"],
        "profile": "work",
        "workdir": "/home/dev/projects/some-quite-long-nested-folder/app",
        "context_from": ["j0"],
        "repeat": {"times": 3, "completed": 1},
        "no_agent": True,
    }
    assert "llm.internal" not in json.dumps(job) and "should-never-appear" not in json.dumps(job)


def test_empty_config_values_are_left_out(client, home):
    job = cron_job(client, home, model=None, provider="", enabled_toolsets=[], context_from=[], workdir=None, no_agent=False)

    assert job["config"] == {}


@pytest.mark.parametrize(
    "key, value",
    [
        ("model", "sk-proj-FAKEFAKEFAKE"),
        ("provider", "ghp_FAKEFAKEFAKEFAKE"),
        ("workdir", "https://deploy:hunter2@git.example.com/repo.git"),
        ("workdir", "https://git.example.com/repo.git?access_token=abc123"),
        ("model", "AKIAFAKEFAKEFAKEFAKE"),
        ("provider", "xoxb-FAKE-FAKE-FAKE"),
        ("model", "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"),
        ("model", "dGhpcyBpcyBhIHNlY3JldCBrZXkgdGhhdCBpcyBsb25nIGVub3VnaA=="),
        ("enabled_toolsets", ["web", "ghp_FAKEFAKEFAKEFAKE"]),
        ("repeat", {"times": 2, "note": "xoxp-FAKE-FAKE-FAKE"}),
    ],
)
def test_a_config_value_that_looks_like_a_credential_is_dropped(client, home, key, value):
    job = cron_job(client, home, no_agent=True, **{key: value})

    assert job["config"] == {"no_agent": True}


def test_a_credential_in_one_field_does_not_hide_the_others(client, home):
    job = cron_job(client, home, model="sk-proj-FAKEFAKEFAKE", provider="openai", workdir="/srv/app")

    assert job["config"] == {"provider": "openai", "workdir": "/srv/app"}


def write_run(home: Path, job_id: str, stamp: str, text: str = "output") -> Path:
    path = home / "cron" / "output" / job_id / f"{stamp}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_runs_are_listed_newest_first_with_a_limit_and_sizes(client, home):
    write_run(home, "j1", "2026-11-07_09-00-00", "a")
    write_run(home, "j1", "2026-11-08_10-00-00", "bbb")
    write_run(home, "j1", "2026-11-08_08-30-00", "cc")
    (home / "cron" / "output" / "j1" / "notes.md").write_text("not a run")
    (home / "cron" / "output" / "j1" / "2026-11-09_00-00-00.txt").write_text("not a run")
    (home / "cron" / "output" / "j1" / "2026-11-09_01-00-00.md").mkdir()
    (home / "cron" / "output" / "j1" / "2026-11-09_02-00-00.md").symlink_to(home / "cron" / "output" / "j1" / "notes.md")

    body = client.get("/crons/j1/runs").json()

    assert body["job_id"] == "j1" and body["total"] == 3
    assert body["runs"] == [
        {"id": "2026-11-08_10-00-00", "at": "2026-11-08T10:00:00", "size": 3},
        {"id": "2026-11-08_08-30-00", "at": "2026-11-08T08:30:00", "size": 2},
        {"id": "2026-11-07_09-00-00", "at": "2026-11-07T09:00:00", "size": 1},
    ]
    limited = client.get("/crons/j1/runs", params={"limit": 2}).json()
    assert [r["id"] for r in limited["runs"]] == ["2026-11-08_10-00-00", "2026-11-08_08-30-00"]
    assert limited["total"] == 3
    assert client.get("/crons/j1/runs", params={"limit": 0}).status_code == 422
    assert client.get("/crons/j1/runs", params={"limit": 201}).status_code == 422


def test_a_job_without_output_has_no_runs(client, home):
    assert client.get("/crons/nothing-yet/runs").json() == {"job_id": "nothing-yet", "runs": [], "total": 0}
    assert not (home / "cron").exists()


def test_one_run_is_read_with_its_time_and_size(client, home):
    write_run(home, "j1", "2026-11-08_10-00-00", "# Result\nall good\n")

    body = client.get("/crons/j1/runs/2026-11-08_10-00-00").json()

    assert body == {
        "job_id": "j1", "id": "2026-11-08_10-00-00", "at": "2026-11-08T10:00:00",
        "size": 18, "truncated": False, "content": "# Result\nall good\n",
    }
    assert client.get("/crons/j1/runs/2026-11-08_11-00-00").status_code == 404
    assert client.get("/crons/other/runs/2026-11-08_10-00-00").status_code == 404


def test_a_big_run_is_cut_at_the_script_limit_and_bad_bytes_are_replaced(client, home, api):
    write_run(home, "j1", "2026-11-08_10-00-00", "x" * (api.MAX_SCRIPT_BYTES + 10))
    (home / "cron" / "output" / "j1" / "2026-11-08_11-00-00.md").write_bytes(b"ok \xff\xfe end")

    big = client.get("/crons/j1/runs/2026-11-08_10-00-00").json()
    odd = client.get("/crons/j1/runs/2026-11-08_11-00-00")

    assert big["truncated"] is True and len(big["content"]) == api.MAX_SCRIPT_BYTES
    assert big["size"] == api.MAX_SCRIPT_BYTES + 10
    assert odd.status_code == 200 and odd.json()["content"] == "ok �� end"


@pytest.mark.parametrize("bad", ["%2E%2E", "a..b", ".hidden", "a%20b", "a%00b", "x" * 129])
def test_a_malformed_job_id_is_a_400(client, home, bad):
    assert client.get(f"/crons/{bad}/runs").status_code == 400
    assert client.get(f"/crons/{bad}/runs/2026-11-08_10-00-00").status_code == 400


@pytest.mark.parametrize("bad", ["%2E%2E", "latest", "2026-11-08", "2026-11-08_10-00-00.md", "2026-11-08_10-00-00%0A", "2026-11-08_10-00-0%EF%BC%90"])
def test_a_malformed_run_id_is_a_400(client, home, bad):
    write_run(home, "j1", "2026-11-08_10-00-00")

    assert client.get(f"/crons/j1/runs/{bad}").status_code == 400


@pytest.mark.parametrize("bad", ["a/b", "../../jobs", "a%2Fb", "..%2F..%2Fcron", ".."])
def test_ids_with_slashes_never_reach_a_file_outside_the_output_folder(client, home, bad):
    write_json(home / "cron" / "jobs.json", {"jobs": [{"id": "j1", "name": "job", "prompt": "TOP-SECRET-JOBS"}]})
    write_run(home, "j1", "2026-11-08_10-00-00")

    for url in (f"/crons/{bad}/runs", f"/crons/j1/runs/{bad}", f"/crons/{bad}/runs/2026-11-08_10-00-00"):
        response = client.get(url)
        assert response.status_code in (400, 404), url
        assert "TOP-SECRET-JOBS" not in response.text


def test_a_run_file_linked_to_somewhere_else_is_refused(client, home, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "passwd.txt"
    outside.write_text("root:secret")
    folder = home / "cron" / "output" / "j1"
    folder.mkdir(parents=True)
    (folder / "2026-11-08_10-00-00.md").symlink_to(outside)

    response = client.get("/crons/j1/runs/2026-11-08_10-00-00")

    assert response.status_code == 403
    assert "secret" not in response.text
    assert client.get("/crons/j1/runs").json()["runs"] == []


def test_runs_of_another_profile_are_readable_but_belong_to_that_profile(client, home):
    work = home / "profiles" / "work"
    write_run(work, "j1", "2026-11-06_07-00-00", "work output")

    listed = client.get("/crons/j1/runs", params={"profile": "work"}).json()
    read = client.get("/crons/j1/runs/2026-11-06_07-00-00", params={"profile": "work"})

    assert [r["id"] for r in listed["runs"]] == ["2026-11-06_07-00-00"]
    assert read.json()["content"] == "work output"
    assert client.get("/crons/j1/runs").json()["runs"] == []
    assert client.get("/crons/j1/runs/2026-11-06_07-00-00").status_code == 404
    assert client.get("/crons/j1/runs", params={"profile": "ghost"}).status_code == 404


def test_scripts_report_the_ref_their_history_is_kept_under(client, home):
    scripts = home / "scripts"
    (scripts / "sub").mkdir(parents=True)
    (scripts / "sub" / "x.sh").write_text("echo x\n")

    for spelling in ("sub/x.sh", "~/.hermes/scripts/sub/x.sh", str(scripts / "sub" / "x.sh")):
        assert client.get("/scripts", params={"path": spelling}).json()["ref"] == "sub/x.sh"


class HistoryCase(NamedTuple):
    kind: str
    ref: str
    old: str
    new: str
    save: Callable[[str, str], object]
    current: Callable[[], str]


def skill_case(client, home):
    md = write_skill(home / "skills", "target")
    new = VALID_SKILL.format(name="target", description="changed")
    return HistoryCase(
        "skill", "target", md.read_text(), new,
        lambda expected, content: client.put("/skills/target", json={"expected": expected, "content": content}),
        md.read_text,
    )


def script_case(client, home):
    script = home / "scripts" / "sub" / "job.sh"
    script.parent.mkdir(parents=True)
    script.write_text("echo old\n")
    return HistoryCase(
        "script", "sub/job.sh", "echo old\n", "echo new\n",
        lambda expected, content: client.put("/scripts", json={"path": "sub/job.sh", "expected": expected, "content": content}),
        script.read_text,
    )


def cron_prompt_case(client, home):
    cron_jobs = pytest.importorskip("cron.jobs")
    job_id = cron_jobs.create_job(prompt="Old prompt", schedule="every 1h", name="hourly")["id"]
    return HistoryCase(
        "cron-prompt", job_id, "Old prompt", "New prompt",
        lambda expected, content: client.put(f"/crons/{job_id}/prompt", json={"expected": expected, "content": content}),
        lambda: cron_jobs.get_job(job_id)["prompt"],
    )


def cron_deliver_case(client, home):
    cron_jobs = pytest.importorskip("cron.jobs")
    job_id = cron_jobs.create_job(prompt="p", schedule="every 1h", name="hourly", deliver="local")["id"]
    return HistoryCase(
        "cron-deliver", job_id, "local", "telegram",
        lambda expected, content: client.put(f"/crons/{job_id}/deliver", json={"expected": expected, "content": content}),
        lambda: cron_jobs.get_job(job_id)["deliver"],
    )


HISTORY_CASES = {
    "skill": skill_case,
    "script": script_case,
    "cron-prompt": cron_prompt_case,
    "cron-deliver": cron_deliver_case,
}


@pytest.fixture(params=list(HISTORY_CASES))
def case(request, client, home):
    return HISTORY_CASES[request.param](client, home)


def history_root(home: Path) -> Path:
    return home / "plugin-data" / "ariadne-thread" / "history"


def versions(client, case, **params) -> list[dict]:
    response = client.get("/history", params={"kind": case.kind, "ref": case.ref, **params})
    assert response.status_code == 200, response.text
    return response.json()["versions"]


def version(client, case, version_id: str, **params):
    return client.get("/history/version", params={"kind": case.kind, "ref": case.ref, "id": version_id, **params})


def revert(client, case, version_id: str, expected: str, **params):
    body = {"kind": case.kind, "ref": case.ref, "id": version_id, "expected": expected}
    return client.post("/history/revert", params=params, json=body)


def test_every_save_keeps_the_text_it_replaces(client, home, case):
    saved = case.save(case.old, case.new)

    assert saved.status_code == 200, saved.text
    assert case.current() == case.new
    (listed,) = versions(client, case)
    assert listed["size"] == len(case.old.encode()) and listed["saved_at"].endswith("+00:00")
    body = version(client, case, listed["id"]).json()
    assert (body["kind"], body["ref"], body["id"], body["saved_at"]) == (case.kind, case.ref, listed["id"], listed["saved_at"])
    assert (body["content"], body["current"]) == (case.old, case.new)
    assert body["diff"].startswith(f"--- current\n+++ version {listed['saved_at']}\n")


def test_history_files_live_under_plugin_data_and_stay_private(client, home, case):
    case.save(case.old, case.new)

    (path,) = history_root(home).rglob("*.json")

    assert path.relative_to(history_root(home)).parts[0] == case.kind
    entry = json.loads(path.read_text())
    assert set(entry) == {"kind", "ref", "id", "saved_at", "content"}
    assert (entry["kind"], entry["ref"], entry["content"], path.stem) == (case.kind, case.ref, case.old, entry["id"])
    assert len(entry["id"]) == 20 and entry["id"].isdigit()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for folder in (home / "plugin-data", history_root(home), path.parent.parent, path.parent):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_a_save_that_changes_nothing_records_nothing(client, home, case):
    saved = case.save(case.old, case.old)

    assert saved.status_code == 200, saved.text
    assert versions(client, case) == []
    assert not (home / "plugin-data").exists()


def test_a_refused_save_records_nothing(client, home, case):
    stale = case.save("not what is on disk", case.new)

    assert stale.status_code == 409
    assert case.current() == case.old
    assert versions(client, case) == []


def test_a_rejected_skill_edit_records_nothing(client, home):
    case = skill_case(client, home)

    response = case.save(case.old, "no frontmatter")

    assert response.status_code == 422
    assert versions(client, case) == []


def test_a_save_fails_and_writes_nothing_when_its_history_cannot_be_written(client, home, case):
    (home / "plugin-data").write_text("a file where the history folder should go")

    response = case.save(case.old, case.new)

    assert response.status_code == 500
    assert "nothing was changed" in response.json()["detail"]
    assert case.current() == case.old


def test_only_the_newest_ten_versions_are_kept(client, home):
    case = skill_case(client, home)
    texts = [case.old] + [VALID_SKILL.format(name="target", description=f"edit {i}") for i in range(1, 13)]
    for before, after in zip(texts, texts[1:]):
        assert case.save(before, after).status_code == 200

    listed = versions(client, case)
    kept = [version(client, case, v["id"]).json()["content"] for v in listed]

    assert len(listed) == 10 == len(list(history_root(home).rglob("*.json")))
    assert kept == texts[11:1:-1]
    assert [v["id"] for v in listed] == sorted((v["id"] for v in listed), reverse=True)


def test_a_step_edit_records_the_whole_skill_file(client, home):
    write_skill(home / "skills", "steps", body=STEP_SKILL)
    step = step_of(client, "steps", 0)

    edit = client.put("/skills/steps/span", json={"start": step["span"][0], "end": step["span"][1], "expected": step["body"], "content": "Changed."})

    assert edit.status_code == 200, edit.text
    (listed,) = client.get("/history", params={"kind": "skill", "ref": "steps"}).json()["versions"]
    saved = client.get("/history/version", params={"kind": "skill", "ref": "steps", "id": listed["id"]}).json()
    assert saved["content"] == STEP_SKILL
    assert "Changed." in saved["current"]


def test_a_version_shows_the_current_text_as_a_diff_and_survives_a_deleted_target(client, home):
    case = script_case(client, home)
    case.save("echo old\n", "echo new\n")
    (listed,) = versions(client, case)

    body = version(client, case, listed["id"]).json()

    assert body["diff"].splitlines() == [
        "--- current", f"+++ version {listed['saved_at']}", "@@ -1 +1 @@", "-echo new", "+echo old",
    ]
    (home / "scripts" / "sub" / "job.sh").unlink()
    gone = version(client, case, listed["id"]).json()
    assert gone["current"] is None and gone["content"] == "echo old\n"
    assert gone["diff"].splitlines()[2:] == ["@@ -0,0 +1 @@", "+echo old"]


def test_reverting_writes_the_old_text_back_and_keeps_the_text_it_replaced(client, home, case):
    case.save(case.old, case.new)
    (listed,) = versions(client, case)

    reverted = revert(client, case, listed["id"], expected=case.new)

    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["reverted"] == listed["id"]
    assert case.current() == case.old
    newest, oldest = versions(client, case)
    assert oldest["id"] == listed["id"]
    assert version(client, case, newest["id"]).json()["content"] == case.new
    undone = revert(client, case, newest["id"], expected=case.old)
    assert undone.status_code == 200 and case.current() == case.new


def test_reverting_from_a_stale_preview_changes_nothing(client, home, case):
    case.save(case.old, case.new)
    (listed,) = versions(client, case)

    stale = revert(client, case, listed["id"], expected="what the preview showed before someone else edited")

    assert stale.status_code == 409
    assert case.current() == case.new
    assert versions(client, case) == [listed]


def test_reverting_is_refused_under_another_profile(client, home, case):
    case.save(case.old, case.new)
    (listed,) = versions(client, case)
    (home / "profiles" / "work").mkdir(parents=True)

    response = revert(client, case, listed["id"], expected=case.new, profile="work")

    assert response.status_code == 403
    assert case.current() == case.new
    assert versions(client, case) == [listed]


def test_other_profiles_history_is_read_from_their_own_home(client, home):
    case = skill_case(client, home)
    case.save(case.old, case.new)
    work = home / "profiles" / "work"
    folder = work / "plugin-data" / "ariadne-thread" / "history" / "skill" / hashlib.sha256(b"target").hexdigest()[:24]
    folder.mkdir(parents=True)
    entry = {"kind": "skill", "ref": "target", "id": "0" * 19 + "7", "saved_at": "2026-11-01T00:00:00+00:00", "content": "work text"}
    (folder / f"{entry['id']}.json").write_text(json.dumps(entry))

    assert [v["id"] for v in versions(client, case, profile="work")] == [entry["id"]]
    assert version(client, case, entry["id"], profile="work").json()["content"] == "work text"
    assert version(client, case, entry["id"]).status_code == 404


def test_reading_history_creates_nothing(client, home):
    (home / "profiles" / "work").mkdir(parents=True)
    case = skill_case(client, home)

    assert versions(client, case) == []
    assert versions(client, case, profile="work") == []
    assert not (home / "plugin-data").exists() and not (home / "profiles" / "work" / "plugin-data").exists()


def test_history_rejects_unknown_kinds_and_malformed_ids(client, home):
    ref = {"ref": "target"}

    assert client.get("/history", params={"kind": "nope", **ref}).status_code == 400
    assert client.get("/history/version", params={"kind": "nope", "id": "0" * 20, **ref}).status_code == 400
    revert_body = {"kind": "nope", "ref": "target", "id": "0" * 20, "expected": ""}
    assert client.post("/history/revert", json=revert_body).status_code == 400
    for bad in ("1", "0" * 19, "0" * 21, "../" + "0" * 17, "a" * 20, "0" * 19 + "\n", "0" * 19 + "٠"):
        params = {"kind": "skill", "id": bad, **ref}
        assert client.get("/history/version", params=params).status_code == 400, repr(bad)
        assert client.post("/history/revert", json={**revert_body, "kind": "skill", "id": bad}).status_code == 400, repr(bad)
    assert client.get("/history/version", params={"kind": "skill", "id": "0" * 20, **ref}).status_code == 404
    assert client.post("/history/revert", json={**revert_body, "kind": "skill"}).status_code == 404
