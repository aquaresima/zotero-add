# zotero-add

CLI tool to add papers to Zotero from the terminal.

Paste a URL (arXiv, PubMed, Nature, bioRxiv, DOI, …) and the paper lands in your Zotero library with metadata and PDF attached — no browser required.

```
zotero-add https://arxiv.org/abs/2507.05505
zotero-add https://arxiv.org/abs/2507.05505 --tags "SNN,auditory" --collection "AuditoryCircuit"
```

---

## How it works

Zotero's connector protocol exposes a local HTTP API (port 23119) that the browser extension normally talks to. `zotero-add` drives that API directly:

1. Sends the URL to a local [Zotero translation server](https://github.com/zotero/translation-server) (port 1969), which extracts metadata and attachment URLs using Zotero's own translators.
2. Posts the item to Zotero via `POST /connector/saveItems`.
3. Downloads the PDF and attaches it via `POST /connector/saveAttachment`.

A small patch to the translation server is required to preserve attachment URLs, which the upstream server strips before returning results.

---

## Requirements

- **Zotero** desktop app — must be running before you call `zotero-add`
- **Docker** (recommended) or **Node.js ≥ 18** — to run the translation server
- **Python ≥ 3.10** — standard library only, no dependencies

---

## Installation

```bash
git clone https://github.com/aquaresima/zotero-add.git
cd zotero-add
bash install.sh
```

`install.sh` does three things:

1. **Builds the patched translation server.**
   - With Docker: builds a local image `zotero-translation-server`.
   - With Node (no Docker): clones the upstream repo to `~/.local/opt/translation-server`, applies the patch, and runs `npm install`.
2. **Copies `zotero_add.py` to `~/.local/bin/zotero-add`** and makes it executable.
3. If using Node, rewrites the `TRANSLATION_SERVER_DIR` path in the installed script.

Make sure `~/.local/bin` is in your `PATH`.

---

## Usage

```
zotero-add <url|file.pdf> [options]

Options:
  --tags        Comma-separated tags to apply (e.g. "SNN,review")
  --collection  Destination collection name (partial match OK)
  --force       Skip duplicate check
```

**Examples:**

```bash
# Add from URL
zotero-add https://arxiv.org/abs/2507.05505

# Add with tags and collection
zotero-add https://doi.org/10.1038/s41593-021-00947-w --tags "review,dendrites" --collection "Dendrites"

# Add a local PDF — DOI/arXiv ID extracted automatically, metadata fetched
zotero-add paper.pdf
zotero-add paper.pdf --tags "SNN" --collection "Reading"

# Force-add even if duplicate detected
zotero-add https://arxiv.org/abs/2507.05505 --force
```

The translation server starts automatically if not running. Zotero must already be open.

### PDF import

When given a local PDF file, `zotero-add` scans the first 64 KB and last 32 KB of the file for a DOI or arXiv ID (no external dependencies — pure regex on raw bytes). If found, it fetches full metadata from the DOI or arXiv page, then attaches the local PDF file. If no identifier is found, it adds the PDF with a bare metadata stub.

---

## The patch

`translation-server.patch` modifies `src/webSession.js` in the upstream [zotero/translation-server](https://github.com/zotero/translation-server). The upstream `itemToAPIJSON` function strips attachment URLs from the response; this patch re-attaches them so that `zotero-add` can download and attach the PDF.

```diff
-    json.push(...Zotero.Utilities.Item.itemToAPIJSON(item));
+    let converted = Zotero.Utilities.Item.itemToAPIJSON(item);
+    // Preserve attachment URLs (stripped by itemToAPIJSON)
+    if (item.attachments && item.attachments.length) {
+        converted[0].attachments = item.attachments.map(a => ({
+            title: a.title, url: a.url, mimeType: a.mimeType,
+            snapshot: a.snapshot !== undefined ? a.snapshot : false
+        })).filter(a => a.url);
+    }
+    json.push(...converted);
```

---

## Duplicate detection

Before adding, `zotero-add` queries the Zotero local API for items matching the DOI or title. If a match is found, it exits without adding. Use `--force` to bypass.

---

## Troubleshooting

**"Zotero is not running"** — open the Zotero desktop app first.

**"Translation server failed to start"** — check Docker is running (`docker info`) or that Node is installed. Run `install.sh` again if the server directory is missing.

**"No items found"** — the URL may not be supported by Zotero's translators. Try the DOI instead of the abstract page.

**"Ambiguous collection"** — the `--collection` argument matches multiple collections. Use a more specific name.
