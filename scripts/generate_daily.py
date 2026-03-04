# scripts/generate_daily.py
import os, json, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import pandas as pd
import yfinance as yf
from openai import OpenAI

# =========================
# Config
# =========================

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = REPO_ROOT / "daily"
MANIFEST = REPO_ROOT / "manifest.json"

# 你希望 dashboard 里看到的“键名”（稳定不变） -> yfinance ticker
TICKERS = {
    "VIX": "^VIX",
    "SPY": "SPY",
    "QQQ": "QQQ",
    "GLD": "GLD",
    "HYG": "HYG",
    "LQD": "LQD",
    "UUP": "UUP",     # 美元代理（可选）
    "TNX": "^TNX",    # 10Y proxy（可选）
}

# 用于风险评分的核心字段（缺这些就降级）
CORE_FOR_SCORE = ["VIX", "SPY", "QQQ", "GLD", "credit_ratio_hyg_lqd"]

# =========================
# Helpers
# =========================

def today_ymd_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def ensure_manifest(date: str, title: str):
    data = {}
    if MANIFEST.exists():
        txt = MANIFEST.read_text(encoding="utf-8").strip()
        data = json.loads(txt) if txt else {}
    days = data.get("days", [])
    if not any(d.get("date") == date for d in days):
        days.append({"date": date, "title": title})
    data["days"] = sorted(days, key=lambda x: x.get("date", ""))
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        if pd.isna(x):
            return None
        return float(x)
    except Exception:
        return None

def pct_change(a: Optional[float], b: Optional[float]) -> Optional[float]:
    # from b -> a
    if a is None or b is None:
        return None
    if b == 0:
        return None
    return (a / b - 1.0) * 100.0

def sma(series: pd.Series, n: int) -> Optional[float]:
    if series is None or len(series) < n:
        return None
    return safe_float(series.tail(n).mean())

