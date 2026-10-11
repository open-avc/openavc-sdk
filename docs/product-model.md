# Describe your product

Edit `product.json`, then build the adapter. This file describes what OpenAVC can
control and read. Your hardware callbacks implement those operations. You do not
write a network driver or publish a separate driver catalog entry for the product.

The build validates the description against the pinned OpenAVC capability rules
and generates typed C callbacks and state publishers. Use `--build-only` after
each model edit to see errors before starting hardware. The generated
`build/generated/model.json` includes SDK connection information; distribute it
with the adapter built from the same input. Do not edit that generated file.

## Product information

| Field | Use |
|---|---|
| `id` | Required stable model identifier, for example `acme_amplifier`. Use lowercase letters, digits and underscores. This is not a unit serial number. |
| `name` | Required product display name. |
| `manufacturer` | Manufacturer name displayed in OpenAVC. Required for the managed product's discovery description. |
| `category` | OpenAVC category, such as `audio`, `displays` or `switchers`. |
| `version` | Semantic version of this capability description, such as `1.0.0`. Separate from hardware identity and the equipment protocol identifier. |
| `author` | Author of the integration. |
| `source_url` | HTTP(S) product or integration documentation URL. |
| `description` | Short description of the controls this model exposes. |
| `tags`, `compatible_models` | Optional canonical OpenAVC search/model metadata. |

Keep the physical unit identity in runtime configuration, separate from the shared
model. A family can share a model when its described controls are compatible.
Qualify changed capabilities against existing projects before deploying an update.

## Product state

`state_variables` is an object keyed by state name:

```json
"state_variables": {
  "input": {"type": "enum", "values": ["analog", "network"], "label": "Input"},
  "fault": {"type": "boolean", "label": "Hardware Fault"},
  "temperature": {"type": "number", "unit": "C", "label": "Temperature"}
}
```

Use `boolean`, `integer`, `number`, `string` or `enum`. State also accepts the
canonical `float` type, mapped to C `double`. `label` and `unit` describe a value
in the Programmer. Numeric `min`/`max` describe its range. An enum's `values` can
contain scalars or `{ "value": ..., "label": ... }` entries; labels and values
are separate. Generated enums preserve the original JSON value.

Publish every declared state during readback, including changes made outside
OpenAVC. A default in the description does not replace actual hardware feedback.
Values are scalars; do not publish nested objects or arrays as hardware state.
See the [adapter reference](adapter-api.md#publish-state-and-channels) for value
bounds, freshness, generation/revision and unavailable readback.

## Channels and other addressable parts

OpenAVC calls addressable parts `child entities`. Describe each kind under
`child_entity_types`, then publish the parts currently present:

```json
"child_entity_types": {
  "channel": {
    "label": "Channel",
    "id_format": {"type": "integer", "min": 1, "max": 4},
    "state_variables": {
      "gain": {"type": "number", "min": -80, "max": 12, "unit": "dB"},
      "mute": {"type": "boolean"}
    }
  }
}
```

The model describes a channel type; readback supplies the current roster.
An amplifier with two present channels publishes IDs 1 and 2 even if the family
supports up to four. Removing a channel from a complete snapshot removes it from
that reported roster. Use `id_format.type: "string"` for named IDs such as
`Main-L`; do not change an ID's type between snapshots.

## Commands and parameters

`commands` is an object keyed by operation name. Each operation has `params`,
an object keyed by parameter name:

```json
"commands": {
  "set_gain": {
    "label": "Set Gain",
    "params": {
      "channel": {"type": "child_id", "child_type": "channel", "required": true},
      "gain": {"type": "number", "min": -80, "max": 12, "required": true}
    }
  }
}
```

| Parameter field | Meaning |
|---|---|
| `type` | `boolean`, `integer`, `number`, `string`, `enum` or `child_id` for generated C callbacks. Omitted type defaults to string. |
| `required` | The caller must supply this value. A default does not make a required value optional. |
| `default` | Value supplied when an optional parameter is absent. |
| `label`, `help` | Name and explanation for the Programmer's input. |
| `min`, `max` | Numeric bounds. |
| `values` | Enum values, optionally paired with display labels. |
| `child_type` | Declared channel type for a `child_id` parameter. Its ID format determines the generated C argument type. |
| `trim` | Canonical string-input behavior in OpenAVC. `false` retains surrounding whitespace; the hardware callback receives the submitted value. |

Optional values use a generated `present`/`value` struct. The compiler rejects a
type with no C callback mapping and reports generated-name collisions. Implement
every callback declared in `commands.h`; a missing implementation fails the link.

Commands can also carry the canonical `description`, `help`, `sets`, `query_for`,
`confirm` and `restarts_device_for` metadata. These describe the command to OpenAVC;
they do not execute your hardware operation or replace its completion/readback.
Transport fields such as `send`, `method` and `path` are supplied by the SDK and
are refused in the product command description.

## Settings and common actions

The product file also accepts canonical `device_settings`, `quick_actions`,
`actions` and `capabilities`. Use these to describe how existing states and
commands appear in OpenAVC. They retain the normal OpenAVC field meanings and
validation; they do not add another firmware callback mechanism.

For their full structures, use the OpenAVC
[driver authoring reference](https://github.com/open-avc/openavc/blob/main/docs/creating-drivers.md).
The exact rules used by your SDK build are included in
[`sdk/contract/openavc/drivers/spec.py`](../sdk/contract/openavc/drivers/spec.py)
and its semantic validator, with source hashes in
[`sdk/contract/origin.json`](../sdk/contract/origin.json). Documentation on OpenAVC's
main branch can describe newer fields; the build's validator and generated
header determine what your selected SDK supports.

The supported top-level product fields are the metadata listed above,
`state_variables`, `child_entity_types`, `commands`, `device_settings`,
`quick_actions`, `actions` and `capabilities`. Connection settings, polling,
parsers and protocol templates belong to the SDK; omit them from `product.json`.

## Inspect and test the result

Run the read-only inspector after building, then create checks using commands
and state from your model. The [product testing guide](product-testing.md) shows
the exact commands and expected report coverage. Confirm the resulting labels,
channels, commands and settings through the normal OpenAVC Programmer as well.
Build validation and typed callbacks do not prove the physical mapping is correct.
