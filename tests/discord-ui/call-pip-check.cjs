// Exercise the toolbar adapter with real component rendering and keyboard input.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
module.exports = async function check(window, root) {
  await window.loadURL(
    "data:text/html," +
      encodeURIComponent(
        '<style>body{margin:24px;background:#1a1a1e;color:white;font:14px sans-serif}#controls{display:flex;gap:8px}.call-control{display:flex;align-items:center;justify-content:center;width:36px;height:36px;padding:6px;border:0;border-radius:4px;background:transparent;color:inherit}.call-control:hover{background:#383940}.call-control:focus-visible{outline:2px solid #00b0f4;outline-offset:2px}.call-control:disabled{opacity:.35}.call-control svg{width:24px;height:24px}</style><div id="controls"></div>',
      ),
  );
  const react = fs.readFileSync(path.join(root, "out/ui/ui-runtime.cjs"), "utf8");
  const runtime = require(path.join(root, "integration/discord-ui/runtime.cjs"));
  const adapter = require(path.join(root, "integration/discord-ui/call-pip.cjs"));
  await window.webContents.executeJavaScript(`(()=>{
    const React=(()=>{const module={exports:{}};${react};return module.exports;})();
    const shared=(${runtime})();let visit;
    const ui={...shared,inspect:()=>({React}),watch:(_id,_match,fn)=>{visit=fn;}};
    const WindowLaunchIcon=()=>React.createElement('svg',{viewBox:'0 0 24 24'},React.createElement('path',{d:'M14 3h7v7h-2V6.4l-8.3 8.3-1.4-1.4L17.6 5H14zM3 5h7v2H5v12h12v-5h2v7H3z',fill:'currentColor'}));
    function Control({label,iconComponent:Icon,onClick,className,disabled}){return React.createElement('button',{className,disabled,onClick,title:label,'aria-label':label,type:'button'},React.createElement(Icon,{}));}
    function Native({popoutOpen,onOpenPopout,onClosePopout,...props}){return React.createElement(Control,{label:popoutOpen?'Return to call':'Pop out',iconComponent:WindowLaunchIcon,onClick:popoutOpen?onClosePopout:onOpenPopout,...props});}
    const exports={};Object.defineProperty(exports,'A',{value:Native,enumerable:true});const runtime={c:{control:{exports}}};
    (${adapter})(${JSON.stringify({ icons: { pip: require(path.join(root, "integration/discord-ui/icons.cjs"))().pip } })},ui);visit(exports,runtime);
    window.entryClicks=[];
    const handlers={handleOpenPopout(){const pip=globalThis[Symbol.for('dpe.callPiP')].consume('DISCORD_CHANNEL_CALL_POPOUT');entryClicks.push(pip?'pip':'stock');}};
    window.renderEntry=(open=false)=>React.render(React.createElement(runtime.c.control.exports.A,{popoutOpen:open,onOpenPopout:()=>handlers.handleOpenPopout(),onClosePopout:()=>entryClicks.push('close'),className:'call-control'}),document.getElementById('controls'));
    renderEntry();
  })()`);
  const layout = await window.webContents.executeJavaScript(
    `(()=>{const [pip,stock]=document.querySelectorAll('button');const a=pip.getBoundingClientRect(),b=stock.getBoundingClientRect();return{labels:[pip.ariaLabel,stock.ariaLabel],width:a.width,height:a.height,gap:b.left-a.right,icon:!!pip.querySelector('svg'),classMatch:pip.className===stock.className};})()`,
  );
  assert.deepEqual(layout, {
    labels: ["Picture-in-picture", "Pop out"],
    width: 36,
    height: 36,
    gap: 8,
    icon: true,
    classMatch: true,
  });
  window.focus();
  window.webContents.focus();
  await window.webContents.executeJavaScript("document.querySelector('button').focus()");
  window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Return" });
  window.webContents.sendInputEvent({ type: "char", keyCode: "\r" });
  window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Return" });
  await new Promise((resolve) => setTimeout(resolve, 70));
  await window.webContents.executeJavaScript("document.querySelectorAll('button')[1].click()");
  assert.deepEqual(await window.webContents.executeJavaScript("entryClicks"), ["pip", "stock"]);
  return {
    assertDisabled: async () => {
      await window.webContents.executeJavaScript("renderEntry(true)");
      await new Promise((resolve) => setTimeout(resolve, 40));
      assert.equal(
        await window.webContents.executeJavaScript("document.querySelector('button').disabled"),
        true,
      );
      await window.webContents.executeJavaScript("document.querySelectorAll('button')[1].click()");
      assert.deepEqual(await window.webContents.executeJavaScript("entryClicks"), [
        "pip",
        "stock",
        "close",
      ]);
    },
  };
};
