// Exercise both adapters together across nested providers and native layouts.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
module.exports = async function check(window, root) {
  const run = async (code) => {
    const result = await window.webContents.executeJavaScript(
      `(async()=>{try{return {value:await eval(${JSON.stringify(code)})};}catch(error){return {error:String(error.stack)};}})()`,
    );
    if (result.error) throw Error(result.error);
    return result.value;
  };
  const react = fs.readFileSync(
    path.join(process.env.DPE_SETTINGS_OUTPUT, "react-fixture.cjs"),
    "utf8",
  );
  await window.loadURL(
    "data:text/html,<style>body{background:%231a1a1e;color:white;font:16px sans-serif}button{background:%232d2e33;color:inherit;border:0;border-radius:8px;padding:12px}</style><div id='root'></div>",
  );
  await run(`(()=>{
    const React=(()=>{const module={exports:{}};${react};return module.exports;})();
    const shared=(${require(path.join(root, "integration/discord-ui/runtime.cjs"))})();
    const visits=new Map();let target={name:'DISCORD_CHANNEL_CALL_POPOUT',closed:false,addEventListener(){},[Symbol.for('dpe.pipWindow')]:true};
    const Context=React.createContext({appContext:'APP',renderWindow:window,windowDispatch:{}});
    Context.$$typeof=Symbol.for('react.context');Context._currentValue={appContext:'APP',renderWindow:window,windowDispatch:{}};
    const ui={...shared,inspect:()=>({React,objects:[Context]}),watch:(id,_match,visit)=>visits.set(id,visit)};
    const runtime={c:{}};
    const WindowLaunchIcon=()=>React.createElement('span',{'aria-hidden':true},'');
    function NativeControl({popoutOpen,onClosePopout,onOpenPopout,...props}){
      const label=popoutOpen?'Return to Discord':'Pop out',iconComponent=WindowLaunchIcon;
      return React.createElement('button',{'aria-label':label,onClick:popoutOpen?onClosePopout:onOpenPopout},React.createElement(iconComponent,{}),label);
    }
    runtime.c.control={exports:Object.freeze({A:NativeControl})};
    (${require(path.join(root, "integration/discord-ui/call-pip.cjs"))})({},ui);
    visits.get('call-pip-button')(runtime.c.control.exports,runtime);
    const position={pipVideoWindow:{id:'native-video',position:'top-right'},getDockedRect(){},pipWidth(){}};
    window.returnMoves=[];
    const moveActions={move(id,position){const dispatch=event=>returnMoves.push(event);dispatch({type:'PICTURE_IN_PICTURE_MOVE',id,position});}};
    visits.get('call-pip-button')({position},runtime);visits.get('call-pip-button')(moveActions,runtime);
    (${require(path.join(root, "integration/discord-ui/pip-participants.cjs"))})({hideMembers:false,showNonVideoParticipants:true},ui);
    const participantVisit=visits.get('pip-participants');participantVisit({Context},runtime);
    window.callLayout='normal';window.closeCount=0;window.registeredCallWindow=null;
    const store={getParticipants:()=>[],getStreamParticipants:()=>[],getVideoParticipants:()=>[],getActivityParticipants:()=>[],getVoiceParticipantsHidden:()=>false,getSelectedParticipant:()=>null,getLayout:()=>callLayout,getMode:()=> 'video'};
    participantVisit({store},runtime);
    class NativeCall extends React.Component {
      get inPopout(){return this.props.appContext==='POPOUT';}
      get popoutOpen(){return !!this.props.popoutWindow && !this.props.popoutWindow.closed;}
      get viewProperties(){return {canPopout:false,canFullscreen:true,canStayOnTop:true};}
      handleClosePopout=()=>{closeCount++;if(target.close)target.close();else target.closed=true;};
      render(){return React.createElement('div',{'data-layout':this.props.layout},this.viewProperties.canPopout?React.createElement(runtime.c.control.exports.A,{popoutOpen:this.popoutOpen,onClosePopout:this.handleClosePopout,onOpenPopout:()=>{throw Error('Must use native close');}}):null);}
    }
    const Pass=props=>props.children;
    function NativeRoot({channel,popoutType}) {
      const context=React.useContext(Context),layout=store.getLayout(channel.id),mode=store.getMode(channel.id),selectedParticipant=store.getSelectedParticipant(channel.id);
      return React.createElement(Pass,null,React.createElement(Pass,null,React.createElement(Pass,null,[React.createElement(NativeCall,{key:'call',channel,popoutType,appContext:context.appContext,popoutWindow:registeredCallWindow,participants:[],guildRoomVisible:false,layout,mode,selectedParticipant,callContainerDimensions:{width:640,height:400}}),React.createElement('span',{key:'unrelated',id:'unrelated'},'Unrelated native child')])));
    }
    runtime.c.call={exports:Object.freeze({default:NativeRoot})};participantVisit(runtime.c.call.exports,runtime);
    globalThis[Symbol.for('dpe.callPiP')].opened(target);
    window.renderCallControls=(layout)=>{callLayout=layout;React.render(React.createElement(Context.Provider,{value:{appContext:'POPOUT',renderWindow:target,windowDispatch:{}}},React.createElement(runtime.c.call.exports.default,{channel:{id:'synthetic-call'},popoutType:'voice'})),document.getElementById('root'));};
    window.useReturnGuest=()=>{target=window.returnGuest;target[Symbol.for('dpe.pipWindow')]=true;globalThis[Symbol.for('dpe.callPiP')].opened(target);registeredCallWindow=target;renderCallControls('normal');};
    renderCallControls('normal');
  })()`);
  for (const layout of ["normal", "no-chat", "full-screen", "normal"]) {
    await run(
      `renderCallControls(${JSON.stringify(layout)});new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`,
    );
    const result = await run(
      `({returns:document.querySelectorAll('[aria-label="Return to Discord"]').length,pip:document.querySelectorAll('[aria-label="Picture-in-picture"]').length,unrelated:!!document.getElementById('unrelated')})`,
    );
    assert.deepEqual(result, { returns: 1, pip: 0, unrelated: true }, layout);
    await run("void (registeredCallWindow=globalThis[Symbol.for('dpe.callPiP')].activeWindow)");
  }
  window.focus();
  window.webContents.focus();
  await run(`document.querySelector('[aria-label="Return to Discord"]').focus()`);
  window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Return" });
  window.webContents.sendInputEvent({ type: "char", keyCode: "\r" });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Return" });
  await new Promise((resolve) => setTimeout(resolve, 70));
  assert.equal(await run("closeCount"), 1);
  const { ipcMain } = require("electron");
  const { once } = require("node:events");
  let guest;
  ipcMain.handle("dpe-test-pip-return-corner", (event) => {
    assert.equal(event.sender, guest.webContents);
    return { available: true, corner: null, returnCorner: "bottom-left" };
  });
  window.webContents.setWindowOpenHandler(() => ({
    action: "allow",
    overrideBrowserWindowOptions: {
      show: false,
      webPreferences: {
        contextIsolation: true,
        sandbox: true,
        preload: path.join(root, "tests/discord-ui/pip-return-preload.cjs"),
      },
    },
  }));
  try {
    const created = once(window.webContents, "did-create-window");
    await run("void (window.returnGuest=window.open('about:blank','DISCORD_CHANNEL_CALL_POPOUT'))");
    [guest] = await created;
    await run(`new Promise((resolve,reject)=>{
      let attempts=0;const ready=()=>{if(returnGuest.DPEPiP)resolve(true);else if(++attempts>60)reject(Error('PiP bridge missing'));else setTimeout(ready,20);};ready();
    })`);
    await run(
      "useReturnGuest();new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))",
    );
    window.focus();
    window.webContents.focus();
    await run("document.querySelector('[aria-label=\"Return to Discord\"]').focus()");
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Return" });
    window.webContents.sendInputEvent({ type: "char", keyCode: "\r" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Return" });
    await new Promise((resolve) => setTimeout(resolve, 150));
    assert.equal(await run("closeCount"), 2);
    assert.deepEqual(await run("returnMoves"), [
      { type: "PICTURE_IN_PICTURE_MOVE", id: "native-video", position: "bottom-left" },
    ]);
    assert.equal(guest.isDestroyed(), true);
  } finally {
    ipcMain.removeHandler("dpe-test-pip-return-corner");
    if (guest && !guest.isDestroyed()) guest.destroy();
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  }
};
