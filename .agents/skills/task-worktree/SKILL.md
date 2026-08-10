---
name: task-worktree
description: >-
  今の feature/<topic> ブランチから子の git / Orca worktree を切り、未コミットの変更として実装し、
  人のレビューで止まる。ユーザーが「子で Agent を動かす」「言語ごとに子タスク」などと明示したら、
  worktree 作成と同時にその中で別エージェントを起動する（親はコーディネータ）。
  ユーザーが明示しない限り git commit / git push しない。
  land を頼まれたら feature/<topic> へ 1 コミット（main でも develop/<topic> でもない）し、
  子 worktree を消す。この組が標準の land。ストリームの diff は
  develop/<topic>...feature/<topic> であり origin/main ではない。
  まとまった実装タスクの開始、worktree / 別ワークツリー / 子 worktree の依頼、
  子タスクとしてエージェントを並べる依頼、このリポジトリを新しいタスクで編集する前に使う。
  読み取り専用の質問には使わない。
---

# タスク用 worktree

ポリシーはリポジトリルートの `AGENTS.md`。この skill は手順。

## いつ使うか

まとまった実装タスク（コード、教材 Markdown、ツリーを変える設定）で使う。

質問、読み取り専用の調査、一行の確認は親 worktree のまま行う。

## Orca CLI を決める

このセッションが Orca の中なら、実行ファイルを一つ選んで使い続ける。シェル変数名 `ORCA` は作らない。

- 設定されていれば `ORCA_CLI_COMMAND`（Orca 管理下の WSL セッションでは `orca-ide`）
- それ以外で、Orca 管理外の Linux なら `orca-ide`
- それ以外は `orca`

管理外 Linux で素の `orca` は実行しない（GNOME のスクリーンリーダー）。選んだバイナリが動かなければ止めてエラーを報告する。

WSL では `orca-ide` の前に **WSLInterop** が要る。無い、または `orca-ide` / Windows の `*.exe` が `Exec format error`（exit 126）のときは Orca が壊れているのではない。ポリシーはルート `AGENTS.md` の「Orca CLI（WSL）」。**`/init` に黙って切り替えない。** 止めてユーザーに登録を促す（`sudo` はエージェントが実行しない）:

```text
echo ':WSLInterop:M::MZ::/init:PF' | sudo tee /proc/sys/fs/binfmt_misc/register
orca-ide status --json
```

登録後に `orca-ide status --json` を再実行する。ユーザーが今すぐ登録できないと明示したときだけ、同じ Windows の `orca.exe` を `/init <orca.exeのパス> -- <引数>` で呼ぶ。これも同じ Orca CLI であり、別製品へフォールバックするのではない。パスは `~/.local/bin/orca-ide` の `ORCA_WIN_LAUNCHER`（典型は `/mnt/c/Users/<user>/AppData/Local/Programs/orca/resources/bin/orca.exe`）。

次のコマンドを打つ前に、今のフラグを `ORCA skills get orca-cli` で読む。

Orca が使えなければ、同じ手順を `git worktree` で行う（親の `feature/<topic>` から子ブランチ）。

## 1. スタックの底を特定する

スタックの底は **親 worktree の今の `feature/<topic>`**。  
diff のストリーム底は対応する **`develop/<topic>`**。  
`main` / `master` / `origin/main` はどちらにも使わない。

```text
ORCA status --json
ORCA worktree current --json
```

または親チェックアウトで `git rev-parse --abbrev-ref HEAD`。

そのブランチが `main` または `master` なら止めて聞く。`develop/<topic>` なら、`feature/<topic>` に積むか聞いてからにする。`develop/<topic>` が無ければ止めて聞く。`origin/main` で代用しない。

## 2. そのブランチから子を切る

Orca の系統は親の上に積む。`--base-branch` に親の `feature/<topic>` を明示する。省略しない（Orca はリポジトリ既定、通常 `origin/main` を使う）。`--no-parent` は使わない。

### 2a. 親が子のパスで実装する（既定）

