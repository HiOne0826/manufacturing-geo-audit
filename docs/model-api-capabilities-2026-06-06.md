# 主流模型官方 API 可配置项对照（2026-06-06）

本文按 `2026-06-06` 当天可查到的官方文档整理，重点回答制造业 GEO 测试里最重要的几个问题：

- 是否支持联网搜索
- 是否支持深度思考 / 推理强度控制
- 除此之外还能配置哪些关键项
- 哪些能力是“产品里有”，但“公开 API 不一定同样开放”

## 先看结论

| 平台 / 产品名 | 开发者 API 对应 | 联网搜索 | 深度思考 | GEO 价值判断 |
| --- | --- | --- | --- | --- |
| ChatGPT | OpenAI API | 支持，内置 `web_search` 工具 | 支持，`reasoning.effort` | 很适合做“纯模型 + 搜索增强”双轨测试 |
| Gemini | Gemini API | 支持，`google_search` 工具 | 支持，`thinkingBudget` / `thinkingLevel` | 适合测“实时搜索 + 引文链路” |
| 豆包 | 火山方舟 ARK API | 支持，`tools: [{type: "web_search"}]` | 支持，`thinking.type` | 适合测中文搜索增强回答与引用 |
| DeepSeek | DeepSeek API | 标准公开 API 文档里未见通用内置联网搜索参数；官方明确支持在 Claude Code 集成里走 Web Search | 支持，`thinking.type` + `reasoning_effort` | 适合做纯模型 / 推理能力对照；搜索增强要谨慎区分“官方 API”与“集成能力” |
| 通义千问 | 阿里云百炼 / DashScope | 支持，`enable_search` 或 Responses API `tools: [{type: "web_search"}]` | 支持，`reasoning.effort`、`enable_thinking`、`thinking_budget` | 非常适合做中文 GEO 测试，搜索与思考控制都比较全 |
| 腾讯元宝 | 开发侧应拆成“腾讯混元 / TokenHub” + “联网搜索 API” | 支持，但更多表现为混元搜索增强参数或独立联网搜索 API | 支持，混元 / TokenHub 文档明确有深度思考模型 | 适合做中文场景，但要明确“元宝产品能力”与“开发 API 接入方式”不是一回事 |
| Kimi | Moonshot / Kimi API | 支持，官方工具 `Web Search` | 支持，`thinking.type` 或使用 thinking 模型 | 很适合测中文长推理和工具型回答 |
| 文心一言 | 百度千帆 / ERNIE API | 支持，ERNIE 内置 `web_search`；开源模型建议走单独搜索接口 | 支持，`enable_thinking`、`thinking_budget`、`reasoning_effort` | 适合做中文搜索增强与带引用回答测试 |

## 使用这份表时要先分清两件事

### 1. 产品名不等于 API 名

- `ChatGPT` 对应开发者侧主要是 `OpenAI API`
- `文心一言` 对应开发者侧主要是 `百度千帆 / ERNIE API`
- `腾讯元宝` 对应开发者侧不能简单理解成“一个独立的元宝聊天 API”，更接近：
  - 文生接口：`腾讯混元 / TokenHub`
  - 搜索能力：`联网搜索 API`

### 2. “支持联网”也分两类

- `模型内置联网`：一次对话请求里，模型自己决定要不要查网
- `独立搜索 API`：先搜，再把结果交给模型总结

做 GEO 测试时，两类都很有价值，但不能混为一谈。

## 逐家说明

### 1. ChatGPT / OpenAI API

#### 是否支持联网搜索

支持。

- Responses API 可直接启用 `web_search`
- 官方文档明确说明：可通过 Responses API 的 `web search tool` 让模型先访问最新网页信息，再生成答案
- 返回结果还能带来源信息，适合做 GEO 引用分析
- 当前系统 GPT 预设按 Responses API hosted tool 配置：
  - `tools: [{ "type": "web_search" }]`
  - 可选 `tools[].search_context_size`
  - 可选 `tools[].user_location`
  - 引用优先从 `output[].content[].annotations[type=url_citation]` 提取，必要时通过 `include[]=web_search_call.action.sources` 补充搜索来源

