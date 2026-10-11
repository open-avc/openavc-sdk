# Try the complete system

This example runs two invented amplifiers and ordinary TCP equipment on an
isolated Docker network. It does not connect to AV hardware. The C example is
the hardware process for each amplifier; one amplifier includes the optional
OpenAVC server and the other omits it.

Install Python 3.12 or newer, Docker and a running Linux container engine. Docker
Desktop can supply that engine on macOS or Windows. Native Linux Docker works
too. The first build needs internet access to obtain build dependencies and the
OpenAVC revision pinned in `sdk/contract/origin.json`. Running the example network
does not require internet or OpenAVC Cloud.

From the SDK checkout:

```sh
python3 try-system.py --arrangement external
```

Keep the terminal open. The launcher prints links to the normal Programmer,
panel and each product's example settings page, plus freshly generated product
credentials. Open the Programmer and set up its administrator login through
OpenAVC's normal first-run page. Signing into the product-settings page does
not sign you into the Programmer. See [Programmer login](product-controls.md#programmer-login)
for setup through your own software.

The project already contains both amplifiers and the ordinary TCP device. Open
the panel and press a control. Its adjacent value reports hardware readback.
In the Programmer, open a device to see its controls, channels and connection
status. The SDK equipment uses the ordinary device view.

On amplifier 1's product settings page, **Allow control from another OpenAVC server**
is on and **Run OpenAVC on this product** is off. Its server is installed but is
not running. Amplifier 2 shows that its firmware does not include a server.

Press Ctrl+C in the launcher terminal to stop and remove the example containers
and network. It retains the generated project, logs and product data under
`build/system-example`. That directory contains example credentials; keep it
private. The built Docker image remains cached for the next run.

## Run OpenAVC on the equipment

Stop the preceding example, then use a new output directory:

```sh
python3 try-system.py --arrangement embedded --out build/embedded-example
```

OpenAVC now runs inside amplifier 1. That amplifier appears as **Product hardware**,
while amplifier 2 and the ordinary TCP device connect remotely. The panel's controls
work the same way. Amplifier 1 cannot be removed from its own OpenAVC project.

Amplifier 1's product page reports that control from other servers is paused.
Other OpenAVC servers can no longer discover or control amplifier 1. Its normal
Programmer and panel remain available. Stop its server
on the product page to make the saved remote-equipment settings active again.
The manufacturer's hardware process continues running.

## Verify the supplied examples

These checks send commands only to the unchanged invented equipment:

```sh
python3 try-system.py --arrangement external --out build/check-external --check
python3 try-system.py --arrangement embedded --out build/check-embedded --check
```

Each check creates a fresh OpenAVC administrator account, verifies commands
against independent hardware counters, checks front-panel-style feedback, runs
normal Discovery and adds discovered equipment through the ordinary Add API.
It stops the example afterward and saves `checks.json`, `discovery.json` and logs.
Do not use the demonstration checker as a command test for a modified product.

Use `--port 19080` if the default localhost ports 18080 through 18084 are in use.
Use `--docker-context <name>` to select a Docker engine without changing your
active context. The Docker engine must run on this computer because the launcher
opens its published localhost ports.

Docker is an evaluation environment here. Firmware can launch the SDK components
directly under its service manager, as described in [Product controls](product-controls.md).
