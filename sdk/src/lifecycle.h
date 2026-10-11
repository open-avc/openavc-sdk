/* Process-local command lifecycle.
 * submit copies its argument; tokens contain no borrowed pointers.
 * post accepts work exactly once or refuses without invoking it. It may run inline.
 * Pending/uncertain work retains the OEM context until explicit completion.
 * stop prevents new work; try_close never waits for an OEM callback/dispatcher.
 * Call try_close only from the owning lifecycle thread, not concurrently with
 * other calls using the adapter pointer. complete takes only an opaque token.
 */
#ifndef OPENAVC_LIFECYCLE_H
#define OPENAVC_LIFECYCLE_H
#include "outcome.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct oavc_lifecycle_adapter oavc_lifecycle_adapter;
/* A command returning DONE, FAILED or REFUSED is terminal. REFUSED means no
 * hardware operation started. Pending/uncertain work retains its context until
 * complete reports DONE or FAILED; a timeout alone is not completion. */
typedef struct {
    void *hardware_context;
    oavc_outcome (*command)(void *, oavc_token, const char *);
    void *dispatch_context;
    int (*post)(void *, void (*)(oavc_token), oavc_token);
} oavc_lifecycle_config;
oavc_lifecycle_adapter *oavc_lifecycle_open(const oavc_lifecycle_config *);
int oavc_lifecycle_submit(oavc_lifecycle_adapter *, const char *, oavc_token *);
oavc_outcome oavc_lifecycle_observe(oavc_lifecycle_adapter *, oavc_token);
void oavc_lifecycle_stop(oavc_lifecycle_adapter *);
int oavc_lifecycle_try_close(oavc_lifecycle_adapter *);
#ifdef __cplusplus
}
#endif
#endif
