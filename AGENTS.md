# エージェント指示

このリポジトリのエージェント用ファイルはポータブルにする:常時有効な `AGENTS.md` と、必要時に読む `.agents/skills/`。ツール固有の複製は作らない（`.grok/` や `.opencode/` のコピー禁止）。Claude Code 用の橋渡しは symlink のみ（`CLAUDE.md` → `AGENTS.md`、`.claude/skills` → `.agents/skills`）。実体の複製は置かない。

オンデマンドの skill 一覧:

- `task-worktree` — 子 worktree を切って別エージェントで実装する手順
- `docs-preview` — Markdown を docsify でプレビューする手順

## Git: エージェントはコミットしない

このリポジトリで動くすべてのエージェント、サブエージェント、git / Orca worktree ワーカーが対象。

1. ファイル変更のみ行い、**未コミットのまま残す**（`git add` / `git commit` / `git commit --amend` / `git push` / `--force` は、ユーザーが現メッセージでその操作を明示的に指示した場合のみ可）。
2. 人のレビューで止まる。`git status` と `git diff`（作業ツリー対 `HEAD`）を示す。コミット範囲では示さない。
3. 「よさそう」やレビュー承認はコミットの許可ではない。「コミットして」と言われたときのみコミット（その場合は**1コミット**、下書きが複数あれば squash）。「push して」と言われたときのみ push。
4. 未コミット作業が残る子 worktree を消さない（`worktree rm` で消えてしまう）。`feature/<topic>` への land 成功後は、子 worktree の削除が**標準の次手順**（下記参照）。削除だけを頼む2通目のメッセージを待たない。

## ブランチ

3つの役割。名前は現ストリームに従う。トピックを決め打ちしない。

| 役割 | 典型名 | 意味 |
| --- | --- | --- |
| Upstream | `origin/main` | 公開の既定。ユーザーが `main` を名指ししない限り、rebase 先・merge 先・PR 先・diff 基準に使わない |
| Develop | `develop/<topic>` | 1ストリームのローカル統合先。フィーチャーストリームの diff はこのブランチ基準 |
| Feature | `feature/<topic>` | そのストリームのローカル作業先。親チェックアウト。子 worktree はここに積む |

`<topic>` は親 feature ブランチの接尾辞（例: `feature/foo` には `develop/foo`）。対応する `develop/<topic>` が無ければ止めて聞く。`origin/main` で代用しない。

ストリームの比較はこうする:

```text
git diff develop/<topic>...feature/<topic>
git log --oneline develop/<topic>..feature/<topic>
```

レビュー中の子は子の作業ツリー対 `HEAD`（`git status` / `git diff`）で見る。作業が feature ブランチに乗った後はストリーム範囲 `develop/<topic>...feature/<topic>` で見る。`origin/main...HEAD` で報告しない。

`develop/*` と `feature/*` は、ユーザーが言うまではローカル扱い。言われるまで `git push` しない。

## 統合作業ツリー

実装作業は親チェックアウトに直接載せない。

1. 親 worktree は `feature/<topic>` ブランチ。そのスタックの土台であり、`main` でも `develop/<topic>` でもない。
2. まとまった実装タスクは [`.agents/skills/task-worktree/SKILL.md`](.agents/skills/task-worktree/SKILL.md) に従う:その feature ブランチから子 worktree を切り、作業して人のレビューで止まる。
3. ユーザーが作業ツリーの diff をレビューし、次の git 操作を明示するまで、merge、`main` / `master` や `develop/<topic>` への rebase、commit、push、PR をしない。
4. feature ブランチへの land は単一の標準手順。ユーザーが `feature/<topic>` へ land / integrate / 統合と言うとき:
   1. 子の作業ツリー変更を `feature/<topic>` へ載せる。
   2. そこで **1** コミットにする（子でコミット済みが1つなら `git merge --squash`、子の作業ツリーをそのまま載せるなら親で1コミット）。
   3. 子 worktree を消す（`ORCA worktree rm` / `git worktree remove`）。この削除は land の一部であり、任意のおまけではない。
   4. 止まる。`git push` しない。ユーザーが一緒に頼まない限り `develop/<topic>` へ merge しない。
5. ユーザーが頼まない限り `feature/<topic>` を `develop/<topic>` へ merge しない。ユーザーがそのブランチを明示しない限り `main` / `master` へは絶対に merge しない。land に失敗して子に未コミット作業が残っているなら `worktree rm` しない。

質問、読み取り専用の調査、ささいな一行回答は親 worktree のまま行う。

## プロジェクト

AWS（`ap-northeast-1`）上の [catatsuy/private-isu](https://github.com/catatsuy/private-isu) 用 Terraform。`terraform` は `terraform/` で実行する。運用ドキュメントは `docs/`（`010_common` / `040_practice`、0010、0020、…の番号順）。ユーザーが言うまで `terraform apply` / `destroy` しない。
