# Adding DIDs and reworking the session/security model

Two jobs that recur on every CDD revision: *make the DID list match the questionnaire* and *make the
unlock state model match how the ECU really behaves*. Both are additive, surgical edits to the XML — no
re-serialisation, no new data types unless unavoidable.

Tool support: `add-did`, `set-trans`, `set-states --inst`, `state-model`, `inspect --json`.

---

## A. Aligning the DID list with the questionnaire

### A.1 Read the questionnaire's DID sheets

The OEM questionnaire (`<OEM>_diagnostic_requirement_investigation_list_*`) uses:

| Sheet | What it contains |
|---|---|
| `03.1.System DID` | system DIDs. A=seq, **B=DID**, C/D=EN/CN name, E=CVT, **F=R/W**, G=Size, H/I=Byte/Bit, T=functional, U–Z=$22 sessions (app 0x01/0x02/0x03, boot 0x01/0x02/0x03), AA–AD=$22 security (Level0/Level1/LevelFBL/LevelIMMO), AE–AJ=$2E sessions, AK–AN=$2E security, AO/AP=product/after-sales, **AR(44)=ECU是否支持** |
| `03.2.ECU DID` | same fields shifted left by one column (no CVT column): **E=R/W, F=Size, S=functional**, T–V=$22 app sessions, W–Y=$22 boot sessions, Z–AC=$22 security, AD–AF=$2E app, AG–AI=$2E boot, AJ–AM=$2E security, AN/AO=product/after-sales, **AQ(43)=ECU是否支持** |

Extraction rules that survive real files:

- A DID row is a row whose **column B starts with `0x`**; the `Num` column is unreliable (mixed text/numbers,
  duplicated or non-monotonic values) and sub-data rows of one DID leave column A empty.
- Stop at the row whose column B contains `#EndOfData`.
- The support column is merged over all sub-data rows of one DID — read its top-left cell.
- Empty support = **N**; `support=Y` is the only thing that must exist in the CDD.
- Duplicate EN names exist (e.g. `Time` for two different DIDs) — compare by DID number, never by name.
- Ignore the old-format sheets (`System DID_bak`, `ECU DID`): they are template leftovers with other DIDs.

Compare the `support=Y` set with `cdd_tool.py inspect --json`:

```bash
python scripts/cdd_tool.py inspect --json ECU.cdd > cdd_dids.json
```

- **support=Y and missing** → add (see A.2).
- **in CDD but not support=Y** → do *not* delete silently. Check evidence first: a DiVa report test
  `SimpleRequest_<qual>_Read` that **Passed** proves the ECU implements the DID even if the sheet says N.
  Internal/supplier DIDs (e.g. `0x1001 Internal sw version`) are often absent from the OEM sheet by design.
  Leave them in the CDD, record the conflict in the change log, and let the test owner decide.
- **$2E columns** decide Write support per session/security; a DID listed as R-only normally should not have a
  Write service, but removing writes is a requirement decision (DiVa projects often exclude the Write services
  anyway). Only remove when the questionnaire *and* the project agree; otherwise leave and document.

### A.2 Add a DID (three insertion points)

A DID lives in three places; all three are required:

| # | Location | Content |
|---|---|---|
| 1 | `<DIDS>` pool, before `</DIDS>` | `<DID id=… oid=… n='<decimal>'>` + NAME/QUAL + `<STRUCTURE>` with `DATAOBJ`(s) whose `dtref` points to existing DATATYPES |
| 2 | `VAR/<DIDREFS>`, before `</DIDREFS>` | `<DIDREF didRef='<DID pool id>'/>` — without it the DID is not part of the variant DiVa uses |
| 3 | `DIAGCLASS Identification`, before its `</DIAGCLASS>` | `<DIAGINST>` with the `Read` (0x22) / `Write` (0x2E) `<SERVICE>`s, `<STATICVALUE shstaticref='<DID-number shortcut>' v='<decimal>'/>`, `<SIMPLECOMPCONT><DIDDATAREF didRef='<DID pool id>'/></SIMPLECOMPCONT>`, and one `<SIMPLECOMPCONT>` NR block per service (`Read_NR` / `Write_NR`) |

The simplest safe recipe is **clone an existing DID of the target byte length**:

```bash
# read-only DID, 9-byte data type -> clone F193/F197-style DID
python scripts/cdd_tool.py add-did ECU.cdd --did 0xF193 --clone 0xF197 --name "F193_供应商定义ECU硬件版本号" --qual F193

# readable+writable DID with write preconditions (extended + unlocked L1)
python scripts/cdd_tool.py add-did ECU.cdd --did 0xF190 --clone 0xF191 --name "F190_VIN" --qual F190 \
    --services Read,Write --mayBeExec "(3,6)"
```

What the command does: copies the pool `<STRUCTURE>` (regenerating every `oid`/`id`, keeping `dtref`), emits
new `DID`/`DIDREF`/`DIAGINST` nodes from the clone's service templates, inserts at the three points, keeps the
original line endings/BOM, backs up and validates. Afterwards adjust the sub-data `NAME`s/`QUAL`s if the clone's
labels do not fit (they are template-internal only).

Data widths: reuse an existing IDENT/STRUCTDT of the required size (`ASCII_n`, `HexDump_nByte`,
`Unsigned_nByte`, `Hex_m_5_1Byte` for variable-length). Prefer numeric/hexdump types for DiVa: a `TEXTTBL`
asserts the value set and produces "constant/texttable mismatch" failures when the ECU returns anything else.
If a DID spans several sub-fields, use one `DATAOBJ` per byte: DiVa only checks total length and data-type
conformance, so `Unsigned_1Byte` per byte is the safe default.

Validation after adding: `validate` (well-formed + no dangling refs), `inspect` shows the new DID and its
service set, and a diff against the original file must be **insert-only**.

