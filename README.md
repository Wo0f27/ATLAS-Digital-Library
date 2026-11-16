# A.T.L.A.S. Digital Library

A native, offline-first Windows digital bookshelf for your personal document collection.

**Development status:** Milestone 2 (embedded reader, first increment). This is not yet the finished metadata/annotations system, OCR, full-text index, or Windows executable.

## Currently implemented

- Native PySide6 desktop window with a cover-based book gallery.
- Read-only discovery of PDF files in a selected folder and its subfolders.
- PDF first-page cover thumbnails, cached outside the repository.
- SQLite catalog that survives application restarts.
- Search by filename/title and browse by existing physical folders.
- Double-click a book to read it in a tab inside A.T.L.A.S.
- Embedded reader: previous/next page, page number jump, continuous or single-page mode, fit-to-width/page, custom zoom, PDF table of contents and in-book text search.
- Last page and viewing preferences saved automatically in the local catalog.
- Ctrl+mouse-wheel/trackpad scrolling adjusts zoom smoothly; native pinch is supported when Qt and the trackpad driver deliver a zoom gesture.
- The bookshelf details sidebar stays fixed-width; full file paths are in tooltips and a Copy full file path control.
- Optional open in the Windows default PDF application.
- Rescan the library for new, modified, and missing files.
- Unit tests for catalog persistence and scanning.

The app does **not** move, rename, delete, or edit your sourcebooks. It does **not** upload PDFs or extracted text to GitHub. In-book search currently works only for PDFs with an existing text layer; image-only books will need the planned OCR phase.

## Setup: Windows / VS Code

Open a terminal in the cloned repository and run:

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
    .\.venv\Scripts\python.exe -m atlas_library

If VS Code asks you to select an interpreter, choose .venv\Scripts\python.exe.

The default library location is:

    C:\Users\Ian\Downloads\Telegram Desktop

You can change the location with the **Choose Folder** button. It is remembered across launches. Your catalog and thumbnail cache are stored under the Windows per-user application data directory, not in this GitHub repository.

## Tests

    .\.venv\Scripts\python.exe -m pytest -q

## Privacy and safety

- The application reads PDF metadata and renders the first page for thumbnails.
- Original PDFs remain untouched; the catalog is stored separately.
- An unreadable or encrypted PDF is listed with a placeholder cover rather than being silently discarded.
- A failed scan does not delete catalog records.
- Changes to the selected library root do not automatically delete records from other roots.

## Upcoming work

See docs/ROADMAP.md. The built-in PDF reader foundation is available now. OCR, DocGoblin-style library-wide full-text search, metadata editor, virtual shelves, theming, and standalone Windows .exe are still planned features. Obsidian / conversational integration is intentionally deferred until after the core app is complete.

## Technology and licensing

Python, PySide6 (Qt), pypdfium2 (PDFium), Pillow, SQLite, pytest. Before distributing a Windows executable, review the applicable upstream licenses and notices; free use does not waive license conditions.
