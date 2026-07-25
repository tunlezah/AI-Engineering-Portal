# Assets

Images rendered on the registry page. Kept small and few; the registry enforces a
400 KB per-page budget and blocks any external asset reference.

| File | Purpose | Constraints |
|---|---|---|
| `card.svg` | Registry card illustration | SVG, < 20 KB, no embedded fonts |
| `screenshot-findings.png` | Findings table in the legal portal | PNG, ≤ 1600px wide, < 180 KB, synthetic data only |
| `screenshot-signoff.png` | Sign-off queue view | as above |
| `og-card.png` | Preview card for internal chat unfurls | 1200×630, < 100 KB |

Rules, enforced by `harnessctl validate`:

1. **No real contract content in screenshots.** Use `examples/sample-msa.pdf`
   and the `CP-SYNTH-*` pseudonyms. Screenshots are the most common route for
   confidential data to escape into an internal-visible site.
2. **No external references.** No CDN URLs, no web fonts, no tracking pixels.
   The site is built and served offline.
3. **Alt text is mandatory** and lives next to the reference in Markdown, not
   here. A screenshot without alt text fails the accessibility check.
4. **Diagrams are Mermaid, not images.** Put them in `architecture/` as `.mmd`
   so they render natively, stay diffable and are validated in CI. Exported
   raster diagrams are rejected in review.
5. **Regenerate on UI change.** Stale screenshots are worse than none; the
   registry shows the file's last-modified date underneath.
