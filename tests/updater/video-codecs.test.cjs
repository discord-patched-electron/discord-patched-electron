const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const install = require("../../integration/video/codecs.cjs");

function fixture(options = {}) {
  const context = vm.createContext({ console, setTimeout, clearTimeout });
  vm.runInContext(
    `
      globalThis.config = {};
      globalThis.nativeCapabilities = [
        {type:'video',name:'AV1',encode:true,decode:true,payloadType:101,rtxPayloadType:102},
        {type:'video',name:'H264',encode:true,decode:true,payloadType:103,rtxPayloadType:104},
        {type:'video',name:'VP8',encode:true,decode:true,payloadType:105,rtxPayloadType:106}
      ];
      class Engine {
        connect(user, settings) {
          if (settings?.fail) throw new Error('upstream failed');
          this.called = [user,settings];
          const connection = {
            experimentFlags: new Set(['signal_av1_decode']), codecs: [], initialCodecs: [],
            context:'default',userId:'self',streamUserId:'self',
            lastOverrideCodecDenylist: '', videoEncoderFallbackPending:false,
            setExperimentFlag(flag, enabled) {
              enabled ? this.experimentFlags.add(flag) : this.experimentFlags.delete(flag);
            },
            getCodecOptions() {},
            ...config
          };
          // Mirror the source-verified native/renderer intersection. Capabilities
          // can be restricted by native discovery or an explicit client mod.
          connection.negotiate = (viewers) => {
            connection.codecs = nativeCapabilities.map(codec => ({...codec,
              encode: codec.encode && (codec.name!=='AV1' || connection.experimentFlags.has('signal_av1_encode'))
            }));
            return connection.codecs.find(codec => codec.encode && viewers.every(viewer => viewer.includes(codec.name)))?.name;
          };
          return connection;
        }
        getCodecSurvey() { this.surveys=(this.surveys||0)+1; return Promise.resolve('null'); }
        fetchAsyncResources(value) { return Promise.resolve(value); }
        createReplayConnection() {}
      }
      globalThis.Engine = Engine;
      globalThis.runtime = {watch(id, match, visit) {
        globalThis.watcher = {id,match,visit};
        visit({A:Engine});
      }};
    `,
    context,
  );
  const run = (source) => vm.runInContext(source, context);
  run(`(${install.toString()})(${JSON.stringify({ platform: "linux", ...options })},runtime)`);
  return { context, run };
}

test("AV1 becomes eligible only through the native and remote capability intersection", () => {
  const { run } = fixture();
  assert.equal(run("new Engine().connect().negotiate([['AV1','H264']])"), "AV1");
  assert.equal(run("new Engine().connect().negotiate([['AV1','H264'],['H264']])"), "H264");
  run("nativeCapabilities[0].encode=false");
  assert.equal(run("new Engine().connect().negotiate([['AV1','H264']])"), "H264");
  assert.equal(run("nativeCapabilities[0].encode"), false);
});

test("explicit codec denylist, fallback and already negotiated connections are preserved", () => {
  const { run } = fixture();
  for (const config of [
    "{lastOverrideCodecDenylist:'H265, av1 '}",
    "{lastOverrideCodecDenylist:'AV1X'}",
    "{videoEncoderFallbackPending:true}",
    "{codecs:[{name:'H264'}]}",
  ]) {
    run(`globalThis.config=${config}`);
    assert.equal(run("new Engine().connect().experimentFlags.has('signal_av1_encode')"), false);
  }
  run("globalThis.config={};globalThis.connection=new Engine().connect()");
  run("connection.setExperimentFlag('signal_av1_encode',false)");
  assert.equal(run("connection.negotiate([['AV1','H264']])"), "H264");
});

test("decoder capabilities and hardware preferences remain authoritative", () => {
  const { run } = fixture();
  run("nativeCapabilities[0].decode=false");
  run("globalThis.connection=new Engine().connect();connection.negotiate([['AV1']])");
  assert.equal(run("connection.codecs[0].decode"), false);
  assert.equal(run("connection.experimentFlags.has('signal_av1_hardware_decode')"), false);
});

