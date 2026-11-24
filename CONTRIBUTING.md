# Contributing

Thank you for helping with VPN Gateway. Issues and pull requests are welcome.

## Before you open an issue

- Read the [troubleshooting](docs/technical.md#troubleshooting) part of the documentation first.
- Never post real host names, addresses, user names, passwords, cookies, tokens or WireGuard keys. Replace them with
  made-up values, for example `vpn.example.com` and `10.20.0.15`. VPN Gateway's logs hide login cookies, but they
  still show hosts, addresses and your user name.
- A security problem goes to [SECURITY.md](SECURITY.md), not to a public issue.

## Pull requests

1. Keep a change small and about one thing.
2. If you changed `web/src/app.ts`, rebuild the page and commit `web/app.js` too:

   ```sh
   npm install
   npm run check
   npm run build
   ```

3. If you changed a Dockerfile or a script under `docker/`, build the images and connect a VPN once.
   CI runs `npm run check`, checks that `web/app.js` matches a fresh build, Python syntax and `shellcheck`.
4. Update `docs/technical.md` when the behaviour changes, and add a line to `CHANGELOG.md` under **Unreleased**.
5. Before you commit, check the diff for real hosts, addresses, names and secrets.

## A new VPN client or login method

Follow [docs/adding-a-vpn-client.md](docs/adding-a-vpn-client.md). Every box in its security checklist must be true
before the change is merged.

## Code of conduct

Everyone who takes part follows the [code of conduct](CODE_OF_CONDUCT.md).
