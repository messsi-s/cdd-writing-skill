# cdd-writing — a DSH skill for CANdela CDD editing

A [DeepSeek Harness](https://github.com/deepseek-ai) skill that teaches the agent to inspect and correct
CANdela CDD (`.cdd`) diagnostic descriptions so that CANoe / CANoe.DiVa generate the diagnostic tests that
match the ECU and the customer's diagnostic questionnaire.

## What it covers

- **DID read/write modes** — which DIDs have a `Read` (0x22) / `Write` (0x2E) service and in which sessions
  (default `0x01`, programming `0x02`, extended `0x03`, supplier `0x60`) and security states.
- **State model (`mayBeExec` and `trans`)** — how DiVa derives test selection and precondition sequences from
  the CDD, and how to encode session/security constraints so the right tests are planned and unlocked.
- **Unsupported services** — removing `0x28` subfunctions or whole DIDs that the ECU rejects, including the
  variant `DIAGINSTREF`s that must go with them.
- **DTC lists** — comparing mandatory DTCs from a questionnaire with the CDD `RECORD` entries
  (`v = DTC << 8 | failure type byte`).
- **DiVa round trip** — synchronize, regenerate, and read the report to separate "CDD is wrong" from
  "ECU is wrong".

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
python scripts/cdd_tool.py inspect  ECU.cdd
python scripts/cdd_tool.py dtc      ECU.cdd
python scripts/cdd_tool.py set-states ECU.cdd --did 0xF190 --qual Write --states 3,6
python scripts/cdd_tool.py remove-service ECU.cdd --did 0xF187 --qual Write
python scripts/cdd_tool.py remove-instance ECU.cdd --did 0x3012
python scripts/cdd_tool.py validate ECU.cdd
```

`cdd_tool.py` uses only the Python standard library. In-place edits create `ECU.cdd.bak_<timestamp>`;
`--out copy.cdd` writes a copy instead.

## Contents

```
SKILL.md                          skill instructions (frontmatter + workflow)
references/cdd-format.md          CDD XML structure, mayBeExec/trans, state indexes, DTC records
references/did-mode-workflow.md   questionnaire sheets -> CDD edit mapping
references/diva-integration.md    DiVa state-model behaviour, regeneration, failure patterns
scripts/cdd_tool.py               inspect / dtc / set-states / remove-service / remove-instance / validate
tools/publish_github.py           create the GitHub repo and upload this directory via the API
```

## Notes

- Written from real projects: CANdela Studio 11 CDD (dtdvers 11.0.102) consumed by CANoe.DiVa 12. The file
  format details and the state-index mapping are general; the exact session/security layout has to be read
  from each CDD (`inspect` prints it).
- Never edit a CDD without a backup and without re-running generation afterwards.

## License

MIT — see `LICENSE`.
