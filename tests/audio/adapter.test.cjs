const {test} = require('node:test');
const assert = require('node:assert/strict');
const {wrap} = require('../../integration/audio/app-audio.cjs');
function fixture() {
  const calls = [];
  let end;
  const native = {
    setTransportOptions: value => calls.push(value),
    setDesktopSource() {}, setDesktopSourceWithOptions() {}, clearDesktopSource() {}, destroy() {},
    setOnDesktopSourceEnded: value => { end = value; },
    setSelfMute(value) { assert.equal(this, native); calls.push({selfMute:value}); },
  };
  const resolved = new Map([['window:10:0', {pid:100, start:'1'}], ['window:20:0', {pid:200, start:'2'}]]);
  const adapter = wrap(native, {enabled:true, resolvePid: source => resolved.get(source.id), warn() {}});
  return {adapter, native, calls, end: () => end?.('ended')};
}

test('window selection replaces system PID, switches, disables and preserves unrelated options', () => {
  const {adapter, calls} = fixture();
  adapter.setTransportOptions({soundsharePid:1, soundshareLoopback:true, encodingBitRate:128000});
  assert.equal(calls.at(-1).soundsharePid,0);
  adapter.setDesktopSourceWithOptions({sourceId:'window:10:0',type:'window'});
  assert.deepEqual(calls.at(-1),{soundsharePid:100,soundshareLoopback:false});
  adapter.setTransportOptions({encodingBitRate:64000});
  assert.equal(calls.at(-1).encodingBitRate,64000);
  adapter.setDesktopSource('window:20:0',false,'window');
  assert.equal(calls.at(-1).soundsharePid,200);
  adapter.setTransportOptions({soundsharePid:0});
  assert.equal(calls.at(-1).soundsharePid,0);
  adapter.setSelfMute(true);
  assert.deepEqual(calls.at(-1),{selfMute:true});
  adapter.destroy();
});

test('unresolved windows cannot fall back to system capture; explicit screens retain it', () => {
  const {adapter,calls}=fixture();
  adapter.setDesktopSource('window:99:0',false,'window');
  adapter.setTransportOptions({soundsharePid:1,soundshareLoopback:true});
  assert.equal(calls.at(-1).soundsharePid,0);
  adapter.setDesktopSource('screen:0:0',false,'screen');
  assert.deepEqual(calls.at(-1),{soundsharePid:1,soundshareLoopback:true});
  adapter.clearDesktopSource();
  assert.equal(calls.at(-1).soundsharePid,0);
  adapter.destroy();
});

test('source-ended callback stops capture and still reaches the client', () => {
  const f=fixture(); let called=false;
  f.adapter.setOnDesktopSourceEnded(()=>{called=true;});
  f.adapter.setDesktopSource('window:10:0',false,'window');
  f.adapter.setTransportOptions({soundsharePid:1});
  f.end();
  assert.equal(f.calls.at(-1).soundsharePid,0);
  assert.equal(called,true);
  f.adapter.destroy();
});

test('adapter remains opt-in', () => {
  const native={}; assert.equal(wrap(native,{enabled:false}),native);
});
