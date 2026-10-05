# エージェント指示

このリポジトリのエージェント用ファイルはポータブルにする:常時有効な `AGENTS.md` と、必要時に読む `.agents/skills/`。ツール固有の複製は作らない（`.grok/` や `.opencode/` のコピー禁止）。Claude Code 用の橋渡しは symlink のみ（`CLAUDE.md` → `AGENTS.md`、`.claude/skills` → `.agents/skills`）。実体の複製は置かない。

オンデマンドの skill 一覧:

- `orca-subagent` — サブタスクの委譲手順（同一ブランチのまま Orca CLI で別エージェントを起動する。worktree は作らない）
- `task-worktree` — git worktree と land の規約。worktree を使うのはユーザーが明示したときだけ
- `docs-preview` — Markdown を docsify でプレビューする手順

## サブタスク: worktree は作らず、同一ブランチで Orca 経由にする

**worktree は作らない。** サブタスク（子エージェント）が要るときは、親と同じブランチの作業ツリーをそのまま使い、Orca CLI で別エージェントを起動する。

1. worktree を切るのは、ユーザーが現メッセージで明示したときだけ。既定は同一ブランチで Orca 経由です。worktree を分ける標準手順は [`.agents/skills/task-worktree/SKILL.md`](.agents/skills/task-worktree/SKILL.md) にあります
2. サブタスクが要ると分かった場合は、同一ブランチのまま [`.agents/skills/orca-subagent/SKILL.md`](.agents/skills/orca-subagent/SKILL.md) に従って Orca CLI の orchestration で別エージェントを起動する
3. 親と子は**同じブランチ・同じ作業ツリー**で作業する。競合しないよう担当範囲（対象パス）を分割して渡す。書き込むパスが重なるなら分けたまま逐次実行にする
4. プラットフォーム備え付けの subagent 機能（バックグラウンド実行）は使わない
5. 起動の合図がない実装タスク・質問・読み取り専用の調査は、委譲せず親のまま行う

## Git: エージェントはコミットしない

このリポジトリで動くすべてのエージェント、サブエージェントが対象。

1. ファイル変更のみ行い、**未コミットのまま残す**（`git add` / `git commit` / `git commit --amend` / `git push` / `--force` は、ユーザーが現メッセージでその操作を明示的に指示した場合のみ可）。
2. 人のレビューで止まる。`git status` と `git diff`（作業ツリー対 `HEAD`）を示す。コミット範囲では示さない。
3. 「よさそう」やレビュー承認はコミットの許可ではない。「コミットして」と言われたときのみコミット（その場合は**1コミット**、下書きが複数あれば squash）。「push して」と言われたときのみ push。
4. ユーザーが明示して作った子 worktree に未コミット作業が残っているなら消さない（`worktree rm` で消える）。

## ブランチ

3つの役割。名前は現ストリームに従う。トピックを決め打ちしない。

| 役割 | 典型名 | 意味 |
| --- | --- | --- |
| Upstream | `origin/main` | 公開の既定。ユーザーが `main` を名指ししない限り、rebase 先・merge 先・PR 先・diff 基準に使わない |
| Develop | `develop/<topic>` | 1ストリームのローカル統合先。フィーチャーストリームの diff はこのブランチ基準 |
| Feature | `feature/<topic>` | そのストリームのローカル作業先。親チェックアウト。サブタスクのワーカーもここ共用する |

`<topic>` は親 feature ブランチの接尾辞（例: `feature/foo` には `develop/foo`）。対応する `develop/<topic>` が無ければ止めて聞く。`origin/main` で代用しない。

ストリームの比較はこうする:

```text
git diff develop/<topic>...feature/<topic>
git log --oneline develop/<topic>..feature/<topic>
```

レビュー中は作業ツリー対 `HEAD`（`git status` / `git diff`）で見る。作業が feature ブランチに乗った後はストリーム範囲 `develop/<topic>...feature/<topic>` で見る。`origin/main...HEAD` で報告しない。同一ブランチのワーカーは同じツリーに書くので、確認する `./` はワーカーの担当パスに絞る。

`develop/*` と `feature/*` は、ユーザーが言うまではローカル扱い。言われるまで `git push` しない。

## 統合作業ツリー

実装作業は `feature/<topic>` ブランチの作業ツリーで行う。サブタスクを起動しても作業ツリーは増やさない。

1. 作業するブランチは `feature/<topic>`。そのスタックの土台であり、`main` でも `develop/<topic>` でもない。
2. まとまった実装タスクは、既定ならこの作業ツリーで実装する。サブタスクを分けるなら [`.agents/skills/orca-subagent/SKILL.md`](.agents/skills/orca-subagent/SKILL.md) に従って Orca CLI で同一ブランチのまま別エージェントを起動する。ユーザーが worktree を明示して求めたときだけ [`.agents/skills/task-worktree/SKILL.md`](.agents/skills/task-worktree/SKILL.md) を使い、子 worktree を切る。
3. 親と子が同じ作業ツリーに書くときは、担当する対象パスが重ならないように分ける。重なるなら同時ではなく逐次で走らせる。
4. ユーザーが作業ツリーの diff をレビューし、次の git 操作を明示するまで、merge、`main` / `master` や `develop/<topic>` への rebase、commit、push、PR をしない。
5. feature ブランチへの land は単一の標準手順。ユーザーが `feature/<topic>` へ land / integrate / 統合と言うとき:
   1. 作業ツリーの変更を `feature/<topic>` に 1 コミットで載せる。
   2. ユーザーが worktree を明示していた場合は、その子 worktree も消す（`ORCA worktree rm` / `git worktree remove`）。
   3. 止まる。`git push` しない。ユーザーが一緒に頼まない限り `develop/<topic>` へ merge しない。
6. ユーザーが頼まない限り `feature/<topic>` を `develop/<topic>` へ merge しない。ユーザーがそのブランチを明示しない限り `main` / `master` へは絶対に merge しない。

質問、読み取り専用の調査、ささいな一行回答は委譲せず、この作業ツリーでそのまま行う。

## プロジェクト

AWS（`ap-northeast-1`）上の [catatsuy/private-isu](https://github.com/catatsuy/private-isu) 用 Terraform。`terraform` は `terraform/` で実行する。運用ドキュメントは `docs/`（`010_common` / `040_practice`、0010、0020、…の番号順）。ユーザーが言うまで `terraform apply` / `destroy` しない。
