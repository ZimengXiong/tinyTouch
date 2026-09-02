#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h:h}"
build_dir="$project_dir/build/distribution"
dist_dir="$project_dir/dist"
venv_python="$project_dir/.venv/bin/python"
version="${TINYTOUCH_VERSION:-$(tr -d '[:space:]' < "$project_dir/VERSION")}"
output="$dist_dir/tinytouch.tar.gz"
signing_identity="${TINYTOUCH_SIGNING_IDENTITY:-}"
release_build="${TINYTOUCH_RELEASE_BUILD:-0}"
notary_profile="${TINYTOUCH_NOTARY_PROFILE:-}"

if [[ ! -x "$venv_python" ]]; then
  python3 -m venv "$project_dir/.venv"
fi

# PEP 517 backends installed in the build environment are executables. Add the
# environment's bin directory so source distributions can invoke them.
export PATH="$project_dir/.venv/bin:$PATH"

"$venv_python" -m pip install -q --require-hashes \
  -r "$project_dir/software/macos-helper/requirements-bootstrap.txt"
"$venv_python" -m pip install -q --no-build-isolation --require-hashes \
  -r "$project_dir/software/macos-helper/requirements-release.txt"

rm -rf "$build_dir"
mkdir -p "$build_dir" "$dist_dir"

"$venv_python" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --strip \
  --optimize 2 \
  --name tinytouch \
  --distpath "$build_dir/bin" \
  --workpath "$build_dir/work-cli" \
  --specpath "$build_dir/spec-cli" \
  --paths "$project_dir/software/macos-helper" \
  --hidden-import tinytouch_helper \
  --hidden-import tinytouch_keychain \
  --hidden-import tinytouch_runtime \
  --hidden-import serial.tools.list_ports \
  --collect-all esptool \
  --add-data "$project_dir/VERSION:." \
  "$project_dir/tinytouch"

if [[ -z "$signing_identity" ]]; then
  signing_identity="$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application:[^"]*\)".*/\1/p' | head -n 1)"
fi
if [[ -z "$signing_identity" ]]; then
  signing_identity="$(security find-identity -v -p codesigning | sed -n 's/.*"\(Apple Development:[^"]*\)".*/\1/p' | head -n 1)"
fi
if [[ -z "$signing_identity" ]]; then
  signing_identity="-"
fi

if [[ "$release_build" = 1 ]]; then
  if [[ "$signing_identity" != "Developer ID Application:"* ]]; then
    echo "Production builds require a Developer ID Application identity." >&2
    exit 1
  fi
  if [[ -z "$notary_profile" ]]; then
    echo "Production builds require TINYTOUCH_NOTARY_PROFILE." >&2
    exit 1
  fi
fi

bundle="$build_dir/bin/tinytouch"
executable="$bundle/tinytouch"
"$executable" _package_test
network_ok=0
for attempt in 1 2 3; do
  if "$executable" _network_test; then
    network_ok=1
    break
  fi
  if [[ "$attempt" -lt 3 ]]; then
    sleep 2
  fi
done
if [[ "$network_ok" -ne 1 ]]; then
  echo "tinyTouch release network smoke test failed after 3 attempts" >&2
  exit 1
fi
sign_arguments=(--force --sign "$signing_identity")
if [[ "$signing_identity" = "Developer ID Application:"* ]]; then
  sign_arguments+=(--options runtime --timestamp)
else
  sign_arguments+=(--timestamp=none)
fi

# Sign nested code before the launcher. PyInstaller's onedir bundle contains the
# Python runtime and extension modules as separate Mach-O files.
while IFS= read -r -d '' candidate; do
  if [[ "$candidate" != "$executable" ]] && file "$candidate" | grep -q 'Mach-O'; then
    codesign "${sign_arguments[@]}" "$candidate"
  fi
done < <(find "$bundle" -type f -print0)
codesign "${sign_arguments[@]}" "$executable"
while IFS= read -r -d '' candidate; do
  if file "$candidate" | grep -q 'Mach-O'; then
    codesign --verify --strict --verbose=2 "$candidate"
  fi
done < <(find "$bundle" -type f -print0)

if [[ "$release_build" = 1 ]]; then
  notarization_archive="$build_dir/tinytouch-notarization.zip"
  ditto -c -k --keepParent "$bundle" "$notarization_archive"
  xcrun notarytool submit "$notarization_archive" \
    --keychain-profile "$notary_profile" --wait
  spctl --assess --type execute --verbose=2 "$executable"
fi
rm -f "$output"
tar -C "$build_dir/bin" -czf "$output" tinytouch
while IFS= read -r -d '' candidate; do
  if file "$candidate" | grep -q 'Mach-O'; then
    codesign --verify --strict --verbose=2 "$candidate"
  fi
done < <(find "$bundle" -type f -print0)

print "Built $output ($version)"
print "Signed executable with: $signing_identity"
