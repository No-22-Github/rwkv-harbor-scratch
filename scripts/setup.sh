#!/usr/bin/env bash
# 按锁定提交拉取外部依赖并安装 harbor。在仓库根目录运行：bash scripts/setup.sh
set -euo pipefail

HARBOR_COMMIT=f3a5a923f6d722472c9e797af836ae4425fb215f
TB2_COMMIT=69671fbaac6d67a7ef0dfec016cc38a64ef7a77c  # harbor registry.json 里 terminal-bench@2.0 锁定的提交
TOKENIZER_COMMIT=5515f91ead5a0198c77967f4c40bf354336ed406

clone_at() {
  local url=$1 dir=$2 commit=$3
  if [ ! -d "$dir/.git" ]; then
    git clone -q "$url" "$dir"
  fi
  git -C "$dir" fetch -q origin
  git -C "$dir" checkout -q "$commit"
  echo "$dir @ $(git -C "$dir" rev-parse --short HEAD)"
}

clone_at https://github.com/harbor-framework/harbor.git harbor "$HARBOR_COMMIT"
clone_at https://github.com/laude-institute/terminal-bench-2.git datasets/terminal-bench-2 "$TB2_COMMIT"
clone_at https://github.com/No-22-Github/rwkv-tokenizer-go.git third_party/rwkv-tokenizer-go "$TOKENIZER_COMMIT"

(cd harbor && uv sync --all-groups)

if command -v go >/dev/null; then
  (cd third_party/rwkv-tokenizer-go && go build -o ../bin/rwkv-tokenizer ./cmd/rwkv-tokenizer)
fi
