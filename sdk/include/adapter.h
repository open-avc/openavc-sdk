/* Hardware adapter with product-thread callbacks and copied readback. */
#ifndef OPENAVC_ADAPTER_H
#define OPENAVC_ADAPTER_H
#include "endpoint.h"
#include "outcome.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct oavc_adapter oavc_adapter;
typedef struct {
    oavc_endpoint_config network;
    const char *bridge_path;
    void *hardware;
    int (*readback)(void *, oavc_snapshot *);
    int (*post)(void *, void (*)(oavc_token), oavc_token);
    unsigned feedback_max_age_ms;
} oavc_adapter_config;
oavc_adapter *oavc_adapter_open(const oavc_adapter_config *, char *, size_t);
unsigned oavc_adapter_port(oavc_adapter *);
/* Call on the product's permitted thread before feedback_max_age_ms expires.
 * The SDK copies the complete snapshot and expires it using a monotonic clock. */
int oavc_adapter_refresh(oavc_adapter *);
/* Nonblocking stop/drain on the lifecycle owner thread. Keep processing accepted
 * product work until try_close succeeds, then release the hardware context. */
void oavc_adapter_stop(oavc_adapter *);
int oavc_adapter_try_close(oavc_adapter *);
#ifdef __cplusplus
}
#endif
#endif
