const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const install = require("../../integration/video/bitrate.cjs");

function fixture(options = {}, methods = "") {
  const listeners = new Map();
  const context = vm.createContext({
    console,
    addEventListener: (name, callback) => listeners.set(name, callback),
  });
  const run = (source) => vm.runInContext(source, context);
  run(`
    globalThis.O={oL:9000000,lo:3500000,us:10000000,l2:600000,KR:16000,
      Y4:100,ei:{SOURCE:1,FIXED:0},x:{STREAM:'stream'},zt:0.5};
    globalThis.config={};
    globalThis.constants={...O};
    globalThis.watchers={};
    class Connection {
      context='stream';userId='self';streamUserId='self';currentVideoCodec='H264';
      videoStreamParameters=[{ssrc:1,quality:100}];
      videoQualityManager={setGoliveQuality(){}};
      calcMaxBitrateFunc=()=>null;
      setCalcMaxBitrateFunc(callback){this.calcMaxBitrateFunc=callback;return 'set';}
      setDesktopEncodingOptions(e,t,n){
        let i=this.calcMaxBitrateFunc({width:e,height:t,framerate:n,videoCodec:this.currentVideoCodec});
        null==i&&(i=0===t&&n>=10||t>720||n>30?O.oL:O.lo);
        this.videoStreamParameters[0].maxBitrate=i;
        this.videoQualityManager.setGoliveQuality({bitrateMax:i});
        return i;
      }
      getConnectionTransportOptions(){return {callBitRate:O.l2,callMinBitRate:O.KR,
        callMaxBitRate:O.us,encodingVideoDegradationPreference:this.videoDegradationPreference};}
      ${methods}
    }
    class Engine {
      connect(value){if(value==='fail')throw Error('upstream');this.argument=value;
        return Object.assign(new Connection(),config);}
      getCodecSurvey(){} fetchAsyncResources(){} createReplayConnection(){}
    }
    globalThis.Connection=Connection;globalThis.Engine=Engine;
    globalThis.runtime={watch(id,match,visit){watchers[id]={match,visit};
      visit(id.endsWith('constants')?constants:{A:Engine});}};
  `);
  run(
    `(${install.toString()})(${JSON.stringify({ platform: "linux", maxBitrate: 16000000, ...options })},runtime)`,
  );
  return { run, context, emit: (name, detail) => listeners.get(name)?.({ detail }) };
}

test("higher bitrate remains disabled unless explicitly requested within bounds", () => {
  for (const maxBitrate of [undefined, null, "16000000", 0, 9000000, 32000001, 16000000.5]) {
    const { run } = fixture({ maxBitrate });
    assert.equal(run('globalThis[Symbol.for("dpe.videoBitrate")].requestedMaxBitrate'), 0);
  }
  assert.equal(fixture({ platform: "win32" }).run("Object.keys(watchers).length"), 0);
});

test("only high-quality owned supported shares receive a higher maximum", () => {
  const { run } = fixture();
  run("globalThis.connection=new Engine().connect();connection.setCalcMaxBitrateFunc(()=>null)");
  for (const [width, height, fps, expected] of [
    [1920, 1080, 60, 16000000],
    [3840, 2160, 30, 16000000],
    [0, 0, 60, 16000000],
    [1280, 720, 60, 16000000],
    [1280, 720, 30, 3500000],
    [0, 0, 5, 3500000],
  ]) {
    assert.equal(run(`connection.setDesktopEncodingOptions(${width},${height},${fps})`), expected);
  }
  for (const codec of ["AV1", "AV1X", "H264", "VP8", "VP9", "H265", "unknown"]) {
    run(`connection.currentVideoCodec=${JSON.stringify(codec)}`);
    assert.equal(
      run("connection.setDesktopEncodingOptions(1920,1080,60)"),
      ["AV1", "AV1X", "H264", "H265", "VP8"].includes(codec) ? 16000000 : 9000000,
    );
  }
  for (const config of ["{context:'default'}", "{streamUserId:'viewer'}", "{userId:''}"]) {
    run(
      `globalThis.config=${config};connection=new Engine().connect();connection.setCalcMaxBitrateFunc(()=>null)`,
    );
    assert.equal(run("connection.setDesktopEncodingOptions(1920,1080,60)"), 9000000);
    assert.equal(run("connection.getConnectionTransportOptions().callMaxBitRate"), 10000000);
  }
});