#### 是否支持深度思考

支持。

- Responses API 支持 `reasoning` 对象
- `reasoning.effort` 支持的官方档位包括：
  - `none`
  - `minimal`
  - `low`
  - `medium`
  - `high`
  - `xhigh`

#### 其他常见可配项

- `model`
- `input`
- `instructions`
- `max_output_tokens`
- `tools`
- `include`
- `tools[].search_context_size`
- `tools[].user_location`
- `previous_response_id`
- `conversation`
- `stream`
- `metadata`
- `prompt_cache_key`
- `prompt_cache_retention`
- `store`
- 结构化输出 / JSON schema
- 文件搜索、计算机操作、代码执行等其他工具

#### GEO 相关最值得记录的字段

- 是否开启 `web_search`
- 返回了哪些 `sources`
- 最终是否带引用
- 模型是否在搜索后改变答案
- 使用的是推理模型还是非推理模型
- `reasoning.effort` 档位

#### 备注

OpenAI 是这几家里“搜索增强 + 推理强度 + 工具调用”组合最成熟的一类，适合做标准基准组。

### 2. Gemini / Gemini API

#### 是否支持联网搜索

支持。

- 官方叫 `Grounding with Google Search`
- 通过 `tools: [{ google_search: {} }]` 启用
- 返回结果包含 `groundingMetadata`
- 可拿到：
  - 搜索词
  - 网页来源
  - 引文映射

#### 是否支持深度思考

支持。

- Gemini 2.5 系列主用 `thinkingBudget`
- Gemini 3 系列主用 `thinkingLevel`
- `thinkingBudget=0` 可关闭部分模型的思考
- `thinkingBudget=-1` 代表动态思考

#### 其他常见可配项

- `model`
- `contents`
- `systemInstruction`
- `tools`
- `generationConfig`
- `responseMimeType`
- `responseSchema`
- `maxOutputTokens`
- `temperature`
- `topP`
- `topK`
- 安全设置 `safetySettings`
- 多模态输入

#### GEO 相关最值得记录的字段

- 是否启用 `google_search`
- `groundingMetadata.webSearchQueries`
- `groundingChunks`
- `groundingSupports`
- 是否返回可做行内引用的来源映射
- `thinkingBudget` 或 `thinkingLevel`

#### 备注

Gemini 的优势是搜索来源、引用链和结构化溯源做得很完整，适合测“品牌是否被搜索出来并被引用”。

### 3. 豆包 / 火山方舟 ARK API

#### 是否支持联网搜索

支持。

- Responses API 可通过 `tools: [{ "type": "web_search" }]` 启用
- 还可配：
  - `max_keyword`
  - `sources`
  - `user_location`
- 支持流式查看模型是否触发搜索

#### 是否支持深度思考

支持。

- 通过 `thinking.type` 控制
- 官方文档明确给出：
  - `enabled`
  - `disabled`
  - `auto`
- 部分文档同时说明 `reasoning.effort` 仅作用于原始思考内容

#### 其他常见可配项

- `model`
- `input`
- `tools`
- `stream`
- `max_output_tokens`
- `service_tier`
- `store`
- `caching`
- 结构化输出
- 多模态输入

#### GEO 相关最值得记录的字段

- 是否启用 `web_search`
- 搜索来源 `sources`
- 用户地理位置 `user_location`
- 是否返回引用网址注释
- 思考模式 `thinking.type`

#### 备注

豆包在中文互联网检索和内容整合场景下很值得单列观察，尤其适合 GEO 的中文结果对照。

### 4. DeepSeek / DeepSeek API

#### 是否支持联网搜索

需要分开说。

- 在我本次查到的标准公开 API 文档里，没有看到像 OpenAI / Gemini / 豆包 那样清晰公开的“通用内置 web_search 请求参数”
- 但 DeepSeek 官方文档明确写了：在 `Claude Code` 集成里，DeepSeek API 原生支持 `Web Search`

因此更稳妥的表述是：

- `标准公开对话 API`：未查到明确公开的通用联网搜索开关
- `官方集成场景`：明确支持 Web Search
- `本项目联网口径`：统一使用博查 Web Search API 作为外部公开网页检索源，再把检索结果作为上下文交给 DeepSeek 生成回答；这应标注为 `DeepSeek + 博查外部检索增强`，不是 DeepSeek 官网联网搜索。

