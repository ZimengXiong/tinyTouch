#!/bin/zsh
# Keep the Keychain identity stable across CLI builds and installation paths.
set -euo pipefail

executable="$1"
signing_identity="${TINYTOUCH_SIGNING_IDENTITY:-}"
keychain_args=()
if [[ -n "${TINYTOUCH_SIGNING_KEYCHAIN:-}" ]]; then
  keychain_args=(--keychain "$TINYTOUCH_SIGNING_KEYCHAIN")
fi
if [[ -z "$signing_identity" ]]; then
  identities="$(security find-identity -v -p codesigning ${TINYTOUCH_SIGNING_KEYCHAIN:+"$TINYTOUCH_SIGNING_KEYCHAIN"})"
  signing_identity="$(print -r -- "$identities" | sed -n 's/.*"\(Developer ID Application:[^"]*\)".*/\1/p' | head -n 1)"
  if [[ -z "$signing_identity" ]]; then
    signing_identity="$(print -r -- "$identities" | sed -n 's/.*"\(Apple Development:[^"]*\)".*/\1/p' | head -n 1)"
  fi
fi
if [[ -z "$signing_identity" ]]; then
  print -u2 'No stable macOS signing identity is available. Set TINYTOUCH_SIGNING_IDENTITY.'
  exit 1
fi
if [[ "$signing_identity" == - && "${TINYTOUCH_ALLOW_ADHOC:-0}" != 1 ]]; then
  print -u2 'Ad hoc signing changes Keychain identity on every build. It is allowed only for explicit CI test builds.'
  exit 1
fi
if [[ "${TINYTOUCH_REQUIRE_STABLE_IDENTITY:-0}" == 1 && "$signing_identity" == - ]]; then
  print -u2 'Published CLI releases require a stable certificate signature.'
  exit 1
fi
if [[ "${TINYTOUCH_REQUIRE_STABLE_IDENTITY:-0}" == 1 && -z "${TINYTOUCH_SIGNING_TEAM_ID:-}" ]]; then
  print -u2 'Published CLI releases require a pinned TINYTOUCH_SIGNING_TEAM_ID.'
  exit 1
fi

codesign --force --timestamp=none --identifier com.tinytouch.cli \
  --sign "$signing_identity" "${keychain_args[@]}" "$executable"
codesign --verify --strict --verbose=2 "$executable"
if [[ "$signing_identity" != - ]]; then
  team_id="$(codesign -d --verbose=2 "$executable" 2>&1 | sed -n 's/^TeamIdentifier=//p')"
  if [[ ! "$team_id" =~ '^[A-Z0-9]{10}$' ]]; then
    print -u2 'The CLI certificate must have an Apple Developer Team ID.'
    exit 1
  fi
  if [[ -n "${TINYTOUCH_SIGNING_TEAM_ID:-}" && "$team_id" != "$TINYTOUCH_SIGNING_TEAM_ID" ]]; then
    print -u2 'The CLI signing certificate does not match the pinned release team.'
    exit 1
  fi
  # Pin product and team, not a changing binary hash or certificate name.
  requirement="identifier \"com.tinytouch.cli\" and anchor apple generic and certificate leaf[subject.OU] = \"$team_id\""
  codesign --force --timestamp=none --identifier com.tinytouch.cli \
    --requirements "=designated => $requirement" \
    --sign "$signing_identity" "${keychain_args[@]}" "$executable"
  codesign --verify --strict -R "=$requirement" "$executable"
  if codesign -d -r- "$executable" 2>&1 | grep -q 'designated => cdhash'; then
    print -u2 'The CLI signing requirement must remain stable across builds.'
    exit 1
  fi
fi
print "Signed executable with: $signing_identity"
