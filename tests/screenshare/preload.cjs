const { ipcRenderer } = require("electron");
const dgram = require("node:dgram");
const fs = require("node:fs");
const path = require("node:path");
const { drawMarker, FrameMeasurements, distribution } = require("./frame-markers.cjs");
const { Bottleneck } = require("./network-relay.cjs");
const { fullPictureEdges } = require("./frame-edges.cjs");
const { av1Padding } = require("./frame-padding.cjs");
const root = process.env.DPE_SCREENSHARE_OUTPUT;
const config = JSON.parse(fs.readFileSync(path.join(root, "config.json"), "utf8"));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const stats = (connection) =>
  new Promise((resolve, reject) =>
    connection.getFilteredStats(-1, (value) => {
      const parsed = JSON.parse(value);
      if (parsed.__fixtureError) reject(Error(parsed.__fixtureError));
      else resolve(parsed);
    }),
  );
window.addEventListener(
  "DOMContentLoaded",
  () => {
    run().catch((error) => ipcRenderer.send("report", { passed: false, error: error.stack }));
  },
  { once: true },
);

async function run() {
  let bottleneck = null;
  const canvas = document.querySelector("canvas");
  const context = canvas.getContext("2d");
  const measurements = new FrameMeasurements({
    sourceClock:
      config.backend === "pipewire" ? () => Number(process.hrtime.bigint() / 1000000n) : null,
    phase: () => bottleneck?.phase() ?? phase,
  });
  const drawDurations = [];
  const pictureEdges = { checked: 0, failed: 0 };
  const padding = { checked: 0, failed: 0, maxValue: 0, maxInterior: 0 };
  let paddingScope;
  const sourceIntervals = [];
  let phase = "initial";
  let previousDraw = null;
  let frame = 0;
  function draw() {
    const began = performance.now();
    if (previousDraw !== null) sourceIntervals.push(began - previousDraw);
    previousDraw = began;
    for (let y = 0; y < config.height; y += 40) {
      for (let x = 0; x < config.width; x += 40) {
        const shade = (x * 13 + y * 7 + frame * 11) % 256;
        context.fillStyle = `rgb(${shade},${(shade + 70) % 256},${(shade + 130) % 256})`;
        context.fillRect(x, y, 40, 40);
      }
    }
    drawMarker(context, config.width, config.height, frame);
    measurements.submitted(frame, bottleneck?.phase() ?? phase);
    drawDurations.push(performance.now() - began);
    frame++;
  }
  if (config.backend !== "pipewire") draw();
  const animation =
    config.motion === "moving" && config.backend !== "pipewire"
      ? setInterval(draw, 1000 / (config.source_fps || config.fps))
      : null;
  const relay = dgram.createSocket("udp4");
  const peers = new Set();
  const roles = new Map();
  bottleneck =
    config.network_profile === "bottleneck"
      ? new Bottleneck({
          durationMs: config.seconds * 1000,
          send: (message, port) => relay.send(message, port, "127.0.0.1"),
          // Keep Blink timer calls bound to this preload's Window. Passing the
          // bare timer and invoking it as a Bottleneck method is an illegal call.
          now: () => performance.now(),
          schedule: (callback, ms) => setTimeout(callback, ms),
          cancel: (timer) => clearTimeout(timer),
        })
      : null;
  let reverseFeedbackPackets = 0;
  let reverseTwccPackets = 0;
  let mediaPackets = 0;
  let feedbackPackets = 0;
  let discardedPackets = 0;
  let rendered = 0;
  const packetRecords = [];
  let recordedBytes = 0;
  relay.on("message", (message, remote) => {
    // Discord's UDP address discovery. Both peers are private synthetic clients.
    if (message.length === 74 && message.readUInt16BE(0) === 1) {
      peers.add(remote.port);
      // Discovery contains the synthetic audio SSRC; arrival order is not a role.
      const ssrc = message.readUInt32BE(4);
      if (ssrc === 42 || ssrc === 52) roles.set(remote.port, ssrc === 42 ? "sender" : "receiver");
      const response = Buffer.alloc(74);
      response.writeUInt16BE(2, 0);
      response.writeUInt16BE(70, 2);
      message.copy(response, 4, 4, 8);
      response.write("127.0.0.1", 8);
      response.writeUInt16BE(remote.port, 72);
      relay.send(response, remote.port, "127.0.0.1");
      return;
    }
    if (message.length < 12 || message[0] >> 6 !== 2) return;
    const payloadType = message[1] & 127;
    if ([101, 102, 103, 104].includes(payloadType)) {
      mediaPackets++;
      if (
        config.record_packets &&
        payloadType === 101 &&
        message.readUInt32BE(8) === 44 &&
        recordedBytes + message.length + 4 <= 4 * 1024 * 1024
      ) {
        const size = Buffer.alloc(4);
        size.writeUInt32LE(message.length);
        packetRecords.push(size, Buffer.from(message));
        recordedBytes += message.length + 4;
      }
      if (config.drop_video) {
        discardedPackets++;
        return;
      }
    }
    if (message[1] === 205 || message[1] === 206) feedbackPackets++;
    if (roles.get(remote.port) === "receiver" && [205, 206].includes(message[1])) {
      reverseFeedbackPackets++;
      // Only an outer TWCC header is counted here. Native logs remain the
      // authority for feedback nested later in compound encrypted RTCP.
      if (message[1] === 205 && (message[0] & 31) === 15) reverseTwccPackets++;
    }
    for (const port of peers) {
      if (port === remote.port) continue;
      if (
        bottleneck &&
        roles.get(remote.port) === "sender" &&
        roles.get(port) === "receiver" &&
        [101, 102, 103, 104].includes(payloadType)
      ) {
        bottleneck.enqueue(message, port);
      } else relay.send(message, port, "127.0.0.1");
    }
  });
  await new Promise((resolve) => relay.bind(0, "127.0.0.1", resolve));
  const voice = require(path.join(root, "voice/discord_voice.node"));
  const pickerEvents = [];
  const capturePicker = config.capture_picker
    ? require("./capture-picker-adapter.cjs").load(
        path.join(root, "voice"),
        path.join(root, "libdpe-camera-probe.so"),
        (event) => {
          pickerEvents.push(event);
          if (event.event === "request") {
            const node = Number(
              fs
                .readFileSync(path.join(root, "source.log"), "utf8")
                .split("\n")
                .find((line) => /^\d+$/.test(line)),
            );
            capturePicker.finish(
              event.requestId,
              "selected",
              config.external_pipewire_node
                ? "/run/dpe-external-pipewire"
                : path.join(process.env.XDG_RUNTIME_DIR, process.env.PIPEWIRE_REMOTE),
              node,
            );
          }
        },
      )
    : null;
  const captureSync = config.capture_sync
    ? require("./capture-sync-adapter.cjs").load(
        path.join(root, "voice"),
        path.join(root, "libdpe-camera-probe.so"),
      )
    : null;
  const captureScale = config.capture_scale
    ? require("./capture-scale-adapter.cjs").load(
        path.join(root, "voice"),
        path.join(root, "libdpe-camera-probe.so"),
      )
    : null;
  const encoderGuard = config.encoder_guard
    ? require("./encoder-adapter.cjs").load(
        path.join(root, "voice"),
        path.join(root, "libdpe-camera-probe.so"),
      )
    : null;
  const captureAdapter =
    config.capture_mode !== "off"
      ? require("./capture-adapter.cjs").load(
          path.join(root, "voice"),
          path.join(root, "libdpe-camera-probe.so"),
          { mode: config.capture_mode },
        )
      : null;
  if (!config.transport_adapter) captureAdapter?.setRate(config.fps);
  voice.initialize({
    audioSubsystem: "standard",
    logLevel: 1,
    dataDirectory: root,
    logDirectory: root,
    maxLogBytes: 2000000,
    useFakeAudioCapture: true,
    useFakeVideoCapture: true,
    offloadAdmControls: true,
    asyncVideoInputDeviceInit: true,
  });
  // GPU discovery is asynchronous. Constructing a connection before it finishes
  // freezes a software-only encoder factory into that connection.
  const codecSurvey = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Codec survey timed out")), 10000);
    voice.getCodecSurvey((value) => {
      clearTimeout(timer);
      try {
        resolve(JSON.parse(value));
      } catch (error) {
        reject(error);
      }
    });
  });
  fs.writeFileSync(path.join(root, "codec-survey.json"), JSON.stringify(codecSurvey, null, 2));
  const codecCapabilities = await new Promise((resolve) =>
    voice.getCodecCapabilities((value) => resolve(JSON.parse(value))),
  );
  const encodeWidth = config.encode_width ?? config.width;
  const encodeHeight = config.encode_height ?? config.height;
  const sourceQuality = config.quality_mode === "source";
  const productionProfile = config.transport_profile === "production";
  const targetBitrate = productionProfile ? 600000 : config.bitrate * 0.8;
  const minimumBitrate = productionProfile ? 500000 : 200000;
  const h264Profile = productionProfile
    ? "4d0033"
    : encodeWidth * encodeHeight > 1920 * 1080
      ? "42e034"
      : "42e02a";
  const streams = [
    {
      type: "video",
      rid: "0",
      ssrc: 44,
      rtxSsrc: 45,
      active: true,
      quality: 100,
      minBitrate: minimumBitrate,
      targetBitrate,
      maxBitrate: config.bitrate,
      ...(sourceQuality
        ? { maxResolution: { type: "source", width: 0, height: 0 } }
        : { maxPixelCount: encodeWidth * encodeHeight }),
      maxFrameRate: config.fps,
    },
  ];
  const endpoint = { address: "127.0.0.1", port: relay.address().port };
  const senderNative = new voice.VoiceConnection(
    "1000",
    { ...endpoint, ssrc: 42, streamParameters: streams },
    () => {},
  );
  const transportModule = config.transport_adapter ? require("./transport-adapter.cjs") : null;
  const transportTracker = transportModule?.createTracker({
    capture: captureAdapter,
    encoder: encoderGuard,
    settings: { read: () => ({ capturePacing: true }) },
    ...(config.capture_transport === "direct-gpu"
      ? {
          backends: {
            snapshot: () => ({
              method: "direct-gpu",
              transport: "direct-gpu",
              sourceType: "screen",
              handoff: "direct-gpu",
            }),
            directSnapshot: () => capturePicker?.directStats(),
            setRate() {},
          },
        }
      : {}),
    portal: () => config.backend === "pipewire",
    x11: config.backend === "x11",
  });
  const sender = transportModule
    ? transportModule.wrap(senderNative, { tracker: transportTracker })
    : senderNative;
  const receiverNative = config.split_peers
    ? await require("./receiver-client.cjs").create(endpoint)
    : new voice.VoiceConnection("2000", { ...endpoint, ssrc: 52, streamParameters: [] }, () => {});
  const receiver = transportModule
    ? transportModule.wrap(receiverNative, { tracker: transportTracker })
    : receiverNative;
  // Fixed test key, confined to this fixture's private network namespace.
  const encryptionSettings = {
    mode: "aead_xchacha20_poly1305_rtpsize",
    secretKey: Array(32).fill(1),
  };
  const codec = {
    name: { "h264-vaapi": "H264", "h265-vaapi": "H265", "av1-vaapi": "AV1X", vp8: "VP8" }[
      config.encoder
    ],
    type: 101,
    rtxType: 102,
    params:
      config.encoder === "h264-vaapi"
        ? {
            "profile-level-id": h264Profile,
            "packetization-mode": "1",
            "level-asymmetry-allowed": "1",
          }
        : config.encoder === "h265-vaapi"
          ? { "profile-id": "1", "tier-flag": "0", "level-id": "153", "tx-mode": "SRST" }
          : {},
  };
  const fallbackCodec = {
    name: "H264",
    type: 103,
    rtxType: 104,
    params: {
      "profile-level-id": h264Profile,
      "packetization-mode": "1",
      "level-asymmetry-allowed": "1",
    },
  };
  const codecFallbacks = [];
  const codecErrors = [];
  const negotiation = [{ codec: codec.name, payloadType: codec.type, reason: "initial" }];
  const options = {
    encryptionSettings,
    // Current Go Live uses the stream experiment set and a 100-second GOP.
    // A 3-second GOP injects keyframes absent from the actual sender.
    // Production mode emulates authenticated SFU receiver announcements, so it
    // can avoid forced sending and use Go Live's native Medium latency.
    keyframeInterval: productionProfile ? 100000 : 3000,
    alwaysSendVideo: !config.receiver_announcements,
    remoteSinkWantsPixelCount: sourceQuality ? 0 : encodeWidth * encodeHeight,
    remoteSinkWantsMaxFramerate: config.fps,
    videoEncoder: codec,
    encodingVideoWidth: sourceQuality ? 0 : encodeWidth,
    encodingVideoHeight: sourceQuality ? 0 : encodeHeight,
    encodingVideoFrameRate: config.fps,
    captureVideoFrameRate: config.fps,
    encodingVideoBitRate: targetBitrate,
    encodingVideoMinBitRate: minimumBitrate,
    encodingVideoMaxBitRate: config.bitrate,
    callBitRate: productionProfile ? 600000 : config.bitrate,
    callMinBitRate: productionProfile ? 16000 : 200000,
    callMaxBitRate: config.receiver_bitrate
      ? config.receiver_bitrate + 128000
      : productionProfile
        ? Math.max(10000000, config.bitrate + 1000000)
        : config.bitrate,
    ...(productionProfile
      ? {
          videoEncoderLatencyProfile: "default",
          videoEncoderExperiments: "unk,nvNewPresets,nvRelaxRc=250,vaapi,linux-vulkan",
        }
      : {}),
    streamParameters: streams,
  };
  if (sourceQuality)
    options.encodingVideoDegradationPreference =
      voice.DegradationPreference.MAINTAIN_FRAMERATE_AND_RESOLUTION ??
      voice.DegradationPreference.DISABLED;
  else
    options.encodingVideoDegradationPreference =
      config.degradation === "balanced"
        ? voice.DegradationPreference.BALANCED
        : voice.DegradationPreference.MAINTAIN_FRAMERATE;
  sender.setTransportOptions(options);
  receiver.setTransportOptions({
    encryptionSettings,
    videoDecoders: (config.fallback_h264 ? [codec, fallbackCodec] : [codec]).map((decoder) => ({
      ...decoder,
      params: {
        ...decoder.params,
        ...(productionProfile && decoder.name === "H264" ? { "profile-level-id": "42e034" } : {}),
        // The pinned Linux native factory uses this for H264, H265 and AV1.
        "hardware-h264": config.decoder === "electron" ? "1" : "0",
        // Current Discord enables these for incoming camera/screenshare video.
        "reset-on-errors": "1",
        "fallback-on-consecutive-errors": "1",
        ...(config.openh264 && decoder.name === "H264"
          ? {
              libopenh264: "1",
              "libopenh264-path": path.join(root, "voice/libopenh264.so"),
            }
          : {}),
      },
    })),
  });
  sender.setOnVideoEncoderFallbackCallback?.((failedCodec) => {
    codecFallbacks.push({ kind: "encoder", codec: failedCodec });
    if (!config.fallback_h264 || !["AV1", "AV1X"].includes(failedCodec) || negotiation.length > 1)
      return;
    // Model the server selecting an independently advertised common codec.
    // Both peers know H264 at payload103; AV1 at101 is never relabeled.
    const supported = codecCapabilities.find((entry) => entry.codec === "H264");
    if (!supported?.encode || !supported?.decode) return;
    negotiation.push({ codec: "H264", payloadType: 103, reason: "encoder-fallback" });
    sender.setTransportOptions({ ...options, videoEncoder: fallbackCodec });
  });
  receiver.setOnVideoDecoderFallbackCallback?.((failedCodec) => {
    codecFallbacks.push({ kind: "decoder", codec: failedCodec });
  });
  for (const [kind, connection] of [
    ["sender", sender],
    ["receiver", receiver],
  ]) {
    connection.setVideoCodecErrorCallback?.((error) => codecErrors.push({ kind, error }));
  }
  voice.setImageDataAllocator((width, height) => new window.ImageData(width, height));
  if (!config.split_peers)
    receiver.setOnVideoCallback((_, __, stream) => {
      voice.setVideoOutputSink(
        stream,
        (imageData) => {
          rendered++;
          if (paddingScope === undefined && config.encoder === "av1-vaapi") {
            const snapshot = encoderGuard?.snapshot();
            paddingScope =
              snapshot?.av1Alignment?.hardwareScope === "amd-vcn4"
                ? "amd-vcn4"
                : snapshot?.hardwareScope || "unknown";
          }
          const layout =
            config.encoder === "av1-vaapi"
              ? av1Padding(
                  imageData,
                  config.encode_width || config.width,
                  config.encode_height || config.height,
                  paddingScope,
                )
              : null;
          if (layout) {
            padding.checked++;
            padding.maxValue = Math.max(padding.maxValue, layout.maxValue);
            padding.maxInterior = Math.max(padding.maxInterior, layout.maxInterior);
            if (!layout.valid) padding.failed++;
          }
          const sample = layout
            ? {
                width: imageData.width,
                height: imageData.height,
                data: imageData.data,
                contentRect: layout.rect,
              }
            : imageData;
          measurements.received(sample);
          if (config.content_pattern === "framing") {
            pictureEdges.checked++;
            if (!fullPictureEdges(sample)) pictureEdges.failed++;
          }
          voice.signalVideoOutputSinkReady(stream);
        },
        true,
      );
      voice.signalVideoOutputSinkReady(stream);
    });
  receiver.mergeUsers([
    { id: "1000", audioSsrc: 42, videoSsrc: 44, rtxSsrc: 45, mute: false, volume: 0 },
  ]);
  sender.mergeUsers([{ id: "2000", audioSsrc: 52, mute: true, volume: 0 }]);
  receiver.setDisableLocalVideo("1000", false);
  for (const connection of [sender, receiver]) {
    // Explicit protocol-0 negotiation for synthetic media, without a DAVE/MLS
    // server. Otherwise native emits placeholder payloads instead of video.
    // This is never applied to the installed client or an account connection.
    await new Promise((resolve) => connection.prepareSecureFramesTransition(0, 0, resolve));
    connection.executeSecureFramesTransition(0);
    connection.setSelfMute(true);
  }
  if (config.baseline_seconds) {
    fs.writeFileSync(path.join(root, "baseline-started"), "ready\n");
    await wait(config.baseline_seconds * 1000);
  }
  const capture = [];
  sender.setDesktopSourceStatusCallback((value) => capture.push(value));
  if (config.backend === "pipewire") {
    voice.setNativeDesktopVideoSourcePickerActive(true);
    const pick = () =>
      new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error("Private portal selection timed out")),
          10000,
        );
        const finish = (error) => {
          clearTimeout(timer);
          if (error) reject(error);
          else resolve();
        };
        voice.setNativeScreenSharePickerCallbacks(
          () => finish(),
          () => finish(new Error("Private portal cancelled")),
          (error) => finish(new Error(String(error))),
        );
        voice.presentNativeScreenSharePicker(config.source);
      });
    await pick();
    if (capturePicker) {
      const unconsumed = pickerEvents.find((event) => event.event === "request");
      if (!unconsumed || !capturePicker.cancel(unconsumed.requestId))
        throw new Error("Unconsumed native selection could not be cancelled.");
      await wait(20);
      if (
        pickerEvents.some(
          (event) => event.event === "started" && event.requestId === unconsumed.requestId,
        )
      )
        throw new Error("Cancelled native selection unexpectedly started.");
      await pick();
    }
  }
  sender.setDesktopSourceWithOptions({
    sourceId: await ipcRenderer.invoke("source"),
    type: config.backend === "pipewire" ? "screen" : config.source,
  });
  sender.setTransportOptions(options);
  sender.setVideoBroadcast(true);
  let receiverAnnouncementIndex = 0;
  const announceReceiver = () => {
    const senderPort = [...roles].find(([, role]) => role === "sender")?.[0];
    const packet = config.receiver_announcements?.[receiverAnnouncementIndex];
    if (senderPort && packet) {
      relay.send(Buffer.from(packet, "base64"), senderPort, "127.0.0.1");
      receiverAnnouncementIndex++;
    }
  };
  announceReceiver();
  const receiverAnnouncementTimer = config.receiver_announcements
    ? setInterval(announceReceiver, 1000)
    : null;
  fs.writeFileSync(path.join(root, "capture-started"), "ready\n");
  if (config.pressure !== "none" && config.pressure)
    fs.writeFileSync(path.join(root, "pressure-phase"), "initial");
  if (config.stall_ms && config.stall_target === "capture") {
    const testHelper = { exports: {} };
    process.dlopen(testHelper, path.join(root, "libdpe-camera-probe.so"));
    setTimeout(() => testHelper.exports.testCaptureDelay(config.stall_ms), 600);
  }
  const samples = [];
  const transitions = [];
  const started = performance.now();
  // Encoder metadata is initially unknown. A repeated ordinary transport
  // update must preserve negotiated limits once the hardware is identified.
  const reconfigureTimer = config.reconfigure_after
    ? setTimeout(() => {
        sender.setTransportOptions({ ...options });
        transitions.push({ phase: "reconfigure", elapsedMs: performance.now() - started });
      }, config.reconfigure_after * 1000)
    : null;
  bottleneck?.start();
  for (let second = 0; second < config.seconds; second++) {
    if (config.scenario !== "steady") {
      const next =
        second < Math.floor(config.seconds / 3)
          ? "initial"
          : second < Math.floor((2 * config.seconds) / 3)
            ? "constrained"
            : "recovery";
      if (next !== phase) {
        phase = next;
        const constrained = phase === "constrained";
        if (config.scenario === "pressure") {
          fs.writeFileSync(path.join(root, "pressure-phase"), phase);
          transitions.push({ phase, elapsedMs: performance.now() - started });
        } else if (config.scenario === "rate-step") {
          const max = constrained ? Math.max(200000, config.bitrate / 4) : config.bitrate;
          sender.setTransportOptions({
            encodingVideoBitRate: Math.max(200000, max * 0.8),
            encodingVideoMaxBitRate: max,
            callBitRate: max,
            callMaxBitRate: max,
            streamParameters: streams.map((stream) => ({
              ...stream,
              maxBitrate: max,
              targetBitrate: Math.max(200000, max * 0.8),
            })),
          });
          transitions.push({
            phase,
            requestedMaxBitrate: max,
            elapsedMs: performance.now() - started,
          });
        } else {
          const captureFps = constrained ? Math.floor(config.fps / 3) : config.fps;
          sender.setTransportOptions({ captureVideoFrameRate: captureFps });
          if (!config.transport_adapter) captureAdapter?.setRate(captureFps);
          transitions.push({
            phase,
            requestedCaptureFps: captureFps,
            elapsedMs: performance.now() - started,
          });
        }
      }
    }
    await wait(1000);
    const sent = await stats(sender);
    if (config.split_peers) receiverNative.setPhase(bottleneck?.phase() ?? phase);
    const received = await stats(receiver);
    if (config.split_peers) rendered = await receiverNative.progress();
    samples.push({
      elapsedMs: performance.now() - started,
      phase: bottleneck?.phase() ?? phase,
      network: bottleneck?.snapshot() ?? null,
      sent: sent.outbound?.video,
      transport: sent.transport,
      received: received.inbound?.find((user) => user.video)?.video,
      rendered,
    });
  }
  const activeTransport = await transportTracker?.snapshot();
  clearInterval(receiverAnnouncementTimer);
  clearTimeout(reconfigureTimer);
  sender.setVideoBroadcast(false);
  sender.clearDesktopSource();
  if (config.backend === "pipewire") voice.releaseNativeDesktopVideoSourcePickerStream();
  let remoteOutput;
  if (config.split_peers) {
    remoteOutput = await receiverNative.output();
    rendered = remoteOutput.rendered;
    Object.assign(pictureEdges, remoteOutput.pictureEdges);
  }
  sender.destroy();
  await receiver.destroy();
  if (capturePicker) {
    const deadline = performance.now() + 2000;
    while (!pickerEvents.some((event) => event.event === "stopped") && performance.now() < deadline)
      await wait(20);
  }
  const idleTransport = await transportTracker?.snapshot();
  bottleneck?.close();
  relay.close();
  clearInterval(animation);
  if (config.record_packets)
    fs.writeFileSync(path.join(root, "packets.bin"), Buffer.concat(packetRecords));
  const last = samples.at(-1);
  const minimumFrames = Math.min(
    30,
    Math.max(5, Math.ceil((config.fps * (config.seconds - 2)) / 2)),
  );
  const passed =
    last.sent?.framesEncoded >= minimumFrames &&
    last.received?.framesDecoded >= minimumFrames &&
    rendered >= minimumFrames &&
    feedbackPackets > 0;
  ipcRenderer.send("report", {
    passed,
    scope: `synthetic ${config.capture_transport === "direct-gpu" ? "direct GPU" : config.backend} native loopback; pass verifies delivery/backend, not a performance improvement`,
    config,
    codecSurvey,
    codecCapabilities,
    codecFallbacks,
    codecErrors,
    transportAdapter: transportTracker ? { active: activeTransport, idle: idleTransport } : null,
    negotiation,
    encoderGuard: encoderGuard?.snapshot() ?? null,
    captureScale: captureScale?.snapshot() ?? null,
    captureSync: captureSync?.snapshot() ?? null,
    capturePicker: config.capture_picker ? pickerEvents : null,
    captureEventDelivery: capturePicker?.eventDelivery ?? null,
    captureDirect: capturePicker?.directStats() ?? null,
    mediaPackets,
    feedbackPackets,
    discardedPackets,
    network: bottleneck
      ? {
          ...bottleneck.snapshot(),
          senderIdentified: [...roles.values()].includes("sender"),
          receiverIdentified: [...roles.values()].includes("receiver"),
          reverseFeedbackPackets,
          reverseTwccPackets,
        }
      : null,
    capture,
    transitions,
    samples,
    measurements: remoteOutput?.measurements ?? measurements.report(),
    pictureEdges,
    av1Padding: padding,
    sourceTiming: {
      submittedFrames: frame,
      drawDurationMs: distribution(drawDurations),
      updateIntervalMs: distribution(sourceIntervals),
    },
    captureAdapter: captureAdapter?.snapshot() ?? null,
  });
}
