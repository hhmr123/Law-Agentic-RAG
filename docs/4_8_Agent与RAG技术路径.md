# 4_8_Agent与RAG技术路径

这份文档用于整理当前 `MY_agent` 的 Agent 与 RAG 技术路径，重点回答 4 个问题：

1. 现在项目到底用了哪些技术
2. Agent 的工作流是什么
3. RAG 的工作流是什么
4. 我们是怎么从早期版本演进到当前版本，并解决之前问题的

## 1. 当前技术栈

### 前后端

- 前端：`React + Vite`
- 后端：`FastAPI`
- 本地存储：`SQLite`
- 文件上传目录：`data/uploads`
- 会话管理：前端会话列表 + 后端 `chat_messages / session_summaries`

### 大模型与 Agent

- 模型接入：`Gemini native via langchain-google-genai`
- Agent / LLM 编排：`LangChain`
- 检索工作流编排：`LangGraph`
- 可观测性与评测：`LangSmith`

### RAG 与知识库

- 知识库平台：`RAGFlow`
- 主检索路径：`RAGFlow retrieval API`
- 底层 doc engine：当前由 `RAGFlow` 负责，项目内统一抽象为 `ragflow-doc-engine`
- 默认法律 dataset：
  - `RAGFLOW_DATASET_NAME=MY_agent_laws`
  - `RAGFLOW_DATASET_CHUNK_METHOD=laws`

### 本地兜底

- 本地 fallback 检索：`Dense + lexical + RRF`
- 作用：当 `RAGFlow` 不可用或远程结果不稳定时兜底

## 2. 当前 Agent 技术路径

当前项目不是“模型直接看问题然后自由发挥”，而是一个带编排的 Agentic RAG。

核心分工可以概括为：

- `RAGFlow`：负责文档解析、建库、主检索
- `LangGraph`：负责检索流程状态流转
- `LangChain / Gemini`：负责路由、改写、相关性判断、答案生成
- `LangSmith`：负责 trace 与评估

也就是说，现在系统更像：

> 一个由 LangGraph 编排的法律检索 Agent，  
> 其中 RAGFlow 是知识底座，Gemini 是推理与生成核心。

## 3. 当前 Agent 工作流

用户在前端输入问题后，当前主流程如下：

1. `receive_question`
   后端接收问题，写入会话历史。

2. `mode`
   判断当前模型链路使用哪种工作模式。
   现在默认优先走编排式检索模式，而不是把整条链路压在不稳定的原生 tool calling 上。

3. `route`
   先判断问题是否需要检索知识库。
   如果是法律条文、上传文档、需要依据支持的问题，进入检索链。
   如果只是闲聊或明显不需要知识库，则直接生成回答。

4. `retrieval graph`
   如果进入检索链，则进入 LangGraph 编排的检索图。

5. `synthesize_answer`
   将最终检索到的上下文交给模型生成答案，并返回前端。

6. `session management`
   前端可以：
   - 新建会话
   - 切换历史会话
   - 删除会话
   后端会按 `session_id` 读取历史消息，并在消息数达到阈值后使用摘要压缩较早轮次。

## 4. 当前 RAG 工作流

### 4.1 主链路：RAGFlow + LangGraph

当前检索编排已经迁移到 `LangGraph`，主节点包括：

1. `primary_retrieve`
   用原始问题调用 `RAGFlow retrieval API`

2. `grade_primary`
   对主检索结果做相关性门控

3. `rewrite_queries`
   如果主检索不够好，就生成多路重写查询：
   - 法律关键词重写
   - Step-Back
   - HyDE

4. `expanded_retrieve`
   用重写后的 query 再次调用 `RAGFlow`

5. `finalize_remote`
   对多路远程候选进行：
   - 合并
   - 去重
   - 重排

6. `local_fallback`
   如果远程结果仍然不稳定，再退回本地兜底检索

### 4.2 本地 fallback

本地 fallback 不是主链路，而是兜底：

- Dense embedding 相似度
- lexical 匹配
- RRF 融合

这个路径现在的定位不是“主系统”，而是“远程检索异常时，避免整个系统完全失效”。

## 5. 当前使用的关键技术

这版项目里真正已经落地、并且可以在面试里明确说出来的技术包括：

### 5.1 RAGFlow 远程主检索

现在主检索已经不是本地 Python 扫 chunk，而是：

- 调 `RAGFlow retrieval API`
- 让 `RAGFlow` 承担主检索
- 项目端只负责编排与后处理

这解决了早期本地扫全量 chunk 太慢的问题。

### 5.2 Query Rewrite

已经落地的重写方式有：

- 法律关键词重写
- Step-Back
- HyDE

目的不是“为了炫技多加几个 prompt”，而是解决：

> 用户问题是自然语言，法律知识库内容是法条语言，二者之间存在语义表达鸿沟。

### 5.3 Grade Documents

主检索命中后不会直接相信，而是先做相关性门控。

这样做是为了避免：

