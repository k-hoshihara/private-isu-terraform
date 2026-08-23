# Agent instructions

This repository uses portable agent files: `AGENTS.md` (always-on) and `.agents/skills/` (on demand). Do not add Grok-only (`.grok/`) or OpenCode-only (`.opencode/`) copies of these rules.

## Git: agents do not commit

This applies to every agent, subagent, and git/Orca worktree worker in this repo.

1. Make file changes only. Leave them **uncommitted** (`git add` / `git commit` / `git commit --amend` / `git push` / `--force` are forbidden unless the user explicitly asks for that action in the current message).
2. Stop for human review. Point at `git status` and `git diff` (working tree vs `HEAD`), not at a commit range.
3. "Looks good" / review approval is **not** permission to commit. Commit only when the user says to commit (then **one** commit, squash if several drafts exist). Push only when the user says to push.
4. Do not remove a child worktree that still has uncommitted work (`worktree rm` would drop it).

## Integration worktrees

Implementation work does **not** land directly on the parent checkout.

1. Treat the **current parent worktree branch** as the integration branch. That is usually a `feature/*` branch, not `main` or `master`.
2. For a discrete implementation task, follow [`.agents/skills/task-worktree/SKILL.md`](.agents/skills/task-worktree/SKILL.md): create a child worktree from that integration branch, do the work there, then stop for review.
3. Do **not** merge, rebase onto `main`/`master`, commit, push, or open a PR until the user has reviewed the working-tree diff and explicitly asked for the next git action.
4. After the user asks to land it on the parent feature branch, take the reviewed changes there as **one** commit (`git merge --squash` after a single child commit, or apply the child's working tree and commit once on the parent). Then stop unless they also asked to push.
5. Never merge to `main`/`master` unless the user explicitly names that branch. Remove the child worktree only after the work is committed somewhere the user accepted.

Questions, read-only investigation, and tiny one-line answers stay in the parent worktree.

## Project

Terraform for [catatsuy/private-isu](https://github.com/catatsuy/private-isu) on AWS (`ap-northeast-1`). Run `terraform` in `terraform/`. Operational docs live in `docs/` (lite / heavy / local × single / multi; shared files in `docs/common/`). Do not `terraform apply` / `destroy` unless the user asks.
