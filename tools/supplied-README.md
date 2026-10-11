# OpenAVC prebuilt libraries

This archive contains compiled SDK libraries, the C hardware example and
manufacturer integration guides. Linux archives also include the device service.
Your build compiles your C adapter and generated product code against the supplied libraries.

Start on a compatible Linux development host with Python 3.12 or newer, CMake,
a C11 compiler and OpenSSL 3 development files:

```sh
python3 doctor.py --report build/environment.json
python3 try-example.py --exercise-demo
```

The doctor verifies the supplied files and compiles a link check. The example
uses invented hardware; it does not connect to AV equipment. See
[Building](docs/building.md) for prerequisites and target compatibility.
`bundle.json` records the CPU, libc, upstream contract and file hashes.

Use the OpenAVC revision recorded in
[`sdk/contract/origin.json`](sdk/contract/origin.json) with this preview, including
on a separate server controlling your product. OpenAVC v0.36.0 does not include
SDK equipment support.

To connect your product, edit [product.json](product.json) and implement the
callbacks in [sample/example.c](sample/example.c). Follow
[Hardware integration](docs/hardware-integration.md) for readback, channels,
asynchronous completion and shutdown. Use `--build-only` or `--inspect` after
changing the model; the demonstration command checks are for the unchanged
invented amplifier.
Use [Product testing tools](docs/product-testing.md) for a separate C product
starter, selected command checks, resource measurements and diagnostic previews.

[Product controls](docs/product-controls.md) explains network discovery,
credentials, controls in your own software and the optional full OpenAVC server.
[Linux firmware](docs/firmware.md) supplies service startup checks, storage layout
and systemd examples.
The [adapter API](docs/adapter-api.md) and [product model](docs/product-model.md)
references describe fields, callback types and value lifetimes.
[Firmware testing](docs/qualification.md) and [Troubleshooting](docs/troubleshooting.md)
cover target testing and diagnostics.

For the complete Docker system examples, C++ example or a different firmware
toolchain, use the [source repository](https://github.com/open-avc/openavc-sdk).
The source and supplied libraries use the same hardware callback interface.
This is an engineering preview. Test it on your product before including it
in a firmware release.

Software: [MIT](LICENSE), with redistributed notices in `licenses/`.
