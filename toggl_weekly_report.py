#!/usr/bin/env python3
"""Toggl Trackの週次活動ログと前週比較レポートを生成する。"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


API_BASE_URL = "https://api.track.toggl.com/api/v9"
JST = ZoneInfo("Asia/Tokyo")
CSV_COLUMNS = [
  "ワークスペースID",
  "ワークスペース名",
  "プロジェクトID",
  "プロジェクト名",
  "クライアントID",
  "クライアント名",
  "タスク名",
  "開始日時",
  "終了日時",
  "経過時間_秒",
  "経過時間",
  "タグ",
]


@dataclass(frozen=True)
class WeekRange:
  start: datetime
  end: datetime

  @property
  def ending_date(self) -> date:
    return self.end.date()


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="Toggl Trackから1週間分の活動を取得し、前週CSVとの差分を出力します。"
  )
  parser.add_argument(
    "--week-ending",
    type=date.fromisoformat,
    metavar="YYYY-MM-DD",
    help="レポート対象週の日曜日。省略時は直近の日曜日です。",
  )
  parser.add_argument(
    "--works-dir",
    type=Path,
    default=Path.cwd() / "works",
    help="CSVとレポートの保存先（既定: ./works）",
  )
  parser.add_argument(
    "--no-report-file",
    action="store_true",
    help="Markdownレポートをファイル保存せず、標準出力だけに表示します。",
  )
  return parser.parse_args()


def required_env(name: str) -> str:
  value = os.environ.get(name, "").strip()
  if not value:
    raise ValueError(f"環境変数 {name} が設定されていません。")
  return value


def determine_week(week_ending: date | None) -> WeekRange:
  if week_ending is None:
    today = datetime.now(JST).date()
    week_ending = today - timedelta(days=(today.weekday() + 1) % 7)
  if week_ending.weekday() != 6:
    raise ValueError("--week-ending には日曜日の日付を指定してください。")
  start_date = week_ending - timedelta(days=6)
  start = datetime.combine(start_date, time.min, JST)
  end = datetime.combine(week_ending, time.max, JST)
  return WeekRange(start=start, end=end)


def iso_utc(value: datetime) -> str:
  return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def api_get(session: requests.Session, path: str, **params: Any) -> Any:
  response = session.get(f"{API_BASE_URL}{path}", params=params, timeout=30)
  response.raise_for_status()
  return response.json()


def fetch_metadata(
  session: requests.Session, workspaces: dict[str, int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
  projects: list[dict[str, Any]] = []
  clients: list[dict[str, Any]] = []
  for label, workspace_id in workspaces.items():
    by_id: dict[int, dict[str, Any]] = {}
    for active in ("true", "false"):
      for project in api_get(
        session, f"/workspaces/{workspace_id}/projects", active=active
      ):
        project["workspace_label"] = label
        by_id[project["id"]] = project
    projects.extend(by_id.values())
    for client in api_get(session, f"/workspaces/{workspace_id}/clients"):
      client["workspace_label"] = label
      clients.append(client)
  return projects, clients


def parse_api_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
  parsed = []
  for original in entries:
    entry = dict(original)
    entry["start_jst"] = parse_api_datetime(entry.get("start"))
    entry["stop_jst"] = parse_api_datetime(entry.get("stop"))
    duration = entry.get("duration", 0)
    entry["duration"] = max(0, int(duration)) if duration is not None else 0
    parsed.append(entry)
  return parsed


def parse_api_datetime(value: str | None) -> datetime | None:
  if not value:
    return None
  return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(JST)


def optional_int(value: Any) -> int | None:
  if value is None or str(value).strip() in ("", "nan", "None"):
    return None
  return int(float(str(value)))


def duration_seconds(value: Any) -> int:
  if value is None or str(value).strip() in ("", "N/A", "nan"):
    return 0
  text = str(value).strip()
  try:
    return max(0, int(float(text)))
  except ValueError:
    parts = [int(part) for part in text.split(":")]
    if len(parts) == 3:
      hours, minutes, seconds = parts
    elif len(parts) == 2:
      hours, (minutes, seconds) = 0, parts
    elif len(parts) == 1:
      hours, minutes, seconds = 0, 0, parts[0]
    else:
      raise ValueError(f"経過時間を解釈できません: {value}")
    return hours * 3600 + minutes * 60 + seconds


def parse_csv_datetime(value: str) -> datetime | None:
  value = value.strip()
  if not value:
    return None
  return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)


def load_comparison_csv(
  path: Path,
  workspace_name_to_id: dict[str, int],
  project_name_to_id: dict[str, int],
  client_name_to_id: dict[str, int],
) -> list[dict[str, Any]]:
  if not path.exists():
    print(f"注意: 比較対象CSVがありません: {path}", file=sys.stderr)
    return []
  entries = []
  with path.open("r", encoding="utf-8-sig", newline="") as handle:
    for row in csv.DictReader(handle):
      entries.append({
        "workspace_id": optional_int(row.get("ワークスペースID"))
        or workspace_name_to_id.get(row.get("ワークスペース名", "")),
        "project_id": optional_int(row.get("プロジェクトID"))
        or project_name_to_id.get(row.get("プロジェクト名", "")),
        "client_id": optional_int(row.get("クライアントID"))
        or client_name_to_id.get(row.get("クライアント名", "")),
        "description": row.get("タスク名", ""),
        "start_jst": parse_csv_datetime(row.get("開始日時", "")),
        "stop_jst": parse_csv_datetime(row.get("終了日時", "")),
        "duration": duration_seconds(
          row.get("経過時間_秒") or row.get("経過時間")
        ),
        "tags": row.get("タグ", "").split(":") if row.get("タグ") else [],
      })
  return entries


def format_duration(seconds: float | int | None) -> str:
  if seconds is None:
    return "N/A"
  total = max(0, int(seconds))
  return f"{total // 3600:02}:{(total % 3600) // 60:02}:{total % 60:02}"


def format_diff(seconds: float | int) -> str:
  sign = "+" if seconds >= 0 else "-"
  return sign + format_duration(abs(seconds))


def save_csv(
  path: Path,
  entries: list[dict[str, Any]],
  workspace_names: dict[int, str],
  project_names: dict[int, str],
  project_clients: dict[int, int],
  client_names: dict[int, str],
) -> None:
  with path.open("w", encoding="utf-8-sig", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for entry in entries:
      project_id = entry.get("project_id")
      client_id = project_clients.get(project_id)
      start = entry.get("start_jst")
      stop = entry.get("stop_jst")
      writer.writerow({
        "ワークスペースID": entry.get("workspace_id"),
        "ワークスペース名": workspace_names.get(
          entry.get("workspace_id"), "Unknown Workspace"
        ),
        "プロジェクトID": project_id,
        "プロジェクト名": project_names.get(project_id, "No Project"),
        "クライアントID": client_id,
        "クライアント名": client_names.get(client_id, "No Client"),
        "タスク名": entry.get("description", ""),
        "開始日時": start.strftime("%Y-%m-%d %H:%M:%S") if start else "",
        "終了日時": stop.strftime("%Y-%m-%d %H:%M:%S") if stop else "",
        "経過時間_秒": entry.get("duration", 0),
        "経過時間": format_duration(entry.get("duration", 0)),
        "タグ": ":".join(entry.get("tags") or []),
      })


def aggregate_projects(
  entries: list[dict[str, Any]], project_names: dict[int, str]
) -> tuple[int, dict[str, int]]:
  durations: defaultdict[str, int] = defaultdict(int)
  for entry in entries:
    durations[project_names.get(entry.get("project_id"), "No Project")] += int(
      entry.get("duration", 0)
    )
  return sum(durations.values()), dict(durations)


def aggregate_clients(
  entries: list[dict[str, Any]],
  project_clients: dict[int, int],
  client_names: dict[int, str],
) -> dict[str, int]:
  durations: defaultdict[str, int] = defaultdict(int)
  for entry in entries:
    client_id = entry.get("client_id") or project_clients.get(entry.get("project_id"))
    if client_id is not None:
      durations[client_names.get(client_id, "Unknown Client")] += int(
        entry.get("duration", 0)
      )
  return dict(durations)


def daily_category(
  category: str,
  entries: list[dict[str, Any]],
  project_names: dict[int, str],
  week: WeekRange,
) -> dict[date, int]:
  result = {(week.start + timedelta(days=i)).date(): 0 for i in range(7)}
  ids = {project_id for project_id, name in project_names.items() if name == category}
  for entry in entries:
    if entry.get("project_id") in ids and entry.get("start_jst"):
      result[entry["start_jst"].date()] += int(entry.get("duration", 0))
  return result


def build_report(
  week: WeekRange,
  report_entries: list[dict[str, Any]],
  comparison_entries: list[dict[str, Any]],
  work_workspace_id: int,
  project_names: dict[int, str],
  project_clients: dict[int, int],
  client_names: dict[int, str],
) -> str:
  current_work = [e for e in report_entries if e.get("workspace_id") == work_workspace_id]
  previous_work = [e for e in comparison_entries if e.get("workspace_id") == work_workspace_id]
  total, projects = aggregate_projects(current_work, project_names)
  previous_total, previous_projects = aggregate_projects(previous_work, project_names)
  lines = [
    f"# {week.start:%Y年%m月%d日}から{week.end:%Y年%m月%d日}までのレポート",
    "",
  ]
  if not comparison_entries:
    lines += [
      "> 注意: 比較対象週のCSVがないか、対象データがありません。差分は当週の値と同じになります。",
      "",
    ]
  lines += [
    f"* 計算日: {week.ending_date:%Y/%m/%d}",
    f"* ワークスペースの総作業時間: {format_duration(total)}({format_diff(total - previous_total)})",
    f"* 平均: {format_duration(total / 7)}({format_diff((total - previous_total) / 7)})",
    "",
    "### 上位三件",
    "",
  ]
  descending = sorted(projects.items(), key=lambda item: item[1], reverse=True)
  ascending = sorted(
    ((name, value) for name, value in projects.items() if value > 0),
    key=lambda item: item[1],
  )
  lines.extend(project_lines(descending[:3], total, previous_projects, current_work, project_names))
  lines += ["", "### 下位三件", ""]
  lines.extend(project_lines(ascending[:3], total, previous_projects, current_work, project_names))

  current_clients = aggregate_clients(current_work, project_clients, client_names)
  previous_clients = aggregate_clients(previous_work, project_clients, client_names)
  client_total = sum(current_clients.values())
  lines += ["", "### クライアント別の作業時間", ""]
  if current_clients:
    for name, value in sorted(current_clients.items(), key=lambda item: item[1], reverse=True):
      percent = value / client_total * 100 if client_total else 0
      lines.append(
        f"* {name}: {format_duration(value)}({format_diff(value - previous_clients.get(name, 0))})  {percent:.2f}%"
      )
  else:
    lines.append("クライアントに紐づく作業時間がありませんでした。")

  for category, icon in (("睡眠", "💤"), ("趣味", "🎮")):
    current_daily = daily_category(category, report_entries, project_names, week)
    previous_daily = daily_category(
      category,
      comparison_entries,
      project_names,
      WeekRange(week.start - timedelta(days=7), week.end - timedelta(days=7)),
    )
    current_sum = sum(current_daily.values())
    previous_sum = sum(previous_daily.values())
    ranked = sorted(current_daily.items(), key=lambda item: (-item[1], item[0]))
    shortest = min(current_daily.items(), key=lambda item: (item[1], item[0]))
    lines += [
      "",
      f"## {category}{icon}",
      "",
      f"* 週合計: {format_duration(current_sum)}({format_diff(current_sum - previous_sum)})",
      f"* 日平均: {format_duration(current_sum / 7)}(前週との日平均差分: {format_diff((current_sum - previous_sum) / 7)})",
      f"* 最も長かった日: {ranked[0][0]:%Y/%m/%d} - {format_duration(ranked[0][1])}",
    ]
    if category == "趣味":
      lines.append(
        f"* 二番目に長かった日: {ranked[1][0]:%Y/%m/%d} - {format_duration(ranked[1][1])}"
      )
    lines.append(
      f"* 最も短かった日: {shortest[0]:%Y/%m/%d} - {format_duration(shortest[1])}"
    )

  sleep = daily_category("睡眠", report_entries, project_names, week)
  hobby = daily_category("趣味", report_entries, project_names, week)
  labels = ", ".join(f'"{day:%d}"' for day in sorted(sleep))
  sleep_hours = ", ".join(str(round(value / 3600, 2)) for value in sleep.values())
  hobby_hours = ", ".join(str(round(value / 3600, 2)) for value in hobby.values())
  maximum = max(1, round(max([*sleep.values(), *hobby.values()], default=0) / 3600 * 1.2))
  lines += [
    "",
    "## 睡眠と趣味のグラフ📊",
    "",
    "```mermaid",
    "xychart-beta",
    '  title "睡眠と趣味（時間）"',
    f'  x-axis "日付" [{labels}]',
    f'  y-axis "時間（h）" 0 --> {maximum}',
    f'  line "睡眠" [{sleep_hours}]',
    f'  line "趣味" [{hobby_hours}]',
    "```",
  ]
  return "\n".join(lines) + "\n"


def project_lines(
  items: list[tuple[str, int]],
  total: int,
  previous: dict[str, int],
  entries: list[dict[str, Any]],
  project_names: dict[int, str],
) -> list[str]:
  lines = []
  for index, (name, value) in enumerate(items, 1):
    percent = value / total * 100 if total else 0
    lines.append(
      f"{index}. {name}: {format_duration(value)}({format_diff(value - previous.get(name, 0))})  {percent:.2f}%"
    )
    if name == "雑務":
      descriptions = sorted({
        str(entry.get("description"))
        for entry in entries
        if project_names.get(entry.get("project_id")) == "雑務"
        and entry.get("description")
      })
      if descriptions:
        lines.append(f"   * 内容: {', '.join(descriptions)}")
  if not lines:
    lines.append("対象データがありません。")
  return lines


def main() -> int:
  args = parse_args()
  try:
    token = required_env("TOGGL_API_TOKEN")
    work_workspace_id = int(required_env("TOGGL_WORK_WORKSPACE_ID"))
    life_workspace_id = int(required_env("TOGGL_LIFE_WORKSPACE_ID"))
    week = determine_week(args.week_ending)
  except ValueError as error:
    print(f"設定エラー: {error}", file=sys.stderr)
    return 2

  works_dir = args.works_dir.resolve()
  works_dir.mkdir(parents=True, exist_ok=True)
  workspaces = {"work": work_workspace_id, "life": life_workspace_id}
  session = requests.Session()
  session.auth = (token, "api_token")
  session.headers.update({"Content-Type": "application/json"})

  try:
    projects, clients = fetch_metadata(session, workspaces)
    raw_entries = api_get(
      session,
      "/me/time_entries",
      start_date=iso_utc(week.start),
      end_date=iso_utc(week.end),
    )
  except requests.RequestException as error:
    print(f"Toggl Track APIの取得に失敗しました: {error}", file=sys.stderr)
    return 1

  entries = parse_api_entries(raw_entries)
  entries = [e for e in entries if e.get("start_jst") and week.start <= e["start_jst"] <= week.end]
  project_names = {p["id"]: p["name"] for p in projects}
  project_clients = {p["id"]: p["client_id"] for p in projects if p.get("client_id")}
  client_names = {c["id"]: c["name"] for c in clients}
  workspace_names = {value: key for key, value in workspaces.items()}

  csv_path = works_dir / f"toggl_activity_log_full_{week.ending_date:%Y-%m-%d}.csv"
  save_csv(
    csv_path,
    entries,
    workspace_names,
    project_names,
    project_clients,
    client_names,
  )

  comparison_end = week.ending_date - timedelta(days=7)
  comparison_path = works_dir / f"toggl_activity_log_full_{comparison_end:%Y-%m-%d}.csv"
  comparison_entries = load_comparison_csv(
    comparison_path,
    {name: value for value, name in workspace_names.items()},
    {name: value for value, name in project_names.items()},
    {name: value for value, name in client_names.items()},
  )
  report = build_report(
    week,
    entries,
    comparison_entries,
    work_workspace_id,
    project_names,
    project_clients,
    client_names,
  )
  print(report, end="")
  print(f"\nCSV保存先: {csv_path}")
  if not args.no_report_file:
    report_path = works_dir / f"toggl_weekly_report_{week.ending_date:%Y-%m-%d}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"レポート保存先: {report_path}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
