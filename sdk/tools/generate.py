"""Generate hardware callbacks and state publishers from an OpenAVC model."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from state_mapping import publishers


def identifier(value, where):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: use a nonempty name.")
    name = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not name:
        name = "value_" + value.encode("utf-8").hex()
    if name[0].isdigit():
        name = "value_" + name
    return name


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key!r}; keep one definition.")
        result[key] = value
    return result


def generate(model_path, core, out):
    sys.path.insert(0, str(core.resolve()))
    from openavc.drivers import avcdriver_semantic

    validator_path = Path(avcdriver_semantic.__file__).resolve()
    if not validator_path.is_relative_to(core.resolve()):
        raise ValueError("Validator came from a different OpenAVC checkout.")
    model_bytes = model_path.read_bytes()
    model = json.loads(model_bytes, object_pairs_hook=unique_fields)
    errors = avcdriver_semantic.validate_driver_definition(model, strict=True)
    if errors:
        raise ValueError("OpenAVC model validation failed:\n" + "\n".join(errors))
    header = ["/* Generated from the equipment model. Do not edit. */",
              "#ifndef OAVC_GENERATED_COMMANDS_H", "#define OAVC_GENERATED_COMMANDS_H",
              '#include "endpoint.h"', '#include "outcome.h"', "#ifdef __cplusplus", 'extern "C" {', "#endif",
              "/* Hardware callbacks return the product result. Arguments are borrowed",
              " * until completion or callback return, whichever is later. Readback is",
              " * published on the product thread. Implement every callback declared below. */"]
    source = ["/* Generated from the equipment model. Do not edit. */",
              '#include "commands.h"', "#include <string.h>"]
    branches = []
    symbols = {"oavc_dispatch", "oavc_model_sha256", "oavc_adapter_open"}

    def declare(symbol, where):
        if symbol in symbols:
            raise ValueError(f"{where}: generated C name {symbol!r} collides; use distinct model names.")
        symbols.add(symbol)

    for command, definition in model.get("commands", {}).items():
        command_name = identifier(command, f"commands.{command}")
        declare("oavc_hw_" + command_name, f"commands.{command}")
        signature = ["void *context", "oavc_token operation"]
        arguments = ["context", "operation"]
        branch = [f"    if (!strcmp(name, {json.dumps(command)})) {{"]
        for parameter, rule in definition.get("params", {}).items():
            where = f"commands.{command}.params.{parameter}"
            parameter_name = identifier(parameter, where)
            kind = rule.get("type", "string")
            if kind == "child_id":
                kind = model["child_entity_types"][rule["child_type"]].get("id_format", {}).get("type", "integer")
            variable = "p_" + parameter_name
            optional = rule.get("required") is not True
            destination = variable + ".value" if optional else variable
            conversion = []
            types = {"number": "double", "integer": "int64_t", "boolean": "int", "string": "const char *"}
            if kind == "enum":
                enum_type = f"oavc_{command_name}_{parameter_name}"
                declare(enum_type, where)
                choices = [v["value"] if isinstance(v, dict) else v for v in rule.get("values", [])]
                if not choices:
                    raise ValueError(f"{where}.values: supply at least one enum value.")
                if len(choices) != len({json.dumps(v) for v in choices}):
                    raise ValueError(f"{where}.values: duplicate enum values cannot be mapped.")
                constants = []
                for choice in choices:
                    choice_name = identifier(str(choice), f"{where}.values[{choice!r}]")
                    constants.append(f"OAVC_{command_name}_{parameter_name}_{choice_name}".upper())
                    declare(constants[-1], where)
                header += ["typedef enum {", ",\n".join("    " + c for c in constants), f"}} {enum_type};"]
                value_type = enum_type
                for index, (choice, constant) in enumerate(zip(choices, constants)):
                    prefix = "if" if index == 0 else "else if"
                    conversion.append(f"        {prefix} (oavc_parameter_equals_json(request, {json.dumps(parameter)}, {json.dumps(json.dumps(choice))})) {destination} = {constant};")
                conversion.append("        else return OAVC_REFUSED;")
            elif kind in types:
                value_type = types[kind]
                conversion += [f"        if (oavc_parameter_{kind}(request, {json.dumps(parameter)}, &{destination})) return OAVC_REFUSED;"]
            else:
                raise ValueError(f"{where}.type: {kind!r} has no C callback mapping.")
            if optional:
                optional_type = f"oavc_{command_name}_{parameter_name}_optional"
                declare(optional_type, where)
                header.append(f"typedef struct {{ int present; {value_type} value; }} {optional_type};")
                signature.append(f"{optional_type} {variable}")
                branch += [f"        {optional_type} {variable} = {{0}};",
                           f"        if (oavc_parameter_present(request, {json.dumps(parameter)})) {{",
                           f"            {variable}.present = 1;"]
                branch += conversion
                branch.append("        }")
            else:
                signature.append(f"{value_type} {variable}")
                branch += [f"        {value_type} {variable};", *conversion]
            arguments.append(variable)
        header.append(f"oavc_outcome oavc_hw_{command_name}({', '.join(signature)});")
        branch += [f"        return oavc_hw_{command_name}({', '.join(arguments)});", "    }"]
        branches.extend(branch)
    state_header, state_source = publishers(model, identifier, declare)
    header += state_header
    source += state_source
    header += ["oavc_outcome oavc_dispatch(void *, oavc_token, const oavc_command *);",
               "extern const char oavc_model_sha256[];",
               "/* Checks that the bundled model matches this generated adapter. */",
               "int oavc_check_model(const oavc_endpoint_config *, char *, size_t);",
               "#ifdef __cplusplus", "}", "#endif", "#endif", ""]
    source += [f'const char oavc_model_sha256[] = "{hashlib.sha256(model_bytes).hexdigest()}";',
               "oavc_outcome oavc_dispatch(void *context, oavc_token operation, const oavc_command *request) {",
               "    const char *name = oavc_command_name(request);",
               "    (void)context; (void)operation; (void)name;", *branches, "    return OAVC_REFUSED;", "}", ""]
    generated = {"commands.h": "\n".join(header), "commands.c": "\n".join(source)}
    contract_files = ["drivers/avcdriver_semantic.py", "drivers/spec.py", "utils/regex_safety.py"]
    record = {
        "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "state_generator_sha256": hashlib.sha256(Path(__file__).with_name("state_mapping.py").read_bytes()).hexdigest(),
        "contract_hashes": {p: hashlib.sha256((core / "openavc" / p).read_bytes()).hexdigest() for p in contract_files},
        "generated_hashes": {name: hashlib.sha256(value.encode()).hexdigest() for name, value in generated.items()},
    }
    out.mkdir(parents=True, exist_ok=True)
    for name, value in generated.items():
        (out / name).write_text(value, encoding="utf-8")
    (out / "provenance.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        generate(args.model, args.core, args.out)
    except (ValueError, OSError, ImportError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
