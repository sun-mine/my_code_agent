# 评测报告：offline-demo

- 任务清单哈希：`06013cc9290fa45efc791d30a7a9075ae4c1f549654037dc357248c810def276`（v1.0.0）
- 任务数：20（通过 20，评测或运行异常 0）
- 运行方式：离线预置补丁回放（offline_oracle）
- 策略配置：预置补丁回放（harness-oracle）
- 人工介入次数：0
- 评测链路验收通过率：20/20（100.0%）
- Agent 任务完成率：待测（未调用模型）
- Agent 耗时、工具调用和实际 Token 用量：待测（未调用模型）

> 此确定性回放只验证仓库初始化与外部评分链路，不代表 Agent 的任务完成能力。

| 任务 | 数据划分 | 结果 | 失败原因 |
|---|---|---|---|
| repair-001-clamp | 开发集 | 通过 |  |
| repair-002-chunks | 开发集 | 通过 |  |
| repair-003-dedupe | 开发集 | 通过 |  |
| repair-004-parse-bool | 开发集 | 通过 |  |
| repair-005-merge-counts | 开发集 | 通过 |  |
| repair-006-backoff | 开发集 | 通过 |  |
| repair-007-normalize-tags | 开发集 | 通过 |  |
| repair-008-moving-average | 开发集 | 通过 |  |
| repair-009-top-k | 开发集 | 通过 |  |
| repair-010-mask-email | 开发集 | 通过 |  |
| repair-011-parse-duration | 开发集 | 通过 |  |
| repair-012-flatten-one | 开发集 | 通过 |  |
| repair-013-page | 留出集 | 通过 |  |
| repair-014-group-by | 留出集 | 通过 |  |
| repair-015-encode-query | 留出集 | 通过 |  |
| repair-016-parse-csv-line | 留出集 | 通过 |  |
| repair-017-interleave | 留出集 | 通过 |  |
| repair-018-merge-intervals | 留出集 | 通过 |  |
| repair-019-rotate-right | 留出集 | 通过 |  |
| repair-020-compare-versions | 留出集 | 通过 |  |
