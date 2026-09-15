const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('youtubeExternal', {
  open: (url) => ipcRenderer.invoke('open-external', url)
});
