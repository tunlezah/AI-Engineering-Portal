/* Faceted browse — runs entirely in the browser against the generated catalogue.
 *
 * No search server, no query API. One fetch of catalog.json (~1.2 KB per harness),
 * an in-memory MiniSearch index for text, and plain array filtering for facets.
 * The URL always reflects the full query state, so any search is a shareable link
 * — which is how search results end up in merge requests and tickets.
 *
 * Ranking is deliberately not pure relevance: a perfectly-matching abandoned
 * experiment should not outrank a slightly-less-matching certified harness.
 * Weights live in RANK below and are documented at /docs/search-ranking/.
 */
(function () {
  "use strict";

  var el = document.getElementById("browse");
  if (!el) return;

  // Short keys keep the catalogue small; expand once, here.
  var FIELDS = {
    i: "id", n: "name", s: "summary", o: "owner", tm: "team", tg: "tags",
    bd: "business", td: "technical", ar: "pattern", md: "models", rt: "runtimes",
    pl: "plugins", lc: "lifecycle", v: "version", q: "quality", e: "pass_rate",
    sc: "classification", rl: "risk", ap: "approval", u: "updated", c: "consumers",
    w: "warnings"
  };

  var FACETS = [
    { key: "business", label: "Business domain" },
    { key: "technical", label: "Technical domain" },
    { key: "lifecycle", label: "Lifecycle" },
    { key: "pattern", label: "Architecture" },
    { key: "team", label: "Team" },
    { key: "models", label: "Model" },
    { key: "plugins", label: "Required plugin" },
    { key: "classification", label: "Classification" },
    { key: "risk", label: "Risk level" },
    { key: "approval", label: "Approval" },
    { key: "tags", label: "Tags" }
  ];

  var RANK = {
    lifecycle: {
      certified: 1.5, "org-ready": 1.3, "team-ready": 1.1, prototype: 0.9,
      experimental: 0.7, deprecated: 0.3, retired: 0.1, invalid: 0.2
    },
    qualityFloor: 0.7, qualityWeight: 0.3,
    consumerStep: 0.03, consumerCap: 10,
    freshFullDays: 180, freshFloor: 0.6
  };

  var state = { q: "", facets: {}, sort: "relevance", ranges: {}, excludes: {} };
  var data = [], search = null;

  // ---------- load ----------
  fetch(el.dataset.catalog)
    .then(function (r) { return r.json(); })
    .then(function (cat) {
      data = cat.harnesses.map(expand);
      buildIndex();
      readURL();
      render();
    })
    .catch(function (err) {
      document.getElementById("result-count").textContent =
        "Could not load the catalogue (" + err + "). The static harness list still works.";
    });

  function expand(h) {
    var out = {};
    for (var k in h) if (FIELDS[k]) out[FIELDS[k]] = h[k];
    out.text = [out.name, out.summary, (out.tags || []).join(" "), out.id].join(" ");
    return out;
  }

  function buildIndex() {
    if (typeof MiniSearch === "undefined") return;      // text search degrades, facets still work
    search = new MiniSearch({
      fields: ["name", "summary", "tags", "id", "business", "technical", "pattern"],
      storeFields: ["id"],
      idField: "id",
      searchOptions: {
        prefix: true, fuzzy: 0.2,
        boost: { name: 3, tags: 2, summary: 2, business: 1.5, technical: 1.5 }
      },
      extractField: function (doc, field) {
        var v = doc[field];
        return Array.isArray(v) ? v.join(" ") : (v == null ? "" : String(v));
      }
    });
    search.addAll(data);
  }

  // ---------- query language ----------
  // `rag owner:group:legal lifecycle:certified quality:>85 -lifecycle:deprecated "clause extraction"`
  var ALIASES = {
    bd: "business", td: "technical", lc: "lifecycle", ar: "pattern", model: "models",
    plugin: "plugins", tag: "tags", classification: "classification", risk: "risk",
    approval: "approval", owner: "owner", team: "team", pattern: "pattern"
  };
  var RANGE_FIELDS = { quality: "quality", pass: "pass_rate", consumers: "consumers", updated: "updated" };

  function parse(q) {
    var terms = [], facets = {}, excludes = {}, ranges = {};
    var re = /(-?)(?:([a-z_]+):)?(?:"([^"]*)"|(\S+))/gi, m;
    while ((m = re.exec(q))) {
      var neg = m[1] === "-", field = (m[2] || "").toLowerCase(), value = m[3] || m[4];
      if (!field) { terms.push(value); continue; }
      if (RANGE_FIELDS[field]) {
        ranges[RANGE_FIELDS[field]] = value;                    // ">85", "<0.9", "<90d"
        continue;
      }
      var f = ALIASES[field] || field;
      var bucket = neg ? excludes : facets;
      (bucket[f] = bucket[f] || []).push(value);
    }
    return { terms: terms.join(" ").trim(), facets: facets, excludes: excludes, ranges: ranges };
  }

  function matchRange(value, expr) {
    if (value == null) return false;
    var m = /^([<>]=?)?\s*([0-9.]+)(d)?$/.exec(String(expr));
    if (!m) return String(value) === String(expr);
    var op = m[1] || ">=", num = parseFloat(m[2]), isDays = !!m[3];
    if (isDays) {
      var age = (Date.now() - Date.parse(value)) / 86400000;
      return op.charAt(0) === "<" ? age <= num : age >= num;
    }
    var v = parseFloat(value);
    if (isNaN(v)) return false;
    switch (op) {
      case ">": return v > num;
      case ">=": return v >= num;
      case "<": return v < num;
      case "<=": return v <= num;
      default: return v === num;
    }
  }

  function has(h, field, value) {
    var v = h[field];
    if (Array.isArray(v)) return v.indexOf(value) !== -1;
    if (field === "owner") return String(v || "").indexOf(value) !== -1;
    return String(v) === value;
  }

  // ---------- filtering + ranking ----------
  function apply() {
    var parsed = parse(state.q);
    var facets = merge(state.facets, parsed.facets);
    var ranges = Object.assign({}, state.ranges, parsed.ranges);
    var scores = null;

    if (parsed.terms && search) {
      scores = {};
      search.search(parsed.terms).forEach(function (r) { scores[r.id] = r.score; });
    }

    var rows = data.filter(function (h) {
      for (var f in facets) {
        if (!facets[f].length) continue;
        if (!facets[f].some(function (v) { return has(h, f, v); })) return false;
      }
      for (var e in parsed.excludes) {
        if (parsed.excludes[e].some(function (v) { return has(h, e, v); })) return false;
      }
      for (var r in ranges) {
        if (!matchRange(h[r], ranges[r])) return false;
      }
      if (scores && !(h.id in scores)) return false;
      return true;
    });

    rows.forEach(function (h) { h._score = rank(h, scores ? scores[h.id] : 1); });
    rows.sort(sorter());
    return { rows: rows, facets: facets, parsed: parsed };
  }

  function rank(h, relevance) {
    var s = (relevance || 1) * (RANK.lifecycle[h.lifecycle] || 1);
    s *= RANK.qualityFloor + RANK.qualityWeight * ((h.quality || 0) / 100);
    s *= 1 + Math.min(h.consumers || 0, RANK.consumerCap) * RANK.consumerStep;
    if (h.updated) {
      var days = (Date.now() - Date.parse(h.updated)) / 86400000;
      var fresh = days <= RANK.freshFullDays ? 1
        : Math.max(RANK.freshFloor, 1 - ((days - RANK.freshFullDays) / 365) * 0.4);
      s *= fresh;
    }
    return s;
  }

  function sorter() {
    switch (state.sort) {
      case "quality": return function (a, b) { return b.quality - a.quality; };
      case "consumers": return function (a, b) { return b.consumers - a.consumers || b.quality - a.quality; };
      case "updated": return function (a, b) { return String(b.updated).localeCompare(String(a.updated)); };
      case "pass": return function (a, b) { return (b.pass_rate || 0) - (a.pass_rate || 0); };
      case "name": return function (a, b) { return a.name.localeCompare(b.name); };
      default: return function (a, b) { return b._score - a._score; };
    }
  }

  function merge(a, b) {
    var out = {};
    [a, b].forEach(function (src) {
      for (var k in src) { if (src[k] && src[k].length) out[k] = (out[k] || []).concat(src[k]); }
    });
    return out;
  }

  // ---------- rendering ----------
  function render() {
    var res = apply();
    renderResults(res.rows);
    renderFacets(res.rows, res.facets);
    writeURL();
  }

  function renderResults(rows) {
    var list = document.getElementById("results");
    var count = document.getElementById("result-count");
    count.textContent = rows.length + " of " + data.length + " harnesses";
    if (!rows.length) {
      list.innerHTML = '<li class="hcard"><p><strong>Nothing matches.</strong> Try removing a filter, ' +
        'or <a href="' + base() + 'docs/contributing/">propose a new harness</a>.</p></li>';
      return;
    }
    list.innerHTML = rows.slice(0, 200).map(function (h) {
      return '<li class="hcard">' +
        '<div class="hcard-head">' +
          '<a class="hcard-title" href="' + base() + 'harnesses/' + h.id + '/">' + esc(h.name) + '</a>' +
          '<span class="badge lc lc-' + h.lifecycle + '">' + h.lifecycle + '</span>' +
        '</div>' +
        '<p class="hcard-summary">' + esc(h.summary) + '</p>' +
        '<p class="hcard-meta">' +
          '<span>Q ' + h.quality + '</span>' +
          (h.pass_rate != null ? '<span>pass ' + Math.round(h.pass_rate * 100) + '%</span>' : '<span class="muted">no evaluation</span>') +
          '<span>' + h.consumers + ' consumer' + (h.consumers === 1 ? '' : 's') + '</span>' +
          '<span>' + esc(h.team) + '</span>' +
          '<span>' + esc(h.pattern) + '</span>' +
          (h.updated ? '<span>updated ' + h.updated + '</span>' : '') +
          (h.warnings ? '<span class="bad">' + h.warnings + ' warning' + (h.warnings === 1 ? '' : 's') + '</span>' : '') +
        '</p></li>';
    }).join("");
    if (rows.length > 200) {
      list.insertAdjacentHTML("beforeend",
        '<li class="muted">Showing the first 200 of ' + rows.length + '. Narrow the query to see the rest.</li>');
    }
  }

  function renderFacets(rows, active) {
    var box = document.getElementById("facets");
    // Counts are computed over the *current* result set, so a facet never offers
    // a filter that would return nothing.
    var html = FACETS.map(function (f) {
      var counts = {};
      rows.forEach(function (h) {
        var v = h[f.key];
        (Array.isArray(v) ? v : [v]).forEach(function (x) {
          if (x === undefined || x === null || x === "") return;
          counts[x] = (counts[x] || 0) + 1;
        });
      });
      (active[f.key] || []).forEach(function (v) { if (!(v in counts)) counts[v] = 0; });
      var keys = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a] || a.localeCompare(b); });
      if (!keys.length) return "";
      return '<div class="facet"><h3>' + f.label + '</h3><ul>' + keys.map(function (k) {
        var on = (active[f.key] || []).indexOf(k) !== -1;
        return '<li><label><input type="checkbox" data-facet="' + f.key + '" value="' + esc(k) + '"' +
          (on ? " checked" : "") + '> ' + esc(String(k).replace("group:", "")) +
          '<span class="count">' + counts[k] + '</span></label></li>';
      }).join("") + "</ul></div>";
    }).join("");
    box.innerHTML = html +
      '<div class="facet"><button type="button" class="button" id="reset-facets">Reset filters</button></div>';
  }

  // ---------- URL state ----------
  function readURL() {
    var p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    state.sort = p.get("sort") || "relevance";
    FACETS.forEach(function (f) {
      var v = p.getAll(f.key);
      if (v.length) state.facets[f.key] = v.slice();
    });
    // Short aliases used in links elsewhere on the site (/browse/?lc=certified).
    ["lc:lifecycle", "bd:business", "td:technical"].forEach(function (pair) {
      var a = pair.split(":");
      var v = p.getAll(a[0]);
      if (v.length) state.facets[a[1]] = (state.facets[a[1]] || []).concat(v);
    });
    document.getElementById("browse-q").value = state.q;
    document.getElementById("sort").value = state.sort;
  }

  function writeURL() {
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.sort !== "relevance") p.set("sort", state.sort);
    for (var f in state.facets) state.facets[f].forEach(function (v) { p.append(f, v); });
    var qs = p.toString();
    history.replaceState(null, "", qs ? "?" + qs : location.pathname);
  }

  // ---------- events ----------
  var input = document.getElementById("browse-q");
  input.addEventListener("input", debounce(function () { state.q = input.value; render(); }, 160));
  document.getElementById("sort").addEventListener("change", function (e) {
    state.sort = e.target.value; render();
  });
  document.getElementById("browse-clear").addEventListener("click", function () {
    state = { q: "", facets: {}, sort: state.sort, ranges: {}, excludes: {} };
    input.value = ""; render();
  });
  document.getElementById("facets").addEventListener("change", function (e) {
    var cb = e.target;
    if (!cb.dataset || !cb.dataset.facet) return;
    var key = cb.dataset.facet, list = state.facets[key] || [];
    state.facets[key] = cb.checked
      ? list.concat([cb.value])
      : list.filter(function (v) { return v !== cb.value; });
    render();
  });
  document.getElementById("facets").addEventListener("click", function (e) {
    if (e.target.id === "reset-facets") { state.facets = {}; render(); }
  });

  function debounce(fn, ms) {
    var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function base() {
    var m = location.pathname.match(/^(.*\/)browse\/?$/);
    return m ? m[1] : "/";
  }
})();
