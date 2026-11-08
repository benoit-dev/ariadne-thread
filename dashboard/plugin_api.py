"""Ariadne's Thread: backend API for the Hermes Desktop plugin.

Read-only views over the files Hermes already keeps, plus guarded editing of
the active profile's own skills, scripts, cron prompts and webhook prompts.
Nothing here runs a job, a script or a skill, and nothing is sent anywhere:

  GET  /health                       -> {ok, name, version, sources}
  GET  /profiles                     -> {profiles}
  GET  /crons                        -> {jobs, total, categories, updated_at}; each job has its
                                        true run status, health, config and issues
  GET  /crons/{id}/delivery          -> where each deliver token resolves to
  GET  /crons/{id}/runs              -> the run output files Hermes saved, newest first
  GET  /crons/{id}/runs/{run_id}     -> one run's output
  GET  /webhooks                     -> {routes}
  GET  /skills                       -> {skills, count}
  GET  /skills/{name}                -> skill detail, raw SKILL.md, its parsed flow and used_by
  GET  /scripts?path=...             -> a cron or webhook script from <home>/scripts, with its history ref
  GET  /history?kind=&ref=           -> the saved earlier versions of one edited text
  GET  /history/version?...&id=      -> one earlier version beside the current text and a diff
  PUT  /skills/{name}                -> edit a skill (validated, atomic write)
  PUT  /skills/{name}/span           -> edit the lines of one flow step
  PUT  /scripts                      -> edit a script in <home>/scripts
  PUT  /crons/{id}/prompt            -> edit a cron job's prompt
  PUT  /crons/{id}/deliver           -> edit a cron job's delivery target
  PUT  /webhooks/{name}/prompt       -> edit a webhook route's prompt
  POST /history/revert               -> write an earlier version back through the matching PUT

Every edit carries the text the user started from and gets 409 if the file
changed since. Every edit first saves the text it replaces under
<home>/plugin-data/ariadne-thread/history, so it can be undone. The read endpoints
take an optional ?profile=<name> to look at another profile's files read-only. Paths
are resolved per request because this module can be served from a profile-scoped host
process.

Webhook HMAC secrets are never serialized, and never stored in the history.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import time
from bisect import bisect_right
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from hermes_constants import get_default_hermes_root, get_hermes_home

try:
    import yaml as _yaml

    _yaml_load: Optional[Callable[[str], object]] = _yaml.safe_load
except ImportError:  # Hermes 0.22 ships ruamel.yaml, not PyYAML.
    try:
        from ruamel.yaml import YAML

        def _yaml_load(text: str) -> object:
            return YAML(typ="safe").load(text)

    except ImportError:
        _yaml_load = None

router = APIRouter()

_log = logging.getLogger("ariadne-thread")

PLUGIN_NAME = "ariadne-thread"
PLUGIN_VERSION = "0.2.0"
MAX_DESCRIPTION_LENGTH = 1024
MAX_SCRIPT_BYTES = 256 * 1024
HISTORY_KEEP = 10

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_JOB_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_RUN_ID_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}")
_RUN_FILE_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2})\.md")
_HISTORY_ID_RE = re.compile(r"[0-9]{20}")

# Mirrors the directories Hermes itself skips when it discovers skills, so a
# template or a cached copy is not listed as a second skill.
_SKIPPED_DIRS = frozenset({
    ".git", ".github", ".hub", ".archive", ".curator_backups", ".locks", ".hermes-dist",
    ".venv", "venv", "node_modules", "site-packages", "__pycache__",
    ".tox", ".nox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
})
_SUPPORT_DIRS = frozenset({"references", "templates", "assets", "scripts"})


class Roots(NamedTuple):
    home: Path
    skills: Path
    scripts: Path
    cron_jobs: Path
    webhooks: Path
    editable: bool
    external: tuple[Path, ...] = ()


def _profile_roots(profile: Optional[str]) -> Roots:
    if profile in (None, "", "default"):
        home, editable = get_hermes_home(), True
    else:
        if not _PROFILE_RE.match(profile):
            raise HTTPException(status_code=400, detail=f"invalid profile name: {profile!r}")
        home = get_default_hermes_root() / "profiles" / profile
        if not home.is_dir():
            raise HTTPException(status_code=404, detail=f"profile not found: {profile}")
        editable = False
    skills = home / "skills"
    return Roots(
        home=home,
        skills=skills,
        scripts=home / "scripts",
        cron_jobs=home / "cron" / "jobs.json",
        webhooks=home / "webhook_subscriptions.json",
        editable=editable,
        external=_external_skill_dirs(home, skills),
    )


def _external_skill_dirs(home: Path, local: Path) -> tuple[Path, ...]:
    """``skills.external_dirs`` from the profile's config.yaml, resolved the way Hermes does:
    ``~`` and ``${VAR}`` expanded, relative entries anchored at the home, missing dirs and
    the local skills dir dropped."""
    config = home / "config.yaml"
    if _yaml_load is None or not config.is_file():
        return ()
    try:
        cfg = _yaml_load(config.read_text(encoding="utf-8-sig"))
    except Exception:
        _log.warning("config.yaml unreadable, external skill dirs skipped: %s", config)
        return ()
    skills_cfg = cfg.get("skills") if isinstance(cfg, dict) else None
    raw = skills_cfg.get("external_dirs") if isinstance(skills_cfg, dict) else None
    entries = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    local_real = local.resolve()
    dirs: list[Path] = []
    for entry in entries:
        text = str(entry).strip()
        if not text:
            continue
        p = Path(os.path.expanduser(os.path.expandvars(text)))
        p = (p if p.is_absolute() else home / p).resolve()
        if p != local_real and p not in dirs and p.is_dir():
            dirs.append(p)
    return tuple(dirs)


def _read_json(path: Path, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{label} unreadable: {exc}")


def _json_or_none(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _text_or_none(path: Path) -> Optional[str]:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None


def _checked(value: str, pattern: re.Pattern, what: str) -> str:
    """The boundary check for an id that becomes part of a file name."""
    if not pattern.fullmatch(value) or ".." in value:
        raise HTTPException(status_code=400, detail=f"invalid {what}: {value!r}")
    return value


def _skill_files(root: Path) -> list[Path]:
    found: list[Path] = []
    if not root.is_dir():
        return found
    chains = {str(root): frozenset({os.path.realpath(root)})}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        chain = chains.pop(dirpath, frozenset())
        is_skill = "SKILL.md" in filenames
        if is_skill:
            found.append(Path(dirpath) / "SKILL.md")
        kept = []
        for d in sorted(dirnames):
            if d in _SKIPPED_DIRS or (is_skill and d in _SUPPORT_DIRS):
                continue
            child = os.path.join(dirpath, d)
            real = os.path.realpath(child)
            if real in chain:
                continue
            chains[child] = chain | {real}
            kept.append(d)
        dirnames[:] = kept
    return sorted(found)


class SkillRef(NamedTuple):
    md: Path
    root: Path


def _skill_index(roots: Roots) -> dict[str, SkillRef]:
    """Skill folder name -> where it lives. Like Hermes, the local skills dir shadows the
    external dirs, which shadow each other in config order; within one dir the shallowest
    path wins."""
    index: dict[str, SkillRef] = {}
    for root in (roots.skills, *roots.external):
        for md in sorted(_skill_files(root), key=lambda p: (len(p.parts), str(p))):
            index.setdefault(md.parent.name, SkillRef(md, root))
    return index


def _resolve_skill(name: str, roots: Roots) -> SkillRef:
    if not _SKILL_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"invalid skill name: {name!r}")
    ref = _skill_index(roots).get(name)
    if ref is None:
        raise HTTPException(status_code=404, detail=f"skill not found: {name}")
    return ref


def _skill_location(ref: SkillRef, roots: Roots) -> dict:
    local = ref.root == roots.skills
    return {
        "category": _category_of(ref.md, ref.root),
        "path": str(ref.md),
        "source": "local" if local else "external",
        "editable": roots.editable and local and _within(ref.md, roots.skills),
    }


def _category_of(md: Path, skills_root: Path) -> str:
    parent = md.parent.relative_to(skills_root).parent
    return "" if parent == Path(".") else parent.as_posix()


def _within(path: Path, root: Path) -> bool:
    real_root = os.path.normcase(os.path.realpath(root))
    real = os.path.normcase(os.path.realpath(path))
    return real == real_root or real.startswith(real_root + os.sep)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    content = text.lstrip("\ufeff")
    if not content.startswith("---"):
        return {}, content.strip()
    end = re.search(r"\n---\s*\n", content[3:])
    if not end:
        return {}, content.strip()
    fm_text = content[3 : end.start() + 3]
    body = content[end.end() + 3 :].strip()
    fm: dict = {}
    if _yaml_load is not None:
        try:
            parsed = _yaml_load(fm_text)
            if isinstance(parsed, dict):
                fm = dict(parsed)
        except Exception:
            fm = {}
    return fm, body


def _json_safe(value):
    return json.loads(json.dumps(value, default=str))


def _string_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x is not None]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _credential_names(fm: dict) -> list[str]:
    names: list[str] = []
    for key in ("credentials", "required_environment_variables"):
        value = fm.get(key)
        if not isinstance(value, list):
            continue
        for entry in value:
            if isinstance(entry, dict):
                entry = entry.get("name")
            if entry:
                names.append(str(entry))
    return names


# A flow step has the same shape as a cron sequence step so one Pipeline
# component renders both.
MAX_LABEL = 80
MAX_DETAIL = 160

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_ORDERED_RE = re.compile(r"^( {0,3})(\d{1,3})[.)][ \t]+(\S.*)$")
_BULLET_RE = re.compile(r"^ {0,3}[-*+][ \t]+")
_STEP_HEAD_RE = re.compile(
    r"^(?:(?:step|phase|stage)\s*\d+(?:\.\d+)?|\d+(?:\.\d+)*[.)])(?=[\s:.\-\u2013\u2014)]|$)", re.I
)
_STEP_PREFIX_RE = re.compile(
    r"^(?:(?:step|phase|stage)\s*\d+(?:\.\d+)?|\d+(?:\.\d+)*[.)])[\s:.\-\u2013\u2014)]*", re.I
)
_PROCEDURE_HEAD_RE = re.compile(
    r"\b(?:procedure|steps?|workflow|instructions?|how to|usage|process|quick ?start|getting started"
    r"|walkthrough|playbook|method)\b",
    re.I,
)
_NON_FLOW_HEAD_RE = re.compile(
    r"\b(?:prerequisites?|requirements?|pitfalls?|gotchas?|caveats|limitations?|rules?|guidelines?|principles?"
    r"|best practices|tips|notes?|mental model|checklist|when (?:not )?to use|output|examples?"
    r"|troubleshooting|verification|references?|related|see also)\b",
    re.I,
)
_BOLD_LEAD_RE = re.compile(r"^(\*\*|__)(.+?)\1\s*[:.\-\u2013\u2014]*\s*(.*)$")
_DATA_FENCE_LANGS = frozenset({
    "json", "jsonc", "yaml", "yml", "toml", "ini", "xml", "html", "csv",
    "text", "txt", "md", "markdown", "diff", "mermaid", "output",
})
_SKILL_VIEW_RE = re.compile(r"""skill_view\s*\(\s*(?:name\s*=\s*)?['"]([^'"\s]+)['"]""")
_SKILL_PHRASE_RE = re.compile(r"\b(?:load|use|invoke|via|see)\s+(?:the\s+)?`([A-Za-z0-9][\w.-]*)`\s+skill\b", re.I)
_SKILL_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_CODE_SPAN_RE = re.compile(r"(`+)([^`]*?)\1")
_SHELL_COMMANDS = frozenset({
    "git", "gh", "python", "python3", "pip", "pip3", "uv", "node", "npm", "npx", "pnpm", "yarn", "bun",
    "curl", "wget", "bash", "sh", "zsh", "make", "docker", "kubectl", "cargo", "go", "ruby", "brew",
    "apt", "apt-get", "ls", "cat", "grep", "rg", "sed", "awk", "cd", "mkdir", "rm", "cp", "mv", "chmod",
    "tar", "ssh", "scp", "rsync", "hermes", "pytest", "ffmpeg", "jq", "systemctl", "sudo", "terraform",
    "aws", "gcloud", "helm", "pandoc", "tmux", "crontab",
})


class _Heading(NamedTuple):
    idx: int
    level: int
    text: str


class _Item(NamedTuple):
    idx: int
    end: int
    number: int
    text: str
    section: int


class _Doc(NamedTuple):
    lines: list[str]
    masked: list[bool]
    code_fences: list[tuple[int, str]]
    headings: list[_Heading]
    lists: list[list[_Item]]
    fm: dict

    def span(self, a: int, b: int) -> str:
        return "\n".join(self.lines[a:b])

    def prose(self, a: int, b: int) -> str:
        return "\n".join(ln for ln, hidden in zip(self.lines[a:b], self.masked[a:b]) if not hidden)

    def has_code_block(self, a: int, b: int) -> bool:
        return any(a <= i < b and lang not in _DATA_FENCE_LANGS for i, lang in self.code_fences)

    def next_heading(self, k: int) -> int:
        return self.headings[k + 1].idx if k + 1 < len(self.headings) else len(self.lines)

    def section_end(self, k: int) -> int:
        level = self.headings[k].level
        for h in self.headings[k + 1 :]:
            if h.level <= level:
                return h.idx
        return len(self.lines)

    def heading_before(self, idx: int) -> Optional[_Heading]:
        pos = bisect_right([h.idx for h in self.headings], idx)
        return self.headings[pos - 1] if pos else None


def _scan(body: str, fm: dict) -> _Doc:
    lines = re.split(r"\r\n|\r|\n", body.expandtabs(4))
    masked = [False] * len(lines)
    code_fences: list[tuple[int, str]] = []
    fence: Optional[tuple[str, int]] = None
    in_comment = False
    for i, line in enumerate(lines):
        m = _FENCE_RE.match(line) if ("```" in line or "~~~" in line) else None
        if fence:
            masked[i] = True
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                fence = None
        elif in_comment:
            masked[i] = True
            in_comment = "-->" not in line
        elif m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            masked[i] = True
            fence = (m.group(1)[0], len(m.group(1)))
            info = m.group(2).strip().split()
            code_fences.append((i, info[0].lower() if info else ""))
        elif line.lstrip().startswith("<!--"):
            masked[i] = True
            in_comment = "-->" not in line

    headings: list[_Heading] = []
    raw_items: list[list] = []
    container_col: Optional[int] = None
    open_item: Optional[list] = None
    prev_blank = True

    def close(end: int) -> None:
        nonlocal open_item
        if open_item is not None:
            open_item[1] = end
            open_item = None

    for i, line in enumerate(lines):
        if masked[i]:
            continue
        if not line.strip():
            prev_blank = True
            continue
        indent = len(line) - len(line.lstrip(" "))
        if container_col is not None and indent >= container_col:
            prev_blank = False
            continue
        lead = line[indent : indent + 1]
        heading = _HEADING_RE.match(line) if lead == "#" else None
        ordered = _ORDERED_RE.match(line) if lead.isdigit() else None
        bullet = _BULLET_RE.match(line) if lead in ("-", "*", "+") else None
        if heading:
            close(i)
            container_col = None
            headings.append(_Heading(i, len(heading.group(1)), heading.group(2)))
        elif ordered:
            close(i)
            container_col = ordered.start(3)
            open_item = [i, len(lines), int(ordered.group(2)), ordered.group(3), len(headings)]
            raw_items.append(open_item)
        elif bullet:
            close(i)
            container_col = bullet.end()
        elif prev_blank or container_col is None:
            close(i)
            container_col = None
        prev_blank = False
    close(len(lines))

    lists: list[list[_Item]] = []
    current: list[_Item] = []
    for raw in raw_items:
        item = _Item(*raw)
        if current and (
            item.section != current[-1].section
            or not (item.number == current[-1].number + 1 or item.number == current[-1].number <= 1)
        ):
            lists.append(current)
            current = []
        current.append(item)
    if current:
        lists.append(current)

    return _Doc(lines, masked, code_fences, headings, lists, fm)


_INLINE_MARKUP_RE = re.compile(r"[`*_\[<]")
_CODE_SPLIT_RE = re.compile(r"(`+[^`]*`+)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC_RE = re.compile(r"(?<![\w*])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*])")
_TAG_RE = re.compile(r"<[^>]+>")


def _plain(text: str) -> str:
    if not _INLINE_MARKUP_RE.search(text):
        return " ".join(text.split())
    out = []
    for part in _CODE_SPLIT_RE.split(text):
        if part.startswith("`"):
            out.append(part.strip("`"))
            continue
        part = _IMAGE_RE.sub(r"\1", part)
        part = _LINK_RE.sub(r"\1", part)
        part = _BOLD_RE.sub(r"\2", part)
        part = _ITALIC_RE.sub(r"\2", part)
        out.append(_TAG_RE.sub("", part))
    return " ".join("".join(out).split())


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit - 1)
    return text[: cut if cut > limit // 2 else limit - 1].rstrip(" ,;:.-") + "\u2026"


def _first_prose(doc: _Doc, a: int, b: int) -> str:
    for i in range(a, b):
        if doc.masked[i]:
            continue
        line = doc.lines[i].strip()
        if not line or re.fullmatch(r"[-=*_|:\s]+", line):
            continue
        if _HEADING_RE.match(doc.lines[i]):
            break
        text = _plain(re.sub(r"^(?:[-*+]|\d+[.)]|>)\s+", "", line))
        if text:
            return _clip(text, MAX_DETAIL)
    return ""


def _split_label(text: str) -> tuple[str, str]:
    lead = _BOLD_LEAD_RE.match(text.strip())
    if lead:
        label = _plain(lead.group(2)).rstrip(":.- ")
        if label:
            return _clip(label, MAX_LABEL), _plain(lead.group(3))
    plain = _plain(text)
    if len(plain) <= MAX_LABEL:
        return plain.rstrip(":"), ""
    boundary = re.search(r"[.:;!?]\s", plain[:MAX_LABEL])
    if boundary and boundary.start() >= 12:
        return plain[: boundary.start()], plain[boundary.start() + 1 :].strip()
    label = _clip(plain, MAX_LABEL)
    return label, plain[len(label) - 1 :].lstrip(" ,;:.-")


def _looks_like_shell(code: str) -> bool:
    code = code.strip()
    if code.startswith("$ "):
        return True
    tokens = code.split()
    if not tokens:
        return False
    head = tokens[0]
    if head.startswith("./"):
        return len(tokens) >= 2 or head.endswith((".sh", ".py", ".js", ".mjs"))
    return len(tokens) >= 2 and head in _SHELL_COMMANDS


def _has_inline_command(text: str) -> bool:
    return any(_looks_like_shell(m.group(2)) for m in _CODE_SPAN_RE.finditer(text))


def _skill_reference(text: str, self_name: str) -> Optional[str]:
    for pattern in (_SKILL_VIEW_RE, _SKILL_PHRASE_RE):
        for m in pattern.finditer(text):
            ref = m.group(1).rsplit("/", 1)[-1]
            if ref != self_name and _SKILL_REF_RE.match(ref):
                return ref
    return None


def _step(doc: _Doc, label: str, detail: str, a: int, b: int) -> dict:
    ref = _skill_reference(doc.span(a, b), str(doc.fm.get("name") or ""))
    if ref:
        kind = "skill"
    elif doc.has_code_block(a, b) or _has_inline_command(doc.prose(a, b)):
        kind = "script"
    else:
        kind = "prompt"
    # span: the body lines [a, b) this step came from, so PUT /skills/{name}/span can edit just them.
    step = {"type": kind, "kind": kind, "kindLabel": kind, "label": label, "detail": detail,
            "body": doc.span(a, b).strip(), "span": [a, b]}
    if ref:
        step["name"] = ref
    return step


def _heading_step(doc: _Doc, k: int, strip_prefix: bool) -> dict:
    h = doc.headings[k]
    plain = _plain(h.text)
    title = _STEP_PREFIX_RE.sub("", plain).strip(" :.-\u2013\u2014") if strip_prefix else plain
    detail = _first_prose(doc, h.idx + 1, doc.next_heading(k))
    return _step(doc, _clip(title or plain, MAX_LABEL), detail, h.idx + 1, doc.section_end(k))


def _item_step(doc: _Doc, item: _Item) -> dict:
    label, rest = _split_label(item.text)
    detail = _clip(rest, MAX_DETAIL) if rest else _first_prose(doc, item.idx + 1, item.end)
    return _step(doc, label, detail, item.idx, item.end)


def _by_step_headings(doc: _Doc) -> list[dict]:
    candidates = [
        (k, h) for k, h in enumerate(doc.headings)
        if 2 <= h.level <= 4 and _STEP_HEAD_RE.match(_plain(h.text))
    ]
    for level in sorted({h.level for _, h in candidates}):
        group = [k for k, h in candidates if h.level == level]
        if len(group) >= 2:
            return [_heading_step(doc, k, strip_prefix=True) for k in group]
    return []


def _by_procedure_list(doc: _Doc) -> list[dict]:
    for k, h in enumerate(doc.headings):
        if not _PROCEDURE_HEAD_RE.search(_plain(h.text)):
            continue
        end = doc.section_end(k)
        for items in doc.lists:
            if len(items) >= 2 and h.idx < items[0].idx < end:
                return [_item_step(doc, it) for it in items]
    return []


def _by_first_ordered_list(doc: _Doc) -> list[dict]:
    for items in doc.lists:
        if len(items) < 2:
            continue
        heading = doc.heading_before(items[0].idx)
        if heading and _NON_FLOW_HEAD_RE.search(_plain(heading.text)):
            continue
        return [_item_step(doc, it) for it in items]
    return []


def _by_sections(doc: _Doc) -> list[dict]:
    ks = [k for k, h in enumerate(doc.headings) if h.level == 2]
    return [_heading_step(doc, k, strip_prefix=False) for k in ks] if len(ks) >= 2 else []


def _whole_skill(doc: _Doc) -> list[dict]:
    label = _plain(str(doc.fm.get("name") or "")) or "Skill"
    detail = _clip(_plain(str(doc.fm.get("description") or "")), MAX_DETAIL)
    return [{"type": "prompt", "kind": "prompt", "kindLabel": "prompt", "label": label, "detail": detail}]


class _Strategy(NamedTuple):
    name: str
    parse: Callable[[_Doc], list[dict]]
    fallback: bool
    min_steps: int = 2


_STRATEGIES: tuple[_Strategy, ...] = (
    _Strategy("step-headings", _by_step_headings, False),
    _Strategy("procedure-list", _by_procedure_list, False),
    _Strategy("first-ordered-list", _by_first_ordered_list, False),
    _Strategy("sections", _by_sections, True),
    _Strategy("single", _whole_skill, True, min_steps=1),
)


def parse_skill_flow(fm: dict, body: str) -> dict:
    doc = _scan(body or "", fm if isinstance(fm, dict) else {})
    for strategy in _STRATEGIES:
        steps = strategy.parse(doc)
        if len(steps) >= strategy.min_steps:
            return {"steps": steps, "strategy": strategy.name, "fallback": strategy.fallback}
    raise AssertionError("the final strategy always yields a step")


def _humanize_schedule(expr: str) -> str:
    if not expr or expr == "?":
        return "?"
    day_names = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
    if expr.startswith("every "):
        return expr.replace("every ", "Every ")
    parts = expr.split()
    if len(parts) != 5:
        return expr
    minute, hour, dom, month, dow = parts
    time_str = ""
    if hour != "*" and minute != "*":
        try:
            h, m = int(hour), int(minute)
            ampm = "am" if h < 12 else "pm"
            disp_h = h if h <= 12 else h - 12
            disp_h = 12 if disp_h == 0 else disp_h
            time_str = f"{disp_h}{'' if m == 0 else f':{m:02d}'}{ampm}"
        except ValueError:
            time_str = f"{hour}:{minute}"
    if dow == "*" and dom == "*":
        return f"Daily at {time_str}" if time_str else "Daily"
    if dow != "*":
        try:
            days = [day_names.get(int(d), d) for d in dow.split(",")]
            day_str = ", ".join(days)
        except ValueError:
            day_str = dow
        if dow == "1,2,3,4,5" or dow == "1-5":
            return f"Weekdays at {time_str}" if time_str else "Weekdays"
        if dow == "1":
            day_str = "Monday"
        elif dow == "0":
            day_str = "Sunday"
        return f"{day_str} at {time_str}" if time_str else day_str
    if "/" in minute:
        return f"Every {minute.split('/')[1]} minutes"
    if "/" in hour:
        return f"Every {hour.split('/')[1]} hours"
    return expr


def _job_skills(job: dict) -> list[str]:
    skills = job.get("skills") or job.get("skill") or []
    if isinstance(skills, str):
        skills = [skills]
    return [str(s) for s in skills] if isinstance(skills, list) else []


def _cron_sequence(j: dict, skills: list[str]) -> list[dict]:
    prompt = j.get("prompt") or ""
    sequence = []
    if j.get("script"):
        sequence.append(
            {"type": "script", "kind": "script", "kindLabel": "script",
             "label": j["script"], "name": j["script"], "script": str(j["script"])}
        )
    if prompt and not j.get("no_agent"):
        sequence.append(
            {"type": "prompt", "kind": "prompt", "kindLabel": "prompt",
             "label": "Prompt", "detail": prompt[:80], "body": prompt}
        )
    for s in skills:
        sequence.append(
            {"type": "skill", "kind": "skill", "kindLabel": "skill",
             "label": s, "name": s}
        )
    sequence.append(_cron_deliver_step(j))
    return sequence


def _deliver_text(value, default: str = "local") -> str:
    """A job's ``deliver`` as Hermes reads it: comma-separated, lists flattened, empty is local."""
    if isinstance(value, (list, tuple)):
        value = ",".join(str(p).strip() for p in value if str(p).strip())
    return str(value).strip() if value not in (None, "") and str(value).strip() else default


def _cron_deliver_step(j: dict) -> dict:
    deliver = _deliver_text(j.get("deliver"))
    failure = _deliver_text(j.get("failure_deliver"), "")
    detail = "Output is saved, not sent" if deliver == "local" else ""
    if failure:
        detail = (detail + " \u00b7 " if detail else "") + "failures \u2192 " + failure
    return {"type": "deliver", "kind": "deliver", "label": deliver, "detail": detail,
            "deliver": deliver, "job_id": j.get("id")}


def _jobs_of(live) -> list[dict]:
    raw = live.get("jobs", []) if isinstance(live, dict) else live
    return [j for j in raw if isinstance(j, dict)] if isinstance(raw, list) else []


# What a job's last_status means for the health bar. Paused and never-run jobs have no
# last_status to look up, and any value missing here is "other", never "ok".
_STATUS_HEALTH = {"ok": "ok", "error": "error", "blocked_config": "blocked"}


def _job_status(j: dict) -> tuple[str, str]:
    """(status, health) of a job: the row text and the health bucket it counts in."""
    if not j.get("enabled", True):
        return ("completed", "other") if j.get("state") == "completed" else ("paused", "paused")
    last = j.get("last_status")
    if last in (None, ""):
        return "never", "never"
    return str(last), _STATUS_HEALTH.get(str(last), "other")


def _count(value) -> int:
    return value if type(value) is int and value > 0 else 0


def _unattached(j: dict, skills: list[str]) -> bool:
    return not skills and not j.get("script") and not j.get("no_agent")


def _job_issues(j: dict, skills: list[str], status: str, health: str) -> list[str]:
    issues = []
    last_status = str(j.get("last_status") or status)
    if j.get("last_error"):
        issues.append(f"Last run {last_status}: {j['last_error']}")
    elif health in ("error", "blocked", "other") and status != "completed":
        issues.append(f"Last run {last_status}")
    streak = _count(j.get("failure_streak"))
    if streak:
        issues.append(f"Failed {streak} run{'' if streak == 1 else 's'} in a row")
    if health == "paused":
        since, reason = j.get("paused_at"), j.get("paused_reason")
        issues.append("Paused" + (f" since {since}" if since else "") + (f": {reason}" if reason else ""))
    if j.get("last_delivery_error"):
        issues.append(f"Delivery: {j['last_delivery_error']}")
    if _unattached(j, skills):
        issues.append("No skill or script attached")
    return issues


_CONFIG_KEYS = ("model", "provider", "enabled_toolsets", "profile", "workdir", "context_from", "repeat", "no_agent")
_CREDENTIAL_RES = (
    re.compile(r"\b(?:sk-|gh[opsu]_|xox[abprs]-|AKIA)[A-Za-z0-9_-]{4,}"),
    re.compile(r"://[^/\s:@]+:[^/\s@]+@"),
    re.compile(r"[?&][\w.-]*(?:key|token|secret|password)=", re.I),
)
_TOKEN_RUN_RE = re.compile(r"(?=[A-Za-z+/=]*[0-9])[A-Za-z0-9+/=]{32,}")


def _looks_like_credential(text: str) -> bool:
    if any(pattern.search(text) for pattern in _CREDENTIAL_RES):
        return True
    return not text.startswith(("/", "~", ".")) and bool(_TOKEN_RUN_RE.search(text))


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _job_config(j: dict) -> dict:
    """The settings an operator reads off a job. A value that looks like a credential is dropped
    whole, and base_url is not on the list at all."""
    return {
        key: j[key] for key in _CONFIG_KEYS
        if j.get(key) and not any(_looks_like_credential(text) for text in _strings(j[key]))
    }


@router.get("/crons")
def get_crons(profile: Optional[str] = Query(None)):
    roots = _profile_roots(profile)
    if not roots.cron_jobs.exists():
        return {"jobs": [], "total": 0, "categories": {}, "updated_at": ""}
    live = _read_json(roots.cron_jobs, "jobs.json")
    index = _skill_index(roots)

    jobs = []
    categories: dict = {}
    for j in _jobs_of(live):
        prompt = j.get("prompt") or ""
        skills = _job_skills(j)
        name = j.get("name") or "Unnamed"
        schedule_raw = j.get("schedule") or {}
        if isinstance(schedule_raw, dict):
            schedule = schedule_raw.get("display") or schedule_raw.get("expr") or "?"
        else:
            schedule = str(schedule_raw)

        category = ""
        for s in skills:
            ref = index.get(s.rsplit("/", 1)[-1])
            if ref is not None:
                category = _category_of(ref.md, ref.root)
                break

        status, health = _job_status(j)

        jobs.append({
            "id": j.get("id"),
            "name": name,
            "schedule": schedule,
            "schedule_human": _humanize_schedule(schedule),
            "skills": skills,
            "has_skill": len(skills) > 0,
            "script": j.get("script"),
            "no_agent": j.get("no_agent", False),
            "status": status,
            "health": health,
            "last_status": j.get("last_status"),
            "last_error": j.get("last_error"),
            "failure_streak": _count(j.get("failure_streak")),
            "paused_reason": j.get("paused_reason"),
            "paused_at": j.get("paused_at"),
            "last_delivery_error": j.get("last_delivery_error"),
            "next_run_at": j.get("next_run_at"),
            "unattached": _unattached(j, skills),
            "config": _job_config(j),
            "deliver": _deliver_text(j.get("deliver")),
            "workdir": j.get("workdir"),
            "prompt_preview": prompt[:100] + "..." if len(prompt) > 100 else prompt,
            "prompt_full": prompt,
            "issues": _job_issues(j, skills, status, health),
            "category": category,
            "sequence": _cron_sequence(j, skills),
            "last_run": j.get("last_run_at"),
        })
        categories[category] = categories.get(category, 0) + 1

    return {
        "updated_at": live.get("updated_at", "") if isinstance(live, dict) else "",
        "total": len(jobs),
        "jobs": jobs,
        "categories": categories,
    }


def _webhook_sequence(name: str, route: dict) -> list[dict]:
    steps = [{
        "type": "trigger", "kind": "trigger",
        "label": f"{name} \u00b7 POST", "detail": f"/webhooks/{name}",
        "kindLabel": "webhook",
    }]
    if route.get("script"):
        steps.append({
            "type": "script", "kind": "script",
            "label": "Script", "detail": str(route["script"]),
            "kindLabel": "script", "script": str(route["script"]),
        })
    prompt = route.get("prompt") or ""
    steps.append({
        "type": "prompt", "kind": "prompt", "label": "Prompt",
        "detail": prompt[:200], "kindLabel": "prompt", "body": prompt,
    })
    for skill in _string_list(route.get("skills")):
        steps.append({
            "type": "skill", "kind": "skill", "label": skill,
            "name": skill, "kindLabel": "skill",
        })
    deliver = route.get("deliver", "deliver")
    extra = route.get("deliver_extra") or {}
    steps.append({
        "type": "deliver", "kind": "deliver",
        "label": deliver,
        "detail": str(extra)[:200],
        "kindLabel": deliver,
        "body": json.dumps(extra, indent=2, default=str) if extra else "",
    })
    return steps


@router.get("/webhooks")
def get_webhooks(profile: Optional[str] = Query(None)):
    roots = _profile_roots(profile)
    if not roots.webhooks.exists():
        return {"routes": []}
    raw = _read_json(roots.webhooks, "webhook_subscriptions.json")
    if not isinstance(raw, dict):
        return {"routes": []}

    routes = []
    for name, route in raw.items():
        if not isinstance(route, dict):
            continue
        routes.append({
            "name": name,
            "description": route.get("description", ""),
            "path": f"/webhooks/{name}",
            "events": route.get("events") or [],
            "deliver": route.get("deliver", ""),
            "deliver_extra": route.get("deliver_extra", {}),
            "skills": _string_list(route.get("skills")),
            "prompt_preview": (route.get("prompt") or "")[:200],
            "prompt_full": route.get("prompt") or "",
            "created_at": route.get("created_at"),
            "sequence": _webhook_sequence(name, route),
        })
    routes.sort(key=lambda r: r["name"])
    return {"routes": routes}


def _related_skills(fm: dict, body: str) -> list[str]:
    meta = fm.get("metadata")
    hermes_meta = meta.get("hermes") if isinstance(meta, dict) else None
    sources = [fm] + ([hermes_meta] if isinstance(hermes_meta, dict) else [])
    rel: list[str] = []
    for src in sources:
        for key in ("depends_on", "related_skills"):
            rel.extend(_string_list(src.get(key)))
    rel.extend(m.group(1).rsplit("/", 1)[-1] for m in _SKILL_VIEW_RE.finditer(body))
    seen: set = set()
    out = []
    for x in rel:
        if _SKILL_REF_RE.match(x) and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _uses_skill(skills: list[str], name: str) -> bool:
    return any(s.rsplit("/", 1)[-1] == name for s in skills)


def _used_by(name: str, roots: Roots) -> list[dict]:
    """The crons and webhook routes that attach a skill, so an edit shows what it reaches."""
    users = []
    for j in _jobs_of(_json_or_none(roots.cron_jobs)):
        if _uses_skill(_job_skills(j), name):
            status, health = _job_status(j)
            users.append({
                "kind": "cron", "id": j.get("id"), "name": j.get("name") or "Unnamed",
                "deliver": _deliver_text(j.get("deliver")), "status": status, "health": health,
            })
    routes = _json_or_none(roots.webhooks)
    for route_name, route in routes.items() if isinstance(routes, dict) else ():
        if isinstance(route, dict) and _uses_skill(_string_list(route.get("skills")), name):
            users.append({"kind": "webhook", "name": route_name, "deliver": route.get("deliver") or ""})
    return sorted(users, key=lambda u: (u["kind"], u["name"]))


class _Summary(NamedTuple):
    description: str
    has_credentials: bool
    related: tuple
    steps: int
    fallback: bool


@lru_cache(maxsize=4096)
def _skill_summary(path: str, mtime_ns: int, size: int) -> _Summary:
    """Parsed once per file version: the Skills tab polls this listing."""
    fm, body = _split_frontmatter(Path(path).read_text(encoding="utf-8", errors="replace"))
    flow = parse_skill_flow(fm, body)
    return _Summary(
        description=str(fm.get("description") or ""),
        has_credentials=bool(_credential_names(fm)),
        related=tuple(_related_skills(fm, body)),
        steps=len(flow["steps"]),
        fallback=flow["fallback"],
    )


@router.get("/skills")
def list_skills(profile: Optional[str] = Query(None)):
    roots = _profile_roots(profile)
    skills = []
    for name, ref in _skill_index(roots).items():
        try:
            st = ref.md.stat()
            summary = _skill_summary(str(ref.md), st.st_mtime_ns, st.st_size)
            skills.append({
                "name": name,
                **_skill_location(ref, roots),
                "description": summary.description,
                "has_credentials": summary.has_credentials,
                "related": list(summary.related),
                "steps": summary.steps,
                "fallback": summary.fallback,
            })
        except Exception as exc:  # one broken skill must not hide the others
            _log.warning("skill scan failed for %s: %s", ref.md, exc)
    return {"skills": skills, "count": len(skills)}


@router.get("/skills/{name}")
def get_skill(name: str, profile: Optional[str] = Query(None)):
    roots = _profile_roots(profile)
    ref = _resolve_skill(name, roots)
    try:
        text = ref.md.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"unreadable: {exc}")
    fm, body = _split_frontmatter(text)
    return {
        "name": name,
        **_skill_location(ref, roots),
        "frontmatter": _json_safe(fm),
        "description": str(fm.get("description") or ""),
        "credentials": _credential_names(fm),
        "body": body,
        "raw": text,
        "related_skills": _related_skills(fm, body),
        "used_by": _used_by(name, roots),
        "flow": parse_skill_flow(fm, body),
    }


