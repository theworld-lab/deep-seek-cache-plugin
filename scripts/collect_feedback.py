#!/usr/bin/env python3
"""Collect repository feedback metrics and write FEEDBACK.md.

The daily workflow runs this straight after `actions/setup-python` with no
dependency installation step, so this file uses the standard library only.

Environment:
    GITHUB_TOKEN  token used for API calls; falls back to GH_TOKEN, then to
                  unauthenticated access (public data only, 60 requests/hour)
    REPO          "owner/name"; defaults to theworld-lab/deep-seek-cache-plugin
    FEEDBACK_OUTPUT   output path; defaults to FEEDBACK.md
    FEEDBACK_WINDOW_DAYS  reporting window in days; defaults to 30

Exit status is 0 when the report was written, 1 when the core repository
metrics could not be read (so a genuinely broken run stays visible in CI).
Optional metrics degrade to "unavailable" instead of failing the run.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_ROOT = "https://api.github.com"
DEFAULT_REPO = "theworld-lab/deep-seek-cache-plugin"

REPO = os.environ.get("REPO", DEFAULT_REPO).strip() or DEFAULT_REPO
TOKEN = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
OUTPUT_PATH = os.environ.get("FEEDBACK_OUTPUT", "FEEDBACK.md")
WINDOW_DAYS = int(os.environ.get("FEEDBACK_WINDOW_DAYS", "30"))

UNAVAILABLE = "unavailable"


def _request(url, required):
    """Return decoded JSON, or None when an optional call fails."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ds-cache-guard-feedback",
        },
    )
    if TOKEN:
        request.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = "{0} {1}".format(error.code, error.reason)
        if required:
            raise RuntimeError("GET {0} failed: {1}".format(url, detail)) from error
        sys.stderr.write("warning: {0} -> {1}\n".format(url, detail))
        return None
    except (urllib.error.URLError, ValueError, OSError) as error:
        if required:
            raise RuntimeError("GET {0} failed: {1}".format(url, error)) from error
        sys.stderr.write("warning: {0} -> {1}\n".format(url, error))
        return None


def get(path, **params):
    """Authenticated GET against the GitHub REST API."""
    url = API_ROOT + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return _request(url, required=True)


def get_optional(path, **params):
    """Best-effort GET; returns None instead of raising."""
    url = API_ROOT + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return _request(url, required=False)


def search_count(query):
    """Total match count for a search query, or None when unavailable."""
    payload = get_optional("/search/issues", q=query, per_page=1)
    if not payload or "total_count" not in payload:
        return None
    return payload["total_count"]


def count_or_dash(value):
    return "—" if value is None else str(value)


def relative(iso_timestamp):
    """Render an ISO timestamp as 'YYYY-MM-DD (N days ago)'."""
    if not iso_timestamp:
        return UNAVAILABLE
    try:
        moment = datetime.strptime(iso_timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return iso_timestamp
    moment = moment.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - moment).days
    return "{0} ({1} 天前)".format(moment.strftime("%Y-%m-%d"), days)


def collect():
    """Gather every metric, returning (report_data, warnings)."""
    warnings = []
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=WINDOW_DAYS)
    since_date = since.strftime("%Y-%m-%d")

    repo = get("/repos/" + REPO)

    commits = get_optional(
        "/repos/{0}/commits".format(REPO),
        since=since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        per_page=100,
    )
    if commits is None:
        warnings.append("最近提交读取失败")
        commits_in_window = None
    else:
        commits_in_window = len(commits)

    runs = get_optional("/repos/{0}/actions/runs".format(REPO), per_page=20)
    if runs is None or "workflow_runs" not in runs:
        warnings.append("Actions 运行记录读取失败")
        run_total = run_success = run_failure = None
        last_run = UNAVAILABLE
    else:
        recent = runs["workflow_runs"]
        run_total = len(recent)
        run_success = sum(1 for r in recent if r.get("conclusion") == "success")
        run_failure = sum(1 for r in recent if r.get("conclusion") == "failure")
        last_run = (
            "{0} · {1}".format(recent[0].get("conclusion", "?"), relative(recent[0].get("created_at")))
            if recent
            else "无记录"
        )

    # Search API has a stricter rate limit than the core API, so each of these
    # is optional and degrades to "—" rather than failing the run.
    issues_open = search_count("repo:{0} type:issue state:open".format(REPO))
    issues_opened = search_count("repo:{0} type:issue created:>={1}".format(REPO, since_date))
    issues_closed = search_count("repo:{0} type:issue closed:>={1}".format(REPO, since_date))
    prs_opened = search_count("repo:{0} type:pr created:>={1}".format(REPO, since_date))
    prs_merged = search_count("repo:{0} type:pr is:merged merged:>={1}".format(REPO, since_date))

    return {
        "now": now,
        "window_days": WINDOW_DAYS,
        "since_date": since_date,
        "repo": repo,
        "commits_in_window": commits_in_window,
        "run_total": run_total,
        "run_success": run_success,
        "run_failure": run_failure,
        "last_run": last_run,
        "issues_open": issues_open,
        "issues_opened": issues_opened,
        "issues_closed": issues_closed,
        "prs_opened": prs_opened,
        "prs_merged": prs_merged,
    }, warnings


