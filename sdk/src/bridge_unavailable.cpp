#include "bridge.h"
#include <cstdio>

extern "C" oavc_bridge *oavc_bridge_open(const oavc_endpoint_config *, const char *,
                                        const char *, char *error, size_t size) {
    if (error && size)
        std::snprintf(error, size, "The service example requires Linux. Use the direct example on this development host.");
    return nullptr;
}
extern "C" void oavc_bridge_close(oavc_bridge *) {}
