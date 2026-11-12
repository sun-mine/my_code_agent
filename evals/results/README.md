# 评测记录

`offline-demo/` 是通过验收的离线评测演示。它没有调用模型，因此其 Agent 指标明确标记为待测；其中 100% 的预置补丁回放验收通过率也不是 Agent 得分。

已完成一次真实本地模型小样本评测：2 个项目自建的合成 Python 修复任务分别使用 `baseline`、`retrieval` 和 `repair` 三种 profile 运行，各 profile 的补丁接受率均为 0/2。该结果不是实际仓库模型评测，不能代表实际仓库能力，也不能据此声称能力有所提升；实际仓库模型评测仍待进行。

查看[中文对照报告与失败分析](free-local-smoke/comparison.md)或[离线演示报告](offline-demo/summary.md)。

