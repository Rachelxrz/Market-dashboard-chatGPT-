#!/usr/bin/env python3
"""Push the generated daily-news link to WeChat through ServerChan."""

import argparse
import json
import os
import sys
from datetime import datetime
from urllib import error, parse, request
from zoneinfo import ZoneInfo


NEWS_URL = "https://rachelxrz.github.io/Market-dashboard-chatGPT-/"
TIMEZONE_NAME = os.getenv("TZ", "America/New_York")


def build_message(run_mode: str) -> tuple[str, str]:
    today = datetime.now(ZoneInfo(TIMEZONE_NAME)).strftime("%Y-%m-%d")
    edition = "收盘版" if run_mode == "close" else "早间版"
    title = f"每日新闻｜{today}｜{edition}"
    body = (
        f"{today} 的每日新闻已经生成。\n\n"
        f"[点击查看当天新闻]({NEWS_URL})\n\n"
        "网站内可通过日期按钮查看历史新闻。"
    )
    return title, body


def send_message(send_key: str, title: str, body: str) -> None:
    endpoint = f"https://sctapi.ftqq.com/{parse.quote(send_key, safe='')}.send"
    payload = parse.urlencode({"title": title, "desp": body}).encode("utf-8")
    req = request.Request(endpoint, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise RuntimeError(f"ServerChan returned HTTP {exc.code}") from None
    except (error.URLError, TimeoutError, json.JSONDecodeError):
        raise RuntimeError("ServerChan request failed") from None

    if result.get("code") != 0:
        message = result.get("message") or result.get("data") or "unknown error"
        raise RuntimeError(f"ServerChan rejected the push: {message}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    send_key = os.getenv("SERVERCHAN_SENDKEY", "").strip()
    if not send_key:
        print("SERVERCHAN_SENDKEY is required.", file=sys.stderr)
        return 1

    run_mode = os.getenv("RUN_MODE", "morning").strip().lower()
    title, body = build_message(run_mode)

    if args.dry_run:
        assert title and NEWS_URL in body
        print("WeChat push payload verified (dry run; no message sent).")
        return 0

    try:
        send_message(send_key, title, body)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Daily news notification pushed to WeChat successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
