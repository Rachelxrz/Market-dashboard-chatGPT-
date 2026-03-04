# scripts/generate_daily.py
import os
import json
import time
import math
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

from openai import OpenAI


# =========================
# Config
# =========================
REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = REPO_ROOT / "daily"
MANIFEST = REPO_ROOT / "manifest.json"

# 你要的核心标的（连续趋势模型）
TICKERS = {
    "VIX": "^VIX",
    "SPY": "SPY",
    "QQQ": "QQQ",
    "GLD": "GLD",
    "HYG": "HYG",
    "LQD": "LQD",
    # 可选：
    "UUP": "UUP",      # 美元代理
    "TNX": "^TNX",     # 10Y proxy（Yahoo 上是利率*10；通常 last ~ 4.2 => 42 左右）
}

# Yahoo chart：建议取更长一点，避免 3d/5d 不够
YAHOO_RANGE = "6mo"
YAHOO_INTERVAL = "1d"

HTTP_TIMEOUT = 20
HTTP_RETRIES = 3
SLEEP_BETWEEN_RETRIES = 1.2


# =========================
# Helpers: date / manifest
# =========================
def today_ymd_et() -> str:
    """用美东日期做文件夹日期（cron 8:00 ET 触发时更一致）"""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    return now_et.strftime("%Y-%m-%d")


def ensure_manifest(date: str, title: str):
    if MANIFEST.exists():
        raw = MANIFEST.read_text(encoding="utf-8").strip()
        data = json.loads(raw) if raw else {}
    else:
        data = {}

    days = data.get("days", [])
    if not any(d.get("date") == date for d in days):
        days.append({"date": date, "title": title})
    data["days"] = days
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================
# Yahoo fetch (no yfinance)
# =========================
def _yahoo_chart_url(symbol: str, range_: str, interval: str) -> str:
    sym = urllib.parse.quote(symbol, safe="")
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={range_}&interval={interval}&includePrePost=false&events=div%2Csplits"


def fetch_yahoo_daily_closes(symbol: str, range_: str = YAHOO_RANGE, interval: str = YAHOO_INTERVAL):
    """
    Return: list of (ts_utc:int, close:float) sorted by time asc, filtered close != None
    """
    url = _yahoo_chart_url(symbol, range_, interval)
    last_err = None

    for _ in range(HTTP_RETRIES):
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            j = r.json()

            result = j.get("chart", {}).get("result")
            if not result:
                raise RuntimeError(f"Yahoo chart empty result for {symbol}")

            res0 = result[0]
            ts = res0.get("timestamp") or []
            closes = (res0.get("indicators", {}).get("quote", [{}])[0].get("close") or [])

            out = []
            for t, c in zip(ts, closes):
                if c is None:
                    continue
                out.append((int(t), float(c)))

            # 确保升序
            out.sort(key=lambda x: x[0])
            if len(out) < 15:
                # 太短会导致 10d/5d 计算缺失
                raise RuntimeError(f"Too few data points for {symbol}: {len(out)}")

            return out

        except Exception as e:
            last_err = e
            time.sleep(SLEEP_BETWEEN_RETRIES)

    raise RuntimeError(f"Yahoo fetch failed for {symbol}: {last_err}")


def pct_change(a: float, b: float) -> float:
    """(a/b - 1) * 100, safe"""
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1.0) * 100.0


def sma(values, n: int):
    if values is None or len(values) < n:
        return None
    return sum(values[-n:]) / float(n)


def compute_series_metrics(symbol: str, closes_ts):
    """
    closes_ts: list[(ts, close)] asc
    Return dict with:
      last, chg_1d_pct, chg_3d_pct, chg_5d_pct, chg_10d_pct, sma_3, sma_5, sma_10
    """
    closes = [c for _, c in closes_ts]
    last = closes[-1]

    # 注意：1d/3d/5d/10d = 用“交易日步长”，不是自然日
    def _lag(n):
        if len(closes) <= n:
            return None
        return closes[-1 - n]

    chg_1d = pct_change(last, _lag(1))
    chg_3d = pct_change(last, _lag(3))
    chg_5d = pct_change(last, _lag(5))
    chg_10d = pct_change(last, _lag(10))

    return {
        "symbol": symbol,
        "last": last,
        "chg_1d_pct": chg_1d,
        "chg_3d_pct": chg_3d,
        "chg_5d_pct": chg_5d,
        "chg_10d_pct": chg_10d,
        "sma_3": sma(closes, 3),
        "sma_5": sma(closes, 5),
        "sma_10": sma(closes, 10),
    }


