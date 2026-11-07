const assert = require("node:assert/strict");
const path = require("node:path");
const { BrowserWindow } = require("electron");

module.exports = async function check({
  window,
  values,
  click,
  get,
  until,
  evaluate,
  capture,
  root,
}) {
  const permissions = require(path.join(root, "integration/updater/capture-permission.cjs"));
  const originalPermissions = {
    status: permissions.status,
    allow: permissions.allow,
    revoke: permissions.revoke,
  };
  let revokeCalls = 0;
  permissions.revoke = async () => {
    revokeCalls++;
    permission = { allowed: false, configured: false, available: true, reason: null };
    return permission;
  };
  let permission = { allowed: false, configured: false, available: true, reason: null };
  let permissionCalls = 0;
  permissions.status = async () => permission;
  permissions.allow = async () => {
    permissionCalls++;
    return permission;
  };
  // Legacy custom method preferences must not silently enable the new picker.
  values.openasar.screensharing = { captureMethod: "kms", capturePacing: true };
  await click("t_screensharing");
  await until(() =>
    get('$("screenshare-experimental") && !$("screenshare-experimental").disabled'),
  );
  assert.equal(await get('$("screenshare-experimental").checked'), false);
  assert.equal(await get('$("screenshare-permission-allow").disabled'), true);
  assert.equal(await get('$("screenshare-method")'), null);
  assert.equal(await get('$("screenshare-pacing").checked'), true);
  assert.equal(await get('$("screenshare-preserve-bitrate").checked'), false);
  assert.equal(await get('$("screenshare-details")'), null);
  assert.match(await get('$("screenshare-current-state").textContent'), /aren’t available/);
  assert.equal(permissionCalls, 0);
  const blocked = await evaluate(
    `window.Native.capturePermissionAllow().then(() => "unexpected", error => error.message)`,
  );
  assert.match(blocked, /Enable experimental capture methods first/);
  assert.equal(permissionCalls, 0, "main IPC must enforce the toggle, not just the button");

  assert.equal(await evaluate(`document.querySelector('.dpe-capture-launch-help').inert`), true);
  assert.equal(
    await evaluate(`document.querySelector('.dpe-capture-launch-help .command-copy').disabled`),
    true,
  );
  assert.equal(
    await evaluate(
      `getComputedStyle(document.querySelector('.dpe-capture-launch-help code')).userSelect`,
    ),
    "none",
  );
  assert.equal(
    await evaluate(`(() => {
    const event = new Event('copy', {bubbles:true, cancelable:true});
    document.querySelector('.dpe-capture-launch-help code').dispatchEvent(event);
    return event.defaultPrevented;
  })()`),
    true,
  );

  // This isolated local renderer stands in for the native module. Every status
  // request and response still passes through Electron's real IPC, including
  // the exact originating WebContents/mainFrame checks. No account is loaded.
  const peer = new BrowserWindow({
    show: false,
    webPreferences: {
      preload: path.join(root, "tests/launch-settings/screensharing-preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
    },
  });
  peer.webContents.on("console-message", (event) =>
    console.error("Screenshare fixture:", event.message),
  );
  const presentation = require(path.join(root, "integration/updater/presentation.cjs"));
  const trusted = presentation.trusted;
  presentation.trusted = (contents) => contents === peer.webContents || trusted(contents);
  try {
    await peer.loadURL("data:text/html,<title>Isolated screenshare diagnostics fixture</title>");
    peer.webContents.send("dpe-fixture-status", {
      active: false,
      methodReasons: { kms: "permission-required" },
    });
    const reload = async () => {
      await click("t_settings");
      await click("t_screensharing");
      await evaluate(
        "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
      );
      await until(() =>
        get(
          '!$("screenshare-current-title").parentElement.getAttribute("aria-busy") || $("screenshare-current-title").parentElement.getAttribute("aria-busy") === "false"',
        ),
      );
    };
    await reload();
    await until(() => get('$("screenshare-current-state").textContent.includes("aren’t sharing")'));
    peer.webContents.send("dpe-fixture-status", {
      active: false,
      watching: [
        {
          codec: "AV1X",
          decoder: "electron",
          hardware: "hardware",
          width: 1920,
          height: 1072,
          fps: 60,
        },
      ],
    });
    await reload();
    await until(() =>
      get('$("screenshare-incoming-title")?.parentElement.textContent.includes("Hardware")'),
    );
    assert.match(
      await get('$("screenshare-incoming-title").parentElement.textContent'),
      /1920 × 1072/,
    );
    await evaluate(
      'document.getElementById("screenshare-incoming-title").scrollIntoView({block:"start"})',
    );
    await capture("screenshare-incoming-hardware");
    peer.webContents.send("dpe-fixture-status", {
      active: false,
      watching: [
        {
          codec: "VP8",
          decoder: "libvpx",
          hardware: "software",
          width: 1280,
          height: 720,
          fps: 30,
        },
      ],
    });
    await reload();
    await until(() =>
      get('$("screenshare-incoming-title")?.parentElement.textContent.includes("Software")'),
    );
    peer.webContents.send("dpe-fixture-status", { active: false });
    await reload();
    assert.equal(await get('$("screenshare-incoming-title")'), null);

    await click("screenshare-experimental");
    await until(() => values.openasar.screensharing?.experimentalCapture === true);
    await until(() => get('!$("screenshare-permission-allow").disabled'));
    assert.equal(permissionCalls, 0, "enabling the picker must never authorize capture");
    await evaluate(`document.querySelector('.dpe-capture-launch-help .command-copy').click()`);
    await until(
      async () =>
        (await require("electron").clipboard.readText()) ===
        `'${require("node:os").homedir()}/.local/bin/dpe-game-capture' -- %command%`,
    );

    await until(() =>
      evaluate(
        `document.querySelector('.dpe-capture-launch-help .command-copy').getAttribute('aria-label') === 'Copied command'`,
      ),
    );
    await until(() =>
      evaluate(
        `document.querySelector('.dpe-capture-launch-help .command-copy').getAttribute('aria-label') === 'Copy command'`,
      ),
    );

    let finishPermission;
    permissions.allow = () => {
      permissionCalls++;
      return new Promise((resolve) => {
        finishPermission = resolve;
      });
    };
    window.focus();
    window.webContents.focus();
    await evaluate('document.getElementById("screenshare-permission-allow").focus()');
    for (const type of ["keyDown", "keyUp"])
      window.webContents.sendInputEvent({ type, keyCode: "Space" });
    await until(() => Boolean(finishPermission));
    assert.equal(await get('$("screenshare-permission-allow").checked'), true);
    assert.match(
      await get('$("screenshare-permission-status").textContent'),
      /waiting for authentication/i,
    );
    assert.equal(await get('$("screenshare-permission-allow").disabled'), true);
    assert.equal(await get('$("screenshare-experimental").disabled'), true);
    assert.equal(
      await evaluate(
        `getComputedStyle(document.getElementById('screenshare-permission-allow').nextElementSibling).opacity`,
      ),
      "1",
      "pending authorization stays fully visible",
    );
    assert.equal(permissionCalls, 1);
    permission = {
      allowed: false,
      configured: false,
      available: true,
      reason: "authorization-cancelled",
    };
    finishPermission(permission);
    await until(() => get('!$("screenshare-permission-allow").disabled'));
    assert.match(await get('$("screenshare-permission-status").textContent'), /cancelled/);
    assert.equal(await get('$("screenshare-permission-allow").checked'), false);
    await capture("screensharing-permission-cancelled");
    permissions.allow = async () => {
      permissionCalls++;
      permission = {
        allowed: true,
        configured: true,
        canRevoke: true,
        available: true,
        reason: null,
      };
      return permission;
    };
    await click("screenshare-permission-allow");
    await until(() =>
      get(
        '$("screenshare-permission-allow").checked && !$("screenshare-permission-allow").disabled',
      ),
    );
    assert.equal(
      await get('$("screenshare-permission-status").textContent'),
      permission.configured ? "System permission installed" : "System permission uninstalled",
    );
    assert.equal(permissionCalls, 2);
    await capture("screensharing-permission-allowed");
    assert.equal(
      await get('$("screenshare-permission-status").textContent'),
      "System permission installed",
    );
    const successfulRevoke = permissions.revoke;
    let finishRevoke;
    permissions.revoke = () =>
      new Promise((resolve) => {
        finishRevoke = resolve;
      });
    await click("screenshare-experimental");
    await until(() => Boolean(finishRevoke));
    assert.equal(await get('$("screenshare-revoke")'), null);
    assert.equal(await get('$("screenshare-experimental").checked'), false);
    assert.equal(await get('$("screenshare-experimental").disabled'), true);
    assert.equal(await get('$("screenshare-permission-allow").checked'), false);
    assert.equal(
      await evaluate(
        `getComputedStyle(document.getElementById('screenshare-experimental').nextElementSibling).opacity`,
      ),
      "1",
      "pending removal stays fully visible",
    );
    assert.match(
      await get('$("screenshare-permission-status").textContent'),
      /Removing system permission/,
    );
    permission = { ...permission, reason: "authorization-cancelled" };
    finishRevoke(permission);
    await until(() => get('!$("screenshare-experimental").disabled'));
    assert.equal(values.openasar.screensharing.experimentalCapture, true);
    assert.equal(await get('$("screenshare-experimental").checked'), true);
    assert.equal(await get('$("screenshare-permission-allow").checked'), true);
    await capture("screensharing-revoke-cancelled");
    permissions.revoke = successfulRevoke;
    await click("screenshare-permission-allow");
    await until(() =>
      get(
        '!$("screenshare-permission-allow").checked && !$("screenshare-permission-allow").disabled',
      ),
    );
    assert.equal(revokeCalls, 1);
    assert.equal(
      await get('$("screenshare-permission-status").textContent'),
      "System permission uninstalled",
    );
    permission = {
      configured: true,
      allowed: false,
      needsUpdate: true,
      canRevoke: true,
      available: true,
      reason: "permission-update-required",
    };
    await reload();
    assert.equal(await get('$("screenshare-permission-allow").checked'), true);
    assert.match(await get('$("screenshare-permission-status").textContent'), /needs an update/);
    assert.equal(await get('$("screenshare-permission-update").disabled'), false);
    const allowBeforeUpdate = permissions.allow;
    let helperUpdates = 0;
    permission = { ...permission, allowed: true, reason: null };
    permissions.allow = async () => {
      helperUpdates++;
      permission = { ...permission, needsUpdate: false };
      return permission;
    };
    await reload();
    assert.equal(await get('$("screenshare-permission-update").textContent'), "Update helper");
    assert.equal(await get('$("screenshare-permission-allow").checked'), true);
    window.focus();
    window.webContents.focus();
    await evaluate('document.getElementById("screenshare-permission-update").focus()');
    assert.equal(await get('$("screenshare-permission-update").disabled'), false);
    assert.equal(await get("document.activeElement?.id"), "screenshare-permission-update");
    for (const type of ["keyDown", "keyUp"])
      window.webContents.sendInputEvent({ type, keyCode: "Space" });
    await until(() => helperUpdates === 1);
    await until(() => get('$("screenshare-permission-update") === null'));
    await until(() => get('document.activeElement?.id === "screenshare-permission-allow"'));
    assert.equal(await get('$("screenshare-permission-allow").checked'), true);
    assert.equal(revokeCalls, 1, "updating a helper preserves the existing permission");
    await capture("screensharing-helper-updated");
    permissions.allow = allowBeforeUpdate;
    permission = {
      configured: true,
      allowed: false,
      needsUpdate: false,
      canRevoke: false,
      available: false,
      reason: "permission-blocked",
    };
    await reload();
    assert.equal(await get('$("screenshare-permission-allow").checked'), true);
    assert.equal(await get('$("screenshare-permission-allow").disabled'), true);
    assert.match(await get('$("screenshare-permission-status").textContent'), /blocks/);
    permission = { configured: false, allowed: false, available: true, reason: null };
    await reload();
    assert.equal(
      await evaluate(
        `Boolean(document.querySelector('#screenshare-experimental-label [data-dpe-icon="experimental"]'))`,
      ),
      true,
    );
    assert.equal(
      await evaluate(
        `Boolean(document.querySelector('#screenshare-permission-allow-label [data-dpe-icon="warning"]'))`,
      ),
      true,
    );
    const codecIcon = await evaluate(`(() => {
      const icon = document.querySelector('#screensharing-panel .diagnostic-row h3 .risk-icon');
      const style = getComputedStyle(icon);
      const heading = getComputedStyle(icon.parentElement);
      const reference = getComputedStyle(document.querySelector('#screenshare-experimental-label .risk-icon'));
      const probe = document.createElement('span');
      probe.style.color = 'var(--dpe-danger)'; icon.append(probe);
      const danger = getComputedStyle(probe).color; probe.remove();
      return {color: style.color, danger, margin: style.marginInlineEnd,
        gap: heading.columnGap, expectedGap: reference.marginInlineEnd, align: heading.alignItems};
    })()`);
    assert.equal(codecIcon.color, codecIcon.danger);
    assert.equal(codecIcon.margin, "0px");
    assert.equal(codecIcon.gap, codecIcon.expectedGap);
    assert.equal(codecIcon.align, "center");
    assert.match(
      await evaluate(`document.querySelector('.dpe-capture-launch-help code').textContent`),
      /^'\/.*\/\.local\/bin\/dpe-game-capture' -- %command%$/,
    );
    await click("screenshare-codecs");
    assert.equal(await get('$("screenshare-codecs-VP9")'), null);
    for (const codec of ["AV1", "H265", "H264"]) {
      await click(`screenshare-codecs-${codec}`);
      await until(() => values.openasar.screensharing.excludedCodecs?.includes(codec));
      await until(() => get(`!$("screenshare-codecs-${codec}").disabled`));
    }
    assert.equal(await get('$("screenshare-codecs-VP8").disabled'), true);
    assert.equal(await get('$("screenshare-codecs-menu").matches(":popover-open")'), true);
    await capture("screensharing-codec-exclusions");
    for (const codec of ["AV1", "H265", "H264"]) {
      await click(`screenshare-codecs-${codec}`);
      await until(() => !values.openasar.screensharing.excludedCodecs?.includes(codec));
      await until(() => get(`!$("screenshare-codecs-${codec}").disabled`));
    }
    await evaluate('document.getElementById("screenshare-codecs-menu").hidePopover()');
    await peer.webContents.executeJavaScript(
      `dispatchEvent(new CustomEvent('dpe-codec-exclusion-status',{detail:'no-allowed-encoder'}))`,
    );
    await reload();
    await until(() => get('!$("screenshare-codec-error").hidden'));
    await peer.webContents.executeJavaScript(
      `dispatchEvent(new CustomEvent('dpe-codec-exclusion-status',{detail:null}))`,
    );
    await reload();
    await until(() => get('$("screenshare-codec-error").hidden'));

    window.focus();
    window.webContents.focus();
    await evaluate('document.getElementById("screenshare-preserve-bitrate").focus()');
    for (const type of ["keyDown", "keyUp"])
      window.webContents.sendInputEvent({ type, keyCode: "Space" });
    await until(() => values.openasar.screensharing.preserveViewerBitrate === true);
    await until(() => get('!$("screenshare-preserve-bitrate").disabled'));
    await reload();
    assert.equal(await get('$("screenshare-preserve-bitrate").checked'), true);
    await evaluate(
      'document.getElementById("screenshare-preserve-bitrate").scrollIntoView({block:"center"})',
    );
    await capture("screensharing-bitrate-preserved");
    const bitrateSave = global.settings.save;
    global.settings.save = () => {
      throw new Error("Screensharing bitrate save failed");
    };
    await click("screenshare-preserve-bitrate");
    await until(() => get('!$("screenshare-save-error").hidden'));
    assert.equal(await get('$("screenshare-preserve-bitrate").checked'), true);
    assert.equal(values.openasar.screensharing.preserveViewerBitrate, true);
    global.settings.save = bitrateSave;
    await click("screenshare-preserve-bitrate");
    await until(() => values.openasar.screensharing.preserveViewerBitrate === false);
    await until(() => get('!$("screenshare-preserve-bitrate").disabled'));
    await reload();
    assert.equal(await get('$("screenshare-preserve-bitrate").checked'), false);

    await click("screenshare-pacing");
    await until(() => values.openasar.screensharing.capturePacing === false);
    await until(() => get('!$("screenshare-pacing").disabled'));
    await reload();
    assert.equal(await get('$("screenshare-experimental").checked'), true);
    assert.equal(await get('$("screenshare-pacing").checked'), false);
    const save = global.settings.save;
    global.settings.save = () => {
      throw new Error("Screensharing fixture save failed");
    };
    await click("screenshare-pacing");
    await until(() => get('!$("screenshare-save-error").hidden'));
    assert.equal(await get('$("screenshare-pacing").checked'), false);
    assert.equal(values.openasar.screensharing.capturePacing, false);
    global.settings.save = save;
    peer.webContents.send("dpe-fixture-status", {
      active: true,
      actualCodec: "AV1",
      requestedCodec: "AV1X",
      encodedFrames: 0,
      inputFps: 60,
      outputFps: 0,
      bitrate: 0,
      hardware: "unknown",
    });
    await reload();
    await until(() => get('$("screenshare-encoding-pending")'));
    assert.match(await get('$("screenshare-details").textContent'), /Not started/);
    assert.match(
      await get('$("screenshare-encoding-pending").textContent'),
      /With no viewers, this is expected/,
    );
    const activeState = {
      active: true,
      sourceType: "window",
      captureMethod: "portal",
      requestedFps: 60,
      inputFps: 144,
      outputFps: 59.9,
      actualCodec: "AV1",
      requestedCodec: "AV1",
      encoder: "VaapiVideoEncoder",
      hardware: "hardware",
      bitrate: 7900000,
      encodedFrames: 200,
      dimensions: { width: 1920, height: 1080 },
      fallbackCount: 0,
      captureCounters: {
        frames: 1000,
        dropped: 20,
        paced: 600,
        mode: "latest",
        targetFps: 60,
        upstreamFps: 60000 / 1001,
        upstreamAppliedFps: 60,
      },
      adaptation: {
        cpuLimitedFrameRate: true,
        cpuLimitedResolution: null,
        bwLimitedFrameRate: false,
        bwLimitedResolution: false,
        cpuChanges: 0,
        qualityChanges: 1,
      },
      encoding: {
        configuredMaxBitrate: 6000000,
        estimatedConnectionBandwidth: 10000000,
        averageQp: 38.25,
        keyFramesEncoded: 3,
        rateLimiterDiscards: 42,
        encoderQueueDiscards: 0,
        congestionWindowDiscards: null,
      },
    };
    peer.webContents.send("dpe-fixture-status", activeState, 350);
    await click("t_settings");
    await click("t_screensharing");
    await until(() =>
      get('$("screenshare-current-title").parentElement.getAttribute("aria-busy") === "true"'),
    );
    await until(() => get('$("screenshare-details")?.textContent.includes("1920 × 1080")'));
    assert.equal(await get('$("screenshare-encoding-pending")'), null);
    assert.match(await get('$("screenshare-details").textContent'), /1920 × 1080/);
    assert.match(await get('$("screenshare-details").textContent'), /7.9 Mbps/);
    assert.equal(await get('$("screenshare-counters").open'), false);
    assert.equal(await get('$("screenshare-adaptation").open'), false);
    assert.equal(await get('$("screenshare-adaptation-label").textContent'), "Encoding details");
    assert.equal(await get('$("screenshare-counters-label").textContent'), "Capture details");
    await evaluate('document.querySelector("#screenshare-adaptation summary").click()');
    const adaptation =
      await evaluate(`Object.fromEntries(Array.from(document.querySelectorAll('#screenshare-adaptation dl > div'))
      .map(row => [row.querySelector('dt').textContent, row.querySelector('dd').textContent]))`);
    assert.deepEqual(adaptation, {
      "Configured video bitrate limit": "6 Mbps",
      "Connection bandwidth estimate": "10 Mbps",
      "Reported quantizer (codec-specific)": "38.3",
      "Key frames encoded": "3",
      "CPU limited frame rate": "Yes",
      "CPU limited resolution": "Not reported",
      "Quality-limited frame rate": "No",
      "Quality-limited resolution": "No",
      "CPU adjustments": "0",
      "Quality adjustments": "1",
      "Rate limiter frame discards": "42",
      "Encoder queue frame discards": "0",
      "Congestion window frame discards": "Not reported",
    });
    assert.match(
      await get('$("screenshare-adaptation").textContent'),
      /Quality limits can reflect encoder quality or bandwidth/,
    );
    await evaluate(
      'document.querySelector("#screenshare-adaptation summary").scrollIntoView({block:"center"})',
    );
    await capture("screensharing-adaptation");
    await evaluate('document.querySelector("#screenshare-adaptation summary").click()');
    await evaluate('document.querySelector("#screenshare-counters summary").click()');
    const producerLimit = `Array.from(document.querySelectorAll('#screenshare-counters dl > div'))
      .find(row => row.querySelector('dt').textContent === 'Producer frame-rate limit')
      ?.querySelector('dd').textContent`;
    assert.equal(await evaluate(producerLimit), "59.9 FPS");
    await evaluate(
      'document.querySelector("#screenshare-counters summary").scrollIntoView({block:"center"})',
    );
    await capture("screensharing-producer-limit");
    await evaluate('document.querySelector("#screenshare-counters summary").click()');
    await evaluate('document.getElementById("screenshare-experimental").focus()');
    peer.webContents.send("dpe-fixture-status", {
      ...activeState,
      outputFps: 58.5,
      bitrate: 10000,
      encoding: { ...activeState.encoding, estimatedConnectionBandwidth: null },
      captureCounters: { ...activeState.captureCounters, upstreamFps: null },
    });
    await until(() => get('$("screenshare-details").textContent.includes("58.5 FPS")'));
    assert.match(await get('$("screenshare-details").textContent'), /10 kbps/);
    assert.equal(
      await evaluate(`Array.from(document.querySelectorAll('#screenshare-adaptation dl > div'))
        .find(row => row.querySelector('dt').textContent === 'Connection bandwidth estimate')
        ?.querySelector('dd').textContent`),
      "Not reported",
    );
    assert.equal(await evaluate(producerLimit), "Not reported");
    assert.equal(await evaluate("document.activeElement.id"), "screenshare-experimental");
    await capture("screensharing-active-dark");
    assert.equal(
      await get('$("capture-source-title")'),
      null,
      "source selection belongs to Discord's picker",
    );
    peer.webContents.send("dpe-fixture-status", {
      ...activeState,
      captureMethod: "vkcapture",
      captureHandoff: "relay",
    });
    await until(() =>
      get('$("screenshare-details").textContent.includes("Direct buffer sharing")'),
    );
    peer.webContents.send("dpe-fixture-status", {
      ...activeState,
      captureMethod: "kms",
      sourceType: "screen",
      captureHandoff: "copy",
    });
    await until(() => get('$("screenshare-details").textContent.includes("KMS monitor capture")'));
    assert.match(await get('$("screenshare-details").textContent'), /GPU snapshot/);
    await capture("screensharing-kms-active");
    await evaluate('document.body.dataset.dpeTheme = "light"');
    await capture("screensharing-active-light");
    await evaluate(
      'document.body.dataset.dpeTheme = "dark"; document.body.dataset.reducedMotion = "true"',
    );
    window.setMinimumSize(1, 1);
    window.setSize(720, 620);
    peer.webContents.setZoomFactor(1.5);
    window.webContents.setZoomFactor(1.5);
    await evaluate(`document.getElementById('screenshare-pacing-label').textContent =
      'Match capture to the currently selected screenshare frame rate with a longer translated label';
      document.getElementById('screenshare-pacing').scrollIntoView({block:'center'});
      new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
    assert.equal(window.webContents.getZoomFactor(), 1.5);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true);
    assert.equal(
      await evaluate(`Array.from(document.querySelectorAll('.settings-sidebar [role=tab]'))
        .every(tab => tab.scrollWidth <= tab.clientWidth + 1)`),
      true,
    );
    await capture("screensharing-narrow-zoom");
    await evaluate(`document.getElementById('screenshare-preserve-bitrate-label').textContent =
      'Preserve the screenshare bitrate allowance in smaller viewer windows with a longer translated label';
      document.getElementById('screenshare-preserve-bitrate').scrollIntoView({block:'center'});
      new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true);
    assert.equal(
      await evaluate(`(() => {
      const input=document.getElementById('screenshare-preserve-bitrate');
      const row=input.closest('.setting-row').getBoundingClientRect();
      const label=document.getElementById('screenshare-preserve-bitrate-label').getBoundingClientRect();
      const control=input.parentElement.getBoundingClientRect();
      return label.right <= control.left + 1 && control.right <= row.right + 1;
    })()`),
      true,
    );
    await capture("screensharing-bitrate-narrow-zoom");
    await evaluate('document.documentElement.dir = "rtl"');
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true);
    await evaluate('document.documentElement.dir = "ltr"');
    window.webContents.debugger.attach("1.3");
    await window.webContents.debugger.sendCommand("Emulation.setDeviceMetricsOverride", {
      width: 320,
      height: 900,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await evaluate(
      "new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))",
    );
    await evaluate('document.querySelector("#screenshare-adaptation summary").click()');
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), true);
    assert.equal(
      await evaluate(`Array.from(document.querySelectorAll('#screenshare-adaptation dt, #screenshare-adaptation dd'))
        .every(element => element.scrollWidth <= element.clientWidth + 1)`),
      true,
    );
    const tree = await window.webContents.debugger.sendCommand("Accessibility.getFullAXTree");
    for (const node of tree.nodes.filter((node) =>
      ["tab", "button", "switch", "combobox", "DisclosureTriangle"].includes(node.role?.value),
    ))
      assert.ok(node.name?.value, `Unnamed ${node.role.value}`);
    await window.webContents.debugger.sendCommand("Emulation.clearDeviceMetricsOverride");
    window.webContents.debugger.detach();
    peer.webContents.send("dpe-fixture-methods", ["auto", "portal"]);
    peer.webContents.send("dpe-fixture-status", {
      active: false,
      methodReasons: { kms: "permission-required" },
    });
    await reload();
    await until(() => get('$("screenshare-current-state").textContent.includes("aren’t sharing")'));
    peer.webContents.send("dpe-fixture-status", {
      active: false,
      watching: [
        {
          codec: "AV1X",
          decoder: "electron",
          hardware: "hardware",
          width: 1920,
          height: 1072,
          fps: 60,
        },
      ],
    });
    await reload();
    await until(() =>
      get('$("screenshare-incoming-title")?.parentElement.textContent.includes("Hardware")'),
    );
    assert.match(
      await get('$("screenshare-incoming-title").parentElement.textContent'),
      /1920 × 1072/,
    );
    await evaluate(
      'document.getElementById("screenshare-incoming-title").scrollIntoView({block:"start"})',
    );
    await capture("screenshare-incoming-hardware");
    peer.webContents.send("dpe-fixture-status", {
      active: false,
      watching: [
        {
          codec: "VP8",
          decoder: "libvpx",
          hardware: "software",
          width: 1280,
          height: 720,
          fps: 30,
        },
      ],
    });
    await reload();
    await until(() =>
      get('$("screenshare-incoming-title")?.parentElement.textContent.includes("Software")'),
    );
    peer.webContents.send("dpe-fixture-status", { active: false });
    await reload();
    assert.equal(await get('$("screenshare-incoming-title")'), null);

    assert.equal(await get('$("screenshare-details")'), null);
    await click("screenshare-experimental");
    await until(() => values.openasar.screensharing.experimentalCapture === false);
    assert.equal(await get('$("screenshare-permission-allow").disabled'), true);
    assert.equal(
      values.openasar.screensharing.capturePacing,
      false,
      "Vanilla pacing preference is independent of experimental capture",
    );
    assert.equal(permissionCalls, 2);
    permission = {
      configured: true,
      allowed: false,
      needsUpdate: true,
      canRevoke: true,
      available: true,
      reason: "permission-update-required",
    };
    await reload();
    await click("screenshare-experimental");
    await until(() => permissionCalls === 3 && permission.allowed);
    assert.equal(values.openasar.screensharing.experimentalCapture, true);
    await until(() => get('!$("screenshare-experimental").disabled'));
    const removePermission = permissions.revoke;
    let finishRemoval;
    permissions.revoke = () =>
      new Promise((resolve) => {
        finishRemoval = resolve;
      });
    await click("screenshare-experimental");
    await until(() => Boolean(finishRemoval));
    assert.equal(await evaluate(`document.querySelector('.dpe-capture-launch-help').inert`), true);
    assert.equal(
      await evaluate(
        `document.querySelector('.dpe-capture-launch-help').getAttribute('aria-disabled')`,
      ),
      "true",
    );
    assert.equal(
      await evaluate(`document.querySelector('.dpe-capture-launch-help .command-copy').disabled`),
      true,
    );
    assert.equal(
      await evaluate(
        `Number(getComputedStyle(document.querySelector('.dpe-capture-launch-help')).opacity) < 1`,
      ),
      true,
    );
    finishRemoval({ ...permission, reason: "authorization-cancelled" });
    await until(() => get('!$("screenshare-experimental").disabled'));
    assert.equal(await get('$("screenshare-experimental").checked'), true);
    assert.equal(await evaluate(`document.querySelector('.dpe-capture-launch-help').inert`), false);
    permissions.revoke = removePermission;
    await click("screenshare-experimental");
    await until(() => values.openasar.screensharing.experimentalCapture === false);
    assert.equal(permission.configured, false);
    assert.equal(revokeCalls, 2);
  } finally {
    Object.assign(permissions, originalPermissions);
    presentation.trusted = trusted;
    peer.destroy();
  }
};
