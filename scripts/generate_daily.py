#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_daily.py (Version 2.0)
- 生成 daily/{YYYY-MM-DD}/structure.md 结构监控（真·结构监控：先抽取/计算指标 -> 再写报告）
- 生成 daily/{YYYY-MM-DD}/extended.md  每日扩展阅读 Strict
- 更新 manifest.json

✅ 关键改进：
1) 结构监控不再“让模型瞎写数值”，而是：
   - 先从 market_snapshot.json（或你已有的数据管线）拿到 m
   - 用 score_risk(m) 输出 risk_obj（含 snapshot/reasons/actions/triggers）
   - 结构监控 markdown 由 risk_obj +（可选）LLM润色生成
2) 缺数据就降级：明确提示“数据源需修复”，不胡编。
"""

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from openai import OpenAI

# ----------------------------
# Config
# ----------------------------
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = REPO_ROOT / "daily"
MANIFEST = REPO_ROOT / "manifest.json"

# 你可以把外部抓取结果写到这个文件，让本脚本读取
# 建议结构：
# {
#   "series": {
#     "VIX": {"last": 23.57, "chg_1d_pct": 9.93, "chg_3d_pct": 26.52, "chg_5d_pct": 20.56},
#     "SPY": {"last": ..., "chg_3d_pct": ...},
#     "QQQ": {"last": ..., "chg_3d_pct": ...},
#     "GLD": {"last": ..., "chg_3d_pct": ...},
#     "UUP": {"last": ..., "chg_3d_pct": ...},   # optional
#     "TNX": {"last": ..., "chg_3d_pct": ...},   # optional
#     "HYG": {...}, "LQD": {...}                 # optional
#   },
#   "credit_ratio_hyg_lqd": {"chg_3d_pct": -0.15}
# }
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "market_snapshot.json"


# ----------------------------
# Helpers
# ----------------------------
def today_ymd_utc() -> str:
    # 你希望按“美国东部 8:00 AM”生成：在 GitHub Actions cron 控制触发时间
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ensure_manifest(date: str, title: str) -> None:
    if MANIFEST.exists():
        raw = MANIFEST.read_text(encoding="utf-8").strip()
        data = json.loads(raw) if raw else {}
    else:
        data = {}

    days = data.get("days", [])
    if not any(d.get("date") == date for d in days):
        days.append({"date": date, "title": title})
    else:
        # 如果同一天已存在，就更新 title（避免旧风险色残留）
        for d in days:
            if d.get("date") == date:
                d["title"] = title

    data["days"] = days
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def gen_text(model: str, system_prompt: str, user_prompt: str, max_output_tokens: int) -> str:
    resp = client.responses.create(
        model=model,
        max_output_tokens=max_output_tokens,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.output_text or ""


def load_market_snapshot() -> Dict[str, Any]:
    """
    真·结构监控的数据入口：m
    - 优先：环境变量 MARKET_SNAPSHOT_PATH 指定路径
    - 其次：repo 根目录 market_snapshot.json
    - 都没有：返回空（会自动降级）
    """
    p = os.environ.get("MARKET_SNAPSHOT_PATH", "").strip()
    path = Path(p) if p else DEFAULT_SNAPSHOT_PATH
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def safe_num(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


# ----------------------------
# Continuous Trend Risk Model (0-12)
# ----------------------------
def score_risk(m: dict) -> dict:
    """
    连续趋势评分 (0-12) -> 风险颜色
    只用你现在能稳定抓到的数据:
      VIX, SPY, QQQ, GLD, UUP(可选), TNX(可选), credit_ratio_hyg_lqd
    """
    score = 0
    reasons = []
    triggers = []
    actions = []

    series = (m.get("series") or {})

    vix = (series.get("VIX") or {})
    spy = (series.get("SPY") or {})
    qqq = (series.get("QQQ") or {})
    gld = (series.get("GLD") or {})
    uup = (series.get("UUP") or {})
    tnx = (series.get("TNX") or {})

    # levels
    vix_last = safe_num(vix.get("last"))
    spy_last = safe_num(spy.get("last"))
    qqq_last = safe_num(qqq.get("last"))
    gld_last = safe_num(gld.get("last"))
    uup_last = safe_num(uup.get("last"))
    tnx_last = safe_num(tnx.get("last"))

    # changes
    vix_1d = safe_num(vix.get("chg_1d_pct"))
    vix_3d = safe_num(vix.get("chg_3d_pct"))
    vix_5d = safe_num(vix.get("chg_5d_pct"))

    spy_3d = safe_num(spy.get("chg_3d_pct"))
    qqq_3d = safe_num(qqq.get("chg_3d_pct"))
    gld_3d = safe_num(gld.get("chg_3d_pct"))

    uup_3d = safe_num(uup.get("chg_3d_pct"))
    tnx_3d = safe_num(tnx.get("chg_3d_pct"))

    # credit ratio (HYG/LQD)
    cr_3d = safe_num((m.get("credit_ratio_hyg_lqd") or {}).get("chg_3d_pct"))

    # -------- 缺数据提示（先做，再打分）--------
    def missing(val):
        return val is None

    if missing(vix_last): reasons.append("缺数据：VIX last（抓取失败/源缺失）")
    if missing(vix_3d):  reasons.append("缺数据：VIX chg_3d_pct")
    if missing(cr_3d):   reasons.append("缺数据：credit_ratio_hyg_lqd chg_3d_pct")
    if missing(spy_3d):  reasons.append("缺数据：SPY chg_3d_pct")
    if missing(qqq_3d):  reasons.append("缺数据：QQQ chg_3d_pct")
    if missing(gld_3d):  reasons.append("缺数据：GLD chg_3d_pct")
    if missing(uup_3d):  reasons.append("缺数据：UUP chg_3d_pct（美元代理，可选）")
    if missing(tnx_3d):  reasons.append("缺数据：TNX chg_3d_pct（10Y，可选）")

    required = [vix_last, vix_3d, cr_3d, spy_3d, qqq_3d, gld_3d]
    scorable = all(not missing(x) for x in required)

    if not scorable:
        return {
            "score": None,
            "risk": "🟡",
            "data_quality": "degraded",
            "reasons": reasons + ["⚠️ 核心数据缺失：今日评分/策略建议降级为“定性提示”。先修复数据源。"],
            "triggers": [
                "修复数据源后再启用评分：必须拿到 VIX(last,3D)、cr_3D、SPY_3D、QQQ_3D、GLD_3D"
            ],
            "actions": [
                "先不要根据缺数据的报告做大幅操作；优先修复抓取与字段映射"
            ],
            "snapshot": {
                "vix_last": vix_last, "vix_1d": vix_1d, "vix_3d": vix_3d, "vix_5d": vix_5d,
                "cr_3d": cr_3d,
                "spy_last": spy_last, "spy_3d": spy_3d,
                "qqq_last": qqq_last, "qqq_3d": qqq_3d,
                "gld_last": gld_last, "gld_3d": gld_3d,
                "uup_last": uup_last, "uup_3d": uup_3d,
                "tnx_last": tnx_last, "tnx_3d": tnx_3d,
            }
        }

    # -------- A. VIX 水平（0-3）--------
    if vix_last >= 30:
        score += 3
        reasons.append(f"VIX高位({vix_last:.2f}≥30)：系统性压力")
        triggers.append("若 VIX 连续2天≥30：进入防守/降杠杆优先")
    elif vix_last >= 20:
        score += 2
        reasons.append(f"VIX进入高波动区({vix_last:.2f}∈[20,30))")
        triggers.append("若 VIX ≥25 且 3D继续上行：风险升级")
    elif vix_last >= 16:
        score += 1
        reasons.append(f"VIX抬升({vix_last:.2f}∈[16,20))：风险偏好走弱")
    else:
        reasons.append(f"VIX低位({vix_last:.2f}<16)：波动层面偏稳")

    # -------- B. VIX 趋势（0-2）--------
    if vix_3d >= 15:
        score += 2
        reasons.append(f"VIX 3D升温({vix_3d:+.2f}%)：波动趋势恶化")
    elif vix_3d >= 5:
        score += 1
        reasons.append(f"VIX 3D上行({vix_3d:+.2f}%)：波动抬升")

    # -------- C. 信用风险（0-3）--------
    # 这里按你此前逻辑：cr_3d < 0 = 信用恶化（risk-off）
    if cr_3d <= -1.5:
        score += 3
        reasons.append(f"信用显著走弱(cr_3d={cr_3d:+.2f}%)：risk-off 信号强")
        triggers.append("若 cr_3d 继续下探且 VIX>20：优先控仓/防守")
    elif cr_3d < 0:
        score += 2
        reasons.append(f"信用走弱(cr_3d={cr_3d:+.2f}%)：风险偏好下降")
    elif cr_3d > 0.8:
        score -= 1
        reasons.append(f"信用改善(cr_3d={cr_3d:+.2f}%)：对冲部分风险")
    else:
        reasons.append(f"信用中性(cr_3d={cr_3d:+.2f}%)")

    # -------- D. 风险资产压力（0-2）--------
    if spy_3d <= -2 and qqq_3d <= -2:
        score += 2
        reasons.append(f"股指同步走弱(SPY3D={spy_3d:+.2f}%, QQQ3D={qqq_3d:+.2f}%)")
    elif qqq_3d <= -2 or spy_3d <= -2:
        score += 1
        reasons.append(f"股指出现压力(SPY3D={spy_3d:+.2f}%, QQQ3D={qqq_3d:+.2f}%)")

    # -------- E. 去杠杆结构（0-2）--------
    # VIX↑ 且 QQQ↓ 且 GLD↓ => “去杠杆/现金为王”
    if vix_3d > 0 and qqq_3d < 0 and gld_3d < 0:
        score += 2
        reasons.append(
            f"去杠杆结构：VIX↑({vix_3d:+.2f}%) 且 QQQ↓({qqq_3d:+.2f}%) 且 GLD↓({gld_3d:+.2f}%)"
        )
        triggers.append("若该结构连续2天：避免抄底，优先防守与分批")

    # 截断到 0-12
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

    # 动作建议（与评分绑定）
    if risk in ("🟠", "🔴"):
        actions += [
            "不追高；减少高beta加仓冲动（尤其在 VIX>20 且信用走弱时）",
            "若必须操作：以“分批/小步/可撤回”为原则，优先降低波动来源（高估值成长/高杠杆）",
            "防守侧：保留现金/短债；对冲可用保护性put或降低净敞口（视账户权限）",
        ]
    elif risk == "🟡":
        actions += [
            "以持仓管理为主：不追涨、回撤分批，避免一次性重仓",
            "关注信用与VIX是否同向恶化：一旦出现，立刻转防守节奏",
        ]
    else:
        actions += [
            "风险偏稳：可按计划执行轮动，但仍避免情绪化加杠杆",
        ]

    # 可选：美元/利率辅助观测（不参与核心评分）
    if (uup_3d is not None) and (tnx_3d is not None):
        reasons.append(f"辅助观测：UUP3D={uup_3d:+.2f}%；TNX3D={tnx_3d:+.2f}%（美元/利率压力）")

    return {
        "score": score,
        "risk": risk,
        "data_quality": "ok",
        "reasons": reasons,
        "triggers": triggers,
        "actions": actions,
        "snapshot": {
            "vix_last": vix_last,
            "vix_1d": vix_1d, "vix_3d": vix_3d, "vix_5d": vix_5d,
            "cr_3d": cr_3d,
            "spy_last": spy_last, "spy_3d": spy_3d,
            "qqq_last": qqq_last, "qqq_3d": qqq_3d,
            "gld_last": gld_last, "gld_3d": gld_3d,
            "uup_last": uup_last, "uup_3d": uup_3d,
            "tnx_last": tnx_last, "tnx_3d": tnx_3d,
        }
    }


# ----------------------------
# Structure report builder (deterministic first, then optional LLM polish)
# ----------------------------
def build_structure_md(date: str, risk_obj: dict) -> str:
    """
    结构监控：优先用确定性模板（不依赖 LLM）保证“真·结构监控”可用
    你也可以在 main() 里打开 LLM 润色（可选）
    """
    snap = risk_obj.get("snapshot", {}) or {}
    score = risk_obj.get("score")
    risk = risk_obj.get("risk", "🟡")
    dq = risk_obj.get("data_quality", "ok")

    def f(x, nd=2, pct=False):
        if x is None:
            return "NA"
        try:
            if pct:
                return f"{float(x):+.{nd}f}%"
            return f"{float(x):.{nd}f}"
        except Exception:
            return "NA"

    lines = []
    lines.append(f"# 结构监控（{date}）")
    lines.append("")
    lines.append("## 1) 风险等级与评分")
    if score is None:
        lines.append(f"- 风险等级：{risk}（数据降级）")
        lines.append(f"- 风险评分：NA（核心数据缺失）")
    else:
        lines.append(f"- 风险等级：{risk}")
        lines.append(f"- 风险评分：{score} / 12")
    lines.append(f"- 数据质量：{dq}")
    lines.append("")

    lines.append("## 2) 核心指标快照（自动抽取）")
    lines.append(f"- VIX：{f(snap.get('vix_last'))}；1D {f(snap.get('vix_1d'), pct=True)} / 3D {f(snap.get('vix_3d'), pct=True)} / 5D {f(snap.get('vix_5d'), pct=True)}")
    lines.append(f"- 信用（HYG/LQD 比例 3D）：{f(snap.get('cr_3d'), pct=True)}（<0 通常视为信用走弱）")
    lines.append(f"- SPY 3D：{f(snap.get('spy_3d'), pct=True)}；QQQ 3D：{f(snap.get('qqq_3d'), pct=True)}；GLD 3D：{f(snap.get('gld_3d'), pct=True)}")
    lines.append(f"- UUP 3D（可选）：{f(snap.get('uup_3d'), pct=True)}；TNX 3D（可选）：{f(snap.get('tnx_3d'), pct=True)}")
    lines.append("")

    lines.append("## 3) 结构解读（原因链）")
    for r in (risk_obj.get("reasons") or [])[:12]:
        lines.append(f"- {r}")
    lines.append("")

    lines.append("## 4) 风险触发条件（如果继续恶化/转好）")
    if risk_obj.get("triggers"):
        for t in risk_obj["triggers"][:10]:
            lines.append(f"- {t}")
    else:
        lines.append("- 暂无（或数据不足以生成触发条件）")
    lines.append("")

    lines.append("## 5) 可执行策略建议（不追高 / 加减仓倾向）")
    if risk_obj.get("actions"):
        for a in risk_obj["actions"][:10]:
            lines.append(f"- {a}")
    else:
        lines.append("- 数据不足：先修复抓取，再输出策略")
    lines.append("")

    return "\n".join(lines)


# ----------------------------
# Main
# ----------------------------
def main():
    date = today_ymd_utc()
    day_dir = DAILY_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)

    # 0) Load market snapshot (m)
    m = load_market_snapshot()

    # 1) 先评分（真·结构监控的核心）
    risk_obj = score_risk(m)

    # 2) 结构监控（确定性模板输出，保证不胡编）
    structure_md = build_structure_md(date, risk_obj)

    # （可选）LLM润色：仍然禁止编造数值（默认关闭更稳）
    # 如果你想开：把 ENABLE_LLM_POLISH=1 写进 Actions env
    if os.environ.get("ENABLE_LLM_POLISH", "").strip() == "1":
        structure_system = "你是严谨的宏观与风险结构监控分析师，用中文输出，结构清晰，避免空话。禁止编造任何数值。"
        structure_user = f"""
