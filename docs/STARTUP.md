# MY_agent 启动说明

这份文档只回答一个问题：

> 以后每次我要启动这个项目，应该按什么顺序做？

## 你现在会接触到的 3 个对象

- `RAGFlow`
- `MY_agent`
- `LangSmith`

它们的启动逻辑不一样：

- `RAGFlow` 需要单独启动 Docker Compose
- `MY_agent` 也需要单独启动自己的 Docker Compose
- `LangSmith` 不需要本地启动服务，只要 `.env` 里的 key 正确即可

## 标准启动顺序

1. 先启动 `RAGFlow`
2. 再启动 `MY_agent`
3. 检查 `/api/health`
4. 确认上传会进入 `MY_agent_laws`
5. 提问并去 `LangSmith` 看 trace

## 一、启动 RAGFlow

目录：

```powershell
cd D:\RAGFlow\ragflow\docker
```

启动：

```powershell
docker compose -f docker-compose.yml up -d
```

查看状态：

```powershell
docker compose ps
```

查看日志：

```powershell
docker compose logs -f
```

停止：

```powershell
docker compose down
```

### 下次启动还要不要这样写

要。

RAGFlow 日常重启还是：

```powershell
docker compose -f docker-compose.yml up -d
```

一般不需要 `--build`，因为它主要是官方镜像。

## 二、启动 MY_agent

目录：

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG\MY_agent
```

### 如果你刚改过代码、依赖或 Dockerfile

```powershell
docker compose down
docker compose up -d --build
```

### 如果你只改了 `.env`

```powershell
docker compose down
docker compose up -d
```

### 如果只是日常启动

```powershell
docker compose up -d
```

查看状态：

```powershell
docker compose ps
```

查看后端日志：

```powershell
docker compose logs -f backend
```

查看前端日志：

```powershell
docker compose logs -f frontend
```

停止：

```powershell
docker compose down
```

## 三、启动后先检查什么

### 1. 后端健康状态

打开：

- <http://127.0.0.1:8000/api/health>

理想结果：

```json
{
  "ok": true,
  "ragflow_enabled": true,
  "langsmith_enabled": true
}
```

### 2. 前端页面

打开：

- <http://127.0.0.1:5173>

### 3. RAGFlow 页面

打开：

- <http://127.0.0.1>

### 4. LangSmith 页面

打开：

- <https://smith.langchain.com/>

## 四、你当前这套 `.env` 的关键写法

你现在的部署方式是：

- `RAGFlow` 跑在宿主机 Docker
- `MY_agent` 也跑在 Docker

所以 `MY_agent` 容器访问 `RAGFlow` 时，推荐写法是：

```env
RAGFLOW_ENABLED=true
RAGFLOW_BASE_URL=http://host.docker.internal:9380
RAGFLOW_API_KEY=你的_RAGFlow_Key
RAGFLOW_DATASET_NAME=MY_agent_laws
RAGFLOW_DATASET_CHUNK_METHOD=laws
RAGFLOW_AUTO_CREATE_DATASET=true
```

不要写：

```env
RAGFLOW_BASE_URL=http://127.0.0.1:9380
```

因为在容器里，`127.0.0.1` 指向的是容器自己，不是宿主机。

LangSmith 只需要：

```env
LANGSMITH_API_KEY=你的_LangSmith_Key
LANGSMITH_PROJECT=MY_agent
LANGSMITH_TRACING=true
```

## 五、法律文档上传前的推荐检查

在重新上传法律文档前，先确认两件事：

1. `MY_agent` 的 `.env` 已经是：

```env
RAGFLOW_DATASET_NAME=MY_agent_laws
RAGFLOW_DATASET_CHUNK_METHOD=laws
```

2. `RAGFlow` 里的模型已经配置好：

- 默认 chat model 可用
- 默认 embedding model 可用

如果没配好，先看：

- [RAGFlow Model 配置](/c:/Users/hmr/Desktop/Int/agent_RAG/MY_agent/docs/RAGFlow%20model%E9%85%8D%E7%BD%AE.md)

## 六、重新上传前的当前状态

当前我已经帮你做了这些清理：

- 本地 `MY_agent` 的文档记录已清空
- 本地 `retrieval_chunks` 已清空
- 本地上传目录已清空

当前需要你知道的一点是：

- 旧的 `MY_agent / general` dataset 在 `RAGFlow` 侧可能还保留历史状态
- 但后续新上传会优先进入新的 `MY_agent_laws`
- 所以后续使用不会再依赖旧的 `general` dataset

## 七、推荐的日常命令

### 先启动 RAGFlow

```powershell
cd D:\RAGFlow\ragflow\docker
docker compose -f docker-compose.yml up -d
docker compose ps
```

### 再启动 MY_agent

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG\MY_agent
docker compose up -d
docker compose ps
```

如果你刚改过代码：

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG\MY_agent
docker compose down
docker compose up -d --build
docker compose ps
```

### 快速检查健康状态

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/health
```

## 八、常见情况速查

### 改了 `.env` 但项目没变化

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG\MY_agent
docker compose down
docker compose up -d
```

### 改了代码但容器里还是旧逻辑

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG\MY_agent
docker compose down
docker compose up -d --build
```

### RAGFlow 页面能打开，但上传后一直不出 chunk

先检查：

- 模型 provider 是否配置完成
- embedding model 是否设置成功
- dataset 是否真的在用 `laws`
- 文档 parse 是否完成到 `100%`

### 旧文档想再同步进 RAGFlow

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG\MY_agent
docker compose exec backend python -m backend.sync_ragflow
```

## 九、本地重要路径

- 上传目录: [data/uploads](/c:/Users/hmr/Desktop/Int/agent_RAG/MY_agent/data/uploads)
- SQLite: [data/app.db](/c:/Users/hmr/Desktop/Int/agent_RAG/MY_agent/data/app.db)

## 一句话版

以后每次最稳的流程就是：

1. `RAGFlow` 执行 `docker compose -f docker-compose.yml up -d`
2. `MY_agent` 执行 `docker compose up -d`
3. 打开 `/api/health`
4. 确认会落到 `MY_agent_laws`
5. 上传法律文档并提问
