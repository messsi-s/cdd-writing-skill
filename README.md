# cdd-writing — a DSH skill for CANdela CDD editing

[English](#english) | [中文](#中文)

---

<a id="english"></a>
## English

### Overview

A [DeepSeek Harness](https://github.com/deepseek-ai) skill that teaches the agent to inspect and correct
CANdela CDD (`.cdd`) diagnostic descriptions so that CANoe / CANoe.DiVa generate the diagnostic tests that
match the ECU and the customer's diagnostic questionnaire.

### What it covers

- **DID alignment with the questionnaire** — extract the `ECU是否支持`/R/W matrix from OEM sheets
  (`03.1.System DID`, `03.2.ECU DID`, `4_1_Read DID`, `4_2_Write DID`, …), find DIDs that are supported but
  missing, and add them without breaking the XML (DID pool + variant `DIDREF` + `Identification` instance).
- **DID read/write modes** — which DIDs have a `Read` (0x22) / `Write` (0x2E) service and in which sessions
  (default `0x01`, programming `0x02`, extended `0x03`, supplier `0x60`) and security states.
- **State model (`mayBeExec` and `trans`)** — how DiVa derives test selection and precondition sequences from
  the CDD, how to encode session jumps and relocks, how to add a new `0x27` seed/key level, and how to give
  writable DIDs a reachable unlock precondition.
- **Unsupported services** — removing `0x28` subfunctions or whole DIDs that the ECU rejects, including the
  variant `DIAGINSTREF`s that must go with them.
- **DTC lists** — comparing mandatory DTCs from a questionnaire with the CDD `RECORD` entries
  (`v = DTC << 8 | failure type byte`).
- **DiVa round trip** — project files (`*.diva` / `*.conf`), synchronize, regenerate, testing aspects such as
  `TestTransportLayer` / `RunsOnCan`, service exclusions, and reading the report to separate "CDD is wrong"
  from "ECU is wrong".

### Install

Copy or clone this directory into a DSH skill directory, named `cdd-writing`:

| Scope | Path |
|---|---|
| user | `~/.dsh/skills/cdd-writing/` |
| project | `<project>/.dsh/skills/cdd-writing/` |

The skill is discovered on the next session (or immediately, depending on the Harness version). Verify with
`/cdd-writing` or by asking the agent for its available skills.

```bash
git clone https://github.com/messsi-s/cdd-writing-skill.git ~/.dsh/skills/cdd-writing
```

### Use

Ask the agent for a CDD task, or invoke the skill explicitly. The bundled CLI can also be used directly:

```bash
python scripts/cdd_tool.py inspect    ECU.cdd          # state groups + DID matrix
python scripts/cdd_tool.py inspect    ECU.cdd --json   # machine-readable
python scripts/cdd_tool.py dtc        ECU.cdd
python scripts/cdd_tool.py state-model ECU.cdd         # sessions/security + reachability
python scripts/cdd_tool.py add-did ECU.cdd --did 0xF0F0 --clone 0xF160 \
        --name "F0F0_当前运行分区信息" --qual F0F0
python scripts/cdd_tool.py set-states ECU.cdd --did 0xF190 --qual Write --states 3,6
python scripts/cdd_tool.py set-trans  ECU.cdd --inst ProgrammingSession --qual Start \
        --trans "(2,2,3,2,6,5,7,5,8,5)"
python scripts/cdd_tool.py remove-service ECU.cdd --did 0xF187 --qual Write
python scripts/cdd_tool.py remove-instance ECU.cdd --did 0x3012
python scripts/cdd_tool.py enable-tp  D:\path\to\diva\project.conf --state on
python scripts/cdd_tool.py validate   ECU.cdd
```

`cdd_tool.py` uses only the Python standard library. In-place CDD edits create `ECU.cdd.bak_<timestamp>` and
`--out copy.cdd` writes a copy instead; `enable-tp` backs up the `.conf` before editing.

### Contents

```
SKILL.md                            skill instructions (frontmatter + workflow + recipes)
references/cdd-format.md            CDD XML structure, mayBeExec/trans, state indexes, DTC records
references/did-mode-workflow.md     questionnaire sheets -> CDD edit mapping (incl. OEM 03.x/02.x layouts)
references/did-add-and-state-model.md  adding DIDs, reworking unlock state model, DiVa TP setup
references/diva-integration.md      DiVa project files/config, regeneration, failure patterns
scripts/cdd_tool.py                 inspect / dtc / state-model / add-did / set-states / set-trans /
                                    remove-service / remove-instance / enable-tp / validate
tools/publish_github.py             create the GitHub repo and upload this directory via the API
```

### Notes

- Written from real projects: CANdela Studio 11 CDD (dtdvers 11.0.102) consumed by CANoe.DiVa 12. The file
  format details and the state-index mapping are general; the exact session/security layout has to be read
  from each CDD (`inspect` / `state-model` print it).
- `mayBeExec` semantics used by the checker: a state group that appears in the list is constrained, a group
  that does not appear is unconstrained. This matches DiVa 12's `AutomaticStateChange` behaviour on real runs.
- Never edit a CDD without a backup and without re-running generation afterwards.
- The DiVa project stores the CDD path **and MD5**; after changing the CDD, re-open/synchronize the project and
  re-generate — and close the DiVa GUI before editing its `*.conf` externally.

### License

MIT — see `LICENSE`.

---

<a id="中文"></a>
## 中文

### 简介

这是一个 [DeepSeek Harness](https://github.com/deepseek-ai) skill：教模型检查并修正 CANdela CDD（`.cdd`）
诊断描述文件，使 CANoe / CANoe.DiVa 生成的诊断测试与真实 ECU 以及整车厂的诊断问卷一致。

### 覆盖内容

- **DID 与问卷对齐** —— 从 OEM 问卷表（`03.1.System DID`、`03.2.ECU DID`、`4_1_Read DID`、`4_2_Write DID`
  等）提取「ECU是否支持」和读写矩阵，找出 ECU 支持但 CDD 里缺失的 DID，并在不破坏 XML 的前提下补进去
  （DID 池 + 变体 `DIDREF` + `Identification` 实例）。
- **DID 读写模式** —— 哪些 DID 有 `Read`（0x22）/ `Write`（0x2E）服务，以及在哪些会话（默认 `0x01`、
  编程 `0x02`、扩展 `0x03`、供应商 `0x60`）和哪些安全状态下可执行。
- **状态模型（`mayBeExec` 与 `trans`）** —— DiVa 如何从 CDD 推导用例选择和前置状态序列；如何表达会话跳转与
  重新锁定；如何新增一级 `0x27` 种子/密钥；如何让可写 DID 具备「可达的解锁前置状态」。
- **ECU 不支持的服务** —— 删除 ECU 拒绝的 `0x28` 子服务或整个 DID，并一并清理变体里的 `DIAGINSTREF` 引用。
- **DTC 列表** —— 把问卷中的必填 DTC 与 CDD 的 `RECORD` 条目对照（`v = DTC << 8 | 故障类型字节`）。
- **DiVa 闭环** —— 工程文件（`*.diva` / `*.conf`）、同步、重新生成、`TestTransportLayer` / `RunsOnCan`
  等测试项开关、服务排除，以及读报告区分「CDD 配错了」还是「ECU 有问题」。

### 安装

把本目录复制或克隆到 DSH 的 skill 目录，目录名用 `cdd-writing`：

| 作用域 | 路径 |
|---|---|
| 用户级 | `~/.dsh/skills/cdd-writing/` |
| 项目级 | `<项目>/.dsh/skills/cdd-writing/` |

下个会话即可被发现（部分版本热加载立即生效）。用 `/cdd-writing` 或让模型列出可用 skill 验证。

```bash
git clone https://github.com/messsi-s/cdd-writing-skill.git ~/.dsh/skills/cdd-writing
```

### 使用

直接让模型处理 CDD 任务，或手动 `/cdd-writing` 调用。自带的命令行工具也可以单独使用：

```bash
python scripts/cdd_tool.py inspect    ECU.cdd          # 状态组 + DID 矩阵
python scripts/cdd_tool.py inspect    ECU.cdd --json   # 机器可读输出
python scripts/cdd_tool.py dtc        ECU.cdd
python scripts/cdd_tool.py state-model ECU.cdd         # 会话/安全模型 + 可达性
python scripts/cdd_tool.py add-did ECU.cdd --did 0xF0F0 --clone 0xF160 \
        --name "F0F0_当前运行分区信息" --qual F0F0
python scripts/cdd_tool.py set-states ECU.cdd --did 0xF190 --qual Write --states 3,6
python scripts/cdd_tool.py set-trans  ECU.cdd --inst ProgrammingSession --qual Start \
        --trans "(2,2,3,2,6,5,7,5,8,5)"
python scripts/cdd_tool.py remove-service ECU.cdd --did 0xF187 --qual Write
python scripts/cdd_tool.py remove-instance ECU.cdd --did 0x3012
python scripts/cdd_tool.py enable-tp  D:\path\to\diva\project.conf --state on
python scripts/cdd_tool.py validate   ECU.cdd
```

`cdd_tool.py` 只依赖 Python 标准库。原地修改 CDD 会先生成 `ECU.cdd.bak_<时间戳>` 备份；`--out copy.cdd`
则写到副本；`enable-tp` 改 `.conf` 前同样先备份。

### 目录结构

```
SKILL.md                            skill 指令（frontmatter + 工作流 + 命令配方）
references/cdd-format.md            CDD XML 结构、mayBeExec/trans、状态索引、DTC 记录
references/did-mode-workflow.md     问卷表 → CDD 改法映射（含 OEM 03.x/02.x 表结构）
references/did-add-and-state-model.md  新增 DID、重构解锁状态模型、DiVa TP 配置
references/diva-integration.md      DiVa 工程文件/配置、重新生成、失败模式
scripts/cdd_tool.py                 inspect / dtc / state-model / add-did / set-states / set-trans /
                                    remove-service / remove-instance / enable-tp / validate
tools/publish_github.py             用 GitHub API 建仓库并上传本目录
```

### 说明

- 来自真实项目：CANdela Studio 11 生成的 CDD（dtdvers 11.0.102），由 CANoe.DiVa 12 消费。文件格式细节和
  状态索引映射是通用的；具体的会话/安全布局必须从每个 CDD 里读（`inspect` / `state-model` 会打印出来）。
- 工具采用 `mayBeExec` 语义：出现在列表里的状态组表示「受约束」，未出现的状态组表示「不受约束」。这与
  DiVa 12 在实际运行中的 `AutomaticStateChange` 行为一致。
- 改 CDD 前必须备份，改完必须重新生成验证。
- DiVa 工程里存了 CDD 路径**和 MD5**；改完 CDD 要重新打开/同步工程并重新生成；从外部改 `*.conf` 前先关掉
  DiVa 界面。

### 许可证

MIT，见 `LICENSE`。
