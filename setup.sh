#!/bin/sh
# no-errands stage-0 (Linux/macOS)
# 唯一職責：找到「真的能跑」的 Python >= 3.9，交給 setup.py；找不到才抓 uv 單檔。
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

try_py() {
    v=$("$1" --version 2>&1) || return 1
    case "$v" in
        Python\ 3.[0-8].*) return 1 ;;
        Python\ 3.*|Python\ [4-9]*) return 0 ;;
        *) return 1 ;;
    esac
}

for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && try_py "$cand"; then
        exec "$cand" "$repo/setup.py" "$@"
    fi
done

echo '找不到可用的 Python >= 3.9，下載 uv 單檔（user-scope）...'
home_dir="${NO_ERRANDS_HOME:-${HOME}/.no-errands}"
prev=""
for a in "$@"; do
    [ "$prev" = "--home" ] && home_dir="$a"
    prev="$a"
done
bin="$home_dir/bin"; mkdir -p "$bin"
os=$(uname -s); arch=$(uname -m)
case "$os-$arch" in
    Linux-x86_64)  key='linux-x86_64' ;;
    Darwin-arm64)  key='darwin-arm64' ;;
    Darwin-x86_64) key='darwin-x86_64' ;;
    *) echo "未支援平台 $os-$arch，請手動安裝 Python 3.9+ 再跑 setup.py"; exit 1 ;;
esac
# 這條路代表機器上沒有 python 可解析 JSON：用 awk 取 uv 區塊內該平台的 url/sha256
url=$(awk '/"uv"/{f=1} f && /"'"$key"'"/{g=1} g && /"url"/{print; exit}' "$repo/versions.json" | sed 's/.*"url": *"//; s/".*//')
sha=$(awk '/"uv"/{f=1} f && /"'"$key"'"/{g=1} g && /"sha256"/{print; exit}' "$repo/versions.json" | sed 's/.*"sha256": *"//; s/".*//')
tmp=$(mktemp -d)
curl -fsSL "$url" -o "$tmp/uv.tar.gz"
got=$( (sha256sum "$tmp/uv.tar.gz" 2>/dev/null || shasum -a 256 "$tmp/uv.tar.gz") | cut -d' ' -f1)
[ "$got" = "$sha" ] || { echo "uv sha256 不符（got $got），中止"; exit 1; }
tar -xzf "$tmp/uv.tar.gz" -C "$tmp"
find "$tmp" -type f -name uv -exec mv {} "$bin/uv" \;
chmod +x "$bin/uv"; rm -rf "$tmp"
exec "$bin/uv" run --no-project "$repo/setup.py" "$@"
