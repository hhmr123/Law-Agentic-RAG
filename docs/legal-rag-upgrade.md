# 法律 RAG 升级说明

这份文档记录的是：为什么这次不再满足于“能问能答”，而是要把项目改造成一个更像真实法律 Agentic RAG 系统的版本。

## 升级目标

这次升级不是为了堆功能，而是为了正面解决三类高频 bad case：

1. 搜不准、搜不到
2. 命中了，但上下文断裂
3. 后端做了很多事，前端却像死机

## 第一梯队：解决“搜不准、搜不到”

### 1. 主检索改成 RAGFlow hybrid retrieval

#### 更准确的说法

现在主链路不是“本地 Dense + BM25 + RRF”。

现在主链路是：

- `RAGFlow retrieval API`
- 底层由 `RAGFlow` 的 doc engine 承担混合检索
- 检索逻辑更准确的表述是：
  - keyword / full-text similarity
  - + vector similarity
  - + weighted fusion

#### 为什么要改

法律场景下，像“定金”“抵押权”“孳息”“善意取得”这种词，字面命中非常重要。只靠 dense retrieval 容易召回一堆“语义接近但法条不对”的内容。

而如果把 hybrid retrieval 放在本地 Python 层现算：

- 性能差
- 容易扫全量 chunk
- 不能复用 RAGFlow 自带的全文索引和向量索引能力

所以主检索被下沉到 `RAGFlow`，而不是继续停留在本地原型实现。

### 2. 本地 Dense + BM25 + RRF 仍然保留，但现在是 fallback

#### 为什么保留

因为远程系统仍然可能遇到：

- RAGFlow 接口超时
- 文档还在解析中
- doc engine 短时不可用

这时如果一点 fallback 都没有，系统会直接瘫掉。

#### 现在的准确定位

- 主链路：`RAGFlow weighted hybrid retrieval`
- 本地 fallback：`Dense + lexical + RRF`

也就是说，面试时不要再说“项目主链路是 Dense + BM25 + RRF”，更准确的说法应该是：

> 主链路走 RAGFlow 的 hybrid retrieval，本地 Dense + BM25 + RRF 只保留为降级兜底。

### 3. Query Rewrite：Step-Back + HyDE

#### 更贴切的 bad case

用户问：

> 我买的二手车被调表了，我能要求退一赔三吗？

这类输入通常不是法条原话，而是“平民表达”。如果直接原句去搜，容易搜不到。

#### 为什么要加

这里的失败点不在 retrieval engine，而在 query 本身不适合法律知识库。

所以系统会在必要时做两层改写：

- Step-Back：把问题抽象成更标准的法律关系
- HyDE：生成一段更像法条语言的假设答案，再去辅助语义检索

### 4. Grade Documents

#### 更贴切的 bad case

用户问：

> 今天北京天气怎么样？

如果系统只要听到问题就硬搜法律库，可能会搜出“不可抗力”“恶劣天气”之类的片段，然后一本正经地胡说。

#### 为什么要加

检索命中了，不代表命中的内容真的适合作为答案依据。

所以在 retrieval 和 generation 之间增加一层相关性门控，让模型先判断：

- 这些 chunk 到底相关不相关
- 如果不相关，是去重写 query，还是直接拒绝把这些 chunk 当依据

## 第二梯队：解决“上下文断裂”

### 5. 三级分块 + Auto-merging

#### 更贴切的 bad case

用户问：

> 法条里写“前款规定的除外”，这里的前款到底指什么？

如果系统只命中一个很小的 leaf chunk，比如：

> 前款规定的除外。

那模型几乎没有办法独立理解它。

#### 为什么要加

法律文本天然是层级结构：

- 编
- 章
- 节
- 条
- 款

所以系统要同时满足两个目标：

- 检索时粒度小，才能命中准
- 生成时上下文大，才能解释清

当前方案是：

- 细粒度 leaf chunk 负责命中
- 上层块负责补语境
- 如果命中块出现“前款”“本条”等强指代，就自动向上合并

## 第三梯队：解决工程体验

### 6. 实时执行轨迹

#### 更贴切的 bad case

加了重写、混合检索、门控、合并之后，一次问答可能就要好几秒。

如果前端什么都不显示，用户会以为系统挂了。

#### 为什么要加

用户最怕的不是慢，而是“黑盒慢”。

所以系统会把关键阶段推到前端：

- `route`
- `hybrid_search`
- `grade_documents`
- `query_rewrite`
- `auto_merge`
- `synthesize_answer`

### 7. Session Summary

#### 更贴切的 bad case

用户围绕一场离婚财产纠纷连续问 20 轮。

如果每一轮都把所有历史消息原样塞进 prompt：

- token 成本会暴涨
- 真正重要的信息反而被淹没
- 模型会越来越不稳定

#### 为什么要加

长对话的关键不是“全记住”，而是“把记忆组织好”。

所以当历史超过阈值后：

- 系统会压缩较早历史
- 保留摘要
- 最近几轮继续原样保留

## 这次升级后的准确定位

这版项目现在更适合这样介绍：

> 一个面向法律文档场景的 Agentic RAG 原型。主检索下沉到 RAGFlow，Agent 层负责 rewrite、gate、fallback 和 answer orchestration，并通过 trace 把整个检索链路显式暴露出来。

## 面试时要避免的说法

不要说：

- “我们主链路就是 Dense + BM25 + RRF”
- “RAGFlow 就是一个向量数据库”
- “我看别人的项目这么做，所以我也这么做”

更准确的说法应该是：

- “主检索走 RAGFlow 的 hybrid retrieval，本地 Dense + BM25 + RRF 只做 fallback”
- “RAGFlow 不只是向量存储，它承担了解析、切块、索引、检索和引用返回”
- “我是从 bad case 反推架构，而不是从框架流行度反推架构”

## 当前默认知识库策略

为了避免继续把法律文档上传到旧的 `general` dataset，当前项目已经把默认目标 dataset 调整为：

- `RAGFLOW_DATASET_NAME=MY_agent_laws`
- `RAGFLOW_DATASET_CHUNK_METHOD=laws`

也就是说，后续重新上传时，系统会优先走新的法律专用 dataset。

## 后续架构演进：检索编排已迁移到 LangGraph

这一版文档最初记录的还是“手写 orchestration”阶段。
现在项目已经继续演进了一步：

- 主检索编排正式迁移到了 `LangGraph`
- `RAGFlow` 负责 retrieval
- `LangGraph` 负责状态流转
- `LangSmith` 负责图级别追踪

当前检索图的主节点包括：

1. `primary_retrieve`
2. `grade_primary`
3. `rewrite_queries`
4. `expanded_retrieve`
5. `finalize_remote`
6. `local_fallback`

这样做的意义不是“多用了一个框架”，而是让下面这些问题都能被清楚表达：

- 是主检索没命中
- 还是主检索不够相关
- 是不是触发了 rewrite
- rewrite 之后有没有形成稳定命中
- 最终是否退回本地 fallback

更完整的迁移说明见：

- [LangGraph迁移说明](/c:/Users/hmr/Desktop/Int/agent_RAG/MY_agent/docs/LangGraph%E8%BF%81%E7%A7%BB%E8%AF%B4%E6%98%8E.md)
