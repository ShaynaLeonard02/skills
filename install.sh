#!/usr/bin/env bash
# Install (symlink or copy) a skill from this library into a target repo's
# .github/skills/ directory, where the assistant discovers project skills.
#
# Usage:
#   ./install.sh <skill-name> <target-repo-path> [--copy]
#
# Examples:
#   ./install.sh generate-hifmc-als /home/sleonarx/mmg
#   ./install.sh csr-checks /home/sleonarx/cnic --copy
#   ./install.sh --all /home/sleonarx/mmg          # install every skill in this library
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$SCRIPT_DIR/skills"

usage() {
    echo "Usage: $0 <skill-name|--all> <target-repo-path> [--copy]" >&2
    echo "  <skill-name>        one of: $(ls "$SKILLS_DIR" | tr '\n' ' ')" >&2
    echo "  --all               install every skill in this library" >&2
    echo "  <target-repo-path>  repo to install into (its .github/skills/ will be used)" >&2
    echo "  --copy              copy instead of symlink (default: symlink)" >&2
    exit 1
}

[ $# -lt 2 ] && usage

SKILL_NAME="$1"
TARGET_REPO="$2"
MODE="symlink"
[ "${3:-}" = "--copy" ] && MODE="copy"

if [ ! -d "$TARGET_REPO" ]; then
    echo "Error: target repo path does not exist: $TARGET_REPO" >&2
    exit 1
fi

DEST_BASE="$TARGET_REPO/.github/skills"
mkdir -p "$DEST_BASE"

install_one() {
    local name="$1"
    local src="$SKILLS_DIR/$name"
    local dest="$DEST_BASE/$name"

    if [ ! -d "$src" ]; then
        echo "Error: unknown skill '$name' (not found under $SKILLS_DIR)" >&2
        exit 1
    fi

    if [ -e "$dest" ] || [ -L "$dest" ]; then
        echo "Removing existing $dest"
        rm -rf "$dest"
    fi

    if [ "$MODE" = "copy" ]; then
        cp -r "$src" "$dest"
        echo "Copied  $src -> $dest"
    else
        ln -s "$src" "$dest"
        echo "Linked  $dest -> $src"
    fi
}

if [ "$SKILL_NAME" = "--all" ]; then
    for d in "$SKILLS_DIR"/*/; do
        install_one "$(basename "$d")"
    done
else
    install_one "$SKILL_NAME"
fi

echo "Done. Reload the assistant/window in '$TARGET_REPO' so it re-scans .github/skills/."
