# Start OpenAVC in Linux firmware

Your product application owns the hardware. Run it as its own service, with the
SDK adapter on its hardware event loop. Run the Python product-management service
alongside it. That service supervises the native device service and the optional
OpenAVC server. Restarting OpenAVC management must not restart your product's
audio/video application.

The supplied example uses an invented amplifier and loopback network listeners.
It demonstrates startup, shutdown, process separation and persistent storage.
It does not configure a manufacturer's hardware, network or firmware updater.

## Run the firmware example

On Linux, install the [build prerequisites](building.md), activate the
[Python tools environment](building.md#python-tools), then install the SDK's
Python package and build the example:

```sh
python3 -m pip install .
python3 try-example.py --build-only
python3 examples/linux/check.py --out build/firmware-device
```

This runs the product without a terminal and checks command/readback, graceful
shutdown, unavailable feedback while hardware is stopped, a new hardware session
after restart, and retained settings/data after a management restart. It stops
its processes and retains a report and private logs under the output directory.
The public product checker observes both hardware unavailability and management
connection loss, then verifies recovery without sending commands. Its reports
are under `product-hardware-reconnect/` and `product-management-reconnect/`.
Use [reconnect observation](product-testing.md#observe-connection-loss-and-recovery)
to perform the same check against your product.
Checks put the local socket in short temporary storage under `/tmp` and remove
that storage after a successful shutdown. A failed run retains it for inspection;
its path is recorded in `staging.json`. Reports and persistent test data remain
in the selected output directory.
For the C++ source example, build with `try-example.py --language cpp --build-only`
and add `--language cpp` to the firmware check. Prebuilt library archives supply C.

With the compatible OpenAVC server installed in the same Python environment,
also run:

```sh
python3 examples/linux/check.py --with-server --out build/firmware-server
```

Use the [offline server bundle](building.md#build-the-optional-server-bundle)
for the pinned server and web interfaces. The full-server check also starts
OpenAVC, confirms local hardware reservation and checks that the saved server
setting is restored after management restarts. It creates no cloud connection.

The check uses the current unprivileged account. When invoked as root in a
container, it runs the product processes as `nobody`; `--service-user` selects
another existing account. Use a dedicated service account in firmware. Keep
the output directory private: its `product/private/` directory contains generated
example credentials and keys. Programs, config and data are separate; the check
does not install a host service or modify your existing product.

## Stage files and systemd units

To create the files without starting anything:

```sh
python3 examples/linux/stage.py --with-server --out /var/tmp/openavc-product-example
```

Omit `--with-server` for equipment-only firmware. `--python` selects an already
installed Python environment. `--identity` accepts an existing unique unit
identity; otherwise this example generates one and retains it in the configuration.
Existing output directories are refused. Staging never rotates an existing
identity or credential.
`--runtime-dir` selects a separate short runtime path, such as `/run/my-product`.
It defaults to the staging directory's `run/`. The native Unix-socket path must
fit Linux's socket-address bound; staging reports an overlong path before writing
the product files. The automated checks select a short path for you.

| Directory | Contents and lifetime |
|---|---|
| `program/` | Compiled hardware example, device service, generated model, supplied runtime libraries and license notices. Read-only while running; update them together. |
| `private/` | Unit configuration, product credentials, management credential and test certificate/key. Restrict access to the service account; preserve through ordinary updates. |
| `data/` | Management settings, OpenAVC configuration/projects, installed drivers/plugins and bounded SDK diagnostic logs. Persistent and writable. |
| `run/` | Hardware socket and temporary files. Writable; suitable for volatile storage in firmware. Recreate it at boot with the correct ownership. |
| `systemd/` | Generated hardware/management units and a tmpfiles configuration for the runtime directories. |
| `staging.json` | Selected identity/account, core contract pin and program-file hashes. |

The staging example keeps these directories under one prefix for inspection.
Map them into your firmware's program, protected configuration, persistent data
and runtime partitions. Set the paths in both the management configuration and
service units. The generated paths are absolute; moving the directory requires
updating them. For these templates, paths and names use letters, numbers,
underscore, dot, slash and hyphen.

The hardware unit uses the example's `--daemon` mode. It ignores stdin, processes
SIGTERM/SIGINT on the product event loop, and drains accepted work before releasing
the adapter. Replace that command with your application's startup command when
integrating your hardware. Both C and C++ examples implement this behavior.

The management unit starts after the hardware unit and has its own restart policy.
Its filesystem view is read-only except for the data and runtime directories.
Temporary files use `run/tmp`. Both units use an unprivileged account and bounded
restart attempts. The manager receives SIGTERM first and shuts down its own
children; systemd's `KillMode=mixed` provides final group cleanup if shutdown fails.
See the upstream [shutdown reference](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html)
and [execution settings](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html).

On a disposable Linux host running systemd, test the generated units with:

```sh
python3 examples/linux/check-systemd.py --with-server --out build/firmware-systemd
```

This command requires passwordless `sudo` (or root with an unprivileged
`--service-user`). It refuses existing services with the example's names, links
its units for this boot only, starts/stops/restarts them, inspects the service's
mount namespace, and removes its unit links afterward. It needs `systemctl`,
`systemd-analyze`, `journalctl`, `nsenter` and `findmnt`. It checks the filesystem
restriction, hardware restart/session change, local server startup, retained
settings and ordered shutdown. Do not run it on a product already using these
service names. The direct-process check above does not claim systemd validation.

For a firmware image using systemd, review the generated units, install them and
the tmpfiles configuration through your image build, and enable the management
unit. On another supervisor, preserve the same process ownership, startup order,
readiness, shutdown and storage behavior. The SDK does not require systemd.

## Provision each unit

Use [per-unit provisioning](provisioning.md) for the reusable initialization and
management-credential replacement helper. The staging example uses it under
`private/unit/`; its configuration records the resulting credential-file path.

Keep the model ID shared across units of the same model. Give every physical unit
its own stable identity, derived from your factory identity or generated once and
stored in protected persistent configuration. Do not clone the staged `private/`
directory across products. A project import or administrator reset must not turn
one unit into another unit's identity.

The example creates separate random product and management credentials and a
seven-day self-signed certificate for `localhost` and `127.0.0.1`. These are for
local evaluation. Your product should use its existing credential store through
the documented callback and its normal certificate provisioning process.

The equipment certificate must cover the network names/addresses used to reach
the product and `127.0.0.1` for the native connection. The configured `ca_file`
must trust that chain. Keep the private key and management token out of browser
responses, projects, shared logs and firmware images distributed to multiple units.
The full OpenAVC server's browser certificate is managed separately through its
normal HTTPS settings; the equipment certificate does not configure Programmer HTTPS.

## Certificates and credentials

The product manager reads equipment TLS material and its management credential
at startup. To replace them, prepare a complete matching certificate/key and
trust chain, stop management cleanly, replace the protected files using your
firmware's atomic configuration update, then restart management and verify both
local hardware and remote equipment access. The hardware application can keep
running. Do not replace only one file in a certificate/key/trust set while the
service is starting.

Use the normal product-account controls to revoke an old controller's equipment
credentials when replacing that controller. Stopping its OpenAVC process alone
does not prevent someone starting it again with still-valid credentials.
Product login, management access and the Programmer login are separate; see
[Product controls](product-controls.md#programmer-login).

## Firmware updates, rollback and reset

Package the adapter, generated model, native service, Python integration and
optional OpenAVC server as a tested set. Keep their hashes and the SDK/core pin
with your firmware version. Firmware owns updates; the included OpenAVC server
directs updates through the product.

Before changing the hardware application or SDK files, stop accepting new control
work and wait for accepted hardware operations to complete. Then stop management,
followed by the hardware application when it needs updating. A timeout does not
prove a physical operation was cancelled. Check the hardware's actual outcome
before deciding to retry an operation or continue an update.

The example's service stop timeouts are demonstration settings. Measure your
product's completion and shutdown bounds. The SDK manager currently gives each
supervised child ten seconds to stop before treating shutdown as failed; do not
rely on a larger systemd timeout to extend that internal deadline.

Preserve unit identity, private configuration and the full data directory during
ordinary firmware updates. Take a consistent data backup with management stopped
before the first start of a new firmware version. A rollback must restore a
compatible software/data combination; downgrading binaries alone is not evidence
that a newer project or settings format remains readable. Keep the pre-update
backup until the product's acceptance checks pass.

Define user reset and factory/service reset separately. A user-project reset
should preserve the physical unit's identity and firmware hardware mapping.
Your factory procedure must explicitly handle account revocation, Programmer
credentials, cloud unpairing when used, and any certificate replacement.
`POST /oem/repair` repairs SDK settings; it is not a complete product factory reset.

### Run the settings recovery example

After building the hardware example, run:

```sh
python3 examples/linux/check.py --storage-recovery --out build/firmware-storage-device
python3 examples/linux/check.py --with-server --storage-recovery --out build/firmware-storage-server
```

The second command requires the matching server installed. Both source and
prebuilt library checkouts supply this exercise. It changes only the new staging
directory it creates and refuses an existing output directory.

The check stops management, then separately supplies a truncated settings file
and an interrupted-save marker. On each restart, equipment access and the local
server must remain stopped while the hardware application keeps running. A
settings change and unauthenticated repair must be refused. Authenticated repair
must retain the damaged input and leave every OpenAVC access setting disabled,
including after another restart.

The check explicitly restores the earlier access choices and verifies retained
unit identity, credentials and hardware control. With the server included, it
also verifies the original Programmer account and saved project variable. The
hardware application's own counters must show that refused commands were never
executed. Reports and private logs remain in the selected output directory.

This demonstrates detected settings damage and interrupted-save recovery. It
does not interrupt physical storage writes, remove power, repair a filesystem or
perform a factory reset. Repeat those checks through your product's storage and
recovery facilities.

### Run the application update and rollback example

On an isolated Linux host, use a source checkout and build both hardware examples:

```sh
python3 try-example.py --language c --build-only
python3 try-example.py --language cpp --build-only
python3 examples/linux/check.py --update --out build/firmware-update-device
python3 examples/linux/check.py --with-server --update --out build/firmware-update-server
```

These commands create new staging directories and start only their own example
processes. The prebuilt library archive does not include the C++ source needed
for this exercise. Run the full-server case with the matching server and SDK
Python packages installed, as for the startup check above.

The check stops management and hardware before taking a data snapshot. It
replaces the C hardware application with the separately built C++ application,
which implements the same product model. It then starts the updated application
and verifies retained product settings and hardware control. With the server
included, it also creates an ordinary Programmer account and a persistent project
variable, and verifies both after the application update.

Next it changes the candidate's data and disables Programmer access, stops both
processes, and restores the original application and earlier project data.
The matching core's existing backup functions capture and restore the project
with its writers stopped. Current product settings and credentials stay in place:
rollback must not undo an access restriction. The check verifies that Programmer
remains disabled, then explicitly re-enables it through product management to
verify the earlier saved variable and working authenticated hardware control.
The old and candidate directories, process logs and `report.json` remain in the
chosen output directory. Treat that directory as private: it contains generated
example credentials and complete data snapshots.

This exercises an orderly application replacement and project rollback with the same
OpenAVC version and data format. It does not flash firmware, change OS partitions,
migrate a newer server's data, interrupt an in-flight hardware operation or test
power loss. The directory-copy/swap sequence and pinned core backup calls are
demonstrations in stopped, disposable services, not a product firmware updater
or a portable backup-upload interface. Exercise the complete
firmware update, interrupted-write recovery, downgrade and factory reset through
your own updater and recovery facilities before shipping.
