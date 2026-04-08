# RAGFlow 与 LangSmith 从零配置指南

这份文档是给第一次接触 `RAGFlow` 和 `LangSmith` 的你准备的。

目标只有两个：

1. 你能自己把 `RAGFlow` 跑起来
2. 你能自己拿到 `LangSmith API Key` 和 `RAGFlow API Key`，并把它们填进 `MY_agent`

我会尽量把步骤写到“照着点就能完成”的程度。

---

## 先理解这 3 个东西分别是什么

你的项目里其实有 3 层：

- `LangChain`：Python 代码库，不需要单独启动一个服务
- `RAGFlow`：独立的 RAG 系统，通常用 Docker 启动
- `LangSmith`：LangChain 官方的 tracing / evaluation 平台，通常直接用云版

所以你不需要去“安装 LangChain 服务”。

你真正要配置的是：

- 给 `MY_agent` 准备一个模型 API Key
- 启动 `RAGFlow`
- 在 `RAGFlow` 里配置模型
- 在 `LangSmith` 里创建 API Key
- 再把这些值填回 `MY_agent/.env`

---

## 一、开始前准备

### 1. 你已经具备的东西

- Windows
- Docker Desktop
- 当前项目目录：`C:\Users\hmr\Desktop\Int\agent_RAG\MY_agent`

### 2. 你还需要具备的东西

- 一个可用的模型 API Key
- 一个 LangSmith 账号
- Git

### 3. 推荐你现在就安装 Git

如果你还没有 Git：

1. 打开：<https://git-scm.com/download/win>
2. 下载并安装
3. 安装完后打开 PowerShell，执行：

```powershell
git --version
```

如果能看到版本号，就说明 Git 已经好了。

---

## 二、先拿到模型 API Key

无论是 `MY_agent` 还是 `RAGFlow`，都需要一个“真正能调用大模型”的 key。

你现在项目里已经在用 Gemini，所以我先按 Gemini 给你讲。

### 1. 打开 Google AI Studio

官网：

- <https://ai.google.dev/aistudio>

官方说明：

- Google AI Studio 主页：<https://ai.google.dev/aistudio>
- Gemini API Key 教程：<https://ai.google.dev/tutorials/setup>
- Gemini OpenAI compatibility：<https://ai.google.dev/gemini-api/docs/openai>

### 2. 登录 Google 账号

第一次登录时，通常会要求：

- 接受条款
- 选择或创建项目

根据 Google 官方说明，新用户通常会自动获得一个默认项目和一个默认 key；如果没有，就需要手动创建或导入项目。官方文档里写到，已有 Google Cloud 用户可能需要先在 AI Studio 里导入项目。

### 3. 创建或查看 Gemini API Key

在 Google AI Studio 里：

1. 进入 `Dashboard`
2. 找到 `Projects`
3. 如果没有项目：
   - 点击 `Import projects`
   - 选择一个 Google Cloud project 导入
4. 再进入 `API Keys`
5. 点击 `Create API key`
6. 复制这个 key

把它先临时保存到记事本里，后面会用到。

### 4. 你要记住的两个值

如果你继续用 Gemini 的 OpenAI 兼容接口，`MY_agent` 里建议这样填：

```env
ARK_API_KEY=你刚拿到的 Gemini API key
MODEL=gemini-2.5-flash
BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
```

注意：

- `BASE_URL` 建议用官方 OpenAI compatibility 地址
- 末尾最好带 `/openai/`
- 不要再额外加单引号

---

## 三、配置 LangSmith

LangSmith 最简单的使用方式不是 Docker，而是官方云版。

官方文档：

- 创建账号与 API Key：<https://docs.langchain.com/langsmith/create-account-api-key>
- Tracing quickstart：<https://docs.langchain.com/langsmith/observability-quickstart>

### 1. 注册 LangSmith 账号

打开：

- <https://smith.langchain.com/>

然后：

1. 点击 `Sign up`
2. 用邮箱或第三方账号注册
3. 注册完成后登录

### 2. 创建 LangSmith API Key

这是你最关心的一步。

登录后：

1. 点击左下角或左侧边栏的 `Settings`
2. 找到 `API Keys`
3. 点击 `Create API Key`
4. 选择 key 类型
   - 你个人学习用，直接先建一个普通 key 就可以
5. 给 key 起名字
   - 例如：`my-agent-local`
6. 设置有效期
   - 你可以先选 `Never` 或较长时间
7. 点击 `Create API Key`
8. **立刻复制这个 key**

LangSmith 官方文档明确说明：这个 key 通常只会展示一次，所以一定要立刻保存。

### 3. 把 LangSmith 填进 `MY_agent/.env`

打开文件：

- [`.env`](c:/Users/hmr/Desktop/Int/agent_RAG/MY_agent/.env)

