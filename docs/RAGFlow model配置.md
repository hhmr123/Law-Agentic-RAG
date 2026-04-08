# RAGFlow Model 配置

这份文档专门回答两个问题：

1. 为什么 `RAGFlow` 明明是做 RAG 的，还要额外配置 model
2. 对 `MY_agent` 这套法律项目来说，RAGFlow 最推荐怎么配

## 1. 先讲清楚：RAGFlow 不是“自带一切模型的向量数据库”

你可以把一套 RAG 系统拆成几步：

1. 上传文档
2. 解析文档
3. 切 chunk
4. 用 embedding model 把 chunk 变成向量
5. 建立全文索引与向量索引
6. 做检索
7. 把检索结果交给 chat model 生成答案

这里面：

- `RAGFlow` 负责解析、切块、索引、检索、引用返回
- `doc engine` 负责承载全文索引和向量索引
- `embedding model` 负责向量化
- `chat model` 负责最终回答

所以：

> RAGFlow 不是“只要把容器拉起来，就会自动具备 embedding 能力”的产品。

如果 embedding model 没配好，文档可以上传，但无法真正完成向量化和建索引。

## 2. 为什么这件事在你这里一定要配

你的项目现在不是只让 `RAGFlow` 存文档，而是让它承担主检索链路。

只要主检索走 `RAGFlow retrieval API`，那它就必须具备：

- 可用的 embedding model
- 可用的 chat model
- 正常完成的 parse / chunk / index

否则就会出现：

- 页面能打开
- 文档能上传
- 但 `chunk_count` 一直是 `0`
- 检索返回空

## 3. 对你当前项目的推荐配置

### 模型 provider

优先推荐：

- `Gemini` 作为 chat model
- `Qwen embedding` 或你已经验证能用的 embedding model

如果你已经在 `RAGFlow` UI 里把：

- provider
- API key
- 默认 embedding model

都配好了，那这一步就算完成。

### dataset 级别配置

对法律文档，推荐：

- dataset 名称：`MY_agent_laws`
- `chunk_method`: `laws`

不推荐继续把法律 PDF 放在：

- `general`
- `naive`

原因很直接：

- `general` 更偏通用切块
- `laws` 更贴合法条、条款、款项这类结构化法律文本

## 4. 现在这套项目的默认目标

当前 `MY_agent` 已经改成默认写入：

```env
RAGFLOW_DATASET_NAME=MY_agent_laws
RAGFLOW_DATASET_CHUNK_METHOD=laws
```

也就是说：

- 以后新上传的法律文档，目标不是旧的 `MY_agent / general`
- 而是新的 `MY_agent_laws / laws`

## 5. 如果你要在 RAGFlow UI 里手动核对

### Step 1. 打开 RAGFlow

- <http://127.0.0.1>

### Step 2. 进入 Model providers

确认：

- 你的 chat model provider 已经可用
- 你的 embedding model provider 已经可用

### Step 3. 进入 System Model Settings

确认：

- 默认 chat model 不是空
- 默认 embedding model 不是空

### Step 4. 检查 dataset

确认新的法律 dataset：

- 名称是 `MY_agent_laws`
- chunk method 是 `laws`

## 6. 判断是否真的配置成功

你可以用这 4 个信号判断：

1. `RAGFlow` 页面能正常打开
2. 文档 parse 最终到 `100%`
3. `chunk_count > 0`
4. `MY_agent` 提问时，trace 里远程 retrieval 能命中结果

## 7. 和面试相关的准确说法

不要说：

- “RAGFlow 就是一个向量数据库”

更准确的说法是：

> RAGFlow 在我的项目里承担了解析、切块、索引和主检索能力；embedding model 负责把 chunk 变成向量，doc engine 负责全文索引和向量索引的混合检索。

## 8. 当前需要你知道的一个现实状态

当前 `MY_agent` 本地环境已经清空，后续新上传会走 `MY_agent_laws`。

如果你在 `RAGFlow` 页面里还看到旧的 `MY_agent / general` 历史文档，那属于旧任务遗留状态，不影响你后续用新的法律 dataset 继续上传和问答。
