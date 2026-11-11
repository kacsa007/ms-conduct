# Publishing pipeline

`publishing/` turns `data/dossier.json` into a source-linked Markdown article with
self-contained SVG figures. It is a standard-library-only module and does not
fetch anything: the article can contain only what the reviewed dossier and its
named sources already support.

## Generate the review

```bash
python3 -m publishing
```

The default command writes:

```
article/generated/allianz-conduct-review.md
article/generated/assets/records-timeline.svg
article/generated/assets/evidence-mix.svg
article/generated/assets/jurisdictions.svg
```

The Markdown links to `assets/…`, so the generated directory can be published as
a unit. The command refuses to run if the dossier has structural errors, because
a broken source reference would produce a misleading article.

```bash
python3 -m publishing --help
python3 -m publishing --output article/generated/review.md
python3 -m publishing --stdout                  # inspect without writing files
python3 -m publishing --no-charts               # text-only draft
python3 -m publishing --no-verification         # drop the internal QA appendix
python3 -m publishing --title "…" --author "…" --contact "…"
```

`--allow-invalid` exists for debugging only. A draft produced that way carries an
**Unresolved source reference(s)** block and must not be published.

## What the writer guarantees

- Every record keeps its dossier evidence class, entity, date label and source
  ids. The writer does not upgrade an allegation into an outcome.
- Every source id resolves to a numbered entry in the source register, with a
  publisher link. Missing ids are named in the article and in `draft.warnings`.
- A reported allegation is rendered with the qualifier
  `reported claim — not a finding`.
- The response section quotes only passages where Allianz or a named
  representative is the speaker. Court outcomes and reporter attributions are
  not presented as company responses. Company disclosures are separately
  labelled as the company's own description.
- Uncited indexes and databases are listed under **Research resources**, so they
  are visible without implying that they support a specific sentence.
- Record text, source titles and contact details are escaped before entering
  Markdown. Links are restricted to public `http`/`https` URLs.
- Figures are generated from record metadata only. There are no stock images,
  illustrations, AI-generated images or remote assets.
- Output is deterministic for a fixed `generated_at`, which keeps diffs
  reviewable.

## Review before publication

1. Remove `## Appendix: publication checks (internal)`.
2. Resolve `Unresolved source reference` blocks and review every high-priority
   row in the appendix.
3. Check the response section against the current company position, and add any
   reply received before publication with its date and source.
4. Re-run `python3 -m evidence validate --snapshots` and `python3 -m pytest`.

The appendix and the automated evidence checks are review aids, not additional
findings.

## API

```python
from publishing import ArticleOptions, build_article

draft = build_article(dossier, snapshots, options=ArticleOptions(author="Desk"))
draft.markdown   # str
draft.assets     # {"records-timeline.svg": "<svg …>", …}
draft.stats      # records, sources, classes, words, integrity
draft.warnings   # dossier warnings plus unresolved source ids
draft.write("article/generated/review.md")
```