# =========================
# Risk model (continuous trend score 0-12)
# =========================
def missing(v) -> bool:
    return v is None


def score_risk(m: dict) -> dict:
    """
    连续趋势评分（0-12）-> 风险颜色
    只用你明确能抓到的数据：
      VIX, HYG/LQD ratio, SPY, QQQ, GLD, (可选 UUP, TNX)
    """
    score = 0
    reasons = []

    series = (m.get("series") or {})
    vix = (series.get("VIX") or {})
    spy = (series.get("SPY") or {})
    qqq = (series.get("QQQ") or {})
    gld = (series.get("GLD") or {})
    uup = (series.get("UUP") or {})
    tnx = (series.get("TNX") or {})

    vix_last = vix.get("last")
    vix_3d = vix.get("chg_3d_pct")
    vix_1d = vix.get("chg_1d_pct")
    vix_5d = vix.get("chg_5d_pct")

    spy_3d = spy.get("chg_3d_pct")
    spy_5d = spy.get("chg_5d_pct")

    qqq_3d = qqq.get("chg_3d_pct")
    gld_3d = gld.get("chg_3d_pct")

    uup_3d = uup.get("chg_3d_pct")
    tnx_3d = tnx.get("chg_3d_pct")

    cr_3d = (m.get("credit_ratio_hyg_lqd") or {}).get("chg_3d_pct")  # HYG/LQD

    # 缺数据提示（显示在 report）
    if missing(vix_last): reasons.append("缺数据：VIX last（抓取失败/源缺失）")
    if missing(vix_3d):   reasons.append("缺数据：VIX chg_3d_pct")
    if missing(cr_3d):    reasons.append("缺数据：credit_ratio_hyg_lqd chg_3d_pct")
    if missing(spy_3d):   reasons.append("缺数据：SPY chg_3d_pct")
    if missing(qqq_3d):   reasons.append("缺数据：QQQ chg_3d_pct")
    if missing(gld_3d):   reasons.append("缺数据：GLD chg_3d_pct")
    if missing(uup_3d):   reasons.append("缺数据：UUP chg_3d_pct（美元代理，可选）")
    if missing(tnx_3d):   reasons.append("缺数据：TNX chg_3d_pct（10Y，可选）")

    # 核心门控：缺核心就降级（不输出硬评分）
    required = [vix_3d, cr_3d, spy_3d, qqq_3d, gld_3d]
    scorable = all(not missing(x) for x in required)
    data_quality = "ok" if scorable else "degraded"

    if not scorable:
        return {
            "score": None,
            "level": "🟡",
            "data_quality": data_quality,
            "reasons": reasons + ["⚠️ 核心数据缺失：风险评分/策略建议已降级为“定性提示”，请先修复数据源。"],
            "snapshot": {
                "VIX_last": vix_last, "VIX_1d": vix_1d, "VIX_3d": vix_3d, "VIX_5d": vix_5d,
                "CR_3d": cr_3d,
                "SPY_3d": spy_3d, "SPY_5d": spy_5d,
                "QQQ_3d": qqq_3d, "GLD_3d": gld_3d,
                "UUP_3d": uup_3d, "TNX_3d": tnx_3d,
            },
        }

    # ===== 连续趋势规则（可继续加权，但先把骨架定住）=====
    # 1) VIX 高位 & 上升
    if vix_last is not None:
        if vix_last >= 25:
            score += 3; reasons.append("VIX≥25（高波动风险区）")
        elif vix_last >= 20:
            score += 2; reasons.append("VIX 20–25（偏高波动）")
        elif vix_last >= 16:
            score += 1; reasons.append("VIX 16–20（波动抬升）")

    if vix_3d is not None and vix_3d >= 12:
        score += 2; reasons.append("VIX 3D 快速升温（≥+12%）")
    elif vix_3d is not None and vix_3d > 0:
        score += 1; reasons.append("VIX 3D 上行（风险升温）")

    # 2) 信用：HYG/LQD 比值走弱（<0 表示信用偏弱）
    if cr_3d is not None and cr_3d < 0:
        score += 2; reasons.append("HYG/LQD 3D 走弱（信用风险抬升）")
    elif cr_3d is not None and cr_3d > 0:
        score -= 1; reasons.append("HYG/LQD 3D 走强（信用改善）")

    # 3) 风险资产走势：SPY/QQQ 下行加分（风险上升）
    if spy_3d is not None and spy_3d < 0:
        score += 1; reasons.append("SPY 3D 下行（risk-off 倾向）")
    if qqq_3d is not None and qqq_3d < 0:
        score += 1; reasons.append("QQQ 3D 下行（成长承压）")

    # 4) “去杠杆”特征：VIX↑ + SPY↓ + GLD↓（连避险也跌）
    if (vix_3d is not None and spy_3d is not None and gld_3d is not None):
        if vix_3d > 0 and spy_3d < 0 and gld_3d < 0:
            score += 2; reasons.append("VIX↑ + SPY↓ + GLD↓（去杠杆/流动性收缩特征）")

    # 5) 风险钝化：VIX↑但 SPY↑（减分）
    if (vix_3d is not None and spy_3d is not None):
        if vix_3d > 0 and spy_3d > 0:
            score -= 1; reasons.append("VIX↑但 SPY↑（可能为对冲需求/事件波动，非全面 risk-off）")

    # 6) 可选宏观：美元/利率的方向（轻权重）
    if uup_3d is not None and uup_3d > 0:
        score += 1; reasons.append("美元走强（风险资产/大宗可能承压）")
    if tnx_3d is not None and tnx_3d > 0:
        score += 1; reasons.append("10Y 上行（估值压力/金融条件偏紧）")

    # clamp 0..12
    score = max(0, min(12, score))

    # 映射风险颜色
    if score <= 2:
        level = "🟢"
    elif score <= 5:
        level = "🟡"
    elif score <= 8:
        level = "🟠"
    else:
        level = "🔴"

    return {
        "score": score,
        "level": level,
        "data_quality": data_quality,
        "reasons": reasons,
        "snapshot": {
            "VIX_last": vix_last, "VIX_1d": vix_1d, "VIX_3d": vix_3d, "VIX_5d": vix_5d,
            "CR_3d": cr_3d,
            "SPY_3d": spy_3d, "SPY_5d": spy_5d,
            "QQQ_3d": qqq_3d, "GLD_3d": gld_3d,
            "UUP_3d": uup_3d, "TNX_3d": tnx_3d,
        },
    }


