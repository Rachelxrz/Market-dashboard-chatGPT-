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
    # 用 Responses API / SDK 生成内容（SDK 会随官方更新）
    resp = client.responses.create(
        model="gpt-5",  # 你也可以换成你账号可用的其它模型
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    # SDK 会给出聚合后的文本
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
    extended_system = "你是一个结构化阅读简报编辑，用中文输出 Markdown，标题清晰。"
    extended_user = (
        "请生成《每日扩展阅读 Strict》框架版（先不抓取外部新闻，给可填充模板也可以）：\n"
        "板块：投资、健康（抗衰老）、心理/哲学、AI/科技、美学。\n"
        "每个板块：Top10占位符（每条3-5句话的占位结构）+ 板块综合评论。\n"
        "最后给总评。\n"
        "输出为 Markdown。"
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
