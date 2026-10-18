# Keep an integration current

Select an SDK revision and build its matching OpenAVC server. The SDK records
the exact core commit in [`sdk/contract/origin.json`](../sdk/contract/origin.json).
The complete-system launcher and offline server builder use that commit.
An OpenAVC version number alone does not identify the tested source combination.

The current kit is an engineering preview without a versioned SDK release.
Use a committed SDK checkout and retain its commit ID with your firmware build.
The public OpenAVC release you already use may predate manufacturer integration;
use the server selected by the kit's pin when evaluating these examples.

## Select matching components

| Component | Combination for this checkout |
|---|---|
| OpenAVC server, separate or product-hosted | Core commit `bd3e70407e1043f20d80742e5ee666b1ab29ffa0`, recorded in `sdk/contract/origin.json`. |
| Equipment protocol | `openavc-device-1`. A different protocol is refused before control. |
| Product description and callbacks | Generate the model and compile the adapter together using this SDK checkout. |
| Native headers, libraries and service | Use one matching source build or supplied archive for the target CPU. |
| SDK Python package and optional server | Use the matching offline server bundle or the source builder's pinned inputs. |

Use the run for the SDK commit you select to confirm ARM64 and x86-64 results;
an untested branch or a different core commit is not a qualified combination.
The [build guide](building.md) lists each delivered form and its requirements.

After equipment restarts, OpenAVC reads its protocol and capabilities again
before accepting more commands. Added commands become available after reconnect.
Removed commands disappear from the command list and attempts to send them are
refused. Existing programming is retained; review any scripts, macros or panels
that refer to removed or renamed capabilities.

Run the [model update example](system-example.md#check-model-updates) to exercise
compiled additions and removals with normal Discovery/Add and Product hardware.
The example records the exact generated model, binaries and independent command
effects. Its successful run does not qualify your firmware updater.

## Record what you ship

Keep the SDK commit, core commit, product description, generated model, adapter,
native service, Python environment and web interfaces together in your firmware
build record. The prebuilt-library and server bundles each supply `bundle.json`
with artifact hashes and environment information. Preserve those records along
with your own firmware version, toolchain/sysroot and dependency inventory.
The prebuilt-library archive also supplies `native-build.json` with compiler,
CMake and link-input details; `bundle.json`'s `runtime_origins` entries identify the installed
binary/source packages supplying the redistributed libraries.
Archives built through `tools/build_native.py` also include `build-inputs/` and
the selected Docker image ID. The direct host build records the host's tools instead.
The server bundle also supplies `python-packages.cdx.json`, a CycloneDX inventory
of the delivered Python wheel archives. See its [coverage](building.md#build-the-optional-server-bundle)
before combining it with your complete firmware inventory.

Check the selected revision's [SDK checks](https://github.com/open-avc/openavc-sdk/actions/workflows/ci.yml).
That workflow builds native ARM64/x86-64 examples and archives, extracts the
libraries, runs product and firmware checks, exercises the complete systems and
installs the server bundle offline. Read the run for your actual commit.

Official package builds also provide [verifiable build records](building.md#build-the-optional-server-bundle).
Retain the archive, signed record and SDK revision together. These records identify
the producing workflow and source revision; they do not sign your complete firmware.

The hashes identify the produced files; they do not provide a firmware signature,
a complete software bill of materials or a reproducible-build guarantee. Apply
your product's signing and dependency-management process before distribution.

## Evaluate an update

1. Keep the currently qualified firmware and its build inputs available for
   recovery. Take a consistent backup of persistent OpenAVC data before a new
   firmware version first opens it.
2. Select the new SDK revision. Rebuild your adapter and generated model together,
   then build the server bundle from that SDK if your product includes OpenAVC.
   Do not replace the server independently of the integration you are qualifying.
3. Run the doctor, model generation and your selected
   [product checks](product-testing.md). Compare the actual command effects,
   readback, channel membership and resource measurements with your accepted
   firmware under the same AV workload.
4. Exercise equipment-only and product-hosted operation when both are offered.
   Check the controls in your product GUI, normal Discovery/Add, existing
   projects, authentication, panels, restart and recovery through your firmware
   update path. Include optional cloud workflows when your product enables them.
5. Retain the exact artifacts and results. Release the update through your
   product's firmware process after its acceptance checks pass.

Changing a command name, parameter type, state name or channel ID can affect
existing programming. Build validation checks the new description; it does not
prove that an installed project's scripts, macros and panel bindings remain
compatible. Test representative existing projects through normal import and
backup recovery. Preserve stable physical identity through ordinary updates.

For rollback, restore a compatible software/data combination. A newer data
format is not assumed readable by an older server. The
[firmware guide](firmware.md#firmware-updates-rollback-and-reset) describes the
storage and shutdown boundaries; qualify interrupted update and recovery using
your actual updater.

## How upstream changes are checked

The SDK's [upstream compatibility workflow](https://github.com/open-avc/openavc-sdk/actions/workflows/upstream.yml)
runs weekly and can be started manually. OpenAVC's
[manufacturer compatibility workflow](https://github.com/open-avc/openavc/blob/main/.github/workflows/sdk-compatibility.yml)
also checks relevant core changes against the maintained SDK. The shared check
records both resolved commit IDs, compares the canonical capability/protocol/DNS
sources, builds the native example, and runs the SDK's adapter and host tests.

These checks report drift or integration failures. They do not change the kit's
core pin, publish a release or update your firmware. A passing upstream check
is an early compatibility signal; it does not replace package, UI, AV-load,
upgrade or product-specific acceptance for a new firmware release.

The example/server builder uses the committed Python dependency locks in
`tools/requirements-server.txt` and `tools/requirements-build.txt`, plus the
base-image digests in the server, native-library and cross-build Dockerfiles. `tools/debian.sources`
selects dated Debian archives for these builders; package signatures remain
required. Check for dependency and security updates when selecting a new firmware
build, and deliberately update and qualify these pins together. The reference
build does not automatically adopt newer system packages.

When updating the SDK's core
pin, review these inputs together. Downloaded wheels must match their recorded
hashes, and the installed combination must pass `pip check`. Qualify changed
inputs through both native Linux package jobs before distributing a new bundle.
Do not remove the hash checks to get past a dependency mismatch.

From an SDK source checkout, compare a local OpenAVC checkout without changing
the SDK:

```sh
python3 tools/sync_contract.py --core /path/to/openavc --check-upstream
```

The command checks that checkout's committed `HEAD`, not its uncommitted edits.
Use `--check` instead to require both matching contract bytes and the exact
recorded core commit. A mismatch needs review before updating the pin or generated
files; do not suppress a failing comparison to continue a firmware build.
