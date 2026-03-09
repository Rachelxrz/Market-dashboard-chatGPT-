#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
scripts/generate_daily.py

输出：
1. docs/index.html                  -> 最新结构监控主页
2. docs/monitor.json                -> 最新结构监控数据
3. docs/reading.html                -> 最新每日扩展阅读
4. docs/reading.json                -> 最新每日扩展阅读数据

5. docs/history/YYYY-MM-DD/index.html
6. docs/history/YYYY-MM-DD/monitor.json
7. docs/history/YYYY-MM-DD/reading.html
8. docs/history/YYYY-MM-DD/reading.json

功能：
- 保留最近 7 天历史
- 扩展阅读中英双栏
- 结构监控中英双语
- 四层结构监控
  波动层：VIX
  利率层：MOVE + TNX
  信用层：HYG + LQD
  资产层：QQQ + GLD + VLCC

环境变量：
- OPENAI_API_KEY      可选；没有时会使用兜底分析
- OPENAI_MODEL        可选，默认 gpt-4o-mini
- TZ                  可选，默认 America/New_York
- VLCC_PROXY_SYMBOL   可选，默认 DHT，可改成 FRO 等

依赖：
pip install openai feedparser python-dateutil requests
"""

import os
import re
import json
import time
import html
import shutil
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

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
TIMEZONE_NAME = os.getenv("TZ", "America/New_York").strip()
VLCC_PROXY_SYMBOL = os.getenv("VLCC_PROXY_SYMBOL", "DHT").strip().upper()

NOW_UTC = datetime.now(timezone.utc)
CUTOFF_UTC = NOW_UTC - timedelta(hours=24)
TODAY_STR = NOW_UTC.strftime("%Y-%m-%d")

HISTORY_DIR = DOCS_DIR / "history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

TODAY_HISTORY_DIR = HISTORY_DIR / TODAY_STR
TODAY_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 DailyDashboardBot/Final"
)

REQUEST_TIMEOUT = 20
TARGET_ITEMS_PER_SECTION = 10
MAX_ITEMS_PER_FEED = 20
MAX_SUMMARY_CHARS = 1200
HISTORY_KEEP_DAYS = 7

client = OpenAI(api_key=OPENAI_API_KEY) if (OpenAI and OPENAI_API_KEY) else None


# =========================
# 扩展阅读新闻源
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
# 结构监控标的
# =========================

MARKET_SYMBOLS = {
    "SPY": {"label": "SPY", "kind": "etf", "note": "美股大盘代理"},
    "QQQ": {"label": "QQQ", "kind": "etf", "note": "科技成长代理"},
    "DIA": {"label": "DIA", "kind": "etf", "note": "道指代理"},
    "IWM": {"label": "IWM", "kind": "etf", "note": "小盘股代理"},
    "GLD": {"label": "GLD", "kind": "etf", "note": "黄金代理"},
    "SLV": {"label": "SLV", "kind": "etf", "note": "白银代理"},
    "USO": {"label": "USO", "kind": "etf", "note": "原油代理"},
    "UUP": {"label": "UUP", "kind": "etf", "note": "美元代理"},
    "^TNX": {"label": "TNX", "kind": "index", "note": "10年美债收益率"},
    "^VIX": {"label": "VIX", "kind": "index", "note": "股市波动率"},
    "^MOVE": {"label": "MOVE", "kind": "index", "note": "债券波动率"},
    "HYG": {"label": "HYG", "kind": "etf", "note": "高收益债信用"},
    "LQD": {"label": "LQD", "kind": "etf", "note": "投资级信用"},
    VLCC_PROXY_SYMBOL: {
        "label": "VLCC",
        "kind": "equity",
        "note": f"VLCC 航运代理（当前: {VLCC_PROXY_SYMBOL}）",
    },
}


# =========================
# 通用工具
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


def safe_float(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def fmt_num(x, digits=2):
    if x is None:
        return "N/A"
    try:
        return f"{float(x):,.{digits}f}"
    except Exception:
        return "N/A"


def fmt_pct(x, digits=2):
    if x is None:
        return "N/A"
    try:
        return f"{float(x):+.{digits}f}%"
    except Exception:
        return "N/A"


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


def list_history_days(limit: int = HISTORY_KEEP_DAYS):
    if not HISTORY_DIR.exists():
        return []
    days = []
    for p in HISTORY_DIR.iterdir():
        if p.is_dir():
            try:
                datetime.strptime(p.name, "%Y-%m-%d")
                days.append(p.name)
            except Exception:
                pass
    return sorted(days, reverse=True)[:limit]


def cleanup_old_history(keep_days: int = HISTORY_KEEP_DAYS):
    if not HISTORY_DIR.exists():
        return

    dirs = []
    for p in HISTORY_DIR.iterdir():
        if p.is_dir():
            try:
                dt = datetime.strptime(p.name, "%Y-%m-%d")
                dirs.append((dt, p))
            except Exception:
                continue

    dirs.sort(key=lambda x: x[0], reverse=True)

    for _, path in dirs[keep_days:]:
        try:
            shutil.rmtree(path)
            log(f"已删除旧归档: {path}")
        except Exception as e:
            log(f"删除旧归档失败 {path}: {e}")


def render_history_links(page_name: str) -> str:
    """
    page_name:
    - 'index.html'
    - 'reading.html'
    """
    days = list_history_days(HISTORY_KEEP_DAYS)
    if not days:
        return ""

    links = []
    for day in days:
        href = f"./history/{day}/{page_name}"
        links.append(f'<a href="{html.escape(href)}">{html.escape(day)}</a>')

    return '<div class="history-links">' + "".join(links) + "</div>"


# =========================
# 阅读页逻辑
# =========================

def parse_entry_datetime(entry):
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


def is_recent(dt):
    return bool(dt and dt >= CUTOFF_UTC)


def fetch_url_text(url: str) -> str:
    try:
        headers = {"User-Agent": USER_AGENT}
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        text = clean_text(resp.text or "")
        return short_text(text, limit=3000)
    except Exception:
        return ""


def fetch_feed_items(feed_url: str, max_items: int = MAX_ITEMS_PER_FEED):
    items = []
    try:
        feed = feedparser.parse(feed_url)
        entries = getattr(feed, "entries", []) or []
        feed_title = ""
        try:
            feed_title = clean_text(feed.feed.get("title", "")) if getattr(feed, "feed", None) else ""
        except Exception:
            pass

        for entry in entries[:max_items]:
            title = clean_text(getattr(entry, "title", ""))
            link = normalize_url(getattr(entry, "link", ""))
            summary = clean_text(getattr(entry, "summary", "") or getattr(entry, "description", ""))
            published_dt = parse_entry_datetime(entry)

            if not title or not link or not published_dt:
                continue
            if not is_recent(published_dt):
                continue

            items.append({
                "title": title,
                "link": link,
                "summary": short_text(summary, 500),
                "published_dt": published_dt,
                "published_utc": published_dt.strftime("%Y-%m-%d %H:%M UTC"),
                "source": feed_title or "RSS",
            })
    except Exception as e:
        log(f"抓取 RSS 失败: {feed_url} | {e}")
    return items


def dedupe_items(items):
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
    parts = [f"这条{section}新闻反映了最近24小时内该板块的一个具体变化。"]

    clean_summary = clean_text(summary or "")
    if clean_summary:
        # 尽量减少直接塞入长英文
        short_summary = short_text(clean_summary, 80)
        parts.append(f"从公开摘要看，核心线索集中在：{short_summary}")
    else:
        parts.append(f"从标题看，当前讨论重点集中在“{title}”这一主题。")

    parts.append("更重要的是观察这一主题是否会在接下来几天持续发酵，并进一步影响预期、估值或市场情绪。")
    parts.append("如果同类信息连续出现，它更可能代表趋势，而不是一次性噪音。")
    return "\n".join(parts[:4])


SECTION_EN_MAP = {
    "投资": "Investment",
    "健康": "Health",
    "心理/哲学": "Psychology / Philosophy",
    "AI 科技": "AI / Technology",
    "美学": "Aesthetics",
}
def fallback_analysis_en(section: str) -> str:
    section_en = SECTION_EN_MAP.get(section, section)
    return (
        f"This {section_en} news item reflects a concrete development within the last 24 hours. "
        f"The key question is whether this theme will continue to shape expectations, valuations, or sentiment over the next few days. "
        f"If similar reports keep appearing, it is more likely to represent a trend rather than one-off noise."
    )


def gpt_bilingual_analysis(title: str, summary: str, body_text: str, section: str, source: str) -> dict:
    if not client:
        return {
            "zh": fallback_analysis(title, summary, section),
            "en": fallback_analysis_en(section),
        }

    section_en = SECTION_EN_MAP.get(section, section)

    prompt = f"""
