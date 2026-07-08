#pragma once

// ESP-only prototype keys. Do not use these for a real login token.
// Flash dumping this firmware reveals the private key.
//
// Next step for real security: replace these with a secure element operation.

static const char PIV_CERT_9A_PEM[] =
"-----BEGIN CERTIFICATE-----\n"
"REPLACE_WITH_AUTHENTICATION_CERTIFICATE\n"
"-----END CERTIFICATE-----\n";

static const char PIV_PRIVATE_KEY_9A_PEM[] =
"-----BEGIN PRIVATE KEY-----\n"
"REPLACE_WITH_AUTHENTICATION_PRIVATE_KEY\n"
"-----END PRIVATE KEY-----\n";

static const char PIV_CERT_9D_PEM[] =
"-----BEGIN CERTIFICATE-----\n"
"REPLACE_WITH_KEY_MANAGEMENT_CERTIFICATE\n"
"-----END CERTIFICATE-----\n";

static const char PIV_PRIVATE_KEY_9D_PEM[] =
"-----BEGIN PRIVATE KEY-----\n"
"REPLACE_WITH_KEY_MANAGEMENT_PRIVATE_KEY\n"
"-----END PRIVATE KEY-----\n";
