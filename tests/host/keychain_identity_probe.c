/* Verify that a rebuilt signed executable retains unattended Keychain access. */
#include <Security/Security.h>
#include <stdio.h>
#include <string.h>

#ifndef PROBE_BUILD
#define PROBE_BUILD 1
#endif

int main(int argc, char **argv) {
    const char *service = "tinyTouch-signing-test";
    const char *sample = "temporary-test-value";
    if (argc != 3 && argc != 4) return 2;
    SecKeychainSetUserInteractionAllowed(false);
    OSStatus status;
    SecKeychainItemRef item = NULL;
    SecKeychainRef keychain = NULL;
    if (argc == 4 && SecKeychainOpen(argv[3], &keychain) != errSecSuccess) return 2;
    if (strcmp(argv[1], "store") == 0) {
        status = SecKeychainAddGenericPassword(keychain, strlen(service), service,
            strlen(argv[2]), argv[2], strlen(sample), sample, &item);
    } else {
        UInt32 length = 0;
        void *data = NULL;
        status = SecKeychainFindGenericPassword(keychain, strlen(service), service,
            strlen(argv[2]), argv[2], &length, &data, &item);
        if (status == errSecSuccess && strcmp(argv[1], "delete") == 0) {
            status = SecKeychainItemDelete(item);
        } else if (status == errSecSuccess &&
                   (length != strlen(sample) || memcmp(data, sample, length) != 0)) {
            status = errSecDecode;
        }
        if (data) SecKeychainItemFreeContent(NULL, data);
    }
    if (item) CFRelease(item);
    if (keychain) CFRelease(keychain);
    printf("probe build=%d status=%d\n", PROBE_BUILD, (int)status);
    return status == errSecSuccess ? 0 : 1;
}