class SkillUpdate(BaseModel):
    content: str
    # The SKILL.md the edit started from. Older clients omit it and overwrite unconditionally.
    expected: Optional[str] = None


def _validate_skill_content(content: str) -> Optional[str]:
    """Same checks as Hermes' skill_manage edit path. Returns an error or None."""
    if not content.strip():
        return "Content cannot be empty."
    content = content.lstrip("\ufeff")
    if not content.startswith("---"):
        return "SKILL.md must start with YAML frontmatter (---)."
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return "SKILL.md frontmatter is not closed (missing closing '---' line)."
    yaml_content = content[3 : end_match.start() + 3]
    if _yaml_load is not None:
        try:
            parsed = _yaml_load(yaml_content)
        except Exception as exc:
            return f"YAML frontmatter parse error: {exc}"
        if not isinstance(parsed, dict):
            return "Frontmatter must be a YAML mapping (key: value pairs)."
        if "name" not in parsed:
            return "Frontmatter must include a 'name' field."
        if "description" not in parsed:
            return "Frontmatter must include a 'description' field."
        if len(str(parsed["description"])) > MAX_DESCRIPTION_LENGTH:
            return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."
    body = content[end_match.end() + 3 :].strip()
    if not body:
        return "SKILL.md must have content after the frontmatter."
    return None


