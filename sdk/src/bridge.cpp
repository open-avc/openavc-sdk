#include "bridge.h"
#include "bridge_internal.hpp"
#include <openssl/rand.h>
#include <sys/stat.h>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include <cstdio>

struct bridge_job {
    uint64_t id;
    oavc_command request;
    uint64_t generation;
    bool complete = false;
    int result = -4;
};
struct oavc_bridge {
    oavc_endpoint_config config;
    std::string path, model, incarnation;
    bridge_fd listener{-1};
    std::atomic<bool> stopping{false};
    std::vector<std::thread> readers;
    std::thread worker;
    std::mutex mutex;
    std::condition_variable changed;
    std::shared_ptr<bridge_job> queued, active;
    std::deque<std::shared_ptr<bridge_job>> results;
    uint64_t next = 1;
    bool bound = false, completion_uncertain = false, confirmation_pending = false;
    uint64_t confirmation_generation = 0;

    // The caller holds mutex. A completed callback plus fresh readback from
    // the same hardware generation confirms idle without an OEM reset step.
    void confirm_generation(const oavc_snapshot& snapshot) {
        if (confirmation_pending) {
            completion_uncertain = completion_uncertain || snapshot.generation != confirmation_generation;
            confirmation_pending = false;
        }
        if (active && !active->complete && snapshot.generation != active->generation)
            completion_uncertain = true;
    }
    ino_t inode = 0;