把这几行改成：

```env
LANGSMITH_API_KEY=你刚创建的 LangSmith key
LANGSMITH_PROJECT=MY_agent
LANGSMITH_TRACING=true
```

### 4. 如何判断 LangSmith 配好了

你的 `MY_agent` 运行后：

1. 再去问一次问题
2. 打开 LangSmith 网页
3. 看 `MY_agent` 这个 project 下有没有新的 trace

如果没有 trace，最常见的原因就是：

- key 填错了
- 没重启容器
- `LANGSMITH_TRACING=false`

### 5. 修改 `.env` 后要重启

每次改完 `.env`，都执行：

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG\MY_agent
docker compose down
docker compose up -d --build
```

---

## 四、安装并启动 RAGFlow

官方来源：

- RAGFlow 官方 GitHub：<https://github.com/infiniflow/ragflow>
- 官方 releases：<https://github.com/infiniflow/ragflow/releases>

RAGFlow 官方 README 当前写的先决条件大致是：

- CPU >= 4 cores
- RAM >= 16 GB
- Disk >= 50 GB
- Docker >= 24

### 1. 先检查 Docker Desktop 资源

在 Windows 上，建议先打开 Docker Desktop，确认资源别太低。

建议你至少确保：

- 内存尽量接近或达到 `16 GB`
- 磁盘空间足够

如果 Docker Desktop 资源太小，RAGFlow 常见现象是：

- Elasticsearch 起不来
- 页面 502
- 文档一直解析失败

### 2. 下载 RAGFlow

在你想放置 RAGFlow 的目录执行：

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG
git clone https://github.com/infiniflow/ragflow.git
cd ragflow
```

### 3. 切到稳定版本

RAGFlow 官方 README 建议让代码和镜像版本匹配。

你可以先看 release：

- <https://github.com/infiniflow/ragflow/releases>

然后切到一个稳定 tag，例如官方 README 当前提到的 `v0.23.1`：

```powershell
git checkout v0.23.1
```

如果该 tag 将来变化了，就用当时 release 页面里的稳定版。

### 4. 进入 Docker 目录

```powershell
cd docker
```

### 5. 先不改复杂配置，直接启动 CPU 版

官方 README 的 CPU 启动方式是：

```powershell
docker compose -f docker-compose.yml up -d
```

第一次启动会拉很多镜像，耐心等。

### 6. 看容器是否正常

```powershell
docker compose ps
docker compose logs -f
```

如果你只想盯某个服务的日志，可以用：

```powershell
docker compose logs -f ragflow-server
```

服务名如果和你本地不完全一致，以 `docker compose ps` 里实际显示为准。

### 7. 打开 RAGFlow 页面

官方 README 说明默认服务端口是 `80`。

所以通常浏览器打开：

- <http://127.0.0.1>

如果你后面自己改了端口映射，就用你改后的端口。

---

## 五、第一次登录 RAGFlow

这一段我说明一下：

- RAGFlow 官方 README 明确讲了 Docker 启动方式
- 但“UI 上某个按钮的具体文案和位置”会随版本改版

下面这部分是我基于当前版本常见 UI 流程给你的操作建议；如果你发现按钮名字略有不同，不要慌，核心逻辑是一样的。

### 1. 打开登录页

第一次进入 RAGFlow，通常会看到登录 / 注册页面。

### 2. 如果没有账号，就先注册

一般流程是：

1. 点击 `Register` 或 `Sign up`
2. 输入邮箱 / 用户名 / 密码
3. 创建你的第一个账号

如果页面不允许注册，先看日志里有没有服务错误；某些启动异常会表现成“页面能开，但注册/登录没有反应”。

### 3. 登录成功后先别急着传文档

你先做下一件事：

- 配置模型

因为 RAGFlow 本身要解析、嵌入、问答，必须先知道你要用哪个模型提供方。

---

## 六、在 RAGFlow 里配置模型

RAGFlow 官方 README 明确提到：

- 可以在 `service_conf.yaml.template` 里配置 `user_default_llm`
- 需要选择模型工厂（provider/factory）并填写对应 API key

你有两种做法：

### 做法 A：先用 UI 配

如果当前版本 UI 提供模型配置界面，这是最适合新手的。

你登录后，优先在以下位置找：

- `Settings`
- `Model Providers`
- `Model Settings`
- `LLM`
- `Embedding`

不同版本名字可能略有差异。

#### 如果你打算继续用 Gemini

你需要准备：

- Chat model key：Gemini API key
- Embedding model：如果 RAGFlow 支持你当前提供方的 embedding，就在 UI 里一起配

注意：

- 有些 RAGFlow 工作流需要同时配置 `chat model` 和 `embedding model`
- 只配聊天模型但没配 embedding，知识库检索可能跑不起来

