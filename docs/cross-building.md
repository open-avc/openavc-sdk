# Build on one computer, run on another

Cross-compiling produces binaries for the product's processor and firmware
without running them on the build computer. Use your firmware team's toolchain
and target libraries for a product build. The supplied Debian example lets you
try that workflow before connecting your hardware.

## Try the reference build

Use the [source checkout](https://github.com/open-avc/openavc-sdk), Python 3.12+
and a running Linux Docker engine on an ARM64 or x86-64 development computer:

```sh
python3 tools/build_target.py --target arm64 --out build/arm64-target
```

Choose `--target x86_64` and a different output directory for x86-64.
`--docker-context <name>` selects an engine without changing your active context.
The first build downloads its toolchain and target development packages.

The container runs on the build computer's processor. It builds the invented
C and C++ adapters, generated model and Linux device service against a Debian 12
sysroot: a directory containing the target's headers and libraries. It does not
run the target binaries, use CPU emulation or build the optional Python server.

The output contains:

- `target.tar.gz` and its SHA-256 file, preserving executable permissions for transfer.
- The binaries, generated model and successful build records with file hashes.
- `cross-build.json`, identifying the build computer, target, compiler and core contract.
- ELF reports showing each binary's CPU, loader and dynamic-library dependencies.
- Compiler-package versions, target-package hashes and build logs.
- `toolchain-check.json`, the prerequisite check for the selected compiler and sysroot.

This is a reference development environment, not a universal firmware binary.
Dependencies are fetched from the configured package repositories; the recipe
does not promise identical rebuilds from locked release inputs.

## Run the transferred output

Transfer the archive and checksum through your normal engineering workflow to a
matching Linux target. Use the same SDK source revision there. The test needs
Python 3.12+, the OpenSSL command-line tool and the target runtime libraries:
glibc, libstdc++, libgcc, OpenSSL 3 and their normal system dependencies.
It does not need CMake, a compiler or development headers.

From the directory containing the transferred archive:

```sh
sha256sum -c target.tar.gz.sha256
tar -xzf target.tar.gz
```

From the SDK source directory, point to the extracted directory:

```sh
python3 try-example.py --run-built /path/to/target --exercise-demo
python3 try-example.py --run-built /path/to/target --language cpp --exercise-demo
python3 try-example.py --run-built /path/to/target --form direct --exercise-demo
```

The launcher verifies the recorded hashes, OS, processor and core contract before
starting the existing binaries. It exercises commands, independent invented
hardware counters, external feedback and delayed completion, then stops both
processes. Reports distinguish the build computer from the computer that ran
the check. Checksums detect changed files; they do not authenticate their source.
Only run artifacts from a source you trust.

`--exercise-demo` is only for the unchanged invented amplifier. For your product,
use `--inspect` for read-only inspection, or `--checks /path/to/checks.json
--out /path/to/new-report` for deliberately selected commands. See
[Product testing](product-testing.md).

## Use your firmware toolchain

The SDK uses CMake's normal [Linux toolchain mechanism](https://cmake.org/cmake/help/latest/manual/cmake-toolchains.7.html#cross-compiling-for-linux).
Your toolchain selects the target processor, C/C++ compilers and sysroot.
Build-time programs such as Python must come from the build computer; target
headers and libraries must come from the firmware toolchain.

Check that environment before building your adapter:

```sh
python3 doctor.py --toolchain /path/to/vendor-toolchain.cmake --report build/toolchain-check.json
```

Run this in an SDK source checkout with your firmware build environment loaded.
The doctor compiles and links a disposable C11/C++17, threads and OpenSSL 3 probe
using the selected toolchain. It records the toolchain file hash, selected
compilers, target and configuration/build output. It executes no target binary
and does not check the complete SDK or product firmware. Included toolchain files,
environment settings and sysroot contents remain part of your build records.
Use the report to correct missing target headers, libraries or compiler settings.

After creating a separate product with `new-product.py`, build its adapter:

```sh
python3 try-example.py --project /path/to/my-product \
  --toolchain /path/to/vendor-toolchain.cmake \
  --build-dir build/product-target --form service --build-only
```

Use `--language cpp` for `hardware.cpp`, or `--form direct` for an in-process
endpoint. Use a fresh build directory when changing toolchains or target ABIs.
The launcher supplies the build computer's Python interpreter explicitly.
For an existing CMake firmware project, follow the `add_subdirectory` integration
in [Building](building.md#source-builds) instead.

Keep the complete successful build record, its listed files and their directory
structure when transferring your build. Preserve executable permissions and any
runtime libraries required by your product. `--run-built` uses those outputs
directly; it does not rebuild from source files on the target.

The provided [toolchain example](../examples/linux/cross/toolchain.cmake) shows
the build/target search separation used by our reference container. Replace it
with your vendor's toolchain for firmware integration. A processor match alone
does not establish compatible libc, loader, OpenSSL or C++ ABI.

## Verify your product

CI cross-compiles ARM64 on x86-64 and x86-64 on ARM64, transfers each archive,
and runs the unchanged output on a native target runner. That checks both the
build and transfer workflow. It does not qualify a manufacturer's board, service
manager, hardware callbacks or audio/video workload.

Use [Linux firmware](firmware.md) for startup and storage, and
[Firmware testing](qualification.md) for product acceptance. The
[optional server bundle](building.md#build-the-optional-server-bundle) is built
separately for its matching Python and Linux environment.
