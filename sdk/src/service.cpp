#include "endpoint.h"
#include "bridge_internal.hpp"
#include <cstdio>
#include <cstdlib>
#include <thread>

struct adapter { std::string path, model; };
static int readback(void *context, oavc_snapshot *snapshot) {
    try {
        const auto& source = *static_cast<adapter *>(context);
        const auto reply = bridge_exchange(source.path, {{"op", "snapshot"}, {"model", source.model}});
        if (reply.at("status") != 0) return -1;
        *snapshot = {reply.at("state"), reply.at("children"), reply.at("generation").get<uint64_t>(),
                     reply.at("revision").get<uint64_t>(), reply.at("invalid").get<bool>()};
        snapshot->pending_commands = reply.at("pending_commands").get<unsigned>();
        snapshot->completion_uncertain = reply.at("completion_uncertain").get<bool>();
        return 0;
    } catch (...) { return -1; }
}
static int command(void *context, const oavc_command *request) {
    try {
        const auto& source = *static_cast<adapter *>(context);
        const auto accepted = bridge_exchange(source.path, {{"op", "command"}, {"model", source.model},
                                    {"name", request->name}, {"params", request->params}});
        if (accepted.at("status") != 0) return accepted.at("status").get<int>();
        for (;;) {
            const auto result = bridge_exchange(source.path, {{"op", "result"}, {"model", source.model},
                {"incarnation", accepted.at("incarnation")}, {"id", accepted.at("id")}});
            if (result.at("status") != 0) return -4;
            if (result.at("complete").get<bool>()) return result.at("result").get<int>();
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
    } catch (...) { return -4; }
}
int main(int argc, char **argv) {
    if (argc != 4 || !std::getenv("OPENAVC_TOKEN")) return 2;
    try {
        adapter source{argv[3], bridge_model_hash(argv[1])};
        oavc_endpoint_config config{1, sizeof(oavc_endpoint_config), std::getenv("OPENAVC_DEVICE_ID") ? std::getenv("OPENAVC_DEVICE_ID") : "acme-native", argv[1], std::getenv("OPENAVC_TOKEN"), argv[2], &source, readback, command};
        char error[256];
        auto *endpoint = oavc_open(&config, error, sizeof(error));
        if (!endpoint) { std::fprintf(stderr, "%s\n", error); return 2; }
        std::printf("READY %u\n", oavc_port(endpoint)); std::fflush(stdout);
        char line[16];
        while (std::fgets(line, sizeof(line), stdin) && std::strcmp(line, "quit\n")) {}
        oavc_close(endpoint); return 0;
    } catch (const std::exception& exc) { std::fprintf(stderr, "%s\n", exc.what()); return 2; }
}
