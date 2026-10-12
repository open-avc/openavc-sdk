"""Vendor the canonical OpenAVC validator for offline SDK builds."""
import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess

FILES = ["__init__.py", "drivers/__init__.py", "drivers/spec.py",
         "drivers/avcdriver_semantic.py", "drivers/equipment_protocol.py",
         "utils/__init__.py", "utils/regex_safety.py"]


def literal_constant(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return literal_constant(node.left) * literal_constant(node.right)
    return ast.literal_eval(node)


def required_files(core, revision):
    """Find static canonical dependencies from committed bytes, without imports.

    Refuse package/relative imports that need a wider dependency review. The
    validator currently imports only named leaf modules and the Python stdlib.
    """
    import sys

    pending = list(FILES)
    found = set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        source = subprocess.check_output([
            "git", "-C", str(core), "show", f"{revision}:openavc/{name}",
        ])
        found.add(name)
        tree = ast.parse(source, filename=name)
        if name == "__init__.py":
            # The canonical package's lazy scripting API is outside the
            # validator. Keep its bytes intact, but do not package the server
            # merely because __getattr__ can load scripting names on demand.
            tree.body = [node for node in tree.body if not (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "__getattr__"
            )]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    raise ValueError(f"Review relative contract import in {name}")
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [entry.name for entry in node.names]
            else:
                continue
            for module in modules:
                if module.startswith("openavc."):
                    path = module.removeprefix("openavc.").replace(".", "/") + ".py"
                    pending.append(path)
                    # Repository paths for `git show`, so forward slashes on
                    # every platform.
                    parent = PurePosixPath(path).parent
                    while parent != PurePosixPath("."):
                        pending.append(str(parent / "__init__.py"))
                        parent = parent.parent
                elif module == "openavc" or module.split(".")[0] not in sys.stdlib_module_names:
                    raise ValueError(f"Review non-leaf or external contract import {module!r} in {name}")
    return sorted(found)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--check", action="store_true", help="Compare without writing files.")
    parser.add_argument("--check-upstream", action="store_true", help="Compare contract bytes without requiring the same commit ID.")
    args = parser.parse_args()
    args.check = args.check or args.check_upstream
    output = Path(__file__).resolve().parents[1] / "sdk/contract"
    record = {"repository": "https://github.com/open-avc/openavc",
              "core_head": subprocess.check_output(["git", "-C", str(args.core), "rev-parse", args.revision + "^{commit}"], text=True).strip(),
              "files": {}}
    names = required_files(args.core, record["core_head"])
    changed = []
    for name in names:
        target = output / "openavc" / name
        committed = subprocess.check_output(["git", "-C", str(args.core), "show", f"{record['core_head']}:openavc/{name}"])
        record["files"][name] = {"sha256": hashlib.sha256(committed).hexdigest()}
        if not target.is_file() or target.read_bytes() != committed:
            changed.append(name)
            if not args.check:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(committed)
    for path in (output / "openavc").rglob("*.py"):
        if path.relative_to(output / "openavc").as_posix() not in names:
            changed.append(path.relative_to(output).as_posix())
            if not args.check:
                path.unlink()
    origin = output / "origin.json"
    source = subprocess.check_output(["git", "-C", str(args.core), "show",
                                      f"{record['core_head']}:openavc/drivers/equipment_protocol.py"])
    constants = {node.targets[0].id: literal_constant(node.value) for node in ast.parse(source).body
                 if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    header = ('/* Generated from OpenAVC equipment_protocol.py. Do not edit. */\n'
              '#ifndef OPENAVC_PROTOCOL_H\n#define OPENAVC_PROTOCOL_H\n'
              f'#define OAVC_PROTOCOL {json.dumps(constants["PROTOCOL"])}\n'
              f'#define OAVC_MAX_MESSAGE_BYTES {constants["MAX_MESSAGE_BYTES"]}U\n#endif\n')
    target = output.parent / "include/protocol.h"
    if not target.exists() or target.read_text(encoding="utf-8") != header:
        changed.append("sdk/include/protocol.h")
        if not args.check:
            target.write_text(header, encoding="utf-8", newline="\n")
    target = output.parent.parent / 'openavc_oem/_protocol.py'
    generated = ('"""Generated from the pinned OpenAVC equipment protocol."""\n'
                 + '\n'.join(f'{key} = {value!r}' for key, value in constants.items()) + '\n')
    if not target.exists() or target.read_text(encoding="utf-8") != generated:
        changed.append('openavc_oem/_protocol.py')
        if not args.check:
            target.write_text(generated, encoding="utf-8", newline="\n")
    wire = ['"""Generated DNS wire helpers from the pinned OpenAVC sources."""',
            'import struct', 'import socket', '']
    record['dns_sources'] = {}
    for module, wanted in {
        'mdns_scanner.py': {'encode_dns_name', 'decode_dns_name'},
        'mdns_advertiser.py': {'encode_txt_rdata', '_build_resource_record',
                              'build_announcement_records', 'build_dns_response', '_parse_query_questions'},
    }.items():
        path = 'openavc/discovery/' + module
        source = subprocess.check_output(['git', '-C', str(args.core), 'show', f"{record['core_head']}:{path}"]).decode()
        record['dns_sources'][path] = hashlib.sha256(source.encode()).hexdigest()
        tree = ast.parse(source)
        constant_names = ({'DNS_TYPE_A', 'DNS_TYPE_PTR', 'DNS_TYPE_SRV', 'DNS_TYPE_TXT', 'DNS_CLASS_IN'}
                          if module == 'mdns_scanner.py' else {'DEFAULT_TTL'})
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                       and isinstance(node.targets[0], ast.Name) and node.targets[0].id in constant_names]
        if {node.targets[0].id for node in assignments} != constant_names:
            raise ValueError('Review changed OpenAVC DNS constants before regenerating.')
        wire.extend(ast.get_source_segment(source, node) + '\n' for node in assignments)
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
        if {node.name for node in nodes} != wanted:
            raise ValueError('Review changed OpenAVC DNS helpers before regenerating.')
        wire.extend(ast.get_source_segment(source, node) + '\n\n' for node in nodes)
    target = output.parent.parent / 'openavc_oem/_dns_wire.py'
    generated = '\n'.join(wire)
    if not target.exists() or target.read_text(encoding="utf-8") != generated:
        changed.append('openavc_oem/_dns_wire.py')
        if not args.check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(generated, encoding="utf-8", newline="\n")
    metadata = json.dumps(record, indent=2) + "\n"
    if not args.check_upstream and (not origin.is_file() or origin.read_text(encoding="utf-8") != metadata):
        changed.append("origin.json")
        if not args.check:
            origin.write_text(metadata, encoding="utf-8", newline="\n")
    print(json.dumps({"files": len(names), "core_head": record["core_head"], "changed": changed}))
    if args.check and changed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
