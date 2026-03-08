import os
import json
from datetime import date

today=str(date.today())

base=f"daily/{today}"

os.makedirs(base,exist_ok=True)

market=f"""
# 今日市场简报

日期：{today}

## 市场概览

- 美股指数波动
- 黄金走势
- 能源价格变化
"""

reading=f"""
# 每日扩展阅读

日期：{today}

## 投资

今日投资相关新闻与分析。

## 健康

抗衰老研究与健康新闻。

## AI

人工智能领域发展。

"""

structure=f"""
# 结构监控

日期：{today}

## 风险指标

VIX
信用利差
美债收益率

"""

open(f"{base}/market_brief.md","w").write(market)
open(f"{base}/extended_reading.md","w").write(reading)
open(f"{base}/structure.md","w").write(structure)

manifest={

"latest":today

}

os.makedirs("daily",exist_ok=True)

open("daily/manifest.json","w").write(json.dumps(manifest,indent=2))