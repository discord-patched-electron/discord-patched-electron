const {app,BrowserWindow,ipcMain,session}=require('electron');
const fs=require('node:fs'), path=require('node:path');
app.setPath('userData',path.join(process.env.DPE_AUDIO_OUTPUT,'profile'));
let finished=false;
function finish(result) {
  if(finished)return; finished=true;
  fs.writeFileSync(path.join(process.env.DPE_AUDIO_OUTPUT,'fixture.json'),JSON.stringify({schema:1,...result,versions:process.versions,real_discord_call_verified:false,wayland_source_identity_verified:false,publishable:false},null,2));
  app.exit(result.passed?0:1);
}
ipcMain.on('audio-result',(_e,result)=>finish(result));
app.whenReady().then(()=>{
 session.defaultSession.webRequest.onBeforeRequest((details,callback)=>callback({cancel:!details.url.startsWith('file:')}));
 const w=new BrowserWindow({show:false,webPreferences:{preload:path.join(__dirname,'preload.cjs'),contextIsolation:true,sandbox:false}});
 w.webContents.on('preload-error',(_e,_p,error)=>finish({passed:false,error:String(error)}));
 w.webContents.on('render-process-gone',(_e,details)=>finish({passed:false,error:details}));
 w.webContents.on('console-message',details=>console.log(details.message));
 w.loadFile(path.join(__dirname,'index.html'));
});
setTimeout(()=>finish({passed:false,error:'audio fixture timeout'}),45000);
