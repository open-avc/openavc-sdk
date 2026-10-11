# Test the integration on your firmware

Start with the supplied examples, then repeat the relevant checks on your actual
firmware with its normal service account and audio/video workload. A CPU family
alone does not establish compatibility with a firmware image.

## Build and describe

Run the doctor on the build environment. Build with the product's toolchain and
sysroot, then use `--build-only` and `--inspect` after changing the hardware model.
Confirm that every advertised command, parameter, state and channel matches your
product, including optional values, enum labels, units and numeric ranges.
Check shared-library resolution on the target and include required license
notices with redistributed components.

## Commands and feedback

Choose safe product operations and verify them through the actual hardware API.
Confirm that an OpenAVC command reaches the hardware once, that refusal reports
failure, and that asynchronous completion reports the final hardware result.
Test independent changes through the front panel or your own application.
Readback must change without requiring another OpenAVC command.

Pause or fail hardware readback. OpenAVC must show unavailable feedback, rather
than retaining a stale successful status. Exercise the normal maximum channel
roster and data values. Add/remove dynamic channels and restart the hardware
session; confirm that the reported roster and command outcomes remain accurate.

## Both uses and product controls

With the product's server stopped, discover and add the equipment to ordinary
OpenAVC. Use normal product credentials. Check denied credentials, direct-address
access with discovery off, and withdrawal when equipment access is disabled.

If the firmware includes OpenAVC, start it and check your product's hardware,
another SDK product and ordinary driver-controlled equipment. Confirm that no
other OpenAVC server can discover or command this product's local equipment while
its server runs. Stop/restart the server during an asynchronous operation; verify
that the hardware operation is neither duplicated nor abandoned.

Keep the full Programmer and panels enabled unless the product requires an
explicit access policy. If access controls are used, test existing browser
sessions, panel WebSockets and optional cloud sessions when changing them.
Check that saved settings, actual service state and displayed errors agree.

## Persistence, updates and resources

Test project import and Backup Restore separately, including saved variable
state. Retain the product identity and hardware mapping through project
replacement, product restart and firmware upgrade. For different models,
review compatibility before using Reassign Driver; inspect scripts and bindings
that rely on product-specific behavior.

Measure startup, peak and sustained memory, CPU, storage growth, descriptor/thread
counts, command latency and readback age alongside the normal product workload.
Test network loss, address changes, unavailable storage and process exits.
Run an endurance test for the duration appropriate to your product and preserve
the exact binaries, configuration and independent hardware observations used.

The optional OpenAVC Cloud experience needs its own end-to-end acceptance:
pairing, AI editing, Programmer/panel tunnels, backups/recovery and the cloud
features your firmware offers. The isolated Docker example does not exercise
cloud accounts or qualify an OEM firmware image.

Record the SDK commit, `sdk/contract/origin.json`, runtime package hashes, compiler,
OS/libc/OpenSSL versions, firmware version and workload with every result. Describe
what was actually tested. A build, simulated example and physical product test
are different kinds of evidence.
