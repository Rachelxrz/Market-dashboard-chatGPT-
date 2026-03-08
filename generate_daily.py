#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_daily.py

功能：
1. 抓取最近 24 小时内的新闻
2. 按板块输出：投资 / 健康 / 心理哲学 / AI科技 / 美学
3. 每个板块尽量输出 10 条新闻
4. 每条新闻生成 3-5 句中文分析
5. 生成 docs/daily.json
6. 生成 docs/index.html

环境变量：
- OPENAI_API_KEY   必填（用于生成中文分析）
- OPENAI_MODEL     可选，默认 gpt-4o-mini
- TZ               可选，默认 America/New_York

依赖：
pip install openai feedparser python-dateutil requests
"""

import os
import re
import json
import time
import html
import math
import traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests
from dateutil import parser as date_parser

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


# =========================
# 基础配置
# =========================

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
TIMEZONE_NAME = os.getenv("TZ", "America/New_York").strip()

NOW_UTC = datetime.now(timezone.utc)
CUTOFF_UTC = NOW_UTC - timedelta(hours=24)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 DailyDashboardBot/1.0"
)

REQUEST_TIMEOUT = 20
TARGET_ITEMS_PER_SECTION = 10
MAX_ITEMS_PER_FEED = 20
MAX_SUMMARY_CHARS = 800

# 若 OpenAI 可用则初始化
client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None


# =========================
# 新闻源配置
# =========================

SECTIONS = {
    "投资": {
        "description": "市场、宏观、利率、公司与资产价格",
        "feeds": [
            "https://feeds.reuters.com/reuters/businessNews",
            "https://feeds.reuters.com/news/usmarkets",
            "https://finance.yahoo.com/news/rssindex",
            "https://www.investing.com/rss/news_25.rss",
            "https://www.investing.com/rss/news_301.rss",
            "https://www.marketwatch.com/rss/topstories",
            "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        ],
    },
    "健康": {
        "description": "医学、营养、抗衰老、代谢与公共健康",
        "feeds": [
            "https://www.medicalnewstoday.com/rss",
            "https://www.sciencedaily.com/rss/health_medicine.xml",
            "https://www.sciencedaily.com/rss/mind_brain.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml",
        ],
    },
    "心理/哲学": {
        "description": "心理学、行为、认知、哲学与意义讨论",
        "feeds": [
            "https://www.psychologytoday.com/us/rss",
            "https://aeon.co/feed.rss",
            "https://rss.nytimes.com/services/xml/rss/nyt/Mind.xml",
            "https://www.sciencedaily.com/rss/mind_brain.xml",
        ],
    },
    "AI 科技": {
        "description": "AI、芯片、软件、科技产业与研究进展",
        "feeds": [
            "https://feeds.arstechnica.com/arstechnica/technology-lab",
            "https://www.theverge.com/rss/index.xml",
            "https://www.technologyreview.com/feed/",
            "https://www.wired.com/feed/tag/ai/latest/rss",
            "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
        ],
    },
    "美学": {
        "description": "艺术、设计、摄影、建筑、审美与文化",
        "feeds": [
            "https://www.designboom.com/feed/",
            "https://www.dezeen.com/feed/",
            "https://www.artnews.com/c/art-news/news/feed/",
            "https://rss.nytimes.com/services/xml/rss/nyt/Arts.xml",
        ],
    },
}


# =========================
# 工具函数
# =========================

def log(msg: str):
    print(f"[generate_daily] {msg}", flush=True)


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def short_text(text: str, limit: int = 280) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def parse_entry_datetime(entry) -> datetime | None:
    """
    尽量兼容不同 RSS 字段：
    - published_parsed
    - updated_parsed
    - published
    - updated
    """
    candidates = []

    if getattr(entry, "published_parsed", None):
        try:
            t = entry.published_parsed
            return datetime(*t[:6], tzinfo=timezone.utc)
        except Exception:
            pass

    if getattr(entry, "updated_parsed", None):
        try:
            t = entry.updated_parsed
            return datetime(*t[:6], tzinfo=timezone.utc)
        except Exception:
            pass

    for key in ("published", "updated", "created"):
        val = getattr(entry, key, None)
        if val:
            candidates.append(val)

    for val in candidates:
        try:
            dt = parsedate_to_datetime(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        try:
            dt = date_parser.parse(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

    return None


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    url = re.sub(r"#.*$", "", url)
    return url


def article_key(title: str, url: str) -> str:
    t = re.sub(r"\W+", "", (title or "").lower())
    u = normalize_url(url).lower()
    return f"{t[:120]}|{u[:200]}"


def is_recent(dt: datetime | None) -> bool:
    if not dt:
        return False
    return dt >= CUTOFF_UTC


def fetch_url_text(url: str) -> str:
    """
    尝试抓文章正文前几千字，失败则返回空字符串。
    不依赖第三方正文提取库，尽量保持 GitHub Actions 稳定。
    """
    try:
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        text = resp.text or ""
        text = clean_text(text)
        return short_text(text, limit=3000)
    except Exception:
        return ""


def fetch_feed_items(feed_url: str, max_items: int = MAX_ITEMS_PER_FEED) -> list[dict]:
    """
    从单个 RSS 获取最近24小时内的候选新闻。
    """
    items = []
    try:
        feed = feedparser.parse(feed_url)
        entries = getattr(feed, "entries", []) or []
        for entry in entries[:max_items]:
            title = clean_text(getattr(entry, "title", ""))
            link = normalize_url(getattr(entry, "link", ""))
            summary = clean_text(getattr(entry, "summary", "") or getattr(entry, "description", ""))
            published_dt = parse_entry_datetime(entry)

            if not title or not link or not published_dt:
                continue
            if not is_recent(published_dt):
                continue

            source = ""
            try:
                source = clean_text(feed.feed.get("title", "")) if getattr(feed, "feed", None) else ""
            except Exception:
                source = ""

            items.append({
                "title": title,
                "link": link,
                "summary": short_text(summary, 500),
                "published_dt": published_dt,
                "published_utc": published_dt.strftime("%Y-%m-%d %H:%M UTC"),
                "source": source or "RSS",
            })
    except Exception as e:
        log(f"抓取 RSS 失败: {feed_url} | {e}")
    return items


def dedupe_items(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for item in sorted(items, key=lambda x: x["published_dt"], reverse=True):
        k = article_key(item["title"], item["link"])
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out


def fallback_analysis(title: str, summary: str, section: str) -> str:
    """
    没有 OpenAI Key 或调用失败时的兜底分析。
    保证仍然是 3-5 句话，不会只剩一句模板。
    """
    summary = summary or ""
    parts = []

    parts.append(f"这条{section}新闻值得关注，因为它反映了该板块最近 24 小时内的一个具体变化。")
    if summary:
        parts.append(f"从公开摘要看，核心线索是：{short_text(summary, 140)}")
    else:
        parts.append(f"从标题看，核心线索集中在“{title}”这一主题，说明市场或舆论正在围绕这一点形成关注。")
    parts.append("对你来说，真正重要的不是单条标题本身，而是它是否会在未来几天持续发酵并影响预期、估值或情绪。")
    parts.append("如果同类信息连续出现，就更可能意味着这是趋势信号，而不是一次性噪音。")

    return "\n".join(parts[:4])


def gpt_analysis(title: str, summary: str, body_text: str, section: str, source: str) -> str:
    """
    让 GPT 生成 3-5 句中文分析。
    """
    if not client:
        return fallback_analysis(title, summary, section)

    prompt = f"""
