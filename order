#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_daily.py

输出：
1. docs/index.html       -> 结构监控主页
2. docs/monitor.json     -> 结构监控数据
3. docs/reading.html     -> 每日扩展阅读
4. docs/reading.json     -> 每日扩展阅读数据

环境变量：
- OPENAI_API_KEY   可选；没有时阅读页会用兜底分析
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
    "Chrome/124.0 Safari/537.36 DailyDashboardBot/2.0"
)

REQUEST_TIMEOUT = 20
TARGET_ITEMS_PER_SECTION = 10
MAX_ITEMS_PER_FEED = 20
MAX_SUMMARY_CHARS = 900

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
VLCC_PROXY_SYMBOL = os.getenv("VLCC_PROXY_SYMBOL", "DHT").strip().upper()
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
        "note": f"VLCC 航运代理（当前: {VLCC_PROXY_SYMBOL}）"
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
    if not dt:
        return False
    return dt >= CUTOFF_UTC


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
    parts = []
    parts.append(f"这条{section}新闻反映了最近24小时内该板块的一个具体变化。")
    if summary:
        parts.append(f"从公开摘要看，核心线索是：{short_text(summary, 150)}")
    else:
        parts.append(f"从标题看，当前讨论重点集中在“{title}”这一主题。")
    parts.append("更重要的是观察这一主题是否会在接下来几天持续发酵，并进一步影响预期、估值或市场情绪。")
    parts.append("如果同类信息连续出现，它更可能代表趋势，而不是一次性噪音。")
    return "\n".join(parts[:4])


def gpt_analysis(title: str, summary: str, body_text: str, section: str, source: str) -> str:
    if not client:
        return fallback_analysis(title, summary, section)

    prompt = f"""
你是一位严谨的中文资讯编辑。请根据下面新闻信息，写出 3-5 句话的中文分析。

要求：
1. 必须是中文。
2. 必须是 3-5 句完整句子。
3. 不要空话，不要重复标题，不要写模板句。
4. 要回答：发生了什么、为什么重要、可能影响什么。
5. 面向高信息密度读者，语言清晰、克制、具体。
6. 不要编造未提供的事实；不确定时用“从目前公开信息看”之类表述。
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
                {"role": "system", "content": "你是一个严谨、克制、中文表达自然的新闻分析助手。"},
                {"role": "user", "content": prompt},
            ],
        )
        text = resp.choices[0].message.content.strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        return short_text(text, MAX_SUMMARY_CHARS)
    except Exception as e:
        log(f"OpenAI 摘要失败，回退模板: {e}")
        return fallback_analysis(title, summary, section)


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


def write_reading_json(payload: dict):
    path = DOCS_DIR / "reading.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已写入 reading.json: {path}")


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


def write_reading_html(payload: dict):
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
    .nav {{
      margin: 6px 0 26px;
    }}
    .nav a {{
      display: inline-block;
      background: #111827;
      color: white;
      text-decoration: none;
      padding: 10px 14px;
      border-radius: 10px;
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
    <div class="nav"><a href="./index.html">返回结构监控</a></div>
    {sections_html}
  </div>
</body>
</html>
"""
    path = DOCS_DIR / "reading.html"
    path.write_text(html_content, encoding="utf-8")
    log(f"已写入 reading.html: {path}")


# =========================
# 结构监控逻辑
# =========================

def fetch_yahoo_chart(symbol: str, range_: str = "5d", interval: str = "1d"):
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
        return "watch", "VIX 数据缺失，需人工复核。"

    if vix_value < 16:
        return "normal", "VIX 处于低位，说明市场波动定价仍较温和。"
    if vix_value < 22:
        return "watch", "VIX 已离开舒适区，说明市场对不确定性的定价开始上升。"
    if vix_value < 30:
        return "risk", "VIX 进入高波动区，通常意味着风险偏好明显收缩。"
    return "risk", "VIX 处于极高区间，结构上更应偏向防守。"

    def classify_move(move_value):
    if move_value is None:
        return "watch", "MOVE 数据缺失。"

    if move_value < 90:
        return "normal", "债券市场波动率较低，利率环境稳定。"

    if move_value < 120:
        return "watch", "债券波动率上升，说明利率预期开始不稳定。"

    return "risk", "MOVE 已进入高波动区，利率市场出现明显压力。"


