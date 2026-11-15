#!/usr/bin/env bash
# Symlink the commands into ~/.local/bin, so edits in the repo are live.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
target="${1:-$HOME/.local/bin}"
mkdir -p "$target"
for name in hypr-windows ws-overview; do
    ln -sf "$repo/bin/$name" "$target/$name"
    echo "$target/$name -> $repo/bin/$name"
done
case ":$PATH:" in
    *":$target:"*) ;;
    *) echo "note: $target is not on your PATH" ;;
esac