### 做法 B：用配置文件预置

如果你在 UI 里没找到模型配置，或者想更确定一点，可以看这个文件：

- `ragflow/docker/service_conf.yaml.template`

RAGFlow 官方 README 说明这里可以配置 `user_default_llm`。

这意味着你可以在这个模板里为默认模型填入：

- provider / factory
- api_key
- base_url
- chat_model
- embedding_model

这部分字段在不同 provider 下写法会不完全一样，所以如果你后面决定“RAGFlow 也继续用 Gemini”，我建议我们下一步单独再针对你的版本把这一段配死。

### 新手建议

对你目前来说，最稳的顺序是：

1. 先把 RAGFlow 服务跑起来
2. 先能登录 UI
3. 先在 UI 里找到模型配置页
4. 先配一个 chat model
5. 再配 embedding model
6. 再去建 dataset 和上传文档

---

## 七、在 RAGFlow 里创建 Dataset 并上传文档

当模型配置好之后：

1. 进入 `Datasets` 或 `Knowledge Base`
2. 点击 `Create dataset`
3. 给它起名
   - 例如：`resume-kb`
4. 选择合适的解析方式 / chunking 方式
   - 先用默认值就行
5. 创建后进入这个 dataset
6. 点击 `Upload`
7. 上传你的简历文件，例如：
   - `docx`
   - `pdf`
   - `md`
8. 等待解析完成

如果解析失败，先不要怀疑自己，先看：

- RAGFlow 页面里的 document status
- `docker compose logs -f`

---

## 八、如何获得 RAGFlow API Key

这是你问得最多的点，所以我单独写。

### 先说明

RAGFlow 的 API key 是给“程序调用 RAGFlow HTTP API”用的，不是给模型供应商用的。

也就是说：

- `Gemini API key`：拿来调用 Gemini
- `RAGFlow API key`：拿来让 `MY_agent` 调 RAGFlow

它们不是同一个东西。

### 常见获取方式

在当前版本的 RAGFlow UI 里，API key 一般会在用户设置区域。

请按这个顺序找：

1. 登录 RAGFlow
2. 看页面右上角头像 / 用户菜单
3. 找下面这些关键词之一：
   - `API Key`
   - `API Keys`
   - `Access Token`
   - `Developer`
   - `Settings`
4. 点击 `Create API Key`、`New Key` 或类似按钮
5. 复制生成出来的 key

从社区里能看到，RAGFlow 的 key 常见前缀是 `ragflow-...`。这一点我从公开 issue 里看到了，但具体 UI 文案会随版本变。

### 如果你实在找不到 API Key 按钮

这时请这样做：

1. 先确认你登录的是自己的账号
2. 先去 `Settings` 页把所有 tab 看一遍
3. 在页面里搜索 `API`
4. 还是找不到的话，说明你当前版本 UI 入口改了

这时最有效的办法是：

- 把 RAGFlow 当前页面截图发我
- 我按你那一版界面直接告诉你点哪里

---

## 九、把 RAGFlow 和 LangSmith 填回 `MY_agent`

打开：

- [`.env`](c:/Users/hmr/Desktop/Int/agent_RAG/MY_agent/.env)

### 1. 先填模型相关

如果你继续用 Gemini：

```env
ARK_API_KEY=你的_Gemini_API_Key
MODEL=gemini-2.5-flash
BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
```

### 2. 再填 LangSmith

```env
LANGSMITH_API_KEY=你的_LangSmith_API_Key
LANGSMITH_PROJECT=MY_agent
LANGSMITH_TRACING=true
```

### 3. 再填 RAGFlow

如果 `MY_agent` 跑在 Docker 里，而 `RAGFlow` 是你单独在宿主机启动的，Windows 下一般建议这么写：

```env
RAGFLOW_ENABLED=true
RAGFLOW_BASE_URL=http://host.docker.internal
RAGFLOW_API_KEY=你的_RAGFlow_API_Key
```

如果你的 RAGFlow 不是默认端口，再补端口：

```env
RAGFLOW_BASE_URL=http://host.docker.internal:80
```

或者如果你改成了别的端口，例如 `9380`：

```env
RAGFLOW_BASE_URL=http://host.docker.internal:9380
```

### 4. 关于 `RAGFLOW_SEARCH_PATH`

这里我要非常诚实地提醒你：

你当前项目里的 [ragflow_gateway.py](c:/Users/hmr/Desktop/Int/agent_RAG/MY_agent/backend/ragflow_gateway.py) 还是一个“RAGFlow-ready adapter”，也就是：

- 它已经把“要接 RAGFlow”这件事的结构搭好了
- 但具体用 RAGFlow 的哪个 HTTP API 路径，还需要根据你最终决定的调用方式再做一次对齐

