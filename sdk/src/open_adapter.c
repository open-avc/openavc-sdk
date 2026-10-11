/* Package verification; the model must remain immutable while opening.
 * This detects build/package mistakes. It is not a firmware-signature check. */
#include "commands.h"
#include "runtime.h"
#include <openssl/evp.h>
#include <stdio.h>
#include <string.h>

int oavc_check_model(const oavc_endpoint_config *config, char *error, size_t size) {
    const char *failure = "Cannot verify equipment model. Check product diagnostics.";
    FILE *file = NULL;
    EVP_MD_CTX *hash = NULL;
    unsigned char buffer[4096], digest[EVP_MAX_MD_SIZE];
    unsigned digest_size = 0;
    char hex[65];
    size_t count;
    if (!config || config->abi != 1 || config->size != sizeof(oavc_endpoint_config) || !config->model_path) {
        failure = "Supply ABI 1 configuration and the equipment model path.";
        goto refused;
    }
    file = fopen(config->model_path, "rb");
    if (!file) {
        failure = "Cannot read equipment model. Check the bundled model path.";
        goto refused;
    }
    hash = EVP_MD_CTX_new();
    if (!hash || EVP_DigestInit_ex(hash, EVP_sha256(), NULL) != 1) goto refused;
    while ((count = fread(buffer, 1, sizeof(buffer), file))) {
        if (EVP_DigestUpdate(hash, buffer, count) != 1) goto refused;
    }
    if (ferror(file) || EVP_DigestFinal_ex(hash, digest, &digest_size) != 1 || digest_size != 32) goto refused;
    for (unsigned i = 0; i < 32; ++i) snprintf(hex + i * 2, 3, "%02x", digest[i]);
    if (strcmp(hex, oavc_model_sha256)) {
        failure = "Equipment model differs from the built adapter. Rebuild with this model and bundle both outputs together.";
        goto refused;
    }
    EVP_MD_CTX_free(hash);
    fclose(file);
    return 1;
refused:
    EVP_MD_CTX_free(hash);
    if (file) fclose(file);
    if (error && size) snprintf(error, size, "%s", failure);
    return 0;
}

oavc_adapter *oavc_adapter_open(const oavc_adapter_config *config, char *error, size_t size) {
    if (!config || !oavc_check_model(&config->network, error, size)) return NULL;
    return oavc_runtime_open(config, oavc_dispatch, oavc_model_sha256, error, size);
}
