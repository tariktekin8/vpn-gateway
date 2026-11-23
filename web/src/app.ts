// VPN Gateway web UI. Source of web/app.js: edit this file, then run `npm run build`.
// No dependencies. Talks only to the same-origin /api/* endpoints.
// Every server-provided string goes into the page with textContent, never innerHTML.
// Layout: a list of VPNs on the left, the selected VPN (or the gateway, or a new VPN form) on the right,
// and an attention strip on top for everything that needs the user.

// ---------- API types ----------

type Tone = "ok" | "warn" | "bad" | "busy" | "off";

interface Method {
  required: string[];
  asks_password: boolean;
  iface: string;
}

interface System {
  name: string;
  method: string | null;
  host: string | null;
  user: string | null;
  subnets: string[];
  idle_minutes: number | null;
  domains: string[];
}

interface Login {
  kind: "gp-saml" | "forti-saml" | "gp-password";
  awaiting_callback?: boolean;
  awaiting_input?: boolean;
  auth_url?: string;
  port_used_by?: string;
}

interface SystemStatus {
  state: string;
  iface: string;
  tunnel_up: boolean;
  routes: number;
  busy: string | null;
  error: string | null;
  login: Login | null;
  dns: { servers: string[]; domains: string[] } | null;
}

interface GatewayStatus {
  state: string;
  running: boolean;
  mac_connected: boolean;
  busy: string | null;
  error: string | null;
  profile?: { ready: boolean; version: string | null; auto?: boolean };
}

interface Status {
  docker: boolean;
  gateway: GatewayStatus;
  systems: Record<string, SystemStatus>;
  update: { installed: string; latest: string; url: string } | null;
}

interface RouteEntry {
  entry: string;
  real: string;
  shown: string;
  mac: "tunnel" | "other" | null;
  mac_interface: string | null;
  gateway: "this" | "blocked" | "missing" | "other";
  vpn: "yes" | "no" | "off";
}

interface RouteSystem {
  name: string;
  running: boolean;
  entries: RouteEntry[];
}

interface ErrorBody {
  error?: string;
  errors?: unknown;
}

interface ApiResult<T> {
  ok: boolean;
  status: number;
  data: Partial<T> & ErrorBody;
}

// ---------- settings ----------

const STATUS_EVERY_MS = 3000;
const SYSTEMS_EVERY_MS = 15000;
const ROUTES_EVERY_MS = 5000; // each load asks the Mac about every address, so not on every status poll
const LOG_TAIL = 150;
const PROFILE_KEY = "gateway.profileVersion";
const SELECTED_KEY = "gateway.selected";
const TAB_KEY = "gateway.tab";
const THEME_KEY = "gateway.theme";
const GATEWAY_OPEN_KEY = "gateway.detailsOpen";
const NEW_VIEW = "@new";

// What each login method is called in the page.
const METHOD_LABEL: Record<string, string> = {
  "gp-password": "GlobalProtect · password",
  "gp-saml": "GlobalProtect · single sign-on",
  "forti-saml": "FortiGate · single sign-on",
};

const TOKEN_KEY = "vpn-gateway-token";
const TOKEN = readToken();

// `./vpn web` prints http://127.0.0.1:<port>/#token=<key>. The fragment never reaches the server; keep the key
// for this tab only (a reload keeps working) and take it out of the address bar and the history.
function readToken(): string {
  const match = /^#token=([A-Za-z0-9_-]+)$/.exec(window.location.hash);
  let token = match ? match[1] : "";
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else token = sessionStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    // storage blocked: the key lives only until this page reloads
  }
  if (window.location.hash) history.replaceState(null, "", window.location.pathname + window.location.search);
  return token;
}
// A link pasted into an open tab changes only the fragment: load the page again to take the new key.
window.addEventListener("hashchange", () => {
  if (/^#token=/.test(window.location.hash)) window.location.reload();
});

// ---------- state ----------

let stopped = false; // the token is no longer valid (server restarted)
let methods: Record<string, Method> = {};
let systems: System[] = [];
let systemsLoaded = false;
let lastStatus: Status | null = null;
let routes: Record<string, RouteSystem> = {};
let macChecked = false;
let routesBusy = false;
let routesLoadedAt = 0;
let statusReqSeq = 0; // number of the last status request sent
let lastStatusSeq = 0; // number of the request that produced lastStatus
let selected: string | null = stored(SELECTED_KEY);

// ---------- DOM helpers ----------

function $<T extends HTMLElement = HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error("missing #" + id);
  return node as T;
}

function h<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(label: string, cls = ""): HTMLButtonElement {
  const b = h("button", "btn" + (cls ? " " + cls : ""), label);
  b.type = "button";
  return b;
}

function setText(node: HTMLElement, text: string): void {
  if (node.textContent !== text) node.textContent = text;
}

function show(node: HTMLElement, visible: boolean): void {
  if (node.hidden === visible) node.hidden = !visible;
}

function setTone(node: HTMLElement, base: string, tone: Tone): void {
  const cls = base + " " + tone;
  if (node.className !== cls) node.className = cls;
}

function showError(box: HTMLElement, r: { status: number; data: ErrorBody }, prefix?: string): void {
  box.replaceChildren();
  if (prefix) box.append(h("p", undefined, prefix));
  const errors = Array.isArray(r.data.errors) ? r.data.errors : [];
  if (errors.length) {
    const ul = h("ul");
    for (const e of errors) ul.append(h("li", undefined, String(e)));
    box.append(ul);
  } else {
    box.append(h("p", undefined, r.data.error || "Request failed (HTTP " + r.status + ")."));
  }
  box.className = "msg bad";
  box.hidden = false;
}

function showInfo(box: HTMLElement, text: string): void {
  box.replaceChildren(h("p", undefined, text));
  box.className = "msg ok";
  box.hidden = false;
}

function clearMsg(box: HTMLElement): void {
  box.replaceChildren();
  box.hidden = true;
}

function msgBox(): HTMLDivElement {
  const box = h("div", "msg");
  box.setAttribute("aria-live", "polite");
  box.hidden = true;
  return box;
}

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // private mode: nothing is remembered
  }
}

function field(label: string, control: HTMLElement, hint?: string): HTMLLabelElement {
  const lab = h("label", "field");
  lab.append(h("span", "field-label", label), control);
  if (hint) lab.append(h("small", undefined, hint));
  return lab;
}

function input(type = "text", placeholder = ""): HTMLInputElement {
  const el = h("input");
  el.type = type;
  el.placeholder = placeholder;
  el.autocomplete = type === "password" ? "new-password" : "off";
  el.spellcheck = false;
  return el;
}

// ---------- API ----------

async function api<T>(method: string, path: string, body?: object): Promise<ApiResult<T>> {
  if (stopped) return { ok: false, status: 403, data: { error: "missing or wrong token" } as Partial<T> & ErrorBody };
  const headers: Record<string, string> = { "X-Gateway-Token": TOKEN };
  const init: RequestInit = { method, headers, cache: "no-store", credentials: "same-origin" };
  if (method === "POST" || method === "PUT") {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body ?? {});
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    show($("banner-net"), true);
    return { ok: false, status: 0, data: { error: "Cannot reach the VPN Gateway server." } as Partial<T> & ErrorBody };
  } finally {
    init.body = undefined; // the body may hold a password: drop it at once
  }
  show($("banner-net"), false);
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  const obj = (data && typeof data === "object" ? data : {}) as Partial<T> & ErrorBody;
  if (res.status === 403 && obj.error === "missing or wrong token") {
    stopped = true;
    try {
      sessionStorage.removeItem(TOKEN_KEY); // stale key from an earlier start
    } catch {
      // nothing stored
    }
    show($("banner-token"), true);
  }
  return { ok: res.ok, status: res.status, data: obj };
}

function sysPath(name: string, action?: string): string {
  return "/api/systems/" + encodeURIComponent(name) + (action ? "/" + action : "");
}

// ---------- logs ----------

interface Logs {
  open: boolean;
  busy: boolean;
  box: HTMLDivElement;
  note: HTMLParagraphElement;
  pre: HTMLPreElement;
  path: string;
}

function makeLogs(path: string, label: string): Logs {
  const box = h("div", "logs");
  const note = h("p", "muted", "Loading logs…");
  const pre = h("pre");
  pre.tabIndex = 0;
  pre.setAttribute("aria-label", label);
  pre.hidden = true;
  box.append(note, pre);
  return { open: false, busy: false, box, note, pre, path };
}