def classify_by_change_pct(label, change_pct, positive_good=True):
    if change_pct is None:
        return "watch", f"{label} 数据缺失，需人工复核。"

    score = change_pct if positive_good else -change_pct

    if score >= 0.5:
        return "normal", f"{label} 当日表现偏强，当前方向对整体结构相对有利。"
    if score >= -0.5:
        return "watch", f"{label} 当日变化不大，结构上更像中性或等待确认。"
    return "risk", f"{label} 当日走弱较明显，说明该方向对当前结构的支持不足。"


def classify_tnx(change_pct, price):
    if price is None or change_pct is None:
        return "watch", "TNX 数据缺失，需人工复核。"

    if price >= 4.60 and change_pct > 0:
        return "risk", "10年美债收益率处于高位且继续上行，对成长资产估值压力较大。"
    if change_pct <= -1.5:
        return "normal", "10年美债收益率明显回落，通常有利于成长资产估值稳定。"
    if change_pct <= 0.8:
        return "watch", "10年美债收益率变化有限，当前更偏中性观察。"
    return "risk", "10年美债收益率继续抬升，需警惕对科技与高估值资产的压制。"


def classify_hyg(change_pct):
    if change_pct is None:
        return "watch", "HYG 数据缺失，需人工复核。"

    if change_pct >= 0.35:
        return "normal", "HYG 偏强，说明信用风险偏好整体仍可接受。"
    if change_pct >= -0.40:
        return "watch", "HYG 未给出明显方向，信用层面更偏中性。"
    return "risk", "HYG 走弱较明显，说明信用偏好正在收缩。"


def classify_lqd(change_pct):
    if change_pct is None:
        return "watch", "LQD 数据缺失，需人工复核。"

    if change_pct >= 0.15:
        return "normal", "LQD 偏稳偏强，投资级信用环境暂未见明显恶化。"
    if change_pct >= -0.25:
        return "watch", "LQD 变化有限，信用环境整体中性。"
    return "risk", "LQD 转弱，说明利率或信用层面的压力正在累积。"


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
        "comment": comment
    }

    level, comment = classify_move(move_price)
    items["MOVE"] = {
        "status_level": level,
        "status": status_text(level),
        "comment": comment
    }

    level, comment = classify_by_change_pct("SPY", spy_chg, positive_good=True)
    items["SPY"] = {
        "status_level": level,
        "status": status_text(level),
        "comment": comment
    }

    level, comment = classify_by_change_pct("QQQ", qqq_chg, positive_good=True)
    items["QQQ"] = {
        "status_level": level,
        "status": status_text(level),
        "comment": comment
    }

    level, comment = classify_by_change_pct("GLD", gld_chg, positive_good=True)
    items["GLD"] = {
        "status_level": level,
        "status": status_text(level),
        "comment": comment
    }

    level, comment = classify_by_change_pct("UUP", uup_chg, positive_good=False)
    items["UUP"] = {
        "status_level": level,
        "status": status_text(level),
        "comment": comment
    }

    level, comment = classify_tnx(tnx_chg, tnx_price)
    items["TNX"] = {
        "status_level": level,
        "status": status_text(level),
        "comment": comment
    }

    level, comment = classify_hyg(hyg_chg)
    items["HYG"] = {
        "status_level": level,
        "status": status_text(level),
        "comment": comment
    }

    level, comment = classify_lqd(lqd_chg)
    items["LQD"] = {
        "status_level": level,
        "status": status_text(level),
        "comment": comment
    }

    return items

def build_regime(structure_monitor: dict):
    total = 0
    for v in structure_monitor.values():
    total += risk_level_to_score(v["status_level"])
    
    # 0-3 低风险；4-7 中性；8+ 风险偏高
    if total <= 3:
        regime = "Risk-on"
        risk_score = 1
        summary = "当前结构偏积极，波动未明显放大，信用与风险偏好总体尚可。"
    elif total <= 7:
        regime = "Neutral"
        risk_score = 2
        summary = "当前结构偏中性，部分变量出现分化，更像震荡中的观察阶段，而不是明确单边结构。"
    elif total <= 11:
        regime = "Neutral / Defensive"
        risk_score = 3
        summary = "当前结构偏谨慎，说明风险偏好与估值支撑并不稳固，防守比重应适度提高。"
    else:
        regime = "Risk-off"
        risk_score = 4
        summary = "当前结构偏防守，波动、利率或信用层面至少有多项指标同步施压，不宜激进扩张风险敞口。"

    return regime, risk_score, summary, total


