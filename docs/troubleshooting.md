# Troubleshooting

Start with the command's error and the corresponding log. Keep the product model,
generated files, binaries and SDK/core revision with the report.

| Symptom | Check and next action |
|---|---|
| Doctor reports a missing compiler or library | Install the prerequisites in [Building](building.md), or use prebuilt libraries compatible with your target ABI. Run the doctor again with a new report filename. |
| Build reports a missing `oavc_hw_*` symbol | Implement the callback declared in the generated `commands.h`. Rebuild after changing the model; keep its generated C and model together. |
| Service refuses a model mismatch | Deploy the model and adapter built together. Do not edit `model.json` independently. |
| Inspection says feedback is unavailable | Check the product's `readback` result and refresh timing. Publish all declared state and advance the hardware generation/revision correctly. Check that callbacks execute on the product's permitted thread. |
| Equipment does not appear in Discovery | The product's server must be stopped and equipment discovery enabled. Check `discovery_error`, selected network addresses and multicast access. Ordinary manual Add remains available when remote equipment control is enabled. |
| Remote equipment connection is refused | Use the product's existing username/password. Check certificate trust and that remote equipment access is enabled. Other servers cannot control the product while its own OpenAVC server is enabled. |
| Run OpenAVC is on, but the server is stopped | Read product status and `logs/core-*.log`. Check the Python environment, installed OpenAVC/SDK packages, listener ports, certificate paths and writable storage. Retry from product management after resolving the cause. |
| A command is pending or uncertain | Inspect the retained result and hardware readback. Confirm the manufacturer's completion callback. Do not resend the operation under another request ID. |
| OpenAVC settings require repair | Preserve the logs and saved settings, check product storage, then use the authenticated repair operation. Repair disables access; explicitly re-enable the intended settings afterward. |
| OpenAVC says to update through the product | Update through the manufacturer's firmware process. OpenAVC is updated as part of that firmware. |
| Docker build stops | Read the output directory's `build.log`. Confirm internet access and available Docker memory/storage. The full example builds both web interfaces in addition to native code. This is build-machine resource use, not a measured firmware minimum. |

`GET /oem/diagnostics` requires management authentication and reports current
settings and observed status with credential values excluded. Product logs are
stored under the configured data directory's
`logs/`. Core and recovery logs retain bounded recent generations/segments.
The complete Docker example saves container logs and product data when it stops;
those data include example credentials and must be kept private.

The SDK cannot infer whether an OEM hardware operation physically completed after
its process or device failed. Accurate completion callbacks and independent
readback are necessary. Test those failures against the actual product before
making a recovery or reliability claim.