async function loadLogs(logs: Logs): Promise<void> {
  if (!logs.open || logs.busy || stopped) return;
  logs.busy = true;
  try {
    const r = await api<{ lines: string[] }>("GET", logs.path + "?tail=" + LOG_TAIL);
    if (!logs.open) return;
    if (r.ok) {
      const lines = Array.isArray(r.data.lines) ? r.data.lines.map(String) : [];
      const pre = logs.pre;
      const atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24;
      const text = lines.join("\n");
      if (pre.textContent !== text) {
        pre.textContent = text;
        if (atBottom || pre.hidden) pre.scrollTop = pre.scrollHeight;
      }
      show(pre, lines.length > 0);
      setText(logs.note, lines.length ? "" : "No log lines yet.");
    } else {
      logs.pre.textContent = "";
      show(logs.pre, false);
      setText(logs.note, r.status === 502 ? "No logs yet: this VPN has not been started."
        : "Could not load logs: " + (r.data.error || "HTTP " + r.status));
    }
    show(logs.note, Boolean(logs.note.textContent));
  } finally {
    logs.busy = false;
  }
}

// ---------- tabs ----------

interface Tabs {
  bar: HTMLDivElement;
  panels: Record<string, HTMLDivElement>;
  buttons: Record<string, HTMLButtonElement>;
  current: string;
  onChange: (tab: string) => void;
}

function makeTabs(id: string, names: string[], start: string): Tabs {
  const bar = h("div", "tabs");
  bar.setAttribute("role", "tablist");
  const tabs: Tabs = { bar, panels: {}, buttons: {}, current: names.includes(start) ? start : names[0],
    onChange: () => undefined };
  for (const name of names) {
    const b = h("button", "tab", name);
    b.type = "button";
    b.id = id + "-tab-" + name;
    b.setAttribute("role", "tab");
    const panel = h("div", "tab-panel");
    panel.id = id + "-panel-" + name;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", b.id);
    b.setAttribute("aria-controls", panel.id);
    b.addEventListener("click", () => selectTab(tabs, name));
    b.addEventListener("keydown", (ev) => {
      const i = names.indexOf(name);
      const next = ev.key === "ArrowRight" ? names[(i + 1) % names.length]
        : ev.key === "ArrowLeft" ? names[(i + names.length - 1) % names.length] : null;
      if (next) {
        ev.preventDefault();
        selectTab(tabs, next);
        tabs.buttons[next].focus();
      }
    });
    tabs.buttons[name] = b;
    tabs.panels[name] = panel;
    bar.append(b);
  }
  selectTab(tabs, tabs.current, true);
  return tabs;
}

function selectTab(tabs: Tabs, name: string, quiet = false): void {
  tabs.current = name;
  for (const [n, b] of Object.entries(tabs.buttons)) {
    const on = n === name;
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
    tabs.panels[n].hidden = !on;
  }
  if (!quiet) {
    store(TAB_KEY, name);
    tabs.onChange(name);
  }
}

// ---------- the path of one address (local → gateway → VPN) ----------
// Three stops on one line. A stop that does not use the address turns amber and the line breaks there.

function pathStops(e: RouteEntry, sys: RouteSystem): { label: string; tone: Tone; text: string }[] {
  const off = !sys.running;
  const mac: Tone = e.mac === null ? "off" : e.mac === "tunnel" ? "ok" : "warn";
  const gw: Tone = e.gateway === "this" ? "ok" : e.gateway === "blocked" || off ? "off" : "warn";
  const vpn: Tone = e.vpn === "yes" ? "ok" : e.vpn === "off" ? "off" : "warn";
  return [
    { label: "Local", tone: mac, text: e.mac === null ? "not checked" : e.mac === "tunnel" ? "into the tunnel"
      : "outside the tunnel" + (e.mac_interface ? " (" + e.mac_interface + ")" : "") },
    { label: "Gateway", tone: gw, text: e.gateway === "this" ? "to this VPN" : e.gateway === "blocked"
      ? "held: VPN off" : e.gateway === "other" ? "to another VPN" : "no route yet" },
    { label: "VPN", tone: vpn, text: e.vpn === "yes" ? "passes it on" : e.vpn === "off" ? "off" : "not yet" },
  ];
}

function pathLine(e: RouteEntry, sys: RouteSystem): HTMLSpanElement {
  const line = h("span", "path");
  const stops = pathStops(e, sys);
  line.setAttribute("role", "img");
  line.setAttribute("aria-label", stops.map((s) => s.label + ": " + s.text).join(", "));
  line.title = stops.map((s) => s.label + ": " + s.text).join("\n");
  stops.forEach((s, i) => {
    if (i > 0) {
      // A segment is only as good as its worse end: the line breaks wherever a stop fails.
      const ends = [stops[i - 1].tone, s.tone];
      line.append(h("span", "seg " + (ends.includes("warn") ? "warn" : ends.includes("off") ? "off" : "ok")));
    }
    line.append(h("span", "stop " + s.tone));
  });
  return line;
}

// The column head: the three stop names, spaced like the line below them.
function pathLegend(): HTMLSpanElement {
  const legend = h("span", "path-legend");
  for (const label of ["Local", "Gateway", "VPN"]) legend.append(h("span", undefined, label));
  return legend;
}

function routeHint(e: RouteEntry, sys: RouteSystem): string {
  if (!sys.running) return "";
  if (e.vpn === "no") return "The VPN does not use this address yet. It should within a few seconds.";
  if (e.gateway === "missing" || e.gateway === "other") return "The gateway has no route to this VPN yet.";
  if (e.mac === "other") return "Local sends this address outside the tunnel. Download the profile again, " +
    "or install the automatic tunnel.";
  return "";
}

function problems(name: string): number {
  const rs = routes[name];
  return rs ? rs.entries.filter((e) => routeHint(e, rs)).length : 0;
}

// ---------- per-VPN state ----------

interface LoginBlock {
  form: HTMLFormElement;
  intro: HTMLDivElement;
  input: HTMLInputElement;
  submit: HTMLButtonElement;
  msg: HTMLDivElement;
}

interface Card {
  name: string;
  pending: string | null;
  item: HTMLLIElement;          // in the list
  itemButton: HTMLButtonElement;
  itemDot: HTMLSpanElement;
  itemState: HTMLSpanElement;
  itemBadge: HTMLSpanElement;
  root: HTMLElement;            // in the detail pane
  dot: HTMLSpanElement;
  state: HTMLSpanElement;
  kind: HTMLParagraphElement;
  power: HTMLButtonElement;
  msg: HTMLDivElement;
  error: HTMLParagraphElement;
  saml: LoginBlock;
  samlFallback: HTMLDivElement;
  samlFallbackText: HTMLParagraphElement;
  samlFallbackLink: HTMLAnchorElement;
  typed: LoginBlock;
  pass: LoginBlock;
  passOpen: boolean;
  forti: HTMLDivElement;
  fortiLink: HTMLAnchorElement;
  fortiNotice: HTMLParagraphElement;
  tabs: Tabs;
  addrBody: HTMLDivElement;
  addrMsg: HTMLDivElement;
  addrInput: HTMLInputElement;
  namesBody: HTMLDivElement;
  logs: Logs;
  settings: SettingsForm;
  needsAction: boolean;
  // SAML login pop-up. Only a status requested after popupAfter may act on it.
  popup: Window | null;
  popupUrl: string | null;
  popupBlocked: boolean;
  popupClosed: boolean;
  popupAfter: number;
  gpAwaitSeen: boolean;
}

const cards = new Map<string, Card>();

function makeLoginBlock(id: string, label: string, submitText: string): LoginBlock {
  const form = h("form", "login");
  form.hidden = true;
  form.noValidate = true;
  const intro = h("div");
  const el = input("password");
  el.id = id;
  el.autocomplete = "off";
  el.maxLength = 1024;
  const submit = h("button", "btn btn-primary", submitText);
  submit.type = "submit";
  const msg = msgBox();
  const row = h("div", "inline");
  row.append(field(label, el), submit);
  form.append(intro, row, msg);
  return { form, intro, input: el, submit, msg };
}

function makeListItem(card: Card): void {
  const li = h("li");
  const b = h("button", "vpn-item");
  b.type = "button";
  const dot = h("span", "dot off");
  dot.setAttribute("aria-hidden", "true");
  const text = h("span", "vpn-item-text");
  const name = h("span", "vpn-item-name", card.name);
  const state = h("span", "vpn-item-state", "Loading…");
  text.append(name, state);
  const badge = h("span", "badge");
  badge.hidden = true;
  b.append(dot, text, badge);
  b.addEventListener("click", () => select(card.name));
  li.append(b);
  card.item = li;
  card.itemButton = b;
  card.itemDot = dot;
  card.itemState = state;
  card.itemBadge = badge;
}

