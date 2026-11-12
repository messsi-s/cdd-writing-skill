# CANdela CDD XML — the parts you edit for diagnostic tests

A `.cdd` is plain XML (`<?xml version='1.0' encoding='utf-8'?>` + `<!DOCTYPE CANDELA SYSTEM 'candela.dtd'>`). It is produced by CANdela Studio and read by CANoe/CANoe.DiVa. All attributes use single quotes; files can be several MB because images are embedded as base64.

## Skeleton

```xml
<CANDELA dtdvers='11.0.102'>
  <ECUDOC oid='...' doctype='inst' languages='(en-US,de-DE)' saveno='67' xdtauth='31'>
    <PROTOCOLSTANDARD>UDS</PROTOCOLSTANDARD>
    ... attribute/template definitions (ATTRCATS, DEFATTS, ...) ...
    <ADDRESSING .../>
    <STATEGROUPS> ... </STATEGROUPS>
    <DATATYPES> IDENT / STRDEF / STRUCT / ENUM / TEXTTBL ... </DATATYPES>
    <NEGRESCODES> ... </NEGRESCODES>
    <ECU id='...' ...>
      <NAME>...</NAME><QUAL>ECU short name</QUAL>
      <UNS oid='...' attrref='<idPhysReq attr>' v='2000'/>   <- request CAN ID (decimal)
      <UNS oid='...' attrref='<idPhysRes attr>' v='2008'/>   <- response CAN ID (decimal)
      <UNS oid='...' attrref='<baudrate attr>' v='500000'/>
      <VAR id='...' oid='...' base='1'>                        <- variant (CommonDiagnostics, ...)
        <NAME><TUV xml:lang='en-US'>Common Diagnostics</TUV></NAME>
        <QUAL>CommonDiagnostics</QUAL>
        <DIAGCLASS id='...' oid='...'>                         <- e.g. Sessions, Identification, SecurityAccess
          <NAME><TUV xml:lang='en-US'>Identification</TUV></NAME>
          <QUAL>Identification</QUAL>
          <DIAGINST id='...' oid='...' tmplref='...' req='notReq'>   <- one DID (or one session/security item)
            <NAME><TUV xml:lang='en-US'>F187_Vehicle_Manufacturer_Spare_Part</TUV></NAME>
            <QUAL>F187_Vehicle_Manufacturer_Spare_Part</QUAL>
            <SERVICE id='...' oid='...' tmplref='...' func='1' phys='1' .../>
            ...
            <STATICVALUE oid='...' shstaticref='_...48E8F0' v='61831'/>   <- DID number, decimal
            <SIMPLECOMPCONT> <DATAOBJ ... dtref='<IDENT id>'/> </SIMPLECOMPCONT>
            <SIMPLECOMPCONT> <SPECDATAOBJ> Read_NR / Write_NR + NEGRESCODEPROXIES </SIMPLECOMPCONT>
          </DIAGINST>
          ...
        </DIAGCLASS>
        <DIAGINST .../>  (some instances, e.g. FaultMemory, live directly under VAR)
      </VAR>
      ...
    </ECU>
  </ECUDOC>
</CANDELA>
```

Variants may reference another variant's instances with `<DIAGINSTREF idref='<DIAGINST id>'/>`; deleting an instance without deleting its references breaks the CDD.

## `STATEGROUPS` — sessions and security

```xml
<STATEGROUPS>
  <STATEGROUP oid='...' spec='session'>
    <NAME><TUV xml:lang='en-US'>Session</TUV></NAME>
    <QUAL>Session</QUAL>
    <STATE oid='...'><NAME><TUV xml:lang='en-US'>Default</TUV></NAME><QUAL>Default</QUAL></STATE>
    <STATE ...>Programming</STATE>
    <STATE ...>Extended</STATE>
    <STATE ...>SupplierSession</STATE>
  </STATEGROUP>
  <STATEGROUP oid='...' spec='security'>
    <NAME>SecurityAccess</NAME>
    <STATE ...>Locked</STATE><STATE ...>UnlockedL1</STATE>
    <STATE ...>Level_3</STATE><STATE ...>Level_97</STATE>
  </STATEGROUP>
</STATEGROUPS>
```

**State indexes are flat across groups, in document order.** For the example above:

