# macOS release signing

GitHub release CLIs use the fixed identifier `com.tinytouch.cli` and an Apple
code-signing certificate from the team pinned in `TINYTOUCH_MACOS_TEAM_ID`.
The designated requirement binds the product identifier, Apple certificate
anchor, and Team ID. It excludes binary hashes and certificate common names.
ARM64 and x86-64 releases must use the same team and requirement.

An Apple Development or Developer ID Application certificate can provide this
stable Keychain identity. This project does not notarize the CLI or enable
hardened runtime. Code signing and notarization are separate steps.

Release builds stop if the certificate, private key, or team pin is missing or
invalid. They never fall back to ad hoc signing. CI builds can use ad hoc signing
only with both `TINYTOUCH_SIGNING_IDENTITY=-` and `TINYTOUCH_ALLOW_ADHOC=1`;
those artifacts are test builds, not GitHub release assets.

## Provision GitHub Actions

Use the `release-signing` GitHub environment. It needs:

| Setting | Type | Purpose |
| --- | --- | --- |
| `TINYTOUCH_MACOS_CERTIFICATE_B64` | Secret | Encrypted PKCS#12 certificate and private key |
| `TINYTOUCH_MACOS_CERTIFICATE_PASSWORD` | Secret | PKCS#12 encryption password |
| `TINYTOUCH_MACOS_TEAM_ID` | Variable | Expected Apple Developer Team ID |

Find the certificate fingerprint on a Mac that holds its private key:

```sh
security find-identity -v -p codesigning
```

Use the certificate's Team ID, not the personal identifier in its common name.
The signing script prints the Team ID through `codesign` metadata when needed.
Provision only the selected identity:

```sh
.venv-release/bin/python release/provision-macos-signing.py \
  --certificate-sha1 CERTIFICATE_FINGERPRINT \
  --team-id APPLE_TEAM_ID \
  --repo ZimengXiong/tinyTouch
```

macOS can require approval to export the private key. The script encrypts the
selected identity in memory and sends the values directly to GitHub's encrypted
secrets API through `gh`. It does not write or print private key material.
The workflow imports the identity into a temporary runner Keychain and removes
that Keychain after building. Keep the GitHub environment's access controls.

## Renew the certificate

Provision a renewed code-signing certificate from the same Apple team before
the old one expires. Keep `com.tinytouch.cli` and the pinned Team ID unchanged.
The certificate common name and binary hash can change without changing the
designated requirement. Switching Apple teams requires a credential migration.

## Verify upgrade compatibility

Inspect the requirement in the built executable:

```sh
codesign --verify --strict build/distribution/bin/tinytouch/tinytouch
codesign -d -r- build/distribution/bin/tinytouch/tinytouch
```

It must identify `com.tinytouch.cli`, an Apple anchor, and the pinned team. It
must not consist of a `cdhash`. The host test `keychain_identity_probe.c` verifies
that one signed build can read a temporary Keychain item created by another
signed build with different contents and the same requirement.
Every release job runs `verify-keychain-upgrade.py` against its packaged CLI
before uploading either architecture's release artifact.

Existing items created by ad hoc builds still require a one-time repair. A new
signature cannot silently inherit permission assigned to an old binary hash.
The installer and `tinytouch update` run repair automatically if the replacement
helper cannot read saved credentials. This check applies to every prior version.
Repair includes connected devices and disconnected devices recorded on this Mac.
If approval is denied, the working helper stays installed and firmware is not
staged. The installer also keeps the existing CLI command until the check passes.
Legacy CLIs such as `0.1.25-prod` and `0.1.26-prod` reject stable version strings
without `-prod` before downloading an installer. Those users must run the current
release's installer once; their old `update` command cannot perform this migration.

## Repair an existing HID installation

Run the current certificate-signed CLI with the device connected:

```sh
tinytouch repair
```

Approve the native macOS Keychain dialogs for the saved pairing key, password,
and any fingerprint password overrides. macOS can require the Login Keychain
password to change these access rules. The command keeps credential values and
existing trusted applications. It adds the current signed executable and its
team partition, checks unattended access in a separate helper process, then
reinstalls the service from the current CLI.

If authorization is denied, the command leaves the installed service in place
and reports the failure. Unlocking a Keychain alone does not authorize a new
executable. A fresh helper is installed only after its own credential check
succeeds. If service startup fails, the previous launch configuration is restored.