test("explicit lower or customized policy results and callback context survive", () => {
  const { run } = fixture();
  run(`globalThis.connection=new Engine().connect();
    globalThis.request=null;globalThis.callbackThis=null;
    globalThis.limit=3500000;
    connection.setCalcMaxBitrateFunc(function(value){callbackThis=this;request=value;return limit;});`);
  for (const limit of [0, 2500000, 3500000, 5000000, 8999999, 9000000, 12000000, 24000000]) {
    run(`globalThis.limit=${limit}`);
    assert.equal(
      run("connection.setDesktopEncodingOptions(1920,1080,60)"),
      limit === 9000000 ? 16000000 : limit,
    );
  }
  assert.equal(run("callbackThis===connection"), true);
  assert.equal(run("request.videoCodec"), "H264");
  assert.equal(run("connection.setCalcMaxBitrateFunc(()=>{throw Error('policy')})"), "set");
  assert.throws(() => run("connection.setDesktopEncodingOptions(1920,1080,60)"), /policy/);
});

test("call envelope retains overhead margin without changing minimum, startup or degradation", () => {
  const { run } = fixture();
  run(
    "globalThis.connection=new Engine().connect();connection.videoDegradationPreference='balanced'",
  );
  const options = JSON.parse(run("JSON.stringify(connection.getConnectionTransportOptions())"));
  assert.deepEqual(options, {
    callBitRate: 600000,
    callMinBitRate: 16000,
    callMaxBitRate: 17000000,
    encodingVideoDegradationPreference: "balanced",
  });
  run("O.us=7000000");
  assert.equal(run("connection.getConnectionTransportOptions().callMaxBitRate"), 7000000);
  run("O.us=25000000");
  assert.equal(run("connection.getConnectionTransportOptions().callMaxBitRate"), 25000000);
});

test("unknown constants or changed native quality policy fail closed", () => {
  const { run } = fixture();
  run(`globalThis[Symbol.for('dpe.videoBitrate')].constantsReviewed=false;
    watchers['dpe-video-bitrate-constants'].visit({oL:12000000,lo:3500000,us:13000000});
    globalThis.connection=new Engine().connect();connection.setCalcMaxBitrateFunc(()=>null);`);
  assert.equal(run("connection.setDesktopEncodingOptions(1920,1080,60)"), 9000000);
  assert.equal(run("connection.getConnectionTransportOptions().callMaxBitRate"), 10000000);
  run(`watchers['dpe-video-bitrate-constants'].visit(constants);
    config={setDesktopEncodingOptions(){return this.calcMaxBitrateFunc({width:1920,height:1080,framerate:60,videoCodec:'H264'})}};
    connection=new Engine().connect();connection.setCalcMaxBitrateFunc(()=>null);`);
  assert.equal(run("connection.setDesktopEncodingOptions()"), null);
});

test("installation is repeatable and preserves native connection return, arguments and errors", () => {
  const { run } = fixture();
  run(`watchers['dpe-video-bitrate'].visit({A:Engine});
    globalThis.engine=new Engine();globalThis.argument={};
    globalThis.connection=engine.connect(argument);`);
  assert.equal(run("connection instanceof Connection && engine.argument===argument"), true);
  assert.throws(() => run("engine.connect('fail')"), /upstream/);
  assert.equal(run("globalThis[Symbol.for('dpe.videoBitrate')].installed"), 1);
  run(`connection.setCalcMaxBitrateFunc(()=>null);
    (${install.toString()})({platform:'linux',maxBitrate:0},runtime);`);
  assert.equal(run("connection.setDesktopEncodingOptions(1920,1080,60)"), 9000000);
  assert.equal(run("connection.getConnectionTransportOptions().callMaxBitRate"), 10000000);
});