ユーザーが「別のエージェントを子で動かす」と言っていないとき。`--agent` は付けない。このセッションが子のパスで作業する。

```text
ORCA worktree create --name <task-slug> --parent-worktree active --base-branch <feature/<topic>> --json
```

JSON から worktree の完全な `id`（`<repoId>::<path>`）と、子のファイルシステムパス / 新しいブランチ名を控える。

### 2b. 子でエージェントを起動する（ユーザーが明示したとき）

次のような依頼では、worktree を切る**と同時に**その中で別エージェントを起動する。親はこのセッションのままコーディネータとして残る。自分で子パスに移って実装して終わったことにしない。

- 「各言語ごとに子タスク」
- 「worktree を作ってその中で Agent を動かす」
- 「子でエージェントを起動（grok / claude / codex / opencode などの指名があってもなくても）」
- 「並列にエージェントを並べる」

```text
ORCA worktree create --name <task-slug> --parent-worktree active --base-branch <feature/<topic>> --agent <current-agent> --prompt "<task brief>" --json
```

- `--agent` は現在起動中のエージェントの ID を指定する（例: `grok` / `claude` / `codex` / `opencode` などインストール済み TUI）。ユーザーが指名したらそれを使う。指名が無ければこのセッションと同じエージェントを使い、同一モデルが選べる場合は同一モデルにする。`grok` に固定しない。
- `--prompt` に作業内容を渡す。長文は親 worktree に指示ファイルを置き、prompt はその絶対パスを読ませる（CLI の長さ制限を避ける）。
- `--prompt` に進捗表示の指示を必ず含める。Orca の TUI 稼働検出は Claude Code / Gemini / Codex / OMP / Pi / Grok のみで、OpenCode などは対象外のため、稼働バッジが出ない Agent がある。Agent種別に依存しない表示として、子は作業の節目で `ORCA worktree set --worktree active --comment "<短い進捗>"` を実行し、完了時は `--workspace-status in-review` にする。
- 言語トラックや独立した成果物ごとに **1 worktree = 1 エージェント**。同じ子に二重にエージェントを足さない。
- 親はコーディネータとして残る。全件を full handoff にして監視を止めない（複数子の完了待ち・衝突確認が残る）。
- 子にも `AGENTS.md` が適用される。子はコミットしない。終わったら子を `in-review` にして人が diff を見る。

`--prompt` がコマンドラインに乗らないとき、または `--agent` が古い CLI で拒否されたときは、worktree だけ切り、その子で terminal にエージェントを起動して prompt を送る。

```text
ORCA worktree create --name <task-slug> --parent-worktree active --base-branch <feature/<topic>> --json
ORCA terminal create --worktree id:<repoId>::<childPath> --title <task-slug> --command <current-agent-command> --json
ORCA terminal wait --terminal <handle> --for tui-idle --timeout-ms 60000 --json
ORCA terminal send --terminal <handle> --text "<task brief>" --enter --json
```

Orca が使えないときの同等手段:

1. `git worktree add -b <task-slug> <sibling-dir> <feature/<topic>>`
2. そのパスを cwd にして、このセッションと同じエージェントのサブエージェントを起動する（Grok なら `spawn_subagent` の `cwd`。または `isolation: "worktree"` で子 worktree ごとエージェントを切る）。エージェントを `grok` に固定しない。
3. 親は編集せず、子の完了を待ってレビュー用の `git status` / `git diff` を示す。

git フォールバック（エージェントなし・親が実装）:

```text
git worktree add -b <task-slug> <sibling-dir> <feature/<topic>>
```

兄弟ディレクトリはこのリポジトリの隣（例: `../programming-tutorial-<task-slug>`）。

## 3. 子だけで実装する

編集はすべて子チェックアウトの **汚い作業ツリー** に行う。ポリシーは `AGENTS.md`（エージェントはコミットしない）。

- 親 worktree は編集しない。
- `git add` / `git commit` / `git commit --amend` しない。
- merge しない。
- `git push` しない。
- `main` / `master` や `develop/<topic>` への PR を開かない。

