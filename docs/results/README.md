# 跑分结果

被测：`rwkv7-g1k-7.2b-20260930-ctx25600`（rwkv_lightning_cuda，W7900），agent：`RwkvTerminus`（terminus-2 + 续写后端），
超时倍率 1.0（官方口径），本机 Docker（Apple Silicon，x86 镜像模拟）。均为 2026-10-28。

## 总览

| 题集 | 难度 | 题数 | 得分 | 详情 |
|---|---|---|---|---|
| hello-world | — | 1 | 1 / 1 | 见下 |
| Terminal-Bench 2.0 简单题 | 简单 | 4 | 0 / 4 | [tb2-easy.md](tb2-easy.md) |
| terminal-bench-sample@2.0 | 中等 8、困难 2 | 10（有效 8） | 0 / 8（两种采样均为 0） | [tb2-sample.md](tb2-sample.md) |

有效题数剔除了判分环境损坏的 qemu-alpine-ssh、qemu-startup（标准解法同样 0 分）。

## hello-world

| 路线 | 得分 | 轮数 | 总耗时 |
|---|---|---|---|
| chat 接口 + LiteLLM（修停止串前） | 0.0（AgentTimeoutError） | 2 | 3m48s |
| chat 接口 + LiteLLM（修停止串后） | 1.0 | 5 | 1m19s |
| 续写后端 | 1.0 | 5 | 1m16s |

## 结论

1. **接入可行。** Harbor 源码零改动，RWKV 通过续写接口接到 terminus-2 上，hello-world 满分；改流式请求后链路稳定。
2. **Terminal-Bench 2.0 上，简单题和中等题都是 0 分。** 大多数题能通过一部分测试点，但完不成整题。
3. **失败原因在模型的多步决策，不在接入：**
   - 自发进入 `<think>` 并在其中复读，写满输出上限；
   - 命令失败后照抄上一轮或在几条命令间打转（fix-git 同一条命令执行 52 次）；
   - 偏离目标或违反题目约束（prove-plus-comm 从未改证明文件，overfull-hbox 改了禁止修改的文件）。
   简单题上下文最长只有 10k~24k，说明问题不只是上下文长度。
4. **调采样参数无效。** `agent-fast` 轻度重复惩罚只作用于本轮生成，压不住长循环，也管不到跨轮照抄。
5. 若要继续，需要在 agent 层加复读截断（如 RWKV-Agent 的 LoopGuard）和"命令重复/失败时换方法"的提示。