你是一位严谨的中文资讯编辑。请根据下面新闻信息，写出 3-5 句话的中文分析。

要求：
1. 必须是中文。
2. 必须是 3-5 句完整句子，不要只有一句。
3. 不要空话，不要重复标题，不要写“这条新闻值得注意”之类模板句。
4. 要概括：发生了什么、为什么重要、可能影响什么。
5. 面向高信息密度读者，语言清晰、简洁、具体。
6. 不要编造未提供的事实；不确定时用“从目前公开信息看”这样的表述。
7. 输出纯文本，不要项目符号，不要编号。

板块：{section}
来源：{source}
标题：{title}
摘要：{summary or "无"}
可用正文片段：{body_text or "无"}
"""

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个严谨、克制、中文表达自然的新闻分析助手。",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )
        text = resp.choices[0].message.content.strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        return short_text(text, MAX_SUMMARY_CHARS)
    except Exception as e:
        log(f"OpenAI 摘要失败，回退模板: {e}")
        return fallback_analysis(title, summary, section)


def collect_section_items(section_name: str, section_cfg: dict, target_count: int = TARGET_ITEMS_PER_SECTION) -> list[dict]:
    """
    聚合一个板块的新闻，去重并截取前 N 条。
    """
    all_items = []
    for feed_url in section_cfg["feeds"]:
        items = fetch_feed_items(feed_url, MAX_ITEMS_PER_FEED)
        all_items.extend(items)
        time.sleep(0.3)

    all_items = dedupe_items(all_items)
    all_items = sorted(all_items, key=lambda x: x["published_dt"], reverse=True)

    # 只取最近24小时内的前 target_count 条
    selected = all_items[:target_count]
    return selected


def enrich_items_with_analysis(section_name: str, items: list[dict]) -> list[dict]:
    enriched = []
    for i, item in enumerate(items, start=1):
        log(f"{section_name} - 生成第 {i}/{len(items)} 条分析：{item['title'][:80]}")
        body_text = fetch_url_text(item["link"])
        analysis = gpt_analysis(
            title=item["title"],
            summary=item.get("summary", ""),
            body_text=body_text,
            section=section_name,
            source=item.get("source", ""),
        )
        enriched.append({
            "rank": i,
            "title": item["title"],
            "link": item["link"],
            "source": item.get("source", ""),
            "published_utc": item["published_utc"],
            "analysis": analysis,
        })
        time.sleep(0.5)
    return enriched


def build_payload() -> dict:
    generated_at = NOW_UTC.strftime("%Y-%m-%d %H:%M:%S UTC")
    payload = {
        "generated_at": generated_at,
        "generated_at_unix": int(NOW_UTC.timestamp()),
        "window": {
            "from_utc": CUTOFF_UTC.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "to_utc": NOW_UTC.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "hours": 24,
        },
        "sections": {},
    }

    for section_name, section_cfg in SECTIONS.items():
        log(f"开始抓取板块：{section_name}")
        raw_items = collect_section_items(section_name, section_cfg, TARGET_ITEMS_PER_SECTION)
        enriched_items = enrich_items_with_analysis(section_name, raw_items)
        payload["sections"][section_name] = {
            "description": section_cfg["description"],
            "count": len(enriched_items),
            "items": enriched_items,
        }

    return payload


def write_json(payload: dict):
    path = DOCS_DIR / "daily.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已写入 JSON: {path}")


def section_html(section_name: str, section_data: dict) -> str:
    items = section_data.get("items", []) or []
    count = section_data.get("count", 0)

    html_parts = [
        f'<section class="section">',
        f'  <h2>{html.escape(section_name)}</h2>',
        f'  <p class="section-meta">最近24小时内新闻：{count} 条</p>',
    ]

    if not items:
        html_parts.append('<div class="card"><p>本板块最近24小时内未抓到足够新闻。</p></div>')
    else:
        for item in items:
            analysis_html = "<br>".join(
                html.escape(line.strip()) for line in item["analysis"].splitlines() if line.strip()
            )
            html_parts.append(
                f"""
