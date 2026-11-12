# 检索 A/B 评测复现说明

本目录记录当前项目的源码定位对照，以及多文件合成修复验收。定位与修复分开报告，不能把合成题通过率当作真实仓库修复能力。

- A：Agentic Search，使用文件查找、关键词搜索、Python AST 定位、按行读取。
- B：同一循环和预算，额外提供 BM25＋Embedding＋RRF 工具。没有调用该工具的运行仍保留。
- 生成模型为主机已安装的 `qwen2.5:3b`，向量模型为 `bge-m3:latest`；完整 digest、版本及预算见 `protocol.json`。
- 生成服务经 SSH 转发至 `127.0.0.1:11435`；没有下载模型，没有使用收费 API。

## 证据文件

- `comparison.md`：中文对照报告。
- `results.jsonl`：逐次运行结果，包括真实生成 Token、工具事件、独立测试证据。
- `runs/`：独立任务结果、日志及监督器阶段记录。
- `warmups/`：每次运行前相同的生成模型预热记录，不计入任务耗时。
- `protocol.json`：预注册任务数量、模型、预算、源码/实现/索引内容哈希及冷索引成本。
- `source-snapshot.json`：模型实际可见的源码快照，不依赖未提交文件是否存在于 Git HEAD。
- `tasks.json`、`repair-fixtures.json`：评测器持有的题目、标注和验收材料，不放入 Agent 工作区。
- `index.json.gz`：仅基于源码快照构建的真实向量索引。

## 运行

运行环境为 Python 3.12，使用项目已有的 Anthropic 类型和 pytest；未增加第三方依赖。模型请求由原生 Ollama 接口执行，Agent 工具循环复用项目的 `AgentLoop`。

建立模型主机的 SSH 本地转发后，在仓库根目录执行。准备新实验必须选择新的输出目录：

```bash
python -m evals.search_ab --output evals/results/my-search-ab --stage prepare --live
python -m evals.search_ab --output evals/results/my-search-ab --stage run --live
python -m evals.search_ab --output evals/results/my-search-ab --stage report
```

`prepare` 会真实计算语料向量，`run` 会真实调用生成及查询向量模型，二者都要求显式 `--live`。`report` 不调用模型。运行前会核对源码、任务、索引、评测实现和模型 digest；变化时拒绝混用结果。

要复用本次冻结源码与索引重跑，将 `protocol.json`、`source-snapshot.json`、`tasks.json`、`repair-fixtures.json`、`index.json.gz` 复制到新的空目录，再对新目录执行 `run`。这要求评测实现、项目运行依赖源码及模型身份与冻结记录一致。

8GB 模型主机采用串行推理。B 组每次查询向量的加载及执行时间计入任务耗时；语料向量冷构建成本单列。测试执行是进程隔离，不声明为安全沙箱。
