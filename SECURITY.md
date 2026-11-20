# Security policy

## Report a vulnerability

Do not open a public issue for a security problem. Report it privately on GitHub:
[Report a vulnerability](https://github.com/tariktekin8/vpn-gateway/security/advisories/new).

Please include:

- what an attacker can do, and from where (another website, the local network, a VPN server, another user on the
  computer);
- the steps to reproduce it, with made-up hosts and addresses;
- the version or commit, and Mac or Windows.

Never send real passwords, cookies, tokens, WireGuard keys or an organization's host names.

You get an answer when the report has been read. A confirmed problem is fixed in a new version and listed in
[CHANGELOG.md](CHANGELOG.md).

## Supported versions

Only the newest version gets security fixes.

## Scope

What VPN Gateway protects, and what it cannot protect, is described in
[Security notes](docs/technical.md#security-notes). A report that falls under "What VPN Gateway cannot give you"
there, such as a computer that is already compromised, is a known limit and not a vulnerability.
