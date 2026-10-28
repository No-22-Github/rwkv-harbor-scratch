# Terminal-Bench 2.0 简单题（4 题）

Terminal-Bench 2.0 共 89 题，按题目自带标注只有 4 道简单题：cobol-modernization、fix-git、overfull-hbox、prove-plus-comm。
用来摸 RWKV 的能力下限。2026-10-28 跑。

配置 `configs/rwkv-tb2-easy.yaml`：`agent-default` 采样、流式后端、4 题并发、每题官方超时（overfull-hbox 360 秒，其余 900 秒）。
`max_output_tokens` 从 2048 降到 1024：样例集两轮中正常结束的 745 轮只有 13 轮超过 1024 token，撞上限的几乎都是 think 内复读。

**结论：4 题全部 0 分。每题都能过一部分测试点，但失败方式比中等题更直白：反复执行同一条命令、绕开题目真正要做的事、违反题目约束。**

## 结果

| 题目 | 得分 | 结束方式 | 轮数 | 最大上下文 | 跨轮复读 | 撞输出上限 | 生成耗时 | 测试通过 |
|---|---|---|---|---|---|---|---|---|
| cobol-modernization（上限 1024） | 0 | 超时 | 25 | 24.5k | 71% | 22 | 853s | 1/3 |
| cobol-modernization（上限 2048 补跑） | 0 | 超时 | 14 | 23.1k | 38% | 9 | 516s | 2/3 |
| fix-git | 0 | 超时 | 59 | 19.3k | 81% | 0 | 788s | 0/2 |
| overfull-hbox | 0 | 自报完成 | 70 | 19.6k | 3% | 1 | 381s | 2/4 |
| prove-plus-comm | 0 | 超时 | 54 | 10.7k | 0% | 0 | 489s | 1/4 |

- 全程无 502。
- overfull-hbox 第一次运行时容器里 tmux 没装上（见 [integration-notes](../integration-notes.md) 4.11），表中是单独补跑的结果。
- cobol-modernization 在 1024 上限下，撞上限的 22 轮大多是正常写 Python 脚本被截断，而不是复读，
  所以用 2048 补跑了一次。补跑多过了 1 个测试点，仍是 0 分。

## 每题怎么失败的

**fix-git**（找回切换分支后"丢失"的改动并合并到 master）：
59 轮里 52 轮执行同一条 `git push -u origin feature-new-changes`。push 失败后不换思路，一直重复到超时。

**prove-plus-comm**（补全 Coq 里加法交换律的证明并编译）：
始终没有去改证明文件。54 轮在 `coq -version`、`which apt-get`、`ls /usr/lib/coq/`、`apt-get install -y coq`
几条命令之间来回循环，各重复 12~13 次。

**cobol-modernization**（把 COBOL 程序用 Python 重写）：
读懂了 COBOL 程序，但整段脚本写在一轮 JSON 里，在 1024 上限下被截断。模型在 think 里写了
"分小块写以免超出 token 限制"，下一轮却仍输出同样的整段脚本，同一条 `cat > /app/program.py` 出现 21 次。
放宽到 2048 后脚本能写出来（2/3），但输出与 COBOL 版本不一致，最终超时。

**overfull-hbox**（只能用给定同义词替换 input.tex 中的词，让 LaTeX 编译无 overfull hbox 警告）：
编译成功，也自报完成，但改动了题目明令禁止修改的 main.tex / synonyms.txt，警告也没消除。

## 小结

- 简单题与中等题一样是 0 分，下限没摸到。
- 这 4 题的上下文压力都不大（prove-plus-comm 最长只有 10.7k），失败主要不在上下文长度，而在多步决策：
  命令失败后不换方法、忘掉目标、不守约束。
- 输出上限 1024 对一般轮次足够，但对"一次写完整个文件"的题会截断，cobol 就是这种情况。
