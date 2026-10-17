# Manufacturer integration guides

Use the SDK to make your product controllable by OpenAVC, to run an OpenAVC server
on the product, or to offer both options. Start with the
[complete system example](system-example.md) to see each arrangement before
connecting your hardware. The [SDK README](../README.md) gives the starting
commands and current preview status.

## Evaluate and connect

| Task | Guide |
|---|---|
| Check the target and software combination | [Compatibility](compatibility.md) and [build requirements](building.md) |
| Choose a working example | [Examples by platform and language](examples.md) |
| Describe commands, state and channels | [Product model](product-model.md) |
| Connect existing product APIs | [Hardware integration](hardware-integration.md) |
| Check C types, ownership and callbacks | [Adapter API](adapter-api.md) and [header declarations](c-declarations.md) |
| Inspect the network protocol | [Equipment protocol](protocol.md) |
| Add controls to the product GUI | [Product controls](product-controls.md) |

## Build firmware and validate

| Task | Guide |
|---|---|
| Build for another processor | [Cross-building](cross-building.md) |
| Use a firmware build system | [Buildroot](buildroot.md) or [Yocto/OpenEmbedded](yocto.md) |
| Start services and retain settings | [Linux firmware integration](firmware.md) |
| Prepare identity, credentials and certificates | [Per-unit provisioning](provisioning.md) |
| Verify commands and independent hardware feedback | [Product testing](product-testing.md) |
| Measure alongside the product's AV workload | [Workload testing](workload-testing.md) |
| Define firmware acceptance | [Qualification](qualification.md) |
| Use optional cloud services | [Cloud integration](cloud.md) |
| Diagnose a failure or prepare a support report | [Troubleshooting](troubleshooting.md) |

These guides and the C declarations are included in the SDK archive for offline
reading. Source links refer to files in the same checkout or archive. Links to
GitHub, OpenAVC user documentation and cloud services require Internet access.
