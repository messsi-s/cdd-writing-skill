#!/usr/bin/env python3
"""cdd_tool.py — inspect and edit CANdela CDD files for diagnostic test alignment.

Subcommands
    inspect          state groups, DID/service matrix, statistics
    dtc              DTC list (code, failure type byte, description)
    set-states       set a service's mayBeExec state indexes
    remove-service   delete a service (Read/Write/...) and its NR block
    remove-instance  delete a whole DID instance and its variant references
    validate         XML well-formedness + dangling DIAGINSTREF check

Everything works line-by-line on the original text so embedded base64 payloads and
formatting are preserved. In-place edits create ``<file>.bak_<timestamp>`` first.
"""

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

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

    for name, func in (("set-states", cmd_set_states), ("remove-service", cmd_remove_service)):
        edit = sub.add_parser(name)
        edit.add_argument("cdd")
        edit.add_argument("--service-id", help="SERVICE id attribute")
        edit.add_argument("--did", help="DID number, e.g. 0xF187 or F187")
        edit.add_argument("--qual", help="service qualifier: Read, Write, Request, Send")
        if name == "set-states":
            edit.add_argument("--states", required=True, type=lambda value: [int(item) for item in value.replace("(", "").replace(")", "").split(",") if item.strip()], help="state indexes, e.g. 3,6")
        edit.add_argument("--out", help="write to a copy instead of in place")
        edit.add_argument("--no-backup", action="store_true", help="do not create a .bak file for in-place edits")
        edit.set_defaults(func=func)

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
