# Connect your hardware

Start with `product.json` and `sample/example.c`. The C++ example exposes the same
hardware callbacks. Both examples contain an invented amplifier; their stdin
commands and counters exercise the integration without connecting to equipment.

## Describe the product

Edit `product.json` to name your product, commands, state and channels. For example:

```json
"set_gain": {
  "label": "Set Gain",
  "params": {
    "channel": {"type": "child_id", "child_type": "channel", "required": true},
    "gain": {"type": "number", "min": -80, "max": 12, "required": true}
  }
}
```

The build validates this description with OpenAVC's canonical driver validator,
then generates `build/generated/commands.h`, `commands.c` and `model.json`.
Keep those generated files together. Do not edit them. The runtime refuses a
model that differs from the model compiled into the hardware adapter.

Use a stable model `id` across firmware updates that describe the same product.
Supply a separate, stable identity for each physical unit when opening the
adapter. Do not put a serial number into the shared model ID or distribute one
physical identity to every product in a firmware image.

Command parameters map to these C types:

| Product type | Required callback argument |
|---|---|
| `boolean` | `int`, zero or one |
| `integer` | `int64_t` |
| `number` | `double` |
| `string` | `const char *` |
| `enum` | Generated enum; values retain their original type on the network |
| `child_id` | `int64_t` or `const char *`, following that channel type's `id_format` |

Optional arguments use a generated struct with `present` and `value` members.
Check `present` before using `value`. An optional default is applied before the
callback. A required parameter must still be supplied even if its description
contains a default. Labels describe choices in the Programmer; enum constants
represent their underlying values. A parameter with `trim: false` preserves text.

## Implement the callbacks

Implement the `oavc_hw_*` functions declared in the generated header using your
existing product APIs. Do not implement HTTP, JSON command parsing, retries or a
second OpenAVC driver.

Return `OAVC_DONE` only after the operation completes. Return `OAVC_REFUSED` if
no hardware operation started, or `OAVC_FAILED` when an attempted operation has
finished unsuccessfully. For asynchronous work, retain the operation token,
return `OAVC_PENDING`, and later call `oavc_complete(token, OAVC_DONE)` or
`oavc_complete(token, OAVC_FAILED)`. Complete each accepted operation once.

A timeout does not cancel hardware work. Continue tracking an accepted
operation until its actual completion. Do not resend a command merely because
the caller disconnected or did not receive a response.

## Use your product's thread

The adapter's `post` callback puts the supplied work function and token onto
your product's event queue. Return nonzero only when the queue accepted that
work. Invoke the function once on the thread allowed to access your hardware.
The supplied C and C++ examples demonstrate a product event loop and delayed
completion on that same thread.

Call `oavc_adapter_refresh` on the product thread. It calls your `readback`
function and copies its snapshot for the networking service. Choose
`feedback_max_age_ms` to match your product's measured refresh interval; refresh
before that interval expires. Expired or failed readback is unavailable.

The low-level functions in `endpoint.h` call hardware functions on networking
worker threads. Use `adapter.h` and generated callbacks for integration with an
existing product event loop.

## Report actual state

Your `readback` function reports every declared product state and every present
channel through the generated `oavc_state_*` functions. Report what the hardware
actually says, including changes from your front panel or product software.
Returning success from a command is not a substitute for reading its state.

Call `oavc_snapshot_revision(snapshot, generation, revision)` for each readback.
Use a nonzero generation that changes when your hardware session is reset, and
advance the revision when reported state changes. A snapshot replaces the
previous channel roster, so a removed channel must be absent from the next
complete snapshot. Return nonzero from `readback` when current state is unavailable.

State strings are copied by each publisher call. Command arguments remain
borrowed until the callback returns or its asynchronous operation completes,
whichever is later. Do not retain them past that point.

## Select how it runs

With a `bridge_path`, the adapter provides a local Unix socket inside your
product process. The supplied Linux `example-service` process handles networking
and uses that socket. The SDK supplies both sides of the process bridge.
Keep the socket in a directory owned by the product's service account.

Without a `bridge_path`, networking runs inside the product process. Supply
the local endpoint's token and certificate in `network`; the adapter then
reports its bound loopback port through `oavc_adapter_port`.

Use the same model and callbacks for either form. Keep the same hardware mapping
when adding the optional full OpenAVC server.

## Stop and inspect

Call `oavc_adapter_stop`, continue processing accepted product work, and call
`oavc_adapter_try_close` from the lifecycle owner thread until it returns one.
Only then release the hardware context. Do not block the hardware event loop
waiting for a callback that needs that event loop to finish.

After editing the model or hardware mapping:

```sh
python3 try-example.py --build-only
python3 try-example.py --inspect
```

`--build-only` validates and compiles without starting hardware. `--inspect`
starts the selected integration and reads its description and state; it sends
no hardware commands. Inspect the JSON report and endpoint log under `build/`.
Use `--exercise-demo` only with the unchanged amplifier example: it sends that
example's commands and checks its independent counters.
