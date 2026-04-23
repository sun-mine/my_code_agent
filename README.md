# Code Agent

基于 **LangGraph** 构建的自主 AI 编程智能体 (Autonomous AI Programming Agent)。
它可以理解需求、规划任务，并利用终端和文件系统工具在本地工作区自主编写、测试和修改代码。

## 🌟 核心特性 (Core Features)

- **🧩 状态与工作流管理**: 基于 LangGraph (`src/core`) 构建的图工作流，支持复杂的代码规划、生成、验证及循环重试逻辑。
- **🧠 灵活的模型支持**: 通过 `models/factory.py` 支持多种大语言模型（兼容 OpenAI API，可接入 DeepSeek / GLM 等）。
- **🛠️ 本地工具层**: 提供文件系统读写 (`filesys.py`) 和 终端命令执行 (`terminal.py`) 能力，直接操作本地代码环境。
- **📚 记忆与技能库**: 支持上下文记忆与可扩展的技能库 (`memory/`, `skills/`)。
- **🔒 安全沙箱 (规划中)**: `security/` 模块保障本地环境的命令与操作安全。

## 📂 目录结构 (Directory Structure)

```text
code_agent/
├── pyproject.toml        # 项目依赖配置 (基于 Poetry)
├── .env                  # 环境变量配置 (需自行创建)
├── src/
│   ├── main.py           # 项目入口文件
│   ├── core/             # 核心工作流引擎 (Graph, State)
│   ├── models/           # LLM 模型工厂和配置
│   ├── tools/            # Agent 可用的工具箱 (终端操作、文件系统)
│   ├── memory/           # 记忆管理 (短期/长期上下文)
│   ├── skills/           # Agent 可复用的技能库
│   ├── ui/               # 用户界面交互模块
│   └── security/         # 操作权限与安全控制
└── tests/                # 单元测试与集成测试
```

## 🚀 快速开始 (Quick Start)

### 1. 环境要求
- **Python:** 3.10 及以上 (< 3.13)
- **依赖管理工具:** [Poetry](https://python-poetry.org/)

### 2. 安装依赖
克隆项目后，在根目录下执行：
```bash
poetry install
```

### 3. 配置环境变量
在项目根目录根据需要调整或创建 `.env` 文件，配置你的大模型 API 凭证：
```env
OPENAI_API_KEY=your_api_key_here
OPENAI_API_BASE=https://api.your-provider.com/v1 # 可选
```

### 4. 运行 Agent
通过 Poetry 启动主程序入口：
```bash
poetry run python src/main.py
```

## 🤝 参与贡献
欢迎提交 Issue 和 Pull Request，共同完善 Code Agent。

## 📄 开源协议
[MIT License](./LICENSE) (或在后续补充)