def load_previous_metrics(day_dir: Path) -> Optional[Dict[str, Any]]:
    """
    回退策略：如果今天抓取失败，找最近一天的 metrics.json 作为 fallback。
    """
    if not DAILY_DIR.exists():
        return None

    # 找最近的 metrics.json（排除今天目录）
    all_days = sorted([p for p in DAILY_DIR.iterdir() if p.is_dir()], reverse=True)
    for d in all_days:
        if d == day_dir:
            continue
        mpath = d / "metrics.json"
        if mpath.exists():
            try:
                return json.loads(mpath.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None

# =========================
# Data Fetch + Metrics
# =========================

def fetch_close_prices_yf(symbols: List[str], period: str = "6mo", max_retry: int = 3) -> pd.DataFrame:
    """
    yfinance 偶发返回空/被限流：做重试 + sleep。
    返回：DataFrame index=Datetime, columns=symbol
    """
    last_err = None
    for i in range(max_retry):
        try:
            df = yf.download(
                tickers=" ".join(symbols),
                period=period,
                interval="1d",
                auto_adjust=True,
                group_by="column",
                progress=False,
                threads=True,
            )
            # yfinance 的列结构可能是：
            # - 多标的：columns 为 MultiIndex (Field, Ticker) 或 (Ticker, Field)
            # - 单标的：columns 为单层
            if df is None or df.empty:
                raise RuntimeError("yfinance returned empty dataframe")

            # 提取 Close
            close = None
            if isinstance(df.columns, pd.MultiIndex):
                # 尝试两种结构
                if ("Close" in df.columns.get_level_values(0)):
                    # (Field, Ticker)
                    close = df["Close"]
                elif ("Close" in df.columns.get_level_values(1)):
                    # (Ticker, Field)
                    close = df.xs("Close", axis=1, level=1)
                else:
                    raise RuntimeError("yfinance dataframe has MultiIndex but no Close field")
            else:
                # 单 ticker
                if "Close" in df.columns:
                    close = df[["Close"]].rename(columns={"Close": symbols[0]})
                else:
                    # auto_adjust=True 时，有时直接返回价格列（极少见）
                    raise RuntimeError("yfinance dataframe has no Close column")

            close = close.dropna(how="all")
            if close.empty:
                raise RuntimeError("Close series empty after dropna")
            return close

        except Exception as e:
            last_err = e
            # 退避
            time.sleep(1.5 * (i + 1))

    raise RuntimeError(f"fetch_close_prices_yf failed after retries: {last_err}")

def calc_trend_metrics(close: pd.DataFrame, tickers_map: Dict[str, str]) -> Dict[str, Any]:
    """
    输出结构：
    metrics = {
      "series": {
         "VIX": {"symbol":"^VIX","last":..., "chg_1d_pct":..., "chg_3d_pct":..., ... , "sma_3":...},
         ...
      },
      "credit_ratio_hyg_lqd": {"chg_3d_pct":..., "chg_5d_pct":..., "last":...}
    }
    """
    metrics: Dict[str, Any] = {"series": {}}

    def build_one(key: str, symbol: str) -> Dict[str, Any]:
        if symbol not in close.columns:
            return {"symbol": symbol, "last": None}
        s = close[symbol].dropna()
        if s.empty:
            return {"symbol": symbol, "last": None}

        last = safe_float(s.iloc[-1])
        one = {
            "symbol": symbol,
            "last": last,
            "chg_1d_pct": pct_change(last, safe_float(s.iloc[-2])) if len(s) >= 2 else None,
            "chg_3d_pct": pct_change(last, safe_float(s.iloc[-4])) if len(s) >= 4 else None,
            "chg_5d_pct": pct_change(last, safe_float(s.iloc[-6])) if len(s) >= 6 else None,
            "chg_10d_pct": pct_change(last, safe_float(s.iloc[-11])) if len(s) >= 11 else None,
            "sma_3": sma(s, 3),
            "sma_5": sma(s, 5),
            "sma_10": sma(s, 10),
        }
        return one

    # series
    for k, sym in tickers_map.items():
        # HYG/LQD 用于 ratio，仍然也存起来，方便你看
        metrics["series"][k] = build_one(k, sym)

    # credit ratio: HYG/LQD（用价格比值的“变化”近似利差方向）
    hyg = metrics["series"].get("HYG", {})
    lqd = metrics["series"].get("LQD", {})
    # 直接用 close 来算 ratio 的 3d/5d 更稳
    if TICKERS["HYG"] in close.columns and TICKERS["LQD"] in close.columns:
        hyg_s = close[TICKERS["HYG"]].dropna()
        lqd_s = close[TICKERS["LQD"]].dropna()
        n = min(len(hyg_s), len(lqd_s))
        hyg_s = hyg_s.tail(n)
        lqd_s = lqd_s.tail(n)
        ratio = (hyg_s / lqd_s).dropna()
        r_last = safe_float(ratio.iloc[-1]) if not ratio.empty else None
        r_3d = pct_change(r_last, safe_float(ratio.iloc[-4])) if len(ratio) >= 4 else None
        r_5d = pct_change(r_last, safe_float(ratio.iloc[-6])) if len(ratio) >= 6 else None
    else:
        r_last, r_3d, r_5d = None, None, None

    metrics["credit_ratio_hyg_lqd"] = {
        "last": r_last,
        "chg_3d_pct": r_3d,
        "chg_5d_pct": r_5d,
        "note": "HYG/LQD 比值：下降通常≈信用走弱（利差倾向扩大）；上升通常≈信用改善",
    }

    return metrics

# =========================
# Risk Scoring (连续趋势模型 0-12)
# =========================

def score_risk(m: Dict[str, Any]) -> Dict[str, Any]:
    """
    连续趋势评分（0-12） -> 风险颜色 + 原因链
    只用你能稳定抓到的数据：VIX, HYG/LQD, SPY, QQQ, GLD, UUP(可选), TNX(可选)
    """
    score = 0
    reasons: List[str] = []

    series = (m.get("series") or {})
    vix = (series.get("VIX") or {})
    spy = (series.get("SPY") or {})
    qqq = (series.get("QQQ") or {})
    gld = (series.get("GLD") or {})
    uup = (series.get("UUP") or {})
    tnx = (series.get("TNX") or {})
    cr = (m.get("credit_ratio_hyg_lqd") or {})

    vix_last = safe_float(vix.get("last"))
    vix_3d = safe_float(vix.get("chg_3d_pct"))
    vix_5d = safe_float(vix.get("chg_5d_pct"))

    spy_3d = safe_float(spy.get("chg_3d_pct"))
    qqq_3d = safe_float(qqq.get("chg_3d_pct"))
    gld_3d = safe_float(gld.get("chg_3d_pct"))

    uup_3d = safe_float(uup.get("chg_3d_pct"))
    tnx_3d = safe_float(tnx.get("chg_3d_pct"))

    cr_3d = safe_float(cr.get("chg_3d_pct"))
    cr_5d = safe_float(cr.get("chg_5d_pct"))

    def missing(val) -> bool:
        return val is None

    # 缺数据提示（会展示在 report 里）
    if missing(vix_last): reasons.append("缺数据：VIX last（抓取失败/源缺失）")
    if missing(vix_3d):  reasons.append("缺数据：VIX chg_3d_pct")
    if missing(cr_3d):   reasons.append("缺数据：credit_ratio_hyg_lqd chg_3d_pct")
    if missing(spy_3d):  reasons.append("缺数据：SPY chg_3d_pct")
    if missing(qqq_3d):  reasons.append("缺数据：QQQ chg_3d_pct")
    if missing(gld_3d):  reasons.append("缺数据：GLD chg_3d_pct")
    if missing(uup_3d):  reasons.append("缺数据：UUP chg_3d_pct（美元代理，可选）")
    if missing(tnx_3d):  reasons.append("缺数据：TNX chg_3d_pct（10Y proxy，可选）")

    # 核心打分门控：缺核心就降级，不硬算
    required_for_score = [vix_3d, cr_3d, spy_3d, qqq_3d, gld_3d]
    scorable = all(v is not None for v in required_for_score)
    data_quality = "ok" if scorable else "degraded"
    if not scorable:
        reasons.append("⚠️ 核心数据缺失：今日风险评分/策略建议降级为“定性提示”。优先修复数据源。")

    if scorable:
        # 1) VIX 绝对水平（更稳定）
        if vix_last is not None:
            if vix_last >= 30:
                score += 4; reasons.append("VIX≥30：波动恐慌区（系统风险显著）")
            elif vix_last >= 25:
                score += 3; reasons.append("VIX 25-30：高波动（风险偏好明显下降）")
            elif vix_last >= 20:
                score += 2; reasons.append("VIX 20-25：风险上升（需降低追高冲动）")
            elif vix_last >= 16:
                score += 1; reasons.append("VIX 16-20：温和风险（保持纪律）")
            else:
                score += 0; reasons.append("VIX<16：低波动（注意反身性与钝化风险）")

        # 2) 信用走弱：HYG/LQD 比值下降（近似利差扩大）
        # 这里用“cr_3d<0”代表信用走弱（更保守）
        if cr_3d is not None and cr_3d < 0:
            score += 2; reasons.append("信用走弱：HYG/LQD 比值 3D 下降（利差倾向扩大）")
        elif cr_3d is not None:
            reasons.append("信用未走弱：HYG/LQD 比值 3D 未下降")

        # 3) 风险资产下跌：SPY/QQQ
        if spy_3d is not None and spy_3d < 0:
            score += 1; reasons.append("SPY 3D 走弱：风险偏好减弱")
        if qqq_3d is not None and qqq_3d < 0:
            score += 1; reasons.append("QQQ 3D 走弱：成长风格承压")

        # 4) “去杠杆/相关性上升”检测：风险资产+黄金同跌 且 VIX 上行
        if vix_3d is not None and qqq_3d is not None and gld_3d is not None:
            if vix_3d > 0 and qqq_3d < 0 and gld_3d < 0:
                score += 2; reasons.append("去杠杆特征：VIX↑且QQQ↓且GLD↓（相关性上升）")

        # 5) “风险钝化”检测：VIX 上行但 SPY 上行（可能是对冲买盘/结构性不安）
        if vix_3d is not None and spy_3d is not None:
            if vix_3d > 0 and spy_3d > 0:
                score -= 1; reasons.append("风险钝化：VIX↑但SPY不跌（可能是对冲偏好上升/定价异常）")

        # 6) 美元与利率（可选）：UUP/TNX 强化 risk-off
        if uup_3d is not None and uup_3d > 0:
            score += 1; reasons.append("美元走强（UUP 3D↑）：全球流动性偏紧倾向")
        if tnx_3d is not None and tnx_3d > 0:
            score += 1; reasons.append("10Y上行（TNX 3D↑）：久期资产压力上升倾向")

        # clamp 0-12
        score = max(0, min(12, score))

    # 映射风险颜色
    if score is None:
        risk_color = "🟡"  # 数据降级时保持中性偏谨慎
    else:
        if score <= 2:
            risk_color = "🟢"
        elif score <= 5:
            risk_color = "🟡"
        elif score <= 8:
            risk_color = "🟠"
        else:
            risk_color = "🔴"

    return {
        "score": score,
        "risk": risk_color,
        "data_quality": data_quality,
        "reasons": reasons,
    }

def metrics_for_prompt(date: str, metrics: Dict[str, Any], risk: Dict[str, Any]) -> str:
    # 给模型的“唯一事实来源”
    payload = {
        "date": date,
        "risk": risk,
        "metrics": metrics,
        "interpretation_rules": {
            "credit_ratio_hyg_lqd": "下降≈信用走弱/利差倾向扩大；上升≈信用改善",
            "TNX": "10Y proxy（^TNX）为收益率指数形式，方向只做趋势参考",
        }
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)

# =========================
# LLM generation
# =========================

def llm_md(model: str, system_prompt: str, user_prompt: str, max_tokens: int, debug_bucket: dict, tag: str) -> str:
    """
    - 永不静默：出错/空输出都写入 debug_bucket
    - 返回空时给出可见错误文本（写进md）
    """
    try:
        resp = client.responses.create(
            model=model,
            max_output_tokens=max_tokens,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        text = (resp.output_text or "").strip()

        debug_bucket[tag] = {
            "model": model,
            "has_output_text": bool(resp.output_text),
            "output_text_len": len(resp.output_text or ""),
        }

        if not text:
            return f"**[ERROR] LLM返回空文本：model={model}，请查看 llm_debug.json / Actions logs**\n"

        return text + "\n"

    except Exception as e:
        debug_bucket[tag] = {"model": model, "exception": repr(e)}
        return f"**[ERROR] LLM调用失败：model={model}，error={repr(e)}**\n"


def gen_structure_md(date: str, metrics: Dict[str, Any], risk: Dict[str, Any], debug_bucket: dict) -> str:
    system = (
        "你是一个严谨的宏观与风险结构监控分析师，用中文输出Markdown，结构清晰，避免空话。"
        "你必须【只基于用户提供的JSON数据】做判断；不得编造任何数值、日期或新闻。"
        "如果 data_quality=degraded，必须明确写“数据降级”，并把建议降级为保守的流程化建议。"
    )
    user = (
        f"今天日期是 {date}（UTC）。请生成《结构监控（含策略建议）》并严格使用这个日期。\n"
        f"输出必须以第一行标题开头：# 结构监控（{date}）\n"
        "必须包含以下小节（按顺序）：\n"
        "1) 风险等级与评分（🟢🟡🟠🔴之一 + score 0-12 + data_quality）\n"
        "2) 核心指标快照（VIX、HYG/LQD、SPY、QQQ、GLD、UUP、TNX：给出 last 与 1D/3D/5D 变化；缺失就写 NA）\n"
        "3) 原因链（用 bullet 列出 reasons，尽量归因到“波动/信用/风险资产/去杠杆/美元利率”）\n"
        "4) 风险触发条件（给出 3-5 条明确阈值/组合触发，例如：VIX>25 且 HYG/LQD 3D<0 等）\n"
        "5) 可执行策略建议（不追高；加仓/减仓倾向；用条件句，能落地；至少 6 条）\n\n"
        "下面是【唯一可信数据源】JSON：\n"
        f"{metrics_for_prompt(date, metrics, risk)}\n"
    )
    # ✅ 统一用 gpt-4.1，避免 gpt-5 不可用导致空白
    return llm_md("gpt-4.1", system, user, max_tokens=1600, debug_bucket=debug_bucket, tag="structure")


def gen_extended_md(date: str, debug_bucket: dict) -> str:
    """
    Strict版：拆分生成，避免token截断。
    注意：这里并没有联网抓新闻，所以会输出“需核对”的结构化阅读内容，但不应为空。
    """
    sys_common = (
        "你是一个结构化阅读简报编辑，用中文输出Markdown。"
        "你必须严格按用户模板：每条 3–5 句话，包含：主题/切入点→关键洞见→证据/数据（无法实时获取必须写“需核对”，并给出应核对的指标/来源口径）→影响/行动。"
        "严禁输出“占位符/待补充/模板说明/框架版”等字样，必须输出完整内容。"
    )

    def call(tag: str, prompt: str, max_tokens: int) -> str:
        # ✅ 统一用 gpt-4.1（稳定）
        return llm_md("gpt-4.1", sys_common, prompt, max_tokens=max_tokens, debug_bucket=debug_bucket, tag=tag)

    invest_user = (
        f"请生成《每日扩展阅读 Strict | {date}》的【投资】板块。\n"
        "要求：\n"
        "A) 【投资 | Top 10】编号1-10，每条3-5句。\n"
        "B) 【X/Twitter Top 10 观点】编号1-10，每条3-5句；因无法实时抓取，请用“市场常见分歧点/监控指标/触发条件”组织，并标注“需核对”。\n"
        "C) 【税务规划】给5条可执行要点（与美股/ETF/期权/海外资产申报相关）。\n"
        "D) 【Estate planning】给5条可执行要点（信托/受益人/赠与/跨境）。\n"
        "E) 本板块最后写【板块综合评论】不少于5句。\n"
        "只输出本板块内容（不要输出其他板块）。"
    )
    health_user = f"请生成《每日扩展阅读 Strict | {date}》的【健康（重点抗衰老）】板块。Top10编号1-10，每条3-5句，最后【板块综合评论】不少于5句。"
    psycho_user = f"请生成《每日扩展阅读 Strict | {date}》的【心理/哲学】板块。Top10编号1-10，每条3-5句，最后【板块综合评论】不少于5句。"
    ai_user = f"请生成《每日扩展阅读 Strict | {date}》的【AI/科技】板块。Top10编号1-10，每条3-5句，最后【板块综合评论】不少于5句。"
    art_user = f"请生成《每日扩展阅读 Strict | {date}》的【美学】板块。Top10编号1-10，每条3-5句，最后【板块综合评论】不少于5句。"
    global_user = (
        f"请为《每日扩展阅读 Strict | {date}》生成【全局总评】。不少于8句话："
        "总结五板块共同结构、风险等级、未来1–4周关注点与关键风险。"
        "不得引用任何不存在的实时新闻或具体数据；如涉及数据必须写“需核对”并给出核对口径。"
    )

    out = []
    out.append(f"# 每日扩展阅读 Strict | {date}\n")

    out.append(call("invest", invest_user, 2200).strip() + "\n")
    out.append("\n---\n")
    out.append(call("health", health_user, 1800).strip() + "\n")
    out.append("\n---\n")
    out.append(call("psycho", psycho_user, 1800).strip() + "\n")
    out.append("\n---\n")
    out.append(call("ai", ai_user, 1800).strip() + "\n")
    out.append("\n---\n")
    out.append(call("aesthetics", art_user, 1800).strip() + "\n")
    out.append("\n---\n")
    out.append(call("global", global_user, 900).strip() + "\n")

    return "\n".join(out)

  
 
  
  
  

# =========================
# Main
# =========================

def main():
    date = today_ymd_utc()
    day_dir = DAILY_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)

    # 1) 抓数据 + 算趋势
    symbols = list(set(TICKERS.values()))
    metrics: Dict[str, Any]
    try:
        close = fetch_close_prices_yf(symbols, period="6mo", max_retry=3)
        metrics = calc_trend_metrics(close, TICKERS)
    except Exception as e:
        # 失败：回退到最近一天 metrics.json
        prev = load_previous_metrics(day_dir)
        if prev and isinstance(prev, dict) and "metrics" in prev:
            metrics = prev["metrics"]
            metrics["_fallback"] = {"used_previous_metrics": True, "error": str(e)}
        else:
            # 实在没救：返回空，让下游明显 degraded
            metrics = {"series": {}, "_fatal_fetch_error": str(e)}

    # 2) 风险评分（连续趋势模型）
    risk = score_risk(metrics)

    # 保存机器可读指标（用于 dashboard / debug）
    (day_dir / "metrics.json").write_text(
        json.dumps({"date": date, "metrics": metrics, "risk": risk}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 3) 结构监控（只允许用 metrics.json）
    structure_md = gen_structure_md(date, metrics, risk)
    (day_dir / "structure.md").write_text(structure_md, encoding="utf-8")

    # 4) 扩展阅读 Strict（拆分生成，避免token截断）
    extended_md = gen_extended_md(date)
    (day_dir / "extended.md").write_text(extended_md, encoding="utf-8")

    llm_debug = {}
    structure_md = gen_structure_md(date, metrics, risk, llm_debug)
    (day_dir / "structure.md").write_text(structure_md, encoding="utf-8")

    extended_md = gen_extended_md(date, llm_debug)
    (day_dir / "extended.md").write_text(extended_md, encoding="utf-8")

    (day_dir / "llm_debug.json").write_text(
        json.dumps(llm_debug, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 5) manifest：标题里放🟢🟡🟠🔴，首页自动着色
    icon = risk.get("risk") if risk.get("risk") in ["🟢", "🟡", "🟠", "🔴"] else "🟡"
    q = risk.get("data_quality", "ok")
    suffix = "" if q == "ok" else " (degraded)"
    ensure_manifest(date, f"Structure {icon}{suffix} | Auto-generated")

if __name__ == "__main__":
    main()