#### 是否支持深度思考

支持。

- 通过 `thinking.type` 开启思考
- 通过 `reasoning_effort` 调推理强度
- 官方示例给出：
  - `extra_body={"thinking": {"type": "enabled"}}`
  - `reasoning_effort="high"`

#### 其他常见可配项

- `model`
- `messages`
- `max_tokens`
- `stream`
- `tools`
- `tool_choice`
- `reasoning_effort`
- `thinking.type`

#### GEO 相关最值得记录的字段

- 当前测试到底是：
  - 纯模型问答
  - 还是借助某个集成环境触发 Web Search
  - 统一使用博查 Web Search API 外部检索增强
- 是否开启 `thinking`
- `reasoning_effort`
- 是否有工具调用

#### 备注

如果你要做“严格 API 可复现”的 GEO 基准，DeepSeek 这家要特别标注测试模式，避免把集成层搜索能力误当成标准 API 内置能力。

本系统当前实现选择第三种口径：`DeepSeek + 博查 Web Search API`。采样时博查返回的网页结果会写入 `citations_json`，DeepSeek 的回答 prompt 会明确标注外部资料来源，并禁止自称 DeepSeek 自己联网。

### 5. 通义千问 / 阿里云百炼

#### 是否支持联网搜索

支持，而且方式比较多。

- OpenAI 兼容 Chat Completions 可直接用 `enable_search: true`
- Responses API 可用 `tools: [{ "type": "web_search" }]`
- 官方还给了联网检索 Agent、深度搜索等上层能力

#### 是否支持深度思考

支持。

常见方式包括：

- `reasoning.effort`
- `enable_thinking`
- `thinking_budget`

官方还说明：

- `reasoning.effort` 优先级高于 `enable_thinking`
- 后续更建议优先使用 `reasoning.effort`

#### 其他常见可配项

- `model`
- `messages`
- `tools`
- `tool_choice`
- `temperature`
- `top_p`
- `max_tokens`
- `stream`
- `response_format`
- `enable_search`
- `enable_thinking`
- `thinking_budget`
- `preserve_thinking`
- 文件搜索 `file_search`
- 网页提取 `web_extractor`
- 代码解释器 `code_interpreter`

#### GEO 相关最值得记录的字段

- 是否启用 `enable_search`
- 是否启用 `web_search`
- 是否叠加 `web_extractor`
- 是否保留思考历史 `preserve_thinking`
- `reasoning.effort`
- `thinking_budget`

#### 备注

通义千问是目前最适合做中文 GEO 测试的厂商之一，因为：

- 搜索能力开放得比较直接
- 推理能力控制比较细
- 工具调用生态完整

### 6. 腾讯元宝 / 腾讯混元 / TokenHub

#### 是否支持联网搜索

支持，但开发者侧建议拆成两个能力理解：

#### A. 混元聊天接口里的搜索增强

腾讯混元 OpenAI 兼容接口支持这些与搜索增强相关的参数：

- `enable_enhancement`：功能增强（如搜索）开关
- `force_search_enhancement`：强制搜索增强
- `search_info`：返回搜索信息
- `citation`：回答中的搜索引文角标

#### B. 独立的联网搜索 API

腾讯云还提供独立的 `联网搜索API`，可直接返回：

- 标题
- 摘要
- 内容来源 url

并支持：

- `Query`
- `Site`
- `FromTime`
- `ToTime`
- `Cnt`
- `Industry`

#### 是否支持深度思考

支持。

- 腾讯混元 / TokenHub 官方文档明确列出“深度思考”
- TokenHub 混元调用指南中已有“深度思考”章节

#### 其他常见可配项

- `model`
- `messages`
- `stream`
- `max_tokens`
- `seed`
- `stop`
- `temperature`
- `top_p`
- `tools`
- `tool_choice`
- `citation`
- `enable_enhancement`
- `enable_multimedia`
- `enable_recommended_questions`
- `force_search_enhancement`
- `search_info`

