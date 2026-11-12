# cdd-writing — a DSH skill for CANdela CDD editing

A [DeepSeek Harness](https://github.com/deepseek-ai) skill that teaches the agent to inspect and correct
CANdela CDD (`.cdd`) diagnostic descriptions so that CANoe / CANoe.DiVa generate the diagnostic tests that
match the ECU and the customer's diagnostic questionnaire.

## What it covers

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

## Install

Copy or clone this directory into a DSH skill directory, named `cdd-writing`:

| Scope | Path |
|---|---|
| user | `~/.dsh/skills/cdd-writing/` |
| project | `<project>/.dsh/skills/cdd-writing/` |

The skill is discovered on the next session (or immediately, depending on the Harness version). Verify with
`/cdd-writing` or by asking the agent for its available skills.

```bash
git clone https://github.com/<owner>/cdd-writing-skill.git ~/.dsh/skills/cdd-writing
```

## Use

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
python scripts/cdd_tool.py enable-tp  D:\T1V_DIVA\1111.conf --state on
python scripts/cdd_tool.py validate   ECU.cdd
```

`cdd_tool.py` uses only the Python standard library. In-place CDD edits create `ECU.cdd.bak_<timestamp>` and
`--out copy.cdd` writes a copy instead; `enable-tp` backs up the `.conf` before editing.

## Contents

```
SKILL.md                            skill instructions (frontmatter + workflow + recipes)
references/cdd-format.md            CDD XML structure, mayBeExec/trans, state indexes, DTC records
references/did-mode-workflow.md     questionnaire sheets -> CDD edit mapping (incl. Chery 03.x/02.x layouts)
references/did-add-and-state-model.md  adding DIDs, reworking unlock state model, DiVa TP setup
references/diva-integration.md      DiVa project files/config, regeneration, failure patterns
scripts/cdd_tool.py                 inspect / dtc / state-model / add-did / set-states / set-trans /
                                    remove-service / remove-instance / enable-tp / validate
tools/publish_github.py             create the GitHub repo and upload this directory via the API
```

## Notes

- Written from real projects: CANdela Studio 11 CDD (dtdvers 11.0.102) consumed by CANoe.DiVa 12. The file
  format details and the state-index mapping are general; the exact session/security layout has to be read
  from each CDD (`inspect` / `state-model` print it).
- `mayBeExec` semantics used by the checker: a state group that appears in the list is constrained, a group
  that does not appear is unconstrained. This matches DiVa 12's `AutomaticStateChange` behaviour on real runs.
- Never edit a CDD without a backup and without re-running generation afterwards.
- The DiVa project stores the CDD path **and MD5**; after changing the CDD, re-open/synchronize the project and
  re-generate — and close the DiVa GUI before editing its `*.conf` externally.

## License

MIT — see `LICENSE`.
