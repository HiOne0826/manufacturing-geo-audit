# 数据结构设计

## question_bank

问题库表。

| 字段 | 说明 |
| --- | --- |
| question_id | 问题唯一编号 |
| industry | 行业 |
| product_category | 产品品类 |
| question_type | 问题类型 |
| question | 问题正文 |
| target_brand | 目标品牌 |
| competitor_brands | 竞品品牌，多个用 `;` 分隔 |
| locale | 地区或语言 |
| priority | 优先级 |
| notes | 备注 |

## model_runs

模型运行记录。

| 字段 | 说明 |
| --- | --- |
| run_id | 单次运行唯一编号 |
| batch_id | 批次编号 |
| question_id | 对应问题编号 |
| provider | 模型服务商 |
| model | 模型名称 |
| model_version | 模型版本 |
| search_enabled | 是否启用搜索 |
| temperature | temperature 参数 |
| requested_at | 请求时间 |
| response_text | 原始回答 |
| citations_json | 引用来源 |
| latency_ms | 响应耗时 |
| cost_estimate | 预估成本 |
| status | success / failed |
| error_message | 错误信息 |

## answer_evaluations

回答评估表。

| 字段 | 说明 |
| --- | --- |
| evaluation_id | 评估编号 |
| run_id | 对应运行编号 |
| target_brand_mentioned | 目标品牌是否出现 |
| target_brand_rank | 目标品牌排名 |
| recommendation_strength | 推荐强度 |
| sentiment | 情绪倾向 |
| competitors_mentioned | 出现的竞品 |
| owned_site_cited | 是否引用客户自有网站 |
| third_party_cited | 是否引用第三方来源 |
| factual_errors | 事实错误 |
| risk_level | 风险等级 |
| evaluator | human / llm / rule |
| evaluation_notes | 评估备注 |

## report_metrics

报告指标建议从上面三张表聚合。

- 品牌总命中率。
- 分模型命中率。
- 分问题类型命中率。
- 平均推荐排名。
- 自有站点引用率。
- 竞品共现次数。
- 高风险错误数量。
- 最需要补内容资产的问题类型。
