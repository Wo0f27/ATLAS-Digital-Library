# A.T.L.A.S. Digital Library

A native, offline-first Windows digital bookshelf for your personal document collection.

**Development status:** Milestone 3 early build (page-level full-text index, initial offline OCR support). The full DocGoblin-style search suite and standalone Windows executable are not finished.

## Currently implemented

- Native PySide6 desktop window with a cover-based book gallery.
- Read-only discovery of PDF files in a selected folder and its subfolders.
- PDF first-page cover thumbnails, cached outside the repository.
- SQLite catalog that survives application restarts.
- Search by filename/title and browse by existing physical folders.
- Double-click a book to read it in a tab inside A.T.L.A.S.
- Embedded reader: previous/next page, page number jump, continuous or single-page mode, fit-to-width/page, custom zoom, PDF table of contents and in-book text search.
- Last page and viewing preferences saved automatically in the local catalog.
- Personal named page bookmarks in a reader-side tab (save, reopen, remove).
- Favorite flag and reading status (Unread, Currently Reading, Finished) per book.
- Bookshelf filters for Favorites, Currently Reading, and Finished.
- Editable, personal book metadata: title, author(s), publisher, edition/printing, publication year, system, description, and research notes.
- Comma-separated tags; a book may have several tags, browsable from the Tags tab.
- Virtual shelves (create, rename, delete, and assign books to several shelves) that never move or duplicate PDFs.
- Full-Text Search tab: background indexing, page-linked search results, exact phrases, all-words matching, and advanced SQLite FTS5 expressions.
- Optional offline Tesseract OCR for image-only or nearly textless pages; OCR text is stored in the local search index.
- Resumable indexing: unchanged files are skipped and interrupted PDFs continue at the last committed page.
- Ctrl+mouse-wheel/trackpad scrolling adjusts zoom smoothly; native pinch is supported when Qt and the trackpad driver deliver a zoom gesture.
- The bookshelf details sidebar stays fixed-width; full file paths are in tooltips and a Copy full file path control.
- Optional open in the Windows default PDF application.
- Rescan the library for new, modified, and missing files.
- Unit tests for catalog persistence and scanning.

The app does **not** move, rename, delete, or edit your sourcebooks. It does **not** upload PDFs or extracted text to GitHub. In-book search currently works on PDFs with an existing text layer. OCR-derived text is searchable in the new library-wide index; adding an invisible PDF text layer to the reader is a separate future feature.

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

## Personal catalog organization

Select a book on the Bookshelf and choose **Edit book details / tags** to override its
display title or supply bibliographic details. Editing the display title changes only
how A.T.L.A.S. lists the book; its original filename is retained.

Select **Add to virtual shelves** to place the book into multiple shelves.
Use the **Shelves** tab in the left navigation to create, rename, or delete shelves.
The **Tags** tab lists tags found on books in the active library folder.
Existing physical folders remain browseable under **Folders**.

All book metadata, personal notes, virtual shelves, and tags are saved in the local
SQLite catalog and survive ordinary rescans. Deleting a virtual shelf only removes
its membership records, never the original PDFs.

## Full-text indexing and offline OCR

Open **Full-Text Search** and click **Index / Update Library**. The first run
can take longer for large collections. Later runs skip unchanged books. Each
page is committed independently so **Stop** and relaunch can resume. Indexing
takes place in a worker thread; the bookshelf and PDF reader remain usable.

Search options:
- **All words:** requires every word (order-independent).
- **Exact phrase:** requires the specified sequence.
- **Boolean / FTS5:** supports SQLite FTS5 syntax such as
  "dragon AND ancient", '"legendary actions"', '"green dragon" OR "blue dragon"',
  and "NEAR(dragon treasure, 6)".

Double-click a result to open the **specific page inside A.T.L.A.S.**. Only books
in the active catalog root are displayed. Search results use your custom
display titles where available. Searching currently matches page text, not
personal metadata fields.

For scanned PDFs, install the free **Tesseract OCR** engine separately for
development. On Windows, A.T.L.A.S. will check PATH and common install folders,
or you can select tesseract.exe using **Locate Tesseract...**. The language
defaults to "eng" and requires a corresponding installed Tesseract language
pack. No OCR program or language files are downloaded automatically.

If Tesseract is missing, A.T.L.A.S. still indexes existing PDF text and marks
textless pages as **Awaiting OCR**. After installing Tesseract, run
**Index / Update Library** again; only pending OCR pages of unchanged files
need reprocessing. Page images are temporary and cleaned after OCR.

Current limitations:
- OCR currently targets pages with empty or very short extractable text, not
  every image embedded in an otherwise text-rich page.
- OCR quality depends on scan resolution, language, and unusual page layouts.
- Fuzzy spelling matches, regex, advanced metadata facets, and OCR search
  highlights inside the reader are not implemented yet.
- This development build **does not bundle Tesseract**. Bundling OCR and language
  packs for the future Windows installer is part of the distribution phase.

The search index is a separate SQLite database in the Windows per-user app-data
directory. It contains extracted or OCR-recognized book text **only on your
own computer**; it is excluded from GitHub, along with original PDFs.

## Tests

    .\.venv\Scripts\python.exe -m pytest -q

## Privacy and safety

- The application reads PDF metadata and renders the first page for thumbnails.
- Original PDFs remain untouched; the catalog is stored separately.
- An unreadable or encrypted PDF is listed with a placeholder cover rather than being silently discarded.
- A failed scan does not delete catalog records.
- Changes to the selected library root do not automatically delete records from other roots.

## Upcoming work

See docs/ROADMAP.md. The built-in reader, metadata editor, virtual shelves, and initial page-level full-text search/OCR are implemented. Advanced search operators beyond SQLite FTS5, refinements to OCR and search navigation, theming, and the standalone Windows .exe remain planned. Obsidian / conversational integration is intentionally deferred until after the core app is complete.

## Technology and licensing

Python, PySide6 (Qt), pypdfium2 (PDFium), Pillow, SQLite, pytest. Before distributing a Windows executable, review the applicable upstream licenses and notices; free use does not waive license conditions.
