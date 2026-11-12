---
name: cdd-writing
description: "Write and correct CANdela CDD XML for UDS diagnostic testing: set DID read/write modes, session and security-state constraints (mayBeExec), remove unsupported services, and keep DTC lists aligned with a diagnostic questionnaire so CANoe.DiVa generates the expected tests."
whenToUse: "When a supplier CDD must be corrected or extended before automated diagnostic tests (CANoe.DiVa/CANoe): DID read/write availability, session/security state model, removing sub-services the ECU does not support, or comparing a CDD against a diagnostic questionnaire (Read/Write DID, DTC, service support sheets)."
user-invocable: true
---

# CDD writing (CANdela diagnostic description)

Use this skill to change a `.cdd` (CANdela Studio XML, `<CANDELA dtdvers=...>`) so that CANoe/CANoe.DiVa generate the diagnostic tests that match the real ECU and the customer's diagnostic questionnaire.

**Never edit a CDD casually.** Always: work on a copy or let the tool back up the original, keep the file well-formed XML, and re-run generation in DiVa afterwards and read the report. A CDD that loads but encodes the wrong state model produces plausible-looking test runs that are simply wrong.

## Workflow

1. **Find the requirement.** Typically an Excel diagnostic questionnaire with sheets like `4_1_Read DID`, `4_2_Write DID`, `3_1_DTC Information`, `1_1_ApplicationServices`, `4_5_SecurityAccess`. Extract: which DIDs are readable/writable, in which sessions (`0x01` default, `0x02` programming, `0x03` extended, supplier `0x60`), with which security level (`Level1`, `LevelFBL`, ...), and which services/subfunctions the ECU supports.
2. **Inspect the CDD.** `python scripts/cdd_tool.py inspect ECU.cdd` prints the state groups (with the exact state indexes DiVa uses), every DID with its `Read`/`Write` services and their current `mayBeExec`, and the DTC count. `dtc` lists DTC codes, failure type bytes and descriptions.
3. **Map requirement to states.** State indexes are the *flat* numbering of the CDD `STATEGROUPS` block, in document order: e.g. sessions `1=Default 2=Programming 3=Extended 4=SupplierSession`, then security `5=Locked 6=UnlockedL1 7=Level_3 8=Level_97`. See `references/cdd-format.md`. Never guess indexes — read them from `inspect`.
4. **Apply the edit.** Use `set-states` for `mayBeExec` changes, `remove-service` for sub-services the ECU does not support, and text edits only for things the script does not cover (new data types, new DIDs). Backups are automatic for in-place edits.
5. **Validate.** `python scripts/cdd_tool.py validate ECU.cdd` checks well-formed XML, dangling `DIAGINSTREF`s and duplicate service ids. Also confirm the DID matrix against the requirement sheet by eye or by exporting `inspect --json`.
6. **Regenerate and verify.** In CANoe.DiVa: replace the diagnostic description, *Synchronize with diagnostic specification*, regenerate tests, run, and read the report. Expected test sequences and how to read failures: `references/diva-integration.md`.

## Fast recipes

```bash
# state + DID matrix
python scripts/cdd_tool.py inspect  ECU.cdd
python scripts/cdd_tool.py dtc      ECU.cdd

# read a DID only in sessions 1 and 3 (default + extended), no security constraint
python scripts/cdd_tool.py set-states ECU.cdd --did 0xF187 --qual Read --states 1,3

# write only in extended (3) after L1 unlock (6)
python scripts/cdd_tool.py set-states ECU.cdd --did 0xF190 --qual Write --states 3,6

# write only in supplier session 0x60 (4) after 0x61/0x62 unlock (8)
python scripts/cdd_tool.py set-states ECU.cdd --did 0xF18C --qual Write --states 4,8

# make a DID read-only by deleting its Write service (and its Write NR block)
python scripts/cdd_tool.py remove-service ECU.cdd --did 0xF187 --qual Write

# delete a DID instance entirely, including DIAGINSTREFs in variants
python scripts/cdd_tool.py remove-instance ECU.cdd --did 0x3012

# check the file before giving it to DiVa
python scripts/cdd_tool.py validate ECU.cdd
```

Use `--out other.cdd` to write a copy instead of editing in place (in-place edits leave `ECU.cdd.bak_<timestamp>`).

## Rules that prevent broken test plans

- **`mayBeExec` is the state constraint.** It is a list of flat state indexes in which the service may be executed. Add a service's session(s) *and* the security state(s) simultaneously; e.g. `(3,6)` = extended session and unlocked-L1. Omitting the security indexes means "any security state" is not implied — DiVa treats listed indexes as the allowed compound state.
- **A blocked state is a missing index.** `Read` service with no state index blocked in programming → programming-session read tests disappear (or are generated as "expected denial"). That is how you express "this DID cannot be read in programming session".
- **Session changes relock in many ECUs.** The CDD's `trans` attribute on session-start services encodes the target state, e.g. `trans='(1,3,3,3,6,5,7,5,8,5)'` = session `0x03` starts from default/extended, and unlocked security states `6/7/8` fall back to locked `5`. If the ECU relocks on session change but the CDD does not say so, DiVa plans wrong preconditions.
- **Deleting a service also means deleting its NR block** (`SPECDATAOBJ`/`SIMPLECOMPCONT` with `Write NR`/`Read NR`) inside the same `DIAGINST`, and **deleting a `DIAGINST` also means deleting every `DIAGINSTREF`** in variants (otherwise the CDD is structurally invalid).
- **Keep the XML formatting.** Do not re-serialize the whole document: CDD files contain base64 image payloads and CDATA-ish text. Line-based, targeted rewrites of the `mayBeExec` attribute are safer and produce reviewable diffs.
- **Security subfunction values are part of the spec.** In the CDD they are `STATICVALUE` values in the security diagnostic instances (`Request`/`Send`). `0x27 03/04`, `0x27 01/02`, `0x27 11/12` and supplier `0x27 61/62` are different levels; verify which the ECU answers positively before encoding them.
- **Duplicate copies exist.** A CDD often has several variants (`CommonDiagnostics`, `*_by_reference`, `*_by_copy`). DiVa uses the variant selected in its project — usually `CommonDiagnostics`. Change the copies too if you want the CDD to be internally consistent.

## References

- `references/cdd-format.md` — CDD XML structure, `mayBeExec`/`trans` encoding, state groups, DTC `RECORD` values, static references.
- `references/did-mode-workflow.md` — mapping questionnaire sheets (Read/Write DID, sessions, security levels, DTC sheets) to CDD edits; recurring mapping tables from real projects.
- `references/diva-integration.md` — how DiVa consumes the state model, how to regenerate and read reports, and which failures mean "the CDD is wrong" versus "the ECU is wrong".
- `scripts/cdd_tool.py` — inspect / set-states / remove-service / remove-instance / validate / dtc.
