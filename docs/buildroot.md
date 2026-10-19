# Add the SDK to Buildroot

Use the external tree at `examples/linux/buildroot` from a source checkout.
It builds the native device service and your generated hardware adapter with
Buildroot's target compiler, OpenSSL and C++ runtime. Keep this SDK checkout and
your product description with the other versioned inputs of your firmware build.

Product management is included by default, with Buildroot's Python, aiohttp and
psutil packages. It provides equipment discovery, product authentication and the
on/off controls. The native adapter and device service are separate processes.
Use aiohttp 3.14.4 or newer and yarl 1.25.1 or newer in product firmware.
For Buildroot 2026.08, apply the supplied recipe patch shown below before the
first build. It updates those packages and their source and license hashes.
The optional full OpenAVC server is packaged separately; see
[building the server bundle](building.md#build-the-optional-server-bundle).
The reference filesystem uses Python 3.14; the current offline server bundle
targets Python 3.12. A full-server Buildroot image needs a matching Python/server
build and its own startup and hardware-control checks.

## Use your existing firmware configuration

Add the SDK external tree to your existing `BR2_EXTERNAL` list, then enable
**External options → OpenAVC native hardware integration → openavc-sdk** in
`menuconfig`. Select your product description using its absolute path. Leave
**install invented amplifier examples** disabled for a product firmware build.

For example, from the SDK checkout, with your Buildroot source and output paths:

```sh
make -C /path/to/buildroot O=/path/to/firmware-output \
  BR2_EXTERNAL="$PWD/examples/linux/buildroot" menuconfig
make -C /path/to/firmware-output
```

If your firmware already uses an external tree, retain its path in the
space-separated `BR2_EXTERNAL` value. The package requires Linux ARM64 or x86-64,
C++, threads, shared-library support, GCC 8 or newer and OpenSSL 3. The supplied
reference configurations use Buildroot 2026.08 and its Bootlin glibc stable
2025.08-1 toolchains. Qualify a different BSP/toolchain separately.

| Location | Installed files |
|---|---|
| Target `/usr/bin/openavc-device-service` | Native equipment service. |
| Target `/usr/bin/openavc-oem` and Python site-packages | Product-management entry point, Python package and example settings page. |
| Target `/usr/share/openavc/` | Generated model, model provenance and core contract identity. |
| Staging `/usr/include/openavc/` | Public adapter headers and your generated `commands.h`. |
| Staging `/usr/lib/openavc/` | `libadapter.a`, `libopenavc_runtime.a` and `libhttp.a`. |
| Target `/usr/share/licenses/openavc-sdk/` | SDK and bundled-source notices. |

Your product application implements the generated callbacks. In its CMake project,
enable C and C++, then use the installed target:

```cmake
find_package(OpenAVC CONFIG REQUIRED)
target_link_libraries(your_product PRIVATE OpenAVC::adapter)
```

The target supplies the generated header path and native link dependencies.
Build with your firmware's toolchain/sysroot, as for the SDK package. See the
[hardware mapping guide](hardware-integration.md).

The recipe starts no services, provisions no credentials and creates no unit
identity. Integrate startup and persistent storage through your product's normal
firmware facilities using the [firmware guide](firmware.md). When the model or
SDK source changes, use Buildroot's `openavc-sdk-dirclean` and
`python-openavc-oem-dirclean` targets before rebuilding so their local source
copies are refreshed. If your firmware packages the Python manager separately,
disable **include product management**; the native package needs no target Python.

## Build the reference filesystem

The reference configurations make an evaluation filesystem with the invented
C/C++ amplifiers, product management and Python. They do not contain
a bootloader, kernel or OpenAVC server. Use a Linux
x86-64 build host for the selected Bootlin toolchains.

Install the host prerequisites from the
[Buildroot manual](https://buildroot.org/downloads/manual/manual.html#requirement).
From the SDK source directory:

```sh
mkdir -p build
curl --fail --location https://buildroot.org/downloads/buildroot-2026.08.tar.xz -o build/buildroot.tar.xz
echo '87aaca4164ea9d5c8085854953018263f7963f07c22e73a2a2185cc98c581c34  build/buildroot.tar.xz' | sha256sum -c -
tar -xf build/buildroot.tar.xz -C build
patch --dry-run -d build/buildroot-2026.08 -p1 --forward < examples/linux/buildroot/patches/0001-update-python-http-dependencies.patch
patch -d build/buildroot-2026.08 -p1 --forward < examples/linux/buildroot/patches/0001-update-python-http-dependencies.patch
make -C build/buildroot-2026.08 O="$PWD/build/firmware" \
  BR2_EXTERNAL="$PWD/examples/linux/buildroot" openavc_aarch64_defconfig
make -C build/firmware -j2
make -C build/firmware legal-info
python3 examples/linux/buildroot/export.py --buildroot-output build/firmware --out build/exported
```

Select `openavc_x86_64_defconfig` instead for x86-64, using a separate output
directory. Initial builds download the selected compiler and package sources.
The export retains Buildroot's `legal-info/` manifests, collected notices, source
archives and any warnings about unavailable material. Read its `README` and
`manifest.csv` with `buildroot-packages.json` when preparing your firmware
inventory. The reference recipe patch is included in `buildroot-patches/`.
Buildroot 2026.08 also archives the local SDK and product-manager
source inputs. These recipes apply their source allowlists to both the build
copy and the source archive, so an output directory inside the SDK checkout
cannot be copied into its own archive.
Keep Buildroot's `dl/` cache with your build inputs for subsequent offline builds.
The local SDK source is copied using an allowlist; private SDK `build/` output
is excluded.

The exporter copies the installed example binaries without recompiling, records
their hashes and includes the reference `rootfs.tar`, build configuration and
Buildroot's package/dependency information. It does not run them.
The [Buildroot workflow](https://github.com/open-avc/openavc-sdk/blob/main/.github/workflows/buildroot.yml)
shows execution on the matching native CPU in that filesystem, with an isolated
loopback network and an unprivileged account. The unbooted test filesystem gets
private basic `/dev` nodes and its own `/proc` in place of mounts supplied during
normal boot. Its checks use the supplied
`try-example.py --run-built` command for C and C++ service forms and the C
in-process form. They then run the equipment-only firmware startup check against
the installed Python package: product authentication, hardware restart, retained
settings and orderly shutdown. Check the workflow result for your selected SDK revision.

Running those files in a Linux container filesystem verifies this package path;
it does not boot a manufacturer's firmware or exercise its AV hardware and load.
Follow [firmware testing](qualification.md) before shipping your product.