所以你现在可以先把 RAGFlow 本体和账号都配好，但要让 `MY_agent` 100% 准确地调用它，通常还需要我再帮你补一轮接口对接。

这不是你配置错了，而是项目当前阶段本来就还在 `adapter` 阶段。

---

## 十、改完 `.env` 以后怎么生效

每次改完 `MY_agent/.env`，都执行：

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG\MY_agent
docker compose down
docker compose up -d --build
```

检查状态：

```powershell
docker compose ps
docker compose logs -f backend
```

---

## 十一、如何验证 LangSmith 配好了

### 本地验证

先问一个问题：

```text
请总结一下这个项目为什么选择 LangChain、RAGFlow 和 LangSmith
```

### 网页验证

然后打开：

- <https://smith.langchain.com/>

看：

1. 有没有 `MY_agent` 这个 project
2. 有没有新 trace

如果后端日志里出现 `401 Unauthorized`，基本就是：

- `LANGSMITH_API_KEY` 错了
- 或者你复制时漏字符了

---

## 十二、如何验证 RAGFlow 配好了

### 验证 1：RAGFlow 页面能打开

能打开登录页或主界面，说明服务基本起来了。

### 验证 2：能创建 dataset

如果你能成功新建 dataset，说明：

- UI 正常
- 后端数据库基本正常

### 验证 3：能上传文档并解析

如果文档状态变成成功，说明：

- 文档处理链路基本正常
- 模型和 embedding 至少有一部分已经配通

### 验证 4：能拿到 RAGFlow API Key

拿到 key 后，说明你已经具备程序调用条件。

---

## 十三、最常见的坑

### 1. LangSmith 一直 401

原因通常是：

- key 错了
- key 没复制全
- 改了 `.env` 但没重启容器

### 2. RAGFlow 页面 502 或登录没反应

常见原因：

- Docker Desktop 资源不够
- Elasticsearch 没起来
- 某个依赖容器异常

先看：

```powershell
docker compose ps
docker compose logs -f
```

### 3. RAGFlow 启动很慢

第一次拉镜像很正常。

### 4. `MY_agent` 里填了 RAGFlow 配置还是没命中

别急，这不一定是你配错了。

更可能是：

- 当前 `ragflow_gateway.py` 还没完全对齐真实 RAGFlow HTTP API
- 这是项目 adapter 的下一步工作，不是基础环境没配好

---

## 十四、推荐你现在按这个顺序做

### 路线 A：最稳

1. 先拿到 Gemini API key
2. 先拿到 LangSmith API key
3. 把 LangSmith 配进 `MY_agent`
4. 重启 `MY_agent`
5. 确认 LangSmith trace 已出现
6. 再去安装 RAGFlow
7. 在 RAGFlow 里创建 dataset、上传简历
8. 再回来做 `MY_agent` 和 `RAGFlow` 的接口联调

### 为什么我建议这样做

因为：

- LangSmith 接入最简单，容易先拿到正反馈
- RAGFlow 是一个完整系统，启动和模型配置比 LangSmith 重得多
- 先分开验证，排错成本最低

---

## 十五、你现在最该立刻做的 3 件事

### 第 1 件

打开 Google AI Studio，拿到 Gemini API key：

- <https://ai.google.dev/aistudio>

### 第 2 件

打开 LangSmith，注册并创建 API key：

- <https://smith.langchain.com/>
- <https://docs.langchain.com/langsmith/create-account-api-key>

### 第 3 件

按官方 README 启动 RAGFlow：

```powershell
cd C:\Users\hmr\Desktop\Int\agent_RAG
git clone https://github.com/infiniflow/ragflow.git
cd ragflow
git checkout v0.23.1
cd docker
docker compose -f docker-compose.yml up -d
```

---

## 参考来源

- RAGFlow 官方 GitHub：<https://github.com/infiniflow/ragflow>
- RAGFlow 官方 Releases：<https://github.com/infiniflow/ragflow/releases>
- Gemini API Key 官方教程：<https://ai.google.dev/tutorials/setup>
- Gemini OpenAI compatibility：<https://ai.google.dev/gemini-api/docs/openai>
- Google AI Studio：<https://ai.google.dev/aistudio>
- LangSmith 创建账号与 API Key：<https://docs.langchain.com/langsmith/create-account-api-key>
- LangSmith tracing quickstart：<https://docs.langchain.com/langsmith/observability-quickstart>

---

## 最后一句

这份文档帮你把“账号、key、服务启动、`.env` 回填”都拆开了。

如果你愿意，下一步我可以继续直接陪你做其中一个：

1. 我带你一步一步把 `LangSmith` 先配置成功  
2. 我带你一步一步把 `RAGFlow` 在你电脑上真正启动起来  

你只要告诉我你现在想先做 `LangSmith` 还是 `RAGFlow`。
