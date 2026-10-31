"""Check this registry against the AIAgentLayer app itself, read-only.

Not a unittest module (its name keeps it out of ``unittest discover``): it
imports the app's own code, so it runs with the app's Python environment:

    ../AIAgentLayer/.venv/bin/python -I -B tests/app_contract_check.py [--app ../AIAgentLayer]

What it checks:

1. The real feed (docs/index.json) and a sample feed (the test submissions
   expanded into a temporary copy of this repository with scripts/expand.py,
   then compiled) both parse with the app's ``CommunityIndex``, with the same
   number of plugins, skills and agents before and after (nothing dropped).
2. Every skill entry keeps its raw_url and flavor, its SKILL.md matches
   raw_sha256, the app's install-time skill rules accept it, and the app's
   skill loader reads its frontmatter.
3. Every plugin entry converts with the installer's ``convert_manifest``
   (MCP configuration and bundled skills included).
4. Every agent entry passes the app's ``validate_template``.
5. The sample feed carries the version each submission asked for.
6. Submissions get the same verdict here (scripts/expand.py's pipeline) as
   from the app's Publish checks plus its installer, except the listed
   differences: KNOWN_LOOSER (app-side findings, where the app's installer
   agrees with this registry) and KNOWN_STRICTER (refused here on purpose).
7. Every file covered by schemas/ validates against its JSON Schema, and a
   few broken documents do not.

The app checkout must be unchanged afterwards (git status is compared), and
nothing is fetched from the network. Exit status 1 when any check fails.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURES = HERE / "fixtures" / "submissions"
FIXTURE_FILES = ("skill-portable.json", "plugin-http.json", "plugin-stdio.json", "agent-template.json")
SECTIONS = ("plugins", "skills", "agents")

#: The registry accepts, the app's Publish checks refuse: allowed only where
#: the app's own installer accepts it (an app-side finding, AUDIT-2026-10-31.md).
KNOWN_LOOSER = {
    "stdio uvx pinned with ==": "app finding 1: publish.py looks for @x.y.z or :tag and refuses uvx pkg==x.y.z, "
    "which the installer accepts",
}
#: The registry refuses, the app's Publish checks accept. Stricter is allowed
#: (rules.py): each of these would be refused or misbehave later in the app.
KNOWN_STRICTER = {
    "skill frontmatter name differs": "the installed folder takes the frontmatter name, so the listing would "
    "install under another name",
    "reserved skill name": "it would shadow a skill the app ships (dodaios/skills/builtin)",
    "reserved plugin id": "community_install reserves the seed catalog ids at install time",
    "plugin version differs": "the feed version and the manifest version must be the same release",
}

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    results.append((bool(ok), label))
    return bool(ok)


def git_status(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------


def build_sample(tmp: Path, expand, compile_index) -> tuple[Path, dict[str, Any], dict[str, str]]:
    """A copy of this repository with the test submissions expanded into it."""
    repo = tmp / "sample"
    repo.mkdir()
    for rel in ("skills", "docs"):
        shutil.copytree(ROOT / rel, repo / rel)
    for rel in ("registry.json", "trusted-publishers.json"):
        shutil.copy(ROOT / rel, repo / rel)
    submissions = [load(name) for name in FIXTURE_FILES]
    # A skill as AIAgentLayer's Publish form sends it: listing fields separate,
    # template frontmatter still saying 1.0.0.
    submissions.append(
        {
            "kind": "skill",
            "name": "app-style-skill",
            "version": "1.2.0",
            "title": "App style: skill",
            "description": "A skill shaped like the app's Publish form output.",
            "categories": ["writing"],
            "skill_md": "---\nname: app-style-skill\ndescription: Use when the user asks for a demo of the "
            "app's publish flow.\nversion: 1.0.0\n---\n\n# App style skill\n\nReply with one short line.\n",
        }
    )
    wanted = {}
    for obj in submissions:
        result = expand.plan(repo, obj, publisher="sample-dev", publisher_id=1234567, claimed_at="2026-10-31T00:00:00Z")
        expand.write(repo, result)
        wanted[obj["name"]] = obj["version"]
    feed = compile_index.build_index(repo, None, now="2026-10-31T00:00:00Z")
    return repo, feed, wanted


def check_feed(label: str, repo: Path, feed: dict[str, Any], app: dict[str, Any]) -> None:
    index = app["CommunityIndex"].model_validate(copy.deepcopy(feed))
    for section in SECTIONS:
        before, after = len(feed.get(section) or []), len(getattr(index, section))
        check(before == after, f"{label}: {section} {before} in feed, {after} after CommunityIndex")
    check(isinstance(index.revision, int) and index.revision == feed["revision"], f"{label}: integer revision kept")

    for raw, parsed in zip(feed["skills"], index.skills):
        name = raw["name"]
        check(parsed.raw_url == raw["raw_url"], f"{label}: skill {name} raw_url kept")
        check(parsed.flavor == raw["flavor"], f"{label}: skill {name} flavor kept ({raw['flavor']})")
        skill_path = repo / "skills" / name / "SKILL.md"
        data = skill_path.read_bytes()
        check(hashlib.sha256(data).hexdigest() == raw["raw_sha256"], f"{label}: skill {name} raw_sha256 matches")
        text = data.decode("utf-8")
        try:
            app["validate_bundled_skills"]([{"name": name, "skill_md": text}], plugin_name=name)
            check(True, f"{label}: skill {name} passes the app's install-time skill rules")
        except app["AgentPluginError"] as exc:
            check(False, f"{label}: skill {name} refused by the app: {exc}")
        with tempfile.TemporaryDirectory() as tmp:
            copy_path = Path(tmp) / name / "SKILL.md"
            copy_path.parent.mkdir()
            copy_path.write_bytes(data)
            parsed_skill = app["parse_skill"](copy_path)
            fm = getattr(parsed_skill, "frontmatter", None)
            check(fm is not None, f"{label}: skill {name} frontmatter read by the app's skill loader")

    for raw in feed["plugins"]:
        name = raw["name"]
        try:
            spec = app["convert_manifest"](
                raw["plugin_json"],
                raw.get("mcp_json"),
                publisher=raw["publisher"],
                version=raw["version"],
                source_url=raw["source_url"],
            )
            check(True, f"{label}: plugin {name} converts with the installer ({type(spec).__name__})")
        except Exception as exc:  # noqa: BLE001 - any refusal is a failed check, reported with its message
            check(False, f"{label}: plugin {name} refused by the installer: {exc}")
        parsed = next(p for p in app["CommunityIndex"].model_validate(feed).plugins if p.name == name)
        check(parsed.mcp_json == raw.get("mcp_json"), f"{label}: plugin {name} mcp_json kept")
        check(parsed.usage_card == raw.get("usage_card"), f"{label}: plugin {name} usage_card kept")

    for raw, parsed in zip(feed["agents"], index.agents):
        name = raw["name"]
        listing = {"name": parsed.name, "version": parsed.version, "description": parsed.description}
        problems = app["validate_template"](parsed.agent, listing)
        check(problems == [], f"{label}: agent {name} passes validate_template {problems or ''}".rstrip())
        check(parsed.publisher_id == raw.get("publisher_id"), f"{label}: agent {name} publisher_id kept")


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------


def submission_cases() -> list[tuple[str, dict[str, Any]]]:
    skill, http, stdio, agent = (load(n) for n in FIXTURE_FILES)
    cases: list[tuple[str, dict[str, Any]]] = [("fixture skill", skill), ("fixture plugin http", http)]
    cases += [("fixture plugin stdio", stdio), ("fixture agent", agent)]

    def variant(label: str, base: dict[str, Any], change: Callable[[dict[str, Any]], None]) -> None:
        obj = copy.deepcopy(base)
        change(obj)
        cases.append((label, obj))

    server = "notes-index"
    variant("bad name", skill, lambda o: o.update(name="Bad_Name"))
    variant("path name", skill, lambda o: o.update(name="../x"))
    variant("double dash name", skill, lambda o: o.update(name="a--b"))
    variant("version not semver", skill, lambda o: o.update(version="1.0"))
    variant("skill without title", skill, lambda o: o.pop("title"))
    variant("skill without frontmatter", skill, lambda o: o.update(skill_md="# no frontmatter\n"))
    variant(
        "skill with risk_policy",
        skill,
        lambda o: o.update(skill_md=o["skill_md"].replace("---\n\n", "risk_policy: {}\n---\n\n", 1)),
    )
    variant(
        "skill frontmatter name differs",
        skill,
        lambda o: o.update(skill_md=o["skill_md"].replace("name: meeting-recap", "name: other")),
    )
    variant("skill description too long", skill, lambda o: o.update(description="x" * 501))
    variant("skill with a GitHub token", skill, lambda o: o.update(skill_md=o["skill_md"] + "\nghp_" + "a" * 36 + "\n"))
    variant(
        "reserved skill name",
        skill,
        lambda o: (
            o.update(name="skill-creator"),
            o.update(skill_md=o["skill_md"].replace("meeting-recap", "skill-creator")),
        ),
    )
    variant(
        "reserved plugin id",
        http,
        lambda o: (o.update(name="google-drive"), o["plugin_json"].update(name="google-drive")),
    )
    variant("sse transport", http, lambda o: o["mcp_json"]["mcpServers"]["error-tracker"].update(type="sse"))
    variant(
        "http url",
        http,
        lambda o: o["mcp_json"]["mcpServers"]["error-tracker"].update(url="http://mcp.example.com/mcp"),
    )
    variant(
        "headers on http server",
        http,
        lambda o: o["mcp_json"]["mcpServers"]["error-tracker"].update(headers={"X": "y"}),
    )
    variant(
        "two servers",
        http,
        lambda o: o["mcp_json"]["mcpServers"].update(other={"type": "streamable-http", "url": "https://a.example.com"}),
    )
    variant("plugin version differs", http, lambda o: o["plugin_json"].update(version="9.9.9"))
    variant(
        "bundled skill risk_policy",
        http,
        lambda o: o["skills"][0].update(
            skill_md=o["skills"][0]["skill_md"].replace("---\n\n", "risk_policy: {}\n---\n\n", 1)
        ),
    )
    variant(
        "unpinned npx package",
        stdio,
        lambda o: o["mcp_json"]["mcpServers"][server].update(args=["-y", "@example/notes-index-mcp"]),
    )
    variant("unknown launcher", stdio, lambda o: o["mcp_json"]["mcpServers"][server].update(command="bash"))
    variant("literal env value", stdio, lambda o: o["mcp_json"]["mcpServers"][server].update(env={"API_KEY": "abc123"}))
    variant(
        "reserved env name",
        stdio,
        lambda o: o["mcp_json"]["mcpServers"][server].update(env={"PATH": "$plugin_notes-index_path"}),
    )
    variant(
        "stdio uvx pinned with ==",
        stdio,
        lambda o: o["mcp_json"]["mcpServers"][server].update(command="uvx", args=["notes-index-mcp==2.1.0"]),
    )
    variant("agent named Jarvis", agent, lambda o: o["agent"].update(name="Jarvis"))
    variant("agent with model field", agent, lambda o: o["agent"].update(model="any"))
    variant("agent schema 2", agent, lambda o: o["agent"].update(schema=2))
    variant("agent empty instructions", agent, lambda o: o["agent"].update(instructions=""))
    variant(
        "agent home folder", agent, lambda o: o["agent"].update(instructions="Read /Users/someone/notes.txt first.")
    )
    variant("agent bad tier", agent, lambda o: o["agent"].update(tier="boss"))
    variant("agent bad capability", agent, lambda o: o["agent"].update(grants=["not a capability"]))
    variant(
        "agent generic secret",
        agent,
        lambda o: o["agent"].update(instructions="Use password = hunter2hunter2 to log in."),
    )
    return cases  # fmt: skip


def app_verdict(obj: dict[str, Any], app: dict[str, Any]) -> tuple[bool, str]:
    """What the app decides: its Publish checks, then for plugins its installer."""
    _value, errors = app["validate_draft"](copy.deepcopy(obj))
    if errors:
        return False, "; ".join(getattr(e, "error", None) or getattr(e, "message", None) or str(e) for e in errors)
    if obj.get("kind") == "plugin":
        try:
            app["convert_manifest"](obj.get("plugin_json"), obj.get("mcp_json"))
        except Exception as exc:  # noqa: BLE001 - the installer's refusal, whatever its type
            return False, f"installer: {exc}"
    return True, ""


def our_verdict(obj: dict[str, Any], expand, submission) -> tuple[bool, str]:
    """What this registry decides on the publishing path (expand.py normalises first)."""
    try:
        _, _, problems = expand.check_submission(obj)
    except expand.ExpandError as exc:
        return False, "; ".join(str(e) for e in exc.errors)
    problems = [p for p in problems if p.is_error]
    return not problems, "; ".join(str(p) for p in problems)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


def check_schemas(sample_repo: Path, sample_feed: dict[str, Any], expand) -> None:
    import jsonschema
    import yaml
    from referencing import Registry, Resource

    schemas = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "schemas").glob("*.json"))}
    reg = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas.values())

    def validator(name: str):
        schema = schemas[name]
        jsonschema.Draft202012Validator.check_schema(schema)
        return jsonschema.Draft202012Validator(schema, registry=reg)

    def valid(name: str, doc: Any, label: str) -> None:
        errors = sorted(validator(name).iter_errors(doc), key=lambda e: list(e.path))
        detail = f": {errors[0].message[:160]}" if errors else ""
        check(not errors, f"schema {name}: {label} is valid{detail}")

    def invalid(name: str, doc: Any, label: str) -> None:
        check(not validator(name).is_valid(doc), f"schema {name}: {label} is refused")

    for fixture in FIXTURE_FILES:
        valid("submission.schema.json", load(fixture), fixture)
        obj = load(fixture)
        if obj["kind"] == "plugin":
            valid("plugin.schema.json", obj["plugin_json"], f"{fixture} plugin_json")
            valid("mcp.schema.json", obj["mcp_json"], f"{fixture} mcp_json")
        if obj["kind"] == "agent":
            valid("agent-template.schema.json", obj["agent"], f"{fixture} agent")
    valid("feed.schema.json", json.loads((ROOT / "docs" / "index.json").read_text(encoding="utf-8")), "docs/index.json")
    valid("feed.schema.json", json.loads(json.dumps(sample_feed)), "the sample feed")
    valid("registry.schema.json", json.loads((ROOT / "registry.json").read_text(encoding="utf-8")), "registry.json")
    valid(
        "registry.schema.json",
        json.loads((sample_repo / "registry.json").read_text(encoding="utf-8")),
        "sample registry",
    )
    trusted = json.loads((ROOT / "trusted-publishers.json").read_text(encoding="utf-8"))
    valid("trusted-publishers.schema.json", trusted, "trusted-publishers.json")
    for skill_md in sorted(sample_repo.glob("**/SKILL.md")):
        block = skill_md.read_text(encoding="utf-8").split("\n---", 1)[0].removeprefix("---")
        valid("skill-frontmatter.schema.json", yaml.safe_load(block), skill_md.relative_to(sample_repo).as_posix())

    bad = load("skill-portable.json")
    bad["name"] = "../x"
    invalid("submission.schema.json", bad, "a path-like name")
    bad = load("plugin-http.json")
    bad["mcp_json"]["mcpServers"]["error-tracker"]["type"] = "sse"
    invalid("submission.schema.json", bad, "an sse server")
    bad = load("agent-template.json")
    bad["agent"]["model"] = "x"
    invalid("submission.schema.json", bad, "an agent template with a model field")
    bad = load("plugin-http.json")["plugin_json"]
    bad["extensions"]["io.github.dodaios"]["auth"]["client_secret"] = "x"  # noqa: S105 - a fake value the schema must refuse
    invalid("plugin.schema.json", bad, "an auth block with client_secret")
    invalid("registry.schema.json", {"version": 2, "owners": {"x": {"kind": "skill", "publisher": "a", "owner_email": "e"}}}, "an unknown owner field")  # fmt: skip


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app", type=Path, default=ROOT.parent / "AIAgentLayer", help="AIAgentLayer checkout")
    args = parser.parse_args(argv)
    app_root = args.app.resolve()
    if not (app_root / "dodaios" / "marketplace" / "community_source.py").is_file():
        print(f"app_contract_check: {app_root} is not an AIAgentLayer checkout", file=sys.stderr)
        return 2
    status_before = git_status(app_root)
    head = subprocess.run(
        ["git", "-C", str(app_root), "rev-parse", "--short=10", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    home = tempfile.mkdtemp(prefix="contract-home-")
    os.environ["HOME"] = home  # keep any app import side effects away from the real profile
    sys.path.insert(0, str(app_root))
    sys.path.insert(0, str(ROOT / "scripts"))
    import compile_index
    import expand
    from ainexlayer_marketplace import rules, submission
    from dodaios.marketplace import agent_plugins_loader, publish
    from dodaios.marketplace.community_source import CommunityIndex
    from dodaios.skills.loader import parse_skill
    from dodaios.society.agent_template import validate_template

    app = {
        "CommunityIndex": CommunityIndex,
        "convert_manifest": agent_plugins_loader.convert_manifest,
        "validate_bundled_skills": agent_plugins_loader.validate_bundled_skills,
        "AgentPluginError": agent_plugins_loader.AgentPluginError,
        "validate_draft": publish.validate_draft,
        "validate_template": validate_template,
        "parse_skill": parse_skill,
    }
    print(f"AIAgentLayer {head} (rules mirror {rules.APP_SOURCE_COMMIT})")
    check(head.startswith(rules.APP_SOURCE_COMMIT), f"app checkout is the commit the rules mirror ({head})")

    real = json.loads((ROOT / "docs" / "index.json").read_text(encoding="utf-8"))
    check_feed("real feed", ROOT, real, app)

    with tempfile.TemporaryDirectory() as tmp:
        repo, feed, wanted = build_sample(Path(tmp), expand, compile_index)
        check([len(feed[s]) for s in SECTIONS] == [2, 3, 1], "sample feed: 2 plugins, 3 skills, 1 agent compiled")
        check_feed("sample feed", repo, feed, app)
        for section in SECTIONS:
            for entry in feed[section]:
                if entry["name"] in wanted:
                    check(
                        entry["version"] == wanted[entry["name"]],
                        f"sample feed: {entry['name']} is {entry['version']} as submitted",
                    )
        try:
            check_schemas(repo, feed, expand)
        except ImportError as exc:
            check(False, f"schemas not checked: {exc} (run with the app's environment)")

    agree = differ = 0
    for label, obj in submission_cases():
        ours, why_ours = our_verdict(obj, expand, submission)
        theirs, why_theirs = app_verdict(obj, app)
        if ours == theirs:
            agree += 1
            check(True, f"submission '{label}': both {'accept' if ours else 'refuse'}")
        elif ours and label in KNOWN_LOOSER:
            differ += 1
            check(True, f"submission '{label}': known difference, registry accepts ({KNOWN_LOOSER[label]})")
        elif not ours and label in KNOWN_STRICTER:
            differ += 1
            check(True, f"submission '{label}': registry stricter by design ({KNOWN_STRICTER[label]})")
        else:
            check(False, f"submission '{label}': registry {'accepts' if ours else 'refuses'} ({why_ours[:120]}), "
                         f"app {'accepts' if theirs else 'refuses'} ({why_theirs[:120]})")  # fmt: skip
    unused = {*KNOWN_LOOSER, *KNOWN_STRICTER} - {label for label, _ in submission_cases()}
    check(not unused, "every known difference is still exercised")

    shutil.rmtree(home, ignore_errors=True)
    check(git_status(app_root) == status_before, "AIAgentLayer checkout unchanged (git status before == after)")

    failed = [label for ok, label in results if not ok]
    for ok, label in results:
        print(f"{'PASS' if ok else 'FAIL'}  {label}")
    print(f"\napp_contract_check: {len(results) - len(failed)} passed, {len(failed)} failed; submissions: "
          f"{agree} agree, {differ} known difference(s)")  # fmt: skip
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
