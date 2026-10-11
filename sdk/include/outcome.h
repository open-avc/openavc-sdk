#ifndef OPENAVC_OUTCOME_H
#define OPENAVC_OUTCOME_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef uint64_t oavc_token;
typedef enum {
    OAVC_QUEUED, OAVC_PENDING, OAVC_DONE, OAVC_FAILED, OAVC_UNCERTAIN,
    OAVC_REFUSED, OAVC_EXPIRED
} oavc_outcome;
/* DONE, FAILED and REFUSED are terminal callback results. REFUSED means no
 * hardware operation started. For asynchronous work return PENDING, then call
 * complete with DONE or FAILED once the hardware operation has finished.
 * A timeout alone never completes or cancels an accepted operation. */
int oavc_complete(oavc_token, oavc_outcome);
#ifdef __cplusplus
}
#endif
#endif
