# rwkv-harbor-scratch

把 RWKV 模型接入 [Harbor](https://github.com/harbor-framework/harbor) 评测框架，用 Terminal-Bench 这类
agent 实操题库测 RWKV 的 agent 能力。

不修改 Harbor 源码：在仓库外写了一个 RWKV 续写后端，挂到 Harbor 自带的 `terminus-2` agent 上。

- 接入方式、改动面、踩过的坑：[docs/integration-notes.md](docs/integration-notes.md)
- 跑分结果：[docs/results/](docs/results/README.md)（按题集分文件）
- 内置 harness 体量调查：[docs/harness-survey.md](docs/harness-survey.md)

## 目录

```
rwkv_harbor/            RWKV 接入代码
  llm.py                RwkvContinuationLLM：按 g1 chat 模板拼 prompt，调 /v1/batch/completions
  agent.py              RwkvTerminus：继承 terminus-2，只替换 LLM 后端
configs/                Harbor job 配置
  rwkv-continuation-hello-world.yaml   续写后端跑 hello-world（推荐路线）
  rwkv-tb2-sample.yaml                 续写后端跑 terminal-bench-sample@2.0（10 题）
  rwkv-tb2-sample-fast.yaml            同上，采样换 agent-fast（带轻度重复惩罚）
  rwkv-tb2-easy.yaml                   Terminal-Bench 2.0 的 4 道简单题
  rwkv-hello-world.yaml                对照：走服务端 chat 接口 + LiteLLM（不推荐，见说明文档）
  capture-harnesses.yaml               抓取各内置 harness 的首轮请求（配合 capture_proxy.py）
scripts/
  setup.sh              按锁定提交拉取 harbor / terminal-bench-2 / rwkv-tokenizer-go
  count_prompt_tokens.py  统计数据集首轮 prompt 的 RWKV token 数
  capture_proxy.py      假模型端点，记录 harness 发来的请求
  analyze_captures.py   统计各 harness 系统提示词 / 工具定义的 RWKV token 数
  summarize_run.py      按题统计一次跑分：轮数、think、撑满输出上限、跨轮复读、测试通过数
analysis/
  tb2-prompt-lengths.csv  terminal-bench 2.0 全部 89 题的 prompt 长度
  harness-prompt-sizes.csv  各内置 harness 首轮请求体量
docs/                   说明
  results/              跑分结果，按题集分文件
```

`harbor/`、`datasets/`、`third_party/`、`jobs/`、`captures/` 不入库，由 `scripts/setup.sh` 拉取或运行时生成。

## 上手

前置：`uv`、Docker（已启动）、`git`；可选 `go`（编译本地分词器）。

```bash
bash scripts/setup.sh

# 推理端（rwkv_lightning_cuda）地址，到 /v1 为止；有密码时另设 RWKV_API_KEY
export RWKV_API_BASE=http://<host>:8000/v1

# 冒烟：RWKV 跑 hello-world
PYTHONPATH=. uv run --project harbor harbor run -c configs/rwkv-continuation-hello-world.yaml

# 样例题库（10 题）
PYTHONPATH=. uv run --project harbor harbor run -c configs/rwkv-tb2-sample.yaml

# 简单题（4 题）
PYTHONPATH=. uv run --project harbor harbor run -c configs/rwkv-tb2-easy.yaml

# 浏览结果与轨迹
uv run --project harbor harbor view jobs
```

推理端地址只从环境变量读取，配置文件里不写地址（也可在配置的 `llm_kwargs.api_base` 临时覆盖）。
采样预设用 `llm_kwargs.sampling_preset` 选（默认 `agent-default`，值与 RWKV-Agent 一致）。
每轮调用的完整 prompt 与原始输出保存在 `jobs/<job>/<trial>/agent/rwkv-calls/`。

## 版本锁定

| 依赖 | 提交 |
|---|---|
| harbor-framework/harbor | `f3a5a923`（v0.25.0） |
| laude-institute/terminal-bench-2 | `69671fba`（harbor registry 中 terminal-bench@2.0 的锁定提交） |
| No-22-Github/rwkv-tokenizer-go | `5515f91e` |
| 推理端 | rwkv_lightning_cuda（albatross-1.3.0，API 1.3），模型 `rwkv7-g1k-7.2b-20260930-ctx25600` |
