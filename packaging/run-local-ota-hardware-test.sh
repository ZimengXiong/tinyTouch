#!/bin/zsh
set -euo pipefail

project_dir="${0:A:h:h}"
python_bin="$project_dir/.venv/bin/python"
firmware_bin="$project_dir/firmware/tiny_touch_unified/build/tiny_touch_unified.bin"
signing_key="$project_dir/firmware/tiny_touch_unified/secure_boot_signing_key.pem"
build_id="$(git -C "$project_dir" rev-parse --short=12 HEAD)"
branch="$(git -C "$project_dir" branch --show-current)"
started="$(date -u +%Y%m%dT%H%M%SZ)"
results_dir="$project_dir/build/hardware-tests/$started-$build_id"
main_log="$results_dir/hardware-test.log"
usb_log="$results_dir/usb-timeline.log"
status_log="$results_dir/status-samples.log"
monitor_pid=""

mkdir -p "$results_dir"

finish() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "$monitor_pid" ]]; then
    kill "$monitor_pid" 2>/dev/null || true
    wait "$monitor_pid" 2>/dev/null || true
  fi
  {
    print "exit_code=$exit_code"
    print "finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    print "results=$results_dir"
  } >> "$results_dir/result.txt"
  print
  print "Local hardware-test logs: $results_dir"
  exit "$exit_code"
}
trap finish EXIT INT TERM

exec > >(tee -a "$main_log") 2>&1

print "tinyTouch local OTA hardware test"
print "branch=$branch"
print "commit=$(git -C "$project_dir" rev-parse HEAD)"
print "build_id=$build_id"
print "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
print "This command does not push, tag, publish, or contact GitHub."

[[ "$branch" == "prod/0.8.1/ota-reconnect-hotfix" ]] || {
  print -u2 "Refusing to test from unexpected branch: $branch"
  exit 1
}
[[ -x "$python_bin" ]] || { print -u2 "Missing local Python environment: $python_bin"; exit 1; }
[[ -s "$firmware_bin" ]] || { print -u2 "Missing prebuilt firmware: $firmware_bin"; exit 1; }
[[ -s "$signing_key" ]] || { print -u2 "Missing local firmware signing key."; exit 1; }
grep -a -q "$build_id" "$firmware_bin" || {
  print -u2 "The firmware was not built from current commit $build_id."
  exit 1
}

source /Users/xzm/esp/esp-idf/export.sh >/dev/null
espsecure.py verify_signature --version 2 --keyfile "$signing_key" "$firmware_bin"
shasum -a 256 "$firmware_bin" | tee "$results_dir/firmware.sha256"
sw_vers | tee "$results_dir/macos.txt"
system_profiler SPUSBDataType > "$results_dir/usb-before.txt"

(
  while true; do
    print "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
    ioreg -p IOUSB -l -w 0 | grep -E 'tinyTouch|TT-|idVendor|idProduct|USB Serial Number|USB Product Name' || true
    sleep 0.2
  done
) > "$usb_log" 2>&1 &
monitor_pid=$!

print
print "Pre-update status"
"$python_bin" "$project_dir/tinytouch" --verbose status | tee -a "$status_log"

print
print "The firmware is already built and verified. Touch an enrolled finger when prompted."
"$python_bin" "$project_dir/tinytouch" --verbose update --force --local "$firmware_bin"

for sample in 1 2 3; do
  print
  print "Post-update status sample $sample"
  "$python_bin" "$project_dir/tinytouch" --verbose status | tee -a "$status_log"
  sleep 5
done

system_profiler SPUSBDataType > "$results_dir/usb-after.txt"
grep -q "firmware_version=0.8.0" "$status_log"
grep -q "build=$build_id" "$status_log"
grep -q "ota_state=valid" "$status_log"
grep -q "ota_slot1=.*0.8.0:valid\|ota_slot0=.*0.8.0:valid" "$status_log"

print
print "PASS: the local hotfix stayed connected and its OTA slot is valid."
