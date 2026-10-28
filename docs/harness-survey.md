# Harbor 内置 harness 体量调查

> 2026-10-28。目的：判断值不值得写一个"工具调用模拟层"，把依赖原生 tool call 的内置 harness 接到 RWKV 上。

## 方法

`scripts/capture_proxy.py` 起一个假模型端点（兼容 OpenAI Chat / OpenAI Responses / Anthropic Messages，流式与非流式），
每个 agent 的 `base_url` 指向 `http://host.docker.internal:8765/<agent>/v1`，在 hello-world 上跑一次，
记录它发给模型的第一个请求，统计 **系统提示词 / 工具定义 / 对话内容** 三部分的 RWKV token 数
（`scripts/analyze_captures.py`，本地 rwkv-tokenizer-go 计数）。

这是每道题**开局的固定成本**，之后每轮的终端输出、模型回复还要往上叠。参照：RWKV 上下文 25.6k，
terminal-bench 真题每轮约 +800~1000 token（见 [results/tb2-sample.md](results/tb2-sample.md)）。

配置：`configs/capture-harnesses.yaml`。原始请求存于 `captures/`（不入库：含各厂商 CLI 的完整系统提示词）。

## 结果（首轮请求，RWKV token）

| harness | 工具数 | 系统提示词 | 工具定义 | 对话 | **合计** | 占 25.6k |
|---|---|---|---|---|---|---|
| terminus-2 | 0（JSON 文本动作） | — | — | 721 | **721** | 3% |
| mini-swe-agent | 1（bash） | 12 | 65 | 778 | **855** | 3% |
| pi | 4 | 715 | 716 | 18 | **1449** | 6% |
| dspy-rlm | 0（DSPy 文本格式） | 926 | — | 631 | **1557** | 6% |
| goose | 8 | 858 | 1624 | 77 | **2559** | 10% |
| aider | 0（编辑格式） | 2570 | — | 34 | **2604** | 10% |
| code-puppy | 17 | 905 | 2826 | 16 | **3747** | 15% |
| deerflow | 11 | 2824 | 2869 | 113 | **5806** | 23% |
| openhands-sdk | 5 | 3522 | 4145 | 18 | **7685** | 30% |
| codex | 9 | 4319 | 4216 | 114 | **8649** | 34% |
| mcode | 19 | 4188 | 9291 | 1 | **13480** | 53% |
| copilot-cli | 20 | 6192 | 9191 | 47 | **15430** | 60% |
| qwen-coder | 10 | 7447 | 5029 | 3030 | **15506** | 61% |
| claude-code | 12 | 6078 | 11258 | 2556 | **19892** | 78% |
| kimi-code | 26 | 1906 | 19754 | 183 | **21843** | 85% |

工具定义按原始 JSON Schema 计；若中间层改写成 RWKV-Agent 的扁平占位符目录，可再压缩，
但系统提示词部分压不掉。

**不计入：**
- nemo-agent：默认配置是单次问答（44 token，无工具、无循环），不是做题 harness。
- opencode：只抓到"生成会话标题"的辅助请求，主请求未发出。
- 未能运行（安装或启动阶段失败，未到模型调用）：grok-build、muse-code（安装源网络不通）；
  openhands、trae-agent、vibe（上游安装包依赖错误）；fx、fx-dev（拒绝 http 端点 / 不认自定义 provider）；
  swe-agent（要求工作目录是 git 仓库）；hermes、strands、langgraph（需用户自带 agent 代码或配置）；
  kimi-cli、mimo（超时 / 接口不兼容）。
- 只连厂商后端、无法指定模型：acp、cline-cli、cortex-code、cursor-cli、devin、eve、fabric、junie、openclaw、rovodev-cli；
  只支持 Google 接口：gemini-cli、antigravity-cli、antigravity-sdk。

## 筛选

以"开局固定成本 ≤ 上下文 15%（约 3.8k）"为线：

| 类别 | harness | 接入方式 |
|---|---|---|
| 文本动作，无需中间层 | terminus-2、dspy-rlm | 已接 / 走续写后端即可 |
| 单一 bash 工具 | mini-swe-agent | 可切文本模式（不需中间层），或中间层模拟 1 个工具 |
| 需要中间层 | pi、goose、code-puppy | 模拟 tool call |
| 不适合 terminal-bench | aider | 面向改代码文件，不操作终端 |
| 太重，直接不测 | deerflow 及以下 8 个 | — |

**结论：真正"需要中间层才能测、又不太重"的只有 pi、goose、code-puppy 三个**，其中 code-puppy 已在线上。
主流 CLI（claude-code、codex、qwen-coder、copilot-cli、kimi-code）开局就吃掉 34%~85% 的上下文，
即使接通也没有余量做多轮任务。
