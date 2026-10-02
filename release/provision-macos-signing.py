#!/usr/bin/env python3
"""Export one signing identity to encrypted GitHub environment secrets."""

import argparse
import base64
import ctypes as c
import hashlib
import secrets
import subprocess


class ExportParameters(c.Structure):
    _fields_ = [("version", c.c_uint32), ("flags", c.c_uint32)] + [
        (name, c.c_void_p) for name in (
            "passphrase", "alertTitle", "alertPrompt", "accessRef", "keyUsage", "keyAttributes"
        )
    ]


def export_identity(fingerprint: str, password: str) -> bytearray:
    """Export only the requested certificate and its private key as PKCS#12."""
    security = c.CDLL("/System/Library/Frameworks/Security.framework/Security")
    foundation = c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    signatures = {
        "SecIdentitySearchCreate": ([c.c_void_p, c.c_uint32, c.POINTER(c.c_void_p)], c.c_int32),
        "SecIdentitySearchCopyNext": ([c.c_void_p, c.POINTER(c.c_void_p)], c.c_int32),
        "SecIdentityCopyCertificate": ([c.c_void_p, c.POINTER(c.c_void_p)], c.c_int32),
        "SecCertificateCopyData": ([c.c_void_p], c.c_void_p),
        "SecItemExport": ([c.c_void_p, c.c_uint32, c.c_uint32,
                           c.POINTER(ExportParameters), c.POINTER(c.c_void_p)], c.c_int32),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(security, name)
        function.argtypes, function.restype = arguments, result
    foundation.CFRelease.argtypes = [c.c_void_p]
    foundation.CFDataGetLength.argtypes = [c.c_void_p]
    foundation.CFDataGetLength.restype = c.c_ssize_t
    foundation.CFDataGetBytePtr.argtypes = [c.c_void_p]
    foundation.CFDataGetBytePtr.restype = c.c_void_p
    foundation.CFStringCreateWithCString.argtypes = [c.c_void_p, c.c_char_p, c.c_uint32]
    foundation.CFStringCreateWithCString.restype = c.c_void_p

    def check(status):
        if status:
            raise RuntimeError(f"macOS signing identity access failed ({status})")

    search = c.c_void_p()
    check(security.SecIdentitySearchCreate(None, 0, c.byref(search)))
    try:
        while True:
            identity, certificate = c.c_void_p(), c.c_void_p()
            status = security.SecIdentitySearchCopyNext(search, c.byref(identity))
            if status == -25300:
                raise RuntimeError("The requested signing certificate was not found")
            check(status)
            data = passphrase = exported = None
            try:
                check(security.SecIdentityCopyCertificate(identity, c.byref(certificate)))
                data = security.SecCertificateCopyData(certificate)
                raw = c.string_at(foundation.CFDataGetBytePtr(data), foundation.CFDataGetLength(data))
                if hashlib.sha1(raw).hexdigest().upper() != fingerprint.upper():
                    continue
                passphrase = foundation.CFStringCreateWithCString(None, password.encode(), 0x08000100)
                parameters = ExportParameters(passphrase=passphrase)
                exported = c.c_void_p()
                # kSecFormatPKCS12 = 12. Native Keychain approval may be required.
                check(security.SecItemExport(identity, 12, 0, c.byref(parameters), c.byref(exported)))
                return bytearray(c.string_at(
                    foundation.CFDataGetBytePtr(exported), foundation.CFDataGetLength(exported)
                ))
            finally:
                for reference in (exported, passphrase, data, certificate, identity):
                    if reference:
                        foundation.CFRelease(reference)
    finally:
        foundation.CFRelease(search)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate-sha1", required=True)
    parser.add_argument("--team-id", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--environment", default="release-signing")
    args = parser.parse_args()
    password = secrets.token_urlsafe(48)
    print("macOS may ask to approve export of the selected signing key.", flush=True)
    exported = export_identity(args.certificate_sha1, password)
    try:
        from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
        from cryptography.x509.oid import NameOID
        key, certificate, _ = load_key_and_certificates(exported, password.encode())
        if key is None or certificate is None:
            raise RuntimeError("The exported identity has no usable signing key")
        teams = certificate.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)
        if not teams or teams[0].value != args.team_id:
            raise RuntimeError("The certificate does not match the pinned release team")
        for name, value in (
            ("TINYTOUCH_MACOS_CERTIFICATE_PASSWORD", password.encode()),
            ("TINYTOUCH_MACOS_CERTIFICATE_B64", base64.b64encode(exported)),
        ):
            subprocess.run(
                ["gh", "secret", "set", name, "--repo", args.repo, "--env", args.environment],
                input=value, check=True, stdout=subprocess.DEVNULL,
            )
        subprocess.run([
            "gh", "variable", "set", "TINYTOUCH_MACOS_TEAM_ID", "--repo", args.repo,
            "--env", args.environment, "--body", args.team_id,
        ], check=True)
        print("GitHub release signing certificate, password, and team pin configured.")
    finally:
        exported[:] = b"\x00" * len(exported)


if __name__ == "__main__":
    main()
