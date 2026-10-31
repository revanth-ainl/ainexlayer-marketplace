"""Static security checks on .github/workflows.

Needs PyYAML (requirements-dev.txt). Skipped, loudly, when it is missing.
"""

from __future__ import annotations

import re
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs it
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
PINNED_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
PUBLISHERS = ("intake.yml", "automerge.yml", "publish.yml")


def load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # YAML 1.1 reads the key `on` as the boolean True.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


def all_workflows() -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        yield path.name, load(path.name)


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps") or [])


def is_privileged(job: dict[str, Any]) -> bool:
    perms = job.get("permissions") or {}
    return isinstance(perms, dict) and any(v == "write" for v in perms.values())


@unittest.skipIf(yaml is None, "PyYAML is not installed (pip install -r requirements-dev.txt)")
class WorkflowSecurityTests(unittest.TestCase):
    def test_expected_workflows_exist(self) -> None:
        names = {p.name for p in WORKFLOWS.glob("*.yml")}
        self.assertTrue({"ci.yml", "pages.yml", *PUBLISHERS} <= names, names)

    def test_names_are_ainexlayer(self) -> None:
        for name, wf in all_workflows():
            self.assertTrue(str(wf.get("name", "")).startswith("AINexLayer"), name)

    def test_permissions_default_to_none_and_every_job_declares_its_own(self) -> None:
        for name, wf in all_workflows():
            self.assertEqual(wf.get("permissions"), {}, f"{name}: top-level permissions must be {{}}")
            for job_id, job in wf["jobs"].items():
                self.assertIn("permissions", job, f"{name}:{job_id}")

    def test_actions_are_pinned_to_full_shas(self) -> None:
        for name, wf in all_workflows():
            for job_id, job in wf["jobs"].items():
                uses = job.get("uses")
                if uses:
                    self.assertTrue(uses.startswith("./.github/workflows/"), f"{name}:{job_id} {uses}")
                for step in steps(job):
                    if "uses" in step:
                        self.assertRegex(step["uses"], PINNED_RE, f"{name}:{job_id}")

    def test_checkouts_never_persist_credentials(self) -> None:
        for name, wf in all_workflows():
            for job_id, job in wf["jobs"].items():
                for step in steps(job):
                    if str(step.get("uses", "")).startswith("actions/checkout@"):
                        self.assertIs((step.get("with") or {}).get("persist-credentials"), False, f"{name}:{job_id}")

    def test_no_expressions_inside_shell_commands(self) -> None:
        for name, wf in all_workflows():
            for job_id, job in wf["jobs"].items():
                for step in steps(job):
                    if "run" in step:
                        self.assertNotIn("${{", step["run"], f"{name}:{job_id}: pass values through env instead")

    def test_privileged_jobs_check_out_main_only(self) -> None:
        for name, wf in all_workflows():
            for job_id, job in wf["jobs"].items():
                if not is_privileged(job):
                    continue
                for step in steps(job):
                    if str(step.get("uses", "")).startswith("actions/checkout@"):
                        self.assertEqual((step.get("with") or {}).get("ref"), "main", f"{name}:{job_id}")

    def test_pull_request_target_never_touches_the_head(self) -> None:
        for name, wf in all_workflows():
            if "pull_request_target" not in (wf["on"] or {}):
                continue
            text = (WORKFLOWS / name).read_text(encoding="utf-8")
            for forbidden in ("pull_request.head.sha", "pull_request.head.ref", "github.head_ref", "refs/pull/"):
                self.assertNotIn(forbidden, text, name)
            self.assertEqual(name, "automerge.yml")

    def test_privileged_installs_are_hash_pinned_and_scripts_isolated(self) -> None:
        for name, wf in all_workflows():
            for job_id, job in wf["jobs"].items():
                for step in steps(job):
                    run = step.get("run", "")
                    if "pip install" in run:
                        self.assertIn("--require-hashes", run, f"{name}:{job_id}")
                    if is_privileged(job) and re.search(r"\bpython\b.*\bscripts/", run):
                        for line in run.splitlines():
                            if re.search(r"\bpython\b.*\bscripts/", line):
                                self.assertIn("python -I ", line, f"{name}:{job_id}")

    def test_secrets_only_reach_the_publishing_step(self) -> None:
        for name, wf in all_workflows():
            for job_id, job in wf["jobs"].items():
                for step in steps(job):
                    env_text = str(step.get("env") or {})
                    if "secrets." in env_text:
                        self.assertIn(name, PUBLISHERS, f"{name}:{job_id}")
                        self.assertRegex(step.get("run", ""), r"^python -I scripts/[a-z_]+\.py$")

    def test_publishing_is_off_by_default(self) -> None:
        self.assertIn("vars.AINEXLAYER_INTAKE == 'true'", load("intake.yml")["jobs"]["intake"]["if"])
        self.assertIn("vars.AINEXLAYER_AUTOMERGE == 'true'", load("automerge.yml")["jobs"]["gate"]["if"])

    def test_writers_use_per_source_concurrency_groups(self) -> None:
        groups = {}
        for name in PUBLISHERS:
            for job in load(name)["jobs"].values():
                if is_privileged(job) and "steps" in job:
                    groups[name] = str((job.get("concurrency") or {}).get("group", ""))
        self.assertIn("github.event.issue.number", groups["intake.yml"])
        self.assertIn("github.event.pull_request.number", groups["automerge.yml"])
        self.assertIn("github.sha", groups["publish.yml"])
        self.assertEqual(len(set(groups.values())), 3, "no group may be shared across writers")
        for name in (*PUBLISHERS, "pages.yml"):
            self.assertNotIn("concurrency", load(name), f"{name}: workflow-level groups would be shared by every run")

    def test_pages_builds_the_newest_main_and_contains_the_published_commit(self) -> None:
        pages = load("pages.yml")
        call = pages["on"]["workflow_call"]["inputs"]["published_sha"]
        self.assertEqual(call["type"], "string")
        job = pages["jobs"]["deploy"]
        checkout = next(s for s in steps(job) if str(s.get("uses", "")).startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "main")
        self.assertEqual(checkout["with"]["fetch-depth"], 0)
        guard = next(s for s in steps(job) if "merge-base --is-ancestor" in s.get("run", ""))
        self.assertEqual(guard["env"]["PUBLISHED_SHA"], "${{ inputs.published_sha }}")
        self.assertIs(job["concurrency"]["cancel-in-progress"], False)

    def test_every_writer_calls_pages_with_the_pushed_commit(self) -> None:
        for name in PUBLISHERS:
            jobs = load(name)["jobs"]
            callers = [j for j in jobs.values() if j.get("uses") == "./.github/workflows/pages.yml"]
            self.assertEqual(len(callers), 1, name)
            caller = callers[0]
            need = caller["needs"]
            self.assertEqual(caller["with"]["published_sha"], f"${{{{ needs.{need}.outputs.published_sha }}}}", name)
            self.assertEqual(caller["permissions"], {"contents": "read", "pages": "write", "id-token": "write"}, name)
            self.assertIn("published_sha", jobs[need]["outputs"], name)


if __name__ == "__main__":
    unittest.main()
