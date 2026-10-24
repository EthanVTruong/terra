"""Behavioral checks using disposable Git repos, never the user's history."""
from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("terra", Path(__file__).resolve().parents[1] / "terra.py")
terra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(terra)


def git(root, *args, ok=True):
    proc = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false",
                           "-c", "core.hooksPath=nonexistent-test-hooks", "-C", str(root), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    if ok and proc.returncode:
        raise AssertionError(proc.stderr)
    return proc.stdout.strip() if ok else proc


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def save(root, data):
    write(root, terra.DOC, "# Test coordination\n\n" + "\n\n".join(
        "```json\n" + json.dumps(r, indent=2) + "\n```" for r in data) + "\n")


def fixture():
    return [
        {"kind": "policy", "version": 1, "owners": {"alice": "Windows", "bob": "macOS"}},
        {"kind": "domain", "id": "data", "owner": "alice", "purpose": "Data algorithms",
         "paths": ["algorithm.py", "schema.json", "data.txt"], "depends_on": [], "checks": ["fast"]},
        {"kind": "domain", "id": "app", "owner": "bob", "purpose": "Prediction application",
         "paths": ["app.py"], "depends_on": ["data"], "checks": ["fast"]},
        {"kind": "domain", "id": "coordination", "owner": "alice", "purpose": "Shared coordination",
         "paths": [terra.DOC, ".gitignore"], "depends_on": [], "checks": ["fast"]},
        {"kind": "check", "id": "fast", "argv": ["{python}", "-c", "assert 1 + 1 == 2"], "timeout": 10},
        {"kind": "task", "id": "a", "owner": "alice", "status": "active", "branch": "task/a",
         "intent": "Clamp the normalized algorithm result into the valid range", "base": None,
         "paths": ["algorithm.py", "schema.json", "data.txt"], "domains": ["data"],
         "depends_on": [], "contracts": [], "artifacts": [],
         "handoff": {"revision": None, "summary": "Inspect algorithm", "evidence": [], "next": "Implement clamping"}},
        {"kind": "task", "id": "b", "owner": "bob", "status": "approved", "branch": "task/b",
         "intent": "Add a prediction application entry point", "base": None,
         "paths": ["app.py"], "domains": ["app"], "depends_on": [], "contracts": [], "artifacts": [],
         "handoff": {"revision": None, "summary": "Not started", "next": "Add entry point"}},
    ]


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="terra-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo with spaces"
        self.root.mkdir()
        git(self.root, "init", "--template=", "-b", "main")
        self.configure(self.root)
        self.data = fixture()
        write(self.root, "algorithm.py", "def score(x):\n    return x\n")
        write(self.root, "app.py", "READY = False\n")
        write(self.root, "schema.json", '{"unit":"ratio"}\n')
        write(self.root, "data.txt", "fixture\n")
        write(self.root, ".gitignore", "__pycache__/\n.env\n*.pt\n")
        save(self.root, self.data)
        self.commit(self.root, "Initial fixture")
        self.base = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-b", "task/a")

    def configure(self, root):
        git(root, "config", "user.name", "Terra Fixture")
        git(root, "config", "user.email", "terra-fixture@example.invalid")

    def commit(self, root, message):
        git(root, "add", "--all")
        git(root, "commit", "-m", message)

    def validate(self, task="a", base=None):
        save(self.root, self.data)
        project = terra.Harness(self.root)
        project.validate(base or self.base, task)
        return project

    def test_valid_fixture_and_scoped_cli(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = terra.main(["--root", str(self.root), "check", "--base", self.base, "--task", "a"])
        self.assertEqual(result, 0, output.getvalue())
        self.assertIn("PASS fast", output.getvalue())
        self.assertIn("Scientific/model acceptance is separate", output.getvalue())

    def test_scope_rejects_other_domain(self):
        write(self.root, "app.py", "READY = True\n")
        self.assertTrue(any("outside task" in e for e in self.validate().errors))

    def test_new_unowned_file_is_actionable(self):
        write(self.root, "new_algorithm.py", "VALUE = 1\n")
        self.assertTrue(any("exactly one owned domain" in e for e in self.validate().errors))

    def test_unknown_owner_and_missing_check(self):
        self.data[1]["owner"] = "nobody"
        self.data[1]["checks"] = []
        errors = self.validate().errors
        self.assertTrue(any("unknown human owner" in e for e in errors))
        self.assertTrue(any("validation is missing" in e for e in errors))

    def test_dependency_cycle_and_unfinished_dependency(self):
        self.data[-2]["depends_on"] = ["b"]
        self.assertTrue(any("not done" in e for e in self.validate().errors))
        self.data[-1]["depends_on"] = ["a"]
        self.assertTrue(any("cycle" in e for e in self.validate().errors))

    def test_blocked_task_cannot_integrate(self):
        self.data[-2]["status"] = "blocked"
        self.assertTrue(any("blocked" in e for e in self.validate().errors))

    def test_invalid_base_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, "Unknown base"):
            self.validate(base="missing-commit")

    def test_explicit_base_requires_a_local_checkpoint(self):
        git(self.root, "checkout", "--orphan", "uncommitted-bootstrap")
        project = terra.Harness(self.root)
        with self.assertRaisesRegex(ValueError, "without a committed local checkpoint"):
            project.validate(self.base, "a")

    def test_upstream_must_be_reconciled_before_integration(self):
        git(self.root, "checkout", "main")
        write(self.root, "app.py", "READY = True\n")
        self.commit(self.root, "Upstream changed")
        upstream = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "task/a")
        self.assertTrue(any("not an ancestor" in e for e in self.validate(base=upstream).errors))

    def test_segments_and_cross_platform_paths(self):
        self.assertTrue(terra.matches("a/b/c.py", "a/**/*.py"))
        self.assertTrue(terra.matches("a/c.py", "a/**/*.py"))
        self.assertFalse(terra.matches("a/b/c.py", "a/*.py"))
        for name in ("C:/local.py", "../outside", "a\\b.py", "CON.txt", "a/NUL", "trailing. ", "a:stream", "/tmp/a"):
            with self.subTest(name=name):
                self.assertFalse(terra.portable(name))
        self.assertTrue(terra.portable("soil samples/measurements.py"))

    def test_case_and_unicode_collisions_even_on_insensitive_filesystems(self):
        blob = git(self.root, "hash-object", "-w", "algorithm.py")
        git(self.root, "update-index", "--add", "--cacheinfo", "100644", blob, "Algorithm.py")
        for name in ("caf\u00e9.py", "cafe\u0301.py"):
            git(self.root, "update-index", "--add", "--cacheinfo", "100644", blob, name)
        self.assertTrue(any("collision" in e for e in self.validate().errors))

    def test_portability_reserved_index_path(self):
        blob = git(self.root, "hash-object", "-w", "algorithm.py")
        git(self.root, "-c", "core.protectNTFS=false", "update-index", "--add", "--cacheinfo", "100644", blob, "NUL.txt")
        self.assertTrue(any("Non-portable" in e for e in self.validate().errors))

    def test_symlink_references_are_rejected(self):
        # Git can store a symlink even on Windows with core.symlinks=false.
        write(self.root, "linked.json", "schema.json")
        blob = git(self.root, "hash-object", "-w", "linked.json")
        git(self.root, "update-index", "--add", "--cacheinfo", "120000", blob, "linked.json")
        self.data.append({"kind": "contract", "id": "linked-v1", "owner": "alice", "path": "linked.json", "check": "fast"})
        self.assertTrue(any("symlink" in e for e in self.validate().errors))

    def test_text_line_endings_and_bom(self):
        (self.root / "algorithm.py").write_bytes(b"\xef\xbb\xbfVALUE = 1\r\n")
        self.assertTrue(any("LF line endings" in e for e in self.validate().errors))

    def test_force_added_credentials_and_bulk_are_rejected(self):
        write(self.root, ".env", "NOT_A_REAL_SECRET=fixture\n")
        write(self.root, "weights.pt", "not a real model\n")
        git(self.root, "add", "-f", ".env", "weights.pt")
        errors = self.validate().errors
        self.assertTrue(any("credentials" in e for e in errors))
        self.assertTrue(any("bulk data/model" in e for e in errors))

    def test_small_source_limit_and_notebook_outputs(self):
        self.data[0]["max_source_bytes"] = 20
        self.assertTrue(any("source size limit" in e for e in self.validate().errors))
        self.data[0]["max_source_bytes"] = 10485760
        write(self.root, "explore.ipynb", json.dumps({"cells": [{"outputs": [{"text": "private output"}], "execution_count": 1}]}))
        self.assertTrue(any("clear notebook" in e for e in self.validate().errors))

    def add_artifacts(self):
        self.data.extend([
            {"kind": "contract", "id": "features-v1", "owner": "alice", "path": "schema.json", "check": "fast"},
            {"kind": "artifact", "id": "dataset-v1", "type": "data", "uri": "s3://fixture/data/v1", "sha256": "a" * 64,
             "contract": "features-v1", "inputs": []},
            {"kind": "artifact", "id": "model-v1", "type": "model", "uri": "s3://fixture/model/v1", "sha256": "b" * 64,
             "contract": "features-v1", "inputs": ["dataset-v1"]},
        ])
        self.data[5]["contracts"] = ["features-v1"]
        self.data[5]["artifacts"] = ["dataset-v1", "model-v1"]

    def test_valid_artifact_references_need_no_download(self):
        self.add_artifacts()
        self.assertEqual(self.validate().errors, [])

    def test_artifact_input_contract_mismatch(self):
        self.add_artifacts()
        self.data.append({"kind": "contract", "id": "features-v2", "owner": "alice", "path": "schema.json", "check": "fast"})
        self.data[-2]["contract"] = "features-v2"
        self.assertTrue(any("contract mismatch" in e for e in self.validate().errors))

    def test_task_expected_contract_mismatch(self):
        self.add_artifacts()
        self.data[5]["contracts"] = []
        self.assertTrue(any("expected contracts" in e for e in self.validate().errors))

    def test_artifact_identity_is_immutable_against_base(self):
        self.add_artifacts()
        save(self.root, self.data)
        self.commit(self.root, "Register artifacts")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.data[-2].pop("inputs")  # Omitted empty defaults do not change identity.
        self.data[-1]["sha256"] = "B" * 64
        self.assertEqual(self.validate().errors, [])
        self.data[-1]["sha256"] = "c" * 64
        self.assertTrue(any("immutable artifact changed" in e for e in self.validate().errors))

    def test_contract_identity_is_immutable(self):
        self.add_artifacts()
        save(self.root, self.data)
        self.commit(self.root, "Register contract")
        self.base = git(self.root, "rev-parse", "HEAD")
        write(self.root, "schema.json", '{"unit":"percent"}\n')
        self.assertTrue(any("contract definition changed" in e for e in self.validate().errors))

    def test_rename_repairs_factual_references_without_a_new_contract(self):
        self.add_artifacts()
        save(self.root, self.data)
        self.commit(self.root, "Registered contract before rename")
        self.base = git(self.root, "rev-parse", "HEAD")
        git(self.root, "mv", "schema.json", "feature-schema.json")
        for entry in (self.data[1], self.data[5]):
            entry["paths"] = ["feature-schema.json" if p == "schema.json" else p for p in entry["paths"]]
        self.data[-3]["path"] = "feature-schema.json"
        self.assertEqual(self.validate().errors, [])

    def test_missing_reference_and_unsigned_uri(self):
        self.add_artifacts()
        self.data[-3]["path"] = "missing.json"
        self.data[-1]["uri"] = "https://example.invalid/weights?token=not-a-real-token"
        errors = self.validate().errors
        self.assertTrue(any("Missing reference" in e for e in errors))
        self.assertTrue(any("unsigned storage URI" in e for e in errors))

    def test_stale_handoff_and_continuation_preserve_original(self):
        self.data[5]["handoff"]["revision"] = self.base
        self.data[5]["handoff"]["evidence"] = ["Old baseline check passed"]
        save(self.root, self.data)
        write(self.root, "algorithm.py", "def score(x):\n    return max(0, x)\n")
        self.commit(self.root, "Original agent's shared progress")
        original = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-b", "continue/a")
        self.data[5]["branch"] = "continue/a"
        save(self.root, self.data)
        output = io.StringIO()
        with redirect_stdout(output):
            terra.Harness(self.root).brief("a")
        self.assertIn("stale", output.getvalue())
        self.assertIn("unpushed work is unknown", output.getvalue())
        self.assertEqual(git(self.root, "rev-parse", "task/a"), original)
        self.assertEqual(self.validate().errors, [])

    def test_brief_is_scoped_and_read_only(self):
        before = (self.root / terra.DOC).read_bytes()
        output = io.StringIO()
        with redirect_stdout(output):
            terra.Harness(self.root).brief("a")
        self.assertNotIn("Add a prediction application", output.getvalue())
        self.assertEqual((self.root / terra.DOC).read_bytes(), before)

    def test_fast_check_failure_and_timeout_are_explicit(self):
        for argv, message in ((["{python}", "-c", "raise SystemExit(3)"], "failed"),
                              (["{python}", "-c", "import time; time.sleep(3)"], "timed out")):
            self.data[4]["argv"] = argv
            self.data[4]["timeout"] = 1
            save(self.root, self.data)
            output = io.StringIO()
            with redirect_stdout(output):
                code = terra.main(["--root", str(self.root), "check", "--task", "a"])
            self.assertEqual(code, 1)
            self.assertIn(message, output.getvalue())

    def test_dependency_changes_run_consumer_checks(self):
        self.data.append({"kind": "check", "id": "consumer", "argv": ["{python}", "-c", "raise SystemExit(7)"], "timeout": 10})
        self.data[2]["checks"] = ["consumer"]
        write(self.root, "algorithm.py", "def score(x):\n    return max(0, x)\n")
        project = self.validate()
        with redirect_stdout(io.StringIO()):
            project.run_checks({"data"})
        self.assertTrue(any("consumer failed" in e for e in project.errors))

    def test_malformed_field_types_fail_as_diagnostics(self):
        for index, field in ((0, "owners"), (1, "owner"), (4, "argv"), (5, "status"), (5, "base")):
            data = copy.deepcopy(self.data)
            data[index][field] = [123]
            save(self.root, data)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(terra.main(["--root", str(self.root), "check", "--task", "a"]), 1)

    def test_invalid_records_and_merge_markers_fail_cleanly(self):
        for text in ('```json\n{"kind":\n```\n', '<<<<<<< ours\n=======\n>>>>>>> theirs\n'):
            write(self.root, terra.DOC, text)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(terra.main(["--root", str(self.root), "brief"]), 1)

    def clones(self):
        bare = self.root.parent / "shared.git"
        git(self.root, "clone", "--bare", str(self.root), str(bare))
        # The seed's task branch exists, but the shared default is main.
        git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
        clones = []
        for name in ("alice clone", "bob clone"):
            path = self.root.parent / name
            git(self.root, "clone", str(bare), str(path))
            self.configure(path)
            clones.append(path)
        return clones

    def test_two_clones_integrate_independent_work_and_revert_one_change(self):
        alice, bob = self.clones()
        git(alice, "checkout", "-b", "change-a")
        write(alice, "algorithm.py", "def score(x):\n    return max(0, x)\n")
        alice_data = copy.deepcopy(self.data)
        alice_data[5]["branch"] = "change-a"
        alice_data[5]["handoff"]["summary"] = "Clamping implemented; preserve lower bound"
        save(alice, alice_data)
        self.commit(alice, "Clamp algorithm")
        git(alice, "push", "origin", "change-a")
        git(bob, "checkout", "-b", "change-b")
        write(bob, "app.py", "READY = True\n")
        bob_data = copy.deepcopy(self.data)
        bob_data[6]["branch"] = "change-b"
        bob_data[6]["status"] = "active"
        bob_data[6]["handoff"]["summary"] = "Application entry point implemented"
        save(bob, bob_data)
        self.commit(bob, "Add application entry")
        git(bob, "push", "origin", "change-b")
        git(alice, "checkout", "main")
        git(alice, "merge", "--squash", "change-a")
        self.commit(alice, "Integrate clamping")
        integration = git(alice, "rev-parse", "HEAD")
        git(alice, "push", "origin", "main")
        git(bob, "fetch", "origin")
        git(bob, "checkout", "main")
        git(bob, "merge", "--ff-only", "origin/main")
        git(bob, "merge", "--squash", "change-b")
        candidate = terra.Harness(bob)
        candidate.validate(git(bob, "rev-parse", "HEAD"), "b")
        self.assertEqual(candidate.errors, [])
        self.assertIn("Clamping implemented", candidate.index["task"]["a"]["handoff"]["summary"])
        self.assertIn("Application entry point", candidate.index["task"]["b"]["handoff"]["summary"])
        self.commit(bob, "Integrate application")
        git(bob, "revert", "--no-edit", integration)
        self.assertEqual((bob / "algorithm.py").read_text(), "def score(x):\n    return x\n")
        self.assertEqual((bob / "app.py").read_text(), "READY = True\n")

    def test_two_clones_real_conflict_reconciles_both_intents(self):
        alice, bob = self.clones()
        git(alice, "checkout", "-b", "clamp")
        write(alice, "algorithm.py", "def score(x):\n    return max(0, min(1, x))\n")
        self.commit(alice, "Clamp ratio into valid range")
        git(alice, "push", "origin", "clamp")
        git(bob, "checkout", "-b", "percent")
        write(bob, "algorithm.py", "def score(x):\n    return x * 100\n")
        self.commit(bob, "Expose score as percent")
        percent = git(bob, "rev-parse", "HEAD")
        git(bob, "fetch", "origin")
        conflict = git(bob, "merge", "origin/clamp", ok=False)
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("<<<<<<<", (bob / "algorithm.py").read_text())
        # Simulate the on-demand integrating agent, not a blind automatic merge.
        write(bob, "algorithm.py", "def score(x):\n    return max(0, min(1, x)) * 100\n")
        data = copy.deepcopy(self.data)
        data[5]["branch"] = "percent"
        data[5]["intent"] = "Preserve clamping and expose the result as percent"
        data[4]["argv"] = ["{python}", "-c", "from algorithm import score; assert score(-1)==0; assert score(0.25)==25; assert score(2)==100"]
        save(bob, data)
        git(bob, "add", "algorithm.py", terra.DOC)
        project = terra.Harness(bob)
        affected, _, _ = project.validate(self.base, "a")
        self.assertEqual(project.errors, [])
        with redirect_stdout(io.StringIO()):
            project.run_checks(affected)
        self.assertEqual(project.errors, [])
        self.assertEqual(git(bob, "rev-parse", "HEAD"), percent)
        self.assertTrue(git(bob, "show", "origin/clamp:algorithm.py"))


if __name__ == "__main__":
    unittest.main()
