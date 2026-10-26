// Card review: one flagged item at a time. Good / PII by button or key; Z undoes.
// Each choice is saved as it's made; "Finish" applies them (releases files, re-cleans PII).
(function () {
  var $ = function (id) { return document.getElementById(id); };
  if (!$("deal")) { return; }
  var deck = [], at = 0, done = [], busy = false;

  function who() { return $("who").value.trim(); }

  function post(url, fields) {
    var body = new URLSearchParams(fields);
    body.set("csrf_token", $("csrf").value);
    return fetch(url, { method: "POST", body: body, credentials: "same-origin" }).then(function (r) {
      if (!r.ok) { return r.text().then(function (t) { throw new Error(t || r.statusText); }); }
      return r.json();
    });
  }

  function marked(el, snippet) {
    // "…before ⟦item⟧ after…" with the item highlighted; built from text nodes, never HTML
    el.textContent = "";
    String(snippet || "").split(/⟦|⟧/).forEach(function (part, i) {
      if (i % 2) { var m = document.createElement("mark"); m.textContent = part; el.appendChild(m); }
      else { el.appendChild(document.createTextNode(part)); }
    });
  }

  function cropUrl(c) { return "/cards/crop/" + c.id + ".png"; }

  function show() {
    $("bar").style.width = (deck.length ? (100 * at / deck.length) : 100) + "%";
    if (at >= deck.length) { return finish(); }
    var c = deck[at];
    $("pos").textContent = at + 1;
    $("err").textContent = "";
    var item = c.kind === "item";
    $("pii-label").textContent = item ? "PII" : "Leave file out";
    $("good-label").textContent = item ? "Good" : "Include file";
    $("text").hidden = true;
    $("shot").hidden = !c.crop;
    if (c.crop) {
      $("img").onerror = function () { $("shot").hidden = true; textOnly(c); };
      $("img").src = cropUrl(c);
    } else {
      textOnly(c);
    }
    var q = $("q");
    q.textContent = "";
    if (item) {
      q.appendChild(document.createTextNode("Might be " + c.what + ": "));
      var b = document.createElement("b"); b.textContent = c.value; q.appendChild(b);
    } else {
      q.textContent = c.message;
    }
    var more = c.count - c.files.length;
    $("where").textContent = (c.count === 1 ? "In 1 file: " : "In " + c.count + " files: ") +
      c.files.join(" · ") + (more > 0 ? " · and " + more + " more" : "");
    // fetch the next picture early, so each card appears at once
    var next = deck[at + 1];
    if (next && next.crop) { new Image().src = cropUrl(next); }
  }

  function textOnly(c) {
    if (!c.snippet) { return; }
    $("text").hidden = false;
    marked($("text"), c.snippet);
  }

  function decide(decision) {
    if (busy || at >= deck.length) { return; }
    if (!who()) { $("err").textContent = "Type your name first (top right). It's recorded with your choices."; $("who").focus(); return; }
    var c = deck[at];
    busy = true;
    post("/cards/decide", { card: c.id, decision: decision, reviewer: who() }).then(function () {
      done.push(c.id); at += 1; busy = false; show();
    }).catch(function (e) { busy = false; $("err").textContent = "Couldn't save that choice: " + e.message; });
  }

  function undo() {
    if (busy || !done.length) { return; }
    busy = true;
    post("/cards/undo", { card: done[done.length - 1] }).then(function () {
      done.pop(); at -= 1; busy = false; show();
    }).catch(function (e) { busy = false; $("err").textContent = "Couldn't undo: " + e.message; });
  }

  function finish() {
    if (!who()) { $("err").textContent = "Type your name first (top right)."; $("who").focus(); return; }
    $("deal").innerHTML = "<div class='gone'><b>All decided.</b> Applying your choices…</div>";
    $("finish-who").value = who();
    $("finish").submit();
  }

  $("pii").addEventListener("click", function () { decide("pii"); });
  $("good").addEventListener("click", function () { decide("good"); });
  $("undo").addEventListener("click", undo);
  $("stop").addEventListener("click", function () {
    if (!done.length && !window.confirm("You haven't decided any cards yet. Go back to the batch?")) { return; }
    if (!done.length) { location.href = "/"; return; }
    finish();
  });
  document.addEventListener("keydown", function (e) {
    if (e.target && e.target.tagName === "INPUT") {
      if (e.key === "Enter") { e.target.blur(); }
      return;                                  // typing a name never triggers a choice
    }
    if (e.metaKey || e.ctrlKey || e.altKey) { return; }
    var k = e.key.toLowerCase();
    if (k === "arrowleft" || k === "p") { e.preventDefault(); decide("pii"); }
    else if (k === "arrowright" || k === "g") { e.preventDefault(); decide("good"); }
    else if (k === "z" || k === "backspace") { e.preventDefault(); undo(); }
  });

  fetch("/cards/deck.json", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
    deck = d;
    if (!deck.length) { $("deal").innerHTML = "<div class='gone'><b>Nothing waiting.</b></div>"; return; }
    show();
    if (!who()) { $("who").focus(); }
  });
})();
