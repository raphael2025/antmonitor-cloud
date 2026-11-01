/* Execute the real onboarding handlers with a small DOM/network adapter. No browser or network required. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
const app = fs.readFileSync(path.join(root, "web/app.js"), "utf8");
const source = app.slice(app.indexOf("// ---- 场地接入："), app.indexOf("// ---- 启动 ----"));
assert.ok(source.length > 1000, "load the real onboarding section");

function setup() {
  const elements = new Map();
  function element(id) {
    assert.ok(ids.has(id), `UI element exists in HTML: ${id}`);
    if (!elements.has(id)) {
      const classes = new Set();
      const tbody = { innerHTML: "" };
      elements.set(id, { value: "", textContent: "", innerHTML: "", checked: false, disabled: false,
        open: false, dataset: {}, style: {}, focused: false,
        classList: { add: (x) => classes.add(x), remove: (x) => classes.delete(x),
          contains: (x) => classes.has(x), toggle: (x, hide) => hide ? classes.add(x) : classes.delete(x) },
        focus() { this.focused = true; }, addEventListener() {}, querySelector: () => tbody,
      });
    }
    return elements.get(id);
  }
  let state = { public_url: "https://cloud.example.com", mode: "domain", fingerprint: "", warnings: [],
    shared: { set: true, hint: "tail" }, auto_register: false, sites: [] };
  const calls = [], copies = [];
  const context = vm.createContext({ $: element, t: (key) => key,
    esc: (s) => String(s).replace(/[&<>"']/g, (x) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[x])),
    api: async () => state,
    send: async (method, url, body) => {
      calls.push({ method, url, body });
      if (url === "/api/connect/settings") {
        state = { ...state, ...body, public_url: body.public_url.replace(/\/+$/, "") };
        return { ok: true, json: async () => state };
      }
      return { ok: true, json: async () => ({ target: "st-test", token: "secret-test-token", code: "AMC1-secret-code",
        site_name: body.site_name, site_type: body.site_type }) };
    },
    errText: async () => "request failed", confirm: () => true,
    navigator: { clipboard: { writeText: async (value) => copies.push(value) } },
    setTimeout: () => 1, clearTimeout() {},
  });
  vm.runInContext(source, context, { filename: "web/app.js:onboarding" });
  element("connSiteName").value = "北场 / 一期";
  element("connSiteType").value = "hydro";
  element("connInstallOs").value = "linux";
  element("connPw").value = "password-for-test";
  context.renderConnect(state);
  return { context, element, calls, copies, state: () => state };
}

(async () => {
  const a = setup();
  a.element("connUrl").value = "https://new.example.com";
  for (const action of [() => a.element("connNewSite").onclick(),
    () => a.context.connAction("reveal", "st-test"), () => a.context.connAction("rotate", "st-test")]) await action();
  assert.equal(a.calls.length, 0, "unsaved URL blocks create, reveal and rotate before any token request");
  assert.equal(a.element("connMsg").textContent, "conn.url_unsaved");
  assert.equal(a.element("connAdvanced").open, true);
  a.context.renderConnect(a.state());
  assert.equal(a.element("connUrl").value, "https://new.example.com", "refresh preserves unsaved edits");
  await a.element("connUrlSave").onclick();
  await a.element("connNewSite").onclick();
  assert.equal(a.calls.length, 2, "saving the URL permits the next create request");
  assert.equal(a.calls[1].body.site_name, "北场 / 一期");
  assert.equal(a.calls[1].body.site_type, "hydro");
  assert.equal(a.calls[1].body.password, "password-for-test");
  assert.equal(a.element("connNewSite").disabled, false);
  for (const [label, sourceName] of [["Github", "github"], ["Gitee", "gitee"]]) {
    const command = a.element("connInstall" + label).textContent;
    assert.ok(command.includes(`--source ${sourceName} --connect-code -`));
    assert.ok(!command.includes("secret") && !command.includes("password-for-test") && !command.includes("AMC1-"));
    await a.element("connInstall" + label + "Copy").onclick();
    assert.equal(a.copies.at(-1), command);
  }
  a.element("connInstallOs").value = "windows";
  a.element("connInstallOs").onchange();
  assert.ok(a.element("connInstallGitee").textContent.includes("install.ps1'))) -Source gitee"));
  assert.ok(!a.element("connInstallGitee").textContent.includes("secret"));
  a.context.renderConnect({ ...a.state(), sites: [{ id: "st-test", name: "<北场>", type: "hydro", registered: false, own_token: true, hint: "tail" }] });
  const table = a.element("tblConnSites").querySelector("tbody").innerHTML;
  assert.ok(table.includes("&lt;北场&gt;") && table.includes("conn.waiting"));
  assert.ok(!table.includes("<北场>"));
  a.context.hideConnResult();
  assert.equal(a.element("connCode").value, "");
  assert.equal(a.element("connTok").value, "");

  const b = setup();
  b.element("connSiteName").value = " ";
  await b.element("connNewSite").onclick();
  assert.equal(b.calls.length, 0);
  assert.equal(b.element("connMsg").textContent, "conn.name_invalid");
  b.element("connSiteName").value = "正常名称";
  b.element("connPw").value = "";
  await b.element("connNewSite").onclick();
  assert.equal(b.calls.length, 0);
  assert.equal(b.element("connMsg").textContent, "conn.need_pw");
  assert.equal(b.element("connNewSite").disabled, false);
  console.log("onboarding UI: unsaved URL, named create, secret-free source commands, pending names and validation passed");
})().catch((e) => { console.error(e); process.exitCode = 1; });
