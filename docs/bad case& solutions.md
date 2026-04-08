# Bad Case & Solutions

这份文档记录 `MY_agent` 在演进过程中遇到过、以及明确预判会遇到的 bad case，并说明为什么这样修。

## Case 1：Native tool calling 不稳定

### 现象

早期版本里，Gemini 走 LangChain native tool calling 时，trace 会出现：

- tool call 触发了
- 但中间流程不稳定
- 然后 fallback 到手动检索

### 分析过程

一开始不能直接下结论说“模型不行”，因为问题可能出在：

- 前端请求
- 后端工具定义
- 检索没命中
- Agent 编排层
- 模型与 tool-calling 协议兼容层

后来确认：

- retrieval 是能命中的
- generation 也是正常的

真正不稳定的是“让模型原生调工具”这一步。

### 解决方案

把主链路改成更稳定的 orchestrated retrieval mode：

- 模型负责路由与生成
- 系统负责控制检索调用

### 面试时可以怎么说

> 我没有把问题简单归因成模型差，而是先分层定位。确认 retrieval 和 generation 都没坏之后，才把问题收敛到 tool-calling 兼容层，然后把主链路改成更稳定的 orchestrated 模式。

## Case 2：法律检索“搜不准、搜不到”

### 现象

用户问：

> 关于定金罚则有什么规定？

如果只做 dense retrieval，容易把“定金罚则”泛化成“违约责任”，召回很多似是而非的法条。

### 分析过程

法律场景下很多术语必须字面精确命中：

- 定金
- 抵押权
- 孳息
- 善意取得

所以仅靠语义接近是不够的。

### 解决方案

这里要分清主链路和 fallback：

- 主链路：`RAGFlow retrieval API`
  - full-text / keyword similarity
  - + vector similarity
  - + weighted fusion
- fallback：本地 `Dense + lexical + RRF`

### 面试时可以怎么说

> 我没有把 Hybrid Search 简化成“只有向量检索”。主链路走 RAGFlow 的全文加向量混合检索，本地 Dense + BM25 + RRF 只保留为 fallback。

## Case 3：平民表达和法律术语之间有语义鸿沟

### 现象

用户问：

> 我买的二手车被调表了，我能要求退一赔三吗？

这句话通常不是法条原话，直接搜很容易搜不到。

### 分析过程

这里的失败点往往不在 retrieval engine，而在 query 本身不适合法律知识库。

### 解决方案

引入 Query Rewrite：

- Step-Back：把问题抽象成更一般的法律关系
- HyDE：生成更像法律语言的假设答案，再辅助检索

## Case 4：命中碎片，但上下文断裂

### 现象

用户问：

> 法条里写“前款规定的除外”，这里的前款到底指什么？

如果系统只命中：

> 前款规定的除外。

那模型几乎无法独立理解。

### 分析过程

这里不是没搜到，而是搜到的粒度不适合直接生成。

### 解决方案

引入：

- 三级分块
- leaf-only 检索
- auto-merging

让小块负责命中，上层块负责补语境。

## Case 5：检索到了不相关内容，反而诱发幻觉

### 现象

用户问：

> 今天北京天气怎么样？

如果系统强行搜法律库，可能会搜到“恶劣天气”“不可抗力”之类内容，然后一本正经地答错。

### 分析过程

最危险的不是“没搜到”，而是“搜到一点看似相关但其实无关的内容”。

### 解决方案

加入 Grade Documents：

- 检索后先判断相关不相关
- 不相关就不把这些 chunk 当依据

## Case 6：后端做了很多事，但前端像死机

### 现象

加了 rewrite、hybrid retrieval、gate、auto-merge 之后，请求可能需要几秒。

如果前端一片空白，用户会以为系统挂了。

### 解决方案

加入流式执行轨迹：

- `route`
- `hybrid_search`
- `grade_documents`
- `query_rewrite`
- `auto_merge`
- `synthesize_answer`

## Case 7：RAGFlow 历史重任务把系统拖到 502

### 现象

之前有一份历史大 PDF 在 `RAGFlow` 里触发了很重的解析任务，导致：

- `ragflow-cpu` 高负载
- API 接口超时
- UI 出现 `502 Gateway error`
- `MY_agent` 主检索拿不到远程结果

### 分析过程

这类问题很容易被误判成“MY_agent 检索代码坏了”。但真实问题其实在知识库底座：

- 文档任务过重
- 任务残留
- RAGFlow 自身接口被拖慢

### 解决方案

这里的处理思路不是盲目改 Agent，而是先恢复知识库健康：

- 清掉异常文档任务
- 恢复 RAGFlow 基础服务
- 再看 MY_agent 的 retrieval 是否恢复

### 面试时可以怎么说

> 我区分了“Agent 层问题”和“知识库底座问题”。当 RAGFlow 自身的解析任务把 API 拖死时，继续调 Agent 是没有意义的，必须先把底层服务恢复健康。

## Case 8：长对话把 prompt 撑爆

### 现象

用户连续咨询十几二十轮法律问题，如果每轮都把所有历史原样塞进 prompt：

- token 成本会越来越高
- 上下文越来越嘈杂
- 模型稳定性下降

### 解决方案

加入 Session Summary：

- 超过阈值后压缩较早历史
- 保留摘要
- 最近几轮继续原样保留

## 总结

这份文档最想说明的是：

> 我不是看到别人用了什么就照搬什么，而是先定义 bad case，再定位失败层级，最后只引入真正能修这个层级问题的能力。
