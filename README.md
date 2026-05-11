# Code Agent

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/React-18-61DAFB?logo=react" alt="React 18">
  <img src="https://img.shields.io/badge/Anthropic-Claude-orange?logo=anthropic" alt="Anthropic Claude">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License MIT">
</p>

> ClaudeCode 风格的智能体工作台 —— 多轮对话、工具调用、任务编排，一站式 AI 编程助手。

## ✨ 核心功能

| 左侧面板 | 右侧面板 |
|---------|---------|
| **流式多轮对话** — SocketIO 实时推送，支持连续对话与随时中断 | **文件树浏览** — 左侧实时文件树，点击即可查看和编辑文件 |
| **工具调用可视化** — 可折叠卡片展示每次工具调用的输入输出和耗时 | **文件内联编辑** — 弹窗编辑器支持代码编辑，带行号，保存前自动备份 |
| **任务看板** — 三列看板（待处理 / 进行中 / 已完成）实时同步 | **多 Agent 协作** — 显示团队成员角色与状态，支持子 Agent 分发任务 |
| **Todo 联动** — Agent 更新 Todo 后前端即时刷新，任务进度一目了然 | **工作目录切换** — 顶栏一键切换 Agent 工作目录，多项目无缝衔接 |
| **安全快照** — 一键创建 / 恢复文件系统快照，放心让 Agent 修改代码 | **底部状态栏** — 连接状态、Agent 状态、模型名称实时可见 |

## 🚀 快速开始

### 环境要求

