# A.T.L.A.S. Digital Library: development roadmap

## Milestone 1 — Desktop foundation (validated on Windows)

- [x] Native PySide6 window
- [x] Browse existing physical folders
- [x] Read-only PDF inventory
- [x] First-page cover thumbnails
- [x] SQLite persistence
- [x] Search by book title / filename
- [x] Safe rescanning and missing-file status
- [x] Initial automated unit tests
- [x] Validate install, cover display, folder navigation, search, and persistence on user's Windows machine

## Milestone 2 — Built-in reader and catalog

- [x] Reader foundation: internal tabs, page navigation, zoom, table of contents, reading position
- [x] Native continuous/single-page switching and in-book text search (text-layer PDFs)
- [x] Validate reader on the user's Windows machine with real sourcebooks
- [x] Validate Ctrl+wheel zoom and Windows trackpad pinch on the user's hardware
- [x] Validate fixed-width bookshelf details sidebar on the user's hardware
- [x] Favorite books, reading-status controls, filters, and manual page bookmarks
- [x] Validate personal bookmarks/favorites and reading-status tracking on the user's Windows machine
- [x] Metadata editing: display title, author(s), publisher, edition, year, system, description, notes
- [x] Validate metadata editor on user's Windows machine
- [x] Virtual shelves, many-to-many book membership, and personal tags
- [x] Validate shelf management and tag filtering on user's Windows machine

## Milestone 3 — Full-text indexing and OCR (first implementation in progress)

- [x] SQLite FTS5 page-level search with sourcebook/page navigation
- [x] Extract existing text using PDFium
- [x] Offline OCR via locally installed Tesseract for textless/near-empty pages
- [x] Background indexer with cancellation, incremental updates, and per-page resume
- [x] Search modes: all words, exact phrase, and user-authored FTS5 Boolean/NEAR
- [x] Validate indexing, page links, OCR, and resume on user's Windows library
- [x] Opt-in OCR pass for text-rich pages containing substantial raster images
- [x] Preserve native text and avoid exact repeated lines when combining OCR results
- [x] Validate mixed-content OCR on a real book using the single-book scope
- [x] Optional small-raster-region OCR with independent resumable checkpoints
- [ ] Validate small-region OCR against actual mixed-content sourcebook pages
- [x] Initial fuzzy single-word and bounded regex search modes
- [x] Search metadata, tag and shelf filters applied before result limiting
- [x] FTS5 supports Boolean and NEAR proximity queries
- [x] Validate fuzzy/regex and metadata filters on user's Windows machine
- [ ] Expand approximate multi-word queries and additional search refinements
- [x] Show OCR-origin labels and highlighted excerpts in reader search-context panel
- [x] On-demand Tesseract TSV OCR word-position extraction, local cache and page preview
- [x] Verify word-position previews with the user's real scanned books
- [x] Add in-reader OCR-highlight page mode with return to unmodified native PDF view
- [x] Verify inline OCR-highlight mode on the user's Windows installation
- [ ] Native QPdfView continuous-scroll coordinate overlays (requires reliable page geometry mapping)
- [x] Read-only local search-index benchmark button and per-query timings
- [x] Validate benchmark control and report display on user's Windows installation
- [ ] Inspect real benchmark measurements and check index size on the user's large collection

## Milestone 4 — Organization and personalization

- Duplicate review, multiple editions, series completeness
- New-file inbox and non-destructive, approved physical moves
- Backups, data exports, catalog recovery
- Fully configurable bookshelf appearance and themes

## Milestone 5 — Windows distribution

- Build and test standalone Windows .exe and installer
- License review and third-party notices
- Update/migration safety, regression tests, release stabilization

## Only after the above: optional integrations

- Read-only Obsidian vault references and research backlinks
- Optional local conversational research using free local models
- These are explicitly not prerequisites for the completed standalone library.
