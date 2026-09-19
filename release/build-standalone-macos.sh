#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h:h}"
build_dir="${TINYTOUCH_BUILD_DIR:-$project_dir/build/distribution}"
dist_dir="$project_dir/dist"
venv_dir="${TINYTOUCH_VENV_DIR:-$project_dir/.venv-release}"
venv_python="$venv_dir/bin/python"
bootstrap_python="${TINYTOUCH_PYTHON:-python3.13}"
version="${TINYTOUCH_VERSION:-$(tr -d '[:space:]' < "$project_dir/VERSION")}"
output="${TINYTOUCH_OUTPUT:-$dist_dir/tinytouch.tar.gz}"
signing_identity="${TINYTOUCH_SIGNING_IDENTITY:-}"

if [[ ! -x "$venv_python" ]]; then
  "$bootstrap_python" -m venv "$venv_dir"
fi

"$venv_python" "$project_dir/release/check-python-runtime.py"

# PEP 517 backends installed in the build environment are executables. Add the
# environment's bin directory so source distributions can invoke them.
export PATH="$venv_dir/bin:$PATH"

"$venv_python" -m pip install -q --require-hashes \
  -r "$project_dir/macos/requirements-bootstrap.txt"
"$venv_python" -m pip install -q --no-build-isolation --require-hashes \
  -r "$project_dir/macos/requirements-release.txt"

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
  --paths "$project_dir/macos" \
  --hidden-import tinytouch_helper \
  --hidden-import tinytouch_keychain \
  --hidden-import tinytouch_runtime \
  --hidden-import serial.tools.list_ports \
  --collect-all esptool \
  --add-data "$project_dir/VERSION:." \
  "$project_dir/macos/cli.py"

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
"$venv_python" "$project_dir/release/check-python-runtime.py" "$bundle"
"$executable" _package_test
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
