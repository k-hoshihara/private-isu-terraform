# init-env（Claude Code Pro / OpenCode Go 切り替え・Proxy経由版）

Claude CodeからOpenCode Goのモデルを使うためのProxy経由の手順です。直接接続版（Anthropic互換の6モデルのみ）ではなく、Proxyでプロトコル変換するためGoの全モデル帯を使えます。

構成：

```text
Claude Code --(Anthropic Messages)--> localhost:3456 [routatic-proxy] --(OpenAI互換/Anthropic互換)--> OpenCode Go
```

## 前提

| 項目 | 内容 |
| --- | --- |
| Claude Code | Pro契約のログイン済み（Pro側で使う） |
| OpenCode Go | 契約済みでAPIキー取得済み（[opencode.ai/auth](https://opencode.ai/auth)） |
| Proxy | [routatic/proxy](https://github.com/routatic/proxy)（旧 `oc-go-cc` の後継。`oc-go-cc` は互換エイリアス） |

`routatic-proxy` が無い場合の導入（WSL/Ubuntu想定。全文は[INSTALLATION.md](https://github.com/routatic/proxy/blob/main/INSTALLATION.md)）：

```bash
# release binary（Linux x86_64の例。最新は Releases ページで確認）
curl -L -o routatic-proxy https://github.com/routatic/proxy/releases/latest/download/routatic-proxy_linux-amd64
chmod +x routatic-proxy
sudo mv routatic-proxy /usr/local/bin/
routatic-proxy --version
```

Homebrewがある環境では `brew tap routatic/tap && brew install routatic-proxy` でもよい。Dockerでも可（`ghcr.io/routatic/proxy:latest` を `-p 3456:3456` で起動）。

## 1. Proxyの初期設定

```bash
routatic-proxy init
routatic-proxy validate
```

`init` が既定のconfigを生成する。生成先は `~/.config/routatic-proxy/config.json`（`ROUTATIC_PROXY_CONFIG` で変更可。旧 `~/.config/oc-go-cc/config.json` は新ファイルが無い場合の移行用に読まれる）。

APIキーはconfig直書きではなく環境変数で渡す。config内の `"api_key": "${ROUTATIC_PROXY_API_KEY}"` の形で参照される（旧 `OC_GO_CC_API_KEY` も互換）：

```bash
export ROUTATIC_PROXY_API_KEY='sk-opencode-xxx'   # Goのキーに置き換える
```

キーの疎通確認：

```bash
curl -H "Authorization: Bearer $ROUTATIC_PROXY_API_KEY" \
  https://opencode.ai/zen/go/v1/models | head -c 500
```

JSONのモデル一覧が返ればキー正常。401ならキー誤り・失効なのでダッシュボードで再発行する。

## 2. Proxyを起動する

```bash
routatic-proxy serve -b
routatic-proxy status
```

- 初回はフォアグラウンドの `routatic-proxy serve` で `listening on 127.0.0.1:3456` を目視確認してから `-b` にするとよい。
- 停止は `routatic-proxy stop`。利用可能モデルは `routatic-proxy models`。
- 自動起動したい場合：macOSは `routatic-proxy autostart enable`、Linuxはsystemd/Dockerの `--restart unless-stopped` を使う。

モデル割り当て（default/think/long_context/background、fallback）はconfigで変える。詳細は[CONFIGURATION.md](https://github.com/routatic/proxy/blob/main/CONFIGURATION.md)。

## 3. Pro / Go を切り替える

切り替えはシェルの環境変数で行う。`settings.json` の `env` に `ANTHROPIC_BASE_URL` を書くと張り付くため、切り替え運用では `~/.claude/settings.json` に `ANTHROPIC_BASE_URL` を書かないこと。`~/.claude/settings.json` に既に書いてある場合は消してClaude Codeを再起動する。

`~/.bashrc`（zshなら `~/.zshrc`）に以下を追記する：

```bash
# Claude Code Pro（既定のAnthropic直結）に戻す
claude-pro() {
  unset ANTHROPIC_BASE_URL ANTHROPIC_API_KEY
  unset ANTHROPIC_AUTH_TOKEN
  unset ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL
  unset CLAUDE_CODE_SUBAGENT_MODEL
  echo "mode=claude-pro (direct)"
}

# OpenCode Go（Proxy経由）にする。Proxyは別途 routatic-proxy serve -b で起動しておく
claude-go() {
  unset ANTHROPIC_API_KEY
  export ANTHROPIC_BASE_URL='http://127.0.0.1:3456'
  export ANTHROPIC_AUTH_TOKEN='unused'   # Proxyが無視する。空でなければ何でもよい
  echo "mode=opencode-go (via proxy)"
}
```

反映と起動：

```bash
source ~/.bashrc
routatic-proxy status   # Goを使う前にproxyが起動していること
claude-go
claude                  # 別ターミナルでもよい
```

Proに戻すとき：

```bash
claude-pro
claude
```

注意：

- `ANTHROPIC_API_KEY` が残っているとバージョンにより `ANTHROPIC_AUTH_TOKEN` より優先され、誤ったキーで401ループになる。`claude-go` は先に `unset` する。
- `ANTHROPIC_BASE_URL` はProxyのアドレス（`127.0.0.1:3456`）であり、OpenCodeのURLではない。CLIはProxyとだけ話す。

## 4. 動作確認

```bash
echo "$ANTHROPIC_BASE_URL"   # Go時: http://127.0.0.1:3456、Pro時: 空
routatic-proxy status
curl -s http://127.0.0.1:3456/health
```

Claude Codeで `hello` と送り、応答が返れば疎通OK。ファイル読み（background経路）と `think through a refactor` のような推論要求（think経路）で振り分けを確認する。

## 5. 失敗時の見分け方

| 症状 | 原因 | 対処 |
| --- | --- | --- |
| 即座に401 | 向き先が違う（旧URL・本家AnthropicにGoキーで接続等）かキー誤り | `echo $ANTHROPIC_BASE_URL`、shell rcの再読み込み |
| 接続拒否→リトライを繰り返す | Proxyが起動していない | `routatic-proxy status` → `routatic-proxy serve -b`（Dockerなら `docker start`） |
| `all models failed` | Proxyは到達したが上流が拒否（キー・利用枠） | 上記 `curl .../zen/go/v1/models` でキーを確認、利用枠を確認 |

詳細ログは `ROUTATIC_PROXY_LOG_LEVEL=debug routatic-proxy serve` で、生リクエスト・変換後・上流応答が出る。全文書は[TROUBLESHOOTING.md](https://github.com/routatic/proxy/blob/main/TROUBLESHOOTING.md)。

## 6. 別方式：Pro優先・Go自動フォールバック

手動切り替えの代わりに、Proxyの `anthropic_first` モードで「普段はPro、429/5xx等の障害時だけGo chainへ自動退避」もできる：

```json
{ "anthropic_first": { "enabled": true, "base_url": "https://api.anthropic.com" } }
```

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:3456
unset ANTHROPIC_AUTH_TOKEN ANTHROPIC_API_KEY   # 保存済みProログインを残すため空ける
```

400/401/403/404はそのまま返し、408・429・5xx・転送失敗のときだけ退避する。詳細は[CONFIGURATION.md](https://github.com/routatic/proxy/blob/main/CONFIGURATION.md) の Anthropic-First Failover節。

## 備考

- 直接接続版（Proxyなし）はAnthropic互換endpointの6モデルのみ（MiniMax M3/M2.7、Qwen3.8 Max、Qwen3.7 Max/Plus、Qwen3.6 Plus）。GLM/Kimi/DeepSeek/Grok/GPT系をClaude Codeで使うには本手順のProxyが必要。
- Orcaのterminalから使う場合も同じ関数を使う。Orcaが開くshellが `~/.bashrc` / `~/.zshrc` を読むことを確認してから `claude-go` / `claude-pro` する。
- APIキーはgitに入れない。`terraform.tfvars` と同様、秘密情報は環境変数かgit対象外ファイルに置く。
