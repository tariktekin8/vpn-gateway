## What this changes

<!-- One or two sentences. -->

## Checks

- [ ] The diff holds no real host, address, user name, password, cookie, token or key.
- [ ] `web/app.js` is rebuilt if `web/src/app.ts` changed (`npm run check && npm run build`).
- [ ] Images build and a VPN connects if anything under `docker/` changed.
- [ ] `docs/technical.md` describes the new behaviour, and `CHANGELOG.md` has a line under **Unreleased**.
- [ ] A new VPN client or login method keeps every rule in
      [docs/adding-a-vpn-client.md](https://github.com/tariktekin8/vpn-gateway/blob/main/docs/adding-a-vpn-client.md).
