import os, json
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

# --- OpenAI client ---
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# --- Paths ---
REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = REPO_ROOT / "daily"
MANIFEST = REPO_ROOT / "manifest.json"


def today_ymd_utc() -> str:
    # 由 GitHub Actions 的 cron 控制触发时间（你想按美东 8:00AM 跑）
    # 这里用触发时刻的 UTC 日期作为归档文件夹名
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ensure_manifest(date: str, title: str) -> None:
    if MANIFEST.exists():
        raw = MANIFEST.read_text(encoding="utf-8").strip()
        data = json.loads(raw or "{}")
    else:
        data = {}

    days = data.get("days", [])
    if not any(d.get("date") == date for d in days):
        days.append({"date": date, "title": title})

    data["days"] = days
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def call_model(*, model: str, system_prompt: str, user_prompt: str, max_output_tokens: int) -> str:
    resp = client.responses.create(
        model=model,
        max_output_tokens=max_output_tokens,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return (resp.output_text or "").strip()


def main():
    date = today_ymd_utc()
    day_dir = DAILY_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)

    # =========================================================
    # 1) 结构监控（含策略建议）——标题/日期由代码写死，模型只写正文
    # =========================================================
    structure_system = (
        "你是一个严谨的宏观与风险结构监控分析师。"
        "用中文输出，结构清晰，避免空话，给出可执行建议。"
    )
    structure_user = (
        "请生成《结构监控（含策略建议）》的【正文内容】。\n"
        "不要写标题，不要写日期，不要写“今天是/日期为”。\n"
        "必须包含以下小节（用 Markdown 二级标题 ##）：\n"
        "1) 风险等级（🟢🟡🟠🔴之一，给出一句话理由）\n"
        "2) VIX（写数值；若无法获得实时数值，写“需核对”并说明应核对的来源口径）\n"
        "3) 信用利差（是否扩大：HYG-LQD、或 HY OAS/IG OAS；若无实时数值写“需核对”）\n"
        "4) 资金流（风险资产/避险资产：流入/流出；若无实时数据写“需核对”）\n"
        "5) DXY 与 10Y（方向与含义；若无实时数值写“需核对”）\n"
        "6) 板块轮动（强/弱板块与含义）\n"
        "7) 风险触发条件（列出 3-5 条具体阈值/条件）\n"
        "8) 可执行策略建议（分：加仓/减仓/观望；强调不追高；给出 1-2 条对冲/防守思路）\n"
        "输出为 Markdown。"
    )

    structure_body = call_model(
        model="gpt-4.1",
        system_prompt=structure_system,
        user_prompt=structure_user,
        max_output_tokens=1200,
    )

    if not structure_body:
        structure_body = "⚠️ 结构监控生成失败（空输出），请检查 API 或重跑 workflow。"

    # ✅ 标题永远正确（由代码控制）
    structure_md = f"# 结构监控（{date}）\n\n{structure_body}\n"
    (day_dir / "structure.md").write_text(structure_md, encoding="utf-8")

    # =========================================================
    # 2) 每日扩展阅读 Strict ——稳定版（不联网，允许“需核对”）
    # =========================================================
    extended_system = (
        "你是一个结构化阅读简报编辑，用中文输出 Markdown，标题清晰。"
        "严格遵循用户固定标准：按板块输出，Top10，每条 3–5 句话，每板块必须有综合评论。"
        "若无法获得实时新闻/数据，必须明确写“需核对”并给出应核对的指标/来源口径。"
        "严禁输出“占位符/模板/框架版”。"
    )

    extended_user = (
        f"请生成 {date} 的《每日扩展阅读 Strict》。\n"
        "必须包含五大板块：投资、健康（抗衰老）、心理/哲学、AI/科技、美学。\n\n"
        "通用要求：\n"
        "- 每个板块列出 Top 10（编号 1-10）\n"
        "- 每条 3–5 句话，结构为：主题/切入点 → 关键洞见 → 证据/数据（无法实时获取写“需核对”并给出应核对的口径） → 对行动的启示\n"
        "- 每个板块最后必须写【板块综合评论】（不少于 5 句话）\n\n"
        "投资板块额外要求：\n"
        "A) 【X/Twitter Top 10 观点】（编号 1-10，每条 3–5 句；若无实时抓取，用“市场分歧点/监控指标/触发条件”组织，并标注“需核对”）\n"
        "B) 【税务规划】给 5 条可执行要点（美股/ETF/期权/海外资产申报相关）\n"
        "C) 【Estate planning】给 5 条可执行要点（信托/受益人/赠与/跨境等）\n\n"
        "最后写【全局总评】（不少于 8 句话：总结五板块共同结构、风险等级、未来 1–4 周关注点）。\n"
        "输出 Markdown，标题层级清晰（#、##、###）。"
    )

    extended_md = call_model(
        model="gpt-4.1",
        system_prompt=extended_system,
        user_prompt=extended_user,
        max_output_tokens=2600,
    )

    if not extended_md:
        extended_md = f"# 每日扩展阅读 Strict（{date}）\n\n⚠️ 生成失败（空输出），请重跑 workflow。\n"

    (day_dir / "extended.md").write_text(extended_md, encoding="utf-8")

    # =========================================================
    # 3) 更新 manifest（用结构监控里的 emoji 给首页着色）
    # =========================================================
    risk = "🟡"
    for r in ["🟢", "🟡", "🟠", "🔴"]:
        if r in structure_md:
            risk = r
            break

    ensure_manifest(date, f"Structure {risk} | Auto-generated")


if __name__ == "__main__":
    main()
