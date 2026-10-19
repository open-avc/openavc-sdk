# Troubleshooting

Start with the command's error and the corresponding log. Keep the product model,
generated files, binaries and SDK/core revision with the report.

| Symptom | Check and next action |
|---|---|
| Doctor reports a missing compiler or library | Install the prerequisites in [Building](building.md), or use prebuilt libraries compatible with your target ABI. Run the doctor again with a new report filename. |
| Build reports a missing `oavc_hw_*` symbol | Implement the callback declared in the generated `commands.h`. Rebuild after changing the model; keep its generated C and model together. |
| A transferred build is refused | Check the selected language/form, recorded hashes and target OS/processor. Transfer the complete original build with executable permissions. Use the same SDK revision; see [Cross-building](cross-building.md). |
| A matching-CPU binary cannot load a library or interpreter | Check its ELF report and the target's loader, libc, OpenSSL and C++ runtime. Rebuild with the actual firmware toolchain/sysroot; matching the CPU alone is insufficient. |
| Service refuses a model mismatch | Deploy the model and adapter built together. Do not edit `model.json` independently. |
| Inspection says feedback is unavailable | Check the product's `readback` result and refresh timing. Publish all declared state and advance the hardware generation/revision correctly. Check that callbacks execute on the product's permitted thread. |
| Equipment does not appear in Discovery | The product's server must be stopped and equipment discovery enabled. Check `discovery_error`, selected network addresses and multicast access. Ordinary manual Add remains available when remote equipment control is enabled. |
| Remote equipment connection is refused | Use the product's existing username/password. Check certificate trust and that remote equipment access is enabled. Other servers cannot control the product while its own OpenAVC server is enabled. After a refused login, OpenAVC stops reconnecting until the username or password is changed in the device's settings or **Reconnect** is pressed on its device page. |
| Run OpenAVC is on, but the server is stopped | Read product status and `logs/core-*.log`. Check the Python environment, installed OpenAVC/SDK packages, listener ports, certificate paths and writable storage. Retry from product management after resolving the cause. |
| A command is pending or uncertain | Inspect the retained result and hardware readback. Confirm the manufacturer's completion callback. Do not resend the operation under another request ID. |
| OpenAVC settings require repair | Preserve the logs and saved settings, check product storage, then use the authenticated repair operation. Repair disables access; explicitly re-enable the intended settings afterward. |
| OpenAVC says to update through the product | Update through the manufacturer's firmware process. OpenAVC is updated as part of that firmware. |
| The complete example cannot reach OpenAVC Cloud | Start it with `--cloud` in a new output directory. The default example has no outbound controller access. Pair through the normal Cloud page; see [OpenAVC Cloud](cloud.md). |
| A cloud tunnel says the interface is disabled | Enable the corresponding Programmer or Panel switch in product management. A connected tunnel does not override product permissions. |
| Docker build stops | Read the output directory's `build.log`. Confirm internet access and available Docker memory/storage. The full example builds both web interfaces in addition to native code. This is build-machine resource use, not a measured firmware minimum. |

`GET /oem/diagnostics` requires management authentication and reports current
settings and observed status with credential values excluded. Product logs are
stored under the configured data directory's
`logs/`. Core and recovery logs retain bounded recent generations/segments.
The complete Docker example saves container logs and product data when it stops;
those data include example credentials and must be kept private.
Use [the diagnostic preview](product-testing.md#preview-diagnostics-before-sharing)
to export selected status, measurement and server-health summaries without those
private values. `--health-log` includes recorded failures and timing from selected
`health.jsonl` segments; `--network` reports interface state on the computer running
the collector, without addresses. Neither establishes end-to-end connectivity.

The SDK cannot infer whether an OEM hardware operation physically completed after
its process or device failed. Accurate completion callbacks and independent
readback are necessary. Test those failures against the actual product before
making a recovery or reliability claim.

## Report a problem or ask an integration question

Use the [SDK integration form](https://github.com/open-avc/openavc-sdk/issues/new?template=integration.yml).
Include the guide or command you used, the expected and observed behavior, and
your SDK revision or archive name. Distinguish the development computer's OS/CPU
from the product's. You can attach a reviewed diagnostic preview; a report is
optional for a question. Use an invented model when a reproduction would otherwise
expose confidential product details.

The SDK maintainers handle problems in the supplied libraries, tools, examples,
documentation and shared OpenAVC integration. Your firmware team owns the product's
hardware callbacks, operating system/toolchain, factory provisioning, service
startup, firmware updates and product qualification. Keep the tested SDK and core
revisions together when reporting a problem. An exact reproduction using an
invented product helps separate a kit defect from a hardware-specific failure.

For setup problems, include a reviewed `collect-diagnostics.py --environment`
summary from the matching SDK tools. The private doctor report contains the
detailed compiler output your team needs locally. See
[build and toolchain diagnostics](product-testing.md#include-build-and-toolchain-checks).

For a security-sensitive finding, use
[OpenAVC's private vulnerability reporting](https://github.com/open-avc/openavc/security)
and select **Report a vulnerability**. State that the report concerns the SDK
and include its revision. Do not post the vulnerability or product credentials
in the public integration form.