def build_actions(snapshot: dict, regime: str, risk_score: int):
    vix = snapshot.get("VIX", {}).get("price")
    qqq_chg = snapshot.get("QQQ", {}).get("change_pct")
    gld_chg = snapshot.get("GLD", {}).get("change_pct")
    vlcc_chg_3d = snapshot.get("VLCC", {}).get("change_3d_pct")

    if regime == "Risk-on":
        core = "核心仓可继续持有，优先保留高质量主线资产，但仍不建议在单日急涨后追高。"
        trend = "趋势仓可偏向强势方向，但需要继续观察成交与后续跟随，而不是只看单日涨幅。"
        defense = "防守仓可以维持基础配置，不必明显扩张。"
        watchlist = "重点继续观察 VIX 是否维持低位，以及 TNX 是否重新上行。"
    elif regime == "Neutral":
        core = "核心仓先不动，维持既有框架，避免因为单日波动频繁切换。"
        trend = "趋势仓更适合聚焦少数强势资产，弱势科技或高波动标的不要盲目加仓。"
        defense = "黄金和防守仓维持原配置即可，更多是平衡而不是全面转防守。"
        watchlist = "重点观察 VIX、TNX、HYG 是否出现连续两到三天同向变化。"
    elif regime == "Neutral / Defensive":
        core = "核心仓仍以稳定为主，但要降低进攻性，优先保留确定性更高的资产。"
        trend = "趋势仓应缩小战线，避免在结构不明时追逐短线弹性。"
        defense = "黄金、防守和真实资产比重可以适度提高，用来对冲结构不确定性。"
        watchlist = "重点观察 VIX 是否继续抬升，以及 HYG/LQD 是否同步走弱。"
    else:
        core = "核心仓以保守处理为主，不宜在高波动阶段扩大总风险暴露。"
        trend = "趋势仓应明显收缩，只保留最强主线，弱势仓位优先处理。"
        defense = "防守仓、黄金和低波动方向应明显提高权重，用于稳定组合。"
        watchlist = "重点观察 VIX、TNX、信用ETF 是否继续恶化，防止结构进一步破坏。"

    one_liner = summary_from_actions(regime, risk_score, vix, qqq_chg, gld_chg, vlcc_chg_3d)

    return {
        "one_liner": one_liner,
        "core": core,
        "trend": trend,
        "defense": defense,
        "watchlist": watchlist,
    }


def summary_from_actions(regime, risk_score, vix, qqq_chg, gld_chg, vlcc_chg_3d):
    vix_text = f"VIX {fmt_num(vix, 2)}" if vix is not None else "VIX 待确认"
    qqq_text = f"QQQ {fmt_pct(qqq_chg, 2)}" if qqq_chg is not None else "QQQ 待确认"
    gld_text = f"GLD {fmt_pct(gld_chg, 2)}" if gld_chg is not None else "GLD 待确认"
    vlcc_text = f"VLCC 3天 {fmt_pct(vlcc_chg_3d, 2)}" if vlcc_chg_3d is not None else "VLCC 待确认"
    return f"当前结构为 {regime}，风险等级 {risk_score}/4；{vix_text}，{qqq_text}，{gld_text}，{vlcc_text}。"

def build_market_snapshot():
    raw = fetch_market_snapshot()
    order = ["SPY","QQQ","DIA","IWM","GLD","SLV","USO","UUP","TNX","MOVE","VIX","HYG","LQD","VLCC"]
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
    regime, risk_score, summary, internal_score = build_regime(structure_monitor)
    actions = build_actions(market_snapshot, regime, risk_score)

    payload = {
        "generated_at": generated_at,
        "regime": regime,
        "risk_score": risk_score,
        "internal_score": internal_score,
        "summary": summary,
        "market_snapshot": market_snapshot,
        "structure_monitor": structure_monitor,
        "actions": actions,
    }
    return payload


def write_monitor_json(payload: dict):
    path = DOCS_DIR / "monitor.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已写入 monitor.json: {path}")


