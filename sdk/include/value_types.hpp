// Shared representation inside the SDK libraries, not an OEM API.
#ifndef OPENAVC_VALUE_TYPES_HPP
#define OPENAVC_VALUE_TYPES_HPP
#include "json.hpp"
#include <cstdint>
#include <string>
struct oavc_snapshot {
    nlohmann::json state = nlohmann::json::object(), children = nlohmann::json::array();
    uint64_t generation = 0, revision = 0;
    bool invalid = false;
    unsigned pending_commands = 0;
    bool completion_uncertain = false;
};
struct oavc_command { std::string name; nlohmann::json params; };
#endif
