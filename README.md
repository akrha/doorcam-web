# camera-web

ローカルネットワーク内で USB カメラ映像をブラウザ表示するための、Docker Compose ベースの小規模配信システムです。  
`ffmpeg` で `/dev/video0` から HLS (`.m3u8` + `.ts`) を生成し、`nginx` 経由で配信、`Flask` 側で起動・停止と監視 API を提供します。

## 何をするリポジトリか

- カメラ映像を HLS へ変換して配信
- ブラウザ画面 (`/camera`) から表示・再接続・停止
- 一定時間アクセスがない場合に自動停止（watchdog）
- Private IP 帯からのみアクセス許可（nginx で制限）

## 構成

- `compose.yaml`: `app`(Flask + ffmpeg) と `web`(nginx) の 2 サービス定義
- `app/app.py`: API・ffmpeg プロセス管理・watchdog
- `app/templates/camera.html`: HLS.js を使った再生 UI
- `nginx/default.conf`: `/hls/` の静的配信と `/` のリバースプロキシ
- `hls/`: 生成される HLS セグメントの配置先（実行時）
- `state/`: `last_seen` など状態ファイル（実行時）

## 動作フロー（概要）

1. 画面アクセスで `/api/start` を実行
2. `ffmpeg` がカメラデバイスから HLS を生成して `hls/` に出力
3. ブラウザが `/hls/index.m3u8` を再生
4. ブラウザが定期的に `/api/ping` を送信し最終アクセス時刻を更新
5. `HEARTBEAT_TIMEOUT` を超えてアイドル状態が続くと watchdog が停止

## 起動方法

前提:

- Docker / Docker Compose が利用できること
- ホストに対象カメラデバイスが存在すること（`/dev/video0` と `CAMERA_DEVICE`）

設定:

```bash
cp .env.example .env
```

`.env` の `CAMERA_DEVICE` に使用するカメラデバイスを設定します。

起動:

```bash
docker compose up -d --build
```

アクセス:

- `http://<ホストIP>:8090/camera`

停止:

```bash
docker compose down
```

## 主な環境変数（`.env`）

- `CAMERA_DEVICE`: 取得元デバイス（例: `/dev/v4l/by-id/...`）
- `CAMERA_SIZE`: 解像度（例: `640x480`）
- `CAMERA_FPS`: フレームレート（例: `10`）
- `DEBUG_LOG`: `1` のときだけ `state/ffmpeg.log` に ffmpeg ログを保存（既定: `0`）
- `HLS_TIME`: HLS セグメント秒数（既定: `2`）
- `HLS_LIST_SIZE`: プレイリストに載せるセグメント数（既定: `6`）
- `STARTUP_SEGMENTS`: 再生開始前にそろえるセグメント数（既定: `2`）
- `HEARTBEAT_TIMEOUT`: 無通信で自動停止する秒数（既定: `300`）

## API エンドポイント

- `POST /api/start`: ffmpeg 起動（既に起動中なら維持）
- `POST /api/stop`: ffmpeg 停止
- `POST /api/ping`: 生存通知
- `GET /api/status`: 稼働状態取得

## 注意点

- `nginx/default.conf` で `192.168.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12` 以外は拒否します。
- 通常運用では ffmpeg ログは保存しません。調査時だけ `DEBUG_LOG=1` にすると `state/ffmpeg.log` に追記されます。
- 物理環境のカメラ ID が変わる場合は `.env` の `CAMERA_DEVICE` を調整してください。
- 起動直後のカクつきを避けるため、既定では 2 秒セグメントを生成し、2 セグメントそろってから再生を開始します。
