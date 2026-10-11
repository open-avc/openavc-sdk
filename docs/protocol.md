# Equipment protocol reference

Manufacturers normally use the supplied SDK and generated hardware callbacks.
This reference describes the network contract for inspection and interoperability;
it is not additional protocol work required to use the SDK.

The maintained OpenAVC server defines the protocol identifier and message size in
`openavc.drivers.equipment_protocol`. The SDK generates `sdk/include/protocol.h`
and `openavc_oem/_protocol.py` from the pinned source. The current identifier is
`openavc-device-1`; a serialized message is limited to 1 MiB.

## Identity and authentication

Equipment advertises `_openavc-device._tcp.local.` using DNS-SD/mDNS. The SRV record
contains its HTTPS equipment port. TXT records describe its stable unit identity,
manufacturer, model name and whether remote access is enabled. Advertisements
contain no credentials and do not authorize control.

The product service authenticates remote requests using HTTP Basic over TLS,
against the manufacturer's product credentials. Normal OpenAVC device settings
contain the username/password and certificate-verification option. Keep
verification enabled with a certificate trusted by the OpenAVC server.

The connection from a server to its own product hardware uses a separate,
randomly provisioned bearer credential and a TLS-verified loopback endpoint. Its connection details belong to the
firmware manifest and are not exported with the integrator's project.

## Endpoints

| Request | Result |
|---|---|
| `GET /v1/description` | Protocol, unit identity, session, canonical capability description and current channel IDs. |
| `GET /v1/snapshot` | Complete current product state and channel roster, with generation/revision and quality. |
| `GET /v1/changes?generation=…&revision=…` | Wait up to 250 ms for a change, then return a complete snapshot. |
| `POST /v1/command` | Validate and submit one described command. |
| `GET /v1/commands/{id}` | Read the retained outcome for this client's request ID. |
| `GET /health` | Pending operations, completion uncertainty and current readback quality. |

Description, snapshot and change requests return HTTP 503 when current hardware
readback is unavailable. Consumers must not interpret retained old state as
confirmed current state.

## Command requests

Use a persistent, 32-character hexadecimal client ID in `X-OpenAVC-Client`.
The request body contains exactly these fields:

```json
{
  "session": "session-from-the-current-description",
  "id": 1,
  "command": "set_gain",
  "params": {"channel": 1, "gain": -12.5}
}
```

Request IDs are positive integers up to JavaScript's maximum exact integer.
Increase and persist the ID before dispatch. Reusing a retained ID with the same
request returns its outcome without performing the operation again. Reusing it
with different content returns a conflict. An old ID whose result has expired
is refused; it does not become new work.

The endpoint validates parameter names, required values, types, ranges and enum
choices before invoking a hardware callback. Optional defaults are applied at
that boundary. The capability model uses OpenAVC's canonical driver rules.

An outcome is `completed`, `rejected`, `failed`, `pending` or `uncertain`.
HTTP 200 with `completed` confirms successful completion. HTTP 409 can report a
hardware rejection or failure. HTTP 202 reports work without confirmed final
completion; inspect its request result and actual readback. Never replay an
uncertain operation with a new ID simply because a request timed out.

A changed session invalidates old command requests. Read a fresh description and
snapshot. Do not automatically translate outstanding work into the new session.

## State and operational bounds

A snapshot contains the complete declared product state and present child
entities. Child IDs retain their declared numeric or string type. A new hardware
generation establishes a new session; revisions identify changes within it.
The generated state publishers preserve enum values and copy strings.

The native endpoint retains 128 command results and 128 client high-water marks
per session. It accepts up to two pending operations and four simultaneous
waiting change requests. Excess work returns HTTP 429. These are transport
flow-control bounds, not project, channel, file or full-server feature limits.
Do not use repeated retries as a response to capacity or uncertain completion.

The runtime rejects oversized descriptions, snapshots and requests at the shared
1 MiB envelope. It does not impose a separate fixed channel count or fixed state
string length within that envelope. Qualify the product's largest actual model
and snapshot as part of its firmware testing.
