# Hardware adapter API

Include `adapter.h` and your build's generated `commands.h`. Implement the
`oavc_hw_*` callbacks declared in that generated header. The same C interface is
available to C++ with C linkage. The [C example](../sample/example.c) demonstrates
the complete event loop and shutdown sequence; source checkouts also provide
`sample/example.cpp`.

The public declarations are in [adapter.h](../sdk/include/adapter.h),
[outcome.h](../sdk/include/outcome.h) and [endpoint.h](../sdk/include/endpoint.h).
Use the generated state publishers and command callbacks for normal integration.
The SDK's runtime, lifecycle and process-bridge headers are implementation
interfaces, not additional firmware components you must implement.

## Configuration

`oavc_adapter_config` contains:

| Member | Contract |
|---|---|
| `network.abi` | Set to `1`. |
| `network.size` | Set to `sizeof(oavc_endpoint_config)` from the headers used to build your adapter. |
| `network.identity` | Nonempty, stable identity of this physical unit. Give separate units different identities. |
| `network.model_path` | Generated `model.json` built with this adapter. It must remain unchanged while opening. |
| `network.token` | Local endpoint bearer credential in the in-process form. The separate service receives its own credential from product management. |
| `network.certificate_path` | PEM certificate and private key for the in-process endpoint. The separate service receives its certificate path at startup. |
| `network.context`, `network.readback`, `network.command` | Leave null when using the combined adapter. It supplies these low-level callbacks. |
| `bridge_path` | Non-null selects the Linux Unix-socket connection to the supplied service. Null selects networking inside your process. |
| `hardware` | Your hardware context, passed to `readback`, `post` and generated hardware callbacks. Keep it alive until close succeeds. |
| `readback` | `int (*)(void *hardware, oavc_snapshot *snapshot)`. Publish a complete snapshot; return zero for successful readback, nonzero when current feedback is unavailable. |
| `post` | `int (*)(void *hardware, void (*work)(oavc_token), oavc_token token)`. Queue `work(token)` onto the product's permitted thread and return nonzero only if accepted. |
| `feedback_max_age_ms` | Positive freshness deadline. Call refresh often enough for your actual readback duration and scheduling jitter. |

`post` can be called from an SDK worker thread. Make queue admission thread-safe,
return promptly, and invoke each accepted work item once. Do not run hardware
code on that worker unless your hardware API explicitly permits it. Once accepted,
continue servicing the product queue during shutdown.

The product and separate device service must run as the same OS user. Keep the
Unix socket in a private directory owned by that account. The SDK checks peer
credentials. See [firmware startup](firmware.md) for a working layout.

## Lifecycle calls

| Function | Result and use |
|---|---|
| `oavc_adapter_open(config, error, error_size)` | Returns an adapter or null. Supply a zero-initialized error buffer for available failure detail. Verifies ABI and the exact generated model hash before opening the connection. |
| `oavc_adapter_port(adapter)` | Returns the bound loopback HTTPS port in the in-process form; zero for the Unix-socket form or a null adapter. |
| `oavc_adapter_refresh(adapter)` | Call on the permitted product thread. Calls your readback, copies the snapshot and records its observation time using a monotonic clock. Returns nonzero while the copied feedback is current, otherwise zero. |
| `oavc_adapter_stop(adapter)` | Begins shutdown without blocking the hardware event loop. Stops admitting new work and marks feedback unavailable. Safe to call repeatedly before close. |
| `oavc_adapter_try_close(adapter)` | Call from the lifecycle owner while continuing to process accepted work. Returns zero while draining; returns one after releasing the adapter. Clear your pointer immediately after one. Never use the released pointer again. A null pointer returns one. |

Refresh does not run periodically by itself. A successful command does not refresh
state. A slow readback consumes part of the freshness interval, which starts when
readback begins. Report failures instead of repeatedly publishing a stale cached
value as new feedback. The endpoint also validates the published fields against
the model; a refresh return value is not a substitute for that validation.

Do not destroy your queue or hardware context while SDK work still references
them. Do not block the hardware thread waiting for close: it may need that same
thread to finish an accepted operation. The example's daemon mode handles signals
by requesting shutdown on its event loop; it does not call the SDK from a signal
handler.

## Hardware commands and completion

Each generated callback receives your `hardware` context, an `oavc_token`, then
the declared command parameters in model order. The generated header is the
exact prototype to implement for your build.

