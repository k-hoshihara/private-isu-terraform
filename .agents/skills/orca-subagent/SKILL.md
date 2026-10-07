---
name: orca-subagent
description: >-
  サブタスク（子エージェント）の委譲は Orca CLI の orchestration 経由で行う。
  worktree は作らず、親と同じブランチの作業ツリーをそのまま使う。
  プラットフォームの subagent 機能（バックグラウンド実行）はこのリポジトリでは使わない。
  「サブタスク」「子エージェント並列」「別エージェントで回して」「orca 経由で委譲」と言われたら使う。
  /orca-subagent
---

# orca-subagent — Orca CLI 経由のサブタスク委譲

このリポジトリのサブタスクは **worktree を切らず、親と同じブランチの作業ツリーをそのまま共有**して行う。別エージェントの起動・並行作業・完了待ちを **Orca CLI の orchestration** で扱う。プラットフォーム備え付けの subagent 機能（バックグラウンド実行）は使わない。Git・レビュー・land の規約は `AGENTS.md` に従う。この skill は委譲経路だけを定める。

## いつ使うか

- ユーザーの現メッセージに「サブタスク」「子エージェント並列」「別エージェントで回して」「orca 経由で委譲」のいずれか、または別エージェントで走らせたい旨の明示があるとき。
- 合図がない実装タスク・質問・読み取り専用の調査は委譲せず、親のまま行う。

## worktree は作らない

- 親の作業ツリー（`feature/<topic>`）をそのまま使う。子用に `worktree create` も `ORCA orchestration ... --worktree new-child` も使わない。
- ワーカーの指定は `--worktree branch:<feature/<topic>>` のような**既存セレクタ**で行う。書式は版数一致ガイドで確認する。
- ユーザーが「worktree を切って」と明示したときだけ、[task-worktree](../task-worktree/SKILL.md) の手順で子 worktree を作る。

## Orca CLI を決める

解決手順は [task-worktree](../task-worktree/SKILL.md) の「Orca CLI を決める」節に従う。要点のみ:

- `ORCA_CLI_COMMAND` があればそれを使う。
- macOS（`uname -s` が `Darwin`）は `orca`。`orca-ide` は使わない。
- Linux / WSL は `orca-ide`。素の `orca` は GNOME のスクリーンリーダーに当たるため実行しない。

以下 `ORCA` は選んだ実行ファイルに置き換える。シェル変数は作らない。

## 版数一致ガイドを読む

委譲の前に必ず読む。フラグは推測しない（版で変わる）:

```text
ORCA skills get orchestration
```

## 標準ループ（同一ブランチ・worktree なし）

```text
ORCA status --json
ORCA orchestration run-create --objective "<目的>" --json
ORCA orchestration worker-start --spec "<作業内容>" --worktree branch:<feature/<topic>> --agent opencode --json
ORCA orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json
```

- `--worktree` には **既存セレクタ**（`branch:<feature/<topic>>`）を渡す。`new-child` は使わない（新しい作業ツリーを作るため使わない）。
- セレクタの正確な書式は版数一致ガイドで確認する。渡すブランチは親の現ブランチ（`git rev-parse --abbrev-ref HEAD`）に合わせる。
- 完了待ちは `check --wait` で行う。`worker_done` を検証し、settled terminal は `worker-release`（残すなら `worker-retain`）してから ack する。
- `--agent` は稼働サーバーで有効な ID（`opencode`、`claude`、`codex` 等）。`--help` で確認する。
- 複数ワーカーを同じ作業ツリーで走らせるときは、担当パスが重ならないように `--spec` を分ける。重なるなら逐次起動にする。

## このリポジトリの作業契約（子への指示に含める）

- 作業先は親の `feature/<topic>` と同じ作業ツリー。同じツリーに書くため、担当パスを明示して渡す。
- 対応する `develop/<topic>` を diff 基準にする（`origin/main` は使わない）。
- 子は未コミットのまま作業し、人のレビューで止まる。`git add` / `commit` / `push` はユーザーが明示したときのみ。
- `worker_done` の要約には `git status` とストリーム範囲 `develop/<topic>...feature/<topic>` の要点を入れる。
- land は `AGENTS.md` の標準手順（feature へ 1 コミット）で行い、この skill では行わない。worktree を明示して作った子がある場合はその削除も行う。
