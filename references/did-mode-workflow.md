# From a diagnostic questionnaire to a corrected CDD

The input is normally a customer/OEM questionnaire (Excel) plus a supplier CDD. The output is a CDD whose state model makes CANoe.DiVa generate the tests the customer expects — and pass against the real ECU.

## 1. Read the requirement sheets

Typical sheets and what they give you:

| Sheet | Contents |
|---|---|
| `4_1_Read DID` | every DID with `0x22` support: size, data type, security level, and columns for application/boot sessions (`0x01`, `0x03` / `0x01`, `0x02`, `0x03`) marked `R` (readable) or `N` |
| `4_2_Write DID` | every DID with `0x2E` support: sessions marked `R`, `W`, `R/W`, `N`, plus security level (`Level1`, `LevelFBL`, ...) |
| `3_1_DTC Information` | mandatory DTC list: display code, 3-byte `DTC Byte` (`DTC high`, `DTC low`, `failure type byte`), descriptions, supported DTC status bits, DTC groups |
| `1_1_ApplicationServices` | per-service and per-subfunction support, addressing (physical/functional), sessions (`0x01`/`0x03`), security access |
| `4_5_SecurityAccess` | seed/key algorithm (used to configure the DiVa seed-key DLL) |

Mapping questions to the CDD:

- A DID listed only in `4_1` is **read-only**: its `Read` service exists, its `Write` service must be absent.
- A DID listed in `4_2` with `W`/`R/W` gets a `Write` service constrained to the marked session; `R/W` in a cell also means read is possible there.
- Boot-software sessions (`0x01`/`0x02`/`0x03`) map onto the same CDD session states by subfunction number: boot `0x02` is the CDD "Programming" state (`2`) whether the header calls it application or boot.
- A DID with `N` in a boot/programming column must not be readable there: remove that session index from the `Read` `mayBeExec`.

## 2. Map security levels to states

| Questionnaire | Typical CDD state (unlocked by) |
|---|---|
| `Level0` / locked | `Locked` (5) |
| `Level1` | `UnlockedL1` (6), usually unlocked by `27 03/04` or `27 01/02` — check the ECU |
| `LevelFBL` | bootloader level, often `27 11/12`; encode only if the CDD has a reachable state for it |
| supplier / `0x61/0x62` level | `Level_97` (8) if the template names it that way; the `27 62` `trans` should point Locked → that state |

Verify against the actual ECU (report or manual frame test) which subfunction pair answers positively. A CDD level that the ECU rejects produces permanent `NRC 0x12` failures in the security tests and no successful unlock precondition for write tests.

## 3. Encode read/write modes

Practical rules used on real projects:

- **Identification DIDs (`0xF1xx`, `0xF18C`, `0xF190`, `0xF191`, ...)** — readable in application default/extended (`1,3`); not readable in programming if the questionnaire says so; writing usually requires the session + a Level1/LevelFBL unlock, e.g. `Write=(3,6)`.
- **Supplier session `0x60` writes** — `Write=(4,8)`: supplier session plus `Level_97` reached by `27 61/62`. The ECU usually relocks on session change, so the session-start service needs the relock transition (`6,5`,`7,5`,`8,5`).
- **The serial-number / PCBA DIDs written after a supplier unlock** are exactly the DIDs that belong to this `(4,8)` group; do not move other DIDs there without evidence.
- **`0x22` only reads** — no security indexes in `mayBeExec` (reads are usually allowed in any security state; listing session indexes only is enough).
- **52/50-prefix DIDs** are often the programming-session DIDs: allow programming session (`2`) plus the FBL/supplier unlock state.

After the edit the interface matrix should be reproducible with:

```bash
python scripts/cdd_tool.py inspect ECU.cdd
```

## 4. Services the ECU does not support

- `0x28` subfunction missing → delete that `CommunicationControl` `DIAGINST` (e.g. keep only `0x00` and `0x03` if the ECU rejects `0x01`/`0x02`). DiVa then stops emitting those tests.
- A whole DID not supported → `remove-instance` (also removes variant references).
- `0x19 0A` unreadable in programming → the FaultMemory `ReadAllSupported` service loses session `2`.
- `0x2E` unsupported for a DID → remove its `Write` service.

Deleting a subfunction is a *requirement decision*: the questionnaire (`1_1_ApplicationServices`) may still mark it supported; when the ECU behaviour contradicts the sheet, follow the ECU and record the conflict in the repo/issue, not silently in the CDD.

## 5. DTC check

Compare the CDD's `RECORD` list with `3_1_DTC Information`:

```bash
python scripts/cdd_tool.py dtc ECU.cdd
```

- All mandatory DTCs must exist as `RECORD` entries with the exact 3-byte code (DTC + FTB).
- Demo/sample rows (often marked `(demo)`) are not required in the CDD.
- DTC status bits and groups are separate from the DTC list; only review them when the questionnaire marks them unsupported.

## 6. Keep a change log

Write down, per CDD revision: requirement source (file + version/date), which DIDs changed read/write mode, which services were removed, and why. The next engineer will otherwise re-derive the same state-index mapping from scratch.
