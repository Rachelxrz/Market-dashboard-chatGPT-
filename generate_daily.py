# -*- coding: utf-8 -*-
"""
generate_daily.py

用途：
1. 生成 daily/YYYY-MM-DD/market_brief.md
2. 生成 daily/YYYY-MM-DD/extended_reading.md
3. 生成 daily/YYYY-MM-DD/structure.md
4. 更新 daily/manifest.json

说明：
- 尽量使用公开可访问数据源（Yahoo Finance Chart API + Google News RSS）。
- 若部分抓取失败，会自动降级，不让 GitHub Action 整体失败。
- 输出内容与当前 viewer.html 的路径兼容：
    ./daily/{date}/market_brief.md
    ./daily/{date}/extended_reading.md
    ./daily/{date}/structure.md
"""

from __future__ import annotations

import json
import math
import os
import re
import textwrap
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from xml.etree import ElementTree as ET


# =========================
# 基础配置
# =========================

ROOT = Path(".")
DAILY_DIR = ROOT / "daily"
TIMEOUT = 20

# 你可以按需扩展
WATCHLIST = [
    ("QQQ", "纳指ETF"),
    ("SPY", "标普ETF"),
    ("GLD", "黄金ETF"),
    ("SLV", "白银ETF"),
    ("USO", "原油ETF"),
    ("XLE", "能源ETF"),
    ("NVDA", "英伟达"),
    ("GOOG", "谷歌"),
    ("MPWR", "Monolithic Power"),
]

# 结构监控重点指标
STRUCTURE_SYMBOLS = [
    ("^VIX", "VIX"),
    ("HYG", "高收益债(HYG)"),
    ("LQD", "投资级债(LQD)"),
    ("TLT", "长债(TLT)"),
    ("UUP", "美元代理(UUP)"),
    ("GLD", "黄金(GLD)"),
    ("USO", "原油(USO)"),
    ("QQQ", "科技风险偏好(QQQ)"),
    ("SPY", "大盘风险偏好(SPY)"),
]

# Google News RSS 查询
NEWS_QUERIES = {
    "market": [
        ("美股与宏观", "US stock market OR Federal Reserve OR Treasury yield OR inflation"),
        ("黄金与油价", "gold OR crude oil OR Brent OR WTI"),
        ("AI与科技龙头", "NVIDIA OR Google OR AI stocks"),
        ("中东与能源", "Middle East oil shipping energy"),
    ],
    "investment": [
        ("投资", "investing markets federal reserve earnings"),
    ],
    "health": [
        ("健康与抗衰老", "healthy aging longevity metabolic health"),
    ],
    "philosophy": [
        ("心理与哲学", "psychology philosophy wellbeing cognition"),
    ],
    "ai": [
        ("AI", "artificial intelligence AI model semiconductor data center"),
    ],
    "aesthetics": [
        ("美学与艺术", "art design exhibition aesthetics photography"),
    ],
}


# =========================
# 工具函数
# =========================

def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def safe_get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> Optional[requests.Response]:
    try:
        r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        return r
    except Exception:
        return None


def clean_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    s = re.sub(r"\s+", " ", s).strip()
    return s


def pct_str(v: Optional[float]) -> str:
    if v is None or math.isnan(v):
        return "N/A"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def num_str(v: Optional[float], digits: int = 2) -> str:
    if v is None or math.isnan(v):
        return "N/A"
    return f"{v:.{digits}f}"


def bullet_join(items: List[str]) -> str:
    return "\n".join(f"- {x}" for x in items)


def wrap(s: str, width: int = 100) -> str:
    return textwrap.fill(s, width=width)


# =========================
# Yahoo Finance 数据
# =========================