| index | group | state |
|---|---|---|
| 1 | Session | Default (`0x01`) |
| 2 | Session | Programming (`0x02`) |
| 3 | Session | Extended (`0x03`) |
| 4 | Session | SupplierSession (`0x60`) |
| 5 | SecurityAccess | Locked |
| 6 | SecurityAccess | UnlockedL1 |
| 7 | SecurityAccess | Level_3 |
| 8 | SecurityAccess | Level_97 |

Verify with `cdd_tool.py inspect` — names and counts differ per template, and a template may have 3 sessions or 6 (application/boot split).

## `mayBeExec` and `trans`

Every `SERVICE` may carry `mayBeExec='(1,3,6)'`:

- **`mayBeExec`** = the state indexes in which the service is executable. A compound state is allowed only when each participating state group's current state index is in this list (a service blocked in one group is blocked).
- No `mayBeExec` attribute = executable in every state (most read services in a fresh template).
- **`trans`** = transition pairs `(from,to)` for states whose target differs from the source, e.g. `trans='(1,1,2,1,3,1,6,5)'` on the "Default session start" service: from session states 1/2/3 → 1, and from unlocked security 6 → locked 5. Pairs are per state-group component; states not named keep their value.
- Session services also carry the session subfunction via a `STATICVALUE`/`ENUM` value (`<STATICVALUE ... shstaticref='<session attr id>' v='3'/>` for `0x10 0x03`).

Editing `mayBeExec` correctly is enough for DiVa's test *selection and precondition*; editing `trans` is needed for correct *state preparation* when a service changes the session or unlocks the ECU.

## Identification DIDs

- DID number is the first `STATICVALUE` in the `DIAGINST` whose `shstaticref` points at the DID static shortcut (in the default template this is a fixed internal id; the script detects it as the instance's DID value).
- Services are `Read` (`0x22`), `Write` (`0x2E`); one instance can have both. A read-only DID simply has no `Write` service.
- Data content is `SIMPLECOMPCONT → DATAOBJ spec='no' dtref='<IDENT/STRUCT id>'`; data type lengths are the `cv min/max` sizes in `DATATYPES` (`ASCII_10`, `HexDump_9_Byte`, ...).
- Negative-response whitelists live in `SIMPLECOMPCONT → SPECDATAOBJ (Read_NR/Write_NR) → NEGRESCODEPROXIES`; add `<NEGRESCODEPROXY idref='<NEGRESCODE id>'/>` to allow e.g. `0x24`, `0x35` for `0x27`.
- A DID is defined in **three** places (pool `<DIDS>` node with `n` = decimal number, variant `<DIDREFS>` entry, and the `Identification` `DIAGINST`). To add or clone DIDs use `cdd_tool.py add-did`; the full recipe is in `did-add-and-state-model.md`.

## DTCs

Individual DTCs are `<RECORD id='...' oid='...' v='<decimal>'>` entries (usually under a pool referenced by `RECORDDATAOBJ`):

```
v = (DTC_high_byte << 16) | (DTC_low_byte << 8) | failure_type_byte
```

Example: `0x5600` with FTB `0x16` → `v = 0x560016 = 5636118`. The DTC display name is the en-US `<TEXT>` inside the `RECORD`. The DTC-status byte structure (`<STRUCT>` with `TestFailed`, `PendingDtc`, ... `DATAOBJ`) and the groups (`GODTCDATAOBJ` with a `TEXTTBL`, e.g. `All groups = 0xFFFFFF`) are separate; a questionnaire usually only marks a subset as supported.

## Session/security service instances

`Sessions` class instances each hold a `Start` service with the session subfunction:
`0x01` Default, `0x02` Programming, `0x03` Extended, and supplier-specific ones such as `0x60` (`v='96'`). `SecurityAccess` class instances pair `Request` (seed) and `Send`/`SendKey` services whose subfunction is the pair `0x01/0x02`, `0x03/0x04`, `0x11/0x12`, `0x61/0x62`, etc.; the `Send` service's `trans` points from `Locked` to the unlocked state.

`CommunicationControl` (`0x28`) has one instance per subfunction (`0x00`..`0x03`); removing an instance removes that subfunction from DiVa's test generation.

## File handling rules

- Encoding UTF-8; some files start with a BOM. Preserve BOM and line endings (`\r\n` vs `\n`) when rewriting.
- Keep base64 payloads intact — never round-trip the document through an XML serializer.
- Line-targeted regex edits of attributes are reviewable and safe; validate afterwards with an XML parser and a dangling-reference check.
