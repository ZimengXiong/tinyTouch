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

bundle="$build_dir/bin/tinytouch"
executable="$bundle/tinytouch"
"$venv_python" "$project_dir/release/check-python-runtime.py" "$bundle"
"$executable" _package_test
# Keep this CLI certificate-signed without hardened runtime or notarization.
# Bundled Python libraries retain their PyInstaller signatures.
zsh "$project_dir/release/sign-macos-cli.sh" "$executable"
rm -f "$output"
tar -C "$build_dir/bin" -czf "$output" tinytouch
codesign --verify --strict --verbose=2 "$executable"

print "Built $output ($version)"
