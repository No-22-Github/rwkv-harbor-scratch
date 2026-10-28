# RWKV 接入 Harbor：接入方式、改动面与问题记录

> 2026-10-28。被测：`rwkv7-g1k-7.2b-20260930-ctx25600`，推理端 rwkv_lightning_cuda（albatross-1.3.0）。

## 1. 上游框架简介

**Harbor**（harbor-framework/harbor，v0.25.0）是 agent 评测框架：给模型一个 Docker 容器和一道任务，
模型自己在终端里一步步操作，结束后由任务自带的测试脚本判分（0~1）。

| 概念 | 含义 |
|---|---|
| Task | 一道题：`instruction.md` + `environment/`（镜像）+ `tests/test.sh`（判分） |
| Dataset | 一套题，如 terminal-bench@2.0（89 题）、swebench-verified（500 题） |
| Agent | 把题目交给模型、把模型输出变成命令的"做题框架" |
| Environment | 容器跑在哪：本机 Docker、Daytona、Modal 等 |
| Trial / Job | 一个 agent 做一道题 / 一批 trial |

Agent 分两类：
- **宿主机侧**（如 `terminus-2`）：LLM 调用在 Harbor 进程里，命令经 tmux 发进容器。模型端点只需宿主机可达。
- **容器内**（如 `claude-code`、`codex`、`mini-swe-agent`）：agent 装在容器里自己调模型，
  要从容器访问端点，且多依赖 Anthropic Messages / Responses 等特定协议。

`terminus-2` 的 LLM 层是可替换的：`BaseLLM` 只要求实现 `call()`、`get_model_context_limit()`、
`get_model_output_limit()`；仓库里的 `llms/tinker.py` 就是一个非 LiteLLM 后端的先例。

## 2. 接入方式与改动面

**Harbor 源码零改动。** 所有代码在本仓库 `rwkv_harbor/`，通过 job 配置的 `import_path` 加载。

| 文件 | 作用 |
|---|---|
| `rwkv_harbor/llm.py` | `RwkvContinuationLLM(BaseLLM)`：渲染 transcript、调用续写接口、计 token、处理停止/截断、落盘每轮调用 |
| `rwkv_harbor/agent.py` | `RwkvTerminus(Terminus2)`：覆写 `_init_llm` 换成上面的后端；跳过 LiteLLM 的凭据与连接解析 |

调用链：

```
harbor run ─▶ RwkvTerminus（= terminus-2 的全部逻辑：提示词、JSON 解析、tmux、确认完成、上下文压缩）
                 └─▶ RwkvContinuationLLM.call(prompt, message_history)
                        ├─ render_prompt：User/Assistant 块，g1 chat 模板
                        ├─ POST /v1/tokens/count   （prompt token 数，超上下文则抛 ContextLengthExceededError）
                        ├─ POST /v1/batch/completions  （纯续写、流式，服务端不套模板、不预填）
                        └─ 截掉命中的停止串；按 finish_reason 判断截断，抛 OutputLengthExceededError
```

prompt 渲染规则照搬 RWKV-Agent（`internal/inference/prompt.go`）：

```
User: <内容，块内空行折叠>

Assistant: <上一轮输出>

User: <终端输出>

Assistant:
```

采样用命名预设（`llm_kwargs.sampling_preset`），五个预设的数值与 RWKV-Agent `internal/samplingpreset` 一致；
默认 `agent-default`（`temperature 0.3, top_k 65536, top_p 0.5`，无重复惩罚）。
RWKV-Agent 跑分规程建议长多步任务用 `agent-fast`。

端点地址只从环境变量 `RWKV_API_BASE`（及可选 `RWKV_API_KEY`）读取，配置文件不含地址。

## 3. 两条路线的对比

| | 路线 A：服务端 chat 接口 + LiteLLM | 路线 B：纯续写接口 + 自写后端（采用） |
|---|---|---|
| 配置 | `configs/rwkv-hello-world.yaml` | `configs/rwkv-continuation-hello-world.yaml` |
| 代码 | 无 | `rwkv_harbor/` 约 250 行 |
| hello-world | 1.0 | 1.0 |
| 解析告警 | 5 次 | 1 次 |
| 最后确认轮 | 969 token | 178 token |
| prompt 可控性 | 服务端模板，看不到也改不了 | 逐字节可控、每轮落盘 |

