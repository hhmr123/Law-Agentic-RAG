# MY_agent

基于法律咨询定制的 Agentic RAG 系统。

项目目标不是只做一个“能上传文档并回答问题”的普通 RAG Demo，而是围绕法律问答里的几个高频 bad case，做一套更适合面试展示和后续扩展的工程化方案：既要保证法条检索尽可能准，也要尽量减少上下文断裂和法律幻觉。

## 项目技术创新点

### 1. 基于正则与版面的层级识别（Structure-aware Chunking）

本项目处理的是法律咨询场景，因此文档切分不是普通的 token 滑窗切分，而是强调“结构感知”。

这样做的原因是：

- 法律文档天然有严格层级结构，如“编、章、节、条、款”
- 如果使用通用字符切分，很容易把同一法条的适用条件、例外情形和法律后果切散
- 一旦法条被截断，模型拿到的就是不完整上下文，很容易出现法律幻觉

本项目里，这个创新点更准确的说法是：

- 基于正则与版面的层级识别
- 尽量在法条边界、条款边界完成切分
- 保证一个 chunk 内尽可能包含语义完整的法律单元

### 2. Parent-Child（父子块）保留上下文机制

法律问答里，一个命中的短片段往往并不足够。

例如：

- 命中了某一条里的例外条款
- 但如果缺少它所属法条、前款或上位上下文
- 模型很可能误判适用范围

所以本项目强调：

- 子块负责精准匹配 query
- 父块负责补足法律上下文

这个思路的目标不是单纯“切得更碎”，而是：

- 检索时保持精度
- 生成时保证上下文完整

### 3. Hybrid Retrieval 混合检索

本项目核心检索策略是 `hybrid retrieval`，也就是同时结合：

- keyword / full-text similarity
- vector similarity
- weighted fusion

这样做的原因是：

- 法律问题里很多词必须字面命中，比如“定金”“善意取得”“物权登记”
- 但用户提问经常不是法条原话，而是生活语言或咨询语言
- 所以如果只做纯向量检索，可能“懂意思但漏掉关键法条”
- 如果只做关键词检索，又可能只能搜到字面相似，召回不到真正相关的法律概念

混合检索的价值在于：

- 既保留法律术语的精确命中能力
- 又保留语义泛化能力
- 更适合法律咨询这类“术语精确 + 表达多样”的场景

### 4. Query Rewrite：Step-Back + HyDE

为了缩小“用户自然语言”和“法律条文语言”之间的表达鸿沟，项目加入了两类查询重写：

- `Step-Back`
  - 把用户问题抽象成更标准的法律问题
- `HyDE`
  - 先生成一段“法律风格的假设性答案”，再拿这段文本做语义检索

这解决的是法律 RAG 很典型的问题：

- 用户说的是生活语言
- 知识库里存的是法条语言
- 不做 rewrite，召回很容易偏

### 5. 相关性门控，控制“检索到了但其实不相关”

项目不是“搜到什么就直接喂给模型”，而是先做相关性门控。

这样做是为了避免：

- 检索命中表面相关的条文
- 但这些条文并不能直接支持用户问题
- 模型基于错误上下文一本正经地胡说

因此当前流程中，主检索结果会先过一轮 `grade documents`，不通过才会继续重写和扩展检索。

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
