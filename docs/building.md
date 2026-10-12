# Build the hardware integration

Select a matching SDK/server revision and retain its build evidence as described
in [Compatibility and updates](compatibility.md).

The source checkout includes the C/C++ runtime and examples. A prebuilt library
archive includes compiled libraries and the Linux device service; its normal
build compiles only your C adapter and generated C code.

## Workstation prerequisites

Use Python 3.12 or newer, CMake 3.16 or newer, a C11 compiler and OpenSSL 3
development files. A source build also needs a C++17 compiler. The example
launcher uses the OpenSSL command-line tool to create temporary test certificates.

On Ubuntu 24.04:

```sh
sudo apt-get update
sudo apt-get install python3 cmake build-essential clang libc++-dev libc++abi-dev libssl-dev openssl
python3 doctor.py --report build/environment.json
python3 try-example.py --exercise-demo
```

On macOS, install the Xcode command-line tools and Homebrew prerequisites:

```sh
brew install python@3.12 cmake openssl@3
python3.12 doctor.py --openssl "$(brew --prefix openssl@3)" --report build/environment.json
python3.12 try-example.py --openssl "$(brew --prefix openssl@3)" --exercise-demo
```

The macOS example uses the in-process library. The separate service uses Linux
Unix-socket facilities. On Windows, use the Ubuntu environment in WSL2 for these
hardware examples; this is distinct from the ordinary OpenAVC Windows server.

The doctor compiles a disposable dependency check. It does not execute product
code, install packages or upload the report. Choose a new report filename for
each run; existing reports are not overwritten.

## Source builds

```sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
python3 try-example.py --language cpp --exercise-demo
```

On Linux, `try-example.py` defaults to the service form. Pass `--form direct`
to try networking in the product process. On macOS, the default is `direct`.
The `--language cpp` example is available in the source checkout; the supplied
runtime archive's example uses C.

To use the SDK from your own CMake project, add this source directory with
`add_subdirectory`, set `OPENAVC_PRODUCT` to your description, set
`OPENAVC_BUILD_EXAMPLES=OFF`, and link your product target to `adapter`.
Your product supplies the callbacks declared in the generated `commands.h`.
For a working separate starter, use `new-product.py` and the
[product testing workflow](product-testing.md).

### Install a native CMake package

For a firmware build that stages libraries before building the product application,
enable `OPENAVC_INSTALL` while configuring the SDK. Select your product description
at that step so the installed adapter and generated header match your hardware.
The Buildroot recipe does this for you. To try a local source install:

```sh
cmake -S . -B build/native-install -DOPENAVC_INSTALL=ON -DOPENAVC_BUILD_EXAMPLES=OFF
cmake --build build/native-install --parallel 2
cmake --install build/native-install --prefix "$PWD/build/native-sdk"
cmake -S examples/installed -B build/installed-product \
  -DCMAKE_PREFIX_PATH="$PWD/build/native-sdk" \
  -DOPENAVC_ADAPTER_SOURCE="$PWD/sample/example.c"
cmake --build build/installed-product
```

On macOS, add `-DOPENSSL_ROOT_DIR="$(brew --prefix openssl@3)"` to both
configure commands.

This builds the supplied C product application against the installed libraries.
For your application, enable C and C++ in its CMake project, call
`find_package(OpenAVC CONFIG REQUIRED)` and link `OpenAVC::adapter` to its target.
The package supplies include paths and link dependencies. Keep the installed model
and adapter from the same build. A cross-build uses your firmware toolchain file
for both SDK and application; it does not execute target binaries on the build host.

## Firmware toolchains

For a runnable cross-build, transfer and target-check workflow, see
[Cross-building](cross-building.md). It includes reference ARM64/x86-64 builds
and commands for your own product toolchain.
For Buildroot firmware, use the supplied [external package](buildroot.md).

Use your manufacturer's toolchain file and sysroot when compiling for another
target. Python runs on the build workstation to generate C code; the native
hardware adapter and device service do not run Python.

```sh
cmake -S . -B build-target \
  -DCMAKE_TOOLCHAIN_FILE=/path/to/vendor-toolchain.cmake \
  -DOPENAVC_PRODUCT=/path/to/product.json \
  -DOPENAVC_BUILD_EXAMPLES=OFF
cmake --build build-target --parallel 2
```

The native target needs threads, a monotonic clock, OpenSSL 3, a compatible C++
runtime and, for the service form, Linux Unix sockets. ARM64 and x86-64 are
separate targets. Build against the product's actual libc, loader, OpenSSL and
compiler ABI. An archive for one Linux development environment is not a claim
that every firmware image with that CPU can load it.

The optional full server has its own Python and platform dependencies. Test the
complete installed software alongside the product's audio/video workload.
Measure startup, peak memory, storage, command latency and hardware readback
before selecting firmware resources.

## Build the optional server bundle

With Python 3.12 or newer and a running Linux Docker engine:

```sh
python3 tools/build_server.py --out build/server-bundle
```

The tool builds both OpenAVC web interfaces from the pinned core revision, builds
the server and product-management wheels, and collects their Python dependencies.
It starts no product or hardware integration. The result is under
`build/server-bundle/wheels`; `bundle.json` records hashes, core revision, Python,
CPU, libc and the exact Docker build image ID. Source hashes come from the files
inside that image. `python-packages.cdx.json` inventories the delivered wheels
in [CycloneDX 1.6 JSON format](https://cyclonedx.org/specification/overview/),
including package names, versions, archive hashes and declared metadata.
Keep both records with your firmware build. To check the files and preview a
summary for support, use `python3 collect-diagnostics.py --server-bundle build/server-bundle`
from the SDK directory. See [diagnostic previews](product-testing.md#preview-diagnostics-before-sharing)
for export controls and the verification scope. `INSTALL.txt` contains the offline
installation command:

```sh
python3 -m pip install --no-index --find-links=wheels --require-hashes -r requirements.txt
```

Run that command from the bundle directory in a fresh firmware staging environment.
The generated `requirements.txt` pins every delivered Python package and enables
[pip's hash checks](https://pip.pypa.io/en/stable/topics/secure-installs/).
Keep the requirements file with the trusted bundle; it detects changed archives,
but does not authenticate the publisher or verify packages already installed.
Supply Python 3.12 and a Linux CPU/ABI compatible with the recorded wheels. The
native hardware adapter, device service and OpenSSL are separate firmware
components. Use an ARM64 Docker engine for an ARM64 bundle and an x86-64 engine
for an x86-64 bundle. The tool does not qualify a different firmware libc or
cross-compile a Python interpreter. `--docker-context <name>` selects an engine
without changing the active Docker context.

The inventory covers Python wheel archives. It does not enumerate native code
or JavaScript dependencies inside them, the Python interpreter, operating system,
build tools or other firmware components. Dependency and license declarations
come from each wheel's metadata and have not been evaluated for your firmware.
Include those components and your own software in your product's complete bill
of materials. Build inputs are not fully locked; retaining hashes does not make
a rebuild identical or authenticate an artifact's publisher.

## Produce a prebuilt library archive

On a native Linux Debian/Ubuntu build host with Clang, libc++, libc++abi,
libunwind and OpenSSL development packages installed:

```sh
python3 tools/package_runtime.py --out build/package
```

The output contains a C-only example package, an archive and its SHA-256 file.
`bundle.json` records the build host, libc, canonical contract revision, source
hashes and redistributed runtime origins. Libraries include their license
notices. Use a new output directory for each build.

Extract that archive into a clean directory and run its `doctor.py` and
`try-example.py` commands before distributing it. The doctor checks archive
contents and compiles a link test against the supplied libraries. Firmware
deployment also needs a test on the actual target with its normal service
account and filesystem permissions.
