#!/bin/sh
# tinyTouch Batch 0 installer for macOS.
set -eu

site_root="${TINYTOUCH_SITE_ORIGIN:-https://tinytouch.alpacaengineer.ing}"
case "$site_root" in
  https://tinytouch.alpacaengineer.ing|https://docs.tinytouch.dev) ;;
  *) echo 'TINYTOUCH_SITE_ORIGIN is not a trusted tinyTouch site.' >&2; exit 1 ;;
esac
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT HUP INT TERM

if [ "$(uname -s)" != 'Darwin' ]; then
  echo 'tinyTouch setup requires macOS.' >&2
  exit 1
fi

machine="$(uname -m)"
if [ "$machine" = 'x86_64' ] && [ "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null || true)" = '1' ]; then
  machine='arm64'
fi
case "$machine" in
  arm64) cli_key='macos-arm64'; expected_file='tinytouch-macos-arm64.tar.gz' ;;
  x86_64) cli_key='macos-x86_64'; expected_file='tinytouch-macos-x86_64.tar.gz' ;;
  *) echo "tinyTouch does not support this Mac architecture: $(uname -m)." >&2; exit 1 ;;
esac

curl -fsSL "$site_root/release.json" -o "$work_dir/release.json"
version="$(plutil -extract version raw -o - "$work_dir/release.json")"
release_file="$(plutil -extract "cli.$cli_key.file" raw -o - "$work_dir/release.json")"
release_sha256="$(plutil -extract "cli.$cli_key.sha256" raw -o - "$work_dir/release.json")"
case "$version" in
  [0-9]*.[0-9]*.[0-9]*-preprod) ;;
  *) echo 'The tinyTouch release version is invalid.' >&2; exit 1 ;;
esac
if [ "$release_file" != "$expected_file" ]; then
  echo 'The tinyTouch CLI filename is invalid.' >&2
  exit 1
fi
case "$release_sha256" in
  *[!0-9a-f]*|'') echo 'The tinyTouch CLI checksum is invalid.' >&2; exit 1 ;;
esac
if [ "${#release_sha256}" -ne 64 ]; then
  echo 'The tinyTouch CLI checksum is invalid.' >&2
  exit 1
fi
release_url="$site_root/cli/$release_file?sha256=$release_sha256"

path_contains() {
  case ":$PATH:" in
    *":$1:"*) return 0 ;;
    *) return 1 ;;
  esac
}

if [ -n "${TINYTOUCH_INSTALL_DIR:-${TINTOUCH_INSTALL_DIR:-}}" ]; then
  install_dir="${TINYTOUCH_INSTALL_DIR:-$TINTOUCH_INSTALL_DIR}"
elif existing_command="$(command -v tinytouch 2>/dev/null)"; then
  case "$existing_command" in
    "$HOME/.local/bin/tinytouch"|/opt/homebrew/bin/tinytouch|/usr/local/bin/tinytouch)
      install_dir="${existing_command%/tinytouch}"
      ;;
    *) existing_command='' ;;
  esac
fi

if [ -z "${install_dir:-}" ]; then
  for candidate in "$HOME/.local/bin" /opt/homebrew/bin /usr/local/bin; do
    if path_contains "$candidate"; then
      install_dir="$candidate"
      break
    fi
  done
fi

if [ -z "${install_dir:-}" ]; then
  echo 'No safe install directory is present on this Terminal PATH.' >&2
  echo 'Set TINYTOUCH_INSTALL_DIR to a directory already on PATH and run the installer again.' >&2
  exit 1
fi

echo 'Installing tinyTouch...'
curl -fsSL "$release_url" -o "$work_dir/tinytouch.tar.gz"
actual_sha256="$(shasum -a 256 "$work_dir/tinytouch.tar.gz" | awk '{print $1}')"
if [ "$actual_sha256" != "$release_sha256" ]; then
  echo 'Download checksum did not match. Stopping.' >&2
  echo 'Contact tinytouch@alpacaengineer.ing if this continues.' >&2
  exit 1
fi

tar -C "$work_dir" -xzf "$work_dir/tinytouch.tar.gz"
test -x "$work_dir/tinytouch/tinytouch"
test -d "$work_dir/tinytouch/_internal"
support_dir="$HOME/Library/Application Support/tinyTouch"
bundle="$support_dir/cli-$(printf %.16s "$release_sha256")"
mkdir -p "$support_dir"
if [ ! -d "$bundle" ]; then
  mv "$work_dir/tinytouch" "$bundle"
fi
xattr -dr com.apple.quarantine "$bundle" 2>/dev/null || true

if { [ -d "$install_dir" ] && [ -w "$install_dir" ]; } || \
   { [ ! -e "$install_dir" ] && [ -w "${install_dir%/*}" ]; }; then
  mkdir -p "$install_dir"
  ln -sfn "$bundle/tinytouch" "$install_dir/.tinytouch.new"
  mv -f "$install_dir/.tinytouch.new" "$install_dir/tinytouch"
else
  echo "Installing to $install_dir requires your Mac administrator password."
  sudo mkdir -p "$install_dir"
  sudo ln -sfn "$bundle/tinytouch" "$install_dir/.tinytouch.new"
  sudo mv -f "$install_dir/.tinytouch.new" "$install_dir/tinytouch"
fi

echo 'tinyTouch installed. Run: tinytouch setup'
