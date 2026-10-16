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

To try optional cloud pairing, add `--cloud`. Only the selected controller gets
outbound network access; the other products remain on the isolated network.
Nothing is paired automatically. See [OpenAVC Cloud](cloud.md) for the normal
account, pairing and product-permission workflow.

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
On failure, `checks.json` preserves the original error and attempts to record each
device's connection status and offline reason. Product process logs are under
each saved `*-data/openavc/logs/` directory. Hosted checks include those logs in
the downloadable artifact.
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

The checker then gives amplifier 1 a delayed operation. After its hardware
callback accepts the command and OpenAVC reports that completion is uncertain,
it forcibly terminates only the selected OpenAVC server. The hardware process
must complete the operation once. A product-hosted server restarts through the
SDK supervisor and keeps its hardware reserved; the standalone example container
is restarted explicitly. After recovery, account access, product identity and
panel bindings must remain intact, and a fresh command must execute once without
replaying the earlier operation.

`checks.json` records the image identity, core revision, completed checks,
offline reason, observed reconnect time and independent example-hardware counters.
`run.json` records the containers, addresses and topology; `discovery.json` records
the ordinary discovery result. These files remain with the private example data
after cleanup. A reconnect time from this Docker exercise is an observation on
that host, not a firmware timing guarantee.

This exercise covers peer/controller stop/restart and controller process loss
after a delayed hardware command was accepted. It does not simulate a power cut
to the hardware, loss before command acknowledgement, controller replacement, a network partition,
firmware update or automatic takeover. Use [product testing](product-testing.md)
for selected checks against your own adapter.

## Exercise access and hardware faults

Use `--faults` with the unchanged invented products:

```sh
python3 try-system.py --products 5 --arrangement embedded --out build/fault-check --check --recovery --faults
```

The additional checks verify these outcomes:

| Injected condition | Expected result |
|---|---|
| Untrusted certificate or incorrect product password | TLS verification or authentication refuses access; hardware counters do not change. |
| Undeclared command parameter or expired session | The equipment endpoint refuses the request before hardware work. |
| Remote access to the product running OpenAVC | The equipment interface refuses access, including through a remembered address. |
| Remote control disabled in product settings | Commands are unavailable. Re-enabling it restores the ordinary OpenAVC connection. |
| Hardware readback unavailable | The endpoint reports unavailable feedback and OpenAVC clears the old values. Current values return when readback recovers. |
| Hardware cannot accept a command | OpenAVC reports a rejected operation without a hardware write. A later new command succeeds after admission is restored. |
| One peer loses its network connection | Its hardware keeps operating locally and the controller operates the remaining equipment. Reconnection returns current feedback without restarting the peer or replaying commands. |
| A peer's IP changes while its configured hostname stays the same | The controller reconnects using the new address, with certificate verification, product identity and panel bindings retained. Refused commands are not replayed. |

`--arrangement external` runs the same applicable checks with a separate server.
The example injects hardware faults through loopback-only test instrumentation;
that instrumentation is not part of the SDK adapter API. These checks do not
send commands to AV equipment or disable certificate verification. Reports remain
in the selected output directory alongside the recovery evidence.
The checker spaces its OpenAVC API requests slightly more than a second apart
to respect the server's normal request limits. It does not retry commands.

The network check disconnects amplifier 2 from the private Docker network, changes
its hardware feedback locally, and reconnects it at the same address. It records
the unchanged container process, offline reason, reconnect time and hardware
counters. It then uses normal connection settings to select the product's stable
hostname, disconnects the peer and reconnects it at a different IP. It verifies
the changed DNS result, unchanged product process/certificate, current feedback,
preserved panel bindings and one new hardware command. The original address and
connection settings are restored afterward. This covers a hostname whose DNS
record follows the product; it does not imply that a saved numeric IP follows a
device automatically. The product certificate must match the configured hostname.

These checks do not simulate packet loss, a blocked discovery protocol or a
split network containing competing controllers. Interrupted
physical operations, mixed protocol versions and controller replacement also
require their own acceptance tests.

## Exercise routing and network faults

Use the separate network check with either arrangement:

