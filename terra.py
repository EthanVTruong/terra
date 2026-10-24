"""Terra's on-demand, standard-library-only coordination reader and checker."""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import tempfile
import time
import unicodedata
from urllib.parse import urlsplit

DOC = "COORDINATION.md"
KINDS = {"policy", "domain", "task", "check", "contract", "artifact"}
STATES = {"approved", "active", "blocked", "done"}
BULK = {".pt", ".pth", ".ckpt", ".onnx", ".h5", ".hdf5", ".parquet",
        ".arrow", ".npy", ".npz", ".pkl", ".joblib", ".safetensors",
        ".csv", ".tsv", ".jsonl", ".feather", ".pickle", ".tif", ".tiff"}
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
FENCES = re.compile(r"^```json[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)


CHECK_OUTPUT_LIMIT = 1024 * 1024


def _stop_check_tree(process):
    """Bounded cleanup of only this check's newly created process tree."""
    error = None
    try:
        if os.name == "nt":
            stopped = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if stopped.returncode:
                error = "process-tree termination could not be confirmed"
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except (OSError, subprocess.TimeoutExpired):
        error = "process-tree termination could not be confirmed"
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            error = "check process did not exit after termination"
    return error


def _run_check(argv, root, timeout, output_limit):
    # File capture does not wait for pipe EOF from a check's descendants.
    # Polling bounds captured/returned output; disk writes can briefly overshoot.
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
               if os.name == "nt" else {"start_new_session": True})
    started = time.monotonic()
    reason, cleanup_error = None, None
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(argv, cwd=root, stdout=output, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, **options)
        deadline = started + timeout
        while True:
            if os.fstat(output.fileno()).st_size > output_limit:
                reason = "output limit exceeded"
                break
            if process.poll() is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "timed out"
                break
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                pass
        if reason:
            cleanup_error = _stop_check_tree(process)
        size = os.fstat(output.fileno()).st_size
        if size > output_limit:
            reason = reason or "output limit exceeded"
        marker = b"\n[check output truncated]\n" if size > output_limit else b""
        output.seek(0)
        captured = output.read(max(0, output_limit - len(marker))) + marker[:output_limit]
    return process.returncode, captured.decode("utf-8", "replace"), reason, cleanup_error, min(size, output_limit), time.monotonic() - started


def records(text):
    """One source of truth: JSON records embedded in readable Markdown."""
    if re.search(r"^(?:<<<<<<<|=======|>>>>>>>)", text, re.M):
        raise ValueError(f"{DOC}: unresolved merge markers; reconcile both intents")
    found = []
    for block in FENCES.finditer(text.replace("\r\n", "\n")):
        try:
            item = json.loads(block.group(1))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{DOC}: invalid JSON record: {exc.msg}") from exc
        if (not isinstance(item, dict) or not isinstance(item.get("kind"), str)
                or item["kind"] not in KINDS):
            raise ValueError(f"{DOC}: each JSON block needs a supported record kind")
        found.append(item)
    if not found:
        raise ValueError(f"{DOC}: no coordination records found")
    return found


def matches(path, pattern):
    """Segment-aware globs, unlike fnmatch's slash-crossing star."""
    parts, tokens = path.split("/"), pattern.split("/")

    def visit(i, j):
        if j == len(tokens):
            return i == len(parts)
        if tokens[j] == "**":
            return visit(i, j + 1) or (i < len(parts) and visit(i + 1, j))
        return (i < len(parts) and fnmatch.fnmatchcase(parts[i], tokens[j])
                and visit(i + 1, j + 1))
    return visit(0, 0)


def portable(path, pattern=False):
    if (not isinstance(path, str) or not path or "\\" in path or ":" in path
            or path.startswith("/") or any(p in {"", ".", "..", ".git"} for p in path.split("/"))):
        return False
    for part in path.split("/"):
        if (part.endswith((".", " ")) or RESERVED.match(part)
                or any(ord(c) < 32 or c in '<>"|' for c in part)):
            return False
        if not pattern and any(c in "*?" for c in part):
            return False
    return True