You are a rigorous bilingual editor.

Write two matching analyses for the news item below:

[ZH]
- Write in natural Chinese
- 3 to 5 full sentences
- Explain what happened, why it matters, and what it may affect
- Do NOT copy long English phrases directly into Chinese
- Rewrite naturally for a Chinese reader

[EN]
- Write in natural English
- 3 to 5 full sentences
- Match the Chinese meaning closely
- Explain what happened, why it matters, and what it may affect

Output format exactly:

[ZH]
...
[EN]
...

Section (Chinese): {section}
Section (English): {section_en}
Source: {source}
Title: {title}
Summary: {summary or "None"}
Body snippet: {body_text or "None"}
"""

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": "You are a precise bilingual editor writing fluent Chinese and English."
                },
                {
                    "role": "user",
                    "content": prompt
                },
            ],
        )
        text = resp.choices[0].message.content.strip()

        zh = ""
        en = ""

        if "[ZH]" in text and "[EN]" in text:
            zh_part = text.split("[ZH]", 1)[1]
            zh = zh_part.split("[EN]", 1)[0].strip()
            en = text.split("[EN]", 1)[1].strip()

        if not zh or not en:
            raise ValueError("Bilingual markers not found")
        
        log(f"双语摘要成功: {title[:60]}")

        return {
            "zh": short_text(zh, MAX_SUMMARY_CHARS),
            "en": short_text(en, MAX_SUMMARY_CHARS),
        }

    except Exception as e:
        log(f"双语摘要失败，回退: {e}")
        return {
            "zh": fallback_analysis(title, summary, section),
            "en": fallback_analysis_en(section),
        }

def collect_section_items(section_name: str, section_cfg: dict, target_count: int = TARGET_ITEMS_PER_SECTION):
    all_items = []
    for feed_url in section_cfg["feeds"]:
        items = fetch_feed_items(feed_url, MAX_ITEMS_PER_FEED)
        all_items.extend(items)
        time.sleep(0.25)
    all_items = dedupe_items(all_items)
    all_items = sorted(all_items, key=lambda x: x["published_dt"], reverse=True)
    return all_items[:target_count]


def enrich_items_with_analysis(section_name: str, items):
    enriched = []
    for i, item in enumerate(items, start=1):
        log(f"{section_name} - 生成第 {i}/{len(items)} 条双语分析：{item['title'][:80]}")
        body_text = fetch_url_text(item["link"])
        analysis = gpt_bilingual_analysis(
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
            "analysis_zh": analysis["zh"],
            "analysis_en": analysis["en"],
        })
        time.sleep(0.35)
    return enriched


def build_reading_payload():
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
        log(f"开始抓取扩展阅读板块：{section_name}")
        raw_items = collect_section_items(section_name, section_cfg, TARGET_ITEMS_PER_SECTION)
        enriched_items = enrich_items_with_analysis(section_name, raw_items)
        payload["sections"][section_name] = {
            "description": section_cfg["description"],
            "count": len(enriched_items),
            "items": enriched_items,
        }

    return payload


def write_json_dual(payload: dict, latest_path: Path, history_path: Path, name: str):
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    latest_path.write_text(text, encoding="utf-8")
    history_path.write_text(text, encoding="utf-8")
    log(f"已写入 {name}: {latest_path}")
    log(f"已写入历史 {name}: {history_path}")


def write_html_dual(content: str, latest_path: Path, history_path: Path, name: str):
    latest_path.write_text(content, encoding="utf-8")
    history_path.write_text(content, encoding="utf-8")
    log(f"已写入 {name}: {latest_path}")
    log(f"已写入历史 {name}: {history_path}")


def write_reading_json(payload: dict):
    write_json_dual(
        payload,
        DOCS_DIR / "reading.json",
        TODAY_HISTORY_DIR / "reading.json",
        "reading.json",
    )


def section_html(section_name: str, section_data: dict) -> str:
    items = section_data.get("items", []) or []
    count = section_data.get("count", 0)

    html_parts = [
        '<section class="section">',
        f'  <h2>{html.escape(section_name)}</h2>',
        f'  <p class="section-meta">最近24小时内新闻：{count} 条</p>',
    ]

    if not items:
        html_parts.append('<div class="card"><p>本板块最近24小时内未抓到足够新闻。</p></div>')
    else:
        for item in items:
            analysis_zh_html = "<br>".join(
                html.escape(line.strip()) for line in item["analysis_zh"].splitlines() if line.strip()
            )
            analysis_en_html = "<br>".join(
                html.escape(line.strip()) for line in item["analysis_en"].splitlines() if line.strip()
            )

            html_parts.append(
                f"""
