# 配置清单与多模型使用说明

配置文件名是 **`.env`**，放在仓库根目录，与 `agent.py`、`api_server.py` 同级。

| 文件 | 职责 | 为什么这样拆分 |
| --- | --- | --- |
| `.env` | 当前机器的实际配置和密钥，不提交 Git | 切换服务、调整预算时不需要改 Python；密钥不进入源码 |
| `.env.example` | 无真实密钥的完整模板，可提交 | 新环境可以看到全部选项与默认值 |
| `components/config.py` | 单一 Config 类：读取、类型转换、默认值、模型连接参数 | 所有入口使用相同规则，避免多套配置对象 |
| CLI、Web、独立审查入口 | 加载配置并创建各自的客户端 | 启动方式不同，但供应商、认证与配置优先级一致 |

## 读取规则

Python 入口只加载本应用根目录的 `.env`，不会去所选代码项目或父目录寻找另一个 `.env`。配置优先级是 **进程环境变量 > 根 `.env` > 代码默认值**。这是对原 CLI/Web `override=True` 行为的统一调整；现在 `export` 的设置可以覆盖本地文件。

修改 `.env` 后重启对应的 CLI、后端或前端开发服务器。`Config()` 本身只解析已经加载的环境变量，不会反复覆盖运行进程的配置。

首次使用且尚无 `.env` 时：

```bash
cp -n .env.example .env
```

模板默认选择本地 Ollama；其他供应商的密钥留空，不影响本地启动。供应商拼写错误会在读取时明确报错；缺少地址、密钥或模型名会在创建客户端时提示。数值直接进行 int/float 转换，不再额外重复校验 URL 和数值范围。

## 模型供应商

`LLM_PROVIDER` 选择一次启动所使用的配置组。主 Agent、子 Agent、团队 Agent 和上下文压缩使用这组连接。独立审查进程可以另外选择。

| 值 | 配置前缀 | 默认接口 | 默认模型 |
| --- | --- | --- | --- |
| `ollama` | `OLLAMA_` | `http://127.0.0.1:11434` | `qwen2.5:3b` |
| `deepseek` | `DEEPSEEK_` | `https://api.deepseek.com/anthropic` | `deepseek-flash` |
| `kimi` | `KIMI_` | `https://api.moonshot.cn/anthropic` | `kimi-k3` |
| `anthropic` | `ANTHROPIC_` | `https://api.anthropic.com` | 必须填写账号可用的模型 ID |
| `custom` | `CUSTOM_` | 必须填写 | 必须填写 |