def _atomic_write(target: Path, content: str) -> None:
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.parent.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, str(target))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"write failed: {exc}")


def _history_folder(roots: Roots, kind: str, ref: str) -> Path:
    key = hashlib.sha256(ref.encode("utf-8", "replace")).hexdigest()[:24]
    return roots.home / "plugin-data" / PLUGIN_NAME / "history" / kind / key


def _record_history(roots: Roots, kind: str, ref: str, old: str, new: str) -> None:
    """Keep the text an edit is about to replace, so the edit can be undone. A save whose history
    cannot be written fails before it touches the target."""
    if old == new:
        return
    folder = _history_folder(roots, kind, ref)
    tmp = None
    try:
        at = roots.home
        for part in folder.relative_to(roots.home).parts:
            at = at / part
            at.mkdir(mode=0o700, exist_ok=True)
        stamp = time.time_ns()
        while (folder / f"{stamp:020d}.json").exists():
            stamp += 1
        entry = {
            "kind": kind, "ref": ref, "id": f"{stamp:020d}",
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "content": old,
        }
        fd, tmp = tempfile.mkstemp(dir=str(folder), prefix=".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entry, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, folder / f"{entry['id']}.json")
    except Exception as exc:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail=f"could not save the earlier version, nothing was changed: {exc}")
    for stale in sorted(folder.glob("*.json"), reverse=True)[HISTORY_KEEP:]:
        try:
            stale.unlink()
        except OSError:
            _log.warning("history entry not pruned: %s", stale)


