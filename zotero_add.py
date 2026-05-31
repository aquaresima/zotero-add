#!/usr/bin/env python3
"""
Add a paper URL to Zotero via local translation server + connector protocol.
Auto-starts the translation server if not running. Detects duplicates via DOI.

Usage:
    zotero-add <url> [--tags tag1,tag2] [--collection "Collection Name"] [--force]

Examples:
    zotero-add https://arxiv.org/abs/2507.05505
    zotero-add https://arxiv.org/abs/2507.05505 --tags "SNN,auditory"
    zotero-add https://arxiv.org/abs/2507.05505 --collection "AuditoryCircuit"
    zotero-add https://www.nature.com/articles/s41593-026-02216-0 --tags "review" --collection "Dendrites"
    zotero-add <url> --force   # skip duplicate check
"""

import sys
import json
import time
import subprocess
import argparse
import urllib.request
import urllib.error
import urllib.parse

TRANSLATION_SERVER_DIR = "~/.local/opt/translation-server"
TRANSLATION_SERVER = "http://localhost:1969"
ZOTERO_BASE = "http://localhost:23119"
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


def start_translation_server() -> None:
    import os, shlex
    server_dir = os.path.expanduser(TRANSLATION_SERVER_DIR)
    print("Starting translation server...")
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
    """Return first matching Zotero item or None."""
    user_id = 5577542  # extracted from getSelectedCollection earlier
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


# ── translation ──────────────────────────────────────────────────────────────

def translate(url: str) -> list:
    req = urllib.request.Request(
        f"{TRANSLATION_SERVER}/web",
        data=url.encode(),
        headers={"Content-Type": "text/plain"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Add a paper to Zotero.")
    parser.add_argument("url", help="URL of the paper")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--collection", default="", help="Destination collection name (partial match OK)")
    parser.add_argument("--force", action="store_true", help="Skip duplicate check")
    args = parser.parse_args()

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    # Check Zotero is running
    if not server_running():
        print("Zotero is not running. Please open Zotero first.", file=sys.stderr)
        sys.exit(1)

    # Ensure translation server is up
    if not translation_server_running():
        start_translation_server()

    # Resolve collection
    target_id = None
    if args.collection:
        collections = get_collections()
        target_id = collections.get(args.collection)
        if not target_id:
            matches = {k: v for k, v in collections.items() if args.collection.lower() in k.lower()}
            if len(matches) == 1:
                name, target_id = next(iter(matches.items()))
                print(f"Collection matched: {name}")
            elif len(matches) > 1:
                print(f"Ambiguous collection '{args.collection}'. Matches: {list(matches.keys())}")
                sys.exit(1)
            else:
                print(f"Collection '{args.collection}' not found. Available:")
                for name in sorted(collections):
                    print(f"  {name}")
                sys.exit(1)

    # Translate
    print(f"Translating: {args.url}")
    try:
        items = translate(args.url)
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

    # Duplicate check
    if not args.force:
        dup = find_duplicate(doi=doi, title=title)
        if dup:
            print(f"\nDuplicate found: '{dup.get('title', '?')}' already in library.")
            print("Use --force to add anyway.")
            sys.exit(0)

    # Inject tags
    if tags:
        existing = {t["tag"] if isinstance(t, dict) else t for t in item.get("tags", [])}
        for tag in tags:
            if tag not in existing:
                item.setdefault("tags", []).append({"tag": tag, "type": 1})
        print(f"Tags: {tags}")

    pdf_attach = next(
        (a for a in item.get("attachments", []) if "pdf" in a.get("mimeType", "")), None
    )

    ITEM_ID = "item_001"
    item["id"] = ITEM_ID
    SESSION = f"zotero_add_{int(time.time())}"

    # saveItems
    status, _ = zotero_post("/connector/saveItems", {"items": [item], "sessionID": SESSION})
    if status not in (200, 201):
        print(f"saveItems failed: HTTP {status}", file=sys.stderr)
        sys.exit(1)
    print("Metadata saved.")

    # updateSession — collection + tags
    if target_id or tags:
        update_payload: dict = {"sessionID": SESSION}
        if target_id:
            update_payload["target"] = target_id
            print(f"Collection: {args.collection} ({target_id})")
        if tags:
            update_payload["tags"] = tags
        status, body = zotero_post("/connector/updateSession", update_payload)
        if status not in (200, 201):
            print(f"updateSession failed: HTTP {status} — {body.decode()}", file=sys.stderr)

    # Download + attach PDF
    if not pdf_attach or not pdf_attach.get("url"):
        print("No PDF found — metadata only.")
        return

    pdf_url = pdf_attach["url"]
    print(f"Downloading PDF...")
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

    metadata = json.dumps({
        "sessionID": SESSION,
        "parentItemID": ITEM_ID,
        "title": pdf_attach.get("title", "PDF"),
        "url": pdf_url,
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


if __name__ == "__main__":
    main()
