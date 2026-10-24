# Terra agent agreement

Read `python terra.py brief` at a new task or resumption; request a task ID for detail. Codex, Claude Code and Cursor share these rules. COORDINATION.md is the shared record.

- Sync at normal Git checkpoints when online. Choose approved eligible work, record intent/owner/scope/branch, and refresh before expanding scope or integrating. Offline, continue assigned scope only.
- Use a task branch; isolate concurrent local agents in separate working copies. Preserve unrelated edits. Bootstrap may use main before the first commit.
- Update your task section in place at meaningful changes and handoff. Record decisions, revision, evidence, uncertainty and next step. Share task facts, not private transcripts. Keep old detail in Git history.
- Respect domain ownership and contracts. Maintain factual mappings; propose architecture/boundary changes. Modules are authoritative; notebooks explore. Reference exact data/model versions; full ingestion/training needs approved work.
- Before integration run `python terra.py check --base <upstream-ref> --task <id>`. Keep each merge one small, independently revertible change.
- For overlap, one existing agent reconciles both intents and validates ONE repair cycle. Preserve both branches. If unresolved, park that integration, notify the owner and continue eligible work. Never force-push shared history or blindly choose ours/theirs.
