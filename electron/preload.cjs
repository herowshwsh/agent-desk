const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('agentDesk', {
  requestConfig: () => ipcRenderer.send('request-config'),
  pickFiles: (payload) => ipcRenderer.invoke('files:pick', payload),
  sendChat: (payload) => ipcRenderer.send('chat', payload),
  cancelChat: (payload) => ipcRenderer.send('cancel-chat', payload),
  sendApproval: (payload) => ipcRenderer.send('approval', payload),
  getModels: () => ipcRenderer.send('models:get'),
  saveModels: (payload) => ipcRenderer.send('models:save', payload),
  getServers: () => ipcRenderer.send('servers:get'),
  saveServers: (payload) => ipcRenderer.send('servers:save', payload),
  testServer: (payload) => ipcRenderer.send('servers:test', payload),
  saveBackground: (payload) => ipcRenderer.invoke('appearance:save-background', payload),
  loadBackground: () => ipcRenderer.invoke('appearance:load-background'),
  clearBackground: () => ipcRenderer.invoke('appearance:clear-background'),
  onEvent: (callback) => ipcRenderer.on('bridge-event', (_event, payload) => callback(payload)),
});