def render(data, warnings):
    repo = data["repo"]
    open_issues = repo.get("open_issues_count", 0)
    lines = []
    add = lines.append

    add("# 项目反馈日报")
    add("")
    add("> 本文件由 [`.github/workflows/daily-feedback.yml`](../.github/workflows/daily-feedback.yml) "
        "自动生成，请勿手工编辑。")
    add("")
    add("- **仓库**：`{0}`".format(REPO))
    add("- **生成时间**：{0}".format(data["now"].strftime("%Y-%m-%d %H:%M UTC")))
    add("- **统计窗口**：最近 {0} 天（自 {1} 起）".format(data["window_days"], data["since_date"]))
    add("")

    add("## 仓库概况")
    add("")
    add("| 指标 | 数值 |")
    add("| --- | --- |")
    add("| Stars | {0} |".format(repo.get("stargazers_count", "—")))
    add("| Forks | {0} |".format(repo.get("forks_count", "—")))
    add("| Watchers | {0} |".format(repo.get("subscribers_count", "—")))
    add("| Open issues（含 PR） | {0} |".format(open_issues))
    add("| 未关闭 issue | {0} |".format(count_or_dash(data["issues_open"])))
    add("| 仓库体积 | {0} KB |".format(repo.get("size", "—")))
    add("| 创建时间 | {0} |".format(relative(repo.get("created_at"))))
    add("| 最近推送 | {0} |".format(relative(repo.get("pushed_at"))))
    add("| 默认分支 | `{0}` |".format(repo.get("default_branch", "—")))
    add("")

    add("## 活跃度（最近 {0} 天）".format(data["window_days"]))
    add("")
    add("| 指标 | 数量 |")
    add("| --- | --- |")
    add("| 提交 | {0} |".format(count_or_dash(data["commits_in_window"])))
    add("| 新建 issue | {0} |".format(count_or_dash(data["issues_opened"])))
    add("| 关闭 issue | {0} |".format(count_or_dash(data["issues_closed"])))
    add("| 新建 PR | {0} |".format(count_or_dash(data["prs_opened"])))
    add("| 合并 PR | {0} |".format(count_or_dash(data["prs_merged"])))
    add("")

    add("## CI 健康度（最近 20 次运行）")
    add("")
    add("| 指标 | 数值 |")
    add("| --- | --- |")
    add("| 运行总数 | {0} |".format(count_or_dash(data["run_total"])))
    add("| 成功 | {0} |".format(count_or_dash(data["run_success"])))
    add("| 失败 | {0} |".format(count_or_dash(data["run_failure"])))
    add("| 最近一次 | {0} |".format(data["last_run"]))
    add("")

    if data["run_failure"]:
        add("> ⚠️ 最近 20 次运行中有 {0} 次失败，请检查 Actions 日志。".format(data["run_failure"]))
        add("")

    if warnings:
        add("## 数据完整性说明")
        add("")
        for warning in warnings:
            add("- {0}".format(warning))
        add("")

    add("---")
    add("")
    add("*生成脚本：`scripts/collect_feedback.py`*")
    add("")
    return "\n".join(lines)


def main():
    try:
        data, warnings = collect()
    except RuntimeError as error:
        sys.stderr.write("failed to collect core metrics: {0}\n".format(error))
        return 1

    content = render(data, warnings)
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)

    print(
        "wrote {0}: stars={1} forks={2} open_issues={3} commits/{4}d={5} ci_fail={6}".format(
            OUTPUT_PATH,
            data["repo"].get("stargazers_count"),
            data["repo"].get("forks_count"),
            data["repo"].get("open_issues_count"),
            data["window_days"],
            data["commits_in_window"],
            data["run_failure"],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
