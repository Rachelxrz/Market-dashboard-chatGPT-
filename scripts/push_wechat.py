#!/usr/bin/env python3
"""Push every generated news item to WeChat through ServerChan."""

import argparse
import json
import os
import sys
from datetime import datetime
from urllib import error, parse, request
from zoneinfo import ZoneInfo


NEWS_URL = "https://rachelxrz.github.io/Market-dashboard-chatGPT-/"
TIMEZONE_NAME = os.getenv("TZ", "America/New_York")
READING_JSON = os.getenv("READING_JSON", "docs/reading.json")


def clean_line(value: object) -> str:
    return " ".join(str(value or "").split())


def load_reading(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read generated news JSON: {path}") from exc

    if not isinstance(payload.get("sections"), dict) or not payload["sections"]:
        raise RuntimeError("Generated news JSON has no sections")
    return payload


def build_messages(payload: dict, run_mode: str) -> list[tuple[str, str]]:
    today = datetime.now(ZoneInfo(TIMEZONE_NAME)).strftime("%Y-%m-%d")
    edition = "收盘版" if run_mode == "close" else "早间版"
    sections = list(payload["sections"].items())
    total_sections = len(sections)
    messages = []

    for section_number, (section_name, section) in enumerate(sections, start=1):
        items = section.get("items") or []
        title = f"[{section_number}/{total_sections}] 每日新闻｜{today}｜{clean_line(section_name)}"
        lines = [
            f"**{clean_line(section_name)}｜{edition}**",
            "",
            clean_line(section.get("description")),
            "",
            f"共 {len(items)} 条新闻",
        ]

        for item_number, item in enumerate(items, start=1):
            item_title = clean_line(item.get("title"))
            source = clean_line(item.get("source"))
            published = clean_line(item.get("published_utc"))
            analysis_zh = clean_line(item.get("analysis_zh"))
            analysis_en = clean_line(item.get("analysis_en"))
            link = clean_line(item.get("link"))

            lines.extend(
                [
                    "",
                    "---",
                    "",
                    f"### {item_number:02d}. {item_title}",
                    "",
                    f"**来源：** {source}  ",
                    f"**发布时间：** {published}",
                    "",
                    f"**中文分析：** {analysis_zh}",
                    "",
                    f"**English analysis:** {analysis_en}",
                    "",
                    f"[阅读原文]({link})",
                ]
            )

        lines.extend(["", "---", "", f"[查看全部历史新闻]({NEWS_URL})"])
        messages.append((title, "\n".join(lines)))

    return messages


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
    try:
        payload = load_reading(READING_JSON)
        messages = build_messages(payload, run_mode)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.dry_run:
        item_count = sum(len(section.get("items") or []) for section in payload["sections"].values())
        assert messages and all(title and NEWS_URL in body for title, body in messages)
        for (_, section), (_, body) in zip(payload["sections"].items(), messages):
            for item in section.get("items") or []:
                for field in ("title", "link", "analysis_zh", "analysis_en"):
                    value = clean_line(item.get(field))
                    if value:
                        assert value in body, f"Missing {field} from WeChat payload"
        print(
            f"WeChat payload verified: {len(messages)} sections, {item_count} news items "
            "(dry run; no message sent)."
        )
        return 0

    for message_number, (title, body) in enumerate(messages, start=1):
        try:
            send_message(send_key, title, body)
        except RuntimeError as exc:
            print(f"Message {message_number}/{len(messages)} failed: {exc}", file=sys.stderr)
            return 1

    print(f"Pushed {len(messages)} complete news sections to WeChat successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
