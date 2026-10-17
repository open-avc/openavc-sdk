# C declarations

Use the [hardware adapter guide](adapter-api.md) for ownership, threading,
readback and shutdown requirements. These declarations match the public
headers supplied with this SDK. Include the headers in your application;
do not copy declarations from this page.

Your generated `commands.h` supplies the callbacks and state publishers
for your own [product model](product-model.md). Regenerate that header
when changing the model.

## Hardware adapter

Header: [adapter.h](../sdk/include/adapter.h)

```c
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
```

## Command outcomes

Header: [outcome.h](../sdk/include/outcome.h)

```c
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
```

## Advanced endpoint

Header: [endpoint.h](../sdk/include/endpoint.h)

```c
#ifndef OPENAVC_ENDPOINT_H
#define OPENAVC_ENDPOINT_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* Endpoint ABI. Strings are borrowed only for
 * the duration of a call. The library copies published values. Hardware remains
 * OEM-owned. Command callbacks are serialized on one worker; readback runs on a
 * separate polling thread. Both callbacks must return and may run concurrently.
 * close waits for outstanding callbacks; it cannot cancel arbitrary OEM code. */
typedef struct oavc_endpoint oavc_endpoint;
typedef struct oavc_snapshot oavc_snapshot;
typedef struct oavc_command oavc_command;
/* Child IDs retain the type declared in the equipment model. */
typedef struct { int is_string; int64_t number; const char *text; } oavc_child_id;
static inline oavc_child_id oavc_child_number(int64_t value) {
    oavc_child_id id = {0, value, NULL}; return id;
}
static inline oavc_child_id oavc_child_string(const char *value) {
    oavc_child_id id = {1, 0, value}; return id;
}
typedef struct {
    uint32_t abi;
    size_t size;
    const char *identity, *model_path, *token, *certificate_path;
    void *context;
    int (*readback)(void *, oavc_snapshot *);
    int (*command)(void *, const oavc_command *);
} oavc_endpoint_config;

oavc_endpoint *oavc_open(const oavc_endpoint_config *, char *error, size_t error_size);
unsigned oavc_port(oavc_endpoint *);
void oavc_close(oavc_endpoint *);

void oavc_snapshot_revision(oavc_snapshot *, uint64_t generation, uint64_t revision);
void oavc_snapshot_invalidate(oavc_snapshot *);
/* child_type NULL addresses the product; otherwise child_id addresses a child. */
void oavc_state_number(oavc_snapshot *, const char *child_type, oavc_child_id child_id,
                      const char *key, double value);
void oavc_state_integer(oavc_snapshot *, const char *child_type, oavc_child_id child_id,
                       const char *key, uint64_t value);
void oavc_state_signed_integer(oavc_snapshot *, const char *child_type, oavc_child_id child_id,
                              const char *key, int64_t value);
void oavc_state_boolean(oavc_snapshot *, const char *child_type, oavc_child_id child_id,
                       const char *key, int value);
void oavc_state_string(oavc_snapshot *, const char *child_type, oavc_child_id child_id,
                      const char *key, const char *value);

const char *oavc_command_name(const oavc_command *);
/* Missing or null optional parameters have no value. */
int oavc_parameter_present(const oavc_command *, const char *key);
int oavc_parameter_equals_json(const oavc_command *, const char *key, const char *json_value);
/* Parameters are validated against the supplied model before this callback. */
int oavc_parameter_number(const oavc_command *, const char *key, double *out);
int oavc_parameter_integer(const oavc_command *, const char *key, int64_t *out);
int oavc_parameter_boolean(const oavc_command *, const char *key, int *out);
int oavc_parameter_string(const oavc_command *, const char *key, const char **out);
#ifdef __cplusplus
}
#endif
#endif
```

## Protocol constants

Header: [protocol.h](../sdk/include/protocol.h)

```c
/* Generated from OpenAVC equipment_protocol.py. Do not edit. */
#ifndef OPENAVC_PROTOCOL_H
#define OPENAVC_PROTOCOL_H
#define OAVC_PROTOCOL "openavc-device-1"
#define OAVC_MAX_MESSAGE_BYTES 1048576U
#endif
```