    oavc_bridge(const oavc_endpoint_config& source, const char *hash, const char *socket_path)
        : config(source), path(socket_path), model(bridge_model_hash(source.model_path)) {
        if (model != hash) throw std::runtime_error("Equipment model differs from the built adapter. Rebuild the adapter.");
        unsigned char random[16];
        if (RAND_bytes(random, sizeof(random)) != 1) throw std::runtime_error("Secure randomness is unavailable.");
        constexpr char hex[] = "0123456789abcdef";
        for (auto value : random) { incarnation += hex[value >> 4]; incarnation += hex[value & 15]; }
        const auto slash = path.find_last_of('/');
        struct stat directory{};
        if (slash == std::string::npos || lstat(path.substr(0, slash).c_str(), &directory)
            || !S_ISDIR(directory.st_mode) || directory.st_uid != geteuid() || (directory.st_mode & 0077))
            throw std::runtime_error("Use a private socket directory owned by the product user.");
        const auto address = bridge_address(path);
        listener.value = ::socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
        try {
            if (listener.value < 0 || ::bind(listener.value, reinterpret_cast<const sockaddr *>(&address), sizeof(address)))
                throw std::runtime_error("Cannot bind the product socket. Check its path and existing product process.");
            bound = true;
            struct stat owned{};
            if (lstat(path.c_str(), &owned)) throw std::runtime_error("Cannot inspect the product socket.");
            inode = owned.st_ino;
            if (chmod(path.c_str(), 0600) || listen(listener.value, 4)) throw std::runtime_error("Cannot start the product socket.");
            worker = std::thread([this] { work(); });
            for (int i = 0; i < 2; ++i) readers.emplace_back([this] { serve(); });
        } catch (...) { stop(); throw; }
    }
    void stop() {
        { std::lock_guard<std::mutex> lock(mutex); stopping = true; }
        changed.notify_all();
        for (auto& reader : readers) if (reader.joinable()) reader.join();
        if (worker.joinable()) worker.join();
        struct stat current{};
        if (bound && !lstat(path.c_str(), &current) && current.st_ino == inode) unlink(path.c_str());
        bound = false;
    }
    ~oavc_bridge() { stop(); }
    void work() {
        for (;;) {
            std::unique_lock<std::mutex> lock(mutex);
            changed.wait(lock, [this] { return stopping || queued; });
            if (!queued) return;
            auto job = queued; queued.reset();
            lock.unlock();
            int outcome = -4;
            try { outcome = config.command(config.context, &job->request); }
            catch (...) { /* A thrown callback does not establish no hardware effect. */ }
            oavc_snapshot observed;
            bool current = false;
            try { current = config.readback(config.context, &observed) == 0; }
            catch (...) { /* Wait for fresh readback before confirming the generation. */ }
            lock.lock();
            completion_uncertain = completion_uncertain || outcome == -4;
            confirmation_pending = true;
            confirmation_generation = job->generation;
            if (current) confirm_generation(observed);
            job->result = outcome; job->complete = true;
        }
    }
    bridge_json handle(const bridge_json& value) {
        if (value.at("model") != model) return {{"status", -1}, {"error", "model_mismatch"}};
        if (value.at("op") == "snapshot" && value.size() == 2) {
            oavc_snapshot snapshot;
            const int code = config.readback(config.context, &snapshot);
            std::lock_guard<std::mutex> lock(mutex);
            if (code == 0) confirm_generation(snapshot);
            return {{"pending_commands", active && !active->complete ? 1 : 0}, {"completion_uncertain", completion_uncertain || confirmation_pending}, {"status", code}, {"state", snapshot.state}, {"children", snapshot.children},
                    {"generation", snapshot.generation}, {"revision", snapshot.revision}, {"invalid", snapshot.invalid}};
        }
        if (value.at("op") == "command" && value.size() == 4) {
            // Copied readback must be available before accepting work, including
            // while a service still retains feedback from an earlier process.
            oavc_snapshot snapshot;
            if (config.readback(config.context, &snapshot) != 0) return {{"status", -1}};
            oavc_command request{value.at("name").get<std::string>(), value.at("params")};
            std::lock_guard<std::mutex> lock(mutex);
            confirm_generation(snapshot);
            if (stopping || completion_uncertain || confirmation_pending) return {{"status", -1}};
            if (active && !active->complete) return {{"status", -1}};
            if (next > 9007199254740991ULL) return {{"status", -1}};
            auto job = std::make_shared<bridge_job>(bridge_job{next++, std::move(request), snapshot.generation});
            results.push_back(job);
            if (results.size() > 16) results.pop_front();
            active = queued = job;
            changed.notify_one();
            return {{"status", 0}, {"id", job->id}, {"incarnation", incarnation}};
        }
        if (value.at("op") == "result" && value.size() == 4) {
            std::lock_guard<std::mutex> lock(mutex);
            if (value.at("incarnation") != incarnation) return {{"status", -4}};
            const auto id = value.at("id").get<uint64_t>();
            for (const auto& job : results) if (job->id == id)
                return {{"status", 0}, {"complete", job->complete}, {"result", job->result}};
            return {{"status", -4}};
        }
        return {{"status", -1}};
    }
    void serve() {
        while (!stopping) {
            pollfd ready{listener.value, POLLIN, 0};
            if (poll(&ready, 1, 100) <= 0 || !(ready.revents & POLLIN)) continue;
            bridge_fd client{accept4(listener.value, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC)};
            if (client.value < 0) continue;
            try { bridge_peer(client.value); bridge_write(client.value, handle(bridge_read(client.value))); }
            catch (...) { /* Losing a local response never cancels or replays work. */ }
        }
    }
};
extern "C" oavc_bridge *oavc_bridge_open(const oavc_endpoint_config *config, const char *hash,
                                        const char *path, char *error, size_t size) {
    try {
        if (!config || config->abi != 1 || config->size != sizeof(oavc_endpoint_config) || !config->model_path
            || !config->readback || !config->command || !hash || !path)
            throw std::runtime_error("Supply the model, callbacks and private socket path.");
        return new oavc_bridge(*config, hash, path);
    } catch (const std::exception& exc) { if (error && size) std::snprintf(error, size, "%s", exc.what()); return nullptr; }
}
extern "C" void oavc_bridge_close(oavc_bridge *bridge) { delete bridge; }