| Callback result | Meaning |
|---|---|
| `OAVC_DONE` | Hardware operation completed successfully. |
| `OAVC_FAILED` | The attempted operation finished unsuccessfully. |
| `OAVC_REFUSED` | No hardware operation started. |
| `OAVC_PENDING` | Work was accepted and will complete asynchronously. Retain the operation token. |

For asynchronous work, call `oavc_complete(token, OAVC_DONE)` or
`oavc_complete(token, OAVC_FAILED)` once the actual result is known. It returns
one if that completion was accepted, zero for an invalid result, unknown token
or already-completed operation. It is synchronized and can be called from a
completion thread. Keep your own hardware-thread requirements intact.

`OAVC_QUEUED`, `OAVC_UNCERTAIN` and `OAVC_EXPIRED` are lifecycle observation values;
do not return them as normal hardware callback results. A thrown callback or an
unexpected nonterminal result cannot establish that hardware work was absent.
Do not throw exceptions across the C interface.

Tokens are opaque operation identifiers, not pointers, network request IDs or
persistent work records. Do not reuse a token for another operation. A client
disconnect or response timeout does not cancel accepted hardware work. Neither
the SDK nor your application should resend it merely because its reply was lost.

## Generated parameter types

| Model parameter | Required C argument |
|---|---|
| `boolean` | `int`, zero or one |
| `integer` | `int64_t` |
| `number` | `double` |
| `string` | Borrowed `const char *` |
| `enum` | Generated enum selecting the original JSON value |
| `child_id` | `int64_t` or `const char *` according to the referenced channel's ID format |

An optional parameter is a generated struct with `present` and `value` members.
Check `present` before reading `value`. Defaults are applied by the endpoint
before dispatch. Required parameters remain required even when a default exists.
Generated symbols normalize names for C and reject collisions at build time.
Use the header's symbol spelling rather than constructing it in application code.

Arguments, including strings, remain borrowed until callback return or
asynchronous completion, whichever is later. Copy anything your application
needs to retain beyond that point. A callback completing inline still retains
its arguments until it returns.

## Publish state and channels

The SDK supplies `oavc_snapshot *` to your readback. Do not allocate, free or retain
it. Start with `oavc_snapshot_revision(snapshot, generation, revision)`, then call
each generated `oavc_state_*` publisher for the product and every present channel.

- `generation` is nonzero and changes when the hardware session resets. It is
  separate from the permanent unit identity. The examples generate a new value
  on process start.
- `revision` advances whenever reported state or channel membership changes.
  It must fit the protocol's exact integer range, zero through 9,007,199,254,740,991.
- A snapshot replaces the complete previous channel roster. Omit a removed
  channel; publish every declared state for every channel you include.
- Numeric channel IDs remain integers. String IDs contain nonempty ASCII letters,
  digits, underscore or hyphen. IDs must match the model's declared format/range.

State publishers copy strings during the call. The generated boolean publisher
accepts only zero or one; an invalid enum or boolean invalidates the snapshot.
Numbers must be finite, integers must fit the protocol's exact range, and text
must be valid UTF-8 without embedded null characters. The complete network message
must fit the protocol's 1 MiB envelope. Report unavailable readback with a nonzero
callback result or `oavc_snapshot_invalidate(snapshot)`.

The low-level `oavc_state_number`, `oavc_state_integer` (unsigned),
`oavc_state_signed_integer`, `oavc_state_boolean` and `oavc_state_string` functions
accept a child type, typed child ID and state key. A null child type addresses
product-level state. Generated publishers supply those fields for you and are
preferred. `oavc_child_number` and `oavc_child_string` construct low-level IDs.

## Advanced endpoint interface

`oavc_open`, `oavc_port` and blocking `oavc_close` expose the lower-level endpoint.
Its command callback runs on an SDK worker and its readback callback runs on a
separate polling thread; they may overlap. It does not provide the product-thread
dispatch and copied readback of `oavc_adapter_open`. Normal integrations should
use the combined adapter.

For a low-level command callback, `oavc_command_name` returns the borrowed name.
`oavc_parameter_present` checks presence; `oavc_parameter_equals_json` compares
a supplied JSON scalar. The number/integer/boolean/string parameter readers
return zero on success, negative on failure. String output is borrowed. The
generated dispatcher uses these readers after endpoint parameter validation.

The generated `oavc_check_model` returns one when the configured ABI and model
bytes match the compiled adapter, otherwise zero. `oavc_model_sha256` identifies
those bytes. This is a package-consistency check, not a firmware signature or
an authenticity claim. `oavc_dispatch` is supplied by generation; your application
normally implements the hardware callbacks rather than calling it directly.
