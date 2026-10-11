/* SDK-internal local service frontend.
 * The combined adapter supplies copied readback and product-thread dispatch.
 * The product and service use the same OS user and a private socket directory. */
#ifndef OPENAVC_COMBINED_BRIDGE_H
#define OPENAVC_COMBINED_BRIDGE_H
#include "endpoint.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct oavc_bridge oavc_bridge;
oavc_bridge *oavc_bridge_open(const oavc_endpoint_config *, const char *model_hash,
                            const char *socket_path, char *error, size_t size);
void oavc_bridge_close(oavc_bridge *);
#ifdef __cplusplus
}
#endif
#endif
