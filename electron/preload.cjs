'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('fusion', Object.freeze({
  request: (method, path, body, options) => ipcRenderer.invoke('fusion:request', { method, path, body, progressId: options?.progressId }),
  showProvider: id => ipcRenderer.invoke('fusion:showProvider', id),
  openRecoveryProvider: id => ipcRenderer.invoke('fusion:openRecoveryProvider', id),
  setBounds: bounds => ipcRenderer.invoke('fusion:setBounds', bounds),
  setLayout: layout => ipcRenderer.invoke('fusion:setLayout', layout),
  diagnoseSend: id => ipcRenderer.invoke('fusion:diagnoseSend', id),
  reloadProvider: id => ipcRenderer.invoke('fusion:reloadProvider', id),
  listBrowserProfiles: () => ipcRenderer.invoke('fusion:listBrowserProfiles'),
  importBrowserLogin: payload => ipcRenderer.invoke('fusion:importBrowserLogin', payload),
  importLoginFile: payload => ipcRenderer.invoke('fusion:importLoginFile', payload),
  saveMarkdown: payload => ipcRenderer.invoke('fusion:saveMarkdown', payload),
  copyText: text => ipcRenderer.invoke('fusion:copyText', text),
  runtimeInfo: () => ipcRenderer.invoke('fusion:runtimeInfo'),
  onStatus: callback => {
    if (typeof callback !== 'function') throw new TypeError('callback must be a function');
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('fusion:status', listener);
    return () => ipcRenderer.removeListener('fusion:status', listener);
  },
}));