def fetch_yahoo_quote(symbol: str) -> Dict[str, Optional[float]]:
    """
    使用 Yahoo Finance chart endpoint 获取近 5 天数据
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {
        "range": "5d",
        "interval": "1d",
        "includePrePost": "false",
        "events": "div,splits",
    }
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = safe_get(url, params=params, headers=headers)
    if not r:
        return {
            "price": None,
            "prev_close": None,
            "change_pct": None,
            "high": None,
            "low": None,
        }

    try:
        data = r.json()
        result = data["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        closes = quote.get("close", []) or []
        highs = quote.get("high", []) or []
        lows = quote.get("low", []) or []

        valid_closes = [x for x in closes if x is not None]
        valid_highs = [x for x in highs if x is not None]
        valid_lows = [x for x in lows if x is not None]

        price = valid_closes[-1] if valid_closes else None
        prev_close = valid_closes[-2] if len(valid_closes) >= 2 else None
        change_pct = None
        if price is not None and prev_close not in (None, 0):
            change_pct = (price / prev_close - 1) * 100

        high = valid_highs[-1] if valid_highs else None
        low = valid_lows[-1] if valid_lows else None

        return {
            "price": price,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "high": high,
            "low": low,
        }
    except Exception:
        return {
            "price": None,
            "prev_close": None,
            "change_pct": None,
            "high": None,
            "low": None,
        }


def fetch_quotes(symbols: List[Tuple[str, str]]) -> Dict[str, Dict[str, Optional[float]]]:
    out = {}
    for sym, _name in symbols:
        out[sym] = fetch_yahoo_quote(sym)
    return out


# =========================
# Google News RSS
# =========================

def google_news_rss_url(query: str, hl: str = "zh-CN", gl: str = "US", ceid: str = "US:zh-Hans") -> str:
    # Google News RSS 搜索
    return f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"


def fetch_rss_items(url: str, limit: int = 10) -> List[Dict[str, str]]:
    r = safe_get(url, headers={"User-Agent": "Mozilla/5.0"})
    if not r:
        return []

    try:
        root = ET.fromstring(r.text)
        items = []
        for item in root.findall(".//item")[:limit]:
            title = clean_text(item.findtext("title", ""))
            link = clean_text(item.findtext("link", ""))
            pub = clean_text(item.findtext("pubDate", ""))
            desc = clean_text(item.findtext("description", ""))
            items.append({
                "title": title,
                "link": link,
                "pubDate": pub,
                "description": desc,
            })
        return items
    except Exception:
        return []


def fetch_news_by_queries(pairs: List[Tuple[str, str]], per_query: int = 3) -> List[Dict[str, str]]:
    all_items: List[Dict[str, str]] = []
    seen = set()

    for _label, q in pairs:
        url = google_news_rss_url(q)
        items = fetch_rss_items(url, limit=per_query + 2)
        for it in items:
            key = it["title"].strip().lower()
            if key and key not in seen:
                seen.add(key)
                all_items.append(it)

    return all_items


# =========================
# 内容生成逻辑
# =========================

def summarize_market(quotes: Dict[str, Dict[str, Optional[float]]]) -> List[str]:
    lines = []

    def q(sym: str) -> Dict[str, Optional[float]]:
        return quotes.get(sym, {})

    qqq = q("QQQ")
    spy = q("SPY")
    gld = q("GLD")
    uso = q("USO")
    xle = q("XLE")
    nvda = q("NVDA")
    goog = q("GOOG")
    mpwr = q("MPWR")

    lines.append(
        f"美股风险资产方面，QQQ报 {num_str(qqq.get('price'))}，日变动 {pct_str(qqq.get('change_pct'))}；"
        f"SPY报 {num_str(spy.get('price'))}，日变动 {pct_str(spy.get('change_pct'))}。"
        f"这能帮助快速判断当天市场对成长股与大盘的偏好强弱。"
    )

    lines.append(
        f"避险与大宗方面，GLD报 {num_str(gld.get('price'))}，日变动 {pct_str(gld.get('change_pct'))}；"
        f"USO报 {num_str(uso.get('price'))}，日变动 {pct_str(uso.get('change_pct'))}；"
        f"XLE报 {num_str(xle.get('price'))}，日变动 {pct_str(xle.get('change_pct'))}。"
        f"黄金、原油与能源股三者的同步或背离，往往能反映市场对通胀、地缘与增长预期的变化。"
    )

    lines.append(
        f"你较关注的科技股里，NVDA报 {num_str(nvda.get('price'))}，日变动 {pct_str(nvda.get('change_pct'))}；"
        f"GOOG报 {num_str(goog.get('price'))}，日变动 {pct_str(goog.get('change_pct'))}；"
        f"MPWR报 {num_str(mpwr.get('price'))}，日变动 {pct_str(mpwr.get('change_pct'))}。"
        f"若龙头与指数同向，说明趋势较健康；若龙头显著弱于指数，则要防范结构性松动。"
    )

    return lines


def structure_assessment(quotes: Dict[str, Dict[str, Optional[float]]]) -> Tuple[str, List[str]]:
    def get(sym: str, field: str = "price") -> Optional[float]:
        return quotes.get(sym, {}).get(field)

    vix = get("^VIX")
    hyg = get("HYG")
    lqd = get("LQD")
    tlt = get("TLT")
    uup = get("UUP")
    gld = get("GLD")
    uso = get("USO")
    qqq = get("QQQ")
    spy = get("SPY")

    score = 0
    reasons = []

    # VIX
    if vix is not None:
        if vix < 18:
            score += 2
            reasons.append(f"VIX={vix:.2f}，波动压力较低。")
        elif vix < 24:
            score += 1
            reasons.append(f"VIX={vix:.2f}，仍处可控区间。")
        elif vix < 30:
            reasons.append(f"VIX={vix:.2f}，市场开始进入偏谨慎状态。")
        else:
            score -= 2
            reasons.append(f"VIX={vix:.2f}，波动显著抬升，需警惕风险扩散。")
    else:
        reasons.append("VIX 数据缺失。")

    # HYG vs LQD
    if hyg is not None and lqd is not None:
        ratio = hyg / lqd if lqd else None
        if ratio is not None:
            if ratio > 0.82:
                score += 1
                reasons.append(f"HYG/LQD≈{ratio:.3f}，信用偏好仍在。")
            elif ratio > 0.80:
                reasons.append(f"HYG/LQD≈{ratio:.3f}，信用环境中性。")
            else:
                score -= 1
                reasons.append(f"HYG/LQD≈{ratio:.3f}，信用承受力偏弱。")
    else:
        reasons.append("HYG/LQD 数据不完整。")

    # 美元
    if uup is not None:
        if uup < 29.5:
            score += 1
            reasons.append(f"UUP={uup:.2f}，美元压力不强。")
        elif uup > 30.5:
            score -= 1
            reasons.append(f"UUP={uup:.2f}，美元偏强会压制风险资产。")

    # 黄金/原油
    if gld is not None and uso is not None:
        if gld > 0 and uso > 0:
            reasons.append(f"黄金(GLD)={gld:.2f}，原油(USO)={uso:.2f}，可用于观察避险与通胀线索。")

    # 指数方向
    qqq_chg = quotes.get("QQQ", {}).get("change_pct")
    spy_chg = quotes.get("SPY", {}).get("change_pct")
    if qqq_chg is not None and spy_chg is not None:
        if qqq_chg > 0 and spy_chg > 0:
            score += 1
            reasons.append("QQQ 与 SPY 同步走强，风险偏好较稳定。")
        elif qqq_chg < 0 and spy_chg < 0:
            score -= 1
            reasons.append("QQQ 与 SPY 同步走弱，需观察是否演变为连续性回撤。")

    # TLT
    if tlt is not None:
        reasons.append(f"TLT={tlt:.2f}，可作为长端利率与避险方向的观察代理。")

    if score >= 3:
        regime = "偏风险开启（Risk-On）"
    elif score >= 1:
        regime = "中性偏积极"
    elif score >= -1:
        regime = "中性偏谨慎"
    else:
        regime = "偏防御（Risk-Off）"

    return regime, reasons


def format_news_section(items: List[Dict[str, str]], max_items: int = 10) -> str:
    if not items:
        return "- 今日未成功抓取到新闻，可稍后重跑 workflow。\n"

    lines = []
    for idx, it in enumerate(items[:max_items], start=1):
        title = it.get("title", "").strip()
        link = it.get("link", "").strip()
        pub = it.get("pubDate", "").strip()
        desc = it.get("description", "").strip()

        desc_short = desc[:180] + ("..." if len(desc) > 180 else "")
        if link:
            lines.append(f"{idx}. [{title}]({link})")
        else:
            lines.append(f"{idx}. {title}")
        if pub:
            lines.append(f"   - 时间：{pub}")
        if desc_short:
            lines.append(f"   - 摘要：{desc_short}")

    return "\n".join(lines) + "\n"


def build_market_brief(date_str: str, quotes: Dict[str, Dict[str, Optional[float]]], news_items: List[Dict[str, str]]) -> str:
    market_summary = summarize_market(quotes)

    quote_lines = []
    for sym, name in WATCHLIST:
        d = quotes.get(sym, {})
        quote_lines.append(
            f"- **{name} / {sym}**：{num_str(d.get('price'))} "
            f"（日变动 {pct_str(d.get('change_pct'))}）"
        )

    commentary = [
        "今天先看三条主线：科技风险偏好、黄金与油价的方向、以及能源链是否重新获得相对强势。",
        "如果科技指数与龙头个股同步上行，通常说明市场仍接受成长叙事；若指数平稳但龙头走弱，则更像内部结构轮动而非全面风险上升。",
        "如果黄金、原油、能源股同时偏强，往往意味着市场在重新交易通胀、地缘风险或供给扰动。"
    ]

    return f"""# 今日市场简报