def fmt(x, digits=2):
    if x is None:
        return "NA"
    try:
        return f"{x:.{digits}f}"
    except Exception:
        return str(x)


def format_metrics_for_prompt(m: dict, risk: dict) -> str:
    """
    给模型用的“已计算数据块”，模型只能用这里面的数字做判断
    """
    series = (m.get("series") or {})
    vix = (series.get("VIX") or {})
    spy = (series.get("SPY") or {})
    qqq = (series.get("QQQ") or {})
    gld = (series.get("GLD") or {})
    uup = (series.get("UUP") or {})
    tnx = (series.get("TNX") or {})
    cr = (m.get("credit_ratio_hyg_lqd") or {})

    lines = []
    lines.append("【已计算数据】（只能使用这些数据，禁止编造）")
    lines.append(f"- 风险评分: {risk.get('score')} / 12")
    lines.append(f"- 风险等级: {risk.get('level')}（data_quality={risk.get('data_quality')}）")
    lines.append("")
    lines.append("【VIX】")
    lines.append(f"- last={fmt(vix.get('last'))} | 1D={fmt(vix.get('chg_1d_pct'))}% | 3D={fmt(vix.get('chg_3d_pct'))}% | 5D={fmt(vix.get('chg_5d_pct'))}%")
    lines.append("")
    lines.append("【信用（HYG/LQD）】")
    lines.append(f"- ratio_last={fmt(cr.get('ratio_last'), 4)} | 3D={fmt(cr.get('chg_3d_pct'))}% | 5D={fmt(cr.get('chg_5d_pct'))}%")
    lines.append("")
    lines.append("【风险资产代理】")
    lines.append(f"- SPY: last={fmt(spy.get('last'))} | 3D={fmt(spy.get('chg_3d_pct'))}% | 5D={fmt(spy.get('chg_5d_pct'))}%")
    lines.append(f"- QQQ: last={fmt(qqq.get('last'))} | 3D={fmt(qqq.get('chg_3d_pct'))}% | 5D={fmt(qqq.get('chg_5d_pct'))}%")
    lines.append(f"- GLD: last={fmt(gld.get('last'))} | 3D={fmt(gld.get('chg_3d_pct'))}% | 5D={fmt(gld.get('chg_5d_pct'))}%")
    lines.append("")
    lines.append("【可选宏观代理】")
    lines.append(f"- UUP(美元): last={fmt(uup.get('last'))} | 3D={fmt(uup.get('chg_3d_pct'))}%")
    lines.append(f"- TNX(10Y): last={fmt(tnx.get('last'))} | 3D={fmt(tnx.get('chg_3d_pct'))}%")
    lines.append("")
    if risk.get("reasons"):
        lines.append("【模型原因（reasons）】")
        for r in risk["reasons"]:
            lines.append(f"- {r}")
    return "\n".join(lines)


