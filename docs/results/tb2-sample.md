# terminal-bench-sample@2.0（10 题）

Terminal-Bench 2.0 官方抽样的 10 道题：按题目自带标注，中等 8 道、困难 2 道（configure-git-webserver、
fix-code-vulnerability），没有简单题。2026-10-28 跑了两种采样，被推理端 502 打断的题改流式后端后重跑。

**结论：两种采样都是 0 分；失败在模型（think 内复读、跨轮照抄），重复惩罚看不出效果。**

## 第一轮：`agent-default` 采样

配置 `configs/rwkv-tb2-sample.yaml`：JSON 格式、`agent-default` 采样、`max_output_tokens 2048`、并发 3、每题 900 秒（官方）。
总耗时 1 小时。

**得分 0 / 10。**

| 题目 | 得分 | 结束方式 | 轮数 | 最大上下文 | 压缩次数 | 跨轮复读 | 撑满 2048 | 生成耗时 | 测试通过 |
|---|---|---|---|---|---|---|---|---|---|
| build-cython-ext | 0 | 超时 | 70 | 21.3k | 4 | 22% | 0 | 672s | 2/11 |
| chess-best-move | 0 | 超时 | 110 | 18.6k | 1 | 2% | 0 | 464s | 0/1 |
| configure-git-webserver | 0 | 超时 | 18 | 17.3k | 0 | 53% | 0 | 211s | 0/1 |
| fix-code-vulnerability | 0 | 超时 | 7 | 10.9k | 0 | 33% | 5 | 256s | 2/6 |
| log-summary-date-ranges | 0 | 超时 | 7 | 15.1k | 0 | 0% | 4 | 247s | 0/2 |
| polyglot-c-py | — | 推理端 502 | 1 | 0.8k | 0 | — | 0 | 31s | — |
| qemu-alpine-ssh | 0 | 超时 | 20 | 9.1k | 0 | 84% | 0 | 319s | 判分环境损坏 |
| qemu-startup | 0 | 自报完成 | 19 | 4.7k | 0 | 61% | 0 | 143s | 判分环境损坏 |
| regex-log | 0 | 超时 | 18 | 23.0k | 1 | 65% | 17 | 833s | 0/1 |
| sqlite-with-gcov | 0 | 超时 | 43 | 21.3k | 3 | 67% | 0 | 643s | 2/3 |

- "跨轮复读"：本轮原始输出与上一轮逐字相同的比例。
- "撑满 2048"：没有命中任何停止串、写到输出上限的轮数。
- "测试通过"：判分脚本里 pytest 用例通过数；terminal-bench 是全部通过才得 1 分。
- 压缩次数按 trial.log 中 `Proactively summarizing` 计；统计脚本 `scripts/summarize_run.py`。
- 这一轮推理端也出现过 502：fix-code-vulnerability、log-summary-date-ranges 各 5 次（前几轮即发生），
  build-cython-ext、regex-log 各 1 次，这几题的轮数因此偏少。它们的有效结果见下方"重跑"。

### 失败原因

**1. 模型自发进入 `<think>` 并在其中复读（单轮内循环）。**
prompt 不预填任何 think（thinking=off），但 313 轮里有 159 轮（51%）模型自己以 `<think>` 开头。
26 轮写到 2048 上限，其中 25 轮都是 think 里复读，例如一行 `We can use grep -oE 'ERROR|WARNING|INFO' and then wc -l.`
重复到底，或把"The code uses `args.xxx` without validation"枚举到第 150 条。
这 26 轮占了全部生成量的 39%（53k / 135k token），terminus 还会回一条"输出超长"的报错，白白多一轮。
regex-log 18 轮里 17 轮如此。

**2. 跨轮复读，跳不出失败路径。**
命令失败后，下一轮把同样的分析和同样的命令逐字再输出一遍。qemu-alpine-ssh 84%、sqlite-with-gcov 67%、
regex-log 65% 的轮次与上一轮完全相同。服务端的重复惩罚只统计本轮生成的 token（`src/utils/sampling.cu`），
对"抄上一轮"无效。

