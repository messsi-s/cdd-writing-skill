---
name: cdd-writing
description: "Write and correct CANdela CDD XML for UDS diagnostic testing: align the DID list with a diagnostic questionnaire (add missing DIDs), set DID read/write modes, rework the session/security state model (mayBeExec/trans, new 0x27 levels), remove unsupported services, and switch on CANoe.DiVa test aspects (e.g. Transport Layer) so CANoe.DiVa generates the expected tests."
whenToUse: "When a supplier CDD must be corrected or extended before automated diagnostic tests (CANoe.DiVa/CANoe): new/missing DIDs from the OEM questionnaire, DID read/write availability, session/security unlock flow and state model, removing sub-services the ECU does not support, comparing a CDD against a diagnostic questionnaire (Read/Write DID, DTC, service support sheets), or enabling DiVa test groups such as the transport layer."
user-invocable: true
---

# CDD writing (CANdela diagnostic description)

Use this skill to change a `.cdd` (CANdela Studio XML, `<CANDELA dtdvers=...>`) so that CANoe/CANoe.DiVa generate the diagnostic tests that match the real ECU and the customer's diagnostic questionnaire.

**Never edit a CDD casually.** Always: work on a copy or let the tool back up the original, keep the file well-formed XML, and re-run generation in DiVa afterwards and read the report. A CDD that loads but encodes the wrong state model produces plausible-looking test runs that are simply wrong.

## Workflow

1. **Find the requirement.** Typically an Excel diagnostic questionnaire with sheets like `4_1_Read DID`, `4_2_Write DID`, `3_1_DTC Information`, `1_1_ApplicationServices`, `4_5_SecurityAccess` (or, on some OEM projects, `03.1.System DID`, `03.2.ECU DID`, `02.1.ApplicationServices`, `02.2.BootServices`). Extract: which DIDs are readable/writable, in which sessions (`0x01` default, `0x02` programming, `0x03` extended, supplier `0x60`), with which security level (`Level1`, `LevelFBL`, ...), which services/subfunctions the ECU supports, and each DID's `ECU是否支持` column. Exact column layouts and the extraction rules are in `references/did-mode-workflow.md`.
2. **Inspect the CDD.** `python scripts/cdd_tool.py inspect --json ECU.cdd` prints the state groups (with the exact state indexes DiVa uses), every DID with its `Read`/`Write` services and their current `mayBeExec`, and the DTC count. `dtc` lists DTC codes, failure type bytes and descriptions. `state-model` prints the session/security model with reachability.
3. **Map requirement to states.** State indexes are the *flat* numbering of the CDD `STATEGROUPS` block, in document order: e.g. sessions `1=Default 2=Programming 3=Extended 4=SupplierSession`, then security `5=Locked 6=UnlockedL1 7=Level_3 8=Level_97`. See `references/cdd-format.md`. Never guess indexes — read them from `inspect`/`state-model`.
4. **Apply the edit.** Use `add-did` for missing DIDs, `set-states`/`set-trans` for `mayBeExec`/`trans` changes (also for session/security instances via `--inst`), `remove-service`/`remove-instance` for things the ECU does not support, and text edits only for what the script does not cover. Backups are automatic for in-place edits. Details for the two big jobs: `references/did-add-and-state-model.md`.
5. **Validate.** `python scripts/cdd_tool.py validate ECU.cdd` checks well-formed XML, dangling `DIAGINSTREF`s and duplicate service ids. Confirm the DID matrix against the requirement sheet (`inspect --json`), and check the diff against the previous revision is *additive/targeted only* (attribute rewrites + inserted blocks, no full re-serialisation).
6. **Check the state machine.** `state-model` must show every intended write precondition as reachable, and the forbidden session jumps as absent (`Default→Programming`, `Programming→Extended`, …). Unreachable compound states are a CDD defect, not an ECU defect.
7. **Regenerate and verify.** In CANoe.DiVa: replace the diagnostic description, *Synchronize with diagnostic specification*, regenerate tests, run, and read the report. Test groups and per-service exclusions live in the DiVa project `*.conf` — e.g. `enable-tp` switches the Transport Layer group on. Expected test sequences and failure patterns: `references/diva-integration.md`.

## Fast recipes

```bash
# state + DID matrix / DTC list / session-security model
python scripts/cdd_tool.py inspect    ECU.cdd
python scripts/cdd_tool.py inspect    ECU.cdd --json
python scripts/cdd_tool.py dtc        ECU.cdd
python scripts/cdd_tool.py state-model ECU.cdd
python scripts/cdd_tool.py state-model ECU.cdd --json

# add a DID by cloning an existing one (data structure + services + variant refs)
python scripts/cdd_tool.py add-did ECU.cdd --did 0xF0F0 --clone 0xF160 --name "F0F0_当前运行分区信息" --qual F0F0
python scripts/cdd_tool.py add-did ECU.cdd --did 0xF190 --clone 0xF191 --name F190 --qual F190 \
    --services Read,Write --mayBeExec "(3,6)"

# read a DID only in sessions 1 and 3 (default + extended), no security constraint
python scripts/cdd_tool.py set-states ECU.cdd --did 0xF187 --qual Read --states 1,3

# write only in extended (3) after L1 unlock (6)
python scripts/cdd_tool.py set-states ECU.cdd --did 0xF190 --qual Write --states 3,6

# write only in supplier session 0x60 (4) after 0x61/0x62 unlock (8)
python scripts/cdd_tool.py set-states ECU.cdd --did 0xF18C --qual Write --states 4,8

# session / security instances have no DID: address them by instance QUAL
python scripts/cdd_tool.py set-states ECU.cdd --inst SeedLevel3 --qual Request --states 2,3
python scripts/cdd_tool.py set-trans  ECU.cdd --inst ProgrammingSession --qual Start --trans "(2,2,3,2,6,5,7,5,8,5)"

# make a DID read-only by deleting its Write service (and its Write NR block)
python scripts/cdd_tool.py remove-service ECU.cdd --did 0xF187 --qual Write

# delete a DID instance entirely, including DIAGINSTREFs in variants
python scripts/cdd_tool.py remove-instance ECU.cdd --did 0x3012

# switch on the DiVa Transport Layer test group (edit the DiVa project config, not the CDD)
python scripts/cdd_tool.py enable-tp D:\path\to\diva\project.conf --state on

# check the file before giving it to DiVa
python scripts/cdd_tool.py validate ECU.cdd
```