def _announce_skill_change(name: str) -> None:
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event

        broadcast_plugin_event(PLUGIN_NAME, "skills.changed", {"name": name})
    except Exception:  # push is an accelerator; the desktop also polls
        _log.debug("skills.changed broadcast skipped", exc_info=True)


def _editable_roots(profile: Optional[str], what: str) -> Roots:
    roots = _profile_roots(profile)
    if not roots.editable:
        raise HTTPException(status_code=403, detail=f"{what} of other profiles are read-only here")
    return roots


def _writable_skill(name: str, roots: Roots) -> SkillRef:
    ref = _resolve_skill(name, roots)
    if ref.root != roots.skills:
        raise HTTPException(status_code=403, detail="skills from skills.external_dirs are read-only here")
    if not _within(ref.md, roots.skills):
        raise HTTPException(status_code=403, detail="this skill lives outside the skills directory and is read-only")
    return ref


def _save_skill(name: str, ref: SkillRef, content: str, roots: Roots) -> dict:
    err = _validate_skill_content(content)
    if err:
        raise HTTPException(status_code=422, detail=err)

    real = Path(os.path.realpath(ref.md))
    old = _text_or_none(real)
    if old is not None:
        _record_history(roots, "skill", name, old, content)
    _atomic_write(real, content)
    _announce_skill_change(name)

    fm, _ = _split_frontmatter(content)
    return {
        "ok": True,
        "name": name,
        "description": str(fm.get("description") or ""),
        "path": str(ref.md),
    }