- **Python** 3.10+（推荐 conda 环境）
- **Node.js** 18+
- **Anthropic API Key**（[获取 Token](https://console.anthropic.com/)）

### 1. 安装依赖

```bash
# 后端
conda activate code_agent          # 或使用 venv
pip install -r requirements.txt

# 前端
cd frontend && npm install
```

### 2. 配置环境变量

```bash
cp .env.example .env   # 如有示例文件
```

编辑 `.env`：

```env
ANTHROPIC_AUTH_TOKEN=sk-ant-...
MODEL_ID=claude-sonnet-4-20250514
# 可选：自定义 API 代理
# ANTHROPIC_BASE_URL=https://your-proxy.com
```

### 3. 启动服务

```bash
./start.sh
```

访问 <http://localhost:5173> 即可使用。

<details>
<summary>手动启动（双终端）</summary>

```bash
# 终端 1：后端 API
conda activate code_agent && python api_server.py

# 终端 2：前端开发服务器
cd frontend && npm run dev
```

</details>

## 📁 项目结构

```
code_agent/
├── api_server.py              # Flask + SocketIO 后端入口
├── agent.py                   # CLI 模式入口
├── start.sh                   # 一键启动脚本
├── requirements.txt           # Python 依赖
├── core/
│   ├── loop.py                # Agent 主循环（含事件回调钩子）
│   └── subagent.py            # 子 Agent 执行器
├── components/
│   ├── config.py              # 配置管理
│   ├── compactor.py           # 上下文压缩
│   ├── memory_manager.py      # 记忆管理
│   ├── safety_manager.py      # 安全快照
│   ├── todo_manager.py        # Todo 管理
│   ├── task_manager.py        # 任务管理
│   ├── team_manager.py        # 多 Agent 协作
│   ├── message_bus.py         # 消息总线
│   ├── background_manager.py  # 后台任务管理
│   ├── error_recovery.py      # 错误恢复
│   └── skill_loader.py        # Skill 加载器
├── tools/
│   ├── base.py                # 工具基类与注册
│   ├── memory.py              # 记忆工具
│   ├── safety.py              # 安全工具
│   ├── background.py          # 后台执行工具
│   ├── task_board.py          # 任务看板工具
│   ├── todo.py                # Todo 工具
│   ├── team.py                # 团队工具
│   ├── skill.py               # Skill 工具
│   ├── compress.py            # 压缩工具
│   ├── protocols.py           # 协议工具
│   └── worktree.py            # Worktree 工具
├── frontend/
│   └── src/
│       ├── App.tsx            # 主应用骨架（三栏布局）
│       ├── hooks/             # SocketIO / 聊天状态 Hooks
│       ├── components/        # 聊天气泡、工具时间线、面板组件
│       ├── lib/               # API 客户端、类型定义、工具函数
│       └── assets/            # 静态资源
└── .env                       # 环境变量（已 Git 忽略）
```

## 🔌 API 参考

### RESTful 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/status` | Agent 运行状态 |
| `POST` | `/api/chat` | 发送消息（异步，SocketIO 推送响应） |
| `POST` | `/api/chat/interrupt` | 中断当前执行 |
| `GET` | `/api/history` | 获取对话历史 |
| `DELETE` | `/api/history` | 清空对话历史 |
| `GET` | `/api/tasks` | 获取任务列表 |
| `POST` | `/api/tasks` | 创建新任务 |
| `GET` | `/api/todos` | 获取 Todo 列表 |
| `GET` | `/api/team` | 获取团队成员状态 |
| `GET` | `/api/memory/stats` | 记忆统计信息 |
| `POST` | `/api/memory/search` | 搜索记忆内容 |
| `GET` | `/api/safety/checkpoints` | 快照列表 |
| `POST` | `/api/safety/checkpoint` | 创建文件快照 |
| `POST` | `/api/safety/restore` | 恢复文件快照 |
| `GET` | `/api/files/tree` | 获取文件树 |
| `GET` | `/api/files/content` | 获取文件内容 |

### SocketIO 事件

| 事件 | 方向 | 说明 |
|------|------|------|
| `agent_event` | Server → Client | 所有 Agent 结构化事件推送 |
| `send_message` | Client → Server | 发送对话消息 |
| `interrupt` | Client → Server | 中断 Agent 执行 |

**`agent_event` 类型：** `run_start` · `run_end` · `chunk` · `tool_start` · `tool_result` · `agent_state` · `interrupted` · `error`

## 🛠️ 技术栈

| 层级 | 技术 |
|------|------|
| AI 模型 | Anthropic Claude API |
| 后端框架 | Flask + Flask-SocketIO |
| 实时通信 | Socket.IO (WebSocket) |
| 前端框架 | React 18 + TypeScript |
| 构建工具 | Vite |
| 状态管理 | React Hooks + SocketIO 事件驱动 |

## 🗺️ 后续优化方向

### 短期（v0.2）

- [ ] **上下文窗口优化** — 智能裁剪历史消息，提升长对话下的响应速度与成本控制
- [ ] **工具执行沙箱** — 文件操作与命令执行引入审批流与回滚机制
- [ ] **对话分支** — 支持从任意消息节点分叉出新对话，多方案并行探索
- [ ] **Markdown 渲染增强** — 代码块语法高亮、LaTeX 公式、Mermaid 图表支持

### 中期（v0.3）

- [ ] **多模型适配** — 支持 OpenAI / 本地模型（Ollama）作为后端，降低使用门槛
- [ ] **插件系统** — 允许用户自定义工具与 Skill，通过 pip 安装扩展
- [ ] **会话持久化** — SQLite / PostgreSQL 存储对话历史，支持跨设备同步
- [ ] **权限分级** — 只读模式 / 审批模式 / 自动模式，适配不同信任等级

### 长期（v1.0）

- [ ] **VS Code 插件** — 深度集成编辑器，侧边栏内嵌 Agent 面板
- [ ] **多模态输入** — 支持截图、PDF、手绘草图作为对话上下文
- [ ] **团队协作空间** — 多人共享 Agent 会话，实时协同编程
- [ ] **自动化流水线** — Agent 监听 Git 事件，自动 Code Review / 生成 PR 描述

## 📄 License

MIT © [myh-1302](https://github.com/myh-1302)