test("screenshare exclusions reduce the SFU encode capabilities without changing payloads or decode", () => {
  const { run } = fixture({ excludedCodecs: ["AV1", "VP8"] });
  run("config={context:'stream'};globalThis.connection=new Engine().connect()");
  assert.equal(run("connection.negotiate([['AV1','H264','VP8']])"), "H264");
  assert.equal(run("connection.codecs[0].encode"), false);
  assert.equal(run("connection.codecs[0].decode"), true);
  assert.equal(run("connection.codecs[0].payloadType"), 101);
  assert.equal(run("connection.codecs[0].rtxPayloadType"), 102);
  assert.equal(run("nativeCapabilities[0].encode"), true);
  assert.equal(run("connection.codecs[2].encode"), false);
  run(
    "connection.initialCodecs=nativeCapabilities.map(c=>({...c}));connection.codecs=connection.initialCodecs.map(c=>({...c}))",
  );
  assert.equal(
    run("connection.codecs[0].encode"),
    false,
    "native denylist reset cannot restore excluded AV1",
  );
  assert.equal(run("connection.codecs[2].encode"), false, "VP8 exclusion is not silently ignored");
  run("connection.codecs=connection.codecs.map(c=>({...c,encode:false}))");
  assert.equal(
    run("connection.codecs.some(c=>c.encode)"),
    false,
    "fallback never invents an encoder",
  );
  assert.equal(
    run("globalThis[Symbol.for('dpe.videoCodecs')].exclusionError"),
    "no-allowed-encoder",
  );
});

test("exclusions leave camera calls and received screenshares unchanged", () => {
  const { run } = fixture({ excludedCodecs: ["AV1", "H264"] });
  for (const config of ["{context:'default'}", "{context:'stream',streamUserId:'someone-else'}"])
    assert.equal(
      run(`globalThis.config=${config};new Engine().connect().negotiate([['AV1','H264']])`),
      "AV1",
    );
});

test("unsupported remaining encoders report an actionable error without restoring an excluded codec", () => {
  const { run } = fixture({ excludedCodecs: ["AV1", "H264"] });
  run(
    "config={context:'stream'};nativeCapabilities[2].encode=false;globalThis.connection=new Engine().connect()",
  );
  assert.equal(run("connection.negotiate([['AV1','H264','VP8']])"), undefined);
  assert.equal(run("connection.codecs.some(c=>c.encode)"), false);
  assert.equal(run("connection.codecs.every(c=>c.decode)"), true);
  assert.equal(
    run("globalThis[Symbol.for('dpe.videoCodecs')].exclusionError"),
    "no-allowed-encoder",
  );
});

test("changed exclusions apply to the next share, preserving the current negotiation", () => {
  const { run } = fixture({ excludedCodecs: ["AV1"] });
  run("config={context:'stream'};globalThis.oldConnection=new Engine().connect()");
  run(`(${install.toString()})({platform:'linux',excludedCodecs:['H264','VP8']},runtime)`);
  assert.equal(run("oldConnection.negotiate([['AV1','H264','VP8']])"), "H264");
  assert.equal(run("new Engine().connect().negotiate([['AV1','H264','VP8']])"), "AV1");
  assert.equal(run("globalThis[Symbol.for('dpe.videoCodecs')].installed"), 1);
});

test("only structurally compatible native modules are installed, once", () => {
  const { run } = fixture();
  assert.equal(run("watcher.match('getCodecCapabilities lastOverrideCodecDenylist')"), false);
  assert.equal(
    run(
      "watcher.match('getCodecCapabilities lastOverrideCodecDenylist SIGNAL_AV1_ENCODE hardware-av1-decode')",
    ),
    true,
  );
  run("watcher.visit({A:Engine});watcher.visit({A:Engine,B:class extends Engine{}})");
  assert.equal(run("globalThis[Symbol.for('dpe.videoCodecs')].installed"), 1);
  assert.equal(fixture({ platform: "win32" }).run("typeof watcher"), "undefined");
});

