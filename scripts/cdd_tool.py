#!/usr/bin/env python3
"""cdd_tool.py — inspect and edit CANdela CDD files for diagnostic test alignment.

Subcommands
    inspect          state groups, DID/service matrix, statistics
    dtc              DTC list (code, failure type byte, description)
    set-states       set a service's mayBeExec state indexes
    set-trans        set a service's trans (state transition pairs)
    state-model      print the session/security model and its reachability
    add-did          add a new DID by cloning an existing one (pool + DIDREF + instance)
    enable-tp        switch CANoe.DiVa "Test Transport Layer" (and RunsOnCan) in a .conf
    remove-service   delete a service (Read/Write/...) and its NR block
    remove-instance  delete a whole DID instance and its variant references
    validate         XML well-formedness + dangling DIAGINSTREF check

Everything works line-by-line on the original text so embedded base64 payloads and
formatting are preserved. In-place edits create ``<file>.bak_<timestamp>`` first.
"""

import argparse
import itertools
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import deque

SESSION_LIKE = ("Identification",)


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #

def read_text(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    return text, bom


def write_text(path, text, bom):
    with open(path, "wb") as handle:
        if bom:
            handle.write(b"\xef\xbb\xbf")
        handle.write(text.encode("utf-8"))


def attr(line, name):
    match = re.search(r"\s" + re.escape(name) + r"='([^']*)'", line)
    return None if match is None else match.group(1)


def parse_states(value):
    if not value:
        return []
    inner = re.search(r"\(([^)]*)\)", value)
    if inner is None:
        return []
    result = []
    for item in inner.group(1).split(","):
        item = item.strip()
        if item.isdigit():
            result.append(int(item))
    return result


def render_states(indices):
    unique = sorted({index for index in indices if isinstance(index, int) and index > 0})
    if not unique:
        raise SystemExit("no state index given")
    return "(" + ",".join(str(index) for index in unique) + ")"


def find_block_end(lines, start, tag):
    """Return the index of the line closing element `tag` opened at `start`."""
    close = "</" + tag + ">"
    open_predicate = lambda text: text.startswith("<" + tag + " ") or text == "<" + tag + ">"
    depth = 0
    for index in range(start, len(lines)):
        text = lines[index].strip()
        if open_predicate(text):
            depth += 1
        if text == close:
            depth -= 1
            if depth == 0:
                return index
    raise SystemExit("unterminated element %s at line %d" % (tag, start + 1))


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

def parse_state_groups(lines):
    groups = []
    inside = False
    current = None
    expect_group_qual = False
    expect_state_qual = False
    inside_state = False
    state_name = ""
    for raw in lines:
        text = raw.strip()
        if "<STATEGROUPS>" in text:
            inside = True
            continue
        if not inside:
            continue
        if "</STATEGROUPS>" in text:
            break
        if text.startswith("<STATEGROUP "):
            current = {"spec": attr(text, "spec") or "", "qual": "", "states": []}
            groups.append(current)
            expect_group_qual = True
            continue
        if text.startswith("</STATEGROUP>"):
            current = None
            continue
        if text.startswith("<STATE "):
            inside_state = True
            expect_state_qual = True
            state_name = ""
            continue
        if text.startswith("</STATE>"):
            inside_state = False
            continue
        match = re.search(r"<QUAL>([^<]+)</QUAL>", text)
        if match is not None:
            if expect_group_qual and current is not None:
                current["qual"] = match.group(1)
                expect_group_qual = False
                continue
            if expect_state_qual and current is not None:
                current["states"].append({"qual": match.group(1), "name": state_name or match.group(1)})
                expect_state_qual = False
                continue
        if inside_state and state_name == "" and "<TUV xml:lang='en-US'>" in text:
            value = re.search(r"<TUV xml:lang='en-US'>([^<]*)</TUV>", text)
            if value is not None and value.group(1).strip():
                state_name = value.group(1).strip()
    flat = []
    index = 0
    for group in groups:
        for state in group["states"]:
            index += 1
            flat.append({
                "index": index,
                "kind": group["spec"] or "state",
                "group": group["qual"],
                "qual": state["qual"],
                "name": state["name"],
            })
    return {"groups": groups, "states": flat}


def parse_instances(lines, state_info=None):
    """Yield DIAGINST dicts: id, class name, qual, static values, services."""
    instances = []
    class_name = None
    class_pending = False
    inside = False
    current = None
    inst_pending = False
    service = None
    service_qual_pending = False
    first_static_seen = False

    for line_index, raw in enumerate(lines):
        text = raw.strip()
        if text.startswith("<DIAGCLASS "):
            class_name = None
            class_pending = True
            continue
        if text.startswith("<DIAGINST "):
            current = {
                "id": attr(text, "id"),
                "line": line_index,
                "class": class_name,
                "qual": None,
                "static_values": [],
                "services": [],
            }
            inside = True
            inst_pending = True
            service = None
            first_static_seen = False
            continue
        if text.startswith("</DIAGINST>"):
            if current is not None:
                instances.append(current)
            current = None
            inside = False
            continue
        # A DIAGCLASS qualifier is read before any instance starts.
        if current is None and class_pending:
            match = re.search(r"<QUAL>([^<]+)</QUAL>", text)
            if match is not None:
                class_name = match.group(1)
                class_pending = False
            continue
        if not inside or current is None:
            continue
        if text.startswith("<SERVICE "):
            service = {
                "id": attr(text, "id"),
                "line": line_index,
                "mayBeExec": attr(text, "mayBeExec") or "",
                "trans": attr(text, "trans") or "",
                "qual": None,
            }
            current["services"].append(service)
            service_qual_pending = True
            continue
        if text.startswith("</SERVICE>"):
            service = None
            continue
        if text.startswith("<STATICVALUE "):
            value = attr(text, "v")
            if value is not None and value.lstrip("-").isdigit():
                current["static_values"].append({
                    "value": int(value),
                    "shstaticref": attr(text, "shstaticref"),
                    "line": line_index,
                })
                first_static_seen = True
            continue
        match = re.search(r"<QUAL>([^<]+)</QUAL>", text)
        if match is None:
            continue
        if service is not None and service_qual_pending:
            service["qual"] = match.group(1)
            service_qual_pending = False
            continue
        if inst_pending and current is not None and current["qual"] is None:
            current["qual"] = match.group(1)
            inst_pending = False
    return instances


def instance_did(instance):
    """Return the DID number of a DID instance, or None."""
    name = (instance.get("class") or "")
    if not any(key.lower() in name.lower() for key in SESSION_LIKE):
        return None
    if not instance["static_values"]:
        return None
    return instance["static_values"][0]["value"]


def parse_dtc_records(lines):
    records = []
    for raw in lines:
        text = raw.strip()
        if not text.startswith("<RECORD "):
            continue
        value = attr(text, "v")
        if value is None or not value.isdigit():
            continue
        numeric = int(value)
        if numeric <= 0 or numeric > 0xFFFFFF:
            continue
        records.append({
            "code": (numeric >> 8) & 0xFFFF,
            "ftb": numeric & 0xFF,
            "value": numeric,
            "raw": text,
        })
    return records


# --------------------------------------------------------------------------- #
# XML validation
# --------------------------------------------------------------------------- #

def validate(path):
    problems = []
    text, _ = read_text(path)
    stripped = re.sub(r"<!DOCTYPE[^>]*>", "", text, count=1)
    try:
        root = ET.fromstring(stripped.encode("utf-8"))
    except Exception as error:  # noqa: BLE001 - report parse failure verbatim
        return ["XML parse error: %s" % error], None
    ids = set()
    for element in root.iter():
        value = element.get("id")
        if value:
            ids.add(value)
    dangling = []
    for element in root.iter("DIAGINSTREF"):
        target = element.get("idref")
        if target not in ids:
            dangling.append(target)
    if dangling:
        problems.append("dangling DIAGINSTREF: %d (%s)" % (len(dangling), ", ".join(sorted(set(dangling))[:5])))
    service_ids = []
    for element in root.iter("SERVICE"):
        value = element.get("id")
        if value:
            service_ids.append(value)
    duplicates = sorted({value for value in service_ids if service_ids.count(value) > 1})
    if duplicates:
        problems.append("duplicate SERVICE id: %s" % ", ".join(duplicates[:5]))
    return problems, root


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def cmd_inspect(args):
    text, _ = read_text(args.cdd)
    lines = text.splitlines()
    state_info = parse_state_groups(lines)
    instances = parse_instances(lines, state_info)
    dids = []
    for instance in instances:
        did = instance_did(instance)
        if did is None:
            continue
        services = [svc for svc in instance["services"] if svc["qual"] in ("Read", "Write")]
        if not services:
            continue
        dids.append({
            "did": "0x%04X" % did,
            "name": instance["qual"],
            "class": instance["class"],
            "services": [
                {
                    "id": svc["id"],
                    "qual": svc["qual"],
                    "mayBeExec": svc["mayBeExec"],
                    "states": parse_states(svc["mayBeExec"]),
                }
                for svc in services
            ],
        })
    dtcs = parse_dtc_records(lines)
    if args.json:
        print(json.dumps({
            "path": os.path.abspath(args.cdd),
            "states": state_info["states"],
            "dids": dids,
            "stats": {"dids": len(dids), "dtcs": len(dtcs)},
        }, ensure_ascii=False, indent=2))
        return 0
    print("CDD: %s" % os.path.abspath(args.cdd))
    print("\nState groups (flat mayBeExec indexes):")
    for state in state_info["states"]:
        print("  %d  %-12s %-14s %s" % (state["index"], state["kind"], state["group"], state["qual"]))
    print("\nDIDs (%d):" % len(dids))
    print("  %-8s %-8s %-40s %s" % ("DID", "service", "instance", "mayBeExec"))
    for did in dids:
        for svc in did["services"]:
            print("  %-8s %-8s %-40s %s" % (did["did"], svc["qual"], did["name"], svc["mayBeExec"] or "(all)"))
    print("\nDTC records: %d" % len(dtcs))
    return 0


def cmd_dtc(args):
    text, _ = read_text(args.cdd)
    lines = text.splitlines()
    records = parse_dtc_records(lines)
    # DTC name: <TEXT> inside the same RECORD block
    named = []
    for index, raw in enumerate(lines):
        text_line = raw.strip()
        if not text_line.startswith("<RECORD "):
            continue
        value = attr(text_line, "v")
        if value is None or not value.isdigit() or not (0 < int(value) <= 0xFFFFFF):
            continue
        block_end = index
        for probe in range(index, min(index + 60, len(lines))):
            if lines[probe].strip().startswith("</RECORD>"):
                block_end = probe
                break
        block = "\n".join(lines[index:block_end + 1])
        match = re.search(r"<TUV xml:lang='en-US'>(.*?)</TUV>", block, re.S)
        name = " ".join(match.group(1).split()) if match is not None else ""
        numeric = int(value)
        named.append(((numeric >> 8) & 0xFFFF, numeric & 0xFF, name))
    print("DTC records: %d" % len(named))
    for code, ftb, name in sorted(named):
        print("  0x%04X  FTB 0x%02X  %s" % (code, ftb, name))
    return 0


# --------------------------------------------------------------------------- #
# editing
# --------------------------------------------------------------------------- #

def resolve_services(lines, args):
    """Return [line index, ...] of SERVICE start lines matching the request."""
    matches = []
    if args.service_id:
        for index, raw in enumerate(lines):
            text = raw.strip()
            if text.startswith("<SERVICE ") and attr(text, "id") == args.service_id:
                matches.append(index)
        if not matches:
            raise SystemExit("service id not found: %s" % args.service_id)
        return matches
    if args.inst:
        # match a diagnostic instance by its QUAL (works for Sessions/SecurityAccess,
        # where there is no DID number)
        for instance in parse_instances(lines):
            if instance["qual"] != args.inst:
                continue
            for svc in instance["services"]:
                if args.qual and svc["qual"] != args.qual:
                    continue
                matches.append(svc["line"])
            if matches:
                break
        if not matches:
            raise SystemExit("no matching service (inst=%s, qual=%s)" % (args.inst, args.qual))
        return matches
    did = None
    if args.did:
        did = int(args.did, 16) if args.did.lower().startswith("0x") else int(args.did, 16) if re.fullmatch(r"[0-9a-fA-F]{4}", args.did) else int(args.did)
    instances = parse_instances(lines)
    for instance in instances:
        if did is not None and instance_did(instance) != did:
            continue
        for svc in instance["services"]:
            if args.qual and svc["qual"] != args.qual:
                continue
            matches.append(svc["line"])
        if matches:
            break
    if not matches:
        raise SystemExit("no matching service (did=%s, qual=%s)" % (args.did, args.qual))
    return matches


def apply_set_states(lines, targets, states):
    rendered = render_states(states)
    changed = 0
    for index in targets:
        line = lines[index]
        if "mayBeExec=" in line:
            lines[index] = re.sub(r"mayBeExec='\([^)]*\)'", "mayBeExec='%s'" % rendered, line, count=1)
        else:
            lines[index] = re.sub(r"\s*>$", " mayBeExec='%s'>" % rendered, line, count=1)
        changed += 1
    return changed


def cmd_set_states(args):
    text, bom = read_text(args.cdd)
    lines = text.splitlines()
    targets = resolve_services(lines, args)
    changed = apply_set_states(lines, targets, args.states)
    output = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return finish_edit(args, output, bom, "set mayBeExec=%s on %d service(s)" % (render_states(args.states), changed))


def apply_set_trans(lines, targets, value):
    """Set the ``trans`` attribute of the given SERVICE lines."""
    changed = 0
    for index in targets:
        line = lines[index]
        if re.search(r"\strans='[^']*'", line):
            lines[index] = re.sub(r"\strans='[^']*'", " trans='%s'" % value, line, count=1)
        else:
            lines[index] = re.sub(r"\s*>$", " trans='%s'>" % value, line, count=1)
        changed += 1
    return changed


def cmd_set_trans(args):
    text, bom = read_text(args.cdd)
    lines = text.splitlines()
    targets = resolve_services(lines, args)
    changed = apply_set_trans(lines, targets, args.trans)
    output = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return finish_edit(args, output, bom, "set trans=%s on %d service(s)" % (args.trans, changed))


# --------------------------------------------------------------------------- #
# state model analysis (sessions / security / DID preconditions)
# --------------------------------------------------------------------------- #

def parse_pairs(value):
    indices = parse_states(value)
    return dict(zip(indices[0::2], indices[1::2]))


def service_entries(lines):
    """Every SERVICE with class/instance info and parsed mayBeExec/trans."""
    entries = []
    for instance in parse_instances(lines):
        for svc in instance["services"]:
            entries.append({
                "class": instance["class"] or "",
                "inst": instance["qual"],
                "did": instance_did(instance),
                "svc": svc["qual"],
                "line": svc["line"] + 1,
                "mayBeExec": set(parse_states(svc["mayBeExec"])),
                "trans": parse_pairs(svc["trans"]),
            })
    return entries


def cmd_state_model(args):
    text, _ = read_text(args.cdd)
    lines = text.splitlines()
    info = parse_state_groups(lines)
    states = info["states"]
    if not states:
        raise SystemExit("no STATEGROUPS in %s" % args.cdd)
    name_of = {s["index"]: s["qual"] for s in states}
    members = {}
    for state in states:
        members.setdefault(state["group"], []).append(state["index"])
    groups = sorted(members)
    entries = service_entries(lines)

    def executable(entry, compound):
        allowed = entry["mayBeExec"]
        if not allowed:
            return True
        for group in groups:
            listed = allowed & set(members[group])
            if listed and compound.get(group) not in listed:
                return False
        return True

    def advance(entry, compound):
        result = dict(compound)
        for group in groups:
            current = compound.get(group)
            if current in entry["trans"]:
                result[group] = entry["trans"][current]
        return result

    start = {group: members[group][0] for group in groups}
    session_entries = [e for e in entries if e["class"] == "Sessions" and e["svc"] == "Start"]
    security_entries = [e for e in entries
                        if e["class"] == "SecurityAccess"
                        and e["svc"] in ("Request", "Send", "RequestSeed", "SendKey")]
    actions = session_entries + security_entries

    seen = {tuple(sorted(start.items()))}
    queue = deque([start])
    while queue:
        compound = queue.popleft()
        for entry in actions:
            if not executable(entry, compound):
                continue
            nxt = advance(entry, compound)
            key = tuple(sorted(nxt.items()))
            if key not in seen:
                seen.add(key)
                queue.append(nxt)
    reachable = sorted((dict(key) for key in seen), key=lambda c: tuple(c[g] for g in groups))

    def label(compound):
        return ", ".join("%s=%s" % (group, name_of.get(compound[group], compound[group]))
                         for group in groups)

    def usable(entry):
        return [c for c in reachable if executable(entry, c)]

    if args.json:
        print(json.dumps({
            "cdd": os.path.abspath(args.cdd),
            "states": states,
            "initial": label(start),
            "reachable": [label(c) for c in reachable],
            "sessions": [{"instance": e["inst"], "service": e["svc"],
                          "mayBeExec": sorted(e["mayBeExec"]), "trans": e["trans"]}
                         for e in session_entries],
            "security": [{"instance": e["inst"], "service": e["svc"],
                          "mayBeExec": sorted(e["mayBeExec"]), "trans": e["trans"]}
                         for e in security_entries],
            "did_services": [
                {"did": "0x%04X" % e["did"], "service": e["svc"],
                 "mayBeExec": sorted(e["mayBeExec"]),
                 "reachable_in": [label(c) for c in usable(e)]}
                for e in entries if e["class"] == "Identification" and e["did"] is not None],
        }, ensure_ascii=False, indent=2))
        return 0

    print("State groups:")
    for state in states:
        print("  %d  %-12s %-14s %-16s %s"
              % (state["index"], state["kind"], state["group"], state["qual"], state["name"]))
    print("\nSession services:")
    for entry in session_entries:
        print("  [%-28s.%-8s] mayBeExec=%-24s trans=%-30s"
              % (entry["inst"], entry["svc"], sorted(entry["mayBeExec"]), entry["trans"]))
    print("\nSecurity services:")
    for entry in security_entries:
        print("  [%-28s.%-8s] mayBeExec=%-24s trans=%-30s"
              % (entry["inst"], entry["svc"], sorted(entry["mayBeExec"]), entry["trans"]))

    print("\nReachable compound states from (%s): %d" % (label(start), len(reachable)))
    for compound in reachable:
        print("  ", label(compound))
    all_combos = [dict(zip(groups, combo))
                  for combo in itertools.product(*[members[group] for group in groups])]
    unreachable = [c for c in all_combos if c not in reachable]
    if unreachable:
        print("\nUnreachable compound states (session+security that the model cannot prepare):")
        for compound in unreachable:
            print("  ", label(compound))

    print("\nDID services with a state constraint (write preconditions):")
    count = 0
    for entry in entries:
        if entry["class"] != "Identification" or entry["did"] is None or not entry["mayBeExec"]:
            continue
        count += 1
        combos = usable(entry)
        flag = "" if combos else "   <<< UNREACHABLE with the current model"
        print("  0x%04X %-6s mayBeExec=%-16s reachable in: %s%s"
              % (entry["did"], entry["svc"], sorted(entry["mayBeExec"]),
                 [label(c) for c in combos] or "none", flag))
    if not count:
        print("  (none: every Identification service is executable in all states)")
    return 0


# --------------------------------------------------------------------------- #
# add a new DID by cloning an existing one
# --------------------------------------------------------------------------- #

class IdGen:
    """Generate CDD-format id/oid values that do not collide with existing ones."""

    def __init__(self, text):
        self.used = set(re.findall(r"\bid=['\"]([^'\"]+)['\"]", text))
        self.used |= set(re.findall(r"\boid=['\"]([^'\"]+)['\"]", text))
        self.counter = 0

    def new_id(self):
        while True:
            self.counter += 1
            value = "_7F%014X" % self.counter
            if value not in self.used:
                self.used.add(value)
                return value

    def new_oid(self):
        while True:
            self.counter += 1
            value = "7F%030X" % self.counter
            if value not in self.used:
                self.used.add(value)
                return value


def parse_did_value(value):
    value = value.strip()
    if value.lower().startswith("0x"):
        return int(value, 16)
    if re.fullmatch(r"[0-9A-Fa-f]{4}", value):
        return int(value, 16)
    return int(value, 10)


def find_pool_did(lines, did):
    for index, raw in enumerate(lines):
        match = re.match(r"<DID id='([^']+)' oid='([^']+)' n='(\d+)'>", raw.strip())
        if match and int(match.group(3)) == did:
            return index, find_block_end(lines, index, "DID")
    raise SystemExit("DID 0x%04X not found in <DIDS>" % did)


def find_diagclass_end(lines, qual):
    for index, raw in enumerate(lines):
        if not raw.strip().startswith("<DIAGCLASS "):
            continue
        name = None
        for probe in range(index + 1, len(lines)):
            text = lines[probe].strip()
            if text.startswith("<DIAGINST ") or text == "</DIAGCLASS>":
                break
            match = re.match(r"<QUAL>([^<]+)</QUAL>", text)
            if match:
                name = match.group(1)
                break
        if name == qual:
            return find_block_end(lines, index, "DIAGCLASS")
    raise SystemExit("DIAGCLASS %s not found" % qual)


def clone_oid_ids(block_lines, gen):
    """Copy a block, regenerating every ``oid``/``id`` (dtref/attrref untouched)."""
    result = []
    for line in block_lines:
        line = re.sub(r"oid='[^']*'", lambda _: "oid='%s'" % gen.new_oid(), line)
        line = re.sub(r"\bid='[^']*'", lambda _: "id='%s'" % gen.new_id(), line)
        result.append(line)
    return result


def collect_service_info(lines, start, end):
    info = {}
    for index in range(start, end + 1):
        text = lines[index].strip()
        if not text.startswith("<SERVICE "):
            continue
        service_end = find_block_end(lines, index, "SERVICE")
        qual = None
        for probe in range(index, service_end + 1):
            match = re.match(r"<QUAL>([^<]+)</QUAL>", lines[probe].strip())
            if match:
                qual = match.group(1)
                break
        attrs = {key: attr(text, key) for key in
                 ("temploid", "tmplref", "func", "phys", "respOnPhys", "respOnFunc", "req")
                 if attr(text, key) is not None}
        info[qual] = {"attrs": attrs, "start": index, "end": service_end}
    return info


def collect_nr_block(lines, start, end, wanted):
    """Return the SIMPLECOMPCONT NR block info (proxy refs, NRC proxies) or None."""
    for index in range(start, end + 1):
        if not lines[index].strip().startswith("<SIMPLECOMPCONT"):
            continue
        block_end = find_block_end(lines, index, "SIMPLECOMPCONT")
        block = lines[index:block_end + 1]
        if "<QUAL>%s</QUAL>" % wanted not in "\n".join(block):
            continue
        spec_temploid = None
        for line in block[1:]:
            if line.strip().startswith("<SPECDATAOBJ "):
                spec_temploid = attr(line.strip(), "temploid")
                break
        refs = [match.group(1) for line in block
                for match in [re.search(r"<NEGRESCODEPROXY idref='([^']+)'", line)] if match]
        return {"proxy": attr(lines[index], "shproxyref"),
                "temploid": attr(lines[index], "temploid"),
                "spec_temploid": spec_temploid,
                "refs": refs}
    return None


def collect_did_data_proxy(lines, start, end):
    for index in range(start, end + 1):
        if not lines[index].strip().startswith("<SIMPLECOMPCONT"):
            continue
        block_end = find_block_end(lines, index, "SIMPLECOMPCONT")
        block = "\n".join(lines[index:block_end + 1])
        if "<DIDDATAREF " in block:
            return {"proxy": attr(lines[index], "shproxyref"),
                    "temploid": attr(lines[index], "temploid")}
    return None


def cmd_add_did(args):
    text, bom = read_text(args.cdd)
    lines = text.splitlines()
    dec = parse_did_value(str(args.did))
    clone_did = parse_did_value(str(args.clone))
    qual = args.qual or ("DID_%04X" % dec)
    name = args.name or ("DID_0x%04X" % dec)
    services = [s.strip() for s in args.services.split(",") if s.strip()]
    for service in services:
        if service not in ("Read", "Write"):
            raise SystemExit("--services only accepts Read/Write, got %s" % service)
    if "Read" not in services:
        services.insert(0, "Read")

    gen = IdGen(text)
    source = None
    for instance in parse_instances(lines):
        if instance_did(instance) == clone_did and (instance["class"] or "") == "Identification":
            source = instance
            break
    if source is None:
        raise SystemExit("clone DID 0x%04X has no Identification instance" % clone_did)
    src_start, src_end = source["line"], find_block_end(lines, source["line"], "DIAGINST")

    service_info = collect_service_info(lines, src_start, src_end)
    for service in services:
        if service not in service_info:
            raise SystemExit("clone DID 0x%04X has no %s service to copy" % (clone_did, service))

    pool_start, pool_end = find_pool_did(lines, clone_did)
    struct_start = next(index for index in range(pool_start, pool_end + 1)
                        if lines[index].strip() == "<STRUCTURE>")
    struct_end = next(index for index in range(struct_start, pool_end + 1)
                      if lines[index].strip() == "</STRUCTURE>")
    structure = clone_oid_ids(lines[struct_start:struct_end + 1], gen)

    data_proxy = collect_did_data_proxy(lines, src_start, src_end)
    if data_proxy is None:
        raise SystemExit("clone DID 0x%04X has no DIDDATAREF to copy" % clone_did)
    nr_blocks = {service: collect_nr_block(lines, src_start, src_end, service + "_NR")
                 for service in services}

    static_line = next(line for line in lines[src_start:src_end + 1]
                       if line.strip().startswith("<STATICVALUE "))
    static_ref = attr(static_line, "shstaticref")
    static_temploid = attr(static_line, "temploid")

    new_pool_id = gen.new_id()
    pool_block = [
        "<DID id='%s' oid='%s' n='%d'>" % (new_pool_id, gen.new_oid(), dec),
        "<NAME>",
        "<TUV xml:lang='en-US'>%s</TUV>" % name,
        "<TUV xml:lang='de-DE' uptodate='0'>DID 0x%04X</TUV>" % dec,
        "</NAME>",
        "<QUAL>%s</QUAL>" % qual,
    ] + structure + ["</DID>"]

    diag_attrs = []
    for key in ("temploid", "tmplref", "req"):
        if attr(lines[src_start], key) is not None:
            diag_attrs.append("%s='%s'" % (key, attr(lines[src_start], key)))
    inst_block = ["<DIAGINST id='%s' oid='%s' %s>"
                  % (gen.new_id(), gen.new_oid(), " ".join(diag_attrs)),
                  "<NAME>",
                  "<TUV xml:lang='en-US'>%s</TUV>" % name,
                  "<TUV xml:lang='de-DE'>%s</TUV>" % name,
                  "</NAME>",
                  "<QUAL>%s</QUAL>" % qual]
    display = {"Read": ("Read", "Lesen"), "Write": ("Write", "Schreiben")}
    for service in services:
        attrs = []
        for key in ("temploid", "tmplref", "func", "phys", "respOnPhys", "respOnFunc", "req"):
            value = service_info[service]["attrs"].get(key)
            if value is not None:
                attrs.append("%s='%s'" % (key, value))
        if args.mayBeExec:
            attrs.append("mayBeExec='%s'" % args.mayBeExec)
        label_en, label_de = display[service]
        inst_block += [
            "<SERVICE id='%s' oid='%s' %s>" % (gen.new_id(), gen.new_oid(), " ".join(attrs)),
            "<NAME>",
            "<TUV xml:lang='en-US'>%s</TUV>" % label_en,
            "<TUV xml:lang='de-DE'>%s</TUV>" % label_de,
            "</NAME>",
            "<QUAL>%s</QUAL>" % service,
            "<SHORTCUTNAME>",
            "<TUV xml:lang='en-US'>%s %s</TUV>" % (name, label_en),
            "<TUV xml:lang='de-DE'>%s %s</TUV>" % (name, label_de),
            "</SHORTCUTNAME>",
            "<SHORTCUTQUAL>%s_%s</SHORTCUTQUAL>" % (qual, service),
            "</SERVICE>",
        ]
    static_attrs = (" temploid='%s'" % static_temploid) if static_temploid else ""
    inst_block.append("<STATICVALUE oid='%s'%s shstaticref='%s' v='%d'/>"
                      % (gen.new_oid(), static_attrs, static_ref, dec))
    proxy_attrs = (" temploid='%s'" % data_proxy["temploid"]) if data_proxy["temploid"] else ""
    inst_block += [
        "<SIMPLECOMPCONT oid='%s'%s shproxyref='%s'>"
        % (gen.new_oid(), proxy_attrs, data_proxy["proxy"]),
        "<DIDDATAREF oid='%s' didRef='%s'>" % (gen.new_oid(), new_pool_id),
        "<NAME>",
        "<TUV xml:lang='en-US'>%s</TUV>" % name,
        "<TUV xml:lang='de-DE'>%s</TUV>" % name,
        "</NAME>",
        "<QUAL>%s</QUAL>" % qual,
        "</DIDDATAREF>",
        "</SIMPLECOMPCONT>",
    ]
    for service in services:
        nr = nr_blocks.get(service)
        if nr is None:
            continue
        wanted = service + "_NR"
        nr_attrs = (" temploid='%s'" % nr["temploid"]) if nr["temploid"] else ""
        spec_attrs = (" temploid='%s'" % nr["spec_temploid"]) if nr["spec_temploid"] else ""
        inst_block += [
            "<SIMPLECOMPCONT oid='%s'%s shproxyref='%s'>"
            % (gen.new_oid(), nr_attrs, nr["proxy"]),
            "<SPECDATAOBJ oid='%s'%s>" % (gen.new_oid(), spec_attrs),
            "<NAME>",
            "<TUV xml:lang='en-US'>%s</TUV>" % wanted.replace("_", " "),
            "<TUV xml:lang='de-DE'>%s</TUV>" % wanted.replace("_", " "),
            "</NAME>",
            "<QUAL>%s</QUAL>" % wanted,
            "<NEGRESCODEPROXIES>",
        ]
        inst_block += ["<NEGRESCODEPROXY idref='%s'/>" % ref for ref in nr["refs"]]
        inst_block += ["</NEGRESCODEPROXIES>", "</SPECDATAOBJ>", "</SIMPLECOMPCONT>"]
    inst_block.append("</DIAGINST>")

    dids_close = next(index for index, line in enumerate(lines) if line.strip() == "</DIDS>")
    refs_close = next(index for index, line in enumerate(lines) if line.strip() == "</DIDREFS>")
    ident_close = find_diagclass_end(lines, "Identification")
    for target, block in sorted(
            [(ident_close, inst_block),
             (refs_close, ["<DIDREF didRef='%s'/>" % new_pool_id]),
             (dids_close, pool_block)],
            key=lambda item: item[0], reverse=True):
        lines[target:target] = block

    output = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    target = args.out or args.cdd
    if not args.out and not args.no_backup:
        stamp = time.strftime("%Y%m%d_%H%M%S") + "_%03d" % (int(time.time() * 1000) % 1000)
        with open(args.cdd, "rb") as handle:
            raw = handle.read()
        with open("%s.bak_%s" % (args.cdd, stamp), "wb") as handle:
            handle.write(raw)
        print("backup: %s.bak_%s" % (args.cdd, stamp))
    write_text(target, output, bom)
    problems, _ = validate(target)
    if problems:
        print("WARNING: %s" % "; ".join(problems), file=sys.stderr)
        return 1
    print("added DID 0x%04X (QUAL %s, services %s, cloned from 0x%04X) -> %s"
          % (dec, qual, ",".join(services), clone_did, os.path.abspath(target)))
    return 0


# --------------------------------------------------------------------------- #
# CANoe.DiVa configuration: transport-layer tests
# --------------------------------------------------------------------------- #

def cmd_enable_tp(args):
    path = args.conf
    with open(path, "rb") as handle:
        raw = handle.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    state = "true" if args.state == "on" else "false"

    if re.search(r'TestTransportLayer="[^"]*"', text):
        text = re.sub(r'TestTransportLayer="[^"]*"', 'TestTransportLayer="%s"' % state, text, count=1)
    elif "<TransportLayerTests" in text:
        text = re.sub(r"<TransportLayerTests", '<TransportLayerTests TestTransportLayer="%s"' % state,
                      text, count=1)
    else:
        text = text.replace(
            "</General>",
            '  <TransportLayerTests TestTransportLayer="%s" Timeout_Bs="1000" Timeout_Cr="1000" '
            'Buffersize="4095" Half_Duplex="false" BlocksizeMayBeOne="false" />\n  </General>' % state,
            1)

    runs_on_can = None
    if args.runs_on_can:
        runs_on_can = "true" if args.runs_on_can == "on" else "false"
        if re.search(r'RunsOnCan="[^"]*"', text):
            text = re.sub(r'RunsOnCan="[^"]*"', 'RunsOnCan="%s"' % runs_on_can, text, count=1)
        else:
            text = text.replace("<General", '<General RunsOnCan="%s"' % runs_on_can, 1)

    if args.out:
        target = args.out
    else:
        target = path
        stamp = time.strftime("%Y%m%d_%H%M%S")
        with open("%s.bak_%s" % (path, stamp), "wb") as handle:
            handle.write(raw)
        print("backup: %s.bak_%s" % (path, stamp))
    with open(target, "wb") as handle:
        if bom:
            handle.write(b"\xef\xbb\xbf")
        handle.write(text.encode("utf-8"))

    try:
        ET.fromstring(text.encode("utf-8"))
    except Exception as error:  # noqa: BLE001 - report parse failure verbatim
        print("WARNING: %s" % error, file=sys.stderr)
        return 1

    current = re.search(r"<TransportLayerTests[^>]*>", text)
    print("TestTransportLayer -> %s" % state)
    if runs_on_can is not None:
        print("RunsOnCan          -> %s" % runs_on_can)
    if current is not None:
        print("element: %s" % current.group(0))
    else:
        print("WARNING: TransportLayerTests element not found after edit", file=sys.stderr)
        return 1
    effective_runs_on_can = runs_on_can
    if effective_runs_on_can is None:
        match = re.search(r'RunsOnCan="([^"]*)"', text)
        effective_runs_on_can = match.group(1) if match else None
    if state == "true" and effective_runs_on_can == "true":
        print("NOTE: DiVa 12 documents the standard transport-layer tests for classic CAN 2.0 "
              "only; with RunsOnCan=true (non-classic CAN/CAN FD) they may be rejected.")
    print("written: %s" % os.path.abspath(target))
    return 0


def cmd_remove_service(args):
    text, bom = read_text(args.cdd)
    lines = text.splitlines()
    targets = resolve_services(lines, args)
    removed = []
    for index in sorted(targets, reverse=True):
        diag_start = index
        while diag_start > 0 and not lines[diag_start].strip().startswith("<DIAGINST "):
            diag_start -= 1
        diag_end = find_block_end(lines, diag_start, "DIAGINST")
        svc_end = find_block_end(lines, index, "SERVICE")
        # matching NR block: Read -> Read_NR, Write -> Write_NR
        qual = None
        for probe in range(index, svc_end):
            match = re.search(r"<QUAL>(Read|Write|Request|Send)</QUAL>", lines[probe])
            if match is not None:
                qual = match.group(1)
                break
        wanted_nr = {"Read": "Read_NR", "Write": "Write_NR"}.get(qual or "")
        nr_range = None
        if wanted_nr:
            for probe in range(index, diag_end):
                raw = lines[probe].strip()
                if raw.startswith("<SIMPLECOMPCONT"):
                    end = find_block_end(lines, probe, "SIMPLECOMPCONT")
                    block = "\n".join(lines[probe:end + 1])
                    if "<QUAL>%s</QUAL>" % wanted_nr in block:
                        nr_range = (probe, end)
                        break
        blocks = [(index, svc_end)] + ([nr_range] if nr_range else [])
        for start, end in sorted(blocks, reverse=True):
            del lines[start:end + 1]
        removed.append(qual)
    output = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    return finish_edit(args, output, bom, "removed %d service(s): %s" % (len(removed), ", ".join(str(item) for item in removed)))


def cmd_remove_instance(args):
    text, bom = read_text(args.cdd)
    lines = text.splitlines()
    did = int(args.did, 16) if args.did.lower().startswith("0x") else int(args.did, 16)
    instances = parse_instances(lines)
    target = None
    for instance in instances:
        if instance_did(instance) == did:
            target = instance
            break
    if target is None:
        raise SystemExit("instance not found for DID 0x%04X" % did)
    end = find_block_end(lines, target["line"], "DIAGINST")
    instance_id = target["id"]
    del lines[target["line"]:end + 1]
    refs = 0
    kept = []
    for raw in lines:
        if raw.strip().startswith("<DIAGINSTREF ") and attr(raw.strip(), "idref") == instance_id:
            refs += 1
            continue
        kept.append(raw)
    output = "\n".join(kept) + ("\n" if text.endswith("\n") else "")
    return finish_edit(args, output, bom, "removed instance %s (DID 0x%04X, %d variant ref(s))" % (instance_id, did, refs))


def finish_edit(args, output, bom, message):
    target = args.out or args.cdd
    if not args.out and not args.no_backup:
        stamp = time.strftime("%Y%m%d_%H%M%S") + "_%03d" % (int(time.time() * 1000) % 1000)
        backup = "%s.bak_%s" % (args.cdd, stamp)
        with open(args.cdd, "rb") as source:
            raw = source.read()
        with open(backup, "wb") as handle:
            handle.write(raw)
        print("backup: %s" % backup)
    write_text(target, output, bom)
    problems, _ = validate(target)
    if problems:
        print("WARNING: %s" % "; ".join(problems), file=sys.stderr)
        return 1
    print("%s -> %s" % (message, os.path.abspath(target)))
    return 0


def cmd_validate(args):
    problems, _ = validate(args.cdd)
    if not problems:
        print("OK: %s is well-formed and has no dangling DIAGINSTREF" % os.path.abspath(args.cdd))
        return 0
    for problem in problems:
        print("FAIL: %s" % problem)
    return 1


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect and edit CANdela CDD files.")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="print state groups, DID/service matrix and statistics")
    inspect.add_argument("cdd")
    inspect.add_argument("--json", action="store_true", help="machine-readable output")
    inspect.set_defaults(func=cmd_inspect)

    dtc = sub.add_parser("dtc", help="list DTC records")
    dtc.add_argument("cdd")
    dtc.set_defaults(func=cmd_dtc)

    state_model = sub.add_parser("state-model",
                                 help="print sessions/security model, reachable states and DID preconditions")
    state_model.add_argument("cdd")
    state_model.add_argument("--json", action="store_true", help="machine-readable output")
    state_model.set_defaults(func=cmd_state_model)

    for name, func in (("set-states", cmd_set_states), ("remove-service", cmd_remove_service),
                       ("set-trans", cmd_set_trans)):
        edit = sub.add_parser(name)
        edit.add_argument("cdd")
        edit.add_argument("--service-id", help="SERVICE id attribute")
        edit.add_argument("--did", help="DID number, e.g. 0xF187 or F187")
        edit.add_argument("--inst", help="diagnostic instance QUAL (e.g. SeedLevel3, DefaultSession)")
        edit.add_argument("--qual", help="service qualifier: Read, Write, Request, Send, Start, ...")
        if name == "set-states":
            edit.add_argument("--states", required=True, type=lambda value: [int(item) for item in value.replace("(", "").replace(")", "").split(",") if item.strip()], help="state indexes, e.g. 3,6")
        if name == "set-trans":
            edit.add_argument("--trans", required=True, help="transition pairs, e.g. (5,6) or (1,3,3,3,6,5,7,5)")
        edit.add_argument("--out", help="write to a copy instead of in place")
        edit.add_argument("--no-backup", action="store_true", help="do not create a .bak file for in-place edits")
        edit.set_defaults(func=func)

    add_did = sub.add_parser("add-did", help="add a new DID by cloning an existing one")
    add_did.add_argument("cdd")
    add_did.add_argument("--did", required=True, help="new DID number, e.g. 0xF0F0")
    add_did.add_argument("--clone", required=True, help="existing DID to clone data structure/services from")
    add_did.add_argument("--name", help="DID name (en-US TUV of NAME)")
    add_did.add_argument("--qual", help="instance QUAL, e.g. F0F0 or _5014")
    add_did.add_argument("--services", default="Read", help="comma list of services to copy: Read[,Write]")
    add_did.add_argument("--mayBeExec", help="optional mayBeExec for the new services, e.g. '(3,6)'")
    add_did.add_argument("--out", help="write to a copy instead of in place")
    add_did.add_argument("--no-backup", action="store_true")
    add_did.set_defaults(func=cmd_add_did)

    enable_tp = sub.add_parser("enable-tp", help="toggle CANoe.DiVa TransportLayerTests / RunsOnCan")
    enable_tp.add_argument("conf", help="DiVa project configuration (*.conf)")
    enable_tp.add_argument("--state", choices=("on", "off"), default="on")
    enable_tp.add_argument("--runs-on-can", choices=("on", "off"), help="also set General/@RunsOnCan")
    enable_tp.add_argument("--out", help="write to a copy instead of in place")
    enable_tp.set_defaults(func=cmd_enable_tp)

    remove_instance = sub.add_parser("remove-instance", help="delete a DID instance and its variant references")
    remove_instance.add_argument("cdd")
    remove_instance.add_argument("--did", required=True)
    remove_instance.add_argument("--out", help="write to a copy instead of in place")
    remove_instance.add_argument("--no-backup", action="store_true")
    remove_instance.set_defaults(func=cmd_remove_instance)

    validate = sub.add_parser("validate", help="check XML well-formedness and references")
    validate.add_argument("cdd")
    validate.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