@router.put("/skills/{name}")
def put_skill(name: str, update: SkillUpdate, profile: Optional[str] = Query(None)):
    roots = _editable_roots(profile, "skills")
    ref = _writable_skill(name, roots)
    if update.expected is not None and ref.md.read_bytes().decode("utf-8", errors="replace") != update.expected:
        raise HTTPException(status_code=409, detail=f"SKILL.md {_CONFLICT}")
    return _save_skill(name, ref, update.content, roots)


class SpanUpdate(BaseModel):
    start: int
    end: int
    expected: str
    content: str


_CONFLICT = "it changed on disk since you opened it; reload and try again"


def _body_bounds(text: str) -> tuple[int, int]:
    """Offsets of the body _split_frontmatter returns, so a step span maps back onto the file."""
    content_start = len(text) - len(text.lstrip("\ufeff"))
    content = text[content_start:]
    after = 0
    if content.startswith("---"):
        end = re.search(r"\n---\s*\n", content[3:])
        if end:
            after = end.end() + 3
    rest = content[after:]
    start = content_start + after + len(rest) - len(rest.lstrip())
    return start, content_start + after + len(rest.rstrip())


@router.put("/skills/{name}/span")
def put_skill_span(name: str, update: SpanUpdate, profile: Optional[str] = Query(None)):
    """Replace the body lines [start, end) of one flow step, if they still read as `expected`."""
    roots = _editable_roots(profile, "skills")
    ref = _writable_skill(name, roots)
    text = ref.md.read_bytes().decode("utf-8", errors="replace")
    a, b = _body_bounds(text)
    # Odd entries are the line breaks, so the file keeps its own line endings.
    parts = re.split(r"(\r\n|\r|\n)", text[a:b])
    lines = parts[0::2]
    if not 0 <= update.start < update.end <= len(lines):
        raise HTTPException(status_code=409, detail=f"This step's lines no longer exist: {_CONFLICT}")
    old = "".join(parts[2 * update.start : 2 * update.end - 1])
    if old.expandtabs(4).strip() != update.expected.strip():
        raise HTTPException(status_code=409, detail=f"This step's text {_CONFLICT}")
    newline = parts[1] if len(parts) > 1 else "\n"
    lead = old[: len(old) - len(old.lstrip())]
    trail = old[len(old.rstrip()) :]
    new = re.sub(r"\r\n|\r|\n", newline, update.content.strip())
    spliced = "".join(parts[: 2 * update.start]) + lead + new + trail + "".join(parts[2 * update.end - 1 :])
    return _save_skill(name, ref, text[:a] + spliced + text[b:], roots)