class Harness:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.items = records((self.root / DOC).read_text(encoding="utf-8"))
        self.errors, self.warnings = [], []
        self.index = {kind: {} for kind in KINDS - {"policy"}}
        policies = [r for r in self.items if r["kind"] == "policy"]
        self.policy = policies[0] if len(policies) == 1 else {}
        if len(policies) != 1 or type(self.policy.get("version")) is not int or self.policy.get("version") != 1:
            self.errors.append("Declare exactly one version-1 policy")
        for item in self.items:
            if item["kind"] == "policy":
                continue
            key = item.get("id")
            if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9][\w.-]*", key):
                self.errors.append("Every non-policy record needs a portable, stable id")
            elif key in self.index[item["kind"]]:
                self.errors.append(f"Duplicate {item['kind']} id: {key}")
            else:
                self.index[item["kind"]][key] = item
        self.branch = (self.git("symbolic-ref", "--short", "HEAD", required=False).strip()
                       or os.environ.get("TERRA_BRANCH", ""))
        self.head = self.git("rev-parse", "--verify", "HEAD", required=False).strip()
        self.is_git = self.git("rev-parse", "--is-inside-work-tree", required=False).strip() == "true"

    def git(self, *args, required=True):
        result = subprocess.run(["git", "-C", str(self.root), *args],
                                capture_output=True, timeout=15)
        if result.returncode:
            if required:
                raise ValueError(result.stderr.decode("utf-8", "replace").strip())
            return ""
        return result.stdout.decode("utf-8", "surrogateescape")

    def files(self):
        if self.is_git:
            return sorted(set(filter(None, self.git(
                "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0"))))
        ignored = {".git", ".venv", "venv", "__pycache__", ".terra-local"}
        return sorted(str(p.relative_to(self.root)).replace(os.sep, "/")
                      for p in self.root.rglob("*") if p.is_file()
                      and not ignored.intersection(p.relative_to(self.root).parts))

    def strings(self, item, name, required=False):
        value = item.get(name, [])
        if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
            self.errors.append(f"{item.get('id', item['kind'])}: {name} must be a list of strings")
            return []
        if required and not value:
            self.errors.append(f"{item.get('id', item['kind'])}: configure {name}; validation is missing")
        return value

    def ref_path(self, path):
        if not portable(path):
            self.errors.append(f"Invalid repository-relative reference: {path!r}")
            return None
        resolved = self.root / path
        if any(p.is_symlink() for p in [resolved, *resolved.parents] if p != self.root.parent):
            self.errors.append(f"Source reference traverses a symlink: {path}")
            return None
        if not resolved.is_file():
            self.errors.append(f"Missing reference: {path}; repair the mapping or restore the file")
            return None
        return resolved

    def task(self, key=None):
        tasks = self.index["task"]
        if key:
            if key not in tasks:
                raise ValueError(f"Unknown task {key!r}; run brief for approved work")
            return tasks[key]
        candidates = [t for t in tasks.values() if t.get("branch") == self.branch]
        current = [t for t in candidates if t.get("status") == "active"]
        if len(current or candidates) > 1:
            raise ValueError("Several tasks match this branch; pass --task <id>")
        return (current or candidates or [None])[0]

    def validate_records(self):
        owners = self.policy.get("owners", {})
        if not isinstance(owners, dict) or not owners or any(
                not isinstance(k, str) or not isinstance(v, str) or not v for k, v in owners.items()):
            self.errors.append("Policy owners must map human aliases to descriptions")
            owners = {}
        maximum = self.policy.get("max_source_bytes", 10485760)
        if type(maximum) is not int or not 1 <= maximum <= 104857600:
            self.errors.append("max_source_bytes must be between 1 and 104857600")
        for kind in ("domain", "task", "contract"):
            for item in self.index[kind].values():
                if not isinstance(item.get("owner"), str) or item["owner"] not in owners:
                    self.errors.append(f"{item['id']}: unknown human owner {item.get('owner')!r}")
        for kind in ("domain", "task"):
            group = self.index[kind]
            for item in group.values():
                for path in self.strings(item, "paths", required=True):
                    if not portable(path, pattern=True):
                        self.errors.append(f"{item['id']}: unsafe path pattern {path!r}")
                for dep in self.strings(item, "depends_on"):
                    if dep not in group:
                        self.errors.append(f"{item['id']}: unknown {kind} dependency {dep}")
            self.cycles(kind)
        for domain in self.index["domain"].values():
            if not isinstance(domain.get("purpose"), str) or not domain["purpose"].strip():
                self.errors.append(f"{domain['id']}: describe its domain responsibility")
            for key in self.strings(domain, "checks", required=True):
                if key not in self.index["check"]:
                    self.errors.append(f"{domain['id']}: missing fast check {key}")
        for check in self.index["check"].values():
            self.strings(check, "argv", required=True)
            if type(check.get("timeout", 60)) is not int or not 1 <= check.get("timeout", 60) <= 120:
                self.errors.append(f"{check['id']}: fast-check timeout must be 1..120 seconds")
        for task in self.index["task"].values():
            if not isinstance(task.get("status"), str) or task["status"] not in STATES:
                self.errors.append(f"{task['id']}: status must be approved, active, blocked, or done")
            for field in ("intent", "branch"):
                if not isinstance(task.get(field), str) or not task[field].strip():
                    self.errors.append(f"{task['id']}: missing {field}")
            if isinstance(task.get("branch"), str) and not self.git(
                    "check-ref-format", "--branch", task["branch"], required=False).strip():
                self.errors.append(f"{task['id']}: invalid Git branch name")
            if task.get("base") is not None and not isinstance(task["base"], str):
                self.errors.append(f"{task['id']}: base must be a revision string or null")
            for field, kind in (("domains", "domain"), ("contracts", "contract"), ("artifacts", "artifact")):
                for key in self.strings(task, field, required=field == "domains"):
                    if key not in self.index[kind]:
                        self.errors.append(f"{task['id']}: unknown {kind} reference {key}")
            for key in self.strings(task, "artifacts"):
                artifact = self.index["artifact"].get(key)
                if artifact and artifact.get("contract") not in self.strings(task, "contracts"):
                    self.errors.append(f"{task['id']}: artifact {key} does not match its expected contracts")
            handoff = task.get("handoff", {})
            if not isinstance(handoff, dict):
                self.errors.append(f"{task['id']}: handoff must be an object")
            else:
                if handoff.get("revision") is not None and not isinstance(handoff["revision"], str):
                    self.errors.append(f"{task['id']}: handoff revision must be a string or null")
                if isinstance(task.get("status"), str) and task["status"] in {"blocked", "done"} and not all(
                        isinstance(handoff.get(k), str) and handoff[k].strip() for k in ("summary", "next")):
                    self.errors.append(f"{task['id']}: a {task['status']} task needs a summary and next action")
        for contract in self.index["contract"].values():
            self.ref_path(contract.get("path"))
            if not isinstance(contract.get("check"), str) or contract["check"] not in self.index["check"]:
                self.errors.append(f"{contract['id']}: configure its contract check")
        for artifact in self.index["artifact"].values():
            key = artifact["id"]
            if not isinstance(artifact.get("type"), str) or artifact["type"] not in {"data", "model"}:
                self.errors.append(f"{key}: artifact type must be data or model")
            if not isinstance(artifact.get("sha256"), str) or not re.fullmatch(r"[a-fA-F0-9]{64}", artifact["sha256"]):
                self.errors.append(f"{key}: provide an exact SHA-256 digest, never latest")
            uri = artifact.get("uri", "")
            try:
                parsed = urlsplit(uri) if isinstance(uri, str) else None
                valid = bool(parsed and uri and parsed.scheme and parsed.scheme != "file"
                             and not parsed.username and not parsed.password and not parsed.query
                             and not parsed.fragment and len(parsed.scheme) > 1)
            except ValueError:
                valid = False
            if not valid:
                self.errors.append(f"{key}: use an unsigned storage URI without credentials/query tokens")
            if not isinstance(artifact.get("contract"), str) or artifact["contract"] not in self.index["contract"]:
                self.errors.append(f"{key}: unknown versioned contract {artifact.get('contract')!r}")
            for key in self.strings(artifact, "inputs"):
                other = self.index["artifact"].get(key)
                if not other:
                    self.errors.append(f"{artifact['id']}: missing input artifact {key}")
                elif artifact.get("type") == "model" and artifact.get("contract") != other.get("contract"):
                    self.errors.append(f"{artifact['id']}: contract mismatch with input {key}")

    def cycles(self, kind):
        group, visiting, visited = self.index[kind], set(), set()

        def visit(key):
            if key in visiting:
                self.errors.append(f"{kind} dependency cycle includes {key}")
                return
            if key in visited or key not in group:
                return
            visiting.add(key)
            for dep in self.strings(group[key], "depends_on"):
                visit(dep)
            visiting.remove(key)
            visited.add(key)
        for key in group:
            visit(key)

    def changes(self, base):
        if not self.is_git or not self.head:
            if base:
                raise ValueError(f"Cannot validate base {base!r} without a committed local checkpoint")
            self.warnings.append("No committed checkpoint yet; checking current files, not shared history")
            return self.files()
        revision = self.git("rev-parse", "--verify", "--end-of-options", f"{base or 'HEAD'}^{{commit}}", required=False).strip()
        if not revision:
            raise ValueError(f"Unknown base {base!r}; fetch/restore the actual checkpoint before integration")
        self.checked_base = revision
        ancestor = self.git("merge-base", revision, "HEAD", required=False).strip()
        if ancestor != revision:
            self.errors.append("Base is not an ancestor of HEAD; reconcile upstream in your task copy first")
        changed = self.git("diff", "--no-ext-diff", "--name-only", "--no-renames", "-z", revision, "--").split("\0")
        extra = self.git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
        return sorted(set(filter(None, changed + extra)))

    def validate_files(self, changed, task, previous):
        seen = {}
        limit = self.policy.get("max_source_bytes", 10485760)
        if type(limit) is not int or not 1 <= limit <= 104857600:
            limit = 10485760
        domains = self.index["domain"]
        affected = set()
        git_links = set()
        if self.is_git:
            git_links = {line.split("\t", 1)[1] for line in self.git("ls-files", "--stage", "-z").split("\0")
                         if line.startswith("120000 ") and "\t" in line}
        current_files = self.files()
        for name in current_files:
            normalized = unicodedata.normalize("NFC", name).casefold()
            if normalized in seen and seen[normalized] != name:
                self.errors.append(f"Case/Unicode filename collision: {seen[normalized]} and {name}")
            seen[normalized] = name
            if not portable(name):
                self.errors.append(f"Non-portable source path: {name!r}")
            path = self.root / name
            if name in git_links or path.is_symlink() or not path.resolve().is_relative_to(self.root):
                self.errors.append(f"Source must not be a symlink or leave the repository: {name}")
                continue
            if not path.is_file():
                continue
            if path.stat().st_size > limit:
                self.errors.append(f"{name}: exceeds source size limit; use an artifact reference")
                continue
            suffix = path.suffix.lower()
            fixture = "fixtures" in PurePosixPath(name).parts
            if suffix in BULK and not fixture:
                self.errors.append(f"{name}: bulk data/model output belongs in versioned storage")
            if ((path.name == ".env" or path.name.startswith(".env."))
                    and path.name not in {".env.example", ".env.template"}) or suffix in {".pem", ".key"}:
                self.errors.append(f"{name}: credentials do not belong in shared source")
            if suffix in {".py", ".md", ".json", ".yaml", ".yml", ".ipynb"}:
                try:
                    content = path.read_bytes()
                    text = content.decode("utf-8")
                    if text.startswith("\ufeff") or b"\r" in content:
                        self.errors.append(f"{name}: use UTF-8 without BOM and LF line endings")
                    if suffix == ".ipynb" and any(c.get("outputs") or c.get("execution_count") is not None
                                                       for c in json.loads(text).get("cells", [])):
                        self.errors.append(f"{name}: clear notebook outputs/execution counts before sharing")
                except (UnicodeError, json.JSONDecodeError, AttributeError, TypeError):
                    self.errors.append(f"{name}: invalid UTF-8 text or notebook JSON")
        for name in changed:
            owners = [d for d in domains.values() if any(matches(name, p) for p in self.strings(d, "paths"))]
            # Attribute deleted paths using the base when a rename repairs the map.
            if not owners and name not in current_files:
                owners = [d for (kind, _), d in previous.items() if kind == "domain"
                          and d["id"] in domains and any(matches(name, p) for p in d.get("paths", []))]
            if len(owners) != 1:
                self.errors.append(f"{name}: map it to exactly one owned domain (found {len(owners)})")
                continue
            affected.add(owners[0]["id"])
            if task and name != DOC:
                scope = self.strings(task, "paths")
                if name not in current_files:
                    scope = scope + previous.get(("task", task["id"]), {}).get("paths", [])
                if owners[0]["id"] not in self.strings(task, "domains") or not any(matches(name, p) for p in scope):
                    self.errors.append(f"{name}: outside task {task['id']}'s declared scope")
        if changed and task is None:
            self.errors.append("Changed files need a recorded task; pass --task <id> or record this branch")
        return affected

    def history(self, base):
        if not base or not self.head:
            return {}
        text = self.git("show", "--no-ext-diff", f"{base}:{DOC}", required=False)
        if not text:
            return {}
        previous = {(r["kind"], r.get("id")): r for r in records(text)}
        for artifact in self.index["artifact"].values():
            old = previous.get(("artifact", artifact["id"]))
            if old and (old.get("sha256", "").lower() != artifact["sha256"].lower()
                        or any(old.get(k) != artifact.get(k) for k in ("type", "contract"))
                        or old.get("inputs", []) != artifact.get("inputs", [])):
                self.errors.append(f"{artifact['id']}: immutable artifact changed; register a new version ID")
        for contract in self.index["contract"].values():
            old = previous.get(("contract", contract["id"]))
            path = self.root / contract.get("path", "")
            if old and path.is_file() and portable(old.get("path")):
                before = self.git("show", "--no-ext-diff", f"{base}:{old['path']}", required=False)
                after = path.read_text(encoding="utf-8")
                if before.replace("\r\n", "\n") != after.replace("\r\n", "\n"):
                    self.errors.append(f"{contract['id']}: contract definition changed; register a new version ID")
        return previous

    def task_warnings(self, task):
        if task:
            for dep in self.strings(task, "depends_on"):
                if self.index["task"].get(dep, {}).get("status") != "done":
                    self.errors.append(f"{task['id']}: dependency {dep} is not done")
            if task.get("status") == "blocked":
                self.errors.append(f"{task['id']}: blocked; resolve its recorded blocker before integration")
        active = [t for t in self.index["task"].values() if t.get("status") == "active"]
        for i, first in enumerate(active):
            for second in active[i + 1:]:
                a, b = self.strings(first, "paths"), self.strings(second, "paths")
                common = (set(a) & set(b)) - {DOC}
                overlap = any(any(matches(p, x) for x in a) and any(matches(p, x) for x in b)
                              for p in self.files() if p != DOC)
                if common or overlap:
                    self.warnings.append(f"Overlapping active tasks: {first['id']} / {second['id']}; coordinate before expanding")

    def validate(self, base=None, task_id=None):
        self.validate_records()
        if self.errors:
            return set(), None, None
        task = self.task(task_id)
        base = base or (task or {}).get("base") or ("HEAD" if self.head else None)
        changed = self.changes(base)
        base = getattr(self, "checked_base", base)
        previous = self.history(base)
        affected = self.validate_files(changed, task, previous)
        self.task_warnings(task)
        return affected, task, base

    def run_checks(self, affected):
        # A producer change also needs its declared consumers' fast checks.
        affected = set(affected)
        while True:
            consumers = {key for key, domain in self.index["domain"].items()
                         if set(self.strings(domain, "depends_on")) & affected}
            if consumers <= affected:
                break
            affected.update(consumers)
        names = set()
        for key in affected:
            names.update(self.strings(self.index["domain"][key], "checks"))
        for contract in self.index["contract"].values():
            if any(any(matches(contract["path"], p) for p in self.strings(self.index["domain"][d], "paths"))
                   for d in affected):
                names.add(contract["check"])
        if not affected:
            names.update(k for d in self.index["domain"].values() for k in self.strings(d, "checks"))
        output_remaining = CHECK_OUTPUT_LIMIT
        for name in sorted(names):
            if not output_remaining:
                self.errors.append("Fast checks exhausted the total command-output limit")
                break
            entry = self.index["check"][name]
            argv = [sys.executable if part == "{python}" else part for part in entry["argv"]]
            timeout = entry.get("timeout", 60)
            print(f"RUN {name} (limit {timeout}s)", flush=True)
            try:
                code, output, reason, cleanup_error, used, elapsed = _run_check(argv, self.root, timeout, output_remaining)
                output_remaining -= used
                if reason or code:
                    detail = f"{reason} after {elapsed:.2f}s" if reason else f"failed (exit {code})"
                    self.errors.append(f"Fast check {name} {detail}; preserve work and escalate after one repair cycle")
                    if output:
                        print(output, end="" if output.endswith("\n") else "\n", flush=True)
                else:
                    print(f"PASS {name} ({elapsed:.2f}s)", flush=True)
                if cleanup_error:
                    self.errors.append(f"Fast check {name}: {cleanup_error}")
            except OSError as exc:
                self.errors.append(f"Cannot run {name}: {exc}")

    def brief(self, key=None):
        self.validate_records()
        if self.errors:
            raise ValueError("; ".join(dict.fromkeys(self.errors)))
        task = self.task(key)
        print(f"Terra | {self.branch or 'detached/unversioned'} | {(self.head or 'no committed checkpoint')[:40]}")
        print("Shared view is from local Git only; other computers' unpushed work is unknown.")
        print("Scope stays assigned offline. Refresh before expanding/integrating; one repair cycle, then park and notify.")
        self.task_warnings(task)
        if task:
            print(f"\n{task['id']} [{task['status']}] owner={task['owner']} branch={task['branch']}")
            print(f"Intent: {task['intent']}\nScope: {', '.join(task['paths'])}")
            for domain in task["domains"]:
                entry = self.index["domain"][domain]
                print(f"Domain {domain} ({entry['owner']}): {entry['purpose']}")
            for field in ("depends_on", "contracts", "artifacts"):
                if task.get(field):
                    print(f"{field}: {', '.join(task[field])}")
            handoff = task.get("handoff", {})
            for field in ("summary", "evidence", "uncertainty", "next"):
                if handoff.get(field):
                    print(f"{field}: {json.dumps(handoff[field], ensure_ascii=False) if isinstance(handoff[field], list) else handoff[field]}")
            revision = handoff.get("revision")
            if not revision:
                self.warnings.append("Handoff has no committed source revision; verify local progress before relying on it")
            else:
                resolved = self.git("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}", required=False).strip()
                if not resolved:
                    self.warnings.append("Handoff revision is unavailable locally; fetch its branch or ask the owner")
                elif self.git("diff", "--no-ext-diff", "--name-only", resolved, "--", ":(exclude)" + DOC):
                    self.warnings.append("Source differs from handoff revision; treat its verification evidence as stale")
                if self.git("ls-files", "--others", "--exclude-standard", "-z"):
                    self.warnings.append("Untracked source is outside committed handoff evidence; inspect it before relying on that evidence")
        else:
            print("\nNo task is recorded for this branch. Approved eligible work:")
            eligible = [entry for entry in self.index["task"].values()
                        if entry["status"] == "approved" and all(self.index["task"][d]["status"] == "done"
                        for d in entry.get("depends_on", []))]
            for entry in eligible[:5]:
                print(f"- {entry['id']} ({entry['owner']}): {entry['intent']}")
            if len(eligible) > 5:
                print(f"{len(eligible) - 5} more eligible tasks in {DOC}; retrieve a specific task to expand.")
            print("Use brief <task-id> for a handoff; record a task before new edits.")
        for warning in dict.fromkeys(self.warnings):
            print(f"WARN {warning}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent,
                        help="repository root (defaults to this script's directory)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("brief", help="read only relevant current context").add_argument("task", nargs="?")
    check = commands.add_parser("check", help="validate records, changed scope, and configured fast checks")
    check.add_argument("--base", help="actual upstream commit/ref; reconcile it before integrating")
    check.add_argument("--task", help="task being validated, including on a detached CI merge")
    args = parser.parse_args(argv)
    try:
        project = Harness(args.root)
        if args.command == "brief":
            project.brief(args.task)
            return 0
        affected, task, base = project.validate(args.base, args.task)
        if not project.errors:
            project.run_checks(affected)
        for warning in dict.fromkeys(project.warnings):
            print(f"WARN {warning}")
        for error in dict.fromkeys(project.errors):
            print(f"FAIL {error}")
        if project.errors:
            print("Integration blocked. Preserve both intents/branches; make one focused repair, then escalate if unresolved.")
            return 1
        print(f"PASS coordination + filesystem + configured fast checks; task={(task or {}).get('id', 'none')}, base={base or 'uncommitted'}")
        print("Scientific/model acceptance is separate; no artifact downloads or full pipeline jobs were run by the harness.")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"FAIL {exc}")
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