## 4. レビューのために止まる

子の作業が終わったら:

```text
ORCA worktree set --worktree id:<repoId>::<childPath> --workspace-status in-review --json
```

それから止まる。ユーザーに示すもの:

- 子 worktree のパスとブランチ
- スタックの底（`feature/<topic>`）とストリームの底（`develop/<topic>`）
- 何が変わったかの要約
- **子の中で**見る方法: `git status` と `git diff`（作業ツリー対 `HEAD`）
- feature ブランチに乗ったあとのストリーム範囲は `git diff develop/<topic>...feature/<topic>`（`origin/main` ではない）

この段階では commit / merge / push しない。

## 5. ユーザーが feature ブランチへ land すると言ったあと（標準）

レビュー承認だけでは足りない。ユーザーが feature ブランチへ commit / land / 統合すると言ったら、この順で行う。子 worktree の削除も含む。削除だけを頼む別メッセージは待たない。

1. 子の作業ツリー変更を `feature/<topic>` へ載せる。親で **1** コミットにする。先に子でコミットするよう頼まれたら、子で **1** コミットし、親で `git merge --squash <child-branch>` して 1 コミット。
2. merge 先は `feature/<topic>`。ユーザーがそのブランチ名を出さない限り `develop/<topic>` や `main` / `master` へ merge しない。
3. そのコミットが `feature/<topic>` に乗ったら子 worktree を消す:

```text
ORCA worktree rm --worktree id:<repoId>::<childPath> --json
```

git フォールバック: `git worktree remove <child-path>`。

4. **止まる**。ユーザーが push を頼まない限り `git push` も `--force` もしない。`git status` とコミットハッシュを伝える。

land に失敗して子に未コミット作業が残っているなら `worktree rm` しない。merge や rebase がコンフリクトしたら親で止めて報告する。

2b の子（中でエージェントを起動した子）を閉じるときは、次の「6. 子タスクをクローズする」に従う（Agent停止のため `terminal close --all` が要る）。

## 6. 子タスクをクローズする（ユーザーが明示したとき）

2b で切った子を、親の現ブランチ（通常 `feature/<topic>`）にマージして閉じる。レビュー承認だけでは足りない。「マージして」「クローズして」とユーザーが明示したら、この順で行う。**マージ成功が削除の条件**。失敗したら止めて報告し、先に進まない。

1. 子の完了確認: `workspace-status` が `in-review` で、terminal read に完了報告があること。未完なら閉じない。
2. 子チェックアウトで **1** コミットにまとめる（close指示がそのまま明示のコミット許可。`git add` 対象は子の成果のみ）。
3. 親の現ブランチで取り込み、**1** コミットにする:

```text
git merge --squash <child-branch>
git commit -m "<topic>: <what>"
```

merge 先は親の現ブランチ。ユーザーが別ブランチ名を出さない限り `develop/<topic>` や `main` / `master` に merge しない。
4. マージ成功を確認してから次へ: コミットが親ブランチに乗ったことと、`git diff <child-branch> HEAD -- <対象パス>` に残差がないこと。コンフリクト・失敗時はここで止めて報告する。`terminal close` も `worktree rm` もしない。
5. 子の Agent を止める（`worktree rm` だけでは子のターミナルが残ることがあるため、先に止める）:

```text
ORCA terminal close --worktree id:<repoId>::<childPath> --all --json
```

6. 子 worktree を消す:

```text
ORCA worktree rm --worktree id:<repoId>::<childPath> --json
```

git フォールバック: `git worktree remove <child-path>`。

`--squash` 取り込みでは git 上ブランチが残ることがある（Orca はマージ済みと証明できないブランチを保持する）。残ったら `git branch -d <child-branch>` を試す。`-d` が拒否したら無理に `-D` せず報告する。
7. **止まる**。ユーザーが push を頼まない限り `git push` も `--force` もしない。ユーザーが頼まない限り `develop/<topic>` へも merge しない。`git status` とコミットハッシュを伝える。
