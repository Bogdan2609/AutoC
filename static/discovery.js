// Read-only polling: no API request is triggered by this script.
(() => {
  'use strict';
  const root = document.querySelector('#supplier-discovery');
  if (!root) return;
  const qs = new URLSearchParams({brand:root.dataset.brand,article:root.dataset.article});
  const list = root.querySelector('#discovery-providers');
  let attempt = 0;
  const poll = async () => {
    if (document.hidden || attempt++ >= 20) return;
    try {
      const response = await fetch('/api/v1/discovery/status?' + qs.toString(), {cache:'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      list.replaceChildren();
      for (const provider of data.suppliers) {
        const node = document.createElement('li');
        node.textContent = provider.supplier + ': ' + provider.status +
          (provider.offers ? ' (' + provider.offers + ' предложений)' : '');
        list.appendChild(node);
      }
      if (data.product_url && data.product_url.startsWith('/product/')) {
        const link = document.createElement('a');
        link.href = data.product_url;
        link.className = 'btn primary';
        link.textContent = 'Открыть найденный товар';
        if (!root.querySelector('a.btn')) root.appendChild(link);
      }
      if (data.pending) setTimeout(poll, 3000);
    } catch (_) { /* Last known state remains visible. */ }
  };
  setTimeout(poll, 1200);
})();