#### GEO 相关最值得记录的字段

- 是否启用 `enable_enhancement`
- 是否强制搜索 `force_search_enhancement`
- 是否返回 `citation`
- 是否返回 `search_info`
- 独立搜索 API 的时间过滤和站点过滤是否启用

#### 备注

这里最容易误判。

- `腾讯元宝` 是产品名
- 真正落地到 API 测试时，更像是：
  - 用 `腾讯混元 / TokenHub` 做模型回答
  - 用 `联网搜索 API` 做实时搜索补强

如果报告里直接写“元宝 API 支持 xxx”，最好补一行说明它实际对应的开发者接口是哪一层。

### 7. Kimi / Moonshot API

#### 是否支持联网搜索

支持。

- 官方平台首页明确列出官方工具 `Web Search`
- Kimi K2.5 文档也明确写了联网搜索是官方提供工具之一

#### 是否支持深度思考

支持。

两种常见方式：

- 使用 thinking 模型，如 `kimi-k2-thinking`
- 对 `kimi-k2.5` 用 `thinking.type` 控制：
  - `enabled`
  - `disabled`

并且 K2.5 文档明确给出：

- 默认 `thinking={"type":"enabled"}`
- 可通过 `thinking={"type":"disabled"}` 关闭思考

#### 其他常见可配项

- `model`
- `messages`
- `max_tokens`
- `thinking`
- `tools`
- `tool_choice`
- `reasoning_content`
- 结构化输出

#### GEO 相关最值得记录的字段

- 是否使用 `Web Search`
- 是否关闭思考模式再配搜索
- 是否保留 `reasoning_content`
- `tool_choice` 是否受限

#### 备注

K2.5 官方文档明确提醒：

- `Kimi K2.5` 的思考模式与官方内置 `$web_search` 暂时不兼容
- 如需使用联网搜索，可先关闭思考模式

这对 GEO 测试很关键，因为它直接影响“搜索增强测试”和“深度推理测试”能否在同一轮请求中同时成立。

### 8. 文心一言 / 百度千帆 / ERNIE API

#### 是否支持联网搜索

支持。

- ERNIE 系列内置 `web_search`
- 在请求体里添加 `web_search` 对象即可启用
- 可配置：
  - `enable`
  - `enable_trace`
  - `enable_status`
  - `enable_citation`
  - `search_mode`
  - `search_number`
  - `reference_number`
  - `user_ip`

官方也明确说明：

- 对 `开源模型`，更建议走单独的搜索接口

#### 是否支持深度思考

支持。

官方文档明确列出：

- `enable_thinking`
- `thinking_strategy`
- `thinking_budget`
- `reasoning_effort`

并说明不同模型支持不同的控制方式。

#### 其他常见可配项

- `model`
- `messages`
- `temperature`
- `top_p`
- `max_tokens`
- `stream`
- `tools`
- `tool_choice`
- `response_format`
- `web_search`
- `enable_thinking`
- `thinking_strategy`
- `thinking_budget`
- `reasoning_effort`

#### GEO 相关最值得记录的字段

- `web_search.enable`
- `enable_trace`
- `enable_citation`
- `search_mode`
- `search_number`
- `reference_number`
- `reasoning_effort`
- `thinking_budget`

#### 备注

百度这套对 GEO 很友好，因为联网参数本身就比较面向“搜索结果质量与可溯源性”。

## 可以统一抽象成哪些测试字段

如果后面你要把这 8 家都接入这个项目，建议统一抽成以下字段：

- `provider_name`
- `api_family`
- `model`
- `supports_web_search`
- `web_search_mode`
- `web_search_param_path`
- `supports_reasoning`
- `reasoning_param_path`
- `reasoning_levels`
- `supports_citation`
- `citation_param_path`
- `supports_site_filter`
- `supports_time_filter`
- `supports_user_location`
- `supports_tool_calling`
- `supports_structured_output`
- `supports_multimodal_input`
- `notes`

## GEO 视角下最重要的 8 个配置项

不是所有参数都值得优先测。对 GEO 来说，最该优先记录的是：