日期：{date_str}

生成时间：{now_utc_iso()}

## 市场概览

{chr(10).join(f"- {x}" for x in market_summary)}

## 重点资产快照

{chr(10).join(quote_lines)}

## 今日观察

{chr(10).join(f"{i+1}. {x}" for i, x in enumerate(commentary))}

## 今日新闻追踪

{format_news_section(news_items, max_items=10)}

## ChatGPT 简评

今天的简报重点不是预测单日涨跌，而是识别**结构是否延续**。  
你比较重视“结构监控”，所以最关键的不是某一条新闻本身，而是：  
1. 科技与大盘是否同向；  
2. 黄金与能源是否同步变强；  
3. 龙头是否弱于指数。  

若这三者里出现两项以上恶化，就要从“正常波动”切换到“结构审视”模式。
"""


def build_extended_reading(date_str: str, sections: Dict[str, List[Dict[str, str]]]) -> str:
    def build_panel(title: str, items: List[Dict[str, str]]) -> str:
        if not items:
            return f"""## {title}

1. 今日该板块未成功抓取到公开新闻源。  
2. 这通常不是页面问题，而是新闻源临时不可访问或返回为空。  
3. 可直接手动重新运行 GitHub Action 再试一次。  
4. 在系统完全稳定前，建议把它理解为“可容错的自动草稿层”。  
5. 等后续接入更稳定的数据源后，这一板块会更完整。  