test("native connection arguments, identity, errors and unknown future layouts survive", () => {
  const { run } = fixture();
  run("globalThis.engine=new Engine();globalThis.settings={};engine.connect('user',settings)");
  assert.equal(run("engine.called[0]"), "user");
  assert.equal(run("engine.called[1]===settings"), true);
  assert.throws(() => run("engine.connect('user',{fail:true})"), /upstream failed/);
  run("globalThis.config={experimentFlags:null}");
  assert.equal(run("new Engine().connect().experimentFlags"), null);
});

test("obsolete VP9 preferences are ignored without disabling other encoders", () => {
  const { run } = fixture({ excludedCodecs: ["VP9"] });
  assert.equal(
    run('JSON.stringify(globalThis[Symbol.for("dpe.videoCodecs")].excludedCodecs)'),
    "[]",
  );
});

test("all four exclusion names preserve native encode eligibility and decoder capabilities", () => {
  for (const excluded of ["AV1", "H265", "H264", "VP8"]) {
    const { run } = fixture({ excludedCodecs: [excluded] });
    run(`nativeCapabilities.push(
      {type:'video', name:'H265', encode:true, decode:true, payloadType:107},
      {type:'video', name:'VP9', encode:true, decode:true, payloadType:108});
      config={context:'stream'};globalThis.connection=new Engine().connect();
      connection.negotiate([['AV1','H265','H264','VP9','VP8']]);`);
    assert.equal(run(`connection.codecs.find(c => c.name === '${excluded}').encode`), false);
    assert.equal(run("connection.codecs.every(c => c.decode)"), true);
    assert.equal(run("connection.codecs.filter(c => c.encode).length"), 4);
    run(`connection.codecs=connection.codecs.map(c=>({...c,encode:false}));
      connection.initialCodecs=connection.codecs;`);
    assert.equal(run("connection.codecs.some(c => c.encode)"), false);
  }
});

test("existing async resource phase waits for GPU discovery before synchronous connect", async () => {
  const { context, run } = fixture();
  run(`globalThis.engine=new Engine();
    engine.getCodecSurvey=function(){this.surveys=(this.surveys||0)+1;return new Promise(resolve=>globalThis.completeSurvey=resolve)};
    globalThis.completed=false;
    globalThis.pending=engine.fetchAsyncResources('resources').then(value=>{completed=true;return value});`);
  await Promise.resolve();
  assert.equal(run("completed"), false);
  run("completeSurvey('null')");
  assert.equal(await context.pending, "resources");
  assert.equal(run("typeof engine.connect()"), "object");
  assert.equal(await run("engine.fetchAsyncResources('again')"), "again");
  assert.equal(run("engine.surveys"), 1);
});

test("missing survey fails safely without swallowing an upstream preparation error", async () => {
  const { run } = fixture();
  run(
    "globalThis.engine=new Engine();engine.getCodecSurvey=()=>Promise.reject(new Error('unavailable'))",
  );
  assert.equal(await run("engine.fetchAsyncResources('resources')"), "resources");
  await assert.rejects(
    run("engine.fetchAsyncResources(Promise.reject(new Error('upstream resources')))"),
    /upstream resources/,
  );
});

test("a late timed-out survey cannot overwrite the replacement discovery state", async () => {
  const { context, run } = fixture();
  context.setTimeout = (callback) => {
    context.expire = callback;
    return 1;
  };
  context.clearTimeout = () => {};
  run(`globalThis.engine = new Engine();
    engine.getCodecSurvey = () => new Promise(resolve => globalThis.oldSurvey = resolve);
    globalThis.pending = engine.fetchAsyncResources('first');`);
  await Promise.resolve();
  run("expire()");
  await context.pending;
  run(`engine.getCodecSurvey = () => new Promise(resolve => globalThis.newSurvey = resolve);
    globalThis.retry = engine.fetchAsyncResources('retry');`);
  await Promise.resolve();
  run("oldSurvey('null')");
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(run("globalThis[Symbol.for('dpe.videoCodecs')].discovery"), "pending");
  run("newSurvey('null')");
  assert.equal(await context.retry, "retry");
  assert.equal(run("globalThis[Symbol.for('dpe.videoCodecs')].discovery"), "ready");
});

