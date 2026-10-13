# Add OpenAVC controls to your product

The SDK provides two capabilities. A product can make its equipment available to
an OpenAVC server elsewhere, and it can optionally include the full OpenAVC server.
Use the same hardware adapter for both.

When OpenAVC runs on your product, your product's own hardware appears
automatically in every project, marked **Product hardware**. For an amplifier,
that means its audio channels and controls. Other OpenAVC servers cannot
discover or control that hardware while the local server is enabled.
The normal Programmer and panel interfaces remain available. Stopping OpenAVC
restores the product's saved equipment-access settings.

If the server fails to start, remote equipment control stays off. The operator
can retry starting OpenAVC or turn off **Run OpenAVC on this product** to allow
another server to control it.

## Runtime components

Your application owns the hardware and its event loop. It links the C-facing
adapter, publishes readback and implements the generated hardware callbacks.
The supplied native device service communicates with that application over a
Unix socket. The `openavc-oem` Python service provides network authentication,
discovery, product controls and optional OpenAVC process supervision.

The complete managed integration needs Linux, Python 3.12 or newer, `aiohttp` and
`psutil`, even when the full OpenAVC server is omitted. Your hardware application
and the native device service do not embed Python. Including the full server
also requires OpenAVC and its dependencies in the selected Python environment.
Use the [server bundle builder](building.md#build-the-optional-server-bundle)
to collect them for offline installation into your firmware image. Product
startup does not download software.

For a development or staging run, activate the
[Python tools environment](building.md#python-tools) and install product management:

```sh
python3 -m pip install .
python3 -m openavc_oem --config /etc/product/openavc.json
```

For a firmware image, package it in the product's Python environment through
your image build. The [Buildroot package](buildroot.md) and
[Yocto/OpenEmbedded layer](yocto.md) supply recipes.

Start your hardware application before this service. Run both under your normal
firmware service supervisor. The SDK starts/stops its native device service and
the optional OpenAVC server; it does not own the manufacturer's application.
Give the service account access to the hardware socket, certificates and its
writable data directory. Keep that directory across firmware updates.
The [Linux firmware example](firmware.md) supplies runnable startup/storage checks,
service templates and provisioning guidance.

## Product configuration

The paths below are example installation paths. Create the files in your firmware
image or provisioning process and substitute your actual paths.

```json
{
  "identity": "stable-identity-for-this-physical-unit",
  "data_dir": "/var/lib/product/openavc",
  "model": "/usr/share/product/openavc/model.json",
  "device_service": "/usr/lib/product/openavc/openavc-device-service",
  "bridge_path": "/run/product/openavc.sock",
  "certificate": "/etc/product/openavc-server.pem",
  "ca_file": "/etc/product/openavc-ca.pem",
  "management_token_file": "/etc/product/openavc-management.token",
  "equipment_port": 9443,
  "management_port": 9081,
  "login": {"username": "product-user", "password": "provision-a-product-password"},
  "server_python": "/usr/lib/product/python/bin/python3",
  "firmware_version": "your-firmware-version"
}
```

- Use absolute paths. `certificate` contains the PEM certificate and private key.
  `ca_file` contains the trusted CA for the native loopback endpoint. Include
  `127.0.0.1` in that endpoint's certificate SAN, together with the product's
  network names/addresses used for remote equipment control.
- Generate a separate management credential of at least 32 random characters.
  Store the credential file and private keys with access restricted to the
  product's service account. Do not give the management credential to a browser.
- Keep `identity` stable for a unit. Use different identities for different units.
  The product model has a separate model ID shared by units of that model.
- Omit `server_python` and `firmware_version` to omit the full server. Remote
  equipment integration still works. A request to start the absent server returns
  a clear refusal.
- `equipment_bind` defaults to `0.0.0.0`. `advertise_addresses`, when supplied,
  selects IPv4 addresses for equipment advertisement. The management API always
  binds to loopback in production.
- `server_environment` passes ordinary OpenAVC environment settings to the
  optional server. The SDK supplies its project/data paths, permanent equipment
  manifest and supervision credentials. The normal OpenAVC interface and account
  setup remain available by default.

For an existing product credential store, construct the service in your Python
backend and provide a synchronous credential check:

```python
from openavc_oem.manager import Manager

manager = Manager(configuration, credential_check=check_product_credentials)
await manager.run()
```

`check_product_credentials(username, password)` returns a boolean. It must be
fast and must not block on network I/O. This checks credentials entered in
OpenAVC's equipment connection settings; it does not authenticate Programmer
users. The SDK's optional example HTML/login is off by default; a production
product uses its own authenticated management UI.

## Programmer login

The included server uses normal OpenAVC administrator setup and login. On first
use, the installer creates the Programmer login. It is retained in the server's
data directory across stop/start. No OpenAVC Cloud account is required.

| Control or credential | What it does |
|---|---|
| Product login / `credential_check` | Authenticates another server controlling the equipment. Your product's own UI retains its own authentication. |
| Management token | Lets your product backend change SDK settings through `/oem/*`. It is not a Programmer password or an OpenAVC API credential. |
| `programmer` setting | Enables or disables Programmer access. It does not create, change or remove the administrator login. |
| OpenAVC administrator login | Authenticates access to the Programmer and protected OpenAVC APIs. |

Your product can keep the normal first-run page, or provide a setup form in its
own authenticated UI. With the server running and Programmer access enabled,
your backend can call the existing OpenAVC API at the server's configured address:

1. Read `GET /api/auth/required`. A `state` of `setup` means the server has no
   administrator credential yet.
2. Submit the installer's chosen `username` and `password` as a JSON object to
   `POST /api/auth/setup`. The password must contain at least eight characters
   after surrounding whitespace is removed. This succeeds only before initial
   setup; HTTP 409 means a credential already exists. Do not overwrite it.
3. Open the Programmer for normal sign-in. Subsequent password changes use
   **Settings > Access > Programmer login**, or the authenticated
   `PATCH /api/system/config` API with `auth.programmer_password`.

These requests go to OpenAVC, not the management port. Initial setup needs no
existing OpenAVC credential; later changes require OpenAVC authentication. Your
backend must still authorize the person using your product's setup form. Use a
local connection from that backend, or HTTPS when crossing a network, and keep
passwords out of logs.

For firmware that manages the Programmer credential itself, `server_environment`
can supply the normal `OPENAVC_PROGRAMMER_USERNAME` and
`OPENAVC_PROGRAMMER_PASSWORD` settings before starting the server. This skips
first-run setup. Environment-managed credentials cannot be changed through the
Programmer; your software owns changes and must restart the server to apply a
changed environment. Provision credentials per product rather than shipping a
shared default password.

The SDK does not provide automatic single sign-on between your product UI and
the Programmer. Supplying the same credentials does not share browser sessions,
and `credential_check` does not connect a product account store to Programmer
authentication. The complete example uses the normal OpenAVC setup page.

## Management API

Your product's backend calls `http://127.0.0.1:9081/oem/settings` with
`Authorization: Bearer <management-file-contents>`. A GET returns saved settings,
their revision, actual server status, actual equipment availability and any error.

A POST changes one setting:

```json
{"revision": 0, "field": "server", "enabled": true}
```

Use the revision from the preceding GET. A stale revision returns HTTP 409;
refresh the status before deciding whether to resubmit. A success response
includes the resulting status. A saved setting that could not be applied returns
an error together with `state`; show that error and the actual state in your UI.
Do not show a running server solely because `server` is true. Use
`server_running`, `server_responsive` and `interfaces_observed`.

| Field | Effect |
|---|---|
| `equipment` | Set equipment discovery and remote access together. Recommended primary control. |
| `server` | Start or stop the included OpenAVC server. Recommended primary control. |
| `discovery` | Advertise equipment to other OpenAVC servers. Advanced control. |
| `remote_access` | Permit authenticated remote equipment control. Advanced control. |
| `programmer` | Permit project editing, including cloud programming. Default on. |
| `panel` | Permit touch-panel access. Default on. |

Turning off Programmer access does not stop the project, schedules, macros or
hardware control. Interface access is optional product policy; the normal full
server example includes both Programmer and panel access.
These settings also apply through cloud tunnels. Cloud configuration restores
require Programmer access; normal runtime control and monitoring continue when
the Programmer is off. See [OpenAVC Cloud](cloud.md).

`GET /oem/diagnostics` returns current status with credential values excluded.
Access requires management authentication. `POST /oem/repair` repairs damaged or
interrupted settings by
disabling all OpenAVC permissions and retaining the damaged settings for
inspection. Show this only when `settings_valid` is false. It does not erase
the OpenAVC project or change the physical unit's identity. The
[settings recovery example](firmware.md#run-the-settings-recovery-example)
exercises this operation and the required explicit access choices afterward.

## Updates and recovery

The manufacturer updates the adapter, native service, Python service and optional
OpenAVC server as part of its firmware. OpenAVC identifies updates as firmware
managed and points to the product's update process. Keep tested component
versions together and retain the OpenAVC data directory when updating.

Your firmware supplies the connection to your product's hardware. Creating or
importing a project cannot replace that connection or remove the product hardware.
Ordinary project import reuses programming; Backup Restore also restores saved
variable state. For a different product model, use **Reassign Driver** and select
**Product hardware** to use the new product's controls. OpenAVC checks compatibility
and refuses the change if those controls already have programming or saved settings.
Review scripts for assumptions about the original product.

The installer chooses which product runs OpenAVC. Starting a second server
creates another control system; it does not provide automatic takeover.
