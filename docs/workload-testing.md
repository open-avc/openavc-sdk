# Measure OpenAVC alongside your product's AV workload

Use your product's normal performance tests to determine whether an integration
fits its available resources. The SDK examples establish control behavior; they
do not measure your audio or video pipeline. Keep each result with the exact
firmware, hardware revision and workload that produced it.

## Choose the workload and acceptance criteria

Start with a repeatable workload your product already supports. Record its
channel or stream count, formats, routing activity and other enabled features.
Include the busiest supported combination and transitions such as route changes
or stream restarts. Select the permitted AV errors, response times, startup time
and resource headroom before measuring; those limits depend on your product.

Compare these configurations on the same unit where your product offers them:

| Configuration | What to include |
|---|---|
| Existing firmware behavior | The product's AV workload with the OpenAVC integration stopped. |
| Equipment controlled remotely | The same AV workload, your adapter/device service, and representative commands and feedback from a separate OpenAVC server. |
| Product running OpenAVC | The same workload plus the local server, project, panels, automation and other controlled devices expected in the installation. |

Keep power settings, cooling, network conditions, logging and background services
consistent. Record differences that cannot be held constant. For firmware updates,
compare the accepted and candidate firmware with the same workload and equipment.
An idle example or a different development computer does not establish headroom.

## Measure resources and physical behavior together

Run [the resource profiler](product-testing.md#measure-resources-on-the-target)
on the target. Select the product process, SDK manager and any other relevant
processes that are not already their descendants. Obtain their PIDs from your
service supervisor. Replace the example PIDs, artifact and data path below:

```sh
python3 profile-product.py --pid 1234 --pid 1235 --duration 60 --interval 1 --label firmware-a --workload "eight active streams and routing changes" --artifact /opt/my-product/hardware --data-dir /var/lib/my-product/openavc --out build/resources-a
```

The duration is an example, not an endurance requirement. Choose a duration that
includes your complete workload cycle and an interval suitable for its activity.
The profiler samples RSS, CPU, threads and descriptors/handles; descendants are
included when observed. It also samples the selected directory's size and free
space. It does not capture every short-lived process or brief resource peak,
and summed RSS can count shared pages more than once. Directory scans and the
profiler itself use resources; check their effect on a constrained target.

In parallel, use your product's independent facilities to record the AV result:
audio dropouts/underruns, stream or frame errors, end-to-end latency, clock/sync
status, temperature or other measures relevant to your hardware. Keep actual
command effects and feedback age with those observations. A successful network
reply cannot establish when a relay moved, a route changed or an AV output settled.

The product checker can execute a selected safe command sequence while the
workload runs. Its request timings measure protocol response time. If a command
completes asynchronously, retain the final hardware outcome as well. Use the
[command and readback workflow](product-testing.md#select-commands-and-expected-state);
do not derive a command rate from the demonstration amplifier.

## Measure startup separately

Record monotonic timestamps through your boot/service instrumentation. Choose a
clear starting event, such as power application, application start or service
restart, and name it in the report. Those measurements are different.

Record when the product's AV function is ready, hardware readback becomes current,
remote equipment control becomes available, and the optional OpenAVC server can
operate the intended project. For the full server, include a successful safe
command and its hardware outcome; an open HTTP port alone is not readiness.
Check the Programmer or panel too when it is part of the product's offer.

The profiler attaches to existing processes. It does not measure earlier boot
work or automatically include a replacement root PID after a supervisor restart.
Start a new measurement for the new process and retain the supervisor timestamps.
Measure cold boot and service restart separately, including a populated persistent
data directory and the recovery cases your firmware supports.

## Compare and retain the evidence

Run the same workload on the candidate firmware and compare completed reports:

```sh
python3 compare-profiles.py --baseline build/resources-a/report.json --candidate build/resources-b/report.json --out build/resource-comparison.json
```

Inspect the context notes and compare the AV observations alongside the resource
numbers. A missing metric remains unavailable. A lower sampled peak is not proof
of improved reliability. Investigate a regression before accepting the firmware;
retain individual runs when repeating a workload so variability remains visible.

Keep a record with these fields for each case:

| Field | Record |
|---|---|
| Product and software | Hardware revision, firmware version, SDK/core revisions, build manifests and artifact hashes. |
| Environment | OS/libc, service configuration, power/cooling and relevant network conditions. |
| Workload | Reproducible setup, inputs, duration, enabled interfaces/features and safe command sequence. |
| Criteria | Product-specific limits and the independent method used to measure each one. |
| Observations | Profiler reports, command/readback results, AV measurements, startup timestamps and relevant local logs. |
| Outcome | Criteria met or failed, unavailable evidence, anomalies and any changes made before a later run. |

Keep raw records private. Use the [diagnostic preview](product-testing.md#preview-diagnostics-before-sharing)
when sharing the supported summary; the full workload and product details are
not included automatically. Firmware updates, recovery, security and long-duration
operation still need the applicable [firmware tests](qualification.md).
