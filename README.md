# Toggl週次活動レポート

Google Colaboratory版ノートブックを、セルの実行順序に依存しないローカル用Pythonスクリプトへ整理したものです。

## 必要環境

- Python 3.9以上
- Toggl TrackのAPIトークン
- `work` と `life` のワークスペースID

## 準備

```bash
python -m venv .venv
```

Windows（PowerShell）:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:TOGGL_API_TOKEN="APIトークン"
$env:TOGGL_WORK_WORKSPACE_ID="workのID"
$env:TOGGL_LIFE_WORKSPACE_ID="lifeのID"
```

macOS・Linux:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
export TOGGL_API_TOKEN="APIトークン"
export TOGGL_WORK_WORKSPACE_ID="workのID"
export TOGGL_LIFE_WORKSPACE_ID="lifeのID"
```

## 実行

```bash
python toggl_weekly_report.py
```

標準では、日本時間における直近の日曜日を終端とする月曜〜日曜の1週間を集計します。結果はカレントフォルダの `works` フォルダに保存されます。

- `toggl_activity_log_full_YYYY-MM-DD.csv`: 当週の全活動ログ
- `toggl_weekly_report_YYYY-MM-DD.md`: 前週との差分を含むレポート

過去の週を指定する場合:

```bash
python toggl_weekly_report.py --week-ending 2026-08-30
```

`--week-ending` には日曜日の日付を指定します。比較には、同じ `works` フォルダにある7日前の日付のCSVを使用します。前週CSVがまだなければ差分は正しく計算できないため、古い週から順番に一度ずつ実行してください。

保存先を変更する場合:

```bash
python toggl_weekly_report.py --works-dir 任意のフォルダ
```

Markdownファイルを作らず画面表示だけにする場合:

```bash
python toggl_weekly_report.py --no-report-file
```

## Colab版からの主な変更

- Colab SecretsをOSの環境変数へ変更
- 日付ウィジェットをコマンドライン引数へ変更
- Google Driveの保存先を `./works/` へ変更
- `files.download()`、`drive.mount()`、セル間のグローバル変数を廃止
- CSVにはID列を常に保存し、同名プロジェクトがある場合の誤対応を減らした
- レポートを標準出力とMarkdownファイルの両方へ出力
