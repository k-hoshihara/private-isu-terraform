---
name: task-worktree
description: >-
  Create a child git/Orca worktree from the current feature branch, implement
  there as uncommitted changes, and stop for user review. Never git commit or
  git push unless the user explicitly asks. After they ask to land it, one
  commit on the parent feature branch (not main). Use when a discrete
  implementation task starts, the user asks
  for a worktree / 別ワークツリー / 子 worktree, or before editing this repo for a
  new task. Do not use for read-only questions.
---

# Task worktree

Policy lives in the repo-root `AGENTS.md`. This skill is the procedure.

## When

Use for a discrete implementation task (code, Terraform, docs that change the tree).

Stay in the parent worktree for questions, read-only investigation, or a one-line clarification.

## Resolve the Orca CLI

If this session is inside Orca, pick one executable and reuse it. Do not create a shell variable named `ORCA`.

- `ORCA_CLI_COMMAND` if set (WSL managed sessions: `orca-ide`)
- else `orca-ide` on Linux outside an Orca-managed terminal
- else `orca`

Never run bare `orca` on unmanaged Linux (GNOME screen reader). If the selected binary cannot run, stop and report the error.

Load current flags from `ORCA skills get orca-cli` before inventing subcommands.

If Orca is not available, use `git worktree` the same way (child branch from the current feature branch).

## 1. Identify the integration branch

The integration branch is the **parent worktree's current branch**, not `main` / `master`.

```text
ORCA status --json
ORCA worktree current --json
```

Or: `git rev-parse --abbrev-ref HEAD` in the parent checkout.

If that branch is `main` or `master`, stop and ask. Do not stack a task on `main` unless the user said to.

## 2. Create the child from that branch

Orca lineage must be stacked on the parent. Pass `--base-branch` explicitly. Do not omit it (Orca would use the repo default, usually `origin/main`). Do not use `--no-parent`.

```text
ORCA worktree create --name <task-slug> --parent-worktree active --base-branch <integration-branch> --json
```

Do **not** pass `--agent` unless the user asked for another agent in the child. This session does the work in the child path.

From the JSON, keep the full worktree `id` (`<repoId>::<path>`) and the child filesystem path / new branch name.

Git fallback:

```text
git worktree add -b <task-slug> <sibling-dir> <integration-branch>
```

Put the sibling dir next to this repo (for example `../private-isu-terraform-<task-slug>`).

## 3. Implement only in the child

All edits happen in the child checkout as a **dirty working tree**. Policy: `AGENTS.md` (agents do not commit).

- Do not edit the parent worktree.
- Do not `git add` / `git commit` / `git commit --amend`.
- Do not merge.
- Do not `git push`.
- Do not open a PR to `main` / `master`.
- Do not `terraform apply` / `destroy` unless the user asked.

## 4. Stop for review

When the child work is ready:

```text
ORCA worktree set --worktree id:<repoId>::<childPath> --workspace-status in-review --json
```

Then stop. Show the user:

- child worktree path and branch
- integration (parent) branch
- summary of what changed
- how to inspect: `git status` and `git diff` (and `git diff --stat`) **in the child**

Do not commit, merge, or push in this step.

## 5. After the user asks to commit or land it

Review approval alone is not enough. Only when the user says to commit / land / squash:

- Prefer **one** commit on the parent feature branch: bring the child's working-tree changes onto the parent, then `git commit` once if they asked to commit.
- If they asked to commit in the child first, make **one** commit there, then on the parent `git merge --squash <child-branch>` and commit once if they asked to land it.
- Merge target is the parent feature branch, not `main` / `master`, unless the user named that branch.

Then **stop**. Do not `git push` and do not `--force` unless they asked to push. Tell them `git status` and the commit hash if you created one.

Do not `worktree rm` while the child still has uncommitted work. Remove only after the work is committed where the user accepted:

```text
ORCA worktree rm --worktree id:<repoId>::<childPath> --json
```

Git fallback: `git worktree remove <child-path>`.

If the merge or rebase conflicts, stop in the parent and report.
