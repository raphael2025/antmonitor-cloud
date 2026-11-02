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
const restoreSource = app.slice(app.indexOf("async function loadDeletedSites()"), app.indexOf("// 事件委托："));
assert.ok(source.length > 1000, "load the real onboarding section");

function setup(options = {}) {
  const elements = new Map();
  function element(id) {
    assert.ok(ids.has(id), `UI element exists in HTML: ${id}`);
    if (!elements.has(id)) {
      const classes = new Set();
      const tbody = { innerHTML: "" };
      elements.set(id, { value: "", textContent: "", innerHTML: "", checked: false, disabled: false,
        open: false, dataset: {}, style: {}, focused: false, listeners: {},
        classList: { add: (x) => classes.add(x), remove: (x) => classes.delete(x),
          contains: (x) => classes.has(x), toggle: (x, hide) => hide ? classes.add(x) : classes.delete(x) },
        focus() { this.focused = true; }, select() { this.selected = true; },
        addEventListener(type, callback) { this.listeners[type] = callback; }, querySelector: () => tbody,
      });
    }
    return elements.get(id);
  }
  let state = { public_url: "https://cloud.example.com", mode: "domain", fingerprint: "", warnings: [],
    address_ready: true, address_error: "",
    shared: { set: true, hint: "tail" }, auto_register: false, sites: options.sites || [] };
  let deletedSites = [], deletedCount = 0;
  const catalogue = new Map(state.sites.map(x => [x.id, x]));
  const calls = [], copies = [], translations = [];
  const context = vm.createContext({ $: element, t: (key, values) => { translations.push({key, values}); return key; },
    atob, TextDecoder, URL, curRole: options.role || "admin", curSite: null, curSiteData: null,
    refresh: async () => {}, fmtFull: value => String(value),
    esc: (s) => String(s).replace(/[&<>"']/g, (x) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[x])),
    api: async url => {
      if (url === "/api/sites/deleted") return { sites: deletedSites };
      if (options.connectResponse) return options.connectResponse(state);
      if (options.failConnectReload && deletedCount) throw new Error("reload failed");
      return state;
    },
    send: async (method, url, body) => {
      calls.push({ method, url, body });
      if (method === "DELETE") {
        if (options.beforeDelete) await options.beforeDelete();
        if (options.deleteError) return { ok: false, status: options.deleteStatus || 403 };
        const id = decodeURIComponent(url.split("/").at(-1));
        deletedSites.push({ ...catalogue.get(id), deleted_ts: 1800000000 });
        state = { ...state, sites: state.sites.filter(x => x.id !== id) };
        deletedCount++;
        return { ok: true, json: async () => ({ ok: true }) };
      }
      if (url.endsWith("/restore")) {
        const id = decodeURIComponent(url.split("/").at(-2));
        deletedSites = deletedSites.filter(x => x.id !== id);
        state = { ...state, sites: [...state.sites, { ...catalogue.get(id), own_token: false, revoked: true, can_reveal: false }] };
        return { ok: true, json: async () => ({ ok: true, requires_new_token: true }) };
      }
      if (url === "/api/connect/reveal" && options.beforeReveal) await options.beforeReveal();
      if (url === "/api/connect/token" && body.action === "rotate" && options.beforeRotate) await options.beforeRotate();
      if (url === "/api/connect/token" && body.action === "revoke") {
        state = { ...state, sites: state.sites.map(x => x.id === body.target ? { ...x, own_token: false, revoked: true, can_reveal: false } : x) };
        return { ok: true, json: async () => ({ ok: true, target: body.target }) };
      }
      if (url === "/api/connect/settings") {
        state = { ...state, ...body, public_url: body.public_url.replace(/\/+$/, "") };
        return { ok: true, json: async () => state };
      }
      const target = body.target || "st-test";
      const code = "AMC1-" + Buffer.from(JSON.stringify({ u: state.public_url, t: "secret-test-token",
        s: target, n: body.site_name || "北场 / 一期", y: body.site_type || "hydro" })).toString("base64url");
      return { ok: true, json: async () => ({ target, token: "secret-test-token", code,
        public_url: state.public_url, site_name: body.site_name || "北场 / 一期", site_type: body.site_type || "hydro",
        ...options.response }) };
    },
    errText: async () => "request failed", confirm: () => options.confirm !== false,
    navigator: { clipboard: { writeText: async (value) => {
      if (options.clipboardBlocked) throw new Error("clipboard denied");
      copies.push(value);
    } } },
    document: { body: { appendChild() {} }, createElement: () => ({ value: "", select() {}, remove() {} }),
      execCommand: () => options.execCopyResult === true },
    setTimeout: () => 1, clearTimeout() {},
  });
  vm.runInContext(source, context, { filename: "web/app.js:onboarding" });
  vm.runInContext(restoreSource, context, { filename: "web/app.js:restore" });
  element("connSiteName").value = "北场 / 一期";
  element("connSiteType").value = "hydro";
  element("connInstallOs").value = "linux";
  element("connPw").value = "password-for-test";
  context.renderConnect(state);
  return { context, element, calls, copies, translations, state: () => state,
    clickRow: (act, id) => element("tblConnSites").listeners.click({ target: { closest: () => ({ disabled: false, dataset: { act, id } }) } }),
    restore: id => element("deletedSitesList").listeners.click({ target: { closest: () => ({ disabled: false, dataset: { restoreSite: id } }) } }),
  };
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
  assert.equal(a.copies.at(-1), a.element("connCode").value, "generation copies the entire connection packet");
  const decoded = JSON.parse(Buffer.from(a.copies.at(-1).slice(5), "base64url").toString());
  assert.deepEqual(decoded, { u: "https://new.example.com", t: "secret-test-token", s: "st-test", n: "北场 / 一期", y: "hydro" });
  assert.equal(a.element("connResultUrl").textContent, decoded.u);
  assert.equal(a.element("connResultSiteId").textContent, decoded.s);
  assert.equal(a.element("connMsg").textContent, "conn.copied_info");
  await a.element("connCodeCopy").onclick();
  assert.equal(a.copies.at(-1), a.element("connCode").value);
  await a.element("connTokCopy").onclick();
  assert.equal(a.copies.at(-1), decoded.t, "advanced copy is explicitly the token only");
  assert.equal(a.element("connMsg").textContent, "conn.token_copied");
  assert.match(html, /id="connTokCopy" data-i18n="conn.copy_token"/);
  assert.match(html, /data-i18n="conn.token_only_help"/);
  await a.context.connAction("reveal", "st-test");
  assert.equal(a.calls.at(-1).url, "/api/connect/reveal", "recopying an existing site does not rotate credentials");
  assert.equal(a.copies.at(-1), a.element("connCode").value, "existing-site action copies in one operation");
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
  a.context.renderConnect({ ...a.state(), sites: [
    { id: "st-shared", name: "已有共享场地", mode: "push", registered: true, can_reveal: true },
    { id: "st-revoked", name: "已撤销场地", mode: "push", registered: true, revoked: true, can_reveal: false },
    { id: "st-pull", name: "拉取场地", mode: "pull", registered: true, can_reveal: false },
  ] });
  const sharedTable = a.element("tblConnSites").querySelector("tbody").innerHTML;
  assert.ok(sharedTable.includes('data-act="reveal" data-id="st-shared"'), "existing shared sites can copy without rotating");
  assert.ok(sharedTable.includes('data-act="rotate" data-id="st-shared"'), "dedicated-token creation remains a separate action");
  assert.ok(!sharedTable.includes('data-act="reveal" data-id="st-revoked"'), "revocation cannot fall back to shared");
  assert.ok(!sharedTable.includes('data-act="reveal" data-id="st-pull"'));
  const beforeSharedCopy = a.calls.length;
  await a.context.connAction("reveal", "st-shared");
  assert.equal(a.calls.length, beforeSharedCopy + 1);
  assert.equal(a.calls.at(-1).url, "/api/connect/reveal");
  assert.equal(a.calls.at(-1).body.target, "st-shared");
  assert.equal(a.copies.at(-1), a.element("connCode").value);
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

  const c = setup({ clipboardBlocked: true });
  await c.element("connNewSite").onclick();
  assert.equal(c.element("connMsg").textContent, "conn.copy_manual", "execCommand false must not claim success");
  assert.equal(c.element("connDetails").open, true);
  assert.equal(c.element("connCode").focused, true);
  assert.equal(c.element("connCode").selected, true);
  assert.ok(c.element("connCode").value.startsWith("AMC1-"), "manual copying keeps the same packet available");
  const d = setup({ clipboardBlocked: true, execCopyResult: true });
  await d.element("connNewSite").onclick();
  assert.equal(d.element("connMsg").textContent, "conn.copied_info", "legacy copy fallback can succeed");

  const packet = values => "AMC1-" + Buffer.from(JSON.stringify(values)).toString("base64url");
  for (const code of ["secret-test-token", packet({t: "secret-test-token"}),
    packet({u: "https://cloud.example.com"}), packet({u: "https://user:secret@cloud.example.com", t: "test"}),
    packet({u: "https://wrong-destination.example.com", t: "test"}), "AMC1-broken"]) {
    const invalid = setup({response: {code}});
    await invalid.element("connNewSite").onclick();
    assert.equal(invalid.copies.length, 0, "incomplete packets and raw tokens must never be copied as connection info");
    assert.equal(invalid.element("connMsg").textContent, "conn.invalid_code");
    assert.equal(invalid.element("connResult").classList.contains("hidden"), true);
    assert.equal(invalid.element("connCode").value, "");
  }
  const modified = setup();
  await modified.element("connNewSite").onclick();
  modified.element("connCode").value = "secret-test-token";
  await modified.element("connCodeCopy").onclick();
  assert.equal(modified.copies.length, 1, "the repeat-copy button also validates its packet");
  assert.equal(modified.element("connMsg").textContent, "conn.invalid_code");

  const metadata = setup({response: {public_url: "https://wrong-display.example.com", token: "wrong-display-token",
    site_name: "Wrong display name", site_type: "air"}});
  await metadata.element("connNewSite").onclick();
  assert.equal(metadata.element("connResultUrl").textContent, "https://cloud.example.com");
  assert.equal(metadata.element("connTok").value, "secret-test-token");
  assert.ok(metadata.element("connResultFor").textContent.includes("北场 / 一期 · type.hydro"),
    "visible destination and site details come from the actual copied packet");

  const legacy = setup({response: {code: packet({u: "https://cloud.example.com", t: "old-shared-token"})}});
  await legacy.context.connAction("reveal", "shared");
  assert.equal(legacy.copies.length, 1, "legacy AMC1 packets still work");
  assert.equal(legacy.element("connIncludes").textContent, "conn.includes_basic");
  assert.equal(legacy.element("connResultSiteRow").classList.contains("hidden"), true);

  const e = setup();
  e.context.renderConnect({ ...e.state(), address_ready: false, address_error: "HTTPS is required" });
  await e.element("connNewSite").onclick();
  assert.equal(e.calls.length, 0, "unusable addresses never issue credentials");
  assert.equal(e.element("connMsg").textContent, "HTTPS is required");
  assert.equal(e.element("connAdvanced").open, true);

  const oldSite = { id: "st-old", name: "test", type: "air", mode: "push", registered: false, own_token: true };
  const newSite = { ...oldSite, id: "st-new" };
  const revoked = { ...oldSite, own_token: false, revoked: true };
  for (const entry of [oldSite, revoked, { ...oldSite, registered: true }, { ...oldSite, from_config: true }]) {
    const h = setup({ sites: [entry, newSite, { ...oldSite, id: "st-pull", mode: "pull" }] });
    const table = h.element("tblConnSites").querySelector("tbody").innerHTML;
    assert.ok(table.includes('data-act="delete" data-id="st-old"'), "pending, revoked, registered and configured-token push sites can be deleted");
    assert.ok(!table.includes('data-act="delete" data-id="st-pull"'), "pull sites keep config-file deletion semantics");
    await h.context.connAction("reveal", "st-old");
    h.element("connPw").value = "";
    h.element("connUrl").value = "https://unsaved.example.com";
    await h.clickRow("delete", "st-old");
    assert.equal(h.calls.at(-1).method, "DELETE");
    assert.equal(h.calls.at(-1).url, "/api/site/st-old");
    assert.equal(h.calls.at(-1).body, undefined, "deletion uses the existing admin DELETE API, without copying credentials into its body");
    const confirmation = h.translations.find(x => x.key === "conn.delete_confirm").values;
    assert.equal(confirmation.name, "test"); assert.equal(confirmation.id, "st-old");
    assert.equal(h.element("connCode").value, "");
    assert.equal(h.element("connTok").value, "");
    const after = h.element("tblConnSites").querySelector("tbody").innerHTML;
    assert.ok(!after.includes('data-id="st-old"'));
    assert.ok(after.includes('data-id="st-new"'), "same-name site with a different ID remains");
    assert.equal(h.element("connMsg").textContent, "conn.deleted");
    h.context.renderConnect({ ...h.state(), sites: [entry, newSite] });
    assert.ok(!h.element("tblConnSites").querySelector("tbody").innerHTML.includes('data-id="st-old"'), "a stale response cannot reinsert the deleted row");
    await h.restore("st-old");
    const restored = h.element("tblConnSites").querySelector("tbody").innerHTML;
    assert.ok(restored.includes('data-act="rotate" data-id="st-old"'));
    assert.ok(!restored.includes('data-act="reveal" data-id="st-old"'));
    assert.ok(restored.includes("conn.revoked_label"));
    assert.equal(h.element("connMsg").textContent, "site.restored");
    assert.equal(h.element("connCode").value, "", "restoration does not reveal or copy the old credential");
  }
  for (const options of [{ confirm: false }, { role: "viewer" }]) {
    const h = setup({ ...options, sites: [oldSite] });
    await h.clickRow("delete", "st-old");
    assert.equal(h.calls.length, 0);
    assert.ok(h.element("tblConnSites").querySelector("tbody").innerHTML.includes('data-id="st-old"'));
  }
  const denied = setup({ sites: [oldSite], deleteError: true });
  await denied.context.connAction("reveal", "st-old");
  const oldCode = denied.element("connCode").value;
  await denied.clickRow("delete", "st-old");
  assert.equal(denied.element("connCode").value, oldCode);
  assert.ok(denied.element("tblConnSites").querySelector("tbody").innerHTML.includes('data-id="st-old"'));
  assert.equal(denied.element("connMsg").textContent, "request failed");
  const uncertain = setup({ sites: [oldSite], deleteError: true, deleteStatus: 503 });
  await uncertain.context.connAction("reveal", "st-old");
  await uncertain.clickRow("delete", "st-old");
  assert.equal(uncertain.element("connCode").value, "", "server errors cannot leave a possibly revoked credential ready to copy");
  assert.ok(uncertain.element("tblConnSites").querySelector("tbody").innerHTML.includes('data-id="st-old"'));
  assert.equal(uncertain.element("connMsg").textContent, "request failed");

  const unrelated = setup({ sites: [revoked, newSite] });
  await unrelated.context.connAction("reveal", "st-new");
  const newCode = unrelated.element("connCode").value;
  await unrelated.clickRow("delete", "st-old");
  assert.equal(unrelated.element("connCode").value, newCode, "deleting old test does not clear the different-ID new test's packet");
  await unrelated.context.connAction("revoke", "st-new");
  assert.equal(unrelated.element("connCode").value, "");
  assert.ok(unrelated.element("tblConnSites").querySelector("tbody").innerHTML.includes('data-id="st-new"'), "revoking only the key retains the site row");
  assert.equal(unrelated.element("connMsg").textContent, "conn.revoked");
  const reload = setup({ sites: [oldSite], failConnectReload: true });
  await reload.clickRow("delete", "st-old");
  assert.equal(reload.element("connMsg").textContent, "conn.deleted_refresh_failed");
  assert.ok(!reload.element("tblConnSites").querySelector("tbody").innerHTML.includes('data-id="st-old"'));

  let releaseDelete;
  const deleteGate = new Promise(resolve => { releaseDelete = resolve; });
  const busy = setup({ sites: [oldSite], beforeDelete: () => deleteGate });
  const deleting = busy.clickRow("delete", "st-old");
  await busy.clickRow("delete", "st-old");
  await busy.context.connAction("rotate", "st-old");
  assert.equal(busy.calls.length, 1, "duplicate deletion and token rotation are blocked while deleting");
  releaseDelete(); await deleting;

  let releaseReveal;
  const revealGate = new Promise(resolve => { releaseReveal = resolve; });
  const late = setup({ sites: [oldSite], beforeReveal: () => revealGate });
  const revealing = late.context.connAction("reveal", "st-old");
  await late.clickRow("delete", "st-old");
  releaseReveal(); await revealing;
  assert.equal(late.copies.length, 0, "late credential responses cannot recopy a deleted site's code");
  assert.equal(late.element("connCode").value, "");
  assert.equal(late.element("connMsg").textContent, "conn.deleted");

  let releaseRotate;
  const rotateGate = new Promise(resolve => { releaseRotate = resolve; });
  const rotate = setup({ sites: [oldSite], beforeRotate: () => rotateGate });
  const rotating = rotate.context.connAction("rotate", "st-old");
  await rotate.context.connAction("rotate", "st-old");
  assert.equal(rotate.calls.length, 1, "double-clicking rotate cannot issue two different credentials");
  releaseRotate(); await rotating;
  assert.ok(rotate.element("connCode").value.startsWith("AMC1-"));

  const external = setup({ sites: [oldSite] });
  await external.clickRow("delete", "st-old");
  external.state().sites.push({ ...revoked }); // another administrator explicitly restored this ID
  await external.context.openConnect();
  assert.ok(external.element("tblConnSites").querySelector("tbody").innerHTML.includes('data-act="rotate" data-id="st-old"'), "fresh authoritative reload shows an externally restored site");

  let releaseOldList, listCount = 0;
  const oldList = new Promise(resolve => { releaseOldList = resolve; });
  const listing = setup({ sites: [oldSite], connectResponse: state => ++listCount === 1 ? oldList : state });
  const loading = listing.context.loadConnect();
  await listing.clickRow("delete", "st-old");
  releaseOldList({ ...listing.state(), sites: [oldSite] }); await loading;
  assert.ok(!listing.element("tblConnSites").querySelector("tbody").innerHTML.includes('data-id="st-old"'), "a list request started before deletion cannot reinsert the row");

  console.log("onboarding UI: complete packets, pending/revoked deletion, restore/rebind, failures and concurrent requests passed");
})().catch((e) => { console.error(e); process.exitCode = 1; });