### ChatGPT 综合评论
这个板块今天没有足够内容，不建议据此做判断，更适合作为提醒你稍后补查的占位区。
"""

        lines = [f"## {title}", ""]
        for i, it in enumerate(items[:10], start=1):
            title_text = it.get("title", "").strip()
            link = it.get("link", "").strip()
            desc = it.get("description", "").strip()[:220]
            pub = it.get("pubDate", "").strip()

            if link:
                lines.append(f"{i}. [{title_text}]({link})")
            else:
                lines.append(f"{i}. {title_text}")

            s1 = "这条信息值得注意，因为它能帮助你快速感知该板块今天最主要的讨论方向。"
            s2 = f"从公开新闻摘要看，核心线索是：{desc or '该条目主要反映近期该领域的新变化。'}"
            s3 = "你可以把它当作进一步深挖的入口，而不是最终结论，因为新闻标题往往放大单一角度。"
            s4 = "真正有价值的做法，是连续几天观察这些主题是否反复出现，从而识别趋势而不是噪音。"
            s5 = f"发布时间：{pub}" if pub else "发布时间信息本次未获取到。"

            lines.append(f"   - {s1}")
            lines.append(f"   - {s2}")
            lines.append(f"   - {s3}")
            lines.append(f"   - {s4}")
            lines.append(f"   - {s5}")
            lines.append("")

        lines.append("### ChatGPT 综合评论")
        lines.append(
            f"{title}板块今天的价值，不在于“看完10条新闻”，而在于识别哪些主题在重复出现。"
            "重复出现的主题，往往比单条爆点更值得进入你的长期知识系统。"
        )
        lines.append("")
        return "\n".join(lines)

    return f"""# 每日扩展阅读

日期：{date_str}

生成时间：{now_utc_iso()}

{build_panel("投资", sections.get("investment", []))}

{build_panel("健康", sections.get("health", []))}

{build_panel("心理/哲学", sections.get("philosophy", []))}

{build_panel("AI", sections.get("ai", []))}

