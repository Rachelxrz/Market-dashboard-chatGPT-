import os, json
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

# V2: real market data
import pandas as pd
import yfinance as yf

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = REPO_ROOT / "daily"
MANIFEST = REPO_ROOT / "manifest.json"

# ---------- date helpers ----------
def today_ymd_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def ensure_manifest(date: str, title: str):
    if MANIFEST.exists():
        raw = MANIFEST.read_text(encoding="utf-8").strip()
        data = json.loads(raw or "{}")
    else:
        data = {}

    days = data.get("days", [])
    # update existing date title (avoid duplicates)
    found = False
    for d in days:
        if d.get("date") == date:
            d["title"] = title
            found = True
            break
    if not found:
        days.append({"date": date, "title": title})

    data["days"] = days
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------- OpenAI helper ----------
def gen_text(model: str, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    resp = client.responses.create(
        model=model,
        max_output_tokens=max_tokens,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.output_text or ""

# ---------- market data fetch ----------
TICKERS = {
    # risk / macro
    "VIX": "^VIX",
    "DXY": "DX-Y.NYB",   # Yahoo DXY index symbol
    "TNX": "^TNX",       # 10Y yield * 10 (e.g., 4.25% => 42.5)
    # credit proxies
    "HYG": "HYG",
    "LQD": "LQD",
    # equities / defensives
    "SPY": "SPY",
    "QQQ": "QQQ",
    # real assets
    "GLD": "GLD",
    "SLV": "SLV",
    # rates proxies
    "IEF": "IEF",
    "TLT": "TLT",
}

def _safe_last_close(hist: pd.DataFrame):
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None
    s = hist["Close"].dropna()
    if s.empty:
        return None
    return float(s.iloc[-1])

def _safe_prev_close(hist: pd.DataFrame):
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None
    s = hist["Close"].dropna()
    if len(s) < 2:
        return None
    return float(s.iloc[-2])

def _pct_change(last, prev):
    if last is None or prev is None or prev == 0:
        return None
    return (last / prev - 1.0) * 100.0

def fetch_market_snapshot(period_days: int = 10) -> dict:
    """
    Pull last ~period_days trading data for tickers from Yahoo via yfinance.
    Returns snapshot dict with last/prev closes and 1d % changes.
    """
    symbols = list(TICKERS.values())
    # Use group download for speed & fewer failures
    df = yf.download(
        tickers=" ".join(symbols),
        period=f"{period_days}d",
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="ticker",
        threads=True,
    )

    snapshot = {}
    for name, sym in TICKERS.items():
        try:
            # yfinance returns multi-index columns if multiple tickers
            if isinstance(df.columns, pd.MultiIndex):
                hist = df[sym].copy()
            else:
                # single ticker case
                hist = df.copy()

            last = _safe_last_close(hist)
            prev = _safe_prev_close(hist)
            chg1d = _pct_change(last, prev)

            snapshot[name] = {
                "symbol": sym,
                "last": last,
                "prev": prev,
                "chg1d_pct": chg1d,
            }
        except Exception as e:
            snapshot[name] = {"symbol": sym, "last": None, "prev": None, "chg1d_pct": None, "error": str(e)}

    # Derived metrics
    hyg = snapshot["HYG"]["last"]
    lqd = snapshot["LQD"]["last"]
    hyg_prev = snapshot["HYG"]["prev"]
    lqd_prev = snapshot["LQD"]["prev"]

    def safe_ratio(a, b):
        if a is None or b is None or b == 0:
            return None
        return a / b

    ratio = safe_ratio(hyg, lqd)
    ratio_prev = safe_ratio(hyg_prev, lqd_prev)
    ratio_chg = None
    if ratio is not None and ratio_prev is not None and ratio_prev != 0:
        ratio_chg = (ratio / ratio_prev - 1.0) * 100.0

    snapshot["HYG_LQD"] = {
        "name": "HYG/LQD",
        "last": ratio,
        "prev": ratio_prev,
        "chg1d_pct": ratio_chg,
    }

    return snapshot

# ---------- risk model ----------
def compute_risk(snapshot: dict) -> dict:
    """
    Simple scoring model (transparent & adjustable).
    Outputs: emoji, label, score, triggers list.
    """
    triggers = []
    score = 0

    vix = snapshot.get("VIX", {}).get("last")
    vix_chg = snapshot.get("VIX", {}).get("chg1d_pct")

    dxy_chg = snapshot.get("DXY", {}).get("chg1d_pct")
    tnx = snapshot.get("TNX", {}).get("last")       # e.g., 43.0 means 4.30%
    tnx_chg = snapshot.get("TNX", {}).get("chg1d_pct")

    hyg_lqd_chg = snapshot.get("HYG_LQD", {}).get("chg1d_pct")
    hyg_chg = snapshot.get("HYG", {}).get("chg1d_pct")
    lqd_chg = snapshot.get("LQD", {}).get("chg1d_pct")

    spy_chg = snapshot.get("SPY", {}).get("chg1d_pct")
    qqq_chg = snapshot.get("QQQ", {}).get("chg1d_pct")

    # 1) Volatility
    if vix is not None:
        if vix >= 30:
            score += 4; triggers.append(f"VIX高位({vix:.1f}>=30)")
        elif vix >= 25:
            score += 3; triggers.append(f"VIX偏高({vix:.1f}>=25)")
        elif vix >= 20:
            score += 2; triggers.append(f"VIX抬升({vix:.1f}>=20)")
        elif vix >= 16:
            score += 1; triggers.append(f"VIX温和({vix:.1f}>=16)")

    if vix_chg is not None and vix_chg >= 8:
        score += 1; triggers.append(f"VIX单日跳升(+{vix_chg:.1f}%)")

    # 2) Credit proxy (risk-on/off)
    # If HYG underperforms LQD or HYG/LQD ratio down => spreads widening / risk-off
    if hyg_lqd_chg is not None and hyg_lqd_chg <= -0.30:
        score += 2; triggers.append(f"信用风险抬头(HYG/LQD {hyg_lqd_chg:.2f}%)")
    elif hyg_lqd_chg is not None and hyg_lqd_chg <= -0.15:
        score += 1; triggers.append(f"信用偏弱(HYG/LQD {hyg_lqd_chg:.2f}%)")

    if hyg_chg is not None and lqd_chg is not None:
        if hyg_chg < lqd_chg - 0.20:
            score += 1; triggers.append(f"HYG跑输LQD({hyg_chg:.2f}% vs {lqd_chg:.2f}%)")

    # 3) Dollar + Rates tightening combo
    if dxy_chg is not None and tnx_chg is not None:
        if dxy_chg > 0.25 and tnx_chg > 0.8:
            score += 2; triggers.append(f"美元+利率共振偏紧(DXY {dxy_chg:.2f}% / TNX {tnx_chg:.2f}%)")
        elif dxy_chg > 0.25 or tnx_chg > 0.8:
            score += 1; triggers.append(f"金融条件偏紧(DXY {dxy_chg:.2f}% / TNX {tnx_chg:.2f}%)")

    # 4) Equity stress
    # If SPY/QQQ large down day => raise risk
    for name, chg in [("SPY", spy_chg), ("QQQ", qqq_chg)]:
        if chg is None:
            continue
        if chg <= -2.0:
            score += 2; triggers.append(f"{name}大跌({chg:.2f}%)")
        elif chg <= -1.0:
            score += 1; triggers.append(f"{name}回撤({chg:.2f}%)")

    # Map to emoji
    # 0-2 green, 3-5 yellow, 6-8 orange, 9+ red
    if score >= 9:
        emoji, label = "🔴", "高风险/防守优先"
    elif score >= 6:
        emoji, label = "🟠", "中高风险/谨慎偏防守"
    elif score >= 3:
        emoji, label = "🟡", "中性偏谨慎"
    else:
        emoji, label = "🟢", "风险可控/偏进攻"

    # Additional readable numbers
    tnx_pct = (tnx / 10.0) if tnx is not None else None

    return {
        "emoji": emoji,
        "label": label,
        "score": score,
        "triggers": triggers,
        "tnx_pct": tnx_pct,
    }

def fmt(x, digits=2):
    if x is None:
        return "NA"
    return f"{x:.{digits}f}"

def build_structure_prompt(date: str, snapshot: dict, risk: dict) -> str:
    """
    Provide the model with REAL numbers + our computed triggers.
    Tell it to output numbers explicitly (no '需核对').
    """
    tnx_pct = risk.get("tnx_pct")

    lines = []
    lines.append(f"今天日期是 {date}（UTC）。你必须严格使用这个日期。")
    lines.append(f"输出必须以第一行标题开头：# 结构监控（{date}）")
    lines.append("")
    lines.append("以下为已抓取的真实市场数据（请直接引用这些数值，不要写“需核对”）：")
    lines.append("")
    lines.append("| 指标 | 最新 | 前一日 | 1D变化 |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| VIX | {fmt(snapshot['VIX']['last'],1)} | {fmt(snapshot['VIX']['prev'],1)} | {fmt(snapshot['VIX']['chg1d_pct'],2)}% |")
    lines.append(f"| DXY | {fmt(snapshot['DXY']['last'],2)} | {fmt(snapshot['DXY']['prev'],2)} | {fmt(snapshot['DXY']['chg1d_pct'],2)}% |")
    lines.append(f"| 10Y(TNX) | {fmt(tnx_pct,2)}% | NA | {fmt(snapshot['TNX']['chg1d_pct'],2)}% |")
    lines.append(f"| HYG | {fmt(snapshot['HYG']['last'],2)} | {fmt(snapshot['HYG']['prev'],2)} | {fmt(snapshot['HYG']['chg1d_pct'],2)}% |")
    lines.append(f"| LQD | {fmt(snapshot['LQD']['last'],2)} | {fmt(snapshot['LQD']['prev'],2)} | {fmt(snapshot['LQD']['chg1d_pct'],2)}% |")
    lines.append(f"| HYG/LQD | {fmt(snapshot['HYG_LQD']['last'],4)} | {fmt(snapshot['HYG_LQD']['prev'],4)} | {fmt(snapshot['HYG_LQD']['chg1d_pct'],2)}% |")
    lines.append(f"| SPY | {fmt(snapshot['SPY']['last'],2)} | {fmt(snapshot['SPY']['prev'],2)} | {fmt(snapshot['SPY']['chg1d_pct'],2)}% |")
    lines.append(f"| QQQ | {fmt(snapshot['QQQ']['last'],2)} | {fmt(snapshot['QQQ']['prev'],2)} | {fmt(snapshot['QQQ']['chg1d_pct'],2)}% |")
    lines.append(f"| GLD | {fmt(snapshot['GLD']['last'],2)} | {fmt(snapshot['GLD']['prev'],2)} | {fmt(snapshot['GLD']['chg1d_pct'],2)}% |")
    lines.append(f"| IEF | {fmt(snapshot['IEF']['last'],2)} | {fmt(snapshot['IEF']['prev'],2)} | {fmt(snapshot['IEF']['chg1d_pct'],2)}% |")
    lines.append(f"| TLT | {fmt(snapshot['TLT']['last'],2)} | {fmt(snapshot['TLT']['prev'],2)} | {fmt(snapshot['TLT']['chg1d_pct'],2)}% |")
    lines.append("")

    lines.append("我们用透明规则计算出的当日风险：")
    lines.append(f"- 风险等级：{risk['emoji']}（{risk['label']}）")
    lines.append(f"- 风险评分：{risk['score']}（0-2🟢 / 3-5🟡 / 6-8🟠 / 9+🔴）")
    if risk["triggers"]:
        lines.append("- 触发项：")
        for t in risk["triggers"]:
            lines.append(f"  - {t}")
    else:
        lines.append("- 触发项：无明显风险触发")

    lines.append("")
    lines.append("请基于以上真实数据生成《结构监控（含策略建议）》：")
    lines.append("必须包含这些小节，并且每节都要给出明确判断与可执行动作：")
    lines.append("1) 风险等级（给出一句话理由）")
    lines.append("2) VIX（解释风险含义）")
    lines.append("3) 信用利差/信用风险（用 HYG/LQD 作为代理，解释是否“扩大/收敛”）")
    lines.append("4) 资金流向（无法直接拿到ETF净申购时：用 SPY/QQQ vs IEF/TLT/GLD 的相对强弱 + 风险代理，判断 risk-on/off）")
    lines.append("5) DXY 与 10Y（解释对成长股/黄金/风险资产的影响）")
    lines.append("6) 板块轮动（用 QQQ vs GLD / SPY 的相对表现推断：成长/防御/真实资产）")
    lines.append("7) 风险触发条件（给出清晰阈值，比如 VIX>25、HYG/LQD连续走弱、SPY/QQQ单日> -2% 等）")
    lines.append("8) 可执行策略建议（不追高；加仓/减仓倾向；对冲/止损/分批原则）")
    lines.append("")
    lines.append("输出为 Markdown。不要出现其它年份或日期。")

    return "\n".join(lines)

def main():
    date = today_ymd_utc()
    day_dir = DAILY_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)

    # 1) Fetch real market data
    snapshot = fetch_market_snapshot(period_days=15)
    risk = compute_risk(snapshot)

    # 2) Build prompts using real numbers
    structure_system = "你是一个严谨的宏观与风险结构监控分析师，用中文输出，结构清晰，避免空话。"
    structure_user = build_structure_prompt(date=date, snapshot=snapshot, risk=risk)

    # 3) Generate structure.md (shorter token)
    structure_md = gen_text(
        model="gpt-4.1",
        system_prompt=structure_system,
        user_prompt=structure_user,
        max_tokens=1400,
    )
    (day_dir / "structure.md").write_text(structure_md, encoding="utf-8")

    # 4) Extended reading (optional: keep as-is; still no real news ingestion)
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

    extended_md = gen_text(
        model="gpt-5",
        system_prompt=extended_system,
        user_prompt=extended_user,
        max_tokens=2600,
    )
    (day_dir / "extended.md").write_text(extended_md, encoding="utf-8")

    # 5) manifest title includes risk emoji
    ensure_manifest(date, f"Structure {risk['emoji']} | Auto-generated")

if __name__ == "__main__":
    main()