DeepSeek 和 Kimi 的地址、模型示例依据其 [Anthropic 接口文档](https://api-docs.deepseek.com/guides/anthropic_api/)与 [Messages API 文档](https://platform.kimi.com/docs/api/messages)。账号可用型号和接口支持仍以供应商为准。Kimi 示例为国内平台，海外账号需使用对应端点与密钥。

每组都有以下五项，`PREFIX` 替换成表中的前缀（不重复下划线）：

| 项 | 用途 | 为什么放在环境配置 |
| --- | --- | --- |
| `PREFIX_BASE_URL` | 模型服务地址 | 本地、云端、自建网关的地址不同 |
| `PREFIX_API_KEY` | 当前供应商的密钥；本地默认 `ollama` | 密钥随账号和环境变化，不能写进代码 |
| `PREFIX_MODEL` | 启动模型 | 更换型号不需要发布代码 |
| `PREFIX_AUTH_MODE` | `api_key` 或 `bearer` | 服务的认证头要求不同 |
| `PREFIX_MODELS` | 逗号分隔的可选模型 ID | 网页不再依赖可能过期的硬编码型号列表 |

当前所有组都使用 **Anthropic Messages 协议**。`custom` 只接入该协议的兼容服务；直接填 OpenAI Chat Completions 或 Responses 接口并不能工作，需另加协议适配层。

例如启用 DeepSeek，在已有 `.env` 中修改：

```dotenv
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=填写你的DeepSeek开放平台密钥
```

启用 Kimi 则修改：

```dotenv
LLM_PROVIDER=kimi
KIMI_API_KEY=填写你的Kimi开放平台密钥
```

地址、型号和认证方式已经在模板中分组给出。客户端显式使用所选组的密钥；Bearer 组另外设置 `Authorization`，避免旧 SDK 从通用环境变量中捡到另一家的密钥。不会为了缺失的 DeepSeek/Kimi 密钥回退到 Anthropic 密钥。

网页汇总各供应商在 `MODEL` 和 `MODELS` 中配置的型号，并按供应商区分同名模型；当前供应商的默认型号也会显示，Ollama 还会尝试查询本地列表。缺少地址、密钥或模型配置的项仍显示，但不可选择。选择其他供应商时会同步切换地址、密钥和客户端，无需重启；主任务或团队线程尚未结束时禁止切换。选择会在本次后端进程内保留，切换项目/会话后继续使用；重启后恢复 `LLM_PROVIDER` 的启动配置。修改 `.env` 文件本身仍需重启加载。

## 工作目录与运行控制

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `AGENT_WORKDIR` | 留空，使用启动目录 | 初始工作目录；网页仍可选择其他项目 |
| `AGENT_MODE` | `auto` | Web 后端初始模式；会话/请求中的显式模式优先，CLI 不使用此审批流程 |
| `TOKEN_THRESHOLD` | `100000` | 对话历史自动压缩阈值，应按所选模型的上下文容量调整 |
| `AGENT_MAX_ROUNDS` | `40` | 一次运行的模型轮数上限 |
| `AGENT_MAX_TOOL_CALLS` | `100` | 一次运行的工具调用上限 |
| `AGENT_MAX_SECONDS` | `600` | 一次运行的耗时预算，秒 |
| `AGENT_MAX_TOKENS` | `100000` | 一次运行按现有 usage 统计的 Token 预算，与压缩阈值不同 |
| `AGENT_MAX_REPAIR_ATTEMPTS` | `2` | 验证失败后的修复次数上限，允许 `0` |
| `AGENT_VERIFY_COMMAND` | 留空 | 可选验证命令，需适用于当前工作目录；留空不自动验证 |

这些数值影响成本、等待时间和自动执行范围，因此可以调整。配置层仅进行数值类型转换；运行预算沿用 `RunLimits` 自身的正数/非负数检查。运行预算的统计与取消边界保持原实现，不因此获得硬实时保证。

## Web 与开发服务

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `API_HOST` | `0.0.0.0` | 后端监听地址，沿用原行为；只允许本机访问可设 `127.0.0.1` |
| `API_PORT` | `8000` | 后端端口；启动脚本和 Vite 默认目标也使用它 |
| `FRONTEND_PORT` | `5173` | 前端开发端口，端口占用时失败而非悄悄换端口 |
| `API_PROXY_TARGET` | 留空，自动为 `http://127.0.0.1:<API_PORT>` | Vite 服务端代理目标；容器或分机开发时可覆盖 |
| `API_CORS_ORIGINS` | `*` | HTTP 和 Socket.IO 共用的跨域来源，可设逗号分隔列表 |
| `TOOL_APPROVAL_TIMEOUT` | `120` | Web Manual 模式等待工具审批的秒数 |

开发环境中 `/api` 与 `/socket.io` 都经过 Vite 同源代理，浏览器不再硬编码 `8000`。生产环境仍需在反向代理中转发这两个路径。前端开发服务器可以读取根配置来设置端口和代理，但不会把整个 `.env` 注入浏览器；不要为密钥添加 `VITE_` 前缀。

## 可选集成

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `GITHUB_MCP_TOKEN` | 空 | GitHub MCP 凭据；为空时关闭集成 |
| `GITHUB_MCP_TIMEOUT` | `20` | GitHub MCP 调用超时，秒 |
| `A2A_REVIEW_URL` | 空 | 独立审查服务地址；为空时不提交审查 |
| `A2A_REVIEW_TOKEN` | 空 | 主服务与审查服务的共享鉴权令牌，与模型密钥不同 |
| `A2A_REVIEW_TIMEOUT` | `120` | 主服务等待审查的超时，秒 |
| `A2A_REVIEW_PROVIDER` | 空 | 独立审查进程的供应商，留空继承 `LLM_PROVIDER` |
| `A2A_REVIEW_MODEL` | 空 | 独立审查模型，留空使用所选供应商的启动模型 |

独立审查服务仍使用命令行参数设置 `--host`、`--port`、`--data-dir`、`--public-url`、`--max-output-tokens` 和 `--timeout-seconds`。它是单独的进程，不与主 Web 服务的监听和预算设置混用。

## 统一配置入口

只需编辑根目录 `.env`；`.env.example` 是复制用的模板，不参与运行时加载。
`components/config.py` 负责读取配置，不需要手工修改。

模型统一使用 `LLM_PROVIDER` 和对应前缀的字段。未设置或留空时选择 Ollama。
不再自动识别旧 `MODEL_ID`、`ANTHROPIC_AUTH_TOKEN` 和 `LLM_AUTH_MODE`；旧配置请迁移为所选供应商的 `PREFIX_MODEL`、`PREFIX_API_KEY`、`PREFIX_AUTH_MODE`。自建代理使用 `LLM_PROVIDER=custom` 和 `CUSTOM_*`。

下列内容继续留在代码或原 CLI 中：

- `.agent/team`、`.agent/tasks` 等目录由工作目录派生，避免把相关路径配置成互相不一致的位置。
- 提示词、工具 schema、只读工具白名单、截断上限与协议字段属于程序行为，不作为环境开关。
- 评测的模型、预算、数据划分和输出位置继续走评测命令行，保持可复现，不自动加载交互程序的 `.env`。
- 每次运行的 `run_id`、会话状态、任务队列等运行数据不属于配置。
