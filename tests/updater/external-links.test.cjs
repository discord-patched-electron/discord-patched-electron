const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const links = require("../../integration/desktop/external-links.cjs");
const replace = (source, before, after) => {
  assert.equal(source.split(before).length, 2);
  return source.replace(before, after);
};

function fixture(endpoint = "https://canary.discord.com") {
  const handlers = [];
  const opened = [];
  const contents = {
    getURL: () => endpoint + "/channels/@me",
    on: (_name, fn) => handlers.push(fn),
  };
  const source = [
    "const nativeOpen = () => electron_1.shell.openExternal(externalUrl)",
    'mainWindow.webContents.on("will-navigate",(evt,url)=>{(0,securityUtils_1.checkUrlOriginMatches)(url,exports.WEBAPP_ENDPOINT)||evt.preventDefault()})',
    'popoutWindow.webContents.on("will-navigate",(evt,url)=>{(0,securityUtils_1.checkUrlOriginMatches)(url,webappEndpoint)||evt.preventDefault()})',
  ].join(";");
  vm.runInNewContext(links.patchDesktop(source, "/external-links.cjs", replace), {
    mainWindow: { webContents: contents },
    popoutWindow: { webContents: contents },
    exports: { WEBAPP_ENDPOINT: endpoint },
    webappEndpoint: endpoint,
    securityUtils_1: {
      checkUrlOriginMatches: (a, b) => {
        try {
          return new URL(a).origin === new URL(b).origin;
        } catch {
          return false;
        }
      },
      saferShellOpenExternal: (url) => opened.push(url),
    },
    require: () => links,
  });
  return { contents, handlers, opened, source };
}

test("main and popout same-window web links open externally while Discord stays loaded", () => {
  for (const endpoint of [
    "https://discord.com",
    "https://canary.discord.com",
    "https://ptb.discord.com",
  ]) {
    const { handlers, opened } = fixture(endpoint);
    for (const handler of handlers) {
      for (const url of [
        "https://example.com/path?q=a%26b#fragment",
        "http://example.com/",
        "https://discord.com.evil.test/",
      ]) {
        let prevented = false;
        handler(
          {
            preventDefault: () => {
              prevented = true;
            },
          },
          url,
        );
        assert.equal(prevented, true);
        assert.equal(opened.pop(), url);
      }
      handler(
        { preventDefault: () => assert.fail("Internal navigation blocked") },
        endpoint + "/login",
      );
    }
    assert.deepEqual(opened, []);
  }
});

test("blocked schemes and untrusted source documents never dispatch an opener", () => {
  const { handlers, contents, opened } = fixture();
  for (const handler of handlers) {
    for (const url of [
      "file:///tmp/test",
      "javascript:alert(1)",
      "data:text/html,test",
      "discord://invite/test",
      "about:blank",
      "invalid",
    ]) {
      let prevented = false;
      handler(
        {
          preventDefault: () => {
            prevented = true;
          },
        },
        url,
      );
      assert.equal(prevented, true);
    }
    for (const source of [
      "https://example.com",
      "https://discord.com.evil.test",
      "about:blank",
      "https://discord.com",
    ]) {
      contents.getURL = () => source;
      handler({ preventDefault() {} }, "https://example.com");
    }
    contents.getURL = () => "https://canary.discord.com/channels/@me";
  }
  assert.deepEqual(opened, []);
  const foreign = fixture("https://example.com");
  foreign.handlers[0]({ preventDefault() {} }, "https://other.test");
  assert.deepEqual(foreign.opened, []);
});

test("opener rejection and synchronous failure are handled without exposing the URL", async () => {
  const warnings = [];
  const warn = console.warn;
  console.warn = (message) => warnings.push(message);
  try {
    for (const open of [
      () => {
        throw Error("private URL");
      },
      () => Promise.reject(Error("private URL")),
    ])
      links.navigate(
        { preventDefault() {} },
        "https://example.com/private",
        { getURL: () => "https://canary.discord.com" },
        "https://canary.discord.com",
        open,
      );
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(warnings, [
      "Could not open an external link.",
      "Could not open an external link.",
    ]);
  } finally {
    console.warn = warn;
  }
});

test("unknown or duplicate navigation guards fail closed", () => {
  const { source } = fixture();
  for (const site of source.split(";")) {
    assert.throws(() => links.patchDesktop(source.replace(site, "unknown"), "/adapter", replace));
    assert.throws(() => links.patchDesktop(source + ";" + site, "/adapter", replace));
  }
});

test("all native opener requests record URL-free outcomes and preserve errors", async () => {
  const events = [],
    opened = [];
  const record = (event, details) => events.push({ event, ...details });
  const secret = "https://example.com/private?token=secret";
  await links.openExternal(secret, (url) => opened.push(url), record);
  const failure = Error(secret);
  await assert.rejects(
    links.openExternal(secret, () => Promise.reject(failure), record),
    (error) => error === failure,
  );
  await assert.rejects(
    links.openExternal(
      secret,
      () => {
        throw failure;
      },
      record,
    ),
    (error) => error === failure,
  );
  assert.deepEqual(opened, [secret]);
  assert.deepEqual(
    events.map((e) => e.phase),
    ["requested", "submitted", "requested", "failed", "requested", "failed"],
  );
  assert.equal(events[0].request, events[1].request);
  assert.notEqual(events[0].request, events[2].request);
  assert.ok(!JSON.stringify(events).includes("secret"));
  assert.ok(!JSON.stringify(events).includes("example.com"));
});