```sh
python3 try-system.py --products 5 --arrangement embedded --out build/network-check --check --network-faults
```

This builds an additional container containing Linux network tools. Docker must
allow `NET_ADMIN` in helpers sharing the example containers' network namespaces,
and its Linux kernel must support `netem`. Products retain their normal restricted
capabilities. The check creates a disposable router. Its test routes and firewall
rules are applied only inside disposable container network namespaces.
The launcher removes the example afterward. These tools are not needed on firmware.

The multicast check blocks amplifier 2's mDNS packets while its discovery setting
remains enabled. It temporarily pauses the original
connection and adds a device through the normal project-save API using the
product address, port and credentials, without a discovery result. An ordinary
scan generates incoming traffic; kernel counters must confirm dropped packets.
It then blocks outgoing announcements and requires observed drops there too.
With both rules active, a command must reach the independent hardware counter once.
Turning discovery off and back on must also succeed while multicast is blocked.
Other discovery methods may still identify equipment. The temporary device and
firewall rules are removed, and the original connection resumes.
Another scan must then identify the product through mDNS without a product restart.

The packet-loss check drops every fifth outbound TCP packet from amplifier 2's
equipment port for at least 15 seconds. It changes hardware feedback locally,
records controller connection/readback observations and operates the ordinary
TCP device. After removing the rule, current feedback must return and one new
command must execute once. Kernel packet counters prove the loss was applied;
hardware counters check that prior commands were not replayed. No new hardware
command is sent to the affected peer during this loss check.

The next checks apply delay, packet duplication and reordering to the peer's
network interface. A bounded stream of numbered test datagrams measures what
actually arrives. OpenAVC continues using its normal TLS/TCP connection on that
interface. Commands must execute once, changed hardware feedback must return,
and ordinary TCP equipment must remain controllable. Hardware counts are checked
again after each impairment is removed.

The controller then gets a second network interface. The IPv4 check selects the
original adapter through **Settings > Network > Reach devices through**, reconnects
the peer and verifies its observed source address. Amplifier 2 then moves to a
separate subnet behind the example router. Manual setup uses its hostname,
credentials and verified certificate. Router counters must show the traffic
crossed subnets. A route outage must produce offline status and unavailable
readback; recovery must return current feedback without replaying commands.
The hardware process, certificate and product identity remain unchanged. A saved
numeric address is expected to go offline when the product moves. The example
supplies hostname resolution explicitly; it does not simulate DHCP or campus DNS.

### IPv6 equipment connections

Run the same routed and impaired network checks with IPv6 equipment connections:

```sh
python3 try-system.py --arrangement external --out build/ipv6-external --check --network-faults --equipment-ipv6
python3 try-system.py --arrangement embedded --out build/ipv6-embedded --check --network-faults --equipment-ipv6
```

Docker must support IPv6 internal networks. Equipment listens on IPv6; the
example management pages and ordinary TCP equipment still use IPv4. Equipment
discovery currently uses IPv4, so these checks use manual connections and record
IPv6 discovery as unsupported. Use **Every connected adapter** for IPv6 equipment;
the pinned server's specific-adapter choice resolves an IPv4 source address.
Scoped link-local IPv6 addresses are not covered by this example.
IPv6 packet loss uses a 10% `netem` policy, confirmed by missing test datagrams.
It checks feedback and subsequent control without sending a hardware command
to the affected peer during loss.

`checks.json` retains the counters, datagram observations, routes and independent
hardware results; `run.json` identifies the helper image. On IPv4, combine
`--network-faults` with `--faults --recovery` for the other supplied failure
exercises. `--equipment-ipv6` cannot be combined with the IPv4 `--faults` exercise.
These checks do not establish firmware timing or network-scale guarantees.

## Ports and cleanup

Use `--port 19080` if the default localhost ports 18080 through 18084 are in use.
The five-product example uses 18080 through 18090 instead. Each product uses two
ports and the separate controller uses the final port.
Use `--docker-context <name>` to select a Docker engine without changing your
active context. The Docker engine must run on this computer because the launcher
opens its published localhost ports.

Docker is an evaluation environment here. Firmware can launch the SDK components
directly under its service manager, as described in [Product controls](product-controls.md).