def _script_ref(target: Path, roots: Roots) -> str:
    """The history ref of a script: its path under <home>/scripts, however the job spelled it."""
    return target.relative_to(roots.scripts.resolve()).as_posix()


def _script_path(value: str, scripts: Path) -> Path:
    """Resolve a job or route script the way Hermes does: relative to ``<home>/scripts``,
    ``~/.hermes/...`` mapped to the profile home, and never outside ``<home>/scripts``."""
    text = os.path.expandvars(value.strip())
    if "\0" in text or not text:
        raise HTTPException(status_code=400, detail="invalid script path")
    if text == "~/.hermes" or text.startswith("~/.hermes/"):
        raw = scripts.parent / text[len("~/.hermes/"):]
    else:
        raw = Path(os.path.expanduser(text))
    root = scripts.resolve()
    path = (raw if raw.is_absolute() else root / raw).resolve()
    if not _within(path, root):
        raise HTTPException(status_code=403, detail=f"script resolves outside {root}")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"script not found: {path}")
    return path


@router.get("/scripts")
def get_script(path: str = Query(...), profile: Optional[str] = Query(None)):
    roots = _profile_roots(profile)
    target = _script_path(path, roots.scripts)
    try:
        with target.open("rb") as fh:
            data = fh.read(MAX_SCRIPT_BYTES + 1)
        size = target.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"unreadable: {exc}")
    return {
        "path": str(target),
        "name": target.name,
        "ref": _script_ref(target, roots),
        "size": size,
        "truncated": len(data) > MAX_SCRIPT_BYTES,
        "content": data[:MAX_SCRIPT_BYTES].decode("utf-8", errors="replace"),
    }


