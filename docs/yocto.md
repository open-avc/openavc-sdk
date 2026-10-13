# Add the SDK to Yocto/OpenEmbedded

The layer at `examples/linux/yocto/meta-openavc` packages the native hardware
adapter, device service and Python product manager. Use it with the Wrynose
release series and the `meta-oe` and `meta-python` layers from meta-openembedded.
It targets Linux ARM64 and x86-64.

The optional full OpenAVC server is supplied separately. The reference build
uses Python 3.14, while the current [offline server archive](building.md#build-the-optional-server-bundle)
requires Python 3.12. Include a matching Python/server environment and qualify
its startup and hardware control before offering the full server in that firmware.

## Add to your firmware build

Keep a selected SDK source checkout with your other firmware inputs. From your
initialized BitBake build environment, add its layer:

```sh
bitbake-layers add-layer /path/to/openavc-sdk/examples/linux/yocto/meta-openavc
```

Add `openavc-sdk` and `python3-openavc-oem` to your image's `IMAGE_INSTALL`.
Select the exact published SDK commit through `OPENAVC_SDK_REVISION` in your
configuration. The native and Python recipes use that same revision.

Supply your model through an `openavc-sdk_%.bbappend` in your product layer:

```bitbake
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI += "file://product.json"
OPENAVC_PRODUCT = "${UNPACKDIR}/product.json"
```

Place your description in the append's `files/product.json`. Build your hardware
application as a separate recipe with `DEPENDS += "openavc-sdk openssl"`.
In a CMake project that enables C and C++, link the exported target:

```cmake
find_package(OpenAVC CONFIG REQUIRED)
target_link_libraries(your_product PRIVATE OpenAVC::adapter)
```

The target supplies the generated header path and native link dependencies.
The `openavc-example-consumer` recipe shows this with the invented amplifier.
See [hardware integration](hardware-integration.md) for the callbacks and thread
rules. Leave the SDK's `examples` package option disabled in product firmware.

| Package or location | Contents |
|---|---|
| `openavc-sdk` | Native device service, generated model and provenance, source notices. |
| `python3-openavc-oem` | Product manager and its Python runtime dependencies. |
| Recipe sysroot `/usr/include/openavc/` | Public headers and generated `commands.h`. |
| Recipe sysroot `/usr/lib/openavc/` and `/usr/lib/cmake/OpenAVC/` | Native libraries and exported CMake targets; the library directory follows the BSP. |
| `openavc-sdk-examples` | Optional invented C/C++ hardware applications. |

The recipes do not start services or provision unit identities and credentials.
Use your firmware's normal service and storage facilities, following the
[firmware guide](firmware.md). Keep the product's persistent settings and identity
separate from software replaced by an update.

## Build the reference filesystem

Use a Linux x86-64 build host with the
[Yocto host prerequisites](https://docs.yoctoproject.org/6.0/ref-manual/system-requirements.html).
Include the host's `python3-websockets` package for the shared-state mirror.
The preparation tool fetches exact OpenEmbedded Core, BitBake and meta-openembedded
revisions and creates a separate build directory. It enables the upstream
shared-state mirror and removes temporary completed build work to reduce disk use.
Initial builds require internet access. Existing build directories are not overwritten.

From a committed SDK source checkout whose revision is available on GitHub:

```sh
python3 examples/linux/yocto/setup.py --out build/yocto \
  --machine qemuarm64 --sdk-revision "$(git rev-parse HEAD)" --tasks 4
sdk_checkout="$PWD"
source build/yocto/layers/openembedded-core/oe-init-build-env \
  "$PWD/build/yocto/build" "$PWD/build/yocto/layers/bitbake"
bitbake -c unpack openavc-sdk python3-openavc-oem openavc-example-consumer
bitbake openavc-sdk openavc-example-image
image_root=$(bitbake-getvar -r openavc-example-image --value IMAGE_ROOTFS)
image_dir=$(bitbake-getvar -r openavc-example-image --value DEPLOY_DIR_IMAGE)
image_link=$(bitbake-getvar -r openavc-example-image --value IMAGE_LINK_NAME)
python3 "$sdk_checkout/examples/linux/yocto/export.py" \
  --rootfs "$image_root" --image "$image_dir/$image_link.tar" \
  --generated "$image_dir/openavc-sdk" --configuration "$BUILDDIR/conf" \
  --out "$sdk_checkout/build/exported"
```

For x86-64, select `qemux86-64` in a separate build directory. These reference
configurations use Yocto's `cortexa57` and `x86-64-v3` CPU settings respectively.
Run their binaries only on a compatible processor. For your product, use your
board-support package's machine and CPU settings.

The reference filesystem includes the native C/C++ examples, a separately
linked product application, Python management and the checks. It is not a
bootable board image and does not include the full OpenAVC server.

`--tasks` selects concurrent BitBake tasks; each compiler invocation uses up to
two jobs. The automated reference builds use four tasks on a four-core Linux
host with 15 GiB RAM. Reduce this setting for a smaller build host. Keep the
build directory to reuse completed work, and reenter its environment to resume
with the same `bitbake` command. You do not need to run the preparation tool again.

The exporter copies the installed binaries, generated files and filesystem
without recompiling, and records their hashes. The
[Yocto workflow](https://github.com/open-avc/openavc-sdk/blob/main/.github/workflows/yocto.yml)
builds each filesystem, transfers it to the matching native processor, verifies
the hashes and runs the hardware examples and firmware lifecycle checks with an
unprivileged account and isolated loopback network. Check that workflow's result
for your selected SDK revision. Compilation and native execution do not qualify
a board boot, your BSP or your product's AV workload; use the
[firmware testing guide](qualification.md) for those checks.
