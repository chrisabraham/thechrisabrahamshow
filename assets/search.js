/* Search the episode titles and show notes in /search.json, in the browser. */
(function () {
  var q = new URLSearchParams(location.search).get("q") || "",
      box = document.getElementById("q"), status = document.getElementById("status"),
      list = document.getElementById("results");
  box.value = q;
  if (!q.trim()) return;
  var words = q.toLowerCase().split(/\s+/).filter(Boolean);
  status.textContent = "Searching…";
  fetch("/search.json").then(function (r) { return r.json(); }).then(function (eps) {
    var hits = eps.map(function (ep) {
      var t = ep.t.toLowerCase(), n = ep.n.toLowerCase(), score = 0;
      for (var i = 0; i < words.length; i++) {
        if (t.indexOf(words[i]) >= 0) score += 3;
        else if (n.indexOf(words[i]) >= 0) score += 1;
        else return null;
      }
      return { ep: ep, score: score };
    }).filter(Boolean).sort(function (a, b) { return b.score - a.score; });
    status.textContent = hits.length ? hits.length + (hits.length === 1 ? " episode" : " episodes") + " found." : "No episodes found.";
    hits.slice(0, 100).forEach(function (h) {
      var li = document.createElement("li"), a = document.createElement("a"), d = document.createElement("span");
      a.href = h.ep.u; a.textContent = h.ep.t;
      d.className = "date"; d.textContent = h.ep.d;
      li.appendChild(a); li.appendChild(d); list.appendChild(li);
    });
  }).catch(function () { status.textContent = "Search is not available right now."; });
})();