- 检索命中了“看起来沾边”的法条
- 但其实并不能直接支持用户问题
- 然后模型基于错误上下文一本正经胡说

### 5.4 Rerank / Dedupe

多路 query 拿回来的候选不会直接拼接给模型，而是会：

- 去重
- 重排
- 选出最终最值得送入生成的片段

### 5.5 LangGraph

这是当前版本很重要的升级点。

迁移前：

- 检索流程是手写的顺序调用
- LangSmith 里更像零散的多个 run

迁移后：

- 检索流程变成显式状态图
- LangSmith 更容易显示为一条完整 graph run
- 更容易解释“当前系统为什么从这一步走到下一步”

### 5.6 多会话管理 + Session Summary

当前这版不再只是“页面刷新就丢上下文”的单会话 Demo。

- 前端已经支持历史会话列表
- 后端按 `session_id` 存储原始消息
- 较早轮次会被总结到 `session_summaries`
- Prompt 中使用“摘要 + 最近几轮原始消息”的组合方式，避免对话越长越贵、越容易超窗

## 6. 当前 Agent 架构图

可以用这张抽象流程来理解：

```text
用户问题
  -> 路由判断（是否需要检索）
  -> LangGraph 检索图
     -> primary_retrieve
     -> grade_primary
     -> rewrite_queries
     -> expanded_retrieve
     -> finalize_remote
     -> local_fallback（必要时）
  -> 组装上下文
  -> Gemini 生成回答
  -> LangSmith 记录整条执行链
```

## 7. 我们是怎么演进到现在这版的

### 阶段 1：最初版本

早期更像一个最小可用 MVP：

- FastAPI + React 跑通前后端
- LangChain 调模型
- RAGFlow 负责文档上传与检索适配

这时候的主要目标是：

> 先把“能上传、能问、能返回答案”跑通。

### 阶段 2：引入法律场景增强

随着 bad case 增多，我们开始补这些能力：

- Query Rewrite
- Grade Documents
- 本地 fallback hybrid retrieval
- 文档删除与 RAGFlow 同步删除
- LangSmith tracing

这时候系统已经不只是 MVP，而是“面向 bad case 修补”的原型。

### 阶段 3：主检索从本地下沉到 RAGFlow

后来发现一个核心问题：

> 本地 Python 版 hybrid retrieval 在 query 时还要扫很多 chunk，性能和稳定性都不够。

所以主检索改成：

- 由 `RAGFlow retrieval API` 承担远程主检索
- 本地 Dense + lexical + RRF 只保留为 fallback

这是一次很关键的架构升级。

### 阶段 4：从手写 orchestration 迁移到 LangGraph

再往后，我们遇到的不是“有没有功能”，而是：

- trace 不够清楚
- 执行链不够显式
- 不容易解释 bad case 到底卡在哪一步

所以最终把检索编排迁到了 `LangGraph`。

这让系统从：

> 手写顺序调用的检索增强链

变成了：

> 一个显式的图执行检索工作流

## 8. 这次演进解决了哪些具体问题

### 问题 1：远程检索命中了，但最终答案还是不对

之前出现过这种情况：

- `RAGFlow` 其实已经能搜到正确法条
- 但后续多路候选合并与截断逻辑把正确片段丢掉了
- 最终生成时模型看到的是“沾边但不关键”的片段

现在的改进是：

- 检索结果先进入 LangGraph 编排
- 多路 query 结果统一合并
- 再做去重和重排
- 避免正确法条在中间环节被提前丢失

### 问题 2：Trace 看起来像多个散点，不像一个完整 Agent 工作流

之前：

- LangSmith 里更像多个分散的 chat / retrieval 调用

现在：

- 检索部分已经是 LangGraph 图执行
- 更适合展示成一条完整工作流

### 问题 3：本地检索太慢

之前主检索如果走本地 Python：

- 需要扫很多 chunk
- 性能差
- 远不如检索引擎做这件事自然

现在：

- 主检索已经下沉到 RAGFlow
- 本地检索只保留为 fallback

## 9. 当前版本的准确表述

现在如果面试官问“你的 Agent 和 RAG 技术路径是什么”，更推荐这样答：

> 我这个项目的底层知识库使用 RAGFlow，主检索通过 RAGFlow retrieval API 完成；  
> 在此之上，我用 LangGraph 编排了一个显式检索工作流，包括主检索、相关性门控、Query Rewrite、扩展检索、远程结果重排和本地 fallback；  
> 大模型用 Gemini 原生接入，负责路由判断、重写、门控和最终答案生成；  
> 全链路通过 LangSmith 做可观测性和调试。

## 10. 一句话总结

这版 `MY_agent` 的技术路径，不再是“一个能调模型的问答 Demo”，而是：

> 一个以 `RAGFlow` 为检索底座、以 `LangGraph` 为工作流骨架、以 `Gemini + LangChain` 为推理核心、以 `LangSmith` 为观测闭环的法律 Agentic RAG 原型。
