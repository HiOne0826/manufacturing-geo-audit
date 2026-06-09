# 采集与分析流程

## 流程总览

1. 准备客户品牌档案。
2. 准备问题库。
3. 选择测试模型。
4. 批量调用 API。
5. 保存原始回答。
6. 抽取结构化指标。
7. 汇总表格。
8. 生成 GEO 现状报告。

## 1. 客户品牌档案

每个客户至少需要：

- 品牌名称。
- 公司全称。
- 主营产品。
- 所属品类。
- 目标区域。
- 主要竞品。
- 官网域名。
- 重要产品页。
- 资质、认证、专利、案例。

## 2. 问题库设计

问题库不要只问品牌名，要覆盖真实采购链路。

建议字段：

- question_id
- industry
- product_category
- question_type
- question
- target_brand
- competitor_brands
- locale
- priority

问题类型：

- brand_direct：品牌直问。
- category_recommendation：品类推荐。
- procurement：采购决策。
- technical：技术参数。
- comparison：竞品对比。
- risk_after_sales：售后与风险。
- regional_supplier：区域供应商。

## 3. 模型调用

每个问题对每个模型独立调用。

关键控制项：

- 每次请求不携带历史对话。
- temperature 使用最低值。
- 记录模型版本。
- 记录是否启用联网搜索。
- 记录请求时间。
- 记录系统提示词。

## 4. 原始数据归档

不要只保存分析结果，必须保存原始回答。

原因：

- 方便后续复查。
- 方便客户看到证据。
- 方便模型版本变化后对比。
- 方便人工纠错和二次分析。

## 5. 指标抽取

建议初版指标：

- target_brand_mentioned：目标品牌是否出现。
- target_brand_rank：目标品牌在推荐列表中的位置。
- sentiment：正向、中性、负向。
- recommendation_strength：强推荐、一般提及、弱提及、未提及。
- competitors_mentioned：出现的竞品。
- citations：引用来源。
- factual_errors：事实错误。
- answer_summary：回答摘要。

## 6. 报告生成

客户报告建议包含：

- 总览结论。
- 模型表现对比。
- 品牌可见度。
- 竞品共现。
- 引用来源分析。
- 事实错误与风险。
- 内容资产缺口。
- 30 天优化建议。

## 初版技术路线

建议先用轻量结构：

- Python 采集脚本。
- CSV 问题库。
- SQLite 保存原始运行数据。
- Pandas 做汇总分析。
- Markdown / HTML 输出报告。

等流程跑通后，再考虑做前端看板。