function makeCard(name: string): Card {
  const root = h("section", "view");
  root.setAttribute("aria-label", name);

  const head = h("div", "view-head");
  const titleWrap = h("div", "view-title");
  const title = h("h2", undefined, name);
  const kind = h("p", "view-kind");
  titleWrap.append(title, kind);
  const statusWrap = h("div", "view-status");
  const dot = h("span", "dot off");
  dot.setAttribute("aria-hidden", "true");
  const state = h("span", "view-state", "Loading…");
  statusWrap.append(dot, state);
  const power = button("Connect", "btn-primary");
  head.append(titleWrap, statusWrap, power);

  const msg = msgBox();
  const error = h("p", "error");
  error.hidden = true;

  const saml = makeLoginBlock("login-saml-" + name, "Callback link", "Connect");
  const samlFallback = h("div", "msg");
  samlFallback.hidden = true;
  const samlFallbackText = h("p");
  const samlFallbackLink = h("a", undefined, "Open the sign-in page");
  samlFallbackLink.target = "_blank";
  samlFallbackLink.rel = "noopener noreferrer";
  const linkP = h("p");
  linkP.append(samlFallbackLink);
  samlFallback.append(samlFallbackText, linkP);
  const pasteHelp = h("p", "muted");
  pasteHelp.append("Sign in in the pop-up window. With the callback helper installed (see README), allow the " +
    "browser to open it at the end and the tunnel starts by itself. Without it, open the pop-up's developer " +
    "tools before you sign in, copy the ", h("code", undefined, "globalprotectcallback:"),
    " link from the Console at the end, and paste it here.");
  saml.intro.append(h("p", "login-title", "Waiting for your sign-in"), samlFallback, pasteHelp);

  const pass = makeLoginBlock("login-pass-" + name, "Password", "Connect");
  pass.intro.append(h("p", "login-title", "Enter your password"),
    h("p", "muted", "Asked at every connect. It goes to the VPN and is never stored on this computer."));

  const typed = makeLoginBlock("login-input-" + name, "Code", "Send");
  const typedHelp = h("p", "muted");
  typedHelp.append("If the VPN asks for a code (MFA), type it here. If it keeps waiting, press Disconnect and " +
    "Connect again, or run ", h("code", undefined, "./vpn login " + name), " in a terminal.");
  typed.intro.append(h("p", "login-title", "Signing in"), typedHelp);

  const forti = h("div", "login");
  forti.hidden = true;
  const fortiP = h("p", undefined, "Sign in at ");
  const fortiLink = h("a");
  fortiLink.target = "_blank";
  fortiLink.rel = "noopener noreferrer";
  fortiP.append(fortiLink);
  const fortiNotice = h("p", "muted");
  fortiNotice.hidden = true;
  forti.append(h("p", "login-title", "Waiting for your sign-in"), fortiP, fortiNotice);

  const tabs = makeTabs("vpn-" + name, ["Addresses", "Names", "Logs", "Settings"], stored(TAB_KEY) ?? "Addresses");

  // Addresses
  const addrBody = h("div");
  const addrMsg = msgBox();
  const addForm = h("form", "add-address");
  const addrInput = input("text", "10.20.0.15 or 10.20.0.0/24");
  addrInput.setAttribute("aria-label", "Address to add");
  const addBtn = h("button", "btn", "Add address");
  addBtn.type = "submit";
  addForm.append(addrInput, addBtn);
  const addrNote = h("p", "muted small", "Only these addresses go through this VPN. Only some ports: write " +
    "10.20.0.15 ports 443,3389. Two VPNs with the same range: write 10.10.0.0/16 as 100.64.0.0/16 and use the " +
    "second range for this one.");
  tabs.panels.Addresses.append(addrMsg, addrBody, addForm, addrNote);

  // Names
  const namesBody = h("div", "stack");
  tabs.panels.Names.append(namesBody);

  // Logs
  const logs = makeLogs(sysPath(name, "logs"), "Logs of " + name);
  tabs.panels.Logs.append(logs.box);

  // Settings
  const settings = makeSettings(name);
  tabs.panels.Settings.append(settings.root);

  root.append(head, msg, error, pass.form, saml.form, forti, typed.form, tabs.bar, ...Object.values(tabs.panels));

  const card = {
    name, pending: null, root, dot, state, kind, power, msg, error,
    saml, samlFallback, samlFallbackText, samlFallbackLink, typed, pass, passOpen: false, forti, fortiLink,
    fortiNotice,
    tabs, addrBody, addrMsg, addrInput, namesBody, logs, settings, needsAction: false,
    popup: null, popupUrl: null, popupBlocked: false, popupClosed: false, popupAfter: Infinity, gpAwaitSeen: false,
  } as unknown as Card;
  makeListItem(card);

  tabs.onChange = (tab) => {
    logs.open = tab === "Logs";
    if (logs.open) void loadLogs(logs);
    if (tab === "Addresses") void loadRoutes(true);
    if (tab === "Settings") fillSettings(card.settings, systems.find((s) => s.name === name) ?? null);
  };
  logs.open = tabs.current === "Logs";
  power.addEventListener("click", () => {
    if (power.dataset.action === "stop") void stopSystem(card);
    else if (systems.find((s) => s.name === name)?.method === "gp-password") {
      card.passOpen = !card.passOpen; // the password is asked here, at every connect
      clearMsg(pass.msg);
      renderCard(card);
      if (card.passOpen) pass.input.focus();
    } else void startSystem(card);
  });
  addForm.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const value = addrInput.value.trim().replace(/\s+/g, " ");
    if (!value) {
      addrInput.focus();
      return;
    }
    void changeSubnets(card, (n) => (n.includes(value) ? n : [...n, value])).then((ok) => {
      if (ok) addrInput.value = "";
    });
  });
  saml.form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    void sendLogin(card, saml, "callback", "url", "Paste the callback link first.");
  });
  pass.form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    clearMsg(pass.msg);
    const password = pass.input.value;
    if (!password) {
      showError(pass.msg, { status: 400, data: { error: "Type the password first." } });
      pass.input.focus();
      return;
    }
    pass.input.value = "";
    card.passOpen = false;
    void startSystem(card, password);
  });
  typed.form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    void sendLogin(card, typed, "input", "text", "Type the password or answer first.");
  });
  return card;
}

// ---------- settings form (also used to add a VPN) ----------

interface SettingsForm {
  root: HTMLFormElement;
  name: HTMLInputElement;
  nameRow: HTMLLabelElement;
  method: HTMLSelectElement;
  host: HTMLInputElement;
  user: HTMLInputElement;
  userRow: HTMLLabelElement;
  subnets: HTMLTextAreaElement;
  subnetsRow: HTMLLabelElement;
  idle: HTMLInputElement;
  save: HTMLButtonElement;
  msg: HTMLDivElement;
  remove: HTMLButtonElement | null;
  editing: System | null;
  dirty: boolean;
}

function makeSettings(name: string | null): SettingsForm {
  const root = h("form", "settings");
  root.noValidate = true;
  const nameEl = input("text", "acme");
  nameEl.maxLength = 31;
  const methodEl = h("select");
  const hostEl = input("text", "vpn.example.com");
  const userEl = input("text");
  const subnetsEl = h("textarea");
  subnetsEl.rows = 3;
  subnetsEl.spellcheck = false;
  subnetsEl.placeholder = "10.20.0.15 ports 443\n10.20.0.0/24";
  const idleEl = input("number", "Off");
  idleEl.min = "1";
  idleEl.max = "1440";
  idleEl.step = "1";
  idleEl.inputMode = "numeric";

  const nameRow = field("Name", nameEl, "Lowercase letters, digits and \"-\", starting with a letter. It cannot be " +
    "changed later.");
  const methodRow = field("Type", methodEl);
  const hostRow = field("VPN address", hostEl, "As in the official app. Add :port when it is not 443.");
  const userRow = field("User name", userEl);
  const subnetsRow = field("Addresses", subnetsEl, "One per line. Only these go through the VPN. Add " +
    "\"ports 443,3389\" to allow only those ports.");
  const idleRow = field("Disconnect when idle (minutes)", idleEl, "Disconnects after this many minutes without " +
    "traffic from this computer. Leave empty to stay connected.");
  const save = h("button", "btn btn-primary", name ? "Save changes" : "Add VPN");
  save.type = "submit";
  const msg = msgBox();
  const actions = h("div", "inline end");
  actions.append(save);
  root.append(nameRow, methodRow, hostRow, userRow, subnetsRow, idleRow, msg, actions);

  let remove: HTMLButtonElement | null = null;
  if (name) {
    nameRow.hidden = true;
    subnetsRow.hidden = true; // addresses have their own tab
    const danger = h("div", "danger-zone");
    remove = button("Delete " + name, "btn-danger");
    danger.append(h("p", "muted small", "Deleting disconnects this VPN and removes its settings from this " +
      "computer."), remove);
    root.append(danger);
  }
  const form: SettingsForm = {
    root, name: nameEl, nameRow, method: methodEl, host: hostEl, user: userEl, userRow, subnets: subnetsEl,
    subnetsRow, idle: idleEl, save, msg, remove, editing: null, dirty: false,
  };
  root.addEventListener("input", () => { form.dirty = true; });
  methodEl.addEventListener("change", () => updateSettingsFields(form));
  root.addEventListener("submit", (ev) => {
    ev.preventDefault();
    void saveSettings(form);
  });
  remove?.addEventListener("click", () => {
    const card = name ? cards.get(name) : undefined;
    if (card) void deleteSystem(card);
  });
  return form;
}

