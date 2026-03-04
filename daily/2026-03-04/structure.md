# 结构监控（2026-03-04）

## 1) 风险等级与评分
- 风险等级：🟡（数据降级）
- 风险评分：NA（核心数据缺失）
- 数据质量：degraded

## 2) 核心指标快照（自动抽取）
- VIX：NA；1D NA / 3D NA / 5D NA
- 信用（HYG/LQD 比例 3D）：NA（<0 通常视为信用走弱）
- SPY 3D：NA；QQQ 3D：NA；GLD 3D：NA
- UUP 3D（可选）：NA；TNX 3D（可选）：NA

## 3) 结构解读（原因链）
- 缺数据：VIX last（抓取失败/源缺失）
- 缺数据：VIX chg_3d_pct
- 缺数据：credit_ratio_hyg_lqd chg_3d_pct
- 缺数据：SPY chg_3d_pct
- 缺数据：QQQ chg_3d_pct
- 缺数据：GLD chg_3d_pct
- 缺数据：UUP chg_3d_pct（美元代理，可选）
- 缺数据：TNX chg_3d_pct（10Y，可选）
- ⚠️ 核心数据缺失：今日评分/策略建议降级为“定性提示”。先修复数据源。

## 4) 风险触发条件（如果继续恶化/转好）
- 修复数据源后再启用评分：必须拿到 VIX(last,3D)、cr_3D、SPY_3D、QQQ_3D、GLD_3D

## 5) 可执行策略建议（不追高 / 加减仓倾向）
- 先不要根据缺数据的报告做大幅操作；优先修复抓取与字段映射