class TextUpdate(BaseModel):
    expected: str
    content: str


class ScriptUpdate(TextUpdate):
    path: str


@router.put("/scripts")
def put_script(update: ScriptUpdate, profile: Optional[str] = Query(None)):
    roots = _editable_roots(profile, "scripts")
    target = _script_path(update.path, roots.scripts)
    try:
        if target.stat().st_size > MAX_SCRIPT_BYTES:
            raise HTTPException(status_code=413, detail="this script is too large to edit here")
        current = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="this script is not UTF-8 text")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"unreadable: {exc}")
    if current != update.expected:
        raise HTTPException(status_code=409, detail=f"The script {_CONFLICT}")
    _record_history(roots, "script", _script_ref(target, roots), current, update.content)
    _atomic_write(target, update.content)
    return {"ok": True, "path": str(target)}


@contextmanager
def _profile_secrets(profile: Optional[str]):
    """Hermes' per-profile config and secret scope, so a home channel is read from the profile's own
    .env. Yields False when another profile cannot be scoped; reading it unscoped would show the
    default profile's channels."""
    if profile in (None, "", "default"):
        yield True
        return
    try:
        from hermes_cli.web_server_profiles import _config_profile_scope
    except ImportError:
        yield False
        return
    with _config_profile_scope(profile):
        yield True


def _channel_name(platform: str, chat_id: str, directory: dict) -> str:
    for ch in directory.get(platform) or directory.get(platform.lower()) or []:
        if isinstance(ch, dict) and str(ch.get("id")) == chat_id:
            return str(ch.get("name") or "")
    return ""


def _unresolved(sd, token: str) -> str:
    if token.lower() == "origin":
        return "the job has no origin chat and no platform has a home channel"
    if token.lower() == "all":
        return "no platform has a home channel"
    if ":" in token:
        return "Hermes could not resolve this chat"
    if not sd._is_known_delivery_platform(token):
        return "not a delivery platform Hermes knows (is its plugin enabled?)"
    env = sd._resolve_home_env_var(token)
    return "no home channel is set" + (f" ({env})" if env else "") + "; run /sethome there"


def _resolve_deliver(job: dict, deliver: str) -> list[dict]:
    """Where each comma-separated token of ``deliver`` goes, resolved by the scheduler's own code."""
    tokens = [t.strip() for t in deliver.split(",") if t.strip() and t.strip() != "local"]
    try:
        from cron import scheduler_delivery as sd
    except ImportError:
        return [{"token": t, "targets": [], "error": "resolving targets needs a newer Hermes"} for t in tokens]
    try:
        from gateway.channel_directory import load_directory

        directory = load_directory().get("platforms") or {}
    except Exception:
        directory = {}
    origin = job.get("origin") if isinstance(job.get("origin"), dict) else {}
    out = []
    for token in tokens:
        entry: dict = {"token": token, "targets": []}
        try:
            broadcast = token.lower() in sd._ROUTING_TOKENS
            for part in sd._expand_routing_tokens(token):
                t = sd._resolve_single_delivery_target(job, part, from_broadcast=broadcast)
                if not t:
                    continue
                platform, chat_id = str(t.get("platform") or ""), str(t.get("chat_id") or "")
                via = t.get("_resolved_from") or ""
                name = _channel_name(platform, chat_id, directory)
                if not name and via.startswith("origin") and str(origin.get("chat_id")) == chat_id:
                    name = str(origin.get("chat_name") or "")
                entry["targets"].append({
                    "platform": platform, "chat_id": chat_id, "name": name, "via": via,
                    "thread_id": None if t.get("thread_id") is None else str(t["thread_id"]),
                })
            if not entry["targets"]:
                entry["error"] = _unresolved(sd, token)
        except Exception as exc:
            entry["error"] = f"could not resolve: {exc}"
        out.append(entry)
    return out


def _delivery_options() -> list[dict]:
    options = [{"id": "local", "name": "Local (save only)", "ready": True},
               {"id": "origin", "name": "Origin chat", "ready": True}]
    try:
        from cron.scheduler_delivery import cron_delivery_targets

        options += [{"id": t["id"], "name": t.get("name") or t["id"], "ready": bool(t.get("home_target_set"))}
                    for t in cron_delivery_targets() if t.get("id")]
    except Exception:
        _log.debug("cron_delivery_targets unavailable", exc_info=True)
    return options


def _job_or_none(roots: Roots, job_id: str) -> Optional[dict]:
    if roots.cron_jobs.exists():
        for j in _jobs_of(_read_json(roots.cron_jobs, "jobs.json")):
            if str(j.get("id")) == job_id:
                return j
    return None


def _find_job(roots: Roots, job_id: str) -> dict:
    job = _job_or_none(roots, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"cron job not found: {job_id}")
    return job


@router.get("/crons/{job_id}/delivery")
def get_cron_delivery(job_id: str, profile: Optional[str] = Query(None)):
    job = _find_job(_profile_roots(profile), job_id)
    deliver = _deliver_text(job.get("deliver"))
    failure = _deliver_text(job.get("failure_deliver"), "")
    origin = job.get("origin") if isinstance(job.get("origin"), dict) else None
    result = {"deliver": deliver, "failure_deliver": failure or None, "origin": origin, "options": [],
              "targets": [], "failure_targets": []}
    with _profile_secrets(profile) as scoped:
        if not scoped:
            note = "resolving another profile's targets needs a newer Hermes"
            result["targets"] = [{"token": t, "targets": [], "error": note} for t in deliver.split(",") if t.strip() not in ("", "local")]
            return result
        result["targets"] = _resolve_deliver(job, deliver)
        result["failure_targets"] = _resolve_deliver(job, failure) if failure else []
        result["options"] = _delivery_options()
    return result


def _run_dir(roots: Roots, job_id: str) -> Path:
    return roots.home / "cron" / "output" / job_id


def _run_at(run_id: str) -> str:
    return f"{run_id[:10]}T{run_id[11:].replace('-', ':')}"


@router.get("/crons/{job_id}/runs")
def list_cron_runs(job_id: str, limit: int = Query(20, ge=1, le=200), profile: Optional[str] = Query(None)):
    """The output files Hermes saved for a job, newest first."""
    _checked(job_id, _JOB_ID_RE, "job id")
    folder = _run_dir(_profile_roots(profile), job_id)
    found: list[tuple[str, int]] = []
    for entry in folder.iterdir() if folder.is_dir() else ():
        match = _RUN_FILE_RE.fullmatch(entry.name)
        try:
            st = entry.lstat()
        except OSError:
            continue
        if match and stat.S_ISREG(st.st_mode):
            found.append((match.group(1), st.st_size))
    found.sort(reverse=True)
    return {
        "job_id": job_id,
        "runs": [{"id": run_id, "at": _run_at(run_id), "size": size} for run_id, size in found[:limit]],
        "total": len(found),
    }


@router.get("/crons/{job_id}/runs/{run_id}")
def get_cron_run(job_id: str, run_id: str, profile: Optional[str] = Query(None)):
    _checked(job_id, _JOB_ID_RE, "job id")
    _checked(run_id, _RUN_ID_RE, "run id")
    folder = _run_dir(_profile_roots(profile), job_id)
    path = folder / f"{run_id}.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    if not _within(path, folder):
        raise HTTPException(status_code=403, detail="this run file resolves outside the job's output folder")
    try:
        with path.open("rb") as fh:
            data = fh.read(MAX_SCRIPT_BYTES + 1)
        size = path.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"unreadable: {exc}")
    return {
        "job_id": job_id,
        "id": run_id,
        "at": _run_at(run_id),
        "size": size,
        "truncated": len(data) > MAX_SCRIPT_BYTES,
        "content": data[:MAX_SCRIPT_BYTES].decode("utf-8", errors="replace"),
    }