**3. 上下文压缩触发频繁，且压缩本身会失败。**
5 题上下文涨到 17k~23k；build-cython-ext 压缩 4 次、sqlite-with-gcov 3 次。
terminus 的压缩是让模型自己写总结，总结请求同样会在 think 里复读写满 2048，
日志里出现 `Even fallback chat failed: Response hit max_tokens=2048`。

**4. 环境与基础设施（与模型无关）。**
- qemu-alpine-ssh、qemu-startup：判分脚本运行时用 apt 安装 curl 得到 404。Debian 11 的 LTS 已结束，
  安装包从 `deb.debian.org` 移到了 `archive.debian.org`（本机直接请求同一个包，前者 404、后者 200）。
  判分脚本本身跑不起来，任何答案都是 0 分；与模型、与本机 x86 转译均无关。**统计时剔除，有效样本 8 题。**
- polyglot-c-py：推理端网关第一轮返回 502，后端没有重试，整题直接失败。已在 `llm.py` 加入 5xx/连接错误重试。
- **oracle 复核**：用 Harbor 自带的 oracle agent（直接执行题目附带的标准解法）跑这两题，同样 0 分、同样 apt 404，
  确认判分环境本身损坏。

**耗时分布**：生成耗时只占每题 900 秒中的 15%~90%，其余在等命令执行和 x86 镜像模拟。
chess-best-move 110 轮只用了 464 秒生成。

### 可以改的地方

| 问题 | 改法 | 依据 |
|---|---|---|
| think 内复读 | 换 `agent-fast`（presence 0.5 / frequency 0.1 / decay 0.996） | RWKV-Agent 跑分规程：长多步任务用 agent-fast，"轻度惩罚抑制循环"；惩罚正好作用于单轮内 |
| think 内复读 | 接 RWKV-Agent 的 LoopGuard：流式检测复读、提前截断 | 其 150 次 run 校准：抓住 208 次撑满中的 184 次，省 74% token |
| 跨轮复读 | agent 层检测"与上一轮相同"，追加提示要求换方法 | 重复惩罚管不到 |
| 502 | 已加重试 | — |
| 判分环境损坏 | 两道 qemu 题从统计中剔除，有效样本 8 题 | oracle 标准解法同样 0 分、同样 404 |


## 第二轮：`agent-fast` 采样

配置 `configs/rwkv-tb2-sample-fast.yaml`：与上一轮只差 `sampling_preset: agent-fast`
（presence 0.5 / frequency 0.1 / decay 0.996）。总耗时 58 分钟。

**得分 0 / 10。这一轮 5 题被 502 中断，这 5 题的有效结果见下方"重跑"。**

| 题目 | 得分 | 结束方式 | 轮数 | 最大上下文 | 压缩次数 | 跨轮复读 | 撑满 2048 | 生成耗时 | 测试通过 | 502 |
|---|---|---|---|---|---|---|---|---|---|---|
| build-cython-ext | 0 | 超时 | 13 | 16.4k | 0 | 0% | 3 | 344s | 2/11 | 5 次 |
| chess-best-move | — | 推理端 502 | 56 | 20.7k | 3 | 69% | 1 | 534s | — | 5 次 |
| configure-git-webserver | — | 推理端 502 | 21 | 19.0k | 0 | 65% | 0 | 200s | — | 4 次 |
| fix-code-vulnerability | 0 | 超时 | 3 | 2.9k | 0 | 0% | 0 | 139s | 4/6 | 1 次（第 3 轮后卡死） |
| log-summary-date-ranges | 0 | 超时 | 1 | 1.1k | 0 | 0% | 0 | 37s | 0/2 | 1 次（第 1 轮后卡死） |
| polyglot-c-py | 0 | 超时 | 20 | 10.7k | 0 | 63% | 0 | 883s | 0/1 | — |
| qemu-alpine-ssh | 0 | 超时 | 21 | 6.0k | 0 | 0% | 0 | 180s | 判分环境损坏 | — |
| qemu-startup | 0 | 自报完成 | 26 | 8.9k | 0 | 52% | 0 | 211s | 判分环境损坏 | — |
| regex-log | 0 | 超时 | 20 | 23.0k | 2 | 58% | 18 | 886s | 0/1 | — |
| sqlite-with-gcov | 0 | 超时 | 31 | 23.3k | 4 | 23% | 4 | 638s | 0/3 | — |

