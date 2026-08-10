# Agent instructions

This repository uses portable agent files: `AGENTS.md` (always-on) and `.agents/skills/` (on demand). Do not add Grok-only (`.grok/`) or OpenCode-only (`.opencode/`) copies of these rules.

## Git: agents do not commit

This applies to every agent, subagent, and git/Orca worktree worker in this repo.

1. Make file changes only. Leave them **uncommitted** (`git add` / `git commit` / `git commit --amend` / `git push` / `--force` are forbidden unless the user explicitly asks for that action in the current message).
2. Stop for human review. Point at `git status` and `git diff` (working tree vs `HEAD`), not at a commit range.
3. "Looks good" / review approval is **not** permission to commit. Commit only when the user says to commit (then **one** commit, squash if several drafts exist). Push only when the user says to push.
4. Do not remove a child worktree that still has uncommitted work (`worktree rm` would drop it). After a successful land onto `feature/<topic>`, removing that child worktree is the **standard** next step (see below). Do not wait for a second message that only says to delete it.

## Branches

Three roles. Names follow the current stream; do not hard-code a topic.

| Role | Typical name | Meaning |
| --- | --- | --- |
| Upstream | `origin/main` | Published default. Do not rebase onto it, merge to it, open a PR to it, or use it as the diff base unless the user names `main`. |
| Develop | `develop/<topic>` | Local integration for one stream. Feature-stream diffs are against this branch. |
| Feature | `feature/<topic>` | Local work on that stream. Parent checkout. Child worktrees stack here. |

`<topic>` is the suffix of the parent feature branch (for example `feature/foo` pairs with `develop/foo`). If that `develop/<topic>` branch does not exist, stop and ask. Do not fall back to `origin/main`.

Compare the stream with:

```text
git diff develop/<topic>...feature/<topic>
git log --oneline develop/<topic>..feature/<topic>
```

Child in-review still uses the child's working tree vs `HEAD` (`git status` / `git diff`). After work is on the feature branch, the stream range is `develop/<topic>...feature/<topic>`. Do not report `origin/main...HEAD`.

`develop/*` and `feature/*` in this layout are local unless the user says otherwise. Do not `git push` them unless asked.

## Integration worktrees

Implementation work does **not** land directly on the parent checkout.

1. The parent worktree is a `feature/<topic>` branch. That is the stack base, not `main` and not `develop/<topic>`.
2. For a discrete implementation task, follow [`.agents/skills/task-worktree/SKILL.md`](.agents/skills/task-worktree/SKILL.md): create a child worktree from that feature branch, do the work there, then stop for review.
3. Do **not** merge, rebase onto `main`/`master` or `develop/<topic>`, commit, push, or open a PR until the user has reviewed the working-tree diff and explicitly asked for the next git action.
4. Landing onto the feature branch is a single standard sequence. When the user asks to land / integrate / 統合 onto `feature/<topic>`:
   1. Bring the child's working-tree changes onto `feature/<topic>`.
   2. Make **one** commit there (`git merge --squash` after a single child commit, or apply the child's working tree and commit once on the parent).
   3. Remove the child worktree (`ORCA worktree rm` / `git worktree remove`). This deletion is part of the land, not an optional extra.
   4. Stop. Do not `git push`. Do not merge into `develop/<topic>` unless the user asked for that too.
5. Do not merge `feature/<topic>` into `develop/<topic>` unless the user asks. Never merge to `main`/`master` unless the user explicitly names that branch. If landing failed and the child still has uncommitted work, do not `worktree rm`.

Questions, read-only investigation, and tiny one-line answers stay in the parent worktree.

## Project

Terraform for [catatsuy/private-isu](https://github.com/catatsuy/private-isu) on AWS (`ap-northeast-1`). Run `terraform` in `terraform/`. Operational docs live in `docs/` (`010_common` / `040_practice`; files numbered 0010, 0020, …). Do not `terraform apply` / `destroy` unless the user asks.