function fillMethods(select: HTMLSelectElement): void {
  const current = select.value;
  select.replaceChildren(...Object.keys(methods).map((k) => {
    const opt = h("option", undefined, METHOD_LABEL[k] ?? k);
    opt.value = k;
    return opt;
  }));
  if (current && methods[current]) select.value = current;
}

function updateSettingsFields(form: SettingsForm): void {
  const m = methods[form.method.value] ?? { required: [], asks_password: false, iface: "" };
  show(form.userRow, m.required.includes("user"));
}

function fillSettings(form: SettingsForm, sys: System | null): void {
  if (form.dirty) return; // never overwrite what someone is typing
  fillMethods(form.method);
  form.editing = sys;
  form.name.value = sys?.name ?? "";
  form.method.value = sys?.method && methods[sys.method] ? sys.method : Object.keys(methods)[0] ?? "";
  form.host.value = sys?.host ?? "";
  form.user.value = sys?.user ?? "";
  form.subnets.value = "";
  form.idle.value = sys?.idle_minutes ? String(sys.idle_minutes) : "";
  updateSettingsFields(form);
}

// One address per line; a line without "ports" may also list several, separated by commas.
function splitAddresses(text: string): string[] {
  return text.split("\n").map((s) => s.trim().replace(/\s+/g, " ")).filter(Boolean)
    .flatMap((s) => (/ ports /.test(" " + s + " ") ? [s] : s.split(",").map((x) => x.trim()).filter(Boolean)));
}

async function saveSettings(form: SettingsForm): Promise<void> {
  clearMsg(form.msg);
  const m = methods[form.method.value] ?? { required: [], asks_password: false, iface: "" };
  const target = form.editing;
  const body: Record<string, unknown> = {
    method: form.method.value,
    host: form.host.value.trim(),
    subnets: target ? target.subnets : splitAddresses(form.subnets.value),
    idle_minutes: form.idle.value.trim() ? Number(form.idle.value) : null,
  };
  if (m.required.includes("user")) body.user = form.user.value.trim();
  const newName = form.name.value.trim();
  form.save.disabled = true;
  let r: ApiResult<{ note?: string }>;
  try {
    r = target ? await api("PUT", sysPath(target.name), body)
      : await api("POST", "/api/systems", { name: newName, ...body });
  } finally {
    form.save.disabled = false;
  }
  if (!r.ok) {
    showError(form.msg, r, "Could not save:");
    return;
  }
  form.dirty = false;
  await refresh("systems", true);
  if (target) {
    showInfo(form.msg, r.data.note ?? "Saved. Changes apply the next time this VPN connects.");
    fillSettings(form, systems.find((s) => s.name === target.name) ?? null);
  } else {
    fillSettings(form, null);
    select(newName);
    const card = cards.get(newName);
    if (card) showInfo(card.msg, "Added. Press Connect to start it.");
  }
  void refresh("status", true);
}

// ---------- gateway view ----------

const gw = {
  root: h("section", "gwbar-panel"),
  dot: h("span", "dot off"),
  state: h("span", "view-state", "Loading…"),
  power: button("Start gateway", "btn-primary"),
  download: button("Download profile"),
  msg: msgBox(),
  lines: h("dl", "facts"),
  logs: makeLogs("/api/gateway/logs", "Gateway logs"),
  pending: false,
  facts: {} as Record<string, HTMLElement>,
};

// The gateway lives in a bar fixed to the bottom of the page: its state always shows, its details fold open.
function buildGateway(): void {
  gw.root.setAttribute("aria-label", "Gateway details");
  const head = h("div", "view-head");
  head.append(h("p", "view-kind grow", "Local reaches every VPN through this gateway."), gw.power);
  gw.dot.setAttribute("aria-hidden", "true");
  const more = h("span", "gwbar-more");
  more.append("Details", strokeIcon(["M6 9l6 6 6-6"], "16"));
  $("gw-summary").append(gw.dot, gw.state, h("span", "grow"), more);
  const bar = $("gw-bar") as HTMLDetailsElement;
  bar.open = stored(GATEWAY_OPEN_KEY) === "1";
  bar.addEventListener("toggle", () => {
    store(GATEWAY_OPEN_KEY, bar.open ? "1" : null);
    if (bar.open) void loadLogs(gw.logs);
  });
  for (const label of ["Local", "WireGuard profile", "Containers"]) {
    const wrap = h("div");
    const dd = h("dd");
    wrap.append(h("dt", undefined, label), dd);
    gw.lines.append(wrap);
    gw.facts[label] = dd;
  }
  const actions = h("div", "inline");
  const logsButton = button("Show logs");
  logsButton.setAttribute("aria-expanded", "false");
  actions.append(gw.download, logsButton);
  gw.logs.box.hidden = true;
  logsButton.addEventListener("click", () => {
    gw.logs.open = !gw.logs.open;
    gw.logs.box.hidden = !gw.logs.open;
    logsButton.setAttribute("aria-expanded", String(gw.logs.open));
    setText(logsButton, gw.logs.open ? "Hide logs" : "Show logs");
    void loadLogs(gw.logs);
  });
  gw.root.append(head, gw.msg, gw.lines, actions, gw.logs.box);
  $("gw-body").append(gw.root);
  gw.power.addEventListener("click", () => void gatewayAction(gw.power.dataset.action === "stop" ? "stop" : "start"));
  gw.download.addEventListener("click", () => void downloadProfile(gw.msg));
}

function profileState(): "none" | "auto" | "changed" | "current" {
  const p = lastStatus?.gateway?.profile;
  if (!p?.ready) return "none";
  if (p.auto) return "auto";
  return p.version !== stored(PROFILE_KEY) ? "changed" : "current";
}

function renderGateway(): void {
  const st = lastStatus?.gateway;
  gw.power.disabled = gw.pending || Boolean(st?.busy);
  if (!st) {
    setTone(gw.dot, "dot", "off");
    setText(gw.state, lastStatus ? "Gateway status unavailable" : "Checking the gateway…");
    return;
  }
  const running = st.running === true;
  setTone(gw.dot, "dot", st.busy ? "busy" : st.error ? "bad" : !running ? "off" : st.mac_connected ? "ok" : "warn");
  setText(gw.state, st.busy ? (st.busy === "stopping" ? "Gateway stopping…" : "Gateway starting…")
    : st.error ? "Gateway error: " + st.error : !running ? "Gateway off"
    : st.mac_connected ? "Gateway running · local connected" : "Gateway running · local not connected");
  gw.power.dataset.action = running || st.busy === "starting" ? "stop" : "start";
  setText(gw.power, gw.power.dataset.action === "stop" ? "Stop gateway" : "Start gateway");
  gw.power.className = "btn" + (gw.power.dataset.action === "start" ? " btn-primary" : "");
  setText(gw.facts["Local"], !running ? "Not connected: the gateway is off"
    : st.mac_connected ? "Connected through WireGuard" : "Not connected yet: import the profile in WireGuard");
  const ps = profileState();
  setText(gw.facts["WireGuard profile"], ps === "none" ? "Created when the gateway starts for the first time"
    : ps === "auto" ? "Applied by the automatic tunnel service; no download needed"
    : ps === "changed" ? "Changed since your last download: download and import it again"
    : "Up to date");
  setText(gw.facts.Containers, lastStatus?.docker === false ? "Docker is not running" : "Docker is running");
  gw.download.disabled = ps === "none";
}

