const {ipcRenderer}=require('electron');
(async()=>{
  const utils=require(process.env.DPE_SHORTCUT_MODULE);
  utils.init({logDirectory:process.env.DPE_SHORTCUT_OUTPUT,logLevel:2});
  const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  for(let i=0;i<50&&!utils.isWaylandGlobalShortcutsAvailable();i++)await delay(100);
  if(!utils.isWaylandGlobalShortcutsAvailable())throw Error('Native Wayland shortcut portal unavailable');
  const buttons=[[0,37],[0,64],[0,96]]; // Ctrl+Alt+F12, test bus only.
  const trigger=utils.getWaylandPortalTrigger(buttons);
  const events=[];
  utils.inputEventRegister(4242,buttons,(...args)=>events.push(args),{
    description:'Synthetic fixture shortcut',shortcutId:'dpe-fixture',
    portal:true,keydown:true,keyup:true,focused:true,blurred:true,
  });
  for(let i=0;i<100&&events.length<2;i++)await delay(100);
  utils.inputEventUnregister(4242);
  ipcRenderer.send('result',{passed:events.length===2&&events[0][0]===true&&events[1][0]===false,trigger,events});
})().catch(error=>ipcRenderer.send('result',{passed:false,error:String(error)}));