<div class="card">
  <div class="title-row">
    <span class="rank">{item["rank"]}.</span>
    <a href="{html.escape(item["link"])}" target="_blank" rel="noopener noreferrer">{html.escape(item["title"])}</a>
  </div>
  <div class="meta">来源：{html.escape(item["source"])} ｜ 发布时间：{html.escape(item["published_utc"])}</div>
  <div class="analysis">{analysis_html}</div>
</div>
""".strip()
            )

    html_parts.append("</section>")
    return "\n".join(html_parts)


def write_html(payload: dict):
    generated_at = payload.get("generated_at", "")
    sections_html = "\n".join(
        section_html(section_name, section_data)
        for section_name, section_data in payload.get("sections", {}).items()
    )

    html_content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>每日扩展阅读</title>
  <style>
    :root {{
      --bg: #f7f7f8;
      --card: #ffffff;
      --text: #111827;
      --muted: #6b7280;
      --line: #e5e7eb;
      --link: #1d4ed8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC",
                   "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      line-height: 1.7;
    }}
    .wrap {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 28px 20px 56px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 34px;
      line-height: 1.2;
    }}
    .top-meta {{
      color: var(--muted);
      margin-bottom: 26px;
      font-size: 14px;
    }}
    h2 {{
      margin: 34px 0 10px;
      font-size: 28px;
      line-height: 1.25;
    }}
    .section-meta {{
      color: var(--muted);
      margin: 0 0 16px;
      font-size: 14px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px 18px 16px;
      margin-bottom: 14px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }}
    .title-row {{
      font-size: 22px;
      font-weight: 700;
      line-height: 1.45;
      margin-bottom: 8px;
    }}
    .rank {{
      display: inline-block;
      min-width: 26px;
    }}
    a {{
      color: var(--link);
      text-decoration: underline;
      text-underline-offset: 2px;
    }}
    .meta {{
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 12px;
    }}
    .analysis {{
      font-size: 20px;
      line-height: 1.9;
      white-space: normal;
    }}
    @media (max-width: 768px) {{
      .wrap {{ padding: 18px 14px 40px; }}
      h1 {{ font-size: 28px; }}
      h2 {{ font-size: 24px; }}
      .title-row {{ font-size: 18px; }}
      .analysis {{ font-size: 17px; line-height: 1.8; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>每日扩展阅读</h1>
    <div class="top-meta">
      生成时间：{html.escape(generated_at)}<br>
      抓取范围：最近 24 小时内新闻
    </div>
    {sections_html}
  </div>
</body>
</html>
"""
    path = DOCS_DIR / "index.html"
    path.write_text(html_content, encoding="utf-8")
    log(f"已写入 HTML: {path}")


def main():
    log("开始生成每日扩展阅读")
    if not OPENAI_API_KEY:
        log("警告：未检测到 OPENAI_API_KEY，将使用兜底分析模板，而不是 GPT 分析。")

    payload = build_payload()
    write_json(payload)
    write_html(payload)
    log("全部完成")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
