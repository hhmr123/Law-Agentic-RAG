# MY_agent

基于法律咨询定制的 Agentic RAG 系统。

项目目标不是只做一个“能上传文档并回答问题”的普通 RAG Demo，而是围绕法律问答里的几个高频 bad case，做一套更适合面试展示和后续扩展的工程化方案：既要保证法条检索尽可能准，也要尽量减少上下文断裂和法律幻觉。

## 项目技术创新点

### 1. 法律知识库显式使用 `RAGFlow Laws` 模板

本项目不是把法律 PDF 当成普通长文本处理，而是显式使用 `RAGFlow` 的 `Laws` 模板。

这样做的原因是：

- 法律文档天然有严格层级结构，如“编、章、节、条、款”
- 如果直接用通用字符切分或 token 滑窗切分，很容易把同一法条的适用条件、例外情形和法律后果切散
- 这会导致模型拿到的是“半句话法条”，从而产生法律幻觉

`Laws` 模板的价值在于：

- 它会按法条结构切分，而不是只按 token 长度硬切
- 更适合《民法典》、合同、合规文件、裁判文书等法律材料
- 能更好保持法条语义完整性

这是本项目非常重要的一个法律场景定制点。

### 2. 主检索下沉到 `RAGFlow hybrid retrieval`

项目最终没有把主检索放在本地 Python 逻辑里，而是下沉到 `RAGFlow retrieval API`。

当前主检索更准确的表述是：

- keyword / full-text similarity
- + vector similarity
- + weighted fusion

这样做的原因是：

- 法律问题里很多词必须字面命中，比如“定金”“善意取得”“物权登记”
- 但用户问题又经常不是法条原话，需要语义匹配能力
- 所以只做纯向量检索不稳，只做关键词检索也不够

本项目里，本地 `Dense + lexical + RRF` 仍然保留，但它已经是 fallback，不是主链路。

### 3. Query Rewrite：Step-Back + HyDE

为了缩小“用户自然语言”和“法律条文语言”之间的表达鸿沟，项目加入了两类查询重写：

- `Step-Back`
  - 把用户问题抽象成更标准的法律问题
- `HyDE`
  - 先生成一段“法律风格的假设性答案”，再拿这段文本做语义检索

这解决的是法律 RAG 很典型的问题：

- 用户说的是生活语言
- 知识库里存的是法条语言
- 不做 rewrite，召回很容易偏

### 4. 相关性门控，控制“检索到了但其实不相关”

项目不是“搜到什么就直接喂给模型”，而是先做相关性门控。

这样做是为了避免：

- 检索命中表面相关的条文
- 但这些条文并不能直接支持用户问题
- 模型基于错误上下文一本正经地胡说

因此当前流程中，主检索结果会先过一轮 `grade documents`，不通过才会继续重写和扩展检索。

### 5. 检索编排升级为 `LangGraph`

项目后期把原来手写的顺序调用，升级成了 `LangGraph` 显式状态流。

好处有两点：

- 检索链路更清楚，方便解释系统为什么从这一步走到下一步
- `LangSmith` 里更容易看到完整 graph run，而不是零散的多个 chat 调用

### 6. 会话摘要记忆 + 多会话管理

项目现在不只是单轮演示。

已经支持：

- 多会话管理
- 会话切换
- 会话删除
- 会话摘要记忆

摘要机制会把较早轮次压缩成 summary，再和最近几轮原始消息一起送给模型，减少长对话时的 token 开销和上下文膨胀。

## 核心流程

### 1) 项目全链路（端到端）

```text
用户在前端输入问题
  -> FastAPI 接收请求
  -> Agent 判断是否需要检索
  -> 进入 LangGraph 检索工作流
  -> 调用 RAGFlow 做主检索
  -> 必要时做相关性门控与 Query Rewrite
  -> 汇总上下文后由 Gemini 生成答案
  -> 返回前端答案、来源和执行轨迹
  -> LangSmith 记录整条运行链路
```

### 2) RAG 全链路（重点）

```text
用户问题
  -> primary_retrieve
     使用原问题调用 RAGFlow retrieval API
  -> grade_primary
     判断当前命中的法律片段是否真的相关
  -> rewrite_queries
     生成 keyword rewrite / Step-Back / HyDE
  -> expanded_retrieve
     使用重写后的 query 再次检索 RAGFlow
  -> finalize_remote
     合并、去重、重排远程候选
  -> local_fallback（必要时）
     如果远程结果不稳定，则退回本地 Dense + lexical + RRF
  -> synthesize_answer
     用最终上下文生成法律回答
```

## 技术栈

- Frontend: `React + Vite`
- Backend: `FastAPI`
- Agent orchestration: `LangChain`
- Retrieval workflow: `LangGraph`
- Model: `Gemini`
- KB / retrieval backend: `RAGFlow`
- Observability / evaluation: `LangSmith`
- Local storage: `SQLite`
- Primary retrieval path: `RAGFlow hybrid retrieval`
- Local fallback: `Dense + lexical + RRF`

## 部署方式

本项目依赖两套服务：

- `RAGFlow`
- `MY_agent`

### 1. 启动 RAGFlow

```powershell
cd D:\RAGFlow\ragflow\docker
docker compose -f docker-compose.yml up -d
docker compose ps
```

浏览器访问：

- `http://127.0.0.1`

### 2. 启动 MY_agent

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG\MY_agent
docker compose up -d --build
docker compose ps
```

浏览器访问：

- 前端：`http://127.0.0.1:5173`
- 后端健康检查：`http://127.0.0.1:8000/api/health`

### 3. 推荐环境变量

`MY_agent/.env` 里建议至少配置：

```env
MODEL_PROVIDER=gemini
GOOGLE_API_KEY=你的_Gemini_Key
MODEL=gemini-3.1-flash-lite-preview

RAGFLOW_ENABLED=true
RAGFLOW_BASE_URL=http://host.docker.internal:9380
RAGFLOW_API_KEY=你的_RAGFlow_Key
RAGFLOW_DATASET_NAME=MY_agent_laws
RAGFLOW_DATASET_CHUNK_METHOD=laws
RAGFLOW_AUTO_CREATE_DATASET=true

LANGSMITH_API_KEY=你的_LangSmith_Key
LANGSMITH_PROJECT=MY_agent
LANGSMITH_TRACING=true
```

### 4. 启动后的检查项

理想情况下，访问：

- `http://127.0.0.1:8000/api/health`

应该返回：

```json
{
  "ok": true,
  "ragflow_enabled": true,
  "langsmith_enabled": true
}
```

### 5. 一句话部署顺序

```text
先启动 RAGFlow
再启动 MY_agent
检查 /api/health
上传法律文档
发起问题并在 LangSmith 查看 trace
```