后端的重试（共约 14 秒）没能扛过；原因是网关切断长请求，见下方"重跑"。chess-best-move、configure-git-webserver 直接以 502 异常结束，
fix-code-vulnerability、log-summary-date-ranges 在 502 后不再有任何调用，一直挂到 900 秒超时。

## 重跑：流式后端

两轮里被 502 打断的题，排查后发现推理端本身没坏：**网关会切断耗时超过约 90 秒的非流式请求**
（实测 60 秒的请求正常返回，100 秒的被切断；同样 100 秒的流式请求正常）。模型在 think 里复读、写满 2048 token，
又有 3 题并发、prompt 长达 20k 时，单次请求就会超过 90 秒。所以 502 与复读是绑在一起出现的。

`rwkv_harbor/llm.py` 改为流式请求后，两轮受影响的题用同一配置各重跑一次，全程没有再出现 502。

| 题目 | 采样 | 得分 | 轮数 | 最大上下文 | 跨轮复读 | 撑满 2048 | 生成耗时 | 测试通过 |
|---|---|---|---|---|---|---|---|---|
| build-cython-ext | agent-default | 0 | 25 | 17.8k | 0% | 0 | 491s | 2/11 |
| fix-code-vulnerability | agent-default | 0 | 23 | 21.8k | 59% | 3 | 875s | 2/6 |
| log-summary-date-ranges | agent-default | 0 | 8 | 20.0k | 0% | 1 | 890s | 1/2 |
| regex-log | agent-default | 0 | 21 | 22.6k | 70% | **21** | 890s | 0/1 |
| polyglot-c-py | agent-default | — | 0 | — | — | — | — | 容器内 tmux 启动失败，未补跑 |
| build-cython-ext | agent-fast | 0 | 57 | 23.1k | 32% | 2 | 728s | 2/11 |
| chess-best-move | agent-fast | 0 | 126 | 21.6k | 16% | 0 | 519s | 0/1 |
| configure-git-webserver | agent-fast | 0 | 26 | 18.4k | 72% | 0 | 188s | 0/1 |
| fix-code-vulnerability | agent-fast | 0 | 9 | 11.3k | 0% | 4 | 717s | 4/6 |
| log-summary-date-ranges | agent-fast | 0 | 9 | 19.4k | 0% | 0 | 801s | 1/2 |

所有题都是跑满 900 秒超时。regex-log 21 轮全部在 think 里复读写满 2048。

## 汇总（剔除判分环境损坏的两道 qemu 题）

每题取未受 502 影响的那一次结果。

| 题目 | agent-default | agent-fast |
|---|---|---|
| build-cython-ext | 0（2/11） | 0（2/11） |
| chess-best-move | 0（0/1） | 0（0/1） |
| configure-git-webserver | 0（0/1） | 0（0/1） |
| fix-code-vulnerability | 0（2/6） | 0（4/6） |
| log-summary-date-ranges | 0（1/2） | 0（1/2） |
| polyglot-c-py | 无有效结果 | 0（0/1） |
| regex-log | 0（0/1） | 0（0/1） |
| sqlite-with-gcov | 0（2/3） | 0（0/3） |
| **合计** | **0 / 7** | **0 / 8** |

括号内为 pytest 用例通过数；terminal-bench 要求全部通过才得 1 分。

## 小结

1. 排除基础设施问题后，失败模式一致：
   - 自发进入 `<think>` 并在其中复读，写满输出上限（regex-log 每一轮都是）；
   - 命令失败后下一轮照抄上一轮，跳不出失败路径；
   - 对话增长到 17k~23k 后触发上下文压缩，压缩本身也会因复读失败。
   部分题能通过一部分测试点（fix-code 4/6、sqlite 2/3），说明模型能做对局部步骤，但完不成整题。
2. `agent-fast`（轻度重复惩罚）看不出效果。服务端惩罚只作用于本轮生成，压不住长循环，也管不到跨轮照抄；
   自发 think 每题几乎是全有或全无，惩罚作用不到每轮第一个 token。
3. 基础设施：qemu 两题判分环境损坏（oracle 同样 0 分）；网关切断超 90 秒的非流式请求（已改流式）。
