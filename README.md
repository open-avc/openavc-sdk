# OpenAVC SDK

Connect your product's hardware to [OpenAVC](https://github.com/open-avc/openavc),
and optionally include the full control system in your firmware.

Describe the product, implement its hardware callbacks, and publish actual state.
The SDK supplies the communication, discovery and product controls. The same
hardware mapping supports both uses:

- **Equipment for another OpenAVC server.** Discover and add the product through
  OpenAVC's normal device setup, using the product's credentials.
- **OpenAVC running on the product.** Use the full Programmer, panels, automation
  and drivers for other equipment. Your product's hardware is automatically
  available in every project and cannot be removed from the project. While
  OpenAVC runs on your product, other OpenAVC servers cannot discover or control it.

Manufacturers can expose the on/off controls in their own product software. The
full server is optional; a server-capable product can keep it stopped while
another OpenAVC server controls the equipment.

This is an engineering preview. The SDK uses the maintained OpenAVC server
and its device capability definitions.
Use the [firmware testing guide](docs/qualification.md) before shipping it in firmware.

## Try the complete system

Install Python 3.12 or newer and Docker with a running Linux container engine.
Clone this repository, then run:

```sh
python3 try-system.py --arrangement external
```

The launcher builds the pinned OpenAVC revision and starts two invented amplifiers,
ordinary TCP equipment and a separate OpenAVC server. It prints the Programmer,
panel and product-settings links, plus generated product credentials. Create the
OpenAVC Programmer administrator login through its normal first-run page.
This is separate from the product-settings login. See
[Programmer login](docs/product-controls.md#programmer-login) to provide setup
through your own product software.

Press Ctrl+C to stop. Then try OpenAVC running inside the first amplifier:

```sh
python3 try-system.py --arrangement embedded --out build/embedded-example
```

The examples run on an isolated Docker network and do not contact AV equipment.
See [the walkthrough](docs/system-example.md) for verification commands, ports,
Docker contexts, saved evidence and cleanup.
Add `--products 5` to try five server-capable products with one selected controller.
The walkthrough also supplies automatic outage and recovery checks.
Add `--cloud` to give the selected controller outbound access for
[optional OpenAVC Cloud pairing](docs/cloud.md).

## Connect your hardware

Start with [product.json](product.json) and the [C example](sample/example.c), or
the [C++ example](sample/example.cpp). The build generates typed command callbacks
and state publishers from the model. Implement those callbacks using your product's
existing APIs; keep hardware work on your product's permitted thread.

On a Linux development host with the [build prerequisites](docs/building.md),
check your environment and run the hardware example:

```sh
python3 doctor.py --report build/environment.json
python3 try-example.py --exercise-demo
```

After editing the product or its hardware mapping, use `--build-only` or `--inspect`.
The demonstration command checks send commands to the unchanged invented amplifier.
Use [your own product project and checks](docs/product-testing.md) to keep your
adapter separate from the SDK, verify selected commands, measure resources and
preview diagnostics before sharing them.

| Guide | Use it for |
|---|---|
| [Example guide](docs/examples.md) | Choose a runnable example and see its platform and test coverage. |
| [Hardware integration](docs/hardware-integration.md) | Capabilities, callbacks, readback, channels, asynchronous completion and shutdown. |
| [Adapter API](docs/adapter-api.md) | C/C++ configuration, function results, thread rules and value lifetimes. |
| [Product model](docs/product-model.md) | Model fields, channels, parameters, state and canonical OpenAVC metadata. |
| [Building](docs/building.md) | Workstation prerequisites, firmware toolchains and prebuilt C libraries. |
| [Cross-building](docs/cross-building.md) | Build for another processor and test the transferred binaries without recompiling them. |
| [Compatibility and updates](docs/compatibility.md) | Select matching SDK/server revisions, qualify an update and retain build evidence. |
| [Product testing tools](docs/product-testing.md) | A separate product starter, command checks, resource measurements/comparisons and diagnostic previews. |
| [Product controls](docs/product-controls.md) | Your product's management UI/API, credentials, optional server and firmware updates. |
| [Linux firmware](docs/firmware.md) | Runnable service startup, persistent storage, systemd templates and per-unit provisioning. |
| [OpenAVC Cloud](docs/cloud.md) | Optional pairing, remote access, product permissions and firmware testing. |
| [Protocol reference](docs/protocol.md) | Network messages, identity, authentication, outcomes and transport bounds. |
| [Firmware testing](docs/qualification.md) | Product-specific build, hardware, resource, recovery and cloud acceptance. |
| [Troubleshooting](docs/troubleshooting.md) | Common failures, diagnostics and next steps. |

The managed product integration targets Linux ARM64 and x86-64. The native adapter
and device service use C/C++ and OpenSSL 3; product management uses Python 3.12 or
newer. The optional full server adds OpenAVC's dependencies. A macOS development
host can run the C/C++ example inside the product process. Follow the firmware
testing guide to check compatibility and resource use on your product.

The installer selects which product runs OpenAVC. Other products connect to
that server; the SDK does not automatically select a server or take over if it fails.

## Upstream compatibility

For evaluation, use the OpenAVC revision built by the supplied system example.
OpenAVC v0.36.0 does not include SDK equipment support. A separate server that
will control your product also needs the compatible revision below; an existing
OpenAVC installation is not necessarily compatible with this preview.

[`sdk/contract/origin.json`](sdk/contract/origin.json) records the compatible
OpenAVC revision and source-file hashes. CI checks that combination, native
builds, prebuilt libraries and both system examples. A separate weekly and
manually triggered workflow checks current OpenAVC changes against the hardware
adapter and product controls. Ship SDK
and server versions that have passed testing together, and repeat the relevant
firmware tests when updating.

## License

[MIT](LICENSE). The license covers the software and does not grant rights to the OpenAVC trademark.
