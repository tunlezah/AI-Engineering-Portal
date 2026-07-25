/* Mermaid initialisation.
 *
 * Two sources of diagrams:
 *   1. <pre class="mermaid"> emitted by the templates from generated diagram text
 *   2. ```mermaid fenced blocks inside README markdown, which Hugo renders as
 *      <pre><code class="language-mermaid">
 * The second is normalised into the first before init.
 *
 * Theme variables are read from the page's CSS custom properties so diagrams
 * match light and dark without shipping two copies.
 */
(function () {
  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  ready(function () {
    document.querySelectorAll("pre > code.language-mermaid").forEach(function (code) {
      var pre = code.parentElement;
      var holder = document.createElement("pre");
      holder.className = "mermaid";
      holder.textContent = code.textContent;
      pre.replaceWith(holder);
    });

    var blocks = document.querySelectorAll("pre.mermaid");
    if (!blocks.length || typeof mermaid === "undefined") return;

    var css = getComputedStyle(document.documentElement);
    var dark = matchMedia("(prefers-color-scheme: dark)").matches;
    var theme = document.documentElement.dataset.theme;
    var isDark = theme === "dark" || (theme === "auto" && dark);

    mermaid.initialize({
      startOnLoad: true,
      securityLevel: "strict",         // registry content is data, never instructions
      theme: isDark ? "dark" : "neutral",
      fontFamily: css.getPropertyValue("--sans") || "system-ui, sans-serif",
      flowchart: { curve: "basis", useMaxWidth: true },
      sequence: { useMaxWidth: true, actorMargin: 40 },
      themeVariables: {
        primaryColor: css.getPropertyValue("--bg-alt").trim(),
        primaryTextColor: css.getPropertyValue("--fg").trim(),
        lineColor: css.getPropertyValue("--border-strong").trim(),
        background: css.getPropertyValue("--bg").trim()
      }
    });
  });
})();
