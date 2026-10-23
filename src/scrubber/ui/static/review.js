// Review queue: "select all" and the selected count.
(function () {
  var all = document.getElementById("all");
  if (!all) { return; }
  var boxes = Array.prototype.slice.call(document.querySelectorAll('input[name="fids"]'));
  var count = document.getElementById("count");
  function update() {
    var n = boxes.filter(function (b) { return b.checked; }).length;
    count.textContent = n;
    all.checked = n === boxes.length;
    all.indeterminate = n > 0 && n < boxes.length;
  }
  all.addEventListener("change", function () { boxes.forEach(function (b) { b.checked = all.checked; }); update(); });
  boxes.forEach(function (b) { b.addEventListener("change", update); });
})();
