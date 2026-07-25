/* Theme toggle: auto (follow the OS) -> light -> dark -> auto.
   Applied before first paint by an inline script in <head>; this only handles
   the click and persistence. */
(function () {
  var root = document.documentElement;
  var order = ["auto", "light", "dark"];
  var btn = document.querySelector("[data-theme-toggle]");
  if (!btn) return;

  function label(t) {
    return t === "auto" ? "Colour theme: follow system" : "Colour theme: " + t;
  }
  function paint(t) {
    root.dataset.theme = t;
    btn.setAttribute("aria-label", label(t) + " (click to change)");
    btn.textContent = t === "dark" ? "◑" : t === "light" ? "◐" : "◒";
  }
  paint(localStorage.getItem("registry-theme") || "auto");

  btn.addEventListener("click", function () {
    var next = order[(order.indexOf(root.dataset.theme || "auto") + 1) % order.length];
    try { localStorage.setItem("registry-theme", next); } catch (e) {}
    paint(next);
  });
})();