Use `--out other.cdd` to write a copy instead of editing in place (in-place edits leave `ECU.cdd.bak_<timestamp>`).

## Rules that prevent broken test plans

- **`mayBeExec` is the state constraint.** It is a list of flat state indexes. For every state group that appears in the list, the current state of that group must be listed; **groups with no listed state are unconstrained** (verified against DiVa: `SeedLevel1 mayBeExec='(3)'` runs in extended + Locked). So `(3,6)` = extended session *and* a security state from `{6}`; `(3)` = extended session in any security state. Add the session(s) and the security state(s) together when you want a compound constraint.
- **A blocked state is a missing index, both ways.** Omitting a session index from a session-start service blocks that jump (`Programming→Extended` = remove `2` from the Extended-start list); omitting `1` from the programming-start list is what makes "default cannot jump to programming" true.
- **Session changes relock in many ECUs.** The CDD's `trans` attribute on session-start services encodes the target state, e.g. `trans='(1,3,3,3,6,5,7,5,8,5)'` = from default/extended into extended, and unlocked security states `6/7/8` fall back to locked `5`. If the ECU relocks on session change but the CDD does not say so, DiVa plans wrong preconditions and the next write fails with `0x24`/`0x22`. Add a pair for every unlocked level the ECU resets.
- **DID write preconditions must be reachable.** A `Write` `mayBeExec` that lists a security state no `0x27` service can produce is silently unreachable; `state-model` reports the reachable combinations — run it after every state-model change.
- **Deleting a service also means deleting its NR block** (`SPECDATAOBJ`/`SIMPLECOMPCONT` with `Write NR`/`Read NR`) inside the same `DIAGINST`, and **deleting a `DIAGINST` also means deleting every `DIAGINSTREF`** in variants (otherwise the CDD is structurally invalid).
- **Keep the XML formatting and keep edits additive.** Do not re-serialize the whole document: CDD files contain base64 image payloads and CDATA-ish text. Line-based, targeted rewrites of `mayBeExec`/`trans` and inserted DID blocks are safer and produce reviewable diffs. Verify with a line diff (insert-only + expected attribute rewrites).
- **DIDs live in three places.** A new DID needs the `<DIDS>` pool node (`n` = decimal DID, `<STRUCTURE>` with `dtref` data objects), a `<DIDREF>` in the variant, **and** a `DIAGINST` in the `Identification` class (`STATICVALUE v` = decimal DID, `DIDDATAREF`, NR blocks). Missing the `DIDREF` means DiVa ignores the DID; a dangling `DIDDATAREF`/`DIDREF` breaks the file.
- **Security subfunction values are part of the spec.** In the CDD they are `STATICVALUE` values in the security diagnostic instances (`Request`/`Send`). `0x27 03/04`, `0x27 01/02`, `0x27 11/12` and supplier `0x27 61/62` are different levels; verify which the ECU answers positively before encoding them (a failing unlock with `NRC 0x12` in a DiVa report = wrong subfunction).
- **The questionnaire is evidence, not gospel.** `ECU是否支持=N` does not always mean the ECU rejects the DID (internal/supplier DIDs are often unlisted, and a passing DiVa read test proves support), and the security-level names in the sheet may map to different `0x27` subfunctions in the ECU. Add what `support=Y` requires, keep existing entries unless the evidence says otherwise, and record every conflict in the change log.
- **Duplicate copies exist.** A CDD often has several variants (`CommonDiagnostics`, `*_by_reference`, `*_by_copy`). DiVa uses the variant selected in its project — usually `CommonDiagnostics`. Change the copies too if you want the CDD to be internally consistent.

## References

- `references/cdd-format.md` — CDD XML structure, `mayBeExec`/`trans` encoding, state groups, DID pool/instance layout, DTC `RECORD` values, static references.
- `references/did-mode-workflow.md` — mapping questionnaire sheets (Read/Write DID, sessions, security levels, DTC sheets, OEM `03.1`/`03.2`/`02.1`/`02.2` layouts) to CDD edits; recurring mapping tables from real projects.
- `references/did-add-and-state-model.md` — adding a DID (three insertion points, data types, clone recipe), reworking session/security transitions and adding a `0x27` level, reachability verification, DiVa transport-layer setup.
- `references/diva-integration.md` — how DiVa consumes the state model, project files (`*.diva`/`*.conf`), transport-layer and exclusion switches, how to regenerate and read reports, and which failures mean "the CDD is wrong" versus "the ECU is wrong".
- `scripts/cdd_tool.py` — inspect / dtc / state-model / add-did / set-states / set-trans / remove-service / remove-instance / enable-tp / validate.