test(
  "retained Discord advertises the same ceiling it sends to native transport",
  {
    skip: !process.env.DPE_CODEC_RENDERER_SOURCE,
  },
  () => {
    const source = fs.readFileSync(process.env.DPE_CODEC_RENDERER_SOURCE, "utf8");
    const between = (start, end) => {
      const first = source.indexOf(start),
        last = source.indexOf(end, first);
      assert.ok(first >= 0 && last > first && last - first < 4000, `${start} stays recognizable`);
      return source.slice(first, last);
    };
    const desktop = between("setDesktopEncodingOptions(e,t,n){", "setSDP(e){}");
    const transport = between("getConnectionTransportOptions(){", "handleSpeakingNative=");
    const send = between("sendVideo(e,t,n,i){let r=this._socket;", "getPings(){");
    const video = between("video(e,t,n,i){this.send(12,", "mediaSinkWants(e){");
    const serialize = between(
      "function ez(e){return e?.map(e=>({type:e.type,rid:e.rid",
      "function eX(e)",
    );
    const constraints = between(
      "applyQualityConstraints(e,t){let n=this.getQuality(t);",
      "setQualityOverwrite(e){",
    );
    const { run } = fixture({}, desktop + transport);
    run(`
    globalThis.C={Xb:{equals:(a,b)=>JSON.stringify(a)===JSON.stringify(b)}};
    globalThis.u={y:{Video:'video'}};globalThis.H=ssrc=>ssrc+1;
    globalThis.k={};globalThis.b={$b:()=>false};globalThis.F={VIDEO_EFFECTS:'effects'};
    ${serialize}
    globalThis.socket=new (class {${video}send(op,payload){this.op=op;this.payload=payload;}})();
    globalThis.rtc=new (class {${send}})();rtc._socket=socket;
    globalThis.connection=new Engine().connect();
    connection.createInputModeOptions=()=>({});connection.getAttenuationOptions=()=>({});
    connection.conn={setTransportOptions(options){this.options=options;}};
    connection.audioSSRC=2;
    connection.videoQualityManager={quality:{},getQuality(){return this.quality},
      setGoliveQuality(next){this.quality={...this.quality,...next}},${constraints}};
    connection.applyQualityConstraints=()=>connection.videoQualityManager.applyQualityConstraints({});
    connection.emit=(event,user,unused,audio,ssrc,rtx,streams)=>rtc.sendVideo(audio,ssrc,rtx,streams);
    connection.setCalcMaxBitrateFunc(function(request){if(this!==connection)throw Error('wrong receiver');return null});
    connection.setDesktopEncodingOptions(1920,1080,60);
  `);
    assert.equal(run("connection.conn.options.encodingVideoMaxBitRate"), 16000000);
    assert.equal(run("socket.op"), 12);
    assert.equal(run("socket.payload.streams[0].max_bitrate"), 16000000);
    assert.equal(run("socket.payload.streams[0].max_resolution.height"), 1080);
    assert.equal(run("connection.getConnectionTransportOptions().callMaxBitRate"), 17000000);
    run("connection.currentVideoCodec='AV1';connection.setDesktopEncodingOptions(1920,1080,60)");
    assert.equal(run("socket.payload.streams[0].max_bitrate"), 16000000);
    run(
      "connection.setCalcMaxBitrateFunc(()=>5000000);connection.setDesktopEncodingOptions(1920,1080,30)",
    );
    assert.equal(run("connection.conn.options.encodingVideoMaxBitRate"), 5000000);
    assert.equal(run("socket.payload.streams[0].max_bitrate"), 5000000);
  },
);

