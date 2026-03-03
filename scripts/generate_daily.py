import os, json
from datetime import datetime, timezone
from pathlib import Path

# 使用官方 OpenAI Python SDK（建议）
# pip install openai
from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = REPO_ROOT / "daily"
MANIFEST = REPO_ROOT / "manifest.json"

def today_ymd_utc():
    # 你希望按“美国东部 8:00 AM”生成：我们会在 GitHub Actions 里用 cron 控制触发时间
    # 这里用触发当天的 UTC 日期作为文件夹日期即可
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

def gen_text(system_prompt: str, user_prompt: str) -> str:
    resp = client.responses.create(
        model="gpt-5",
        max_output_tokens=3500,   # 控制成本
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return resp.output_text

def main():
    date = today_ymd_utc()
    day_dir = DAILY_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)

    # 1) 结构监控（含策略建议）
    structure_system = "你是一个严谨的宏观与风险结构监控分析师，用中文输出，结构清晰，避免空话。"
    structure_user = (
        "请生成今日《结构监控（含策略建议）》：\n"
        "必须包含：风险等级（🟢🟡🟠🔴之一）+ VIX + 信用利差是否扩大 + 资金持续流入/流出 + "
        "DXY与10Y + 板块轮动 + 风险触发条件 + 可执行策略建议（不追高、加仓/减仓倾向）。\n"
        "输出为 Markdown。"
    )

    structure_md = gen_text(structure_system, structure_user)
    (day_dir / "structure.md").write_text(structure_md, encoding="utf-8")

    # 2) 每日扩展阅读（先给模板版；等你想“自动抓新闻源”再升级）
    # 2) 每日扩展阅读（完整 Strict 版：不联网，先生成可读的当日完整稿）
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

    extended_md = gen_text(extended_system, extended_user)
    (day_dir / "extended.md").write_text(extended_md, encoding="utf-8")

    # manifest：标题里放🟢🟡🟠🔴，首页会自动着色
    # 先从 structure_md 里简单找风险等级（你也可以写更严格解析）
    risk = "🟡"
    for r in ["🟢","🟡","🟠","🔴"]:
        if r in structure_md:
            risk = r
            break
    ensure_manifest(date, f"Structure {risk} | Auto-generated")

if __name__ == "__main__":
    main()
