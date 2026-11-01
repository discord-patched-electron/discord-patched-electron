const { contextBridge, webFrame } = require("electron");
contextBridge.exposeInMainWorld("spellcheckProbe", {
  isWordMisspelled: (word) => webFrame.isWordMisspelled(word),
  getWordSuggestions: (word) => webFrame.getWordSuggestions(word),
});