test("receiver pixel wants retain the negotiated ceiling without forcing allocation, FPS or resolution", () => {
  const { run, emit } = fixture({ maxBitrate: 0 });
  run(`
    config.videoQualityManager=new(class {
      goliveMaxQuality={encode:{width:2560,height:1440,pixelCount:3686400,framerate:60},
        capture:{width:0,height:0,framerate:60},bitrateMin:500000,bitrateMax:9000000,bitrateTarget:600000};
      options={videoBitrateFloor:150000};fakeGoLiveEncodePixelCount=null;
      scaleLinearly(e,t,n){return 0===t?0:e*n/t}
      getGoliveQuality(e,t){
        if(this.goliveMaxQuality.encode?.pixelCount===undefined||t<=0)return this.goliveMaxQuality;
        let n=this.fakeGoLiveEncodePixelCount!==null?Math.min(this.fakeGoLiveEncodePixelCount,this.goliveMaxQuality.encode.pixelCount):this.goliveMaxQuality.encode.pixelCount;
        if(t>=n)return this.goliveMaxQuality;
        let i=Math.min(.02*this.goliveMaxQuality.encode.pixelCount*this.goliveMaxQuality.encode.framerate,this.goliveMaxQuality.bitrateMax),
          a=this.scaleLinearly(t,n,this.goliveMaxQuality.bitrateMin),l=this.scaleLinearly(t,n,this.goliveMaxQuality.bitrateMax),
          o=this.scaleLinearly(t,n,this.goliveMaxQuality.bitrateTarget);
        return {encode:this.goliveMaxQuality.encode,capture:this.goliveMaxQuality.capture,
          bitrateMin:Math.max(Math.ceil(a),this.options.videoBitrateFloor),bitrateMax:Math.max(Math.ceil(l),i),
          bitrateTarget:Math.max(Math.ceil(o),this.options.videoBitrateFloor),localWant:e};
      }
    })();
    globalThis.manager=config.videoQualityManager;
    globalThis.before=manager.getGoliveQuality(100,921600);
    globalThis.connection=new Engine().connect();
    globalThis.after=manager.getGoliveQuality(100,921600);
  `);
  assert.equal(run("before.bitrateMax"), 4423680);
  assert.equal(run("after.bitrateMax"), 9000000);
  for (const key of ["encode", "capture", "bitrateMin", "bitrateTarget", "localWant"])
    assert.equal(run(`before.${key}===after.${key}`), true);
  assert.equal(run("manager.goliveMaxQuality.bitrateMax"), 9000000);
  emit("dpe-video-source-size", { width: 1920, height: 1080 });
  assert.equal(run("manager.getGoliveQuality(100,2073600).bitrateMax"), 9000000);
  emit("dpe-video-source-size", { width: 1280, height: 720 });
  assert.equal(run("manager.getGoliveQuality(100,2073600).bitrateMax"), 6000000);
  emit("dpe-video-source-size", { width: 1920, height: 1080 });
  assert.equal(run("manager.getGoliveQuality(100,2073600).bitrateMax"), 9000000);
  emit("dpe-video-source-size", { width: 0, height: 0 });

  run("connection.context='default'");
  assert.equal(run("manager.getGoliveQuality(100,921600).bitrateMax"), 4423680);
  run("connection.context='stream';manager.goliveMaxQuality.bitrateMax=3000000");
  assert.equal(run("manager.getGoliveQuality(100,921600).bitrateMax"), 3000000);
  run(
    "manager.goliveMaxQuality.encode.height=720;manager.goliveMaxQuality.encode.framerate=30;manager.goliveMaxQuality.bitrateMax=9000000",
  );
  assert.equal(run("manager.getGoliveQuality(100,921600).bitrateMax"), 2250000);
});

test(
  "retained production quality manager reproduces and fixes the 4.4 Mbps reduction",
  {
    skip: !process.env.DPE_CODEC_RENDERER_SOURCE,
  },
  () => {
    const source = fs.readFileSync(process.env.DPE_CODEC_RENDERER_SOURCE, "utf8");
    const start = source.indexOf("getGoliveQuality(e,t){");
    const end = source.indexOf("getDefaultGoliveQuality(){", start);
    assert.ok(start > 0 && end > start && end - start < 2500);
    const { run } = fixture({ maxBitrate: 0 });
    run(`globalThis.r={YU:.02};globalThis.s=class{constructor(value){Object.assign(this,value)}};
    config.videoQualityManager=new(class {
      options={videoBitrateFloor:150000};fakeGoLiveEncodePixelCount=null;
      goliveMaxQuality={encode:{width:2560,height:1440,pixelCount:3686400,framerate:60},
        bitrateMin:500000,bitrateTarget:600000,bitrateMax:9000000};
      scaleLinearly(e,t,n){return t===0?0:e*n/t}
      ${source.slice(start, end)}
    })();globalThis.manager=config.videoQualityManager;
    globalThis.before=manager.getGoliveQuality(100,921600);
    globalThis.connection=new Engine().connect();
    globalThis.after=manager.getGoliveQuality(100,921600);`);
    assert.equal(run("before.bitrateMax"), 4423680);
    assert.equal(run("after.bitrateMax"), 9000000);
    assert.equal(run("after.bitrateTarget"), 150000);
  },
);
