import os, json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = REPO_ROOT / "daily"
MANIFEST = REPO_ROOT / "manifest.json"

# ====== 你关心的核心监控标的（Yahoo Finance 可直接取） ======
# VIX: ^VIX
# 10Y: ^TNX (注意：TNX 是“收益率*10”，比如 43.2 = 4.32%)
# DXY: 用 UUP 作为美元强弱代理（更稳定，且 yfinance 可取）
TICKERS = {
    "VIX": "^VIX",
    "SPY": "SPY",
    "QQQ": "QQQ",
    "GLD": "GLD",
    "HYG": "HYG",
    "LQD": "LQD",
    "UUP": "UUP",     # DXY proxy
    "TNX": "^TNX",    # 10Y yield * 10
}

RISK_EMOJI = {0: "🟢", 1: "🟡", 2: "🟠", 3: "🔴"}

def today_ymd_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def ensure_manifest(date: str, title: str):
    if MANIFEST.exists():
        data = json.loads(MANIFEST.read_text(encoding="utf-8") or "{}")
    else:
        data = {}

    days = data.get("days", [])
    if not any(d.get("date") == date for d in days):
        days.append({"date": date, "title": title})
    data["days"] = days
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def safe_pct(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    return float(x)

def fetch_close_prices(symbols: list[str], period="45d") -> pd.DataFrame:
    """
    下载 Close 价格；返回 df: index=Date, columns=symbol
    """
    data = yf.download(
        tickers=" ".join(symbols),
        period=period,
        interval="1d",
        group_by="column",
        auto_adjust=False,
        threads=True,
        progress=False,
    )

    # yfinance 返回结构可能是：
    # - 多 ticker：列是 MultiIndex (PriceField, Ticker)
    # - 单 ticker：列是 PriceField
    if isinstance(data.columns, pd.MultiIndex):
        close = data["Close"].copy()
    else:
        close = data[["Close"]].copy()
        close.columns = [symbols[0]]

    close = close.dropna(how="all")
    return close

def calc_trend_metrics(close: pd.DataFrame, name_map: dict[str, str]) -> dict:
    """
    用 close（列为 yfinance symbol）计算：
    - last
    - 1d/3d/5d/10d 变化%
    - 3/5/10 日均线（对“价格/指数”类）
    - 信用比值 HYG/LQD 与其 3/5/10 日变化%
    """
    def last_valid(s: pd.Series):
        s2 = s.dropna()
        return None if s2.empty else float(s2.iloc[-1])

    def pct_change_n(s: pd.Series, n: int):
        s2 = s.dropna()
        if len(s2) <= n:
            return None
        return float((s2.iloc[-1] / s2.iloc[-1 - n] - 1.0) * 100)

    out = {"asof_utc_date": today_ymd_utc(), "series": {}}

    # 单标的指标
    for k, sym in name_map.items():
        if sym not in close.columns:
            out["series"][k] = {"symbol": sym, "error": "no_data"}
            continue

        s = close[sym]
        out["series"][k] = {
            "symbol": sym,
            "last": last_valid(s),
            "chg_1d_pct": pct_change_n(s, 1),
            "chg_3d_pct": pct_change_n(s, 3),
            "chg_5d_pct": pct_change_n(s, 5),
            "chg_10d_pct": pct_change_n(s, 10),
            "sma_3": float(s.dropna().rolling(3).mean().iloc[-1]) if len(s.dropna()) >= 3 else None,
            "sma_5": float(s.dropna().rolling(5).mean().iloc[-1]) if len(s.dropna()) >= 5 else None,
            "sma_10": float(s.dropna().rolling(10).mean().iloc[-1]) if len(s.dropna()) >= 10 else None,
        }

    # 信用：HYG/LQD
    hyg = close.get(name_map["HYG"])
    lqd = close.get(name_map["LQD"])
    if hyg is not None and lqd is not None:
        ratio = (hyg / lqd).dropna()
        out["credit_ratio_hyg_lqd"] = {
            "last": last_valid(ratio),
            "chg_1d_pct": pct_change_n(ratio, 1),
            "chg_3d_pct": pct_change_n(ratio, 3),
            "chg_5d_pct": pct_change_n(ratio, 5),
            "chg_10d_pct": pct_change_n(ratio, 10),
        }
    else:
        out["credit_ratio_hyg_lqd"] = {"error": "no_data"}

    # 10Y：^TNX 是 *10
    tnx_last = out["series"].get("TNX", {}).get("last")
    if tnx_last is not None:
        out["us10y_yield_pct_est"] = float(tnx_last / 10.0)

    return out

def score_risk(m: dict) -> dict:
    """
    连续趋势评分（0-12）→ 风险颜色
    只用你现在已经能稳定抓到的数据：
      VIX, HYG/LQD, SPY, QQQ, GLD, UUP(美元代理), 10Y(TNX)
    """
    score = 0
    reasons = []

vix = m["series"]["VIX"]
vix_last = vix.get("last")
vix_1d = vix.get("chg_1d_pct")
vix_3d = vix.get("chg_3d_pct")

cr_3d = m.get("credit_ratio_hyg_lqd", {}).get("chg_3d_pct")
spy_3d = m["series"]["SPY"].get("chg_3d_pct")

# 信用+波动同步恶化
if cr_3d is not None and vix_3d is not None:
    if cr_3d < 0 and vix_3d > 0:
        score += 1
        reasons.append("信用+波动同步恶化 (risk-off强化)")

# 风险钝化检测
if vix_3d is not None and spy_3d is not None:
    if vix_3d > 0 and spy_3d > 0:
        score -= 1
        reasons.append("VIX上升但指数企稳 (可能钝化)")
   
    # VIX 水平
    if vix_last is not None:
        if vix_last >= 30:
            score += 4; reasons.append("VIX≥30（高压力）")
        elif vix_last >= 25:
            score += 3; reasons.append("VIX 25-30（压力上升）")
        elif vix_last >= 20:
            score += 2; reasons.append("VIX 20-25（波动抬升）")
        elif vix_last >= 16:
            score += 1; reasons.append("VIX 16-20（波动偏高）")

    # VIX 变化（连续趋势）
    if vix_1d is not None and vix_1d >= 8:
        score += 2; reasons.append("VIX 单日≥+8%（波动冲击）")
    if vix_3d is not None and vix_3d >= 15:
        score += 2; reasons.append("VIX 3日≥+15%（趋势性升温）")

    # 信用：HYG/LQD（越跌越危险）
    cr = m.get("credit_ratio_hyg_lqd", {})
    cr_3d = cr.get("chg_3d_pct")
    cr_5d = cr.get("chg_5d_pct")
    if cr_3d is not None and cr_3d <= -0.6:
        score += 2; reasons.append("HYG/LQD 3日走弱（信用收紧）")
    if cr_5d is not None and cr_5d <= -1.0:
        score += 2; reasons.append("HYG/LQD 5日走弱（信用恶化）")

    # 风险资产回撤
    qqq_5d = m["series"]["QQQ"].get("chg_5d_pct")
    spy_5d = m["series"]["SPY"].get("chg_5d_pct")
    if qqq_5d is not None and qqq_5d <= -3.0:
        score += 2; reasons.append("QQQ 5日≤-3%（成长承压）")
    if spy_5d is not None and spy_5d <= -2.0:
        score += 1; reasons.append("SPY 5日≤-2%（大盘回撤）")

    # 流动性收缩特征：美元走强 + 利率走高 + 风险资产回撤
    uup_5d = m["series"]["UUP"].get("chg_5d_pct")
    tnx_5d = m["series"]["TNX"].get("chg_5d_pct")
    if uup_5d is not None and tnx_5d is not None:
        if uup_5d >= 0.8 and tnx_5d >= 1.5:
            score += 2; reasons.append("美元+利率同涨（金融条件收紧）")

    # “假避险/去杠杆”特征：VIX↑ 但 GLD 也大跌（说明被动抛售）
    gld_1d = m["series"]["GLD"].get("chg_1d_pct")
    if vix_1d is not None and vix_1d >= 8 and gld_1d is not None and gld_1d <= -1.2:
        score += 1; reasons.append("VIX冲击且黄金下跌（去杠杆/现金为王）")

    # 风险等级映射
    # 0-2 🟢 | 3-5 🟡 | 6-8 🟠 | 9+ 🔴
    if score <= 2:
        level = "🟢"
    elif score <= 5:
        level = "🟡"
    elif score <= 8:
        level = "🟠"
    else:
        level = "🔴"

    return {"score": int(score), "level": level, "reasons": reasons}

def format_metrics_for_prompt(m: dict, risk: dict) -> str:
    """
    给模型的“已计算数据摘要”（非常关键：模型不再胡编）
    """
    def line(name, key):
        s = m["series"][name]
        last = s.get("last")
        c1 = s.get("chg_1d_pct")
        c3 = s.get("chg_3d_pct")
        c5 = s.get("chg_5d_pct")
        return f"- {key}: last={last:.2f} | 1d={c1:+.2f}% | 3d={c3:+.2f}% | 5d={c5:+.2f}%"

    tnx_y = m.get("us10y_yield_pct_est")
    credit_last = m.get("credit_ratio_hyg_lqd", {}).get("last")
    credit_3d = m.get("credit_ratio_hyg_lqd", {}).get("chg_3d_pct")
    credit_5d = m.get("credit_ratio_hyg_lqd", {}).get("chg_5d_pct")

    lines = [
        f"## 风险引擎输出（已计算）",
        f"- 风险评分: {risk['score']} / 12",
        f"- 风险等级: {risk['level']}",
        f"- 触发原因: " + "；".join(risk["reasons"]) if risk["reasons"] else "- 触发原因: 无明显触发",
        "",
        "## 关键指标（已抓取）",
        line("VIX", "VIX"),
        line("SPY", "SPY"),
        line("QQQ", "QQQ"),
        line("GLD", "GLD"),
        line("UUP", "美元代理(UUP)"),
        f"- 10Y(来自^TNX): est_yield={tnx_y:.2f}% | 5d={m['series']['TNX'].get('chg_5d_pct', 0):+.2f}%" if tnx_y is not None else "- 10Y: no_data",
        f"- 信用代理(HYG/LQD): last={credit_last:.4f} | 3d={credit_3d:+.2f}% | 5d={credit_5d:+.2f}%" if credit_last is not None else "- 信用代理(HYG/LQD): no_data",
    ]
    return "\n".join(lines)

def main():
    date = today_ymd_utc()
    day_dir = DAILY_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)

    # 1) 抓数据 + 算趋势
    symbols = list(TICKERS.values())
    close = fetch_close_prices(symbols, period="60d")
    metrics = calc_trend_metrics(close, TICKERS)
    risk = score_risk(metrics)

    # 保存机器可读指标
    (day_dir / "metrics.json").write_text(
        json.dumps({"date": date, "metrics": metrics, "risk": risk}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # 2) 结构监控（用“已计算数据”约束模型，避免胡编）
    structure_system = "你是一个严谨的宏观与风险结构监控分析师。你必须只基于我提供的【已计算数据】做判断，不能杜撰任何数值。用中文输出 Markdown，结构清晰，直接给可执行建议。"

    metrics_block = format_metrics_for_prompt(metrics, risk)

    structure_user = (
        f"今天日期是 {date}（UTC）。请生成今日《结构监控（含策略建议）》并严格使用这个日期。\n"
        f"输出必须以第一行标题开头：# 结构监控（{date}）\n"
        "你必须包含：\n"
        "1) 风险等级（🟢🟡🟠🔴之一）+ 风险评分（0-12）\n"
        "2) VIX（水平+1d/3d/5d趋势）\n"
        "3) 信用（HYG/LQD 比值与 3d/5d趋势）\n"
        "4) 资金风险代理（SPY/QQQ/GLD 的 1d/5d趋势，用于判断 risk-on/off 与是否去杠杆）\n"
        "5) DXY 代理（UUP）与 10Y（TNX换算）趋势\n"
        "6) 板块轮动（用 SPY vs QQQ vs GLD 的相对强弱给出结论）\n"
        "7) 风险触发条件（明确数值阈值，比如 VIX>25、HYG/LQD 5d<-1% 等）\n"
        "8) 可执行策略建议（不追高、加仓/减仓倾向；给出 3 条“如果…就…”规则）\n\n"
        "【已计算数据】如下（只能用这些数据，不得新增任何数字）：\n"
        f"{metrics_block}\n\n"
        "输出为 Markdown，不要出现其它年份或日期。"
    )

    structure_md = client.responses.create(
        model="gpt-4.1",
        max_output_tokens=1400,
        input=[
            {"role": "system", "content": structure_system},
            {"role": "user", "content": structure_user},
        ],
    ).output_text

    (day_dir / "structure.md").write_text(structure_md, encoding="utf-8")

    # 3) 每日扩展阅读（保持你现在版本）
    extended_system = (
        "你是一个结构化阅读简报编辑，用中文输出 Markdown。"
        "你必须严格遵循用户的固定模板标准：按板块（投资、健康、心理/哲学、AI/科技、美学）输出。"
        "每条信息必须写 3–5 句话，包含：主题/切入点、关键洞见、证据/数据（若无实时数据可用请明确标注“需核对”或给出可验证的指标口径）、影响/行动。"
        "每个板块最后必须附上【板块综合评论】。"
        "投资板块除主流媒体/财经新闻 Top10 外，还需额外包含：Twitter/X 投资观点 Top10（如无实时来源，输出为“观察要点+需核对”形式）、税务规划、Estate Planning。"
        "严禁输出“占位符/待补充/模板说明/框架版”等字样，必须输出完整内容。"
    )
    extended_user = (
        f"请生成 {date} 的《每日扩展阅读 Strict》。\n"
        "要求：\n"
        "1) 五个板块：投资、健康（重点抗衰老）、心理/哲学、AI/科技、美学。\n"
        "2) 每个板块列出 Top 10 条（编号 1-10）。每条 3–5 句话，按：主题/切入点 → 关键洞见 → 证据/数据（无法实时获取请写“需核对”并给出应核对的指标/来源口径） → 影响/行动。\n"
        "3) 每个板块最后写【板块综合评论】（不少于 5 句话，给出你对该板块的结构性判断与风险提示）。\n"
        "4) 投资板块额外加入：\n"
        "   - 【X/Twitter Top 10 观点】（同样 1-10，每条 3–5 句；若无实时抓取，用“市场上常见的分歧点/监控指标/触发条件”组织，并标注“需核对”）\n"
        "   - 【税务规划】给 5 条可执行要点（与美股/ETF/期权/海外资产申报相关）\n"
        "   - 【Estate planning】给 5 条可执行要点（信托/受益人/赠与/跨境等）\n"
        "5) 最后写【全局总评】（不少于 8 句话：总结五板块的共同结构、风险等级、未来 1–4 周关注点）。\n"
        "输出 Markdown，标题层级清晰。"
    )

    extended_md = client.responses.create(
        model="gpt-5",
        max_output_tokens=2600,
        input=[
            {"role": "system", "content": extended_system},
            {"role": "user", "content": extended_user},
        ],
    ).output_text

    (day_dir / "extended.md").write_text(extended_md, encoding="utf-8")

    # 4) manifest：用风险颜色上色
    ensure_manifest(date, f"Structure {risk['level']} | Auto-generated")

if __name__ == "__main__":
    main()