def render_snapshot_cards(snapshot: dict) -> str:
    order = ["SPY", "QQQ", "DIA", "IWM", "GLD", "SLV", "USO", "UUP", "TNX", "VIX", "HYG", "LQD", "VLCC"]
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
    order = ["VIX","MOVE","TNX","SPY","QQQ","GLD","UUP","HYG","LQD"]
    rows = []
    for key in order:
        item = structure_monitor.get(key, {})
        rows.append(
            f"""
<tr>
  <td>{html.escape(key)}</td>
  <td>{html.escape(item.get("status", ""))}</td>
  <td>{html.escape(item.get("comment", ""))}</td>
</tr>
""".strip()
        )
    return "\n".join(rows)


def write_monitor_html(payload: dict):
    generated_at = payload.get("generated_at", "")
    regime = payload.get("regime", "N/A")
    risk_score = payload.get("risk_score", "N/A")
    summary = payload.get("summary", "")
    one_liner = payload.get("actions", {}).get("one_liner", "")
    snapshot_html = render_snapshot_cards(payload.get("market_snapshot", {}))
    monitor_rows = render_monitor_table(payload.get("structure_monitor", {}))
    actions = payload.get("actions", {})

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
      max-width: 1180px;
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
      margin-bottom: 22px;
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
      font-size: 18px;
      line-height: 1.8;
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
      margin-bottom: 6px;
    }}
    .action-text {{
      font-size: 18px;
      line-height: 1.8;
    }}
    @media (max-width: 900px) {{
      .hero-grid, .snap-grid {{ grid-template-columns: repeat(2, minmax(0,1fr)); }}
    }}
    @media (max-width: 640px) {{
      .wrap {{ padding: 18px 14px 40px; }}
      h1 {{ font-size: 30px; }}
      h2 {{ font-size: 24px; }}
      .hero-grid, .snap-grid {{ grid-template-columns: 1fr; }}
      .hero-value, .snap-price {{ font-size: 22px; }}
      .hero-summary, .action-text {{ font-size: 17px; }}
      th, td {{ font-size: 15px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>结构监控</h1>
    <div class="top-meta">生成时间：{html.escape(generated_at)}</div>
    <div class="nav"><a href="./reading.html">查看每日扩展阅读</a></div>

    <section class="hero">
      <div class="hero-grid">
        <div class="hero-item">
          <div class="hero-label">今日结构</div>
          <div class="hero-value">{html.escape(regime)}</div>
        </div>
        <div class="hero-item">
          <div class="hero-label">风险等级</div>
          <div class="hero-value">{html.escape(str(risk_score))}/4</div>
        </div>
        <div class="hero-item">
          <div class="hero-label">一句话建议</div>
          <div class="hero-value" style="font-size:17px;font-weight:600;">{html.escape(one_liner)}</div>
        </div>
        <div class="hero-item">
          <div class="hero-label">综合判断</div>
          <div class="hero-value" style="font-size:17px;font-weight:600;">{html.escape(summary)}</div>
        </div>
      </div>
      <div class="hero-summary">{html.escape(summary)}</div>
    </section>

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
          <th>说明</th>
        </tr>
      </thead>
      <tbody>
        {monitor_rows}
      </tbody>
    </table>

    <h2>操作建议</h2>
    <div class="card">
      <div class="action-title">核心仓</div>
      <div class="action-text">{html.escape(actions.get("core", ""))}</div>
    </div>
    <div class="card">
      <div class="action-title">趋势仓</div>
      <div class="action-text">{html.escape(actions.get("trend", ""))}</div>
    </div>
    <div class="card">
      <div class="action-title">防守仓</div>
      <div class="action-text">{html.escape(actions.get("defense", ""))}</div>
    </div>
    <div class="card">
      <div class="action-title">观察项</div>
      <div class="action-text">{html.escape(actions.get("watchlist", ""))}</div>
    </div>
  </div>
</body>
</html>
"""
    path = DOCS_DIR / "index.html"
    path.write_text(html_content, encoding="utf-8")
    log(f"已写入 index.html: {path}")


# =========================
# 主流程
# =========================

def main():
    log("开始生成结构监控与每日扩展阅读")

    if not OPENAI_API_KEY:
        log("警告：未检测到 OPENAI_API_KEY，扩展阅读将使用兜底分析模板。")

    # 1) 结构监控主页
    monitor_payload = build_monitor_payload()
    write_monitor_json(monitor_payload)
    write_monitor_html(monitor_payload)

    # 2) 每日扩展阅读
    reading_payload = build_reading_payload()
    write_reading_json(reading_payload)
    write_reading_html(reading_payload)

    log("全部完成")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise





