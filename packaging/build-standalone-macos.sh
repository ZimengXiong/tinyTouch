#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h:h}"
build_dir="$project_dir/build/distribution"
dist_dir="$project_dir/dist"
venv_python="$project_dir/.venv/bin/python"
version="${TINYTOUCH_VERSION:-$(tr -d '[:space:]' < "$project_dir/VERSION")}"
output="$dist_dir/tinytouch.tar.gz"
signing_identity="${TINYTOUCH_SIGNING_IDENTITY:-}"

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
# A PyInstaller one-file binary extracts its bundled Python dylib at runtime.
# Hardened runtime library validation rejects that extracted ad-hoc-signed dylib
# because it does not share the outer Apple Development signature's Team ID.
# Keep this non-notarized pre-production executable signed without hardened
# runtime; production distribution should sign nested components in an app
# bundle before enabling hardened runtime and notarization.
codesign --force --timestamp=none --sign "$signing_identity" "$executable"
codesign --verify --strict --verbose=2 "$executable"
rm -f "$output"
tar -C "$build_dir/bin" -czf "$output" tinytouch
codesign --verify --strict --verbose=2 "$executable"

print "Built $output ($version)"
print "Signed executable with: $signing_identity"