今天日期是 {date}（UTC）。请基于下方 JSON 生成《结构监控（含策略建议）》。
硬性要求：
1) 标题必须是第一行：# 结构监控（{date}）
2) 只允许引用 JSON 内给出的数值；禁止编造；缺失就写 NA 并给“修复建议”
3) 必须包含：风险等级+评分、VIX、信用、资金偏好/去杠杆、DXY/10Y（如果有）、触发条件、可执行策略建议
4) 输出 Markdown，不要出现其它年份或日期

JSON：
{json.dumps(risk_obj, ensure_ascii=False, indent=2)}
"""
        structure_md = gen_text(
            model="gpt-4.1",
            system_prompt=structure_system,
            user_prompt=structure_user,
            max_output_tokens=1200,
        )

    (day_dir / "structure.md").write_text(structure_md, encoding="utf-8")

    # 3) 每日扩展阅读 Strict（与你之前一致：模型输出为主）
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
        max_output_tokens=2600,
    )
    (day_dir / "extended.md").write_text(extended_md, encoding="utf-8")

    # 4) manifest：标题里放🟢🟡🟠🔴，首页会自动着色
    risk = risk_obj.get("risk", "🟡")
    ensure_manifest(date, f"Structure {risk} | Auto-generated")

    # 5) 可选：把 risk_obj 也存档，方便 debug
    if os.environ.get("SAVE_RISK_JSON", "").strip() == "1":
        (day_dir / "risk.json").write_text(json.dumps(risk_obj, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
