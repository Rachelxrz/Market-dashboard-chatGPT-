#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_daily.py (Version 2.0 - Compatible Signature)

目标：
- 每天生成：
  1) daily/YYYY-MM-DD/structure.md   # 结构监控（含策略建议）
  2) daily/YYYY-MM-DD/extended.md    # 每日扩展阅读 Strict
  3) daily/YYYY-MM-DD/metrics.json   # 机器可读指标（含抓取状态与缺失字段）
- 更新 manifest.json（用于首页列表）

关键修复：
- gen_structure_md / gen_extended_md 使用“向后兼容签名”（B方案）：
  def gen_structure_md(date, metrics, risk, news=None, debug_bucket=None)
  def gen_extended_md(date, metrics, risk, news=None, debug_bucket=None)
  -> 即便 main() 仍按旧方式调用也不会 TypeError
- 结构监控：严格只用“已计算数据”（metrics/risk/news），不胡编；缺数据自动降级
- 新闻：默认 RSS 抓取；抓不到则降级但仍输出可读内容（不会整页空白）

依赖：
  pip install openai requests feedparser pandas yfinance
"""

import os
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

import requests
import feedparser
import pandas as pd
import yfinance as yf

from openai import OpenAI


# =========================
# 配置区（你可按需改）
# =========================

# 你要监控的“标签 -> 具体可抓 ticker”
# 注意：VIX 用 ^VIX（yfinance 支持），10Y 可以用 ^TNX（yfinance 支持）
TICKERS = {
    "VIX": "^VIX",
    "SPY": "SPY",
    "QQQ": "QQQ",
    "GLD": "GLD",
    "HYG": "HYG",
    "LQD": "LQD",
    # 可选：美元代理（也可换 DXY 的替代，比如 UUP）
    "UUP": "UUP",
    # 可选：10Y 代理
    "TNX": "^TNX",
}

# RSS 新闻源（可加减）
RSS_FEEDS = [
    # 综合财经
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",      # WSJ Markets
    "https://www.ft.com/?format=rss",                    # FT (可能有地区限制/不稳定)
    "https://www.reuters.com/rssFeed/businessNews",      # Reuters business
    "https://www.bloomberg.com/feed/podcast/etf-report.xml",  # Bloomberg 示例（可能不稳定）
    # 科技/AI
    "https://www.theverge.com/rss/index.xml",
    "https://www.wired.com/feed/rss",
    # 健康/科学
    "https://www.sciencedaily.com/rss/health_medicine.xml",
    "https://www.sciencedaily.com/rss/mind_brain.xml",
    # 文化/美学（可选）
    "https://www.designboom.com/feed/",
]

# 生成文件输出位置（仓库根目录下）
REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = REPO_ROOT / "daily"
MANIFEST = REPO_ROOT / "manifest.json"

# OpenAI
OPENAI_MODEL_STRUCTURE = "gpt-4.1"
OPENAI_MODEL_EXTENDED = "gpt-5"

# token 控制（按你习惯）
STRUCTURE_MAX_TOKENS = 1400
EXTENDED_MAX_TOKENS = 2600

# yfinance 抓取窗口
LOOKBACK_DAYS = 90  # 用于算3/5/10日变化与SMA，留 buffer

# 网络重试（RSS / yfinance）
HTTP_TIMEOUT = 15
HTTP_RETRIES = 2


# =========================
# 工具函数
# =========================

def to_jsonable(obj):
    """Make objects JSON-serializable (OpenAI SDK / pydantic / misc)."""
    if obj is None:
        return None
    # pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    # pydantic v1 / dataclasses-like
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return dict(obj.__dict__)
        except Exception:
            pass
    return str(obj)

def today_ymd_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ensure_manifest(date: str, title: str):
    data = {}
    if MANIFEST.exists():
        try:
            txt = MANIFEST.read_text(encoding="utf-8").strip()
            data = json.loads(txt) if txt else {}
        except Exception:
            data = {}

    days = data.get("days", [])
    # 去重
    days = [d for d in days if d.get("date") != date]
    days.append({"date": date, "title": title})
    # 按日期排序
    days = sorted(days, key=lambda x: x.get("date", ""), reverse=True)

    data["days"] = days
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _http_get(url: str) -> requests.Response:
    last_err = None
    for _ in range(HTTP_RETRIES + 1):
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            time.sleep(1)
    raise last_err  # type: ignore


def safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def pct_change(last: float, prev: float) -> Optional[float]:
    try:
        if last is None or prev is None:
            return None
        if prev == 0:
            return None
        return (last / prev - 1.0) * 100.0
    except Exception:
        return None


def sma(series: pd.Series, window: int) -> Optional[float]:
    try:
        if series is None or len(series) < window:
            return None
        return float(series.tail(window).mean())
    except Exception:
        return None


# =========================
# 1) 抓行情数据 + 算指标
# =========================

def fetch_close_prices(symbols: List[str], period_days: int = LOOKBACK_DAYS) -> Tuple[pd.DataFrame, List[str]]:
    """
    返回：close_df (index=日期, columns=symbol), errors
    """
    errors = []
    end = datetime.now(timezone.utc).date()
    start = end - pd.Timedelta(days=period_days)

    close_df = pd.DataFrame()

    for sym in symbols:
        try:
            df = yf.download(sym, start=str(start), end=str(end + pd.Timedelta(days=1)), progress=False, auto_adjust=False)
            if df is None or df.empty:
                errors.append(f"empty price data: {sym}")
                continue
            # 优先 Close
            if "Close" not in df.columns:
                errors.append(f"missing Close column: {sym}")
                continue
            s = df["Close"].dropna()
            if s.empty:
                errors.append(f"empty Close series: {sym}")
                continue
            close_df[sym] = s
        except Exception as e:
            errors.append(f"fetch error {sym}: {e}")

    # 对齐日期
    if not close_df.empty:
        close_df = close_df.sort_index()
        close_df = close_df.dropna(how="all")

    return close_df, errors


def calc_trend_metrics(close_df: pd.DataFrame, tickers: Dict[str, str]) -> Dict[str, Any]:
    """
    输出结构：
    metrics = {
      "status": "ok"/"degraded",
      "errors": [...],
      "series": {
         "VIX": {"symbol":"^VIX","last":..., "chg_1d_pct":..., "chg_3d_pct":..., "chg_5d_pct":..., "chg_10d_pct":..., "sma_3":..., "sma_5":..., "sma_10":...},
         ...
      },
      "derived": {
         "credit_ratio_hyg_lqd": {"last":..., "chg_3d_pct":..., "chg_5d_pct":...}
      }
    }
    """
    out: Dict[str, Any] = {"status": "ok", "errors": [], "series": {}, "derived": {}}
    if close_df is None or close_df.empty:
        out["status"] = "degraded"
        out["errors"].append("close_df empty")
        return out

    for name, sym in tickers.items():
        s = close_df.get(sym)
        if s is None or s.dropna().empty:
            out["errors"].append(f"missing series: {name}({sym})")
            out["series"][name] = {"symbol": sym}
            continue

        s = s.dropna()
        last = safe_float(s.iloc[-1])
        prev_1 = safe_float(s.iloc[-2]) if len(s) >= 2 else None
        prev_3 = safe_float(s.iloc[-4]) if len(s) >= 4 else None
        prev_5 = safe_float(s.iloc[-6]) if len(s) >= 6 else None
        prev_10 = safe_float(s.iloc[-11]) if len(s) >= 11 else None

        out["series"][name] = {
            "symbol": sym,
            "last": last,
            "chg_1d_pct": pct_change(last, prev_1) if (last is not None and prev_1 is not None) else None,
            "chg_3d_pct": pct_change(last, prev_3) if (last is not None and prev_3 is not None) else None,
            "chg_5d_pct": pct_change(last, prev_5) if (last is not None and prev_5 is not None) else None,
            "chg_10d_pct": pct_change(last, prev_10) if (last is not None and prev_10 is not None) else None,
            "sma_3": sma(s, 3),
            "sma_5": sma(s, 5),
            "sma_10": sma(s, 10),
        }

    # credit ratio: HYG/LQD
    hyg = out["series"].get("HYG", {}).get("last")
    lqd = out["series"].get("LQD", {}).get("last")
    if hyg is not None and lqd not in (None, 0):
        cr_last = float(hyg) / float(lqd)
    else:
        cr_last = None

    # ratio changes 用 close_df 直接算更稳
    try:
        if "HYG" in tickers and "LQD" in tickers:
            hyg_sym = tickers["HYG"]
            lqd_sym = tickers["LQD"]
            if hyg_sym in close_df.columns and lqd_sym in close_df.columns:
                ratio = (close_df[hyg_sym] / close_df[lqd_sym]).dropna()
                if len(ratio) >= 6:
                    r_last = safe_float(ratio.iloc[-1])
                    r_prev_3 = safe_float(ratio.iloc[-4]) if len(ratio) >= 4 else None
                    r_prev_5 = safe_float(ratio.iloc[-6]) if len(ratio) >= 6 else None
                    cr_3d = pct_change(r_last, r_prev_3) if (r_last is not None and r_prev_3 is not None) else None
                    cr_5d = pct_change(r_last, r_prev_5) if (r_last is not None and r_prev_5 is not None) else None
                else:
                    cr_3d, cr_5d = None, None
            else:
                cr_3d, cr_5d = None, None
        else:
            cr_3d, cr_5d = None, None
    except Exception:
        cr_3d, cr_5d = None, None

    out["derived"]["credit_ratio_hyg_lqd"] = {
        "last": cr_last,
        "chg_3d_pct": cr_3d,
        "chg_5d_pct": cr_5d,
    }

    if out["errors"]:
        out["status"] = "degraded"

    return out


# =========================
# 2) 连续趋势模型（0-12）-> 风险颜色
# =========================

def score_risk(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """
    连续趋势评分 (0-12) -> 风险颜色 + 解释
    只使用你目前可稳定抓到的数据：
      VIX, credit_ratio(HYG/LQD), SPY, QQQ, GLD, UUP(可选), TNX(可选)
    """
    report: Dict[str, Any] = {
        "status": "ok",
        "score": None,
        "risk": "🟡",
        "reasons": [],
        "missing": [],
    }

    def missing(val) -> bool:
        return val is None

    series = (metrics.get("series") or {}) if isinstance(metrics, dict) else {}
    derived = (metrics.get("derived") or {}) if isinstance(metrics, dict) else {}

    vix = (series.get("VIX") or {})
    spy = (series.get("SPY") or {})
    qqq = (series.get("QQQ") or {})
    gld = (series.get("GLD") or {})
    uup = (series.get("UUP") or {})
    tnx = (series.get("TNX") or {})
    cr = (derived.get("credit_ratio_hyg_lqd") or {})

    # levels
    vix_last = safe_float(vix.get("last"))
    # changes
    vix_3d = safe_float(vix.get("chg_3d_pct"))
    vix_5d = safe_float(vix.get("chg_5d_pct"))

    spy_3d = safe_float(spy.get("chg_3d_pct"))
    qqq_3d = safe_float(qqq.get("chg_3d_pct"))
    gld_3d = safe_float(gld.get("chg_3d_pct"))

    uup_3d = safe_float(uup.get("chg_3d_pct"))
    tnx_3d = safe_float(tnx.get("chg_3d_pct"))

    cr_3d = safe_float(cr.get("chg_3d_pct"))
    cr_5d = safe_float(cr.get("chg_5d_pct"))

    # 缺失提示（会在结构报告中展示）
    def mark(name: str, val):
        if missing(val):
            report["missing"].append(name)

    mark("VIX last", vix_last)
    mark("VIX chg_3d_pct", vix_3d)
    mark("credit_ratio_hyg_lqd chg_3d_pct", cr_3d)
    mark("SPY chg_3d_pct", spy_3d)
    mark("QQQ chg_3d_pct", qqq_3d)
    mark("GLD chg_3d_pct", gld_3d)

    # 核心打分门槛（没有这些就降级）
    required_for_score = [vix_last, vix_3d, cr_3d, spy_3d, qqq_3d, gld_3d]
    scorable = all(v is not None for v in required_for_score)

    if not scorable:
        report["status"] = "degraded"
        report["score"] = None
        report["risk"] = "🟡"
        report["reasons"].append("⚠️ 核心数据缺失：风险评分/策略建议降级为“定性提示”。请先修复数据源。")
        if report["missing"]:
            report["reasons"].append("缺数据项：" + "；".join(report["missing"]))
        return report

    score = 0
    reasons: List[str] = []

    # 1) VIX 绝对水平（0-3分）
    if vix_last >= 30:
        score += 3
        reasons.append("VIX≥30：极端波动/系统性压力上升")
    elif vix_last >= 20:
        score += 2
        reasons.append("VIX 20-30：高波动区，风险偏好下降")
    elif vix_last >= 16:
        score += 1
        reasons.append("VIX 16-20：波动抬升，需提高警惕")
    else:
        reasons.append("VIX<16：波动偏低（不代表无风险）")

    # 2) VIX 趋势（0-2分）
    if vix_3d is not None and vix_3d >= 15:
        score += 2
        reasons.append("VIX 3日涨幅≥15%：波动快速升温")
    elif vix_3d is not None and vix_3d > 5:
        score += 1
        reasons.append("VIX 3日上涨：短期风险抬升")

    # 3) 信用风险（0-2分）
    # HYG/LQD 比率下降（chg 为负） -> 信用走弱
    if cr_3d is not None and cr_3d <= -0.5:
        score += 2
        reasons.append("HYG/LQD 3日显著走弱：信用利差压力上升")
    elif cr_3d is not None and cr_3d < 0:
        score += 1
        reasons.append("HYG/LQD 3日走弱：信用边际收紧")

    # 4) 风险资产承压（0-2分）
    # SPY/QQQ 下跌增强 risk-off
    eq_down = 0
    if spy_3d is not None and spy_3d < 0:
        eq_down += 1
    if qqq_3d is not None and qqq_3d < 0:
        eq_down += 1
    if eq_down == 2:
        score += 2
        reasons.append("SPY+QQQ 同步走弱：风险资产承压")
    elif eq_down == 1:
        score += 1
        reasons.append("股指一强一弱：结构分化/轮动加剧")

    # 5) 去杠杆/“金也跌”（0-2分）
    # VIX上行 + 股票跌 + 黄金也跌 -> 可能是流动性收缩/去杠杆
    if (vix_3d is not None and vix_3d > 0) and (spy_3d is not None and spy_3d < 0) and (gld_3d is not None and gld_3d < 0):
        score += 2
        reasons.append("VIX↑ + 股↓ + 金↓：疑似去杠杆/流动性收缩")

    # 6) “钝化”检测（-1分）
    # VIX上行但SPY也上行 -> 可能是对冲需求/事件风险而非趋势性risk-off
    if (vix_3d is not None and vix_3d > 0) and (spy_3d is not None and spy_3d > 0):
        score -= 1
        reasons.append("VIX↑但指数企稳：可能为事件对冲/钝化")

    # 7) 可选宏观代理（0-1分）
    # 美元↑ 或 10Y↑ 可增强金融条件收紧的判断（不强依赖，缺了不降级）
    tighten = 0
    if uup_3d is not None and uup_3d > 0.5:
        tighten += 1
    if tnx_3d is not None and tnx_3d > 1.0:
        tighten += 1
    if tighten >= 1:
        score += 1
        reasons.append("美元/利率走强：金融条件趋紧（可选信号）")

    # clamp
    score = max(0, min(12, score))

    # 风险颜色映射
    if score <= 2:
        risk = "🟢"
    elif score <= 5:
        risk = "🟡"
    elif score <= 8:
        risk = "🟠"
    else:
        risk = "🔴"

    report["score"] = score
    report["risk"] = risk
    report["reasons"] = reasons
    return report


# =========================
# 3) 新闻抓取（RSS）
# =========================


def fetch_rss_items(limit_total: int = 30) -> Dict[str, Any]:
    """
    返回：
      {"status": "ok"/"degraded", "items":[{"title","link","source","published"}...], "errors":[...]}
    """
    out = {"status": "ok", "items": [], "errors": []}
    items: List[Dict[str, str]] = []

    for feed_url in RSS_FEEDS:
        try:
            r = _http_get(feed_url)
            parsed = feedparser.parse(r.text)
            if not parsed.entries:
                out["errors"].append(f"empty feed: {feed_url}")
                continue
            source = parsed.feed.get("title", feed_url)
            for e in parsed.entries[:10]:
                title = (e.get("title") or "").strip()
                link = (e.get("link") or "").strip()
                published = (e.get("published") or e.get("updated") or "").strip()
                if title:
                    items.append(
                        {"title": title, "link": link, "source": str(source), "published": published}
                    )
        except Exception as ex:
            out["errors"].append(f"rss error: {feed_url} | {ex}")

    # 去重（按title）
    seen = set()
    dedup = []
    for it in items:
        k = it["title"]
        if k in seen:
            continue
        seen.add(k)
        dedup.append(it)

    out["items"] = dedup[:limit_total]
    if out["errors"] and not out["items"]:
        out["status"] = "degraded"
    elif out["errors"]:
        out["status"] = "degraded"

    return out
print("RSS news fetched:", len(news_items))

# =========================
# 4) LLM 生成（结构监控 / 扩展阅读）
# =========================

def format_metrics_for_prompt(metrics: Dict[str, Any], risk: Dict[str, Any]) -> str:
    """
    把 metrics + risk 压缩成 LLM 可读、可引用的“已计算数据块”
    """
    series = metrics.get("series", {}) or {}
    derived = metrics.get("derived", {}) or {}

    def line(name: str, obj: Dict[str, Any]) -> str:
        last = obj.get("last")
        c1 = obj.get("chg_1d_pct")
        c3 = obj.get("chg_3d_pct")
        c5 = obj.get("chg_5d_pct")
        c10 = obj.get("chg_10d_pct")
        return f"- {name}: last={last} | 1D%={c1} | 3D%={c3} | 5D%={c5} | 10D%={c10}"

    blocks = []
    blocks.append("【已计算数据】")
    blocks.append(f"- data_status: {metrics.get('status')}")
    if metrics.get("errors"):
        blocks.append(f"- data_errors: {metrics.get('errors')}")
    blocks.append("")
    blocks.append("【核心序列】")
    for k in ["VIX", "SPY", "QQQ", "GLD", "UUP", "TNX", "HYG", "LQD"]:
        if k in series:
            blocks.append(line(k, series.get(k) or {}))
    blocks.append("")
    blocks.append("【派生指标】")
    cr = derived.get("credit_ratio_hyg_lqd", {}) or {}
    blocks.append(f"- credit_ratio_hyg_lqd: last={cr.get('last')} | 3D%={cr.get('chg_3d_pct')} | 5D%={cr.get('chg_5d_pct')}")
    blocks.append("")
    blocks.append("【风险模型输出】")
    blocks.append(f"- risk_status: {risk.get('status')}")
    blocks.append(f"- risk_color: {risk.get('risk')}")
    blocks.append(f"- risk_score_0_12: {risk.get('score')}")
    if risk.get("missing"):
        blocks.append(f"- missing: {risk.get('missing')}")
    if risk.get("reasons"):
        blocks.append(f"- reasons: {risk.get('reasons')}")

    return "\n".join(blocks)


def gen_structure_md(date: str,
                     metrics: Dict[str, Any],
                     risk: Dict[str, Any],
                     news: Optional[Dict[str, Any]] = None,
                     debug_bucket: Optional[Dict[str, Any]] = None) -> str:
    """
    ✅ B方案：兼容签名（news/debug_bucket 可不传）
    - 必须只用“已计算数据块”，不得胡编任何数值
    - 缺数据则输出“数据降级”并给修复建议
    """
    if debug_bucket is None:
        debug_bucket = {}
    if news is None:
        news = {"status": "degraded", "items": [], "errors": ["news not provided"]}

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

    metrics_block = format_metrics_for_prompt(metrics, risk)

    structure_system = (
        "你是一个严谨的宏观与风险结构监控分析师，用中文输出 Markdown，结构清晰，避免空话。\n"
        "硬规则：\n"
        "1) 你只能引用我提供的【已计算数据】里的数值；禁止编造任何指标数值。\n"
        "2) 如果某项为 None/NA/缺失，你必须明确标注 NA，并解释可能原因与修复路径。\n"
        "3) 输出必须以第一行标题开头：# 结构监控（YYYY-MM-DD）\n"
        "4) 必须包含：风险等级（🟢🟡🟠🔴之一）+ 风险评分（0-12或NA）+ 核心指标快照 + 原因链解读 + 风险触发条件 + 可执行策略建议。\n"
        "5) 策略必须可执行：不追高/加仓减仓倾向/观察条件/触发阈值。\n"
    )

    structure_user = (
        f"今天日期是 {date}（UTC）。请生成今日《结构监控（含策略建议）》并严格使用这个日期。\n\n"
        f"{metrics_block}\n\n"
        "请按以下结构输出：\n"
        "A) 风险等级与评分（含数据质量：ok/degraded）\n"
        "B) 核心指标快照（VIX、信用(HYG/LQD)、SPY/QQQ/GLD、可选UUP/TNX）\n"
        "C) 结构解读（原因链：波动→信用→风险资产→避险/去杠杆）\n"
        "D) 风险触发条件（给出明确阈值/组合条件）\n"
        "E) 可执行策略建议（今日/未来1-4周；强调不追高）\n"
        "注意：缺失项必须写 NA，不可用“需核对”糊弄。\n"
    )

    resp = client.responses.create(
        model=OPENAI_MODEL_STRUCTURE,
        max_output_tokens=STRUCTURE_MAX_TOKENS,
        input=[
            {"role": "system", "content": structure_system},
            {"role": "user", "content": structure_user},
        ],
    )

    text = resp.output_text or ""
    debug_bucket["structure_model"] = OPENAI_MODEL_STRUCTURE
    debug_bucket["extended_tokens"]  = to_jsonable(getattr(resp, "usage", None))
   
    return text


def gen_extended_md(date: str,
                    metrics: Dict[str, Any],
                    risk: Dict[str, Any],
                    news: Optional[Dict[str, Any]] = None,
                    debug_bucket: Optional[Dict[str, Any]] = None) -> str:
    """
    ✅ B方案：兼容签名（news/debug_bucket 可不传）
    - 尽量基于RSS items，若新闻源空则给“结构化观察要点”，但仍输出完整五板块 Top10
    - 严禁输出“占位符/待补充/模板说明/框架版”
    """
    if debug_bucket is None:
        debug_bucket = {}
    if news is None:
        news = {"status": "degraded", "items": [], "errors": ["news not provided"]}

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

    metrics_block = format_metrics_for_prompt(metrics, risk)

    # 把RSS新闻作为“候选素材”给模型（但模型仍需按 strict 写满）
    news_items = news.get("items") or []
    news_block_lines = ["【新闻素材（RSS）】", f"- news_status: {news.get('status')}"]
    if news.get("errors"):
        news_block_lines.append(f"- news_errors: {news.get('errors')}")
    for i, it in enumerate(news_items[:25], 1):
        news_block_lines.append(f"{i}. {it.get('title','').strip()} | {it.get('source','')} | {it.get('published','')}")
    news_block = "\n".join(news_block_lines)

    extended_system = (
        "你是一个结构化阅读简报编辑，用中文输出 Markdown。\n"
        "你必须严格遵循用户固定模板标准：按板块（投资、健康、心理/哲学、AI/科技、美学）输出。\n"
        "每条信息必须写 3–5 句话，包含：主题/切入点、关键洞见、证据/数据（若无实时数据请明确标注“需核对”，并给出应核对指标口径）、影响/行动。\n"
        "每个板块最后必须附上【板块综合评论】（不少于 5 句话）。\n"
        "投资板块：\n"
        "- 先给“主流媒体/财经新闻 Top10”（可引用RSS素材；无素材也要给当日结构性要点，必须标注需核对）\n"
        "- 再给【X/Twitter Top 10 观点】（无法实时抓取就用“市场常见分歧点/监控指标/触发条件”组织，并标注需核对）\n"
        "- 再给【税务规划】5条可执行要点\n"
        "- 再给【Estate planning】5条可执行要点\n"
        "严禁输出：占位符/待补充/模板说明/框架版。\n"
        "硬规则：标题必须以：# 每日扩展阅读 Strict | YYYY-MM-DD 开头。\n"
    )

    extended_user = (
        f"请生成 {date} 的《每日扩展阅读 Strict》。必须严格使用该日期。\n\n"
        f"{metrics_block}\n\n"
        f"{news_block}\n\n"
        "要求：\n"
        "1) 五个板块：投资、健康（重点抗衰老）、心理/哲学、AI/科技、美学。\n"
        "2) 每个板块列出 Top 10 条（编号 1-10）。每条 3–5 句话，按：主题/切入点 → 关键洞见 → 证据/数据（无法实时获取请写“需核对”并给出应核对的指标/来源口径） → 影响/行动。\n"
        "3) 每个板块最后写【板块综合评论】（不少于 5 句话：结构性判断+风险提示）。\n"
        "4) 投资板块额外加入：\n"
        "   - 【X/Twitter Top 10 观点】（同样 1-10，每条 3–5 句；若无实时抓取，用“分歧点/监控指标/触发条件”组织，并标注“需核对”）\n"
        "   - 【税务规划】给 5 条可执行要点（与美股/ETF/期权/海外资产申报相关）\n"
        "   - 【Estate planning】给 5 条可执行要点（信托/受益人/赠与/跨境等）\n"
        "5) 最后写【全局总评】（不少于 8 句话：总结五板块共同结构、风险等级、未来 1–4 周关注点）。\n"
        "输出 Markdown，标题层级清晰。\n"
    )

    resp = client.responses.create(
        model=OPENAI_MODEL_EXTENDED,
        max_output_tokens=EXTENDED_MAX_TOKENS,
        input=[
            {"role": "system", "content": extended_system},
            {"role": "user", "content": extended_user},
        ],
    )
    text = resp.output_text or ""
    debug_bucket["extended_model"] = OPENAI_MODEL_EXTENDED
    debug_bucket["structure_tokens"] = to_jsonable(getattr(resp, "usage", None))
   
    return text


# =========================
# 5) main
# =========================

def main():
    date = today_ymd_utc()
    day_dir = DAILY_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)

    llm_debug: Dict[str, Any] = {}

    # 1) 抓数据 + 算趋势
    symbols = list(TICKERS.values())
    close_df, price_errors = fetch_close_prices(symbols, period_days=LOOKBACK_DAYS)
    metrics = calc_trend_metrics(close_df, TICKERS)
    if price_errors:
        metrics["errors"] = (metrics.get("errors") or []) + price_errors
        metrics["status"] = "degraded"

    # 2) 风险评分
    risk = score_risk(metrics)

    # 3) 新闻（RSS）
    news = fetch_rss_items()

    # 保存机器可读指标
    (day_dir / "metrics.json").write_text(
        json.dumps({"date": date, "metrics": metrics, "risk": risk, "news": {"status": news.get("status"), "count": len(news.get("items") or []), "errors": news.get("errors")}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4) 生成结构监控 & 扩展阅读
    # ✅ 即便你把下面两行改回旧签名调用，也不会 TypeError（因为函数已做兼容）
    structure_md = gen_structure_md(date, metrics, risk, news=news, debug_bucket=llm_debug)
    (day_dir / "structure.md").write_text(structure_md, encoding="utf-8")

    extended_md = gen_extended_md(date, metrics, risk, news=news, debug_bucket=llm_debug)
    (day_dir / "extended.md").write_text(extended_md, encoding="utf-8")

    # 5) manifest：标题里放🟢🟡🟠🔴，首页会自动着色
    risk_color = risk.get("risk") or "🟡"
    ensure_manifest(date, f"Structure {risk_color} | Auto-generated")

    # 6) 写 debug（可选）
    (day_dir / "debug.json").write_text(json.dumps(llm_debug, ensure_ascii=False, indent=2), encoding="utf-8")

  # 3) 新闻（RSS）
    news = fetch_rss_items()

    print("news fetched:", len(news.get("items", [])))
    if __name__ == "__main__":
    main()