# Cron jobs and webhook routes are written through Hermes' own store functions, which hold
# the same locks as the scheduler and the gateway, so an edit here cannot race a run.
@router.put("/crons/{job_id}/prompt")
def put_cron_prompt(job_id: str, update: TextUpdate, profile: Optional[str] = Query(None)):
    roots = _editable_roots(profile, "cron jobs")
    from cron import jobs as cron_jobs

    job = cron_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"cron job not found: {job_id}")
    if (job.get("prompt") or "") != update.expected:
        raise HTTPException(status_code=409, detail=f"The prompt {_CONFLICT}")
    _record_history(roots, "cron-prompt", job_id, job.get("prompt") or "", update.content)
    try:
        cron_jobs.update_job(job_id, {"prompt": update.content})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "id": job_id}


@router.put("/crons/{job_id}/deliver")
def put_cron_deliver(job_id: str, update: TextUpdate, profile: Optional[str] = Query(None)):
    roots = _editable_roots(profile, "cron jobs")
    from cron import jobs as cron_jobs

    job = cron_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"cron job not found: {job_id}")
    if _deliver_text(job.get("deliver")) != _deliver_text(update.expected):
        raise HTTPException(status_code=409, detail=f"The delivery target {_CONFLICT}")
    # "local" only means "send nowhere", so next to a real target it is dropped.
    tokens = dict.fromkeys(t.strip() for t in update.content.split(",") if t.strip() and t.strip() != "local")
    deliver = ",".join(tokens) or "local"
    _record_history(roots, "cron-deliver", job_id, _deliver_text(job.get("deliver")), deliver)
    try:
        cron_jobs.update_job(job_id, {"deliver": deliver})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "id": job_id, "deliver": deliver}


@router.put("/webhooks/{name}/prompt")
def put_webhook_prompt(name: str, update: TextUpdate, profile: Optional[str] = Query(None)):
    roots = _editable_roots(profile, "webhooks")
    try:
        from hermes_cli.webhook import SubscriptionMutationConflict, _load_subscriptions, _replace_subscription
    except ImportError:
        raise HTTPException(status_code=501, detail="editing webhook routes needs a newer Hermes")
    route = _load_subscriptions().get(name)
    if not isinstance(route, dict):
        raise HTTPException(status_code=404, detail=f"webhook route not found: {name}")
    if (route.get("prompt") or "") != update.expected:
        raise HTTPException(status_code=409, detail=f"The prompt {_CONFLICT}")
    _record_history(roots, "webhook-prompt", name, route.get("prompt") or "", update.content)
    try:
        _replace_subscription(name, {**route, "prompt": update.content}, route)
    except SubscriptionMutationConflict:
        raise HTTPException(status_code=409, detail=f"The route {_CONFLICT}")
    return {"ok": True, "name": name}


class _HistoryKind(NamedTuple):
    current: Callable[[str, Roots], Optional[str]]
    write: Callable[[str, str, str], dict]


def _skill_current(ref: str, roots: Roots) -> Optional[str]:
    found = _skill_index(roots).get(ref)
    return None if found is None else _text_or_none(found.md)


def _script_current(ref: str, roots: Roots) -> Optional[str]:
    try:
        return _text_or_none(_script_path(ref, roots.scripts))
    except HTTPException:
        return None


def _cron_prompt_current(ref: str, roots: Roots) -> Optional[str]:
    job = _job_or_none(roots, ref)
    return None if job is None else job.get("prompt") or ""


def _cron_deliver_current(ref: str, roots: Roots) -> Optional[str]:
    job = _job_or_none(roots, ref)
    return None if job is None else _deliver_text(job.get("deliver"))


def _webhook_prompt_current(ref: str, roots: Roots) -> Optional[str]:
    routes = _json_or_none(roots.webhooks)
    route = routes.get(ref) if isinstance(routes, dict) else None
    return route.get("prompt") or "" if isinstance(route, dict) else None


# A revert is a save through the same PUT handler, so it is conflict-checked and recorded like one.
_HISTORY_KINDS: dict[str, _HistoryKind] = {
    "skill": _HistoryKind(
        _skill_current,
        lambda ref, expected, content: put_skill(ref, SkillUpdate(content=content, expected=expected), None),
    ),
    "script": _HistoryKind(
        _script_current,
        lambda ref, expected, content: put_script(ScriptUpdate(path=ref, expected=expected, content=content), None),
    ),
    "cron-prompt": _HistoryKind(
        _cron_prompt_current,
        lambda ref, expected, content: put_cron_prompt(ref, TextUpdate(expected=expected, content=content), None),
    ),
    "cron-deliver": _HistoryKind(
        _cron_deliver_current,
        lambda ref, expected, content: put_cron_deliver(ref, TextUpdate(expected=expected, content=content), None),
    ),
    "webhook-prompt": _HistoryKind(
        _webhook_prompt_current,
        lambda ref, expected, content: put_webhook_prompt(ref, TextUpdate(expected=expected, content=content), None),
    ),
}


def _history_kind(kind: str) -> _HistoryKind:
    found = _HISTORY_KINDS.get(kind)
    if found is None:
        raise HTTPException(status_code=400, detail=f"unknown history kind: {kind!r}")
    return found


def _history_entry(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"history entry unreadable: {exc}")


@router.get("/history")
def list_history(kind: str = Query(...), ref: str = Query(...), profile: Optional[str] = Query(None)):
    _history_kind(kind)
    folder = _history_folder(_profile_roots(profile), kind, ref)
    versions = []
    for path in sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else ():
        entry = _history_entry(path)
        if entry is not None:
            versions.append({
                "id": entry["id"], "saved_at": entry["saved_at"], "size": len(entry["content"].encode("utf-8")),
            })
    return {"kind": kind, "ref": ref, "versions": versions}


@router.get("/history/version")
def get_history_version(
    kind: str = Query(...), ref: str = Query(...), id: str = Query(...), profile: Optional[str] = Query(None),
):
    handler = _history_kind(kind)
    _checked(id, _HISTORY_ID_RE, "history id")
    roots = _profile_roots(profile)
    entry = _history_entry(_history_folder(roots, kind, ref) / f"{id}.json")
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no saved version {id}")
    current = handler.current(ref, roots)
    diff = difflib.unified_diff(
        (current or "").splitlines(), entry["content"].splitlines(), "current", f"version {entry['saved_at']}",
        lineterm="",
    )
    return {
        "kind": kind, "ref": ref, "id": id, "saved_at": entry["saved_at"],
        "content": entry["content"], "current": current, "diff": "\n".join(diff),
    }


class RevertRequest(BaseModel):
    kind: str
    ref: str
    id: str
    expected: str


@router.post("/history/revert")
def revert_history(request: RevertRequest, profile: Optional[str] = Query(None)):
    handler = _history_kind(request.kind)
    _checked(request.id, _HISTORY_ID_RE, "history id")
    roots = _editable_roots(profile, "history")
    entry = _history_entry(_history_folder(roots, request.kind, request.ref) / f"{request.id}.json")
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no saved version {request.id}")
    result = handler.write(request.ref, request.expected, entry["content"])
    return {**result, "reverted": request.id}


@router.get("/profiles")
def list_profiles():
    names = ["default"]
    profiles_dir = get_default_hermes_root() / "profiles"
    if profiles_dir.is_dir():
        names += sorted(p.name for p in profiles_dir.iterdir() if p.is_dir() and _PROFILE_RE.match(p.name))
    return {"profiles": names}


@router.get("/health")
def health():
    roots = _profile_roots(None)
    return {
        "ok": True,
        "name": PLUGIN_NAME,
        "version": PLUGIN_VERSION,
        "sources": {
            "crons": roots.cron_jobs.exists(),
            "webhooks": roots.webhooks.exists(),
            "skills_root": roots.skills.exists(),
        },
    }
