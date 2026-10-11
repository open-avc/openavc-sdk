# OpenAVC Cloud on your product

The optional full server uses OpenAVC's normal cloud account and pairing flow.
Local hardware control, projects, panels and automation work without a cloud
account or internet connection. An equipment-only product does not need its own
cloud pairing: the selected OpenAVC server reports the equipment it controls.

Your hardware adapter needs no separate cloud implementation. Its declared
commands and published feedback are available through the server's usual cloud
integration. Your firmware still owns credentials, writable storage, certificate
trust and updates as described in [Linux firmware](firmware.md).

## Try it with the complete example

Start a new example with outbound access for its selected controller:

```sh
python3 try-system.py --arrangement embedded --cloud --out build/cloud-example
```

Use `--arrangement external` to put the server in a separate container instead.
In either arrangement, the other equipment stays on the isolated example
network. The launcher retains normal public certificate trust alongside the
invented products' certificates. It does not disable TLS verification.

1. Open the printed Programmer link and create its administrator login.
2. Use your OpenAVC Cloud account to create a system and obtain its pairing token.
3. In the Programmer's **Cloud** page, follow normal pairing using that token.
4. In the cloud portal, open the paired system. Check its devices and current
   feedback, then try **Remote Programming** or **Remote Panel**.
5. Unpair the example when finished. Press Ctrl+C in the launcher terminal to
   stop it and retain its private diagnostic files.

The product-settings login, Programmer login and cloud account serve different
purposes. The example does not share their sessions. Remote Programming uses the
normal Programmer authentication as well as cloud access permissions. See
[Programmer login](product-controls.md#programmer-login) if your software supplies
the initial local account setup.

The automatic `--check` exercise does not create a cloud account or pair a system.
With `--cloud`, it checks the example's network arrangement and retained TLS roots
in addition to ordinary equipment control. It does not test a cloud service.

## Product controls and remote access

| Product setting | Effect on the running server |
|---|---|
| Programmer on | Normal programming access is available, subject to OpenAVC and cloud permissions. |
| Programmer off | Existing programming connections disconnect; new programming requests and cloud configuration restores are refused. The project keeps running. |
| Panel on | Panel access is available independently of Programmer access, subject to normal access permissions. |
| Panel off | Existing panel connections disconnect and new panel requests are refused. The project keeps running. |
| Server off | The local OpenAVC server stops, including its cloud connection. The manufacturer's hardware process keeps running. |

Disabling Programmer or Panel does not disable the cloud account or all runtime
control. Authorized cloud device commands and monitoring remain available.
Unpair the server to remove its cloud connection. A portal tunnel can still be
listed as ready while the product refuses its interface; opening it displays the
product-management explanation.

Cloud project backups hold project programming. Do not treat them as a full
firmware image, account backup or copy of every file on the product. For saved
variable state, use the normal OpenAVC Backup Restore workflow and verify the
complete recovery procedure on your firmware. Retain the product's hardware
integration and its identity separately from an imported project.

The SDK-managed server receives OpenAVC updates through your firmware process.
Generic server-update requests are refused. A cloud deployment label describes
how the process is hosted; it does not authorize replacing the firmware's
OpenAVC installation separately.

## Verify your firmware

Exercise pairing and unpairing, both remote interfaces, ordinary commands and
feedback, project backup/download/restore, each intended account role, alerts,
reporting and the cloud features you intend to offer. Test AI through the actual
service if you offer it. Check the product switches while sessions are already
open and requests are waiting. Verify refused writes leave programming intact.

Disconnect the cloud connection and confirm local panels, automation and hardware
still operate. After reconnect, verify current feedback and confirm that uncertain
commands have not been repeated. Test restarts and your firmware's update policy;
qualify account and cloud reassociation when replacing a controller.

Keep exact SDK/server/firmware revisions, test actions and independent hardware
observations with the results. A local example cannot establish your product's
network, resource, hardware or complete recovery behavior.