async function gatewayAction(action: "start" | "stop"): Promise<void> {
  clearMsg(gw.msg);
  gw.pending = true;
  renderGateway();
  const r = await api("POST", "/api/gateway/" + action);
  gw.pending = false;
  if (!r.ok) {
    showError(gw.msg, r, action === "start" ? "Could not start the gateway:" : "Could not stop the gateway:");
    openGateway();
  }
  renderGateway();
  void refresh("status", true);
}

async function downloadProfile(box: HTMLElement): Promise<void> {
  clearMsg(box);
  const r = await api<{ filename: string; content: string; version: string }>("GET", "/api/wireguard/profile");
  if (!r.ok || typeof r.data.content !== "string") {
    showError(box, r, "Could not download the profile:");
    return;
  }
  const url = URL.createObjectURL(new Blob([r.data.content], { type: "text/plain" }));
  const a = h("a");
  a.href = url;
  a.download = r.data.filename || "vpn-gateway.conf";
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  store(PROFILE_KEY, r.data.version ?? null);
  showInfo(box, "Saved " + a.download + ". In WireGuard choose Import tunnel(s) from file, pick it and turn it on. " +
    "It holds a private key: do not share it.");
  render();
}

// ---------- new VPN view ----------

const newView = {
  root: h("section", "view"),
  form: makeSettings(null),
};

function buildNewView(): void {
  newView.root.setAttribute("aria-label", "Add a VPN");
  const head = h("div", "view-head");
  const titleWrap = h("div", "view-title");
  titleWrap.append(h("h2", undefined, "Add a VPN"),
    h("p", "view-kind", "Ask the IT team that gave you the VPN, or copy the settings from their VPN app."));
  head.append(titleWrap);
  newView.root.append(head, newView.form.root);
}

// ---------- empty view ----------

let welcome: HTMLElement | null = null;

function emptyView(): HTMLElement {
  if (welcome) return welcome;
  const root = h("section", "view welcome");
  welcome = root;
  root.append(h("h2", undefined, "Connect your first VPN"));
  const steps = h("ol", "steps");
  const s1 = h("li");
  const start = button("Start the gateway", "btn-primary");
  start.addEventListener("click", () => void gatewayAction("start"));
  s1.append(h("span", undefined, "Start the gateway. The first start takes a few minutes."), start);
  const s2 = h("li");
  const add = button("Add a VPN");
  add.addEventListener("click", () => select(NEW_VIEW));
  s2.append(h("span", undefined, "Add the VPN and the addresses you need through it."), add);
  const s3 = h("li");
  const dl = button("Download profile");
  const box = msgBox();
  dl.addEventListener("click", () => void downloadProfile(box));
  s3.append(h("span", undefined, "Import the WireGuard profile on this computer, then press Connect."), dl, box);
  steps.append(s1, s2, s3);
  root.append(steps);
  return root;
}

// ---------- selection and rendering ----------

function select(view: string): void {
  selected = view;
  store(SELECTED_KEY, view);
  render();
  const pane = $("detail");
  if (window.matchMedia("(max-width: 760px)").matches) pane.scrollIntoView({ block: "start", behavior: "smooth" });
}

function currentView(): HTMLElement {
  if (selected === NEW_VIEW) return newView.root;
  const card = selected ? cards.get(selected) : undefined;
  if (card) return card.root;
  const first = systems.find((s) => cards.has(s.name));
  if (first) {
    selected = first.name;
    return cards.get(first.name)!.root;
  }
  return systemsLoaded ? emptyView() : h("p", "muted", "Loading…");
}

let shownView: HTMLElement | null = null;

function render(): void {
  const view = currentView();
  if (view !== shownView) {
    $("detail").replaceChildren(view);
    shownView = view;
    if (view === newView.root) fillSettings(newView.form, null);
    const card = selected ? cards.get(selected) : undefined;
    if (card) card.tabs.onChange(card.tabs.current);
  }
  for (const card of cards.values()) {
    card.itemButton.setAttribute("aria-current", String(card.name === selected));
  }
  $("add-vpn").setAttribute("aria-current", String(selected === NEW_VIEW));
  renderGateway();
  for (const card of cards.values()) renderCard(card);
  renderAttention();
}

function renderSystems(): void {
  $("list-loading").hidden = true;
  const list = $("vpn-list");
  const seen = new Set<string>();
  systems.forEach((sys, i) => {
    if (typeof sys.name !== "string" || !sys.name || seen.has(sys.name)) return;
    seen.add(sys.name);
    let card = cards.get(sys.name);
    if (!card) {
      card = makeCard(sys.name);
      cards.set(sys.name, card);
    }
    if (list.children[i] !== card.item) list.insertBefore(card.item, list.children[i] ?? null);
    const label = sys.method ? METHOD_LABEL[sys.method] ?? sys.method : "unknown type";
    setText(card.kind, label + (sys.host ? " · " + sys.host : ""));
    if (card.tabs.current === "Settings") fillSettings(card.settings, sys);
  });
  for (const [name, card] of cards) {
    if (!seen.has(name)) {
      forgetPopup(card);
      card.item.remove();
      cards.delete(name);
      if (selected === name) selected = null;
    }
  }
  render();
}

function openGateway(): void {
  const bar = $("gw-bar") as HTMLDetailsElement;
  if (!bar.open) bar.open = true;
  $("gw-summary").focus();
}

function renderCard(card: Card): void {
  const sys = systems.find((s) => s.name === card.name);
  const st = lastStatus?.systems?.[card.name] ?? null;
  const login = st?.login && typeof st.login === "object" ? st.login : null;
  const busyText = card.pending ?? (st?.busy === "starting" ? "Starting…" : st?.busy === "stopping" ? "Stopping…" : null);
  const waitsForUser = Boolean(login && (login.awaiting_callback || login.awaiting_input || login.auth_url));
  const issues = problems(card.name);

  let tone: Tone;
  let text: string;
  if (busyText && !waitsForUser) [tone, text] = ["busy", busyText];
  else if (!st) [tone, text] = ["off", lastStatus ? "Status unavailable" : "Loading…"];
  else if (st.error) [tone, text] = ["bad", "Error"];
  else if (waitsForUser) [tone, text] = ["warn", "Sign-in needed"];
  else if (st.tunnel_up) [tone, text] = sys?.subnets.length ? (issues ? ["warn", "Connected · check addresses"]
    : ["ok", "Connected"]) : ["warn", "Connected · no addresses"];
  else if (st.state === "running") [tone, text] = ["warn", "Connecting…"];
  else [tone, text] = ["off", "Not connected"];
  setTone(card.dot, "dot", tone);
  setTone(card.itemDot, "dot", tone);
  setText(card.state, text);
  setText(card.itemState, text);
  setText(card.itemBadge, String(issues));
  card.itemBadge.title = issues + (issues === 1 ? " address needs attention" : " addresses need attention");
  show(card.itemBadge, issues > 0);

  const running = st?.state === "running" || st?.busy === "starting" || card.pending === "Starting…";
  // Shown only once the state is known, so it never flashes "Connect" before turning into "Disconnect".
  show(card.power, Boolean(st) || card.pending !== null);
  card.power.dataset.action = running ? "stop" : "start";
  setText(card.power, running ? "Disconnect" : "Connect");
  card.power.className = "btn" + (running ? "" : " btn-primary");
  card.power.disabled = Boolean(busyText) && !waitsForUser;
  if (card.settings.remove) card.settings.remove.disabled = Boolean(busyText);

  setText(card.error, st?.error ? "Error: " + st.error : "");
  show(card.error, Boolean(st?.error));
  show(card.saml.form, Boolean(login?.kind === "gp-saml" && login.awaiting_callback));
  show(card.typed.form, Boolean(login?.kind === "gp-password" && login.awaiting_input));
  if (running) card.passOpen = false;
  show(card.pass.form, card.passOpen);

  const fortiUrl = login?.kind === "forti-saml" && typeof login.auth_url === "string" ? login.auth_url : "";
  const fortiSafe = fortiUrl.startsWith("https://"); // the server sends only https links on the VPN's own host
  if (fortiUrl) {
    setText(card.fortiLink, fortiUrl);
    if (!fortiSafe) card.fortiLink.removeAttribute("href");
    else if (card.fortiLink.getAttribute("href") !== fortiUrl) card.fortiLink.setAttribute("href", fortiUrl);
  }
  show(card.forti, Boolean(fortiUrl));

  syncPopup(card, st, login, fortiSafe ? fortiUrl : "");
  const portUser = login?.kind === "forti-saml" ? login.port_used_by ?? "" : "";
  const fortiNotice = portUser ? "The FortiGate sign-in of " + portUser + " is using the sign-in port. Finish that " +
      "sign-in first, or press Disconnect and Connect here again to take the port."
    : card.popupBlocked ? "Your browser blocked the pop-up. Use the link above."
    : card.popupClosed ? "The sign-in window was closed. Use the link above." : "";
  setText(card.fortiNotice, fortiNotice);
  show(card.fortiNotice, Boolean(fortiNotice));

  if (selected === card.name) {
    renderAddresses(card, sys ?? null);
    renderNames(card, sys ?? null, st);
  }

  // Bring a VPN forward once when it needs the user; never take the view away from them afterwards.
  const needsAction = waitsForUser || Boolean(st?.error);
  if (needsAction && !card.needsAction && (selected === null || selected === card.name)) {
    selected = card.name;
    store(SELECTED_KEY, card.name);
  }
  card.needsAction = needsAction;
}

