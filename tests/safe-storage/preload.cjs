const {ipcRenderer}=require('electron');
const fs=require('node:fs'),path=require('node:path');
try {
  const available=ipcRenderer.sendSync('IS_ENCRYPTION_AVAILABLE');
  const file=path.join(process.env.DPE_STORAGE_OUTPUT,'synthetic-encrypted.txt');
  const reopened=fs.existsSync(file);
  const text='DPE synthetic credential; no account';
  const encrypted=reopened?fs.readFileSync(file,'utf8'):ipcRenderer.sendSync('ENCRYPT_STRING',text);
  const roundtrip=ipcRenderer.sendSync('DECRYPT_STRING',encrypted)===text;
  const invalidRejected=ipcRenderer.sendSync('DECRYPT_STRING',Buffer.from('invalid').toString('base64'))===null;
  const nullHandled=ipcRenderer.sendSync('ENCRYPT_STRING',null)===null;
  if(!reopened&&typeof encrypted==='string') fs.writeFileSync(file,encrypted);
  ipcRenderer.send('result',{passed:available&&roundtrip&&invalidRejected&&nullHandled,available,roundtrip,invalidRejected,nullHandled,reopened});
}catch(error){ipcRenderer.send('result',{passed:false,error:String(error)});}