## 4. 遇到的问题

### 4.1 默认停止 token 截断多行 JSON
cuda 后端默认 `stop_tokens: [0, 261, 24281]`，261 是 `\n\n`。terminus 要求模型输出多行 JSON，遇到空行就会被截断。
（RWKV-Agent `docs/evaluations/benchmark-protocol.md` §3 已记录。）

### 4.2 模型答完不发 EOS，续写下一轮对话
只用 `stop_tokens: [0]` 时，模型写完 JSON 后不停，接着编造 `User: …` 和后续多轮对话，直到 `max_tokens`。
首次跑 hello-world 每步约 1000 token、80 秒，180 秒内只走两步，超时得 0 分。
对应 RWKV-Agent state 横测报告中的"终答不停"。解决：把角色标签设为字符串停止串
（`\nUser:`、`\nSystem:`、`\nTool:`，与 RWKV-Agent `G1Protocol.Stops` 一致）。

### 4.3 传字符串 `stop` 后服务端不再按 EOS 停止（新发现）
`stop` 与 `stop_tokens` 互斥。一旦传了 `stop`，服务端就不再使用默认停止 token，模型输出的 EOS
被解码成文本 `<EOD>`，生成继续进行。复现：

```
POST /v1/batch/completions
{"contents":["User: Say hello in one short sentence.\n\nAssistant:"], "stop":["\nUser:"], ...}
→ " Hello!<EOD>User: 请用一句话概括《红楼梦》的主题。…"   finish_reason=length
```

解决：停止串里加上 `<EOD>`。**建议与后端维护者确认这是否为预期行为。**

### 4.4 chat 接口的 think 预填导致每轮多一个 `>`
`/v1/chat/completions` 的 `think_type: fast` 会在 prompt 末尾预填 `<think></think`（故意不带 `>`，
因为 RWKV 把 `>` 与后续文本合并分词）。模型输出以 `>` 开头，terminus 每轮报
"Extra text detected before JSON object" 并把告警回灌给模型。
RWKV-Agent 的格式消融还表明空 think 预填会改变模型行为。续写路线不预填，问题消失。

### 4.5 块内空行破坏对话结构
RWKV 用空行分隔对话轮次。terminus 的说明文字、终端输出（tmux pane 常带大段空行）都含连续空行，
若原样拼接，模型会把它们读成轮次边界。续写路线中按 RWKV-Agent `CleanChatText` 折叠。
走 chat 接口时服务端是否折叠无法确认。

### 4.6 停止串残留进对话历史
路线 A 中服务端 `stop` 命中的串保留在输出末尾，terminus 把含 `\n\nUser:` 的回复存入历史，下一轮再拼回 prompt。
续写路线在客户端截掉。

### 4.7 截断判断
chat 接口截断时也返回 `finish_reason: stop`；`/v1/batch/completions` 带 `stop` 时会如实返回 `length`。
后端额外按 completion token 数兜底判断。

### 4.8 thinking=off 时模型仍自发 think，并在 think 内复读
样例 10 题中 51% 的轮次以 `<think>` 开头；写满 `max_tokens` 的 26 轮里 25 轮是 think 内复读。
详见 [results/tb2-sample.md](results/tb2-sample.md)。试过 `sampling_preset: agent-fast`（轻度重复惩罚），无明显效果；需要 LoopGuard 这类截断。

### 4.9 网关切断超过约 90 秒的非流式请求（表现为 502）
跑分中频繁出现 502，起初以为是端点不稳定。排查发现推理端本身正常，是前面的网关会切断耗时超过约 90 秒的非流式请求：
实测 60 秒的请求正常，100 秒的被切断（空响应或 502），同样 100 秒的流式请求正常。
模型在 think 里复读写满 2048 token、多题并发、prompt 20k 时，单次请求就会超过 90 秒，所以 502 专挑复读的轮次出现。
解决：`RwkvContinuationLLM` 改用流式请求（`stream: true`，从最后一个分片取 `finish_reason` 与 `matched_stop`）；
另对 5xx 与连接错误指数退避重试 3 次（2s/4s/8s），4xx 直接抛出。改后重跑无 502。

