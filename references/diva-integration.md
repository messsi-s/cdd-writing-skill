# CANoe.DiVa and the CDD state model

DiVa does not decide test preconditions from the report UI — it derives the state machine from the CDD's `STATEGROUPS`, per-service `mayBeExec`, and `trans`. Every change described in this skill shows up as different test cases, different "AutomaticStateChange" sequences, or different expected NRCs.

## What DiVa does with the CDD

- **Test selection**: a service is only tested in the compound states listed in its `mayBeExec`. Removing a state index removes those tests from the plan; leaving only one state makes DiVa set up exactly that state.
- **State preparation**: before a test, DiVa emits `AutomaticStateChange` and drives the ECU through session starts (`0x10`), seed/key (`0x27`) and any transition in `trans` until it believes the target state is reached. If the CDD says a session change relocks (`trans` pair `6,5` etc.), DiVa re-unlocks; if the CDD omits it, DiVa keeps assuming "unlocked" and the next write/read gets `NRC 0x24`/`0x22`.
- **Expected NRCs**: a received NRC that the service's `NEGRESCODEPROXIES` does not list produces "response code undefined for this service" warnings; add valid NRCs (`0x24`, `0x35`, `0x36`, `0x37`, ...) to the service.
- **Timeouts**: `P2Timeout`/`P2ExTimeout`/`P3Time` are CDD ECU attributes; DiVa uses them when the project enables ECU response timings. A CDD default that differs from the OEM spec (e.g. `P2=150 ms` vs a spec `50 ms`) produces timing-test failures that look like ECU bugs.

## Project workflow

1. Point the DiVa project's diagnostic description at the edited `.cdd` (project `.diva` file `<Specfication><File FilePath=...>`; the GUI also has a "change diagnostic specification" entry).
2. In DiVa: **Synchronize with diagnostic specification** (or CLI `DiVaAutomation.exe <project>.diva /S`), then regenerate tests.
3. Check the generated preconditions in the report:
   - F-prefix DID write with `mayBeExec=(3,6)` → report shows `10 03` → `27 03` → `27 04` → `0x2E`, state "Session_Extended, SecurityAccess_UnlockedL1".
   - Supplier-session DID write with `mayBeExec=(4,8)` → `10 03` → `10 60` → `27 61` → `27 62` → `0x2E`, state "Session_Supplier..., Level_97".
   - Read service without programming index → no `0x22` test inside programming session (or an explicit "expected denial" test).
4. Re-run the tests on the ECU. A failure at an `AutomaticStateChange` step means the CDD state model says "go to X" but the ECU forbids the way there — fix the `trans`/`mayBeExec` model, not the ECU test.
5. Report both sides: CDD change (file + version) and DiVa report (test case ids that changed verdict).

## Failure patterns that mean "CDD is wrong"

| Report symptom | Likely CDD defect |
|---|---|
| Write/read sent in `Default`, locked (`Session_Default, SecurityAccess_Locked`) | service has no `mayBeExec` at all (allowed everywhere) |
| `NRC 0x7E` at a session change step (`10 03` from programming) | the session-start service's `mayBeExec`/`trans` allows a transition the ECU forbids; block the source session |
| Unlock step fails with `NRC 0x12` (subfunction not supported) | wrong security subfunction value in the `SecurityAccess` instance |
| Unlock succeeds but the write still fails with `0x22` | session/security state combination in `mayBeExec` does not match the ECU (e.g. write needs `0x60`, CDD says extended) |
| `NRC 0x24` where `0x35` was expected | ECU was already unlocked because a previous rollback was missing in `trans` |
| Tests for a subfunction the ECU rejects (`0x28 01/02`, ...) | instance still present in the CDD; delete it |

## Notes

- The DiVa project stores the diagnostic description path and an MD5 of the file; editing the CDD in place makes DiVa re-synchronize on next load, but a copied/renamed file must be selected explicitly.
- `DiVaAutomation.exe <project>.diva /S /A /I` is the headless variant (`/S` sync, `/A` activate services, `/I` instructions, `/C` CANoe test environment). It needs a licensed CANoe environment; a plain desktop session may answer `CreatePrimitives: Abort because of error code: 1024`.
- Keep a backup of the previous CDD: DiVa reports are only comparable if the CDD revision is known (`saveno` attribute and your own versioned file name).
