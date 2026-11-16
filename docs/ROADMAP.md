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
- [ ] Validate reader on the user's Windows machine with real sourcebooks
- Metadata editing, favorites, bookmarks, and reading status
- Virtual collections and tags independent of physical file paths

## Milestone 3 — Full-text indexing and OCR

- Full PDF text indexing with page-level results
- OCR for image-only and mixed PDFs, working offline
- Resumable OCR jobs, status/errors, original PDF preservation
- Advanced queries (phrases, Boolean, fuzzy/proximity search where supported)
- Performance benchmarking against a growing library

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
