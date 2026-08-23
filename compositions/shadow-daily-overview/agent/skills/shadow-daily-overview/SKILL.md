---
name: shadow-daily-overview
description: 组合用户已授权的 Health 与 Ledger 摘要
---

# Shadow Daily Overview

只在用户要求综合查看当日健康状态与当月财务概况时使用。

先确认已有 Health Profile 授权，再调用 `shadow.daily-overview.read`。结果中的每个领域仍是独立
事实源；不要推断医疗结论、投资建议或未返回的个人信息。某个可选领域不可用时，明确说明缺失，
不要补造数据。