function renderAddresses(card: Card, sys: System | null): void {
  const rs = routes[card.name];
  const entries = sys?.subnets ?? [];
  const key = JSON.stringify([entries, rs?.entries ?? null, rs?.running ?? null, macChecked]);
  if (card.addrBody.dataset.key === key) return;
  card.addrBody.dataset.key = key;
  card.addrBody.replaceChildren();
  if (!entries.length) {
    card.addrBody.append(h("p", "empty", "No addresses yet. Nothing goes through this VPN until you add one."));
    return;
  }
  const wrap = h("div", "table-wrap");
  const table = h("table", "addresses");
  const head = h("tr");
  const pathHead = h("th");
  pathHead.append(pathLegend());
  head.append(h("th", undefined, "Address"), pathHead, h("th"));
  const thead = h("thead");
  thead.append(head);
  const tbody = h("tbody");
  for (const entry of entries) {
    const e = rs?.entries.find((x) => x.entry === entry);
    const tr = h("tr");
    const addr = h("td", "addr");
    const [range, ports] = entry.split(" ports ");
    const parts = range.split(" as ");
    addr.append(h("span", "mono", parts[0]));
    if (parts[1]) addr.append(h("span", "as", " reached at "), h("span", "mono", parts[1]));
    if (ports) addr.append(h("span", "as", " · ports "), h("span", "mono", ports));
    const hint = e && rs ? routeHint(e, rs) : "";
    if (hint) addr.append(h("div", "hint", hint));
    const pathCell = h("td");
    if (e && rs) pathCell.append(pathLine(e, rs));
    else pathCell.append(h("span", "muted small", "checking…"));
    const remove = button("Remove", "btn-quiet btn-small");
    remove.setAttribute("aria-label", "Remove " + entry);
    remove.addEventListener("click", () => void changeSubnets(card, (n) => n.filter((x) => x !== entry)));
    const rm = h("td", "actions");
    rm.append(remove);
    tr.append(addr, pathCell, rm);
    tbody.append(tr);
  }
  table.append(thead, tbody);
  wrap.append(table);
  const legend = h("p", "muted small", macChecked ? "Each address passes local, the gateway and the VPN. Green " +
    "all the way means it works; an amber stop shows what is wrong below the address." : "Each address passes the gateway and the VPN. " +
    "Local is not checked when the page runs on another computer.");
  card.addrBody.append(wrap, legend);
}

function renderNames(card: Card, sys: System | null, st: SystemStatus | null): void {
  const dns = st?.tunnel_up ? st.dns : null;
  const listed = new Set((sys?.subnets ?? []).map((n) => n.split(" ports ")[0].split(" as ")[0].replace(/\/32$/, "")));
  const approved = sys?.domains ?? [];
  const key = JSON.stringify([dns, [...listed], approved]);
  if (card.namesBody.dataset.key === key) return;
  card.namesBody.dataset.key = key;
  card.namesBody.replaceChildren();
  // Only approved domains are resolved through this VPN; what it pushed is a suggestion.
  const all = [...new Set([...approved, ...(dns?.domains ?? [])])].sort();
  if (all.length) {
    const names = h("ul", "servers"); // one row per name, like the name servers below
    for (const d of all) {
      const li = h("li");
      li.append(h("span", "mono", d));
      if (approved.includes(d)) {
        li.append(h("span", "muted small", "used for this VPN"));
        const remove = button("Stop using", "btn-quiet btn-small");
        remove.setAttribute("aria-label", "Stop using " + d);
        remove.addEventListener("click", () => void changeDomains(card, (n) => n.filter((x) => x !== d)));
        li.append(remove);
      } else {
        li.append(h("span", "muted small", "sent by the VPN, not used"));
        const use = button("Use", "btn-small");
        use.setAttribute("aria-label", "Use " + d + " for this VPN");
        use.addEventListener("click", () => void changeDomains(card, (n) => [...n, d]));
        li.append(use);
      }
      names.append(li);
    }
    card.namesBody.append(h("h3", "section-title", "Names"), names, h("p", "muted small", "Your computer looks " +
      "up a name through this VPN only after you press Use. Use only the organization's own domains."));
  }
  if (!dns) {
    card.namesBody.append(h("p", "empty", st?.tunnel_up ? "This VPN sent no name servers."
      : "Connect this VPN to see the names it serves."));
    return;
  }
  if (!all.length) card.namesBody.append(h("p", "muted", "No names; reach its servers by address."));
  const servers = h("ul", "servers");
  for (const ip of dns.servers) {
    const li = h("li");
    li.append(h("span", "mono", ip));
    if (listed.has(ip)) {
      li.append(h("span", "muted small", "in Addresses: names work"));
    } else {
      const add = button("Add to Addresses", "btn-small");
      add.addEventListener("click", () => void changeSubnets(card, (n) => [...n, ip]));
      li.append(h("span", "muted small", "not in Addresses: names through it do not work"), add);
    }
    servers.append(li);
  }
  card.namesBody.append(h("h3", "section-title", "Name servers"), servers);
}

// ---------- attention strip ----------

function renderAttention(): void {
  const items: { tone: Tone; text: string; action?: string; run?: () => void }[] = [];
  const g = lastStatus?.gateway;
  if (lastStatus?.docker === false) items.push({ tone: "bad", text: "Docker is not running. Open Docker Desktop." });
  else if (g && !g.running && !g.busy && systems.length) {
    items.push({ tone: "warn", text: "The gateway is off, so no VPN can be reached.", action: "Start gateway",
      run: () => void gatewayAction("start") });
  } else if (g?.running && !g.mac_connected && profileState() === "auto") {
    // The automatic tunnel brings it up by itself; importing the profile into the WireGuard app would start a
    // second client with the same key, and the two keep breaking each other.
    items.push({ tone: "warn", text: "This computer is not connected to the gateway yet. The automatic tunnel " +
      "connects by itself; if it stays like this, turn off every vpn-gateway tunnel in the WireGuard app." });
  } else if (g?.running && !g.mac_connected) {
    items.push({ tone: "warn", text: "This computer is not connected to the gateway.", action: "Download profile",
      run: () => void downloadProfile($("global-msg")) });
  }
  if (g?.running && profileState() === "changed") {
    items.push({ tone: "warn", text: "Your WireGuard profile changed. Import it again.", action: "Download profile",
      run: () => void downloadProfile($("global-msg")) });
  }
  for (const card of cards.values()) {
    const st = lastStatus?.systems?.[card.name];
    const login = st?.login;
    const open = () => select(card.name);
    if (st?.error) items.push({ tone: "bad", text: card.name + " stopped with an error.", action: "Open", run: open });
    else if (login && (login.awaiting_callback || login.awaiting_input || login.auth_url)) {
      items.push({ tone: "warn", text: card.name + " is waiting for your sign-in.", action: "Sign in", run: open });
    } else if (problems(card.name)) {
      const n = problems(card.name);
      items.push({ tone: "warn", text: card.name + ": " + n + (n === 1 ? " address is" : " addresses are") +
        " not in use everywhere.", action: "Check", run: () => {
        select(card.name);
        selectTab(card.tabs, "Addresses");
      } });
    }
  }
  const list = $("attention");
  const key = JSON.stringify(items.map((i) => [i.tone, i.text]));
  if (list.dataset.key !== key) {
    list.dataset.key = key;
    list.replaceChildren(...items.map((i) => {
      const li = h("li", "attention-item " + i.tone);
      li.append(h("span", undefined, i.text));
      if (i.action && i.run) {
        const b = button(i.action, "btn-small");
        b.addEventListener("click", i.run);
        li.append(b);
      }
      return li;
    }));
  }
  show(list, items.length > 0);
}