<div class="card">
  <div class="title-row">
    <span class="rank">{item["rank"]}.</span>
    <a href="{html.escape(item["link"])}" target="_blank" rel="noopener noreferrer">{html.escape(item["title"])}</a>
  </div>
  <div class="meta">来源：{html.escape(item["source"])} ｜ 发布时间：{html.escape(item["published_utc"])}</div>
  <div class="analysis-grid">
    <div class="analysis-col">
      <div class="analysis-label">中文</div>
      <div class="analysis">{analysis_zh_html}</div>
    </div>
    <div class="analysis-col">
      <div class="analysis-label">English</div>
      <div class="analysis">{analysis_en_html}</div>
    </div>
  </div>
</div>
""".strip()
            )

    html_parts.append("</section>")
    return "\n".join(html_parts)


def write_reading_html(payload: dict):
    generated_at = payload.get("generated_at", "")
    sections_html = "\n".join(
        section_html(section_name, section_data)
        for section_name, section_data in payload.get("sections", {}).items()
    )
    history_links = render_history_links("reading.html")

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
      max-width: 1220px;
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
      margin-bottom: 20px;
      font-size: 14px;
    }}
    .nav {{
      margin: 6px 0 18px;
    }}
    .nav a {{
      display: inline-block;
      background: #111827;
      color: white;
      text-decoration: none;
      padding: 10px 14px;
      border-radius: 10px;
      font-size: 14px;
      margin-right: 10px;
      margin-bottom: 8px;
    }}
    .history-links {{
      margin: 8px 0 28px;
    }}
    .history-links a {{
      display: inline-block;
      color: #111827;
      text-decoration: none;
      background: #eceff3;
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 13px;
      margin-right: 8px;
      margin-bottom: 8px;
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
    .analysis-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      margin-top: 8px;
    }}
    .analysis-col {{
      background: #fafafa;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
    }}
    .analysis-label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
      font-weight: 600;
    }}
    .analysis {{
      font-size: 19px;
      line-height: 1.9;
      white-space: normal;
    }}
    @media (max-width: 900px) {{
      .analysis-grid {{
        grid-template-columns: 1fr;
      }}
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

    <div class="nav">
      <a href="./index.html">返回结构监控</a>
    </div>

    {history_links}

    {sections_html}
  </div>
</body>
</html>
"""
    write_html_dual(
        html_content,
        DOCS_DIR / "reading.html",
        TODAY_HISTORY_DIR / "reading.html",
        "reading.html",
    )


# =========================
# 结构监控逻辑
# =========================

