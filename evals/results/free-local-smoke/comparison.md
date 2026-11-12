# 免费本地模型小样本评测对比

这是一次真实模型小样本对照评测，使用两个项目自建的合成 Python 修复任务：一个开发集任务，以及冻结清单中的第一个留出集任务。它不是实际仓库评测，也不能用来估计模型或 Agent 的通用能力。较早的试运行未纳入统计。

按照冻结的计分规则，补丁只有在受保护的公开测试和外部注入的验收测试均通过，且受保护测试保持不变时，才会被接受。`agent_outcome` 和 `stop_reason` 描述 Agent 循环如何结束；它们与补丁是否被接受分别报告。

## 汇总结果

三种配置分别为：基础工具循环（`baseline`）、增加代码定位（`retrieval`）、增加失败反馈修复（`repair`）。

| profile（配置） | 接受的补丁 | Agent 正常结束 | Agent 延迟（合计） | 总延迟（合计） | 工具调用 | Agent 工具调用 | 修复尝试 | 输入 / 输出 / 总 token 数 | 人工干预 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 0/2 | 2/2 | 46.880 s | 51.701 s | 2 | 2 | 0 | 728 / 1,016 / 1,744 | 0 |
| retrieval | 0/2 | 2/2 | 73.042 s | 76.837 s | 5 | 5 | 0 | 1,342 / 1,456 / 2,798 | 0 |
| repair | 0/2 | 0/2 | 151.629 s | 156.676 s | 9 | 5 | 3 | 2,209 / 1,559 / 3,768 | 0 |

`tool calls` 包含 `repair` 配置中由控制器运行的验证命令。`agent tool calls` 仅包含模型请求的工具。这些 token 数是模型端点为本次运行报告的实际用量。

## 各任务证据

`dev` 为开发集，`heldout` 为留出集。`completed / end_turn` 表示循环正常结束，不能代表补丁验收通过；`incomplete / max_seconds` 表示运行因耗时预算而结束；`failed / verification_failed` 表示验证未通过。

| profile（配置） | 任务 | 数据划分 | 是否接受 | Agent 结果 / 停止原因 | 总延迟 | 工具调用 | token 数 |
|---|---|---|---:|---|---:|---:|---:|
| baseline | `repair-001-clamp` | dev | 否 | completed / end_turn | 35.528 s | 2 | 1,265 |
| baseline | `repair-013-page` | heldout | 否 | completed / end_turn | 16.173 s | 0 | 479 |
| retrieval | `repair-001-clamp` | dev | 否 | completed / end_turn | 44.768 s | 4 | 1,644 |
| retrieval | `repair-013-page` | heldout | 否 | completed / end_turn | 32.069 s | 1 | 1,154 |
| repair | `repair-001-clamp` | dev | 否 | incomplete / max_seconds | 124.776 s | 6 | 1,951 |
| repair | `repair-013-page` | heldout | 否 | failed / verification_failed | 31.900 s | 3 | 1,817 |

全部 6 个补丁均未通过冻结的公开测试和验收测试。受保护测试保持不变，且没有发生评测框架、运行时或网络错误。

日志显示了这个样本中的具体原因：

- `baseline` 把 clamp 实现写入了新建的 `clamp.py`，而不是现有目标文件；处理 page 任务时没有使用任何工具。
- `retrieval` 可以使用 `lookup_symbol`，但没有调用它。它猜测目标文件为 `clamp_fix.py` 和 `src/page.py`，其中后者并不存在。其 clamp 运行还直接调用了任务环境中不可用的 `pytest` 命令。
- `repair` 收到了有界的公开测试失败反馈。处理 clamp 时，它继续猜测 `clamp_fix.py`，其中包括被拒绝的工作区外路径，最终达到时间上限。处理 page 时，它用文字描述了正确的切片方法，却没有发起任何模型请求的工具调用，因此两次验证重试均失败。

在本次模型运行的两个任务中，`retrieval` 或 `repair` 都没有提高补丁接受率。这个 0/2 的样本规模过小，且题目是项目自建的合成任务，不能据此判断实际仓库能力，也不足以对这些 profile 设计或 3.1B 模型作出更广泛的结论，更不能声称能力有所提升。

尽管名义上的单任务时限为 60 秒，clamp 的 `repair` 运行仍记录了 119.997 秒的 Agent 耗时。这是评测所用源码快照中实际观察到的超时控制限制，因此不应把该行视为严格的 60 秒对比。源码中存在未提交的改动，仅凭 Git `HEAD` 不足以复现；[`source-hashes.json`](source-hashes.json) 记录了相关文件的内容哈希，用于核对版本。

## 复现记录

- 清单：版本 `1.0.0`，SHA-256 `06013cc9290fa45efc791d30a7a9075ae4c1f549654037dc357248c810def276`。
- 任务：`repair-001-clamp`（dev）和 `repair-013-page`（heldout）。
- 模型：`qwen2.5:3b`，摘要 `357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b`，GGUF `Q4_K_M`，32,768-token 上下文。
- 单任务限制：10 轮、20 次工具调用、60 秒、20,000 个 token，以及 2 次修复尝试。
- 传输方式：通过 SSH 转发至用户自有机器上 Ollama 的 Anthropic 兼容端点。未使用付费模型。

各 profile 目录包含精确的机器可读结果、生成的摘要和完整 stdout 日志：[`baseline`](baseline/)、[`retrieval`](retrieval/) 和 [`repair`](repair/)。[`metadata.json`](metadata.json) 记录了模型与运行配置，不包含凭据。
