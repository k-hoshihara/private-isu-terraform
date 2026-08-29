# Agent instructions

This repository uses portable agent files: `AGENTS.md` (always-on) and `.agents/skills/` (on demand). Do not add Grok-only (`.grok/`) or OpenCode-only (`.opencode/`) copies of these rules.

## Integration worktrees

Implementation work does **not** land directly on the parent checkout.

1. Treat the **current parent worktree branch** as the integration branch. That is usually a `feature/*` branch, not `main` or `master`.
2. For a discrete implementation task, follow [`.agents/skills/task-worktree/SKILL.md`](.agents/skills/task-worktree/SKILL.md): create a child worktree from that integration branch, do the work there, then stop for review.
3. Do **not** merge, rebase onto `main`/`master`, push, or open a PR until the user has reviewed the child worktree and explicitly approved.
4. After approval, merge **into the parent feature branch**, `git push` that branch, then remove the child worktree.
5. Never merge to `main`/`master` unless the user explicitly names that branch.

Questions, read-only investigation, and tiny one-line answers stay in the parent worktree.

## Project

Terraform for [catatsuy/private-isu](https://github.com/catatsuy/private-isu) on AWS (`ap-northeast-1`). Run `terraform` in `terraform/`. Operational docs live in `docs/` (lite / heavy / local × single / multi; shared files in `docs/common/`). Do not `terraform apply` / `destroy` unless the user asks.
