// Exercise both adapters together across nested providers and native layouts.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
module.exports = async function check(window, root) {
  const react = fs.readFileSync(
    path.join(process.env.DPE_SETTINGS_OUTPUT, "react-fixture.cjs"),
    "utf8",
  );
  await window.loadURL(
    "data:text/html,<style>body{background:%231a1a1e;color:white;font:16px sans-serif}button{background:%232d2e33;color:inherit;border:0;border-radius:8px;padding:12px}</style><div id='root'></div>",
  );
  await window.webContents.executeJavaScript(`(()=>{
    const React=(()=>{const module={exports:{}};${react};return module.exports;})();
    const shared=(${require(path.join(root, "integration/discord-ui/runtime.cjs"))})();
    const visits=new Map(),target={name:'DISCORD_CHANNEL_CALL_POPOUT',closed:false,addEventListener(){},[Symbol.for('dpe.pipWindow')]:true};
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
    (${require(path.join(root, "integration/discord-ui/pip-participants.cjs"))})({hideMembers:false,showNonVideoParticipants:true},ui);
    const participantVisit=visits.get('pip-participants');participantVisit({Context},runtime);
    window.callLayout='normal';window.closeCount=0;window.registeredCallWindow=null;
    const store={getParticipants:()=>[],getStreamParticipants:()=>[],getVideoParticipants:()=>[],getActivityParticipants:()=>[],getVoiceParticipantsHidden:()=>false,getSelectedParticipant:()=>null,getLayout:()=>callLayout,getMode:()=> 'video'};
    participantVisit({store},runtime);
    class NativeCall extends React.Component {
      get inPopout(){return this.props.appContext==='POPOUT';}
      get popoutOpen(){return !!this.props.popoutWindow && !this.props.popoutWindow.closed;}
      get viewProperties(){return {canPopout:false,canFullscreen:true,canStayOnTop:true};}
      handleClosePopout=()=>{closeCount++;target.closed=true;};
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
    renderCallControls('normal');
  })()`);
  for (const layout of ["normal", "no-chat", "full-screen", "normal"]) {
    await window.webContents.executeJavaScript(
      `renderCallControls(${JSON.stringify(layout)});new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`,
    );
    const result = await window.webContents.executeJavaScript(
      `({returns:document.querySelectorAll('[aria-label="Return to Discord"]').length,pip:document.querySelectorAll('[aria-label="Picture-in-picture"]').length,unrelated:!!document.getElementById('unrelated')})`,
    );
    assert.deepEqual(result, { returns: 1, pip: 0, unrelated: true }, layout);
    await window.webContents.executeJavaScript(
      "void (registeredCallWindow=globalThis[Symbol.for('dpe.callPiP')].activeWindow)",
    );
  }
  window.focus();
  window.webContents.focus();
  await window.webContents.executeJavaScript(
    `document.querySelector('[aria-label="Return to Discord"]').focus()`,
  );
  window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Return" });
  window.webContents.sendInputEvent({ type: "char", keyCode: "\r" });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Return" });
  await new Promise((resolve) => setTimeout(resolve, 70));
  assert.equal(await window.webContents.executeJavaScript("closeCount"), 1);
};