test("GPU discovery has a two-second limit and retries after timeout", async () => {
  const { context, run } = fixture();
  context.setTimeout = (callback, delay) => {
    assert.equal(delay, 2000);
    context.expire = callback;
    return 1;
  };
  context.clearTimeout = () => {};
  run(
    "globalThis.engine=new Engine();engine.getCodecSurvey=()=>new Promise(()=>{});globalThis.pending=engine.fetchAsyncResources('resources')",
  );
  run("expire()");
  assert.equal(await context.pending, "resources");
  assert.equal(run("globalThis[Symbol.for('dpe.videoCodecs')].discovery"), "timeout");
  run("engine.getCodecSurvey=()=>Promise.resolve('null')");
  assert.equal(await run("engine.fetchAsyncResources('retry')"), "retry");
  assert.equal(run("globalThis[Symbol.for('dpe.videoCodecs')].discovery"), "ready");
});

test(
  "retained Discord connection capability assignments and native fallback preserve encode-only scope",
  {
    skip: !process.env.DPE_CODEC_RENDERER_SOURCE,
  },
  () => {
    const source = require("node:fs").readFileSync(process.env.DPE_CODEC_RENDERER_SOURCE, "utf8");
    // Exercise the retained client's actual assignment and fallback bodies. The
    // native capability response is synthetic; this is not a live SFU/call test.
    const start = source.indexOf("n.getCodecCapabilities(i=>{this.onVideoCodecsCallbackAt");
    assert.ok(start >= 0, "native capability callback must still be recognizable");
    const bodyStart = source.indexOf("{", start) + 1;
    const bodyEnd = source.indexOf("t.getEncryptionModes", bodyStart);
    assert.ok(bodyEnd > bodyStart && bodyEnd - bodyStart < 4000);
    const assignment = source.slice(bodyStart, bodyEnd).replace(/,$/, ";");
    const fallbackStart = source.indexOf("handleVideoEncoderFallback=e=>{");
    const fallbackEnd = source.indexOf(";handleVideoDecoderFallback=", fallbackStart);
    assert.ok(fallbackStart >= 0 && fallbackEnd > fallbackStart);
    const fallback = source
      .slice(fallbackStart, fallbackEnd)
      .split("handleVideoEncoderFallback=")[1];
    for (const scope of ["stream", "camera", "viewer"]) {
      const { run } = fixture({ excludedCodecs: ["AV1"] });
      run(`globalThis.performance={now:()=>2000};
      globalThis.O={UK:{OPUS:'opus'}}; globalThis.u={y:{VideoEncoderFallback:'fallback'}};
      globalThis.y=()=>({}); globalThis.D=x=>x; globalThis.L=x=>x;
      config={context:${JSON.stringify(scope === "camera" ? "default" : "stream")},
        streamUserId:${JSON.stringify(scope === "viewer" ? "other" : "self")},
        videoSupported:true, logger:{info(){}}, emit(){}, overrideCodecResetAt:0};
      globalThis.connection=new Engine().connect();
      (${`function(i){${assignment}}`}).call(connection,nativeCapabilities);
      connection.handleVideoEncoderFallback=(function(){return ${fallback}}).call(connection);`);
      assert.equal(run("connection.codecs.find(c=>c.name==='AV1').encode"), scope !== "stream");
      assert.equal(run("connection.codecs.find(c=>c.name==='AV1').decode"), true);
      assert.equal(run("connection.codecs.find(c=>c.name==='AV1').payloadType"), 101);
      run("connection.handleVideoEncoderFallback('H264')");
      assert.equal(run("connection.codecs.find(c=>c.name==='H264').encode"), false);
      assert.equal(run("connection.codecs.find(c=>c.name==='H264').decode"), true);
      assert.equal(run("connection.codecs.find(c=>c.name==='AV1').encode"), scope !== "stream");
      assert.equal(run("connection.videoEncoderFallbackPending"), true);
    }
  },
);