# =========================
# LLM generation
# =========================
def llm_text(client: OpenAI, model: str, system_prompt: str, user_prompt: str, max_output_tokens: int) -> str:
    resp = client.responses.create(
        model=model,
        max_output_tokens=max_output_tokens,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.output_text or ""


def write_md(path: Path, text: str):
    path.write_text(text.strip() + "\n", encoding="utf-8")


# =========================
# Build metrics m
# =========================
def build_metrics() -> dict:
    series = {}
    errors = {}

    # 1) fetch series
    for name, sym in TICKERS.items():
        try:
            closes = fetch_yahoo_daily_closes(sym)
            series[name] = compute_series_metrics(sym, closes)
        except Exception as e:
            errors[name] = str(e)
            series[name] = {"symbol": sym}  # keep key exists

    # 2) credit ratio HYG/LQD
    hyg_last = (series.get("HYG") or {}).get("last")
    lqd_last = (series.get("LQD") or {}).get("last")

    def _get_chg(name, key):
        return (series.get(name) or {}).get(key)

    ratio_last = None
    if hyg_last is not None and lqd_last is not None and lqd_last != 0:
        ratio_last = hyg_last / lqd_last

    # 用“变化率差近似”不严谨，所以这里用“ratio 的历史”更好；
    # 但我们没保存历史 ratio 序列，为了稳健：用 3D/5D 的“相对变化”近似：
    # ratio_chg ≈ (1+hyg_chg)/(1+lqd_chg) - 1
    def _ratio_chg(hchg, lchg):
        if hchg is None or lchg is None:
            return None
        return ((1 + hchg / 100.0) / (1 + lchg / 100.0) - 1) * 100.0

    cr_3d = _ratio_chg(_get_chg("HYG", "chg_3d_pct"), _get_chg("LQD", "chg_3d_pct"))
    cr_5d = _ratio_chg(_get_chg("HYG", "chg_5d_pct"), _get_chg("LQD", "chg_5d_pct"))

    m = {
        "asof_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "series": series,
        "credit_ratio_hyg_lqd": {
            "ratio_last": ratio_last,
            "chg_3d_pct": cr_3d,
            "chg_5d_pct": cr_5d,
        },
        "fetch_errors": errors,
    }
    return m


# =========================
# Main
# =========================
def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY env var")

    client = OpenAI(api_key=api_key)

    date = today_ymd_et()
    day_dir = DAILY_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)

    # 1) 抓数据 + 算指标 m
    metrics = build_metrics()
    risk = score_risk(metrics)

    # 保存机器可读
    write_md(day_dir / "metrics.json", json.dumps({"date": date, "metrics": metrics, "risk": risk}, ensure_ascii=False, indent=2))

    # 2) 结构监控：必须基于“已计算数据”
    structure_system = (
        "你是一个严谨的宏观与风险结构监控分析师。"
        "你必须只使用用户提供的【已计算数据】做结论，禁止编造任何数值。"
        "若数据为NA或data_quality=degraded，必须明确写“数据降级/需修复数据源”，并避免给出确定性结论。"
        "输出中文Markdown，结构清晰、可执行。"
    )
    metrics_block = format_metrics_for_prompt(metrics, risk)

    structure_user = (
        f"今天日期是 {date}（美东日期）。请生成今日《结构监控（含策略建议）》并严格使用这个日期。\n"
        f"输出必须以第一行标题开头：# 结构监控（{date}）\n\n"
        f"{metrics_block}\n\n"
        "必须包含以下小节（按顺序）：\n"
        "1) 风险等级与评分（🟢🟡🟠🔴之一 + score/12 + data_quality）\n"
        "2) 核心指标快照（把 VIX、HYG/LQD、SPY、QQQ、GLD、UUP、TNX 的 last 与 1D/3D/5D 摘出来；NA要如实写）\n"
        "3) 结构解读（原因链：波动→信用→风险资产→是否去杠杆/钝化；只用数据推导）\n"
        "4) 风险触发条件（给出 3-5 条“若…则…”的阈值/方向型触发，基于数据口径）\n"
        "5) 可执行策略建议（不追高、加仓/减仓倾向、对冲/现金管理；如果数据降级，策略必须更保守）\n"
    )

    structure_md = llm_text(
        client=client,
        model="gpt-5",
        system_prompt=structure_system,
        user_prompt=structure_user,
        max_output_tokens=1400,
    )
    write_md(day_dir / "structure.md", structure_md)

    # 3) Strict 扩展阅读：拆分多次调用，避免“只出两条”
    extended_system = (
        "你是一个结构化阅读简报编辑。用中文输出 Markdown。\n"
        "强制规则：每条信息必须 3–5 句话（主题/切入点→关键洞见→证据/数据(无实时就写需核对+口径)→影响/行动）。\n"
        "严禁输出占位符。"
    )

    def gen_section(title: str, spec: str, out_tokens: int) -> str:
        user = (
            f"日期：{date}。\n"
            f"请输出《每日扩展阅读 Strict》中的【{title}】板块。\n"
            "要求：Top 10（编号1-10），每条3–5句话，严格按：主题/切入点→关键洞见→证据/数据（需核对则写口径）→影响/行动。\n"
            "板块末尾必须有【板块综合评论】不少于5句话。\n"
            f"补充要求：{spec}\n"
            "输出Markdown。"
        )
        return llm_text(client, "gpt-5", extended_system, user, out_tokens)

    invest = gen_section(
        "投资",
        "除主流媒体/财经新闻Top10外，追加：\n"
        "A) 【X/Twitter Top 10 观点】同样1-10，每条3–5句（无实时抓取则用“常见分歧点/监控指标/触发条件”组织并标注需核对）。\n"
        "B) 【税务规划】5条可执行要点（美股/ETF/期权/海外资产申报相关）。\n"
        "C) 【Estate planning】5条可执行要点（信托/受益人/赠与/跨境）。",
        out_tokens=3200,
    )
    health = gen_section("健康（重点抗衰老）", "优先覆盖：代谢/炎症/肌肉骨骼/睡眠/皮肤与激素环境。", out_tokens=2200)
    psych = gen_section("心理/哲学", "强调：情绪与决策偏差、风险承受、注意力与意义结构。", out_tokens=2200)
    ai = gen_section("AI/科技", "强调：算力/模型迭代/监管/企业落地。", out_tokens=2200)
    aesthetic = gen_section("美学", "强调：艺术训练方法、审美趋势与创作行动建议。", out_tokens=2000)

    # 全局总评单独来
    global_user = (
        f"日期：{date}。\n"
        "请输出《每日扩展阅读 Strict》的【全局总评】。\n"
        "不少于8句话：总结五板块共同结构、风险等级、未来1–4周关注点与关键风险。\n"
        "输出Markdown，仅该段，不要重复前文。"
    )
    global_review = llm_text(client, "gpt-5", extended_system, global_user, max_output_tokens=900)

    extended_md = "\n\n".join([
        f"# 每日扩展阅读 Strict | {date}",
        invest, health, psych, ai, aesthetic,
        "## 全局总评",
        global_review
    ])
    write_md(day_dir / "extended.md", extended_md)

    # 4) manifest：标题里放🟢🟡🟠🔴
    risk_emoji = risk.get("level") or "🟡"
    ensure_manifest(date, f"Structure {risk_emoji} | Auto-generated")


if __name__ == "__main__":
    main()