---

## B. Reworking the session/security state model

### B.1 Hidden semantics (verified against real DiVa reports)

- `mayBeExec` is a list of flat state indexes. For every **state group that appears in the list**, the current
  state of that group must be one of the listed indexes. **Groups with no listed state are unconstrained.**
  This is why `SeedLevel1 mayBeExec='(3)'` is executed by DiVa in `Session_Extended, SecurityAccess_Locked`:
  the security group is not constrained, only the session is.
- `trans` pairs are `(from,to)` per state group: when the service runs, every group whose current index equals
  `from` moves to `to`. A missing pair leaves that group unchanged.
- Session-start services (`0x10`) carry both the allowed source sessions in `mayBeExec` and the resulting
  session + security relock in `trans`.
- Security services (`0x27`): one `DIAGINST` per subfunction pair; the `Request` service has no `trans`, the
  `Send` service has `trans='(locked,unlocked)'` and the `STATICVALUE` carries the subfunction number.

### B.2 Encoding a session transition matrix

Example requirement: *default must not jump to programming; programming must not jump to extended; any session
change relocks Level1/Level3/supplier unlocks.*

| Session-start | mayBeExec | trans |
|---|---|---|
| Default `0x10 01` | `(1,2,3)` | `(1,1,2,1,3,1,6,5,7,5,8,5)` |
| Programming `0x10 02` | `(2,3,4,6,7,8)` (extended + already unlocked only) | `(2,2,3,2,6,5,7,5,8,5)` |
| Extended `0x10 03` | `(1,3,4,5,6,7,8)` (no 2 → programming cannot jump) | `(1,3,3,3,6,5,7,5,8,5)` |

```bash
python scripts/cdd_tool.py set-trans ECU.cdd --inst ProgrammingSession --qual Start --trans "(2,2,3,2,6,5,7,5,8,5)"
python scripts/cdd_tool.py set-states ECU.cdd --inst ExtendedDiagnosticSession --qual Start --states 1,3,4,5,6,7,8
```

The `--inst` selector is what makes session/security instances addressable (they have no DID number).

### B.3 One security level per `0x27` pair

To add a new level (e.g. programming-session unlock via `0x27 03/04` → state `Level_3`), clone the existing
seed/key instance pair:

- `SeedLevel3` `Request`, `STATICVALUE v='3'`, `mayBeExec` = sessions where the seed may be requested
- `KeyLevel3` `Send`, `STATICVALUE v='4'`, `mayBeExec` = same sessions + security states, `trans='(5,7)'`

Then give the writable DIDs the resulting security index: `mayBeExec=(2,7)` (programming + Level_3) or
`(3,6)` (extended + UnlockedL1). **Every DID write precondition must be reachable**: run `state-model` and fix
the seed/session lists until the reported combinations make sense.

### B.4 Verify with `state-model`

```bash
python scripts/cdd_tool.py state-model ECU.cdd          # human-readable
python scripts/cdd_tool.py state-model ECU.cdd --json   # machine-readable
```

It prints the flattened state groups, every session/security service with `mayBeExec`/`trans`, a BFS over
`(session, security)` from the initial state (first state of each group), the unreachable compound states, and
for every constrained DID service the compound states in which it can actually be executed. Use it to assert:

- the forbidden jumps are absent (e.g. `Default→Programming`, `Programming→Extended`);
- every intended write precondition is listed as reachable;
- no state "disappeared" for the wrong reason (unreachable = the model cannot prepare that state).

### B.5 Pitfalls learned on real projects

- **The questionnaire's security-level names may not match the ECU.** In one questionnaire the header said
  `LevelFBL` = "3rd/4th row of the 0x27 table" (= `0x27 11/12`), while the owner asked for `0x27 03/04` as the
  programming-session unlock. Encode what the owner/ECU proves, and write the conflict into the change log
  (`NRC 0x12` on an unlock step in a DiVa report means the subfunction is wrong).
- **Relock pairs matter**: if a session change resets security on the ECU but the CDD only relocks `6→5`, DiVa
  will plan the next write without re-unlocking and the ECU answers `0x24`. Add `(7,5)`/`(8,5)` too.
- **Adding a `Write` mayBeExec can make an old passing test incomparable**: the test ID is stable but its
  precondition sequence changes; re-generate and compare report sequence names, not only verdicts.
- **Keep the change additive**. Every edit today is a 1–2 line attribute rewrite or an inserted block; a full
  XML round-trip would destroy the base64 image and mixed line endings.

---

## C. CANoe.DiVa side (transport layer and configuration)

The CDD cannot switch test groups on; that lives in the DiVa project configuration (`<project>.conf`). See
`diva-integration.md` for the full list. The transport-layer switch is:

```bash
python scripts/cdd_tool.py enable-tp <project>.conf --state on            # Test Transport Layer = true
python scripts/cdd_tool.py enable-tp <project>.conf --state on --runs-on-can off
```

Notes:

- DiVa 12 documents the standard transport-layer tests for **classic CAN 2.0 only**; with
  `RunsOnCan="true"` (CAN FD / other networks) the TP group may be refused, so check the setting against the
  real bus.
- The DiVa project stores the CDD path **and MD5** in `<project>.diva`; after editing the CDD, re-open the
  project and *Synchronize with diagnostic specification* before generating tests. Editing `<project>.conf`
  while the DiVa GUI has the project open risks being overwritten — close the project (or the GUI) first.
- A finished DiVa run leaves `<project>.html` (test specification: groups `Format / DataContent / Application /
  Session / General / FunctionalAddressing / Transport Layer` with test counts) and `<reportNNNN>` (executed
  results). Use them as evidence for "the ECU really supports this DID/service" decisions.
