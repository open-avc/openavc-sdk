# OpenAVC supplied runtime

This archive contains compiled SDK libraries, a native Linux device service,
the C hardware example and manufacturer integration guides. Your build compiles
your C adapter and generated product code against the supplied libraries.

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

To connect your product, edit [product.json](product.json) and implement the
callbacks in [sample/example.c](sample/example.c). Follow
[Hardware integration](docs/hardware-integration.md) for readback, channels,
asynchronous completion and shutdown. Use `--build-only` or `--inspect` after
changing the model; the demonstration command checks are for the unchanged
invented amplifier.

[Product controls](docs/product-controls.md) explains network discovery,
credentials, controls in your own software and the optional full OpenAVC server.
[Qualification](docs/qualification.md) and [Troubleshooting](docs/troubleshooting.md)
cover target testing and diagnostics.

For the complete Docker system examples, C++ example or a different firmware
toolchain, use the [source repository](https://github.com/open-avc/openavc-sdk).
The source and supplied libraries use the same hardware callback interface.
This is an engineering preview, not a claim that your firmware is qualified.

Software: [MIT](LICENSE), with redistributed notices in `licenses/`.