1. 是否启用联网搜索
2. 是否返回引用 / 引文 / 来源链接
3. 是否支持站点过滤
4. 是否支持时间过滤
5. 是否支持用户地理位置
6. 是否开启深度思考
7. 推理强度档位
8. 是否允许工具调用与多步搜索

## 当前最实用的接入建议

如果你的目标是“尽快做出一版制造业 GEO 审计底稿”，我建议先分两层：

### 第一层：纯模型基准组

- OpenAI
- DeepSeek
- Kimi
- 通义千问

用途：

- 测品牌是否存在于模型内生记忆
- 测品牌推荐倾向和竞品共现

### 第二层：搜索增强组

- OpenAI `web_search`
- Gemini `google_search`
- OpenRouter-GPT / OpenRouter-Gemini `web plugin`
- 豆包 `web_search`
- DeepSeek + 博查 Web Search API 外部检索增强
- 通义千问 `enable_search / web_search`
- 百度 `web_search`
- 腾讯混元搜索增强 / 腾讯联网搜索 API

用途：

- 测品牌是否能被实时检索
- 测品牌是否会被引用
- 测官网 / 媒体 / 行业站是否进入来源池

## 这份文档里几个需要你注意的边界

- `腾讯元宝`：我没有查到一个和 ChatGPT / Gemini 同层级、公开命名为“元宝聊天 API”的独立官方文档；这里是按腾讯官方现有开发者体系，拆成 `混元 / TokenHub` 与 `联网搜索 API` 来写的。这是基于官方资料结构做出的工程判断。
- `DeepSeek`：本项目为了客户侧六平台联网口径，统一采用 `DeepSeek + 博查 Web Search API 外部检索增强`，必须和 DeepSeek 官网联网搜索区分。
- `OpenRouter-GPT / OpenRouter-Gemini`：属于 OpenRouter 中转联网口径。它使用 OpenRouter `web` plugin / `:online` 能力，可能优先走 provider native search，也可能按 OpenRouter 策略回退到外部搜索引擎；报告里不要写成 OpenAI / Google 官方直连。
- `Kimi`：K2.5 的“思考模式”和官方内置联网搜索存在兼容性约束，测试时不要默认它们能一起开。

## 官方文档入口

- OpenAI Responses API: <https://platform.openai.com/docs/api-reference/responses>
- OpenAI Web Search: <https://platform.openai.com/docs/guides/tools-web-search>
- Gemini Thinking: <https://ai.google.dev/gemini-api/docs/thinking>
- Gemini Google Search Grounding: <https://ai.google.dev/gemini-api/docs/google-search>
- 豆包深度思考: <https://www.volcengine.com/docs/82379/1956279>
- 豆包 Web Search: <https://www.volcengine.com/docs/82379/1756990>
- DeepSeek Thinking Mode: <https://api-docs.deepseek.com/guides/thinking_mode>
- DeepSeek Claude Code 集成: <https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code>
- 通义千问 API 参考: <https://help.aliyun.com/zh/model-studio/use-qwen-by-calling-api>
- 通义千问联网搜索: <https://help.aliyun.com/zh/model-studio/web-search/>
- 腾讯混元 OpenAI 兼容接口: <https://cloud.tencent.com/document/product/1729/111007>
- 腾讯联网搜索 API: <https://cloud.tencent.com/document/product/1806/121811>
- Kimi K2.5: <https://platform.moonshot.cn/docs/guide/kimi-k2-5-quickstart>
- Kimi 主要概念: <https://platform.moonshot.cn/docs/intro>
- 百度千帆联网搜索: <https://cloud.baidu.com/doc/qianfan-docs/s/Wm8r4sw29>
- 百度千帆深度思考: <https://cloud.baidu.com/doc/qianfan-docs/s/Wm95lyynv>

## 下一步建议

这份文档解决的是“官方能力盘点”。

如果继续推进这个项目，下一步最值钱的是把它落成一份真正可执行的 `provider capability matrix`，然后让采集脚本按能力自动分流：

- 支持内置联网的，走 `search_enhanced`
- 只适合纯模型问答的，走 `model_only`
- 搜索要独立接口的，走 `search_api + summarize_model`

这样后面出 GEO 审计报告时，结论才不会混淆。