{build_panel("美学", sections.get("aesthetics", []))}
"""


def build_structure(date_str: str, quotes: Dict[str, Dict[str, Optional[float]]]) -> str:
    regime, reasons = structure_assessment(quotes)

    metric_lines = []
    for sym, label in STRUCTURE_SYMBOLS:
        d = quotes.get(sym, {})
        metric_lines.append(
            f"- **{label} / {sym}**：{num_str(d.get('price'))} "
            f"（日变动 {pct_str(d.get('change_pct'))}）"
        )

    suggestion = []
    if "Risk-On" in regime or "中性偏积极" in regime:
        suggestion.extend([
            "当前更接近“可持有、可观察”的环境，但仍要盯住龙头股是否持续强于指数。",
            "若科技指数与能源、黄金同时上涨，通常代表宏观驱动更复杂，仓位不宜过度单边。",
            "对你而言，可以把重点放在‘趋势是否延续’而不是单日新闻刺激。"
        ])
    elif "中性偏谨慎" in regime:
        suggestion.extend([
            "当前更适合控制节奏，不适合在高波动背景下追价。",
            "若接下来几天 VIX 抬升、HYG/LQD转弱、QQQ持续弱于SPY，就要进一步提高防御意识。",
            "这种环境里，最怕的是把正常回撤误判成机会，或者把结构破坏误判成短暂噪音。"
        ])
    else:
        suggestion.extend([
            "当前更偏防御，核心任务是先确认风险是否扩散，而不是急于抄底。",
            "若 VIX 高位、信用代理走弱、科技领跌同时出现，说明市场在重新定价风险。",
            "这种情况下，结构性收缩仓位、保留流动性和等待确认，通常比激进加仓更重要。"
        ])

    return f"""# 结构监控

日期：{date_str}

生成时间：{now_utc_iso()}

## 风险状态判断

**当前判断：{regime}**

## 核心指标快照

{chr(10).join(metric_lines)}

## 指标解读

{chr(10).join(f"- {x}" for x in reasons)}

## 今日结构建议

{chr(10).join(f"{i+1}. {x}" for i, x in enumerate(suggestion))}

## 你关心的监控思路

1. 先看 **VIX** 是否明显抬升。  
2. 再看 **HYG / LQD** 是否转弱，作为信用风险代理。  
3. 再看 **QQQ、SPY 与龙头股** 是否同步。  
4. 最后看 **黄金、美元、原油** 是否在传递新的宏观信号。  

如果上述四项里，连续两到三项同时恶化，就不再只是“普通波动”，而更像结构在松动。
"""


# =========================
# manifest.json
# =========================

def update_manifest(date_str: str) -> None:
    ensure_dir(DAILY_DIR)
    manifest_path = DAILY_DIR / "manifest.json"

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    else:
        manifest = {}

    dates = manifest.get("dates", [])
    if date_str not in dates:
        dates.append(date_str)

    dates = sorted(set(dates), reverse=True)

    manifest["latest"] = date_str
    manifest["dates"] = dates

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# =========================
# 主流程
# =========================

def main() -> None:
    date_str = today_str()
    out_dir = DAILY_DIR / date_str
    ensure_dir(out_dir)
    ensure_dir(DAILY_DIR)

    # 1) 行情
    all_symbols = list({sym: name for sym, name in (WATCHLIST + STRUCTURE_SYMBOLS)}.items())
    quotes = fetch_quotes(all_symbols)

    # 2) 市场新闻
    market_news = fetch_news_by_queries(NEWS_QUERIES["market"], per_query=3)

    # 3) 扩展阅读新闻
    ext_sections = {}
    for key in ["investment", "health", "philosophy", "ai", "aesthetics"]:
        ext_sections[key] = fetch_news_by_queries(NEWS_QUERIES[key], per_query=10)

    # 4) 生成 markdown
    market_md = build_market_brief(date_str, quotes, market_news)
    reading_md = build_extended_reading(date_str, ext_sections)
    structure_md = build_structure(date_str, quotes)

    (out_dir / "market_brief.md").write_text(market_md, encoding="utf-8")
    (out_dir / "extended_reading.md").write_text(reading_md, encoding="utf-8")
    (out_dir / "structure.md").write_text(structure_md, encoding="utf-8")

    # 5) 更新 manifest
    update_manifest(date_str)

    print(f"[OK] Generated daily files for {date_str}")
    print(f"[OK] Output dir: {out_dir}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[FATAL] generate_daily.py failed")
        print(str(e))
        traceback.print_exc()
        raise
