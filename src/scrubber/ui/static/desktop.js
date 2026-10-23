// Desktop app only: buttons that need the computer (folder pickers, opening the export
// folder) and the drag-and-drop highlight. The dropped paths themselves are read by the
// app (pywebview), which then reloads this page.
function wire() {
  document.querySelectorAll("[data-app]").forEach(function (el) {
    el.addEventListener("click", function (ev) {
      ev.preventDefault();
      var fn = window.pywebview && window.pywebview.api && window.pywebview.api[el.dataset.app];
      if (!fn) { return; }
      fn().then(function (result) { if (result && el.dataset.reload !== "no") { location.reload(); } });
    });
  });
}
window.addEventListener("pywebviewready", wire);

(function () {
  var depth = 0;
  function zone() { return document.getElementById("dropzone"); }
  document.addEventListener("dragenter", function (e) { e.preventDefault(); depth++; if (zone()) zone().classList.add("over"); });
  document.addEventListener("dragover", function (e) { e.preventDefault(); });
  document.addEventListener("dragleave", function () { depth = Math.max(0, depth - 1); if (!depth && zone()) zone().classList.remove("over"); });
  document.addEventListener("drop", function (e) {
    e.preventDefault(); depth = 0;
    var z = zone();
    if (z) { z.classList.remove("over"); z.querySelector("strong").textContent = "Adding your files…"; }
  });
})();
