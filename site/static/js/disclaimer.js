/* Usage disclaimer: dismissible, and stays dismissed.
 *
 * The banner is in the markup and visible by default — CSS hides it only once
 * the root element is marked dismissed, which an inline script in <head> does
 * before first paint. So the failure mode of every moving part here (no
 * JavaScript, no localStorage, a private window) is that the disclaimer shows.
 * That is the right way round for a disclaimer.
 */
(function () {
  "use strict";

  var KEY = "registry-disclaimer";
  var root = document.documentElement;
  var banner = document.querySelector("[data-disclaimer-banner]");
  if (!banner) return;

  var button = banner.querySelector("[data-disclaimer-dismiss]");
  if (!button) return;

  button.addEventListener("click", function () {
    root.dataset.disclaimer = "dismissed";
    try {
      localStorage.setItem(KEY, "dismissed");
    } catch (e) {
      /* Storage unavailable: the dismissal applies to this page view only. */
    }
    // Focus moves to the content the banner sat above, so a keyboard user is
    // not dropped back at the top of the document.
    var main = document.getElementById("main");
    if (main) {
      main.setAttribute("tabindex", "-1");
      main.focus({ preventScroll: true });
    }
  });
})();
