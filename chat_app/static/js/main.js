if ('serviceWorker' in navigator) {
        window.addEventListener('load', function () {
          navigator.serviceWorker.register('/static/sw.js')
            .then(function (reg) { console.log('SW registered:', reg.scope); })
            .catch(function (err) { console.warn('SW failed:', err); });
        });
      }