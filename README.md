# OpenAVC SDK

Connect your product's hardware to [OpenAVC](https://github.com/open-avc/openavc),
and optionally include the full control system in your firmware.

Describe the product, implement its hardware callbacks, and publish actual state.
The SDK supplies the communication, discovery and product controls. The same
hardware mapping supports both uses:

- **Equipment for another OpenAVC server.** Discover and add the product through
  OpenAVC's normal device setup, using the product's credentials.
- **OpenAVC running on the product.** Use the full Programmer, panels, automation
  and ordinary equipment drivers. The product's own hardware appears as permanent
  built-in equipment. While that server runs, the local hardware is not exported
  to another OpenAVC server.

Manufacturers can expose the on/off controls in their own product software. The
full server is optional; a server-capable product can keep it stopped while
another OpenAVC server controls the equipment.

This is an engineering preview. The SDK consumes the maintained OpenAVC server
and its canonical capability definitions. It does not contain a fork of the server.
Use the [qualification guide](docs/qualification.md) before shipping it in firmware.

## Try the complete system

Install Python 3.12 or newer and Docker with a running Linux container engine.
Clone this repository, then run:

```sh
python3 try-system.py --arrangement external
```

The launcher builds the pinned OpenAVC revision and starts two invented amplifiers,
ordinary TCP equipment and a separate OpenAVC server. It prints the Programmer,
panel and product-settings links, plus generated product credentials. Create the
OpenAVC administrator account through its normal first-run page.

Press Ctrl+C to stop. Then try OpenAVC running inside the first amplifier:

```sh
python3 try-system.py --arrangement embedded --out build/embedded-example
```

The examples run on an isolated Docker network and do not contact AV equipment.
See [the walkthrough](docs/system-example.md) for verification commands, ports,
Docker contexts, saved evidence and cleanup.

## Connect your hardware

Start with [product.json](product.json) and the [C example](sample/example.c), or
the [C++ example](sample/example.cpp). The build generates typed command callbacks
and state publishers from the model. Implement those callbacks using your product's
existing APIs; keep hardware work on your product's permitted thread.

On a Linux development host with the [build prerequisites](docs/building.md):

```sh
python3 doctor.py --report build/environment.json
python3 try-example.py --exercise-demo
```

After editing the product or its hardware mapping, use `--build-only` or `--inspect`.
The demonstration command checks send commands to the unchanged invented amplifier.

| Guide | Use it for |
|---|---|
| [Hardware integration](docs/hardware-integration.md) | Capabilities, callbacks, readback, channels, asynchronous completion and shutdown. |
| [Building](docs/building.md) | Workstation prerequisites, firmware toolchains and supplied C-runtime packages. |
| [Product controls](docs/product-controls.md) | Your product's management UI/API, credentials, optional server and firmware updates. |
| [Protocol reference](docs/protocol.md) | Network messages, identity, authentication, outcomes and transport bounds. |
| [Qualification](docs/qualification.md) | Product-specific build, hardware, resource, recovery and cloud acceptance. |
| [Troubleshooting](docs/troubleshooting.md) | Common failures, diagnostics and next steps. |

The managed product integration targets Linux ARM64 and x86-64. The native adapter
and device service use C/C++ and OpenSSL 3; product management uses Python 3.12 or
newer. The optional full server adds OpenAVC's dependencies. A macOS development
host can run the in-process C/C++ example. These are build/evaluation paths, not a
claim that an unknown manufacturer's firmware is qualified.

There is no automatic controller election, failover or reduced full-server
edition. Long-duration and physical-firmware qualification are separate from
the supplied simulated examples.

## Upstream compatibility

[`sdk/contract/origin.json`](sdk/contract/origin.json) identifies the tested core
revision and canonical source hashes. Normal CI checks the pinned pair, native
builds, supplied runtimes and both system arrangements. The upstream compatibility
workflow checks current OpenAVC against the adapter and host controls. Firmware
updates should use a tested SDK/core combination and repeat product qualification.

## License

[MIT](LICENSE). The license covers the software and does not grant rights to the OpenAVC trademark.