// ---------- SAML login pop-up ----------
// The page opens the pop-up itself, so it may also close it. After navigation the pop-up is cross-origin:
// only `closed`, `close()` and setting `location.href` are used, each in try/catch.

// Must run synchronously in the click handler, before any await, or the browser blocks the pop-up.
function openLoginPopup(card: Card): void {
  card.popupAfter = Infinity;
  card.popupUrl = null;
  card.popupBlocked = false;
  card.popupClosed = false;
  card.gpAwaitSeen = false;
  show(card.samlFallback, false);
  let w: Window | null = null;
  try {
    w = window.open("", "gateway-login-" + card.name, "popup,width=520,height=720");
  } catch {
    w = null;
  }
  if (!w) {
    card.popup = null;
    card.popupBlocked = true;
    return;
  }
  try {
    w.opener = null; // the login site cannot navigate this page; our handle still works
  } catch {
    // ignore
  }
  try {
    const p = w.document.createElement("p");
    p.textContent = "Starting " + card.name + "… the sign-in page will load here.";
    w.document.title = "VPN Gateway sign-in";
    (w.document.body ?? w.document.documentElement).replaceChildren(p);
    w.focus();
  } catch {
    // a reused pop-up may already show another origin
  }
  card.popup = w;
}

// True while the pop-up is open. A pop-up closed by hand is forgotten.
function popupOpen(card: Card): boolean {
  if (!card.popup) return false;
  let closed = true;
  try {
    closed = card.popup.closed;
  } catch {
    closed = true;
  }
  if (closed) {
    card.popup = null;
    card.popupClosed = true;
  }
  return !closed;
}

// Close the pop-up and drop the handle. True if an open pop-up was closed.
function closePopup(card: Card): boolean {
  const w = card.popup;
  card.popup = null;
  if (!w) return false;
  try {
    if (w.closed) return false;
    w.close();
    return true;
  } catch {
    return false;
  }
}

function forgetPopup(card: Card): void {
  closePopup(card);
  card.popupUrl = null;
  card.popupBlocked = false;
  card.popupClosed = false;
  card.popupAfter = Infinity;
  card.gpAwaitSeen = false;
  show(card.samlFallback, false);
}

// Navigate the pop-up once. The URL is remembered first, so a failure is never retried in a loop.
function navigatePopup(card: Card, url: string): boolean {
  card.popupUrl = url;
  try {
    if (card.popup) card.popup.location.href = url;
    return Boolean(card.popup);
  } catch {
    return false;
  }
}

// gp-saml start response: load the one-time login URL into the pop-up, or offer it as a link.
function gpStarted(card: Card, popupUrl: unknown): void {
  const raw = typeof popupUrl === "string" ? popupUrl : "";
  let url = "";
  if (raw.startsWith("/saml/")) {
    try {
      const u = new URL(raw, window.location.href);
      if (u.origin === window.location.origin) url = u.href;
    } catch {
      url = "";
    }
  } else if (raw.startsWith("https://")) {
    url = raw;
  }
  if (!url) {
    closePopup(card);
    showError(card.msg, { status: 0, data: { error: "The server sent no valid sign-in address." } },
      "Could not open the sign-in page:");
    return;
  }
  if (popupOpen(card) && navigatePopup(card, url)) {
    showInfo(card.msg, "The sign-in page is loading in the pop-up window.");
    return;
  }
  closePopup(card);
  setText(card.samlFallbackText, (card.popupBlocked ? "Your browser blocked the pop-up." :
    "The sign-in window was closed.") + " Use this link instead (it works once, for 5 minutes).");
  card.samlFallbackLink.setAttribute("href", url);
  show(card.samlFallback, true);
}

// Runs on every render. Only a status requested after the start response may act on the pop-up.
function syncPopup(card: Card, st: SystemStatus | null, login: Login | null, fortiUrl: string): void {
  popupOpen(card);
  if (!st || lastStatusSeq <= card.popupAfter) return;
  const gpAwaiting = Boolean(login?.kind === "gp-saml" && login.awaiting_callback);
  const gpDone = card.gpAwaitSeen && !gpAwaiting;
  if (gpAwaiting) card.gpAwaitSeen = true;
  if (gpDone) {
    card.gpAwaitSeen = false;
    show(card.samlFallback, false);
  }
  if (!card.popup) return;
  if (st.tunnel_up) {
    if (closePopup(card)) showInfo(card.msg, "Connected. The sign-in window was closed.");
  } else if (st.error && card.popupUrl === null) {
    closePopup(card); // the login page never loaded
  } else if (gpDone) {
    if (closePopup(card) && !st.error) showInfo(card.msg, "Sign-in received. The sign-in window was closed.");
  } else if (fortiUrl && !st.busy && card.popupUrl !== fortiUrl) {
    navigatePopup(card, fortiUrl);
  }
}

// ---------- VPN actions ----------

async function startSystem(card: Card, password?: string): Promise<void> {
  const sys = systems.find((s) => s.name === card.name);
  if (sys?.method === "forti-saml" || sys?.method === "gp-saml") openLoginPopup(card); // before any await
  clearMsg(card.msg);
  card.pending = "Starting…";
  renderCard(card);
  const body: Record<string, string> | undefined = password === undefined ? undefined : { password };
  let r: ApiResult<{ login: string | null; popup_url: string }>;
  try {
    r = await api<{ login: string | null; popup_url: string }>("POST", sysPath(card.name, "start"), body);
  } finally {
    if (body) delete body.password;
  }
  card.pending = null;
  card.popupAfter = statusReqSeq;
  if (!r.ok) {
    closePopup(card);
    showError(card.msg, r, "Could not connect:");
  } else if (r.data.login === "gp-saml") {
    gpStarted(card, r.data.popup_url);
  } else if (sys?.method === "gp-saml") {
    closePopup(card); // no login URL came back
  }
  renderCard(card);
  void refresh("status", true);
}

async function stopSystem(card: Card): Promise<void> {
  forgetPopup(card);
  clearMsg(card.msg);
  card.pending = "Disconnecting…";
  renderCard(card);
  const r = await api("POST", sysPath(card.name, "stop"));
  card.pending = null;
  if (!r.ok) showError(card.msg, r, "Could not disconnect:");
  renderCard(card);
  void refresh("status", true);
}

async function deleteSystem(card: Card): Promise<void> {
  if (!window.confirm('Delete "' + card.name + '"? This disconnects it and removes its settings.')) return;
  forgetPopup(card);
  clearMsg(card.settings.msg);
  card.pending = "Deleting…";
  renderCard(card);
  const r = await api("DELETE", sysPath(card.name));
  card.pending = null;
  if (!r.ok) {
    showError(card.settings.msg, r, "Could not delete:");
    renderCard(card);
    return;
  }
  selected = null;
  store(SELECTED_KEY, null);
  showInfo($("global-msg"), "Deleted " + card.name + ".");
  void refresh("systems", true);
  void refresh("status", true);
}

// Send a login value (gp-saml callback link or gp-password line). It is never shown or logged.
async function sendLogin(card: Card, block: LoginBlock, action: "callback" | "input", key: string,
  emptyText: string): Promise<void> {
  clearMsg(block.msg);
  if (!block.input.value) {
    showError(block.msg, { status: 400, data: { error: emptyText } });
    block.input.focus();
    return;
  }
  const body: Record<string, string> = { [key]: block.input.value };
  block.input.value = "";
  block.submit.disabled = true;
  let r: ApiResult<unknown>;
  try {
    r = await api("POST", sysPath(card.name, action), body);
  } finally {
    delete body[key];
    block.submit.disabled = false;
  }
  if (r.ok) {
    showInfo(block.msg, action === "callback" ? "Sent. The tunnel is starting." : "Sent.");
    if (action === "callback") {
      closePopup(card);
      show(card.samlFallback, false);
    }
  } else {
    showError(block.msg, r);
  }
  void refresh("status", true);
}