def fetch_yahoo_chart(symbol: str, range_: str = "10d", interval: str = "1d"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": range_, "interval": interval}
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def extract_last_n_valid(values, n=2):
    vals = [safe_float(x) for x in values if safe_float(x) is not None]
    if not vals:
        return []
    return vals[-n:]


def fetch_market_symbol(symbol: str):
    info = MARKET_SYMBOLS[symbol]
    try:
        data = fetch_yahoo_chart(symbol, range_="10d", interval="1d")
        result = data["chart"]["result"][0]
        meta = result.get("meta", {}) or {}
        quote = ((result.get("indicators", {}) or {}).get("quote", [{}]) or [{}])[0]
        closes = quote.get("close", []) or []

        valid_closes = extract_last_n_valid(closes, n=5)

        last_close = valid_closes[-1] if len(valid_closes) >= 1 else None
        prev_close = valid_closes[-2] if len(valid_closes) >= 2 else None
        close_3d_ago = valid_closes[-4] if len(valid_closes) >= 4 else None

        regular_market_price = safe_float(meta.get("regularMarketPrice"), last_close)
        previous_close = safe_float(meta.get("chartPreviousClose"), prev_close)

        if regular_market_price is None:
            regular_market_price = last_close
        if previous_close is None:
            previous_close = prev_close

        change = None
        change_pct = None
        if regular_market_price is not None and previous_close not in (None, 0):
            change = regular_market_price - previous_close
            change_pct = change / previous_close * 100.0

        change_3d = None
        change_3d_pct = None
        if regular_market_price is not None and close_3d_ago not in (None, 0):
            change_3d = regular_market_price - close_3d_ago
            change_3d_pct = change_3d / close_3d_ago * 100.0

        return {
            "symbol": symbol,
            "label": info["label"],
            "kind": info["kind"],
            "note": info["note"],
            "price": regular_market_price,
            "previous_close": previous_close,
            "change": change,
            "change_pct": change_pct,
            "change_3d": change_3d,
            "change_3d_pct": change_3d_pct,
            "ok": True,
        }
    except Exception as e:
        log(f"抓取市场数据失败 {symbol}: {e}")
        return {
            "symbol": symbol,
            "label": info["label"],
            "kind": info["kind"],
            "note": info["note"],
            "price": None,
            "previous_close": None,
            "change": None,
            "change_pct": None,
            "change_3d": None,
            "change_3d_pct": None,
            "ok": False,
        }


def fetch_market_snapshot():
    snapshot = {}
    for symbol in MARKET_SYMBOLS.keys():
        snapshot[MARKET_SYMBOLS[symbol]["label"]] = fetch_market_symbol(symbol)
        time.sleep(0.15)
    return snapshot


def status_text(level: str) -> str:
    mapping = {
        "normal": "正常",
        "watch": "警惕",
        "risk": "风险",
    }
    return mapping.get(level, "正常")


def classify_vix(vix_value):
    if vix_value is None:
        return "watch", {
            "zh": "VIX 数据缺失，需人工复核。",
            "en": "VIX data is missing and requires manual review.",
        }
    if vix_value < 16:
        return "normal", {
            "zh": "VIX 处于低位，说明市场波动定价仍较温和。",
            "en": "VIX remains at a low level, indicating relatively mild volatility pricing.",
        }
    if vix_value < 22:
        return "watch", {
            "zh": "VIX 已离开舒适区，说明市场对不确定性的定价开始上升。",
            "en": "VIX has moved out of the comfort zone, suggesting rising pricing of uncertainty.",
        }
    if vix_value < 30:
        return "risk", {
            "zh": "VIX 进入高波动区，通常意味着风险偏好明显收缩。",
            "en": "VIX has entered a high-volatility zone, usually signaling a clear contraction in risk appetite.",
        }
    return "risk", {
        "zh": "VIX 处于极高区间，结构上更应偏向防守。",
        "en": "VIX is at an extremely elevated level, implying a structurally defensive environment.",
    }


def classify_move(move_value):
    if move_value is None:
        return "watch", {
            "zh": "MOVE 数据缺失，需人工复核。",
            "en": "MOVE data is missing and requires manual review.",
        }
    if move_value < 90:
        return "normal", {
            "zh": "债券市场波动率较低，利率环境相对稳定。",
            "en": "Bond-market volatility is relatively low, suggesting a more stable rate environment.",
        }
    if move_value < 120:
        return "watch", {
            "zh": "债券波动率上升，说明利率预期开始不稳定。",
            "en": "Bond volatility is rising, indicating growing instability in rate expectations.",
        }
    return "risk", {
        "zh": "MOVE 已进入高波动区，利率市场出现明显压力。",
        "en": "MOVE has entered a high-volatility zone, showing clear stress in the rates market.",
    }


def classify_by_change_pct(label, change_pct, positive_good=True):
    if change_pct is None:
        return "watch", {
            "zh": f"{label} 数据缺失，需人工复核。",
            "en": f"{label} data is missing and requires manual review.",
        }

    score = change_pct if positive_good else -change_pct

    if score >= 0.5:
        return "normal", {
            "zh": f"{label} 当日表现偏强，当前方向对整体结构相对有利。",
            "en": f"{label} is relatively strong today, which is supportive for the broader structure.",
        }
    if score >= -0.5:
        return "watch", {
            "zh": f"{label} 当日变化不大，结构上更像中性或等待确认。",
            "en": f"{label} is little changed today, leaving the structure more neutral and still awaiting confirmation.",
        }
    return "risk", {
        "zh": f"{label} 当日走弱较明显，说明该方向对当前结构的支持不足。",
        "en": f"{label} is notably weaker today, suggesting insufficient support from this direction.",
    }


def classify_tnx(change_pct, price):
    if price is None or change_pct is None:
        return "watch", {
            "zh": "TNX 数据缺失，需人工复核。",
            "en": "TNX data is missing and requires manual review.",
        }
    if price >= 4.60 and change_pct > 0:
        return "risk", {
            "zh": "10年美债收益率处于高位且继续上行，对成长资产估值压力较大。",
            "en": "The 10-year Treasury yield is high and still rising, creating significant valuation pressure on growth assets.",
        }
    if change_pct <= -1.5:
        return "normal", {
            "zh": "10年美债收益率明显回落，通常有利于成长资产估值稳定。",
            "en": "The 10-year Treasury yield is falling notably, which is usually supportive for growth-asset valuations.",
        }
    if change_pct <= 0.8:
        return "watch", {
            "zh": "10年美债收益率变化有限，当前更偏中性观察。",
            "en": "The 10-year Treasury yield is little changed, leaving the current setup more neutral.",
        }
    return "risk", {
        "zh": "10年美债收益率继续抬升，需警惕对科技与高估值资产的压制。",
        "en": "The 10-year Treasury yield continues to rise, which could weigh on tech and other high-valuation assets.",
    }


def classify_hyg(change_pct):
    if change_pct is None:
        return "watch", {
            "zh": "HYG 数据缺失，需人工复核。",
            "en": "HYG data is missing and requires manual review.",
        }
    if change_pct >= 0.35:
        return "normal", {
            "zh": "HYG 偏强，说明信用风险偏好整体仍可接受。",
            "en": "HYG is relatively strong, indicating that credit risk appetite remains acceptable overall.",
        }
    if change_pct >= -0.40:
        return "watch", {
            "zh": "HYG 未给出明显方向，信用层面更偏中性。",
            "en": "HYG is not giving a clear signal, leaving credit conditions more neutral.",
        }
    return "risk", {
        "zh": "HYG 走弱较明显，说明信用偏好正在收缩。",
        "en": "HYG is weakening meaningfully, suggesting a contraction in credit appetite.",
    }


def classify_lqd(change_pct):
    if change_pct is None:
        return "watch", {
            "zh": "LQD 数据缺失，需人工复核。",
            "en": "LQD data is missing and requires manual review.",
        }
    if change_pct >= 0.15:
        return "normal", {
            "zh": "LQD 偏稳偏强，投资级信用环境暂未见明显恶化。",
            "en": "LQD is relatively stable to firm, with no clear deterioration yet in investment-grade credit conditions.",
        }
    if change_pct >= -0.25:
        return "watch", {
            "zh": "LQD 变化有限，信用环境整体中性。",
            "en": "LQD is little changed, leaving overall credit conditions neutral.",
        }
    return "risk", {
        "zh": "LQD 转弱，说明利率或信用层面的压力正在累积。",
        "en": "LQD is weakening, indicating accumulating pressure from rates and/or credit conditions.",
    }


def risk_level_to_score(level: str) -> int:
    return {"normal": 0, "watch": 1, "risk": 2}.get(level, 1)


def build_structure_monitor(snapshot: dict):
    vix_price = snapshot.get("VIX", {}).get("price")
    move_price = snapshot.get("MOVE", {}).get("price")
    spy_chg = snapshot.get("SPY", {}).get("change_pct")
    qqq_chg = snapshot.get("QQQ", {}).get("change_pct")
    gld_chg = snapshot.get("GLD", {}).get("change_pct")
    uup_chg = snapshot.get("UUP", {}).get("change_pct")
    tnx_chg = snapshot.get("TNX", {}).get("change_pct")
    tnx_price = snapshot.get("TNX", {}).get("price")
    hyg_chg = snapshot.get("HYG", {}).get("change_pct")
    lqd_chg = snapshot.get("LQD", {}).get("change_pct")

    items = {}

    level, comment = classify_vix(vix_price)
    items["VIX"] = {
        "status_level": level,
        "status": status_text(level),
        "comment_zh": comment["zh"],
        "comment_en": comment["en"],
    }

    level, comment = classify_move(move_price)
    items["MOVE"] = {
        "status_level": level,
        "status": status_text(level),
        "comment_zh": comment["zh"],
        "comment_en": comment["en"],
    }

    level, comment = classify_tnx(tnx_chg, tnx_price)
    items["TNX"] = {
        "status_level": level,
        "status": status_text(level),
        "comment_zh": comment["zh"],
        "comment_en": comment["en"],
    }

    level, comment = classify_by_change_pct("SPY", spy_chg, positive_good=True)
    items["SPY"] = {
        "status_level": level,
        "status": status_text(level),
        "comment_zh": comment["zh"],
        "comment_en": comment["en"],
    }

    level, comment = classify_by_change_pct("QQQ", qqq_chg, positive_good=True)
    items["QQQ"] = {
        "status_level": level,
        "status": status_text(level),
        "comment_zh": comment["zh"],
        "comment_en": comment["en"],
    }

    level, comment = classify_by_change_pct("GLD", gld_chg, positive_good=True)
    items["GLD"] = {
        "status_level": level,
        "status": status_text(level),
        "comment_zh": comment["zh"],
        "comment_en": comment["en"],
    }

    level, comment = classify_by_change_pct("UUP", uup_chg, positive_good=False)
    items["UUP"] = {
        "status_level": level,
        "status": status_text(level),
        "comment_zh": comment["zh"],
        "comment_en": comment["en"],
    }

    level, comment = classify_hyg(hyg_chg)
    items["HYG"] = {
        "status_level": level,
        "status": status_text(level),
        "comment_zh": comment["zh"],
        "comment_en": comment["en"],
    }

    level, comment = classify_lqd(lqd_chg)
    items["LQD"] = {
        "status_level": level,
        "status": status_text(level),
        "comment_zh": comment["zh"],
        "comment_en": comment["en"],
    }

    return items


def summarize_layer(name: str, keys: list[str], structure_monitor: dict):
    values = [structure_monitor.get(k, {}) for k in keys]
    scores = [risk_level_to_score(v.get("status_level", "watch")) for v in values]
    avg_score = sum(scores) / len(scores) if scores else 1

    if avg_score < 0.75:
        level = "normal"
        text = "正常"
        text_en = "Normal"
    elif avg_score < 1.5:
        level = "watch"
        text = "警惕"
        text_en = "Watch"
    else:
        level = "risk"
        text = "风险"
        text_en = "Risk"

    return {
        "name": name,
        "keys": keys,
        "status_level": level,
        "status": text,
        "status_en": text_en,
    }


def build_layer_summary(structure_monitor: dict):
    return {
        "波动层": summarize_layer("波动层", ["VIX"], structure_monitor),
        "利率层": summarize_layer("利率层", ["MOVE", "TNX"], structure_monitor),
        "信用层": summarize_layer("信用层", ["HYG", "LQD"], structure_monitor),
        "资产层": summarize_layer("资产层", ["QQQ", "GLD", "VLCC"], structure_monitor),
    }


def build_regime(structure_monitor: dict):
    total = 0
    for v in structure_monitor.values():
        total += risk_level_to_score(v["status_level"])

    if total <= 3:
        regime = "Risk-on"
        risk_score = 1
        summary_zh = "当前结构偏积极，波动未明显放大，信用与风险偏好总体尚可。"
        summary_en = "The current structure is constructive, with no major volatility expansion and generally acceptable credit and risk appetite."
    elif total <= 7:
        regime = "Neutral"
        risk_score = 2
        summary_zh = "当前结构偏中性，部分变量出现分化，更像震荡中的观察阶段，而不是明确单边结构。"
        summary_en = "The current structure is neutral, with some divergence across variables, suggesting more of an observation phase than a clear one-way trend."
    elif total <= 11:
        regime = "Neutral / Defensive"
        risk_score = 3
        summary_zh = "当前结构偏谨慎，说明风险偏好与估值支撑并不稳固，防守比重应适度提高。"
        summary_en = "The current structure is cautious, suggesting that risk appetite and valuation support are not firm and that defensive positioning should be moderately increased."
    else:
        regime = "Risk-off"
        risk_score = 4
        summary_zh = "当前结构偏防守，波动、利率或信用层面至少有多项指标同步施压，不宜激进扩张风险敞口。"
        summary_en = "The current structure is defensive, with simultaneous pressure from multiple indicators across volatility, rates, or credit, making aggressive risk expansion inadvisable."

    return regime, risk_score, summary_zh, summary_en, total


def summary_from_actions_bilingual(regime, risk_score, vix, move, qqq_chg, gld_chg, vlcc_chg_3d):
    zh = (
        f"当前结构为 {regime}，风险等级 {risk_score}/4；"
        f"VIX {fmt_num(vix, 2) if vix is not None else 'N/A'}，"
        f"MOVE {fmt_num(move, 2) if move is not None else 'N/A'}，"
        f"QQQ {fmt_pct(qqq_chg, 2) if qqq_chg is not None else 'N/A'}，"
        f"GLD {fmt_pct(gld_chg, 2) if gld_chg is not None else 'N/A'}，"
        f"VLCC 3天 {fmt_pct(vlcc_chg_3d, 2) if vlcc_chg_3d is not None else 'N/A'}。"
    )
    en = (
        f"The current regime is {regime} with a risk score of {risk_score}/4; "
        f"VIX {fmt_num(vix, 2) if vix is not None else 'N/A'}, "
        f"MOVE {fmt_num(move, 2) if move is not None else 'N/A'}, "
        f"QQQ {fmt_pct(qqq_chg, 2) if qqq_chg is not None else 'N/A'}, "
        f"GLD {fmt_pct(gld_chg, 2) if gld_chg is not None else 'N/A'}, "
        f"VLCC 3d {fmt_pct(vlcc_chg_3d, 2) if vlcc_chg_3d is not None else 'N/A'}."
    )
    return {"zh": zh, "en": en}


def build_actions(snapshot: dict, regime: str, risk_score: int):
    vix = snapshot.get("VIX", {}).get("price")
    move = snapshot.get("MOVE", {}).get("price")
    qqq_chg = snapshot.get("QQQ", {}).get("change_pct")
    gld_chg = snapshot.get("GLD", {}).get("change_pct")
    vlcc_chg_3d = snapshot.get("VLCC", {}).get("change_3d_pct")

    if regime == "Risk-on":
        core_zh = "核心仓可继续持有，优先保留高质量主线资产，但仍不建议在单日急涨后追高。"
        core_en = "Core positions can continue to be held, with priority on high-quality leaders, but chasing sharp single-day gains is still not advised."
        trend_zh = "趋势仓可偏向强势方向，但需要继续观察成交与后续跟随，而不是只看单日涨幅。"
        trend_en = "Trend positions can lean toward stronger areas, but follow-through and participation should still be monitored rather than relying on one-day gains."
        defense_zh = "防守仓可以维持基础配置，不必明显扩张。"
        defense_en = "Defensive positions can remain at baseline allocations without a notable expansion."
        watchlist_zh = "重点继续观察 VIX 是否维持低位，以及 MOVE、TNX 是否重新上行。"
        watchlist_en = "Continue to focus on whether VIX stays low and whether MOVE or TNX start rising again."
    elif regime == "Neutral":
        core_zh = "核心仓先不动，维持既有框架，避免因为单日波动频繁切换。"
        core_en = "Leave core positions unchanged for now and maintain the current framework rather than reacting to one-day volatility."
        trend_zh = "趋势仓更适合聚焦少数强势资产，弱势科技或高波动标的不要盲目加仓。"
        trend_en = "Trend positions should focus on a smaller set of strong assets, while avoiding blind adds to weak tech or high-volatility names."
        defense_zh = "黄金和防守仓维持原配置即可，更多是平衡而不是全面转防守。"
        defense_en = "Gold and defensive positions can remain at current levels, with more emphasis on balance than an outright defensive shift."
        watchlist_zh = "重点观察 VIX、MOVE、TNX、HYG 是否出现连续两到三天同向变化。"
        watchlist_en = "Focus on whether VIX, MOVE, TNX, and HYG show the same directional move for two to three consecutive days."
    elif regime == "Neutral / Defensive":
        core_zh = "核心仓仍以稳定为主，但要降低进攻性，优先保留确定性更高的资产。"
        core_en = "Core positions should remain stability-focused, with reduced aggressiveness and priority given to more certain assets."
        trend_zh = "趋势仓应缩小战线，避免在结构不明时追逐短线弹性。"
        trend_en = "Trend positions should narrow their focus and avoid chasing short-term upside when the structure is unclear."
        defense_zh = "黄金、防守和真实资产比重可以适度提高，用来对冲结构不确定性。"
        defense_en = "Gold, defensive holdings, and real assets can be moderately increased to hedge structural uncertainty."
        watchlist_zh = "重点观察 VIX、MOVE 是否继续抬升，以及 HYG/LQD 是否同步走弱。"
        watchlist_en = "Watch whether VIX and MOVE continue rising and whether HYG and LQD weaken together."
    else:
        core_zh = "核心仓以保守处理为主，不宜在高波动阶段扩大总风险暴露。"
        core_en = "Core positions should be handled conservatively, with no expansion of overall risk exposure during high-volatility phases."
        trend_zh = "趋势仓应明显收缩，只保留最强主线，弱势仓位优先处理。"
        trend_en = "Trend positions should be reduced meaningfully, keeping only the strongest themes and trimming weaker holdings first."
        defense_zh = "防守仓、黄金和低波动方向应明显提高权重，用于稳定组合。"
        defense_en = "Defensive positions, gold, and lower-volatility areas should carry more weight to stabilize the portfolio."
        watchlist_zh = "重点观察 VIX、MOVE、TNX、信用ETF 是否继续恶化，防止结构进一步破坏。"
        watchlist_en = "Watch closely whether VIX, MOVE, TNX, and credit ETFs continue to deteriorate, to guard against further structural damage."

    one_liner = summary_from_actions_bilingual(regime, risk_score, vix, move, qqq_chg, gld_chg, vlcc_chg_3d)

    return {
        "one_liner_zh": one_liner["zh"],
        "one_liner_en": one_liner["en"],
        "core_zh": core_zh,
        "core_en": core_en,
        "trend_zh": trend_zh,
        "trend_en": trend_en,
        "defense_zh": defense_zh,
        "defense_en": defense_en,
        "watchlist_zh": watchlist_zh,
        "watchlist_en": watchlist_en,
    }


def build_market_snapshot():
    raw = fetch_market_snapshot()
    order = ["SPY", "QQQ", "DIA", "IWM", "GLD", "SLV", "USO", "UUP", "TNX", "MOVE", "VIX", "HYG", "LQD", "VLCC"]
    out = {}
    for label in order:
        item = raw.get(label, {})
        out[label] = {
            "label": label,
            "price": item.get("price"),
            "change": item.get("change"),
            "change_pct": item.get("change_pct"),
            "change_3d": item.get("change_3d"),
            "change_3d_pct": item.get("change_3d_pct"),
            "note": item.get("note", ""),
            "ok": item.get("ok", False),
        }
    return out


def build_monitor_payload():
    generated_at = NOW_UTC.strftime("%Y-%m-%d %H:%M:%S UTC")
    market_snapshot = build_market_snapshot()
    structure_monitor = build_structure_monitor(market_snapshot)
    layer_summary = build_layer_summary(structure_monitor)
    regime, risk_score, summary_zh, summary_en, internal_score = build_regime(structure_monitor)
    actions = build_actions(market_snapshot, regime, risk_score)

    return {
        "generated_at": generated_at,
        "regime": regime,
        "risk_score": risk_score,
        "internal_score": internal_score,
        "summary_zh": summary_zh,
        "summary_en": summary_en,
        "market_snapshot": market_snapshot,
        "structure_monitor": structure_monitor,
        "layer_summary": layer_summary,
        "actions": actions,
    }


def write_monitor_json(payload: dict):
    write_json_dual(
        payload,
        DOCS_DIR / "monitor.json",
        TODAY_HISTORY_DIR / "monitor.json",
        "monitor.json",
    )


def render_snapshot_cards(snapshot: dict) -> str:
    order = ["SPY", "QQQ", "DIA", "IWM", "GLD", "SLV", "USO", "UUP", "TNX", "MOVE", "VIX", "HYG", "LQD", "VLCC"]
    cards = []
    for label in order:
        item = snapshot.get(label, {})
        cards.append(
            f"""
<div class="snap-card">
  <div class="snap-title">{html.escape(label)}</div>
  <div class="snap-price">{html.escape(fmt_num(item.get("price"), 2))}</div>
  <div class="snap-change">1天：{html.escape(fmt_pct(item.get("change_pct"), 2))}</div>
  <div class="snap-change">3天：{html.escape(fmt_pct(item.get("change_3d_pct"), 2))}</div>
  <div class="snap-note">{html.escape(item.get("note", ""))}</div>
</div>
""".strip()
        )
    return "\n".join(cards)


def render_monitor_table(structure_monitor: dict) -> str:
    order = ["VIX", "MOVE", "TNX", "SPY", "QQQ", "GLD", "UUP", "HYG", "LQD"]
    rows = []
    for key in order:
        item = structure_monitor.get(key, {})
        rows.append(
            f"""
<tr>
  <td>{html.escape(key)}</td>
  <td>{html.escape(item.get("status", ""))}</td>
  <td>{html.escape(item.get("comment_zh", ""))}</td>
  <td>{html.escape(item.get("comment_en", ""))}</td>
</tr>
""".strip()
        )
    return "\n".join(rows)


def render_layer_cards(layer_summary: dict) -> str:
    order = ["波动层", "利率层", "信用层", "资产层"]
    cards = []
    for key in order:
        item = layer_summary.get(key, {})
        cards.append(
            f"""
<div class="layer-card">
  <div class="layer-title">{html.escape(key)} / {html.escape(item.get("name", ""))}</div>
  <div class="layer-status">{html.escape(item.get("status", ""))} / {html.escape(item.get("status_en", ""))}</div>
  <div class="layer-note">{html.escape(" + ".join(item.get("keys", [])))}</div>
</div>
""".strip()
        )
    return "\n".join(cards)


def bilingual_block(zh: str, en: str) -> str:
    return (
        f'<div class="bilingual-block">'
        f'<div class="bilingual-label">中文</div>'
        f'<div class="bilingual-text">{html.escape(zh)}</div>'
        f'<div class="bilingual-label" style="margin-top:12px;">English</div>'
        f'<div class="bilingual-text">{html.escape(en)}</div>'
        f'</div>'
    )


def write_monitor_html(payload: dict):
    generated_at = payload.get("generated_at", "")
    regime = payload.get("regime", "N/A")
    risk_score = payload.get("risk_score", "N/A")
    summary_zh = payload.get("summary_zh", "")
    summary_en = payload.get("summary_en", "")
    one_liner_zh = payload.get("actions", {}).get("one_liner_zh", "")
    one_liner_en = payload.get("actions", {}).get("one_liner_en", "")
    snapshot_html = render_snapshot_cards(payload.get("market_snapshot", {}))
    monitor_rows = render_monitor_table(payload.get("structure_monitor", {}))
    layer_cards = render_layer_cards(payload.get("layer_summary", {}))
    actions = payload.get("actions", {})
    history_links = render_history_links("index.html")

    html_content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>结构监控</title>
  <style>
    :root {{
      --bg: #f7f7f8;
      --card: #ffffff;
      --text: #111827;
      --muted: #6b7280;
      --line: #e5e7eb;
      --link: #1d4ed8;
      --accent: #111827;
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
      max-width: 1280px;
      margin: 0 auto;
      padding: 28px 20px 60px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 36px;
      line-height: 1.2;
    }}
    h2 {{
      margin: 34px 0 12px;
      font-size: 28px;
      line-height: 1.25;
    }}
    .top-meta {{
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 20px;
    }}
    .nav a {{
      display: inline-block;
      background: var(--accent);
      color: white;
      text-decoration: none;
      padding: 10px 14px;
      border-radius: 10px;
      font-size: 14px;
      margin-bottom: 12px;
      margin-right: 10px;
    }}
    .history-links {{
      margin: 8px 0 24px;
    }}
    .history-links a {{
      display: inline-block;
      color: #111827;
      text-decoration: none;
      background: #eceff3;
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 13px;
      margin-right: 8px;
      margin-bottom: 8px;
    }}
    .hero {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 20px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }}
    .hero-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0,1fr));
      gap: 14px;
      margin-top: 14px;
    }}
    .hero-item {{
      background: #fafafa;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
    }}
    .hero-label {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }}
    .hero-value {{
      font-size: 20px;
      font-weight: 700;
      line-height: 1.35;
    }}
    .hero-summary {{
      margin-top: 14px;
    }}
    .bilingual-block {{
      font-size: 17px;
      line-height: 1.8;
    }}
    .bilingual-label {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 4px;
    }}
    .bilingual-text {{
      font-size: 17px;
      line-height: 1.85;
    }}
    .layer-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }}
    .layer-card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }}
    .layer-title {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }}
    .layer-status {{
      font-size: 22px;
      font-weight: 700;
      line-height: 1.3;
    }}
    .layer-note {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 6px;
    }}
    .snap-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
    }}
    .snap-card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }}
    .snap-title {{
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 6px;
    }}
    .snap-price {{
      font-size: 24px;
      font-weight: 700;
      line-height: 1.3;
    }}
    .snap-change {{
      font-size: 16px;
      margin-top: 4px;
    }}
    .snap-note {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 6px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      overflow: hidden;
      box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }}
    th, td {{
      padding: 14px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 16px;
    }}
    th {{
      background: #fafafa;
      font-size: 14px;
      color: var(--muted);
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      margin-top: 14px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }}
    .action-title {{
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    @media (max-width: 1000px) {{
      .hero-grid {{
        grid-template-columns: repeat(2, minmax(0,1fr));
      }}
    }}
    @media (max-width: 700px) {{
      .hero-grid {{
        grid-template-columns: 1fr;
      }}
      .wrap {{
        padding: 18px 14px 40px;
      }}
      h1 {{
        font-size: 30px;
      }}
      h2 {{
        font-size: 24px;
      }}
      .hero-value, .snap-price, .layer-status {{
        font-size: 22px;
      }}
      th, td {{
        font-size: 15px;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>结构监控</h1>
    <div class="top-meta">生成时间：{html.escape(generated_at)}</div>

    <div class="nav">
      <a href="./reading.html">查看每日扩展阅读</a>
    </div>

    {history_links}

    <section class="hero">
      <div class="hero-grid">
        <div class="hero-item">
          <div class="hero-label">今日结构 / Regime</div>
          <div class="hero-value">{html.escape(regime)}</div>
        </div>
        <div class="hero-item">
          <div class="hero-label">风险等级 / Risk Score</div>
          <div class="hero-value">{html.escape(str(risk_score))}/4</div>
        </div>
        <div class="hero-item">
          <div class="hero-label">一句话建议 / One-liner</div>
          {bilingual_block(one_liner_zh, one_liner_en)}
        </div>
        <div class="hero-item">
          <div class="hero-label">综合判断 / Summary</div>
          {bilingual_block(summary_zh, summary_en)}
        </div>
      </div>
      <div class="hero-summary">
        {bilingual_block(summary_zh, summary_en)}
      </div>
    </section>

    <h2>结构分层总览</h2>
    <div class="layer-grid">
      {layer_cards}
    </div>

    <h2>市场快照</h2>
    <div class="snap-grid">
      {snapshot_html}
    </div>

    <h2>结构监控核心</h2>
    <table>
      <thead>
        <tr>
          <th style="width:110px;">指标</th>
          <th style="width:100px;">状态</th>
          <th>中文说明</th>
          <th>English</th>
        </tr>
      </thead>
      <tbody>
        {monitor_rows}
      </tbody>
    </table>

    <h2>操作建议</h2>
    <div class="card">
      <div class="action-title">核心仓 / Core</div>
      {bilingual_block(actions.get("core_zh", ""), actions.get("core_en", ""))}
    </div>
    <div class="card">
      <div class="action-title">趋势仓 / Trend</div>
      {bilingual_block(actions.get("trend_zh", ""), actions.get("trend_en", ""))}
    </div>
    <div class="card">
      <div class="action-title">防守仓 / Defense</div>
      {bilingual_block(actions.get("defense_zh", ""), actions.get("defense_en", ""))}
    </div>
    <div class="card">
      <div class="action-title">观察项 / Watchlist</div>
      {bilingual_block(actions.get("watchlist_zh", ""), actions.get("watchlist_en", ""))}
    </div>
  </div>
</body>
</html>
"""
    write_html_dual(
        html_content,
        DOCS_DIR / "index.html",
        TODAY_HISTORY_DIR / "index.html",
        "index.html",
    )


# =========================
# 主流程
# =========================

def main():
    log("开始生成结构监控与每日扩展阅读")

    if not OPENAI_API_KEY:
        log("警告：未检测到 OPENAI_API_KEY，双语扩展阅读将使用兜底分析模板。")

    monitor_payload = build_monitor_payload()
    write_monitor_json(monitor_payload)
    write_monitor_html(monitor_payload)

    reading_payload = build_reading_payload()
    write_reading_json(reading_payload)
    write_reading_html(reading_payload)

    cleanup_old_history(HISTORY_KEEP_DAYS)

    log("全部完成")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise
