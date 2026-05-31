#!/usr/bin/env python3
"""
Add a paper URL or local PDF to Zotero via local translation server + connector protocol.
Auto-starts the translation server if not running. Detects duplicates via DOI.

Usage:
    zotero-add <url>  [--tags tag1,tag2] [--collection "Collection Name"] [--force]
    zotero-add <file.pdf> [--tags tag1,tag2] [--collection "Collection Name"] [--force]

Examples:
    zotero-add https://arxiv.org/abs/2507.05505
    zotero-add https://arxiv.org/abs/2507.05505 --tags "SNN,auditory"
    zotero-add https://arxiv.org/abs/2507.05505 --collection "AuditoryCircuit"
    zotero-add paper.pdf
    zotero-add paper.pdf --tags "review" --collection "Dendrites"
    zotero-add <url> --force   # skip duplicate check
"""

import sys
import json
import time
import re
import os
import subprocess
import argparse
import urllib.request
import urllib.error
import urllib.parse

TRANSLATION_SERVER_DIR = "~/.local/opt/translation-server"
TRANSLATION_SERVER = "http://localhost:1969"
ZOTERO_BASE = "http://localhost:23119"
DOCKER_IMAGE = "zotero-translation-server"
API_VERSION = "3"
CONNECTOR_HEADERS = {
    "Content-Type": "application/json",
    "X-Zotero-Connector-API-Version": API_VERSION,
}


# ── server management ────────────────────────────────────────────────────────

def server_running() -> bool:
    try:
        urllib.request.urlopen(f"{ZOTERO_BASE}/connector/ping", timeout=2)
        return True
    except Exception:
        return False


def translation_server_running() -> bool:
    try:
        urllib.request.urlopen(f"{TRANSLATION_SERVER}/web", timeout=2)
    except urllib.error.HTTPError:
        return True  # server up, just rejected empty request
    except Exception:
        return False
    return True


def _docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def start_translation_server() -> None:
    print("Starting translation server...")
    if _docker_available():
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=zotero-ts", "--format", "{{.Status}}"],
            capture_output=True, text=True,
        )
        if "Exited" in result.stdout:
            subprocess.Popen(["docker", "start", "zotero-ts"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(
                ["docker", "run", "-d", "--name", "zotero-ts",
                 "-p", "1969:1969", "--restart", "unless-stopped", DOCKER_IMAGE],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    else:
        server_dir = os.path.expanduser(TRANSLATION_SERVER_DIR)
        if not os.path.isdir(server_dir):
            print(f"Translation server not found at {server_dir}.", file=sys.stderr)
            print("Run install.sh first.", file=sys.stderr)
            sys.exit(1)
        subprocess.Popen(
            ["node", "src/server.js"],
            cwd=server_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    for _ in range(15):
        time.sleep(1)
        if translation_server_running():
            print("Translation server ready.")
            return
    print("Translation server failed to start.", file=sys.stderr)
    sys.exit(1)


# ── zotero API helpers ───────────────────────────────────────────────────────

def zotero_post(endpoint: str, data: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"{ZOTERO_BASE}{endpoint}",
        data=json.dumps(data).encode(),
        headers=CONNECTOR_HEADERS,
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def get_collections() -> dict[str, str]:
    req = urllib.request.Request(
        f"{ZOTERO_BASE}/connector/getSelectedCollection",
        data=b"{}",
        headers=CONNECTOR_HEADERS,
    )
    resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
    return {t["name"]: t["id"] for t in resp.get("targets", [])}


def find_duplicate(doi: str = "", title: str = "") -> dict | None:
    user_id = 5577542
    queries = []
    if doi:
        queries.append(urllib.parse.quote(doi, safe=""))
    if title:
        queries.append(urllib.parse.quote(title[:60], safe=""))

    for q in queries:
        url = f"{ZOTERO_BASE}/api/users/{user_id}/items?q={q}&qmode=everything&limit=5&itemType=-attachment"
        try:
            resp = json.loads(urllib.request.urlopen(url, timeout=5).read())
        except Exception:
            continue
        for item in resp:
            d = item.get("data", {})
            if doi and doi.lower() in d.get("DOI", "").lower():
                return d
            if title and title.lower()[:40] in d.get("title", "").lower():
                return d
    return None


def resolve_collection(collection_arg: str) -> str | None:
    collections = get_collections()
    target_id = collections.get(collection_arg)
    if target_id:
        return target_id
    matches = {k: v for k, v in collections.items() if collection_arg.lower() in k.lower()}
    if len(matches) == 1:
        name, target_id = next(iter(matches.items()))
        print(f"Collection matched: {name}")
        return target_id
    elif len(matches) > 1:
        print(f"Ambiguous collection '{collection_arg}'. Matches: {list(matches.keys())}")
        sys.exit(1)
    else:
        print(f"Collection '{collection_arg}' not found. Available:")
        for name in sorted(collections):
            print(f"  {name}")
        sys.exit(1)


# ── auto-tag extraction ──────────────────────────────────────────────────────

def fetch_tags_from_crossref(doi: str) -> list[str]:
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "zotero-add/1.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        subjects = data.get("message", {}).get("subject", [])
        return [s.lower() for s in subjects if s]
    except Exception:
        return []


def fetch_tags_from_arxiv(arxiv_id: str) -> list[str]:
    url = f"https://export.arxiv.org/abs/{arxiv_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "zotero-add/1.0"})
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="replace")
        # arXiv subjects are in <span class="primary-subject"> and <span class="secondary-subject">
        tags = re.findall(r'class="[^"]*subject[^"]*"[^>]*>([^<(]+)', html)
        return [t.strip().lower() for t in tags if t.strip()]
    except Exception:
        return []


def extract_keywords_from_pdf_text(path: str) -> list[str]:
    """Look for a Keywords: line in the first 64 KB of the PDF."""
    with open(path, "rb") as f:
        head = f.read(65536)
    text = head.decode("latin-1")
    m = re.search(r'[Kk]eywords?\s*[:\-—]\s*([^\n]{5,200})', text)
    if not m:
        return []
    raw = m.group(1)
    # Split on common delimiters: semicolons, commas, bullets
    parts = re.split(r'[;,•·]', raw)
    return [p.strip().lower() for p in parts if 3 < len(p.strip()) < 60]


def auto_tags(doi: str = "", arxiv_id: str = "", pdf_path: str = "") -> list[str]:
    tags: list[str] = []
    if arxiv_id:
        tags = fetch_tags_from_arxiv(arxiv_id)
    if not tags and doi:
        tags = fetch_tags_from_crossref(doi)
    if not tags and pdf_path:
        tags = extract_keywords_from_pdf_text(pdf_path)
    seen: set[str] = set()
    result = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


# ── translation ──────────────────────────────────────────────────────────────

def translate(url: str) -> list:
    req = urllib.request.Request(
        f"{TRANSLATION_SERVER}/web",
        data=url.encode(),
        headers={"Content-Type": "text/plain"},
    )
    items = json.loads(urllib.request.urlopen(req, timeout=30).read())
    for item in items:
        _fix_item_type(item, url)
    return items


def translate_doi(doi: str) -> list:
    """Use /search endpoint for DOI lookup — more reliable than /web with doi.org."""
    req = urllib.request.Request(
        f"{TRANSLATION_SERVER}/search",
        data=doi.encode(),
        headers={"Content-Type": "text/plain"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _fix_item_type(item: dict, url: str) -> None:
    if item.get("itemType") != "webpage":
        return
    m = re.search(r"arxiv\.org/abs/([\d.]+)", url)
    if m:
        item["itemType"] = "preprint"
        item["repository"] = "arXiv"
        item["archiveID"] = f"arXiv:{m.group(1)}"
        item.pop("websiteTitle", None)
        item.pop("accessDate", None)
        return
    if re.search(r"(biorxiv|medrxiv)\.org", url):
        item["itemType"] = "preprint"
        item["repository"] = "bioRxiv" if "biorxiv" in url else "medRxiv"
        item.pop("websiteTitle", None)
        item.pop("accessDate", None)


# ── PDF import ───────────────────────────────────────────────────────────────

DOI_RE = re.compile(r'(?:doi[:/][\s]*)?(10\.\d{4,}/[^\s\]\[\"<>{|}\\^`\x00-\x1f]+)', re.ASCII | re.IGNORECASE)
ARXIV_RE = re.compile(r'arxiv[.:/\s]+(\d{4}\.\d{4,5}(?:v\d+)?)', re.IGNORECASE)


def extract_doi_from_pdf(path: str) -> str | None:
    """Scan raw PDF bytes for a DOI or arXiv ID. No external dependencies."""
    with open(path, "rb") as f:
        content = f.read()
    text = content.decode("latin-1")

    # 1. XMP dc:identifier — most reliable, paper's own DOI
    m = re.search(r'<dc:identifier>\s*doi:(10\.\d{4,}/[^<\s]+)', text, re.IGNORECASE)
    if m:
        return m.group(1).rstrip(".)>,;")

    # 2. PDF /doi entry in document info dict
    m = re.search(r'/doi\s*\(doi:(10\.\d{4,}/[^)]+)\)', text, re.IGNORECASE)
    if m:
        return m.group(1).rstrip(".)>,;")

    # 3. arXiv XMP identifier
    m = re.search(r'<dc:identifier>\s*arxiv[.:/\s]+(\d{4}\.\d{4,5})', text, re.IGNORECASE)
    if m:
        return f"arxiv:{m.group(1)}"

    # 4. Scan first 128 KB for DOI (avoid false positives from references)
    head = text[:131072]
    m = DOI_RE.search(head)
    if m:
        return m.group(1).rstrip(".)>,;")

    # 5. arXiv ID in header
    m = ARXIV_RE.search(head)
    if m:
        return f"arxiv:{m.group(1).split('v')[0]}"

    return None


def save_item_with_local_pdf(item: dict, pdf_path: str, session: str) -> None:
    """Save metadata + attach a local PDF file."""
    ITEM_ID = "item_001"
    item["id"] = ITEM_ID

    status, _ = zotero_post("/connector/saveItems", {"items": [item], "sessionID": session})
    if status not in (200, 201):
        print(f"saveItems failed: HTTP {status}", file=sys.stderr)
        sys.exit(1)
    print("Metadata saved.")

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    metadata = json.dumps({
        "sessionID": session,
        "parentItemID": ITEM_ID,
        "title": os.path.basename(pdf_path),
        "url": "",
    })
    attach_req = urllib.request.Request(
        f"{ZOTERO_BASE}/connector/saveAttachment",
        data=pdf_bytes,
        headers={
            "Content-Type": "application/pdf",
            "Content-Length": str(len(pdf_bytes)),
            "X-Metadata": metadata,
            "X-Zotero-Connector-API-Version": API_VERSION,
        },
    )
    try:
        urllib.request.urlopen(attach_req, timeout=60)
        print("PDF attached. Done.")
    except urllib.error.HTTPError as e:
        print(f"saveAttachment failed: HTTP {e.code} — {e.read().decode()}", file=sys.stderr)


# ── shared save logic ────────────────────────────────────────────────────────

def save_item(item: dict, tags: list, target_id: str | None,
              collection_arg: str, session: str,
              pdf_bytes: bytes | None = None, pdf_meta: dict | None = None) -> None:
    ITEM_ID = "item_001"
    item["id"] = ITEM_ID

    if tags:
        existing = {t["tag"] if isinstance(t, dict) else t for t in item.get("tags", [])}
        for tag in tags:
            if tag not in existing:
                item.setdefault("tags", []).append({"tag": tag, "type": 1})
        print(f"Tags: {tags}")

    status, _ = zotero_post("/connector/saveItems", {"items": [item], "sessionID": session})
    if status not in (200, 201):
        print(f"saveItems failed: HTTP {status}", file=sys.stderr)
        sys.exit(1)
    print("Metadata saved.")

    if target_id or tags:
        update_payload: dict = {"sessionID": session}
        if target_id:
            update_payload["target"] = target_id
            print(f"Collection: {collection_arg} ({target_id})")
        if tags:
            update_payload["tags"] = tags
        status, body = zotero_post("/connector/updateSession", update_payload)
        if status not in (200, 201):
            print(f"updateSession failed: HTTP {status} — {body.decode()}", file=sys.stderr)

    if pdf_bytes is None and pdf_meta is None:
        print("No PDF — metadata only.")
        return

    if pdf_bytes is None and pdf_meta:
        # Download from URL
        pdf_url = pdf_meta["url"]
        print("Downloading PDF...")
        try:
            pdf_req = urllib.request.Request(
                pdf_url,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
            )
            pdf_bytes = urllib.request.urlopen(pdf_req, timeout=60).read()
            print(f"Downloaded {len(pdf_bytes):,} bytes")
        except urllib.error.URLError as e:
            print(f"PDF download failed: {e} — metadata only.", file=sys.stderr)
            return

    # Give Zotero time to finish saving the parent item before attaching
    time.sleep(2)

    metadata = json.dumps({
        "sessionID": session,
        "parentItemID": ITEM_ID,
        "title": (pdf_meta or {}).get("title", "PDF"),
        "url": (pdf_meta or {}).get("url", ""),
    })
    attach_req = urllib.request.Request(
        f"{ZOTERO_BASE}/connector/saveAttachment",
        data=pdf_bytes,
        headers={
            "Content-Type": "application/pdf",
            "Content-Length": str(len(pdf_bytes)),
            "X-Metadata": metadata,
            "X-Zotero-Connector-API-Version": API_VERSION,
        },
    )
    try:
        urllib.request.urlopen(attach_req, timeout=60)
        print("PDF saved. Done.")
    except urllib.error.HTTPError as e:
        print(f"saveAttachment failed: HTTP {e.code} — {e.read().decode()}", file=sys.stderr)


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Add a paper (URL or PDF) to Zotero.")
    parser.add_argument("input", help="URL of the paper or path to a local PDF")
    parser.add_argument("--tags", default="", help="Comma-separated tags (auto-generated if omitted)")
    parser.add_argument("--collection", default="", help="Destination collection name (partial match OK)")
    parser.add_argument("--force", action="store_true", help="Skip duplicate check")
    parser.add_argument("--no-auto-tags", action="store_true", help="Disable automatic tag extraction")
    args = parser.parse_args()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    is_pdf = os.path.isfile(args.input) and args.input.lower().endswith(".pdf")

    if not server_running():
        print("Zotero is not running. Please open Zotero first.", file=sys.stderr)
        sys.exit(1)

    if not translation_server_running():
        start_translation_server()

    target_id = resolve_collection(args.collection) if args.collection else None
    session = f"zotero_add_{int(time.time())}"

    # ── PDF path ──────────────────────────────────────────────────────────────
    if is_pdf:
        pdf_path = os.path.abspath(args.input)
        print(f"PDF: {pdf_path}")

        identifier = extract_doi_from_pdf(pdf_path)
        if not identifier:
            print("No DOI or arXiv ID found in PDF — adding without metadata.", file=sys.stderr)
            bare_title = os.path.splitext(os.path.basename(pdf_path))[0]
            item = {
                "itemType": "journalArticle",
                "title": bare_title,
                "creators": [],
                "tags": [{"tag": "no DOI found", "type": 1}],
            }
            tags = tags or []
            if "no DOI found" not in tags:
                tags = ["no DOI found"] + tags
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
            save_item(item, tags, target_id, args.collection, session,
                      pdf_bytes=pdf_bytes,
                      pdf_meta={"title": os.path.basename(pdf_path), "url": f"file://{pdf_path}"})
            return

        if identifier.startswith("arxiv:"):
            arxiv_id = identifier[6:]
            print(f"Found arXiv ID: {arxiv_id}")
            print(f"Fetching metadata...")
            try:
                items = translate(f"https://arxiv.org/abs/{arxiv_id}")
            except urllib.error.URLError as e:
                print(f"Metadata lookup failed: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Found DOI: {identifier}")
            print(f"Fetching metadata...")
            try:
                items = translate_doi(identifier)
            except urllib.error.URLError as e:
                print(f"Metadata lookup failed: {e}", file=sys.stderr)
                sys.exit(1)

        if not items:
            print("No metadata found — adding PDF without metadata.", file=sys.stderr)
            items = [{"itemType": "journalArticle",
                      "title": os.path.splitext(os.path.basename(pdf_path))[0],
                      "creators": [], "tags": []}]

        item = items[0]
        title = item.get("title", "")
        doi = item.get("DOI", identifier if not identifier.startswith("arxiv:") else "")
        authors = [c.get("lastName", "") for c in item.get("creators", [])[:3]]
        print(f"Found: {title}")
        if authors:
            print(f"Authors: {', '.join(authors)}")

        if not args.force:
            dup = find_duplicate(doi=doi, title=title)
            if dup:
                print(f"\nDuplicate found: '{dup.get('title', '?')}' already in library.")
                print("Use --force to add anyway.")
                sys.exit(0)

        if not tags and not args.no_auto_tags:
            arxiv_id_clean = identifier[6:] if identifier.startswith("arxiv:") else ""
            doi_clean = "" if identifier.startswith("arxiv:") else identifier
            # Use tags from translated item first, then external sources
            item_tags = [t["tag"] if isinstance(t, dict) else t for t in item.get("tags", [])]
            tags = item_tags or auto_tags(doi=doi_clean, arxiv_id=arxiv_id_clean, pdf_path=pdf_path)
            if tags:
                print(f"Auto-tags: {tags}")

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        # Strip remote attachments — we have the local PDF
        item.pop("attachments", None)
        save_item(item, tags, target_id, args.collection, session,
                  pdf_bytes=pdf_bytes,
                  pdf_meta={"title": os.path.basename(pdf_path), "url": f"file://{pdf_path}"})
        return

    # ── URL path ──────────────────────────────────────────────────────────────
    print(f"Translating: {args.input}")
    try:
        items = translate(args.input)
    except urllib.error.URLError as e:
        print(f"Translation failed: {e}", file=sys.stderr)
        sys.exit(1)

    if not items:
        print("No items found.", file=sys.stderr)
        sys.exit(1)

    item = items[0]
    title = item.get("title", "")
    doi = item.get("DOI", "")
    authors = [c.get("lastName", "") for c in item.get("creators", [])[:3]]
    print(f"Found: {title}")
    print(f"Authors: {', '.join(authors)}")
    if doi:
        print(f"DOI: {doi}")

    if not args.force:
        dup = find_duplicate(doi=doi, title=title)
        if dup:
            print(f"\nDuplicate found: '{dup.get('title', '?')}' already in library.")
            print("Use --force to add anyway.")
            sys.exit(0)

    if not tags and not args.no_auto_tags:
        arxiv_m = re.search(r"arxiv\.org/abs/([\d.]+)", args.input)
        tags = auto_tags(
            doi=doi,
            arxiv_id=arxiv_m.group(1) if arxiv_m else "",
        )
        if tags:
            print(f"Auto-tags: {tags}")

    pdf_attach = next(
        (a for a in item.get("attachments", []) if "pdf" in a.get("mimeType", "")), None
    )
    item.pop("attachments", None)

    save_item(item, tags, target_id, args.collection, session,
              pdf_bytes=None,
              pdf_meta={"title": pdf_attach.get("title", "PDF"), "url": pdf_attach["url"]}
              if pdf_attach else None)


if __name__ == "__main__":
    main()
