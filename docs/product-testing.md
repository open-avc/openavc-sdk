# Test your product integration

Use these tools from a development checkout or a prebuilt library archive.
Python 3.12 or newer is required. Activate the
[Python tools environment](building.md#python-tools) first. The resource profiler
also needs `psutil`:

```sh
python3 -m pip install psutil
```

Reports stay on your computer. The tools do not upload them or create a cloud
account. Use a new output directory for each run. Keep the original reports
private: they can contain product identity, hardware state, command parameters
and local process details.

## Start a separate product project

Run this from the SDK directory:

```sh
python3 new-product.py --out ../my-product
python3 try-example.py --project ../my-product --build-only
python3 try-example.py --project ../my-product --inspect
python3 try-example.py --project ../my-product --checks ../my-product/checks.json --out ../my-product/build/check-1
```

The new directory contains `product.json`, `hardware.c`, `checks.json` and a
README. It starts as a working invented amplifier. Replace its model and hardware
callbacks, then update its checks to match your product. The SDK stays separate
from your product files. Source checkouts also create a GitHub Actions workflow
that pins the SDK's current committed revision and compiles your integration.
It starts no hardware. Update that pin deliberately when adopting a newer SDK.

For C++, add `--language cpp` to creation and each build/run command. The C++
starter requires a source checkout; the prebuilt library archive supplies C.
On macOS the launcher uses the in-process library. On Linux it defaults to the
device service; `--form direct` selects the in-process library.

The launcher builds the selected product, creates temporary local credentials
and certificates, starts the adapter, runs the selected checks and stops its
processes. It does not install your product as a service or choose a firmware
toolchain. See [Building](building.md) for those build requirements.
For a product built on another computer, use the [cross-build workflow](cross-building.md)
and pass its output to `--run-built`; inspection and selected checks then use
those binaries without compiling again.

## Select commands and expected state

`--inspect` sends no commands. `--checks` tests the commands in your
check file, in order. Select operations that are safe on your test hardware.
The entire check file is validated before any command is sent.

```json
{
  "model_id": "acme_amplifier",
  "checks": [
    {
      "name": "Set channel gain",
      "command": "set_gain",
      "params": {"channel": 1, "gain": -31},
      "expect": [
        {"child_type": "channel", "child_id": 1, "state": "gain", "equals": -31}
      ]
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `model_id` | Your product model's `id`. |
| `checks` | Ordered command checks. An empty list performs only read checks. |
| `name` | A name for this check in the report. |
| `command`, `params` | A declared command and its parameter object. Required parameters must be present; optional defaults are applied by the endpoint. |
| `expect` | A nonempty list of reported state expectations. All must match. |
| `state`, `equals` | State name and exact expected value. |
| `state`, `delta` | Numeric state name and expected change from the value read immediately before dispatch. Use this for a completion counter, for example. |
| `child_type`, `child_id` | Both select a channel's state. Omit both for product-level state. Numeric and string IDs retain their type. |
| `outcome` | Optional `completed` (default), `rejected` or `failed`. Omit for `before_acceptance`. |
| `timeout` | Seconds allowed for command completion and, separately, matching feedback. Default 10; greater than zero, up to 300. Requests also have a five-second network timeout. |
| `observe_for` | Seconds to check stable state after a duplicate, incomplete request or interrupted command's completion. Default 0.5; from 0.1 to 30. |
| `check_refusals` | Optional boolean, default `false`. Before this command, submit invalid copies and verify refusal with unchanged selected state. |
| `interruption` | Optional `before_acceptance` or `before_response`. Interrupt this command's connection and inspect its result without resending it. See below. |

The checker verifies the description against the generated model, authentication,
typed state, channel IDs, generation/revision and change readback. For a command
without `interruption`, it waits for a terminal outcome and matching feedback, then deliberately
sends the **same request ID and body** again and checks the retained outcome and
state. A counter with `delta: 1` can detect a reported extra execution; a gain
value alone cannot prove that the physical write occurred only once.

Set `check_refusals: true` on a selected command to check requests without
authentication, with a different equipment session, with request ID zero, with
an undeclared command and with an undeclared parameter. Each request must be
refused before the valid command is sent. The checker observes the state selected
in `expect` for `observe_for` seconds after each refusal; it must remain at its
pre-command value. Use an execution counter where available. These requests
deliberately exercise rejection paths: run them on test hardware where an
incorrectly accepted operation is safe, and verify physical effects independently.
They do not change product settings, restart the product or qualify every invalid
input, firmware version or access policy. The report's `refusals` list records
each request before dispatch and retains the failed step if the run stops.

State expectations must remain stable during the observation interval. If your
product changes state independently, select suitable state or control that activity
through your test setup. The tool does not lock the product against other clients.

Check factory identity on two known, separate units. Each private `report.json`
contains `identity`; OpenAVC also reports it as **Equipment ID** in the device's
advanced connection settings. The two units must have different identities.
If they match, correct provisioning before commissioning them. Two addresses
alone do not prove there are two units: one product can have multiple interfaces.
OpenAVC does not automatically warn about duplicate unit identities or elect a
controller from them. Keep identity stable across firmware updates and restarts.

The report records each command's intent before dispatch. On a timeout, restart,
session change or failure, the run stops and retains its evidence. It does not
resume or automatically resend an interrupted command. Inspect the hardware and
the original result before starting another run; a new run has a new client ID.

## Check interrupted commands

Add these checks to your product's checks file, replacing `increment` and `counter`
with your selected command and completion counter:

```json
{
  "model_id": "acme_amplifier",
  "checks": [
    {
      "name": "Incomplete request has no effect",
      "command": "increment", "params": {},
      "interruption": "before_acceptance",
      "expect": [{"state": "counter", "delta": 0}]
    },
    {
      "name": "Lost response completes once",
      "command": "increment", "params": {},
      "interruption": "before_response",
      "expect": [{"state": "counter", "delta": 1}]
    }
  ]
}
```

Run the same `try-example.py --checks` or `check-product.py --checks` command.
Both cases close a verified TLS connection without reading its command response.
`before_acceptance` sends all but the final request-body byte. It requires no
retained command result and unchanged selected state throughout `observe_for`.
`before_response` sends the complete body, then reads the retained result until
completion and checks the expected state. It never repeats the command, including
after completion. A missing result, changed session or unavailable feedback stops
the run; a missing result is not proof that hardware did nothing.

Choose an observation interval long enough to detect delayed hardware effects.
Verify callback and physical-operation counts independently. Use your adapter's
test controls to delay completion as well as testing immediate completion. The
report records sent body length, whether a response was read, and observed command
outcomes. Bytes sent do not prove acceptance; the retained result establishes it.
These checks interrupt one HTTP exchange. They do not reboot equipment, interrupt
its firmware update or simulate every network failure.

For a product that includes the full server, also exercise its normal product
settings while a hardware command is pending:

1. Start a delayed command through remote equipment control, then enable the
   product's OpenAVC server. It must not start while hardware completion is unknown.
   Remote equipment control must remain unavailable during the transition.
2. Let the hardware finish, inspect its operation count, then retry the setting
   if needed. The server must use the completed hardware state without replaying
   the earlier command.
3. Start a delayed command through the local server, then disable that server.
   Remote equipment control must remain unavailable until completion is confirmed
   and the setting is applied successfully.
4. Verify one physical operation, then send a new command through remote control.

The supplied Linux host checks exercise both transitions against the native
adapter and an installed OpenAVC server. Use your product's own hardware observation
and settings interface when qualifying its firmware.

## Check equipment already running on your network

Save its existing product credentials in a private JSON file:

```json
{"username": "your-product-user", "password": "your-product-password"}
```

Then run:

```sh
python3 check-product.py --url https://product.example:9443 --credentials credentials.json --ca-file product-ca.pem --model build/generated/model.json --out build/network-inspection
```

Omit `--ca-file` when the product certificate is trusted by the workstation.
The URL and certificate must match. Redirects are refused. Add `--checks checks.json`
to execute your selected operations. Without it, this command only reads the
equipment. The remote equipment interface must be enabled and the product's own
OpenAVC server stopped. Use this endpoint's port, not the Programmer or management port.

For a local native endpoint, the credential file can instead contain
`{"token": "your-local-endpoint-token"}`. That is an integration test credential,
not a new installer connection step. Ordinary OpenAVC uses its normal Discovery/Add
workflow and product credentials.

## Observe connection loss and recovery

Use a test product with its remote equipment interface enabled. Start this
read-only observer, then wait for **Baseline passed** before interrupting its
network connection or restarting your product application:

```sh
python3 check-product.py --url https://product.example:9443 --credentials credentials.json --ca-file product-ca.pem --model build/generated/model.json --reconnect 120 --out build/reconnect-check
```

Restore the connection within the selected window. The observer must see a
connection failure or HTTP 503, followed by the same product identity and model
with current state, working authentication and change readback. A new equipment
session after restart is allowed. It fails on a different product, changed
capabilities or protocol, unauthorized access, invalid state, no observed
interruption or no recovery. TLS certificate verification failures stop the
check; they do not count as an expected outage.

The window is 1–3600 seconds. Each request has a five-second timeout, and final
readback checks can finish after the window. Keep the interruption long enough for the observer to
detect it. This option sends no hardware commands and cannot be combined with
`--checks`. It does not restart the product, change network settings, follow a
changed address or test an OpenAVC server's reconnection. Arrange the interruption
in your test setup. The report saves the observed transition times and old/new
sessions. Verify AV continuity separately; a successful return does not establish
that hardware work continued correctly during the outage.

## Measure resources on the target

Run the profiler on the same host as the product processes. Select their process
IDs from your supervisor. Include the product application and management service;
observed descendants are included automatically. Additional `--pid` options add
independent processes without counting a process twice.

```sh
python3 profile-product.py --pid 1234 --pid 5678 --duration 60 --interval 1 --data-dir /var/lib/my-product --out build/resource-1
```

Replace the IDs and directory with your product's values. Exercise its normal
audio/video workload during the measurement. `samples.jsonl` contains samples;
`report.json` records elapsed time, host CPU/memory information and observed peaks
for RSS memory, CPU, threads and descriptors (handles where applicable). A selected
process exiting or inaccessible metrics fail the measurement. It never attaches
to a replacement process merely because its PID was reused.

CPU usage of 100% means one logical CPU. RSS totals can count shared pages more
than once. The first CPU sample is unavailable until an interval has elapsed.
Sampling can miss short-lived children and peaks between samples. `--data-dir`
optionally records apparent regular-file size, file count and filesystem free
space without reading file contents or following links. A scan stops at 100,000
files and marks its result incomplete; it is not a consistent filesystem snapshot.
Choose an interval appropriate for the cost of scanning your product's data.

This tool supplies measurements, not a universal memory minimum or a performance
pass/fail threshold. Measure startup separately, compare workloads on the same
firmware, and include independent AV-quality observations. Command request timings
are in the product check report; they are not physical hardware latency measurements.
The [workload measurement guide](workload-testing.md) describes a repeatable
comparison, startup milestones and the evidence to keep with each firmware build.

### Compare firmware versions or workloads

Label each measurement so another engineer can identify the software and activity
behind the numbers. Add `--artifact` for each firmware file or executable whose
identity you want recorded:

```sh
python3 profile-product.py --pid 1234 --duration 60 --interval 1 --label firmware-a --workload "normal audio routing" --artifact /opt/my-product/hardware --out build/resources-a
```

Replace the PID and artifact with your product's values. The report records the
label, declared workload and each selected file's name, size and SHA-256. Hashing
finishes before measurement starts. These identify the files you supplied; they
do not prove which executable or libraries the process loaded. Process arguments
and environment are still excluded. Labels and file names stay in your private
measurement report and are excluded from the diagnostic summary.

Run a second measurement with the intended software or workload, then compare:

```sh
python3 compare-profiles.py --baseline build/resources-a/report.json --candidate build/resources-b/report.json --out build/resource-comparison.json
```

The command prints the comparison and optionally saves it to a new file. It needs
only Python's standard library. Both reports must be completed measurements from
the resource profiler. The output includes each input's hash, declared context,
observed peaks, absolute differences and percentage changes. Percentage change
from a zero baseline is unavailable. Missing metrics and incomplete storage scans
remain unavailable; they are never counted as zero.

Check `notes` for differences in the reported host, requested duration, sampling
interval or declared workload, missing artifact identities, or the same report
selected twice. Matching metadata alone cannot establish equal hardware, equal
workload or unchanged AV quality. Compare repeatable workloads on the same target
and retain your independent audio/video observations. Sampling still has the
limitations described above; a lower observed peak is not a reliability result.

## Preview diagnostics before sharing

In product management, use **Download diagnostics** to save current product
status. Select that file and any reports you want summarized:

```sh
python3 collect-diagnostics.py --status openavc-product-diagnostics.json --checks build/network-inspection/report.json --resources build/resource-1/report.json
```

The command prints the exact preview and creates no file. Run it again with
`--out support.json` to export that summary. Each input is optional; select at
least one. Inputs are limited to 16 MiB each. Existing export files are refused.

The summary includes only recognized status flags, check outcomes and measured
resource totals. Its `collected` list identifies which sources you supplied;
missing sources have no implied result. It excludes credentials, keys, addresses,
paths, identity, labels, raw errors/logs, project contents, command values and
process details. No directories or additional files are collected automatically.
Retain the original reports locally for investigation.

### Include build and toolchain checks

Select the report from `doctor.py` to include setup findings in the same preview:

```sh
python3 collect-diagnostics.py --environment build/environment.json --checks build/network-inspection/report.json --out setup-support.json
```

Use doctor and collector reports from the same SDK revision. If an older report
is refused, run the current doctor to create a new report; do not edit its format.
The summary includes recognized prerequisite results, build-host OS/CPU/Python,
configured target OS/CPU, cross-compiling status and doctor/toolchain file hashes.
Compiler commands, paths, sysroot contents, raw errors and version-output text
stay in the private doctor report. A passing compile check does not prove that
the target can execute the software or sustain the product's AV workload.

Selected product-check reports also summarize invalid-request outcomes and
reconnect observation when those checks were run. Command values, state,
identity and session IDs remain excluded. Missing checks have no implied result.

### Include server health and network information

Select the product's `logs/health.jsonl` to include its recorded server health
observations. On the product itself, add `--network` for local interface status:

```sh
python3 collect-diagnostics.py --health-log /var/lib/my-product/logs/health.jsonl --network --out runtime-support.json
```

Replace the path with your product's data directory. The health summary includes
HTTP status, responsiveness, request duration, time between probes and recognized
error types. It excludes raw messages. The supervisor records changes, failures,
slow/delayed checks and a periodic healthy observation; this is not a record of
every probe. A stopped server has no new observations.

To include rotated segments, repeat `--health-log` in oldest-to-newest order
(`health.jsonl.2`, `health.jsonl.1`, `health.jsonl`), selecting only files that
exist. Up to eight files of 16 MiB each are accepted. The last 100 recognized
observations are retained in the selected order. Each source has a size/hash
and counts of recognized, unrecognized and unreadable records. An unfinished
final line is counted and omitted, so collecting during a write does not imply
a complete history. Keep the original segments locally.

`--network` reads interfaces on **the computer running the collector**. It
reports link status, MTU, reported link speed, multicast capability when available,
and IPv4/IPv6, loopback and link-local address counts. Names, addresses, MAC
addresses and DNS settings are excluded; numbered interfaces identify rows only
within that snapshot. Missing statistics remain `null`. Run it on your product
to report the product's network, or inside its container to report that container.
It sends no traffic and does not test routing, certificate trust, discovery or
connectivity to another device. Interface information may change during collection.

Without `--out`, the command only prints a preview. Repeating `--network` or
reading an active log takes a new snapshot; the exported file always matches the
preview printed by that invocation. Run [the equipment checker](#check-equipment-already-running-on-your-network)
separately to test the selected equipment connection.

### Identify an offline server bundle

To identify the offline server bundle you built, add its directory:

```sh
python3 collect-diagnostics.py --server-bundle build/server-bundle --out bundle-support.json
```

This checks every delivered wheel, the package inventory and installation
requirements against `bundle.json`. Missing, extra or changed wheel files are
refused. The preview includes package versions, build Python/processor, recorded
core commit and build image, plus the bundle metadata hash. It excludes filenames,
source paths and free-form package metadata. Unrecognized version/build fields
appear as `null`. Hashes identify the selected files; they do not authenticate
their publisher or establish which software is installed on a product. Keep the
bundle record from your trusted build with the firmware's release records.

## What a passing report establishes

The product checker exercises the selected protocol operations and reported state.
It cannot establish that the hardware adapter reports truthfully or that audio/video
remained correct. Verify physical effects through independent instrumentation or
your product's own test facilities. Discovery, full-server lifecycle, firmware
updates, failure recovery, security review and sustained AV workload testing remain
separate checks in [Firmware testing](qualification.md).
