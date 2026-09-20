---
name: task-worktree
description: >-
  今の feature/<topic> ブランチから子の git / Orca worktree を切り、
  その中で別エージェントを foreground task セッションとして起動する（既定。親はコーディネータ）。
  子は未コミットの変更として実装し、人のレビューで止まる。
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

まず OS を判定する（WSL 判定は `uname -r` と環境変数の両方を見る。どちらか片方だけでは誤判定する）：

```sh
uname -s # Darwin なら macOS、Linux なら Linux / WSL
uname -r | grep -qi microsoft && echo WSL || true
echo "${WSL_DISTRO_NAME:-} ${WSL_INTEROP:-}"
```

- 設定されていれば `ORCA_CLI_COMMAND` を使う（Orca 管理下の WSL セッションでは `orca-ide`。macOS の Orca 管理下では Orca が配る値をそのまま使う）。
- 未設定の場合:
  - macOS（`uname -s` が `Darwin`）: `orca`。`orca-ide` は使わない。
  - WSL（`uname -r` に `microsoft` を含む、または `$WSL_DISTRO_NAME` / `$WSL_INTEROP` がある）: `orca-ide`。
  - それ以外の Linux（Orca 管理外を含む）: `orca-ide`。
- 素の `orca` を GNOME のスクリーンリーダーとして実行しない注意は **Linux の GNOME 環境だけ** の話。macOS では気にしない。

選んだバイナリが動かなければ止めてエラーを報告する。

### WSL のときだけ: WSLInterop

macOS ではこの節全体を飛ばす。`/proc/sys/fs/binfmt_misc/register` も `/init` も `ORCA_WIN_LAUNCHER`（`/mnt/c/...`）も WSL 専用であり、macOS には存在しない。

WSL では `orca-ide` の前に **WSLInterop** が要る。無い、または `orca-ide` / Windows の `*.exe` が `Exec format error`（exit 126）のときは Orca が壊れているのではない。**`/init` に黙って切り替えない。** 止めてユーザーに登録を促す（`sudo` はエージェントが実行しない）:

```text
echo ':WSLInterop:M::MZ::/init:PF' | sudo tee /proc/sys/fs/binfmt_misc/register
orca-ide status --json
```

登録後に `orca-ide status --json` を再実行する。ユーザーが今すぐ登録できないと明示したときだけ、同じ Windows の `orca.exe` を `/init <orca.exeのパス> -- <引数>` で呼ぶ。これも同じ Orca CLI であり、別製品へフォールバックするのではない。パスは `~/.local/bin/orca-ide` の `ORCA_WIN_LAUNCHER`（典型は `/mnt/c/Users/<user>/AppData/Local/Programs/orca/resources/bin/orca.exe`）。

次のコマンドを打つ前に、今のフラグを `ORCA skills get orca-cli` で読む。

### 子の作成は Orca CLI 経由のみ（`git worktree add` 禁止）

子の worktree は必ず `ORCA worktree create` で切る。`git worktree add` で切ったものは Orca に表示されず、CLI に後から取り込む手段（import）もない。Orca CLI が使えないときは子を作らず止めて、CLI の復旧（WSL なら WSLInterop 節）を優先する。

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

### 2a. 子でエージェントを起動する（既定）

まとまった実装タスクでは、worktree を切る**と同時に**その中で別エージェントを起動する。
親はこのセッションのままコーディネータとして残る。自分で子パスに移って実装して終わったことにしない。

```text
ORCA worktree create --name <task-slug> --parent-worktree active --base-branch <feature/<topic>> --agent <current-agent> --prompt "<task brief>" --json
```

- `--agent` は現在起動中のエージェントの ID を指定する（例: `grok` / `claude` / `codex` / `opencode` などインストール済み TUI）。ユーザーが指名したらそれを使う。指名が無ければこのセッションと同じエージェントを使い、同一モデルが選べる場合は同一モデルにする。`grok` に固定しない。
- `--prompt` に作業内容を渡す。長文は親 worktree に指示ファイルを置き、prompt はその絶対パスを読ませる（CLI の長さ制限を避ける）。
- `--prompt` に進捗表示の指示を必ず含める。子は作業の節目で `ORCA worktree set --worktree active --comment "<短い進捗>"` を実行し、完了時は `--workspace-status in-review` にする。
- 言語トラックや独立した成果物ごとに **1 worktree = 1 エージェント**。同じ子に二重にエージェントを足さない（役割別の複数は 2c）。
- 親はコーディネータとして残る。全件を full handoff にして監視を止めない（複数子の完了待ち・衝突確認が残る）。
- 子にも `AGENTS.md` が適用される。子はコミットしない。終わったら子を `in-review` にして人が diff を見る。
- 子の OpenCode が選択要求（permission / question）で Orca 通知を鳴らす条件は「通知の設定」にまとめた。
  起動前に 1 回だけ確認し、欠けていたら直してから子を立てる。

`--prompt` がコマンドラインに乗らないとき、または `--agent` が古い CLI で拒否されたときは、worktree だけ切り、その子で terminal にエージェントを起動して prompt を送る。

