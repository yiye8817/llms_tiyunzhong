'use strict';
(() => {
  const api = typeof browser !== 'undefined' ? browser : chrome;
  const firefox = typeof browser !== 'undefined';
  const button = document.querySelector('#export');
  const status = document.querySelector('#status');
  const warnings = document.querySelector('#warnings');
  let objectUrl;
  let downloadId;
  let exporting = false;
  function cleanup() {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = undefined;
    downloadId = undefined;
    api.downloads.onChanged.removeListener(changed);
  }
  function changed(event) {
    if (event.id !== downloadId || !event.state) return;
    if (event.state.current === 'complete') { cleanup(); if (!exporting) button.disabled = false; }
    if (event.state.current === 'interrupted') {
      cleanup(); if (!exporting) button.disabled = false;
      status.textContent = FusionLoginExport.safeMessage({ code: 'DOWNLOAD_FAILED' });
    }
  }
  window.addEventListener('unload', cleanup);
  // No Cookie/localStorage reads until the user presses this button.
  button.addEventListener('click', async () => {
    exporting = true;
    button.disabled = true;
    status.textContent = '正在读取当前模型的登录信息…';
    warnings.replaceChildren();
    try {
      const result = await FusionLoginExport.collectSnapshot(api, FUSION_LOGIN_SITES, { firefox });
      const blob = new Blob([result.json], { type: 'application/json;charset=utf-8' });
      objectUrl = URL.createObjectURL(blob);
      api.downloads.onChanged.addListener(changed);
      let createdDownloadId;
      try {
        createdDownloadId = await api.downloads.download({
          url: objectUrl, filename: `multillm-login-${result.providerId}.json`, saveAs: true,
          conflictAction: 'uniquify',
        });
        downloadId = createdDownloadId;
      } catch { throw new FusionLoginExport.ExportError('DOWNLOAD_FAILED'); }
      status.textContent = `${result.providerName}：已请求保存 ${result.cookieCount} 项 Cookie、${result.storageCount} 项网页存储；跳过 ${result.skipped} 项。请等待下载完成后，在应用“设置 → 浏览器登录 → 导入登录文件”中选择文件，并在网页确认登录。`;
      for (const warning of result.warnings) {
        const item = document.createElement('li');
        item.textContent = warning.message;
        warnings.append(item);
      }
      // A tiny local download can finish before download() resolves its ID.
      const completed = await api.downloads.search({ id: createdDownloadId });
      if (completed[0]?.state === 'complete') cleanup();
      else if (completed[0]?.state === 'interrupted') throw new FusionLoginExport.ExportError('DOWNLOAD_FAILED');
    } catch (error) {
      cleanup(); button.disabled = false;
      status.textContent = FusionLoginExport.safeMessage(error);
    } finally {
      exporting = false;
      button.disabled = downloadId !== undefined;
    }
  });
})();
