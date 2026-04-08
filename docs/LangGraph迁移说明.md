# LangGraph 迁移说明

这次迁移的目标，不是简单把现有函数“包一层图”，而是把检索编排真正改成可追踪、可解释的状态流。

## 为什么要迁移

迁移前，`MY_agent` 的检索链路虽然已经有：

- 主检索
- 相关性门控
- Query Rewrite
- 重排
- 本地 fallback

但这些步骤是手写顺序调用的，问题有两个：

1. 在 `LangSmith` 里更像一串零散的 LLM / retrieval 调用，不像一条完整工作流。
2. 当 bad case 出现时，很难清楚表达“系统当前卡在哪个节点、为什么走到下一步”。

`SuperMew` 的一个明显优点，就是把检索编排组织成 LangGraph state graph。这样：

- 节点边界清晰
- 条件分支清晰
- LangSmith 里更容易看到整条图执行链

所以这次不是“借鉴 UI 或 prompt”，而是正式把检索 orchestration 迁到 LangGraph。

## 这次怎么改的

当前 `MY_agent` 的检索主链已经改成 LangGraph 图执行，核心文件在：

- [retrieval_pipeline.py](/c:/Users/hmr/Desktop/Int/agent_RAG/MY_agent/backend/retrieval_pipeline.py)

新增的图执行主入口是：

- `HybridRetrievalPipeline.search()`
- `HybridRetrievalPipeline._search_via_langgraph()`

图里的核心节点是：

1. `primary_retrieve`
2. `grade_primary`
3. `rewrite_queries`
4. `expanded_retrieve`
5. `finalize_remote`
6. `local_fallback`

## 当前图执行逻辑

### 1. `primary_retrieve`

先走 `RAGFlow retrieval API` 做主检索。

输出：

- `base_hits`
- `dataset_scope`

如果远程直接没命中，就转入 `local_fallback`。

### 2. `grade_primary`

对主检索前几条结果做相关性门控。

如果主检索已经足够好，直接进入 `finalize_remote`。
如果不够好，进入 `rewrite_queries`。

### 3. `rewrite_queries`

生成三类改写：

- 法律关键词重写
- Step-Back
- HyDE

这一层的作用是把“用户自然语言问题”变成“更适合法律知识库检索的查询表达”。

### 4. `expanded_retrieve`

把上一步生成的 query 继续送到 `RAGFlow`，把多路候选合并起来。

这一层不会直接产出最终答案，而是先把候选集补全。

### 5. `finalize_remote`

对多路候选做：

- 过滤
- 重排
- 去重

如果这里已经形成稳定命中，就直接把远程结果作为最终上下文。
如果还是不稳定，再转入 `local_fallback`。

### 6. `local_fallback`

这时才回退到本地检索链路：

- Dense
- lexical
- RRF

也就是说，现在本地混合检索已经不是主链路，而是 LangGraph 图里的兜底节点。

## 迁移后的架构含义

迁移前可以说：

> 系统有检索、改写、门控和 fallback。

迁移后可以更清楚地说：

> 系统把 retrieval orchestration 建成了一条显式状态图。  
> RAGFlow 负责主检索，LangGraph 负责节点编排，LangSmith 负责图级别可观测性。

这在面试里是更强的表达，因为它回答了三个问题：

1. 检索步骤有哪些
2. 它们之间如何跳转
3. 为什么 LangSmith 里可以看见一条完整工作流

## 这次迁移解决了什么

### 1. 可观测性更强

原来更像“多次函数调用”。
现在更像“显式状态流转”。

### 2. 更容易解释 bad case

例如：

- 是主检索没命中
- 还是主检索命中了但门控没通过
- 还是 rewrite 后命中了但重排没保住
- 还是最终只能 fallback

这些现在都能对应到具体节点。

### 3. 更接近 `SuperMew` 的工程表达

不是照抄 `SuperMew`，而是吸收它最有价值的一点：

> 检索增强不应该只是“多加几个 prompt”，而应该成为一个可追踪的工作流。

## 当前仍然保留的现实问题

迁到 LangGraph 不代表所有效果问题都自动消失。

当前仍然需要继续迭代的点包括：

- RAGFlow 返回候选本身有时仍不够稳
- 某些关键词 query 可能命中 0 条
- 远程结果的通用重排还有提升空间
- 图执行已经有了，但前端还没显式展示 LangGraph 节点级状态

所以这次迁移的意义是：

> 先把“流程结构”做对，再继续迭代每个节点里的检索质量。

## 一句话版本

现在的 `MY_agent` 不再只是“手写检索 if-else”，而是：

> 用 LangGraph 编排 `主检索 -> 门控 -> 改写 -> 扩展检索 -> 重排 -> fallback`，  
> 用 RAGFlow 承担主检索，  
> 用 LangSmith 观察整条图执行链。