// Save a VPN's addresses; the server applies them to a connected VPN at once.
async function changeSubnets(card: Card, change: (subnets: string[]) => string[]): Promise<boolean> {
  clearMsg(card.addrMsg);
  const sys = systems.find((s) => s.name === card.name);
  if (!sys) return false;
  const body: Record<string, unknown> = { method: sys.method, host: sys.host, subnets: change([...sys.subnets]) };
  if (sys.user) body.user = sys.user;
  const r = await api<{ note?: string }>("PUT", sysPath(card.name), body);
  if (!r.ok) {
    showError(card.addrMsg, r, "Could not save:");
    selectTab(card.tabs, "Addresses");
    return false;
  }
  if (r.data.note) showInfo(card.addrMsg, r.data.note);
  await refresh("systems", true);
  void loadRoutes(true);
  return true;
}

async function changeDomains(card: Card, change: (domains: string[]) => string[]): Promise<boolean> {
  clearMsg(card.msg);
  const sys = systems.find((s) => s.name === card.name);
  if (!sys) return false;
  const body: Record<string, unknown> = { method: sys.method, host: sys.host, subnets: sys.subnets,
    domains: change([...sys.domains]) };
  if (sys.user) body.user = sys.user;
  const r = await api<{ note?: string }>("PUT", sysPath(card.name), body);
  if (!r.ok) {
    showError(card.msg, r, "Could not save:");
    return false;
  }
  await refresh("systems", true);
  return true;
}

// ---------- polling ----------

const loadErrors: Record<"systems" | "status", string | null> = { systems: null, status: null };

function setLoadError(which: "systems" | "status", text: string | null): void {
  loadErrors[which] = text;
  const parts = [loadErrors.systems, loadErrors.status].filter((p): p is string => Boolean(p));
  setText($("banner-load-text"), parts.join(" "));
  show($("banner-load"), parts.length > 0);
}

async function loadSystems(): Promise<void> {
  const r = await api<{ systems: System[]; methods: Record<string, Method>; file_error: string | null }>(
    "GET", "/api/systems");
  if (!r.ok) {
    if (r.status !== 0 && r.status !== 403) {
      setLoadError("systems", "Could not load your VPNs: " + (r.data.error || "HTTP " + r.status) + ".");
    }
    return;
  }
  setLoadError("systems", null);
  methods = r.data.methods && typeof r.data.methods === "object" ? r.data.methods : {};
  systems = Array.isArray(r.data.systems) ? r.data.systems : [];
  systemsLoaded = true;
  const fileError = typeof r.data.file_error === "string" ? r.data.file_error : "";
  setText($("banner-file-text"), fileError);
  show($("banner-file"), Boolean(fileError));
  renderSystems();
}

// Only https links to this project's release pages; anything else hides the notice.
function showUpdate(update: Status["update"]): void {
  const ok = Boolean(update && update.url.startsWith("https://github.com/tariktekin8/vpn-gateway/releases/tag/"));
  if (update && ok) {
    setText($("banner-update-version"), update.latest + " (you have " + update.installed + ")");
    ($("banner-update-link") as HTMLAnchorElement).href = update.url;
  }
  show($("banner-update"), ok);
}

async function loadStatus(): Promise<void> {
  const seq = ++statusReqSeq;
  const r = await api<Status>("GET", "/api/status");
  if (r.ok) {
    setLoadError("status", null);
    lastStatus = r.data as Status;
    lastStatusSeq = Math.max(lastStatusSeq, seq);
    showUpdate(lastStatus.update);
    render();
  } else if (r.status !== 0 && r.status !== 403) {
    setLoadError("status", "Could not load the status: " + (r.data.error || "HTTP " + r.status) + ".");
  }
  void loadRoutes(false);
  if (($("gw-bar") as HTMLDetailsElement).open) void loadLogs(gw.logs);
  const card = selected ? cards.get(selected) : undefined;
  if (card && card.logs.open && shownView === card.root) void loadLogs(card.logs);
}

async function loadRoutes(force: boolean): Promise<void> {
  if (routesBusy || stopped || !systemsLoaded) return;
  if (!force && Date.now() - routesLoadedAt < ROUTES_EVERY_MS) return;
  routesBusy = true;
  routesLoadedAt = Date.now();
  try {
    const r = await api<{ systems: RouteSystem[]; mac_checked: boolean }>("GET", "/api/routes");
    if (!r.ok) return;
    macChecked = r.data.mac_checked === true;
    routes = {};
    for (const s of Array.isArray(r.data.systems) ? r.data.systems : []) routes[s.name] = s;
    render();
  } finally {
    routesBusy = false;
  }
}

const polls = {
  status: { running: false, again: false, fn: loadStatus },
  systems: { running: false, again: false, fn: loadSystems },
};

// force: after an action. If a request is running, run once more after it. A timer tick is skipped instead.
async function refresh(kind: keyof typeof polls, force: boolean): Promise<void> {
  const p = polls[kind];
  if (stopped) return;
  if (p.running) {
    if (force) p.again = true;
    return;
  }
  p.running = true;
  try {
    await p.fn();
  } finally {
    p.running = false;
  }
  if (p.again) {
    p.again = false;
    void refresh(kind, false);
  }
}

// ---------- theme ----------
// System follows the OS setting; Light and Dark override it. Stored in this browser only.

const THEMES = ["system", "light", "dark"] as const;
type Theme = (typeof THEMES)[number];

// Outline icons, 24x24, drawn with the text color: a monitor for System, a sun for Light, a moon for Dark.
const THEME_ICONS: Record<Theme, string[]> = {
  system: ["M4 4h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z", "M8 21h8", "M12 17v4"],
  light: ["M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8z", "M12 2v2", "M12 20v2", "M4.9 4.9l1.4 1.4", "M17.7 17.7l1.4 1.4",
    "M2 12h2", "M20 12h2", "M4.9 19.1l1.4-1.4", "M17.7 6.3l1.4-1.4"],
  dark: ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"],
};

function themeIcon(theme: Theme): SVGSVGElement {
  return strokeIcon(THEME_ICONS[theme], "18");
}

function strokeIcon(paths: string[], size: string): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: size, height: size, fill: "none",
    stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true" })) svg.setAttribute(k, v);
  for (const d of paths) {
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

function applyTheme(theme: Theme): void {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
  const b = $<HTMLButtonElement>("theme");
  const label = "Theme: " + theme[0].toUpperCase() + theme.slice(1);
  b.replaceChildren(themeIcon(theme));
  b.title = label;
  b.setAttribute("aria-label", label + ". Click to change.");
}

function savedTheme(): Theme {
  const t = stored(THEME_KEY);
  return (THEMES as readonly string[]).includes(t ?? "") ? (t as Theme) : "system";
}

// ---------- Advanced menu ----------

function setMenu(open: boolean): void {
  $("advanced-menu").hidden = !open;
  $("advanced").setAttribute("aria-expanded", String(open));
  if (open) ($("advanced-menu").querySelector("button") as HTMLButtonElement | null)?.focus();
}

function buildMenu(): void {
  $("advanced").addEventListener("click", () => setMenu($("advanced-menu").hidden));
  document.addEventListener("click", (ev) => {
    if (!(ev.target as HTMLElement).closest(".menu")) setMenu(false);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !$("advanced-menu").hidden) {
      setMenu(false);
      $("advanced").focus();
    }
  });
  $("menu-gateway").addEventListener("click", () => {
    setMenu(false);
    openGateway();
  });
  $("menu-profile").addEventListener("click", () => {
    setMenu(false);
    void downloadProfile($("global-msg"));
  });
  $("menu-stop-all").addEventListener("click", async () => {
    setMenu(false);
    if (!window.confirm("Disconnect every VPN and stop the gateway?")) return;
    const msg = $("global-msg");
    clearMsg(msg);
    const r = await api("POST", "/api/stop-all");
    if (r.ok) showInfo(msg, "Stopping every VPN and the gateway…");
    else showError(msg, r, "Could not stop everything:");
    void refresh("status", true);
  });
}

// ---------- start ----------

applyTheme(savedTheme());
$("theme").addEventListener("click", () => {
  const next = THEMES[(THEMES.indexOf(savedTheme()) + 1) % THEMES.length];
  store(THEME_KEY, next === "system" ? null : next);
  applyTheme(next);
});
buildGateway();
buildNewView();
buildMenu();
$("add-vpn").addEventListener("click", () => select(NEW_VIEW));

void refresh("systems", true).then(() => refresh("status", true));
setInterval(() => {
  if (!document.hidden) void refresh("status", false);
}, STATUS_EVERY_MS);
setInterval(() => {
  if (!document.hidden) void refresh("systems", false);
}, SYSTEMS_EVERY_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    void refresh("systems", true);
    void refresh("status", true);
  }
});