### 4.10 部分题目判分环境已损坏
terminal-bench 2.0 的 qemu-alpine-ssh、qemu-startup 镜像基于 Debian 11（bullseye），判分脚本 `tests/test.sh`
运行时 `apt-get install curl` 得到 404，判分本身无法运行，与模型和本机环境无关。
原因：bullseye 的 LTS 已于 2026-08 结束，安装包从 `deb.debian.org` 移到 `archive.debian.org`，而题目仍指向旧源。
本机直接请求同一个包可复现：`deb.debian.org/.../curl_7.74.0-1.3+deb11u16_amd64.deb` 返回 404，
`archive.debian.org` 同路径返回 200。把源改到 archive 可以修好，但那就改了官方题目，因此本仓库不改，统计时剔除这两题。
用 oracle agent（执行题目附带的标准解法）复核：两题同样 0 分、同样 404。

### 4.11 容器内 tmux 偶发装不上
terminus 在容器里用 tmux 执行命令。ubuntu:24.04 的题目镜像不带 tmux，Harbor 在运行时用 apt 安装，超时 120 秒
（`tmux_session.py` `_TOOL_INSTALL_TIMEOUT_SEC`），失败后改为源码编译，仍失败就报 `Failed to start tmux session. Error: None`。
本机 x86 镜像走模拟、多题并发时容易超时，polyglot-c-py、overfull-hbox 各遇到一次，单独重跑即正常。与模型无关。

## 5. 上下文压力评估

用 RWKV 分词器（服务端 `/v1/tokens/count`，与 rwkv-tokenizer-go 逐题核对一致）统计 terminal-bench 2.0 全部 89 题：

| | 最短 | 中位数 | P90 | 最长 | 平均 |
|---|---|---|---|---|---|
| 题目描述 | 35 | 179 | 470 | 1302 | 250 |
| 首轮完整 prompt | 732 | 876 | 1167 | 1999 | 947 |

terminus 固定说明占 697 token。首轮 prompt 只占 25.6k 上下文约 4%，**压力来自多轮累积**：

- 每轮把全部历史拼回去；hello-world 每轮约 +150 token。
- 单轮终端输出上限 10000 字节（terminus 截掉中段），约 2500~3500 token；一次 `pip install` 或打印大文件就可能吃掉 3000。
- terminus 在剩余空间不足 8000 token 时主动压缩历史（另预留 2048 输出），约 15.5k 处就会触发；
  且它用 LiteLLM 的 tiktoken 估算，不是 RWKV 分词，触发时机有偏差。

明细：`analysis/tb2-prompt-lengths.csv`。

## 6. 已知限制与后续可做

- 7B 模型在 terminal-bench 上简单题、中等题均为 0 分，瓶颈在多步决策，见 [results/](results/README.md)。
- 上下文压缩阈值、token 估算未针对 RWKV 调整。
- 未接入 RWKV-Agent 的 LoopGuard（流式检测复读并提前截断），复读时会一直生成到 `max_tokens`。
- 未利用 rwkv_lightning 的 state cache（`/state/chat/completions`），每轮整段重新 prefill。
- 题目镜像为 x86，在 Apple Silicon 上走模拟，环境侧耗时偏高。

## 7. 参考

- Harbor：https://github.com/harbor-framework/harbor
- Terminal-Bench 2.0：https://github.com/laude-institute/terminal-bench-2
- rwkv_lightning_cuda API 文档：https://github.com/Alic-Li/rwkv_lightning_cuda/blob/main/rwkv_lightning_api_doc.md
- RWKV 分词器（Go）：https://github.com/No-22-Github/rwkv-tokenizer-go
- RWKV-Agent（本地项目）：g1 chat 模板、agent-wire-v1.0 格式契约、采样预设、跑分规程
