# Prepare each product

Each physical unit needs a stable identity and its own local management
credential. Keep these in protected persistent storage across ordinary updates
and project resets. Product accounts, equipment TLS certificates and the OpenAVC
Programmer login have separate purposes; see [product controls](product-controls.md#programmer-login).

## Initialize unit files

Run the supplied helper on the product's Linux filesystem as its service account:

```sh
python3 provision-unit.py --out /var/lib/my-product/openavc-unit
```

The first call generates an identity and management credential. Later calls
validate and reuse them. The command prints the identity and credential-file
path, never the credential. Use those values for `identity` and
`management_token_file` in the [manager configuration](product-controls.md#product-configuration).

To use a factory identity, add `--identity`. Make it unique across your company's
models and units, for example `example:amplifier:serial-123`. Supplying that
identity on every startup also detects a configuration copied from a different
unit. A different supplied identity is refused without changing the files.

Your application can call the same helper directly:

```python
from openavc_oem.provisioning import provision_unit

unit = provision_unit("/var/lib/my-product/openavc-unit")
configuration.update(
    identity=unit.identity,
    management_token_file=str(unit.management_token_file),
)
```

The helper owns `unit.json` and `management.token` in that directory. It creates
the directory with mode `0700` and files with mode `0600`. Use your firmware's
normal ownership and service-account setup. Do not include an initialized unit
directory in an image installed on multiple products.

An incomplete or damaged directory is refused. Restore that unit's protected
configuration or finish its factory setup. Do not delete it as a normal startup
recovery action: that could change the unit's identity and access credential.
The helper starts no service and changes no product account or certificate.
An existing factory configuration store can supply the manager fields directly
instead of using these files.

## Replace local management access

Stop product management cleanly while leaving your hardware application running.
Replace its credential with:

```sh
python3 provision-unit.py --out /var/lib/my-product/openavc-unit --rotate-management-token
```

Have your product backend reread the credential file, then restart management.
Verify that the new credential reaches `/oem/settings` and the previous credential
is refused. `rotate_management_token(directory)` provides the same operation to
a Python application. It preserves unit identity. It does not rotate the product
account used by a remote OpenAVC server or the Programmer login.

## Product accounts and equipment certificates

Connect the manager's `credential_check` callback to your product's existing
account store. Change or revoke equipment access through that store. The callback
is checked on each remote equipment request. Update the equipment's username and
password in the controlling OpenAVC server's normal connection settings.

Use your product's certificate provisioning process for the PEM certificate/key
and trusted CA configured in the manager. Include its network names/addresses and
the native endpoint's `127.0.0.1` name in the certificate's subject alternative
names. The normal OpenAVC server controlling the product must trust the equipment
certificate. Keep certificate verification enabled.

For replacement, stop management, update the matching certificate/key and trust
files together through your firmware's configuration mechanism, then restart it.
Verify local hardware access and remote access using the new trust chain. Keep
private keys and the management credential out of browser responses, projects and
shared support reports. The equipment certificate does not configure the full
server's Programmer/panel HTTPS certificate.

## Offline startup and time

These operations require no Internet or cloud account. Prepackage the SDK and its
dependencies; the [offline server archive](building.md#build-the-optional-server-bundle)
supplies the optional server's Python packages and web interfaces.

TLS certificate validation needs a correct system clock. Establish trusted time
through your product's RTC, protected time setting or normal time service before
starting equipment control. Internet time service is not required. An expired or
not-yet-valid certificate must be corrected through product setup; disabling
verification is not the recovery procedure. Preserve identity and credentials
while correcting time or replacing a certificate.

Define factory/service reset separately from an integrator's project reset.
Ordinary project work must preserve this physical unit's identity and hardware
mapping. Your factory procedure owns account revocation, certificate replacement
and any cloud unpairing. [Firmware lifecycle guidance](firmware.md#firmware-updates-rollback-and-reset)
describes the storage and shutdown boundaries.

## Run the provisioning example

After [building the service example and installing product management](firmware.md#run-the-firmware-example),
run these checks on an isolated Linux development host:

```sh
python3 examples/linux/check-provisioning.py --out build/provisioning-device
```

With the compatible OpenAVC server installed in that Python environment:

```sh
python3 examples/linux/check-provisioning.py --with-server --out build/provisioning-server
```

The check starts the supplied hardware example and product manager, uses the
per-unit helper, changes and revokes a fixture product account, replaces the
management credential and equipment certificate, and checks expired and
not-yet-valid certificates. It uses verified TLS and independent hardware
counters, preserves unit identity and product data, and checks the diagnostic
export for secrets. The server variant also controls Product hardware after
replacement using the ordinary Programmer account. No system service is installed
and the system clock is not changed. All traffic stays on loopback; after setup,
the check needs no Internet connection.

Use a new output directory each time. Read `report.json` for the completed checks
and the failed step, if any. A TLS failure also records `certificate_verify_code`,
the OpenSSL certificate verification error number. The check uses strict
certificate validation on every supported Python version. The local output includes generated private keys and
credentials under `product/private/`; keep that directory private. Share only the
reviewed diagnostic export from `product/data/support.json`. Product processes
stop when the check finishes, including on failure.

The account file in this check is a disposable fixture demonstrating live
`credential_check` updates. Use your existing product account implementation in
firmware. The generated CA and short-lived certificates are local test material;
use your product's normal certificate provisioning process for production.
