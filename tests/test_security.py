"""Security regressions.

Every test here corresponds to a defect that was found in this codebase and
fixed, not to a hypothetical. The threat model is the one the design implies:
any team can create a project with a `harness.yaml`, so a manifest, a README and
a CHANGELOG are all untrusted input that the registry crawls, indexes and
renders.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.registryctl.content import _mermaid_label  # noqa: E402
from tools.registryctl.index import safe_child, safe_id  # noqa: E402
from tools.registryctl.verify import (  # noqa: E402
    _targets, check_accessibility, verify_site,
)

HEAD = '<!doctype html><html lang=en><body><a class=skip-link href=#main>s</a>'
TAIL = "</body></html>"


def _site(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    """A minimal built site containing one page and the catalogue."""
    public = tmp_path / "public"
    public.mkdir()
    (public / "index.html").write_text(HEAD + body + TAIL)
    (public / "catalog.json").write_text('{"count":0,"harnesses":[]}')
    return public


# --------------------------------------------------------------------------
# Path traversal: metadata.id reaches the filesystem
# --------------------------------------------------------------------------
class TestHarnessIdIsNotAPath:
    @pytest.mark.parametrize("value", [
        "../../../../etc/passwd", "/etc/passwd", "a/../../b", "..", ".",
        "with space", "UPPER", "trailing-", "-leading", "", None, 42,
        "x" * 80,
    ])
    def test_hostile_ids_are_rejected(self, value):
        assert safe_id(value) == ""

    @pytest.mark.parametrize("value", ["contract-review", "sql-analyst", "a1b"])
    def test_real_ids_survive(self, value):
        assert safe_id(value) == value

    @pytest.mark.parametrize("name", [
        "../escape.json", "../../escape.json", "/abs.json", "a/../../b.json",
    ])
    def test_safe_child_refuses_to_leave_its_directory(self, tmp_path, name):
        with pytest.raises(ValueError):
            safe_child(tmp_path, name)

    def test_safe_child_allows_a_normal_name(self, tmp_path):
        assert safe_child(tmp_path, "contract-review.json").parent == tmp_path.resolve()

    def test_invalid_manifest_cannot_choose_its_own_filename(self, tmp_path):
        """An id that fails the schema is exactly the one that reached disk.

        The card for an invalid manifest is keyed by spec.metadata.id, and that
        used to be echoed back from the manifest verbatim — so a crafted id
        wrote a card outside snapshot/harnesses/.
        """
        from tools.registryctl.index import _error_card

        class P:
            project_path = "ai-platform/harnesses/legal/evil"
            web_url = "https://gitlab.example/evil"
            commit = "0" * 40
            visibility = "internal"
            default_branch = "main"
            archived = False
            files: dict = {}

        card = _error_card(P(), [], {"metadata": {"id": "../../../../pwned"}})
        assert card["spec"]["metadata"]["id"] == "evil"
        # The rejected value is kept for diagnosis, just not used as a path.
        assert card["spec"]["metadata"]["declared_id"] == "../../../../pwned"


# --------------------------------------------------------------------------
# The offline gate: it has to actually see the markup Hugo ships
# --------------------------------------------------------------------------
class TestOfflineGate:
    def test_unquoted_attributes_are_inspected(self):
        """--minify drops attribute quotes, so a quoted-only pattern saw nothing."""
        found = dict(_targets('<img src=https://evil.example/x.png alt=x>'))
        assert "https://evil.example/x.png" in found

    @pytest.mark.parametrize("markup", [
        '<img src=https://evil.example/x.png alt=x>',
        '<img src="https://evil.example/x.png" alt=x>',
        "<script src=https://cdn.example/a.js></script>",
        '<link rel=stylesheet href="https://cdn.example/b.css">',
        '<div style="background:url(https://evil.example/bg.png)"></div>',
    ])
    def test_external_assets_fail_the_build(self, tmp_path, markup):
        r = verify_site(_site(tmp_path, markup))
        assert any("external asset" in e for e in r.errors), r.errors

    def test_a_clickable_external_link_is_a_warning_not_an_error(self, tmp_path):
        """The registry indexes repositories it cannot host; links out are fine.

        Nothing fetches them to render the page, so they do not break offline
        use — even when the URL happens to end in .json.
        """
        r = verify_site(_site(
            tmp_path, '<a href="https://gitlab.acme.internal/x/config/schema.json">c</a>'))
        assert not [e for e in r.errors if "external asset" in e]

    def test_a_self_contained_page_passes(self, tmp_path):
        r = verify_site(_site(tmp_path, '<img src=/favicon.svg alt=x>'))
        assert not r.errors or all("broken internal" in e for e in r.errors)


# --------------------------------------------------------------------------
# Injected markup: the escaping is on, and stays on
# --------------------------------------------------------------------------
class TestInjectedMarkup:
    def test_inline_event_handler_fails_the_build(self, tmp_path):
        r = verify_site(_site(tmp_path, '<div onerror="alert(1)">x</div>'))
        assert any("inline event handler" in e for e in r.errors), r.errors

    def test_javascript_url_fails_the_build(self, tmp_path):
        r = verify_site(_site(tmp_path, '<a href="javascript:alert(1)">x</a>'))
        assert any("javascript:" in e for e in r.errors), r.errors

    def test_the_real_site_ships_neither(self):
        public = ROOT / "site/public"
        if not (public / "index.html").is_file():
            pytest.skip("site not built")
        r = verify_site(public)
        assert not [e for e in r.errors
                    if "inline event handler" in e or "javascript:" in e]


# --------------------------------------------------------------------------
# Accessibility gate: same minification blindness
# --------------------------------------------------------------------------
class TestAccessibilityGateSeesMinifiedHtml:
    def test_duplicate_unquoted_ids_are_caught(self, tmp_path):
        public = _site(tmp_path, "<h2 id=evaluation>a</h2><h3 id=evaluation>b</h3>")
        r = check_accessibility(public)
        assert any("duplicate element id" in e for e in r.errors), r.errors


# --------------------------------------------------------------------------
# Generated Mermaid source
# --------------------------------------------------------------------------
class TestMermaidLabels:
    def test_a_quote_cannot_break_out_of_a_label(self):
        assert '"' not in _mermaid_label('evil" onclick="x')

    def test_entities_cannot_be_forged(self):
        # '#' is escaped first, so an author writing "#quot;" cannot produce a
        # literal quote in the diagram source.
        assert _mermaid_label("#quot;") == "#35;quot;"

    def test_a_normal_name_is_readable(self):
        assert _mermaid_label("Contract Review") == "Contract Review"


# --------------------------------------------------------------------------
# Decompression limits on crawled artefacts
# --------------------------------------------------------------------------
class TestArtefactLimits:
    def test_an_oversized_report_is_skipped(self):
        import io
        import zipfile

        from tools.registryctl.crawl import MAX_REPORT_BYTES, _reports_from_zip

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            # Compresses to almost nothing, expands past the cap.
            z.writestr("evaluations/results/bomb.json", "0" * (MAX_REPORT_BYTES + 1))
            z.writestr("evaluations/results/ok.json", '{"ok":true}')
        reports = _reports_from_zip(buf.getvalue())
        assert reports == [{"ok": True}]
