# 仲良しTube+ (Python / FastAPI 版)

複数の Invidious / Piped インスタンスへ**並列リクエスト**を投げ、最初に応答した結果を返す
「レース戦略」で最速に動画ストリーム URL を取得する FastAPI バックエンド + ミニ フロントエンド。

## エンドポイント

| Method | Path | 説明 |
|---|---|---|
| GET | `/api/video?id=VIDEO_ID` | ストリーム URL / HLS / DASH を取得 |
| GET | `/api/search?q=KEYWORD` | 動画検索 |
| GET | `/api/health` | ヘルスチェック |
| GET | `/docs` | Swagger UI |

## ローカル実行

```bash
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000
# http://localhost:8000/static/index.html ではなく
# http://localhost:8000/  (API root) + 別途 static/index.html を開く
```

## デプロイ

### 1. Render (推奨: バックエンド)
リポジトリを連携 → `render.yaml` を自動検出 → Deploy。
無料枠でも常時稼働可。完成 URL を GitHub Pages のフロントから叩く。

### 2. Vercel
`vercel --prod` または GitHub 連携 → `vercel.json` で `/api/*` を Python サーバレス、
`/` を `static/` 配信に振り分け。

### 3. CodeSandbox
Import repo → 自動で `pip install` & `uvicorn` 起動 (`sandbox.config.json`)。

### 4. GitHub Pages (フロントのみ)
`main` ブランチに push すると `.github/workflows/pages.yml` が `static/` を Pages にデプロイ。
画面初回に表示される入力欄で Render / Vercel の API URL を設定してください。

## 速度最適化のポイント

- `asyncio.wait(FIRST_COMPLETED)` による **race** — 7+ ミラーへ同時投擲し最速応答を採用
- 接続/読み取り別タイムアウト (3s / 6s)
- レスポンスに `Cache-Control` を付与しエッジ/ブラウザキャッシュを活用
- HTTP/2 を使わずコネクション確立を高速化（多ホスト並列のため）