```text
ORCA worktree create --name <task-slug> --parent-worktree active --base-branch <feature/<topic>> --json
ORCA terminal create --worktree id:<repoId>::<childPath> --title <task-slug> --command <current-agent-command> --json
ORCA terminal wait --terminal <handle> --for tui-idle --timeout-ms 60000 --json
ORCA terminal send --terminal <handle> --text "<task brief>" --enter --json
```

### 2b. 親が子のパスで実装する（軽微なときのみ）

質問・読み取り専用の調査・一行の確認に準ずる軽微さで、別エージェントを立てるほどでもないときだけ `--agent` を付けず、このセッションが子のパスで作業する。
まとまった実装タスクの既定は 2a であり、こちらは例外である。

Orca CLI が使えないことは 2b の理由にならない（`git worktree add` で代替作成しない。CLI 復旧が先）。

```text
ORCA worktree create --name <task-slug> --parent-worktree active --base-branch <feature/<topic>> --json
```

JSON から worktree の完全な `id`（`<repoId>::<path>`）と、子のファイルシステムパス / 新しいブランチ名を控える。

### 2c. 子のエージェントは worktree に紐づく foreground task セッションとして起動する

子でエージェントを動かすときは、このセッションのバックグラウンドタスクにせず、Orca に表示される foreground task セッションとして子の worktree に紐づけて起動する。既存の子に足す場合は `terminal create` を使う（worktree 作成と同時でよい場合の `--agent` / `--prompt` でもよいが、役割別に複数立てる・後から足す場合はこちら）。

```text
ORCA terminal create --worktree path:<childPath> --title <role-slug> --command <current-agent-command> --json
ORCA terminal wait --terminal <handle> --for tui-idle --timeout-ms 120000 --json
ORCA terminal send --terminal <handle> --text "<task brief>" --enter --json
```

- `<current-agent-command>` はこのセッションと同じエージェントの起動コマンド（フルパス推奨）。エージェント種別を固定しない。
- `--title` は役割が分かる名前にする（例: `review-<track>` / `fix-<track>`）。同じ子に同じ役割を二重に立てない。
- prompt には作業ディレクトリ・対象パス・制約（そのトラック配下のみ編集・コミット禁止・`sudo` 禁止）・進捗表示指示（節目で `ORCA worktree set --worktree path:<childPath> --comment "<短い進捗>"`、完了時 `--workspace-status in-review`）を必ず含める。
- レビュー担当と修正担当のように役割を分ける場合、受け渡しはリポジトリ外（例: `/tmp/opencode/review-<slug>.txt`）のファイル経由にし、リポジトリ内を汚さない。修正担当は結果ファイルが無ければ `sleep` で待って再確認する。
- バックグラウンドの session task として動かさない。Orca の TUI 上で子の稼働が見えることが起動の条件。

`git worktree add` での代替作成はしない。Orca に表示されず、後から取り込む手段もないため。

## 通知の設定（子の選択要求を Orca 通知にする。初回のみ設定、以後は確認だけ）

OpenCode の子が permission / question で選択を求めても、Orca 側に状態が届かなければ通知は鳴らない。
次の 3 点がそろうと、子の「要応答」が Orca の Needs You・通知・Agent Dashboard に出る。

1. OpenCode の attention（`~/.config/opencode/tui.json`）:

```json
{ "$schema": "https://opencode.ai/tui.json", "attention": { "enabled": true, "notifications": true, "sound": true } }
```

`enabled` は既定 `false` のため、無ければ通知も音も出ない。変更後は OpenCode の再起動が要る。
通知は端末が blur しているときに出る。音だけなら `notifications` を切ってもよい。

2. Orca の Agent status hooks（Orca 管理の `orca-opencode-status.js` が子の OpenCode に状態を報告する）:
   - Orca の Settings → Agents → Agent status hooks が on であること。
   - 子の端末内で `echo $OPENCODE_CONFIG_DIR` が空でなく、`ls $OPENCODE_CONFIG_DIR/plugins` に
     `orca-opencode-status.js` があること。無ければ Orca 側の導入が落ちている（`ORCA agent hooks status --json` で確認し、設定を見直す）。
3. Orca の通知先（Settings → Notifications で system / sound / chip-only の分類と音量。携帯で受けるなら Mobile companion）。

2a で子を立てる前の確認: 上の 1〜2 を 1 回だけ確かめ、欠けていたら直してから子を立てる。
子が既に動いている状態での後付けはしない（OpenCode は起動時 config を読むため、直したら子を立て直す）。

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

2a・2c の子（中でエージェントを起動した子）を閉じるときは、次の「6. 子タスクをクローズする」に従う（Agent停止のため `terminal close --all` が要る）。2b（親が実装した子）に Agent はいない。

## 6. 子タスクをクローズする（ユーザーが明示したとき）

2a・2c で切った子を、親の現ブランチ（通常 `feature/<topic>`）にマージして閉じる。レビュー承認だけでは足りない。「マージして」「クローズして」とユーザーが明示したら、この順で行う。**マージ成功が削除の条件**。失敗したら止めて報告し、先に進まない。

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
