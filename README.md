# VPN Gateway

**All your organization VPNs, open at the same time, on your own computer.**

Consultants, freelancers and support teams often need the internal networks of several organizations in one day.
Each one hands out its own VPN, and you can be connected to only one at a time. VPN Gateway ends the switching: it
keeps every VPN connection open side by side, and one page in your browser shows them all.

![VPN Gateway web page: gateway details on top, the VPN list on the left, a connected VPN and its addresses on the right](docs/images/web.png)

*The names, hosts and addresses in this picture are made up.*

## What it does for you

- **Several VPNs at once.** Palo Alto GlobalProtect and Fortinet FortiGate, with a password or a single sign-on
  page (Microsoft, Okta, …). Connect them all and reach every network at the same time.
- **One page for everything.** Connect, sign in, add a VPN, change its addresses: no config files, no terminal
  after the start.
- **Only what you choose goes through a VPN.** You list the addresses for each VPN; everything else, including your
  normal internet, stays as it is.
- **See at a glance what works.** Each address shows three green stops, from your computer through the gateway into
  the VPN. A stop that turns amber tells you what is wrong.
- **Changes apply at once.** Add or remove an address while a VPN is connected; the session stays up.
- **Two VPNs with the same addresses.** Map one of them to another range and reach both.
- **Stays on your computer.** Settings stay in one folder on your computer. Passwords are asked at every connect
  and never stored. Nothing goes to a cloud service.
- **Mac and Windows.** Runs on Docker Desktop and WireGuard, both free.

## Before you start

> [!IMPORTANT]
> Use VPN Gateway only for networks you are allowed to use. Check each organization's rules first:
>
> - Some organizations allow no VPN app other than their own.
> - Some require a managed device or a device health check (GlobalProtect HIP); VPN Gateway does not send one.
> - Some contracts forbid being connected to other networks at the same time.
>
> If you are not sure, ask their IT team.

## Get started

1. Install **Docker Desktop**, **WireGuard**, **Git** and **Python**: [What you need](docs/technical.md#what-you-need).
2. Download VPN Gateway and start the page:

   ```sh
   git clone https://github.com/tariktekin8/vpn-gateway.git
   cd vpn-gateway
   ./vpn web
   ```

   On Windows, run `.\vpn.cmd web` instead: [Install (Windows)](docs/technical.md#install-windows).
3. The page opens in your browser. If it does not, open the link that `./vpn web` printed; it holds the page's
   key. Press **Start the gateway**, import the WireGuard profile and add your VPNs:
   [First setup on the page](docs/technical.md#first-setup-on-the-page).

## Documentation

Everything is in the [documentation](docs/technical.md):

- [Install on a Mac](docs/technical.md#install-mac) or [on Windows](docs/technical.md#install-windows): step by
  step, for people who have never used a terminal.
- [First setup](docs/technical.md#first-setup-on-the-page) and [what to fill in](docs/technical.md#what-to-fill-in)
  for each VPN.
- [Every day](docs/technical.md#every-day): connect, sign in, check and change addresses.
- [Reaching servers by name](docs/technical.md#reaching-servers-by-name): DNS for the VPN networks.
- [Things to keep in mind](docs/technical.md#things-to-keep-in-mind) while you use it.
- [System files VPN Gateway changes](docs/technical.md#system-files-vpn-gateway-changes) on Mac and Windows, and
  how to remove them.
- [Stop or remove](docs/technical.md#stop-or-remove) VPN Gateway.
- [Command line](docs/technical.md#everyday-commands), [configuration file](docs/technical.md#configure-your-vpns-systemsjson),
  [how it works](docs/technical.md#how-it-works) and [security notes](docs/technical.md#security-notes).

## License

[MIT](LICENSE). VPN Gateway is not affiliated with Palo Alto Networks, Fortinet or WireGuard. It uses
[openconnect](https://www.infradead.org/openconnect/), [openfortivpn](https://github.com/adrienverge/openfortivpn)
and [WireGuard](https://www.wireguard.com/), each under its own license.
