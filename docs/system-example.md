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

## Five products, one controller

Use `--products 5` to run five server-capable amplifiers plus ordinary TCP
equipment. Their example panel includes a control and readback for every product.

```sh
python3 try-system.py --products 5 --arrangement embedded --out build/five-products
```

Only amplifier 1 runs OpenAVC. Its own hardware appears as **Product hardware**;
the other four amplifiers are remote equipment. Their installed servers remain
stopped. Each product page shows its saved settings and current availability.
The installer does not create a project or log into four more Programmers.

For a separate controller, stop that example and run:

```sh
python3 try-system.py --products 5 --arrangement external --out build/five-external
```

All five amplifiers now have their servers stopped. One ordinary OpenAVC server
controls all five through their equipment interfaces and also controls the TCP
device. Normal Discovery finds five SDK products in this arrangement, or four
when amplifier 1 runs the server. The active host's equipment interface is paused;
its Programmer and panel remain available.

## Exercise outages and recovery

Add `--check --recovery` to either arrangement:

```sh
python3 try-system.py --products 5 --arrangement embedded --out build/recovery-embedded --check --recovery
python3 try-system.py --products 5 --arrangement external --out build/recovery-external --check --recovery
```

The check stops amplifier 2, waits for OpenAVC to report it offline, and verifies
that an attempted command is refused. It operates every other amplifier and the
ordinary TCP device while that peer is down. It restarts the peer, waits for the
normal reconnect, checks that old and refused commands were not replayed, and
sends a new command. The peer's installed server stays stopped.

It then stops the selected OpenAVC server. Every product's hardware remains
operating and accepts a front-panel-style change; no peer starts a server.
After the selected server restarts, its administrator account and project still
work, feedback includes the changes made while it was stopped, and new commands
reach every product once. On the product-hosted example, the stop/start uses the
product management API. On the separate controller, it stops/starts that container.

`checks.json` records the image identity, core revision, completed checks,
offline reason, observed reconnect time and independent example-hardware counters.
`run.json` records the containers, addresses and topology; `discovery.json` records
the ordinary discovery result. These files remain with the private example data
after cleanup. A reconnect time from this Docker exercise is an observation on
that host, not a firmware timing guarantee.

This exercise covers peer and controller stop/restart. It does not simulate a
power cut during a physical operation, controller replacement, a network partition,
firmware update or automatic takeover. Use [product testing](product-testing.md)
for selected checks against your own adapter.

Use `--port 19080` if the default localhost ports 18080 through 18084 are in use.
The five-product example uses 18080 through 18090 instead. Each product uses two
ports and the separate controller uses the final port.
Use `--docker-context <name>` to select a Docker engine without changing your
active context. The Docker engine must run on this computer because the launcher
opens its published localhost ports.

Docker is an evaluation environment here. Firmware can launch the SDK components
directly under its service manager, as described in [Product controls](product-controls.md).
