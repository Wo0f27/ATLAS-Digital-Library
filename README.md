# A.T.L.A.S. Digital Library

A native, offline-first Windows digital bookshelf for your personal document collection.

**Development status:** Milestone 3 core search/OCR functionality validated on Windows; Milestone 4 organization and personalization is beginning. Advanced refinements and the standalone Windows executable remain unfinished.

## Currently implemented

- Native PySide6 desktop window with a cover-based book gallery.
- Read-only discovery of PDF files in a selected folder and its subfolders.
- PDF first-page cover thumbnails, cached outside the repository.
- SQLite catalog that survives application restarts.
- Search by filename/title and browse by existing physical folders.
- Choose **Root folder first** in the bookshelf sort menu to prioritize PDFs directly inside the selected library folder, with titles alphabetized within each group.
- Use **Folders → All Books → Library root (PDFs only)** to view only those top-level PDFs, excluding nested subfolders.
- Export a **Plan Folders** CSV of the current catalog with proposed subject classification, metadata, virtual shelf memberships and review flags; the export never moves PDFs.
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
- Optional **Inspect mixed text/image pages** setting for OCR on otherwise text-readable pages containing large raster regions, with a single-book index scope for safe testing.
- Additional opt-in **Inspect smaller embedded image regions** pass, cropping up to six candidate images per text-rich page without repeating large-page OCR.
- Source-aware search results and an embedded reader excerpt panel for text/OCR matches, with previous/next result navigation.
- **Benchmark Index** shows local indexed document/page counts, search index size and sample query timings.
- OCR result page-location previews with local, cached Tesseract TSV word bounding boxes and zoomable highlights.
- **Highlight in reader** for OCR hits: a temporary in-reader, zoomable page-rendering mode with OCR word highlights; **Return to PDF** restores the original native view and reading position.
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
Existing physical folders remain browseable under **Folders**. The **Root folder first** sort option changes only the display order, and the **Library root (PDFs only)** folder entry filters the bookshelf to documents stored directly in the selected library directory. Both preserve virtual shelves and file locations.

All book metadata, personal notes, virtual shelves, and tags are saved in the local
SQLite catalog and survive ordinary rescans. Deleting a virtual shelf only removes
its membership records, never the original PDFs.

## Root-folder sorting

The default bookshelf order is **Title A–Z** across all visible books. Choose
**Root folder first** to place PDFs directly inside your selected library root
before books stored in subfolders. Titles remain alphabetical within the
root-level and nested-book groups. This sort preference is saved between
application launches.

To see **only** PDFs placed directly in the root, select
**Folders → All Books → Library root (PDFs only)**. Select **All Books**
to restore the complete library. Both controls affect presentation only;
they never relocate, rename or modify PDFs.

## Safe physical-library reorganization (preview only)

The new **Plan Folders** control on the Bookshelf creates a review-only
CSV covering all PDF records currently marked *available* in the selected
library. Allow any active Rescan operation to finish first, then click
**Plan Folders** and save the file (preferably under Documents, outside the
physical sourcebook collection).

The classification system uses two subject-folder levels: Accession Desk,
Rulebook Stacks, Arcane Collection, Bestiary, World Atlas, Adventure Archives,
Campaign Systems, and Reference Desk. It classifies by **book topic**, not
publisher, author, series, or official/third-party status. Tags, metadata
and virtual shelves continue to handle these cross-cutting dimensions.

The CSV includes current source paths, title, bibliographic metadata, tags,
virtual shelves, a **proposed subject folder**, a proposed relative filename,
confidence, alternate possible subjects, and flags for obvious missing files,
changed files and duplicate destination names.

**Every proposed row is marked PENDING**, and the Approved Subject Folder is
blank. Some suggestions are inferred only from filenames or older subject
folders, not from page contents. Check every proposal and choose a final
subject; unresolved documents may remain under **Accession Desk / Awaiting
Classification**. Book contents and publisher/series metadata may inform a
human review, but the automated rules are only starting suggestions.

**No files, catalog paths, metadata, bookmarks, reader positions, or OCR
records are changed by this export.** No directories are created in the
library. Do not bulk-move PDFs in Windows Explorer before a separate, verified
relocation workflow has been developed and the inventory reviewed. The
relocation phase will require a database backup, source/destination
validation, conflict resolution, and coordinated path updates across all
SQLite tables and caches; this feature does *not* perform those operations.

The exported CSV contains local file paths and bibliographic fields, but not
PDF contents or private research notes. Review it before sharing it with
anyone. The output uses UTF-8 with a BOM so Excel can display accented
filenames and fantasy-book titles correctly.

## Full-text indexing and offline OCR

Open **Full-Text Search** and click **Index / Update Library**. The first run
can take longer for large collections. Later runs skip unchanged books. Each
page is committed independently so **Stop** and relaunch can resume. Indexing
takes place in a worker thread; the bookshelf and PDF reader remain usable.

Search options:
- **All words:** requires every word (order-independent).
- **Exact phrase:** requires the specified sequence.
- **Fuzzy word:** accepts one misspelled Latin-alphabet word (e.g. dragn);
  searches up to eight similar indexed terms sharing its initial letter.
  This is bounded spelling tolerance, not semantic search or phrase correction.
- **Regular expression:** runs a case-insensitive pattern against indexed page
  text with per-page timeouts and a total 12-second/50,000-page safety limit.
  It may return partial results and reports when it stops early.
- **Boolean / FTS5:** supports SQLite FTS5 syntax such as
  "dragon AND ancient", '"legendary actions"', '"green dragon" OR "blue dragon"',
  and "NEAR(dragon treasure, 6)".

Double-click a result to open the **specific page inside A.T.L.A.S.** The reader shows the source of indexed text (selectable PDF, offline OCR, or mixed), highlights matching words in a separate excerpt panel and provides Previous/Next result controls. On OCR or mixed results, **Locate OCR words** opens an additional zoomable page preview with Tesseract word boxes, where matching OCR words can be identified. This preview does not annotate or change the built-in PDF view. The optional **Highlight in reader** action displays the same OCR-highlighted page *within* the reader, and **Return to PDF** resumes the regular continuous-scroll PDF viewer without changing its zoom and page settings. Only books
in the active catalog root are displayed. You can combine full-text searches
with **tag**, **virtual shelf**, and **author/publisher/edition/system**
filters. Filter matching happens before limiting results to the top 150.
Select **Refresh filters** after changing book metadata while the Search tab
is already open. Search results use your custom
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

### Mixed text/image page OCR (opt-in)

The ordinary OCR setting automatically handles pages with no meaningful PDF
text. For pages with **both selectable PDF text and a large embedded raster image**,
tick **Inspect mixed text/image pages (slower, optional)** before indexing.

For a first test, choose a specific book under **Index scope** rather than
**All library books**. This detects PDF image objects covering at least about
10% of a page, rasterizes qualifying pages for offline Tesseract OCR, and
appends additional recognized lines to the existing extracted text. Normalized
lines already present in the original PDF text are skipped to reduce duplicates.

The first opt-in run may examine previously indexed pages of that book; later
runs skip completed checks on unchanged files. Pages where OCR fails remain
eligible for retry. The results remain in the existing local search index,
and you can search them immediately. The user source PDF is never edited.

A large illustration **does not guarantee that any text is present**. This
is a conservative, opt-in candidate heuristic, not precise image-region
segmentation. Processing fully illustrated books may take considerable time.
OCR-derived text is searchable in the library-wide Search tab, but not yet
selectable or visually highlighted inside the PDF view.

### Smaller image-region OCR (experimental)

If a sourcebook page contains selectable PDF text alongside **smaller raster
images carrying text**, first select that individual book under **Index scope**,
enable **OCR scanned pages**, and tick **Inspect smaller embedded image regions**.
This is a separate pass from **Inspect mixed text/image pages**; selecting
one turns the other off.

The detector finds PDF image objects occupying roughly **0.3%–10% of a page**,
renders the page once, and crops up to **six image regions per page** for local
Tesseract OCR. It retains existing native text and OCR results, appends only
new recognized lines, and records per-page checkpoints so unchanged books do
not need this extra pass again. Failed crops remain retryable. All work stays
offline, and the original sourcebook is never modified.

This is an experimental candidate detector, not an exhaustive page analyzer:
vector drawings, tiny images, rotated page content, or scanned words embedded
inside larger artwork may still be missed. Decorative images may yield no
useful text. Test on one sourcebook before choosing the entire library.

Current limitations:
- The detector examines embedded raster objects; some smaller or rotated
  text regions may still be missed.
- OCR quality depends on scan resolution, language, and unusual page layouts.
- Fuzzy search currently supports one Latin-alphabet word and is bounded by
  vocabulary size; it cannot recognize every misspelling or search phrases.
- Regular expressions operate within individual pages; slow or very broad
  patterns may stop at a reported safety limit.
- OCR search highlights inside the embedded reader are not implemented yet.
- This development build **does not bundle Tesseract**. Bundling OCR and language
  packs for the future Windows installer is part of the distribution phase.

Use **Benchmark Index** to measure your *own* indexed collection. The report shows
page counts, OCR/mixed page counts, database size (including SQLite write-ahead
log files), and elapsed milliseconds for five fixed sample FTS5 searches.
The test does not reprocess your sourcebooks. The report can be selected and
copied from the results panel. Query timings will vary by machine and cache.

### Locating OCR words on scanned pages

Open an OCR search result in the embedded reader, then select **Locate OCR words**.
A separate page preview shows the original page image and overlays recognized
word positions for the highlighted search terms.

Word positions are obtained with Tesseract's **TSV output** on demand and
stored in the existing local search database. They are automatically invalidated
when the source PDF changes; subsequent previews reuse cached boxes when the
source file is unchanged, even if Tesseract is temporarily unavailable.

OCR word alignment is approximate and depends on scan quality. The exact
highlight may be unavailable if a word was read differently than the search
index; in that case A.T.L.A.S. displays the page without guessing where to
draw a highlight. The current implementation uses a **separate zoomable page
preview**, not an overlay directly on Qt's scrollable PDF reader canvas.

This remains local/offline, does not rebuild your existing OCR index, and
does not edit the original document.

### Inline OCR highlighting in the reader

After opening an OCR or mixed-content search result, choose **Highlight in
reader**. The current PDF page is displayed with recognized-word overlays in
the same reading area as the native viewer; use Zoom +, Zoom -, Ctrl+scroll,
or Fit page to inspect it. Choose **Return to PDF** to restore the original
Qt PDF viewer with its existing reading position and zoom mode.

This is a *temporary image-based page mode*, not an overlay on Qt's native
continuous-scroll QPdfView, whose internal page geometry is not publicly
exposed. Regular page navigation or selecting a different search result
returns to the normal PDF view. No extra OCR work is required if valid cached
word boxes already exist. Nothing is written to the source PDFs or the
existing full-text index.

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

See docs/ROADMAP.md. The Windows-validated reader, metadata editor, virtual shelves, full-text/OCR search, in-reader OCR highlights and local index benchmarking are implemented. Further OCR/search enhancements are optional refinements; duplicate management, customizable themes, backups and the standalone Windows .exe remain planned. Obsidian / conversational integration is intentionally deferred until after the core app is complete.

## Technology and licensing

Python, PySide6 (Qt), pypdfium2 (PDFium), Pillow, SQLite, pytest. Before distributing a Windows executable, review the applicable upstream licenses and notices; free use does not waive license conditions.
