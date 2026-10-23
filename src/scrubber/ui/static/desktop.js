// Desktop app only: buttons that need the computer (folder pickers, opening Explorer).
function wire() {
  document.querySelectorAll("[data-app]").forEach(function (b) {
    b.addEventListener("click", function () {
      var fn = window.pywebview && window.pywebview.api && window.pywebview.api[b.dataset.app];
      if (!fn) { return; }
      fn().then(function (result) { if (result && b.dataset.reload !== "no") { location.reload(); } });
    });
  });
}
window.addEventListener("pywebviewready", wire);
