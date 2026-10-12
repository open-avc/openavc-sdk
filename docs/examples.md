# Choose an example

Start with the complete system if you want to see how an installer uses the
product. Start with the hardware example when connecting your product APIs.
All examples use invented hardware and leave physical AV equipment untouched
until you replace their callbacks or network configuration.

| Example | Platform and language | Start command from the SDK directory | What runs |
|---|---|---|---|
| [Complete system](system-example.md), separate controller | Linux Docker engine; any host running Python 3.12+ and Docker | `python3 try-system.py --arrangement external` | Two SDK products, ordinary TCP equipment and a separate OpenAVC server. |
| [Complete system](system-example.md), server on product | Linux Docker engine | `python3 try-system.py --arrangement embedded --out build/embedded-example` | The first product runs OpenAVC and controls its hardware, a peer and ordinary equipment. |
| [Five products](system-example.md#five-products-one-controller) | Linux Docker engine | `python3 try-system.py --products 5 --arrangement embedded --out build/five-products` | One product runs OpenAVC, four installed peer servers stay stopped, and ordinary equipment uses its usual driver. Use `--arrangement external` for a separate server. |
| [Outage recovery](system-example.md#exercise-outages-and-recovery) | Linux Docker engine | Add `--check --recovery` to either system arrangement | A peer and then the controller stop and return. Hardware keeps operating, peers do not start servers, and control resumes without command replay. |
| [Access and hardware faults](system-example.md#exercise-access-and-hardware-faults) | Linux Docker engine | Add `--check --faults` to either system arrangement | Trust/authentication refusal, invalid requests, host reservation, unavailable readback and hardware admission refusal. |
| [Hardware integration](hardware-integration.md), supplied service | Linux ARM64/x86-64; C | `python3 try-example.py --exercise-demo` | Product event loop, Unix-socket adapter and separate native device service. |
| Hardware integration, C++ | Linux ARM64/x86-64; C++ source checkout | `python3 try-example.py --language cpp --exercise-demo` | The same behavior and generated C-facing API through a C++ product class. |
| Hardware integration, in-process library | Linux or macOS; C/C++ source | `python3 try-example.py --form direct --exercise-demo` | Product and network endpoint in one process. Add `--language cpp` for C++. |
| [Separate product](product-testing.md) | Same native build requirements | `python3 new-product.py --out ../my-product` | Your model, C hardware source, selected checks and README, separate from the SDK. Source Git checkouts also supply a pinned CI build. |
| [Cross-build and transfer](cross-building.md) | Linux Docker engine; C/C++ source | `python3 tools/build_target.py --target arm64 --out build/arm64-target` | Compiles for the selected Linux processor; `--run-built` checks the transferred binaries on the target. Also accepts `--target x86_64`. |
| [Buildroot package](buildroot.md) | Buildroot 2026.08, Linux x86-64 build host | Follow the reference configuration commands | Builds the native SDK for ARM64 or x86-64 with the firmware toolchain and installs its service/model. Reference filesystems include C/C++ examples for target checks. |
| [Linux firmware](firmware.md), equipment only | Linux; C by default | `python3 examples/linux/check.py --out build/firmware-device` | Staged unprivileged services, hardware restart, persistent settings and shutdown. Build first as described in the guide. |
| Linux firmware, full server | Linux with the compatible server installed | `python3 examples/linux/check.py --with-server --out build/firmware-server` | The same layout with the full server and retained server setting. |
| Linux service supervisor | Isolated Linux host running systemd | `python3 examples/linux/check-systemd.py --with-server --out build/firmware-systemd` | Actual service units, filesystem protection, restart and cleanup; requires administrator access. |

The prebuilt library archive supplies the C adapter example, both native forms on
Linux, product testing tools and firmware examples. It compiles your C code against
supplied libraries; match the recorded CPU/libc/ABI. Use a source checkout for C++
or another firmware toolchain. Windows developers use a Linux environment for
these native hardware examples; this does not change regular OpenAVC's Windows
server support.

## Automated coverage

The SDK's [CI workflow](https://github.com/open-avc/openavc-sdk/blob/main/.github/workflows/ci.yml) runs natively on Ubuntu 24.04
x86-64 and ARM64. It builds C/C++ service examples and the C in-process example,
tests an independently changed model/adapter, creates a separate product project,
builds and extracts the prebuilt libraries, runs the firmware/service checks and
both complete system arrangements, then builds and installs the offline server.
Reports and package hashes are retained as workflow artifacts.
Separate jobs cross-build each processor from the other, transfer the archives
and exercise their unchanged binaries on native runners.

The macOS source path uses the in-process library. The separate Linux service,
product supervision and systemd checks do not run natively on macOS. A firmware
toolchain file lets you build for another target; that is separate from native
execution or physical board qualification.

Check the workflow result for the SDK revision you selected. Passing an example
does not qualify your product's processor, firmware, audio/video workload, update
process or hardware callbacks. Use [Firmware testing](qualification.md) and
retain the exact inputs and independent observations from your integration.
