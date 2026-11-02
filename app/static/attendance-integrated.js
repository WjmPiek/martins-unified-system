(() => {
  const frame = document.getElementById('attendance-module');
  if (!frame) return;
  window.addEventListener('message', event => {
    if (event.source !== frame.contentWindow || event.origin !== frame.dataset.moduleOrigin) return;
    if (event.data?.type === 'martins:attendance-height' && Number.isFinite(event.data.height)) {
      frame.height = String(Math.max(500, Math.min(50000, Math.ceil(event.data.height))));
    }
    if (event.data?.type === 'martins:attendance-retry') window.location.reload();
    if (event.data?.type === 'martins:attendance-scroll-top') frame.scrollIntoView({block: 'start'});
  });
})();
