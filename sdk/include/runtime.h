/* SDK-internal entry used by the generated C adapter. */
#ifndef OPENAVC_RUNTIME_H
#define OPENAVC_RUNTIME_H
#include "adapter.h"
#ifdef __cplusplus
extern "C" {
#endif
oavc_adapter *oavc_runtime_open(const oavc_adapter_config *,
    oavc_outcome (*dispatch)(void *, oavc_token, const oavc_command *),
    const char *model_hash, char *error, size_t error_size);
#ifdef __cplusplus
}
#endif
#endif
