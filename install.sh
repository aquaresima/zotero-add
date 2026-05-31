#!/usr/bin/env bash
# Install zotero-add: builds the patched translation server and installs the CLI.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
SERVER_DIR="${SERVER_DIR:-$HOME/.local/opt/translation-server}"

# ── 1. translation server (Docker) ──────────────────────────────────────────
if command -v docker &>/dev/null; then
    echo "Building Docker image: zotero-translation-server"
    docker build -t zotero-translation-server "$REPO_DIR"
    echo "Run with: docker run -d --name zotero-ts -p 1969:1969 --restart unless-stopped zotero-translation-server"

# ── 2. translation server (Node, no Docker) ─────────────────────────────────
elif command -v node &>/dev/null; then
    echo "Docker not found — installing translation server with Node."
    mkdir -p "$SERVER_DIR"
    if [ ! -d "$SERVER_DIR/.git" ]; then
        git clone --depth=1 https://github.com/zotero/translation-server.git "$SERVER_DIR"
        git clone --depth=1 https://github.com/zotero/translators.git "$SERVER_DIR/modules/translators"
    fi
    cd "$SERVER_DIR"
    git apply --check "$REPO_DIR/translation-server.patch" 2>/dev/null && \
        git apply "$REPO_DIR/translation-server.patch" && \
        echo "Patch applied." || echo "Patch already applied — skipping."
    npm install --silent
    echo "Start server with: node $SERVER_DIR/src/server.js"

else
    echo "ERROR: Docker or Node.js required." >&2
    exit 1
fi

# ── 3. CLI script ────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
SCRIPT="$INSTALL_DIR/zotero-add"
cp "$REPO_DIR/zotero_add.py" "$SCRIPT"
chmod +x "$SCRIPT"

# Point the script at the right server dir if using Node
if ! command -v docker &>/dev/null; then
    sed -i.bak "s|TRANSLATION_SERVER_DIR = .*|TRANSLATION_SERVER_DIR = \"$SERVER_DIR\"|" "$SCRIPT"
    rm -f "$SCRIPT.bak"
fi

echo ""
echo "Installed: $SCRIPT"
echo "Usage:     zotero-add <url> [--tags tag1,tag2] [--collection 'Name']"
