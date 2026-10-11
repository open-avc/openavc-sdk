#include "adapter.h"
#include "bridge.h"
#include "runtime.h"
#include "lifecycle.h"
#include "value_types.hpp"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <memory>
#include <mutex>
#include <thread>

using namespace std::chrono_literals;
struct oavc_adapter {
    oavc_adapter_config config{};
    oavc_outcome (*dispatch)(void *, oavc_token, const oavc_command *) = nullptr;
    oavc_lifecycle_adapter *lifecycle = nullptr;
    oavc_endpoint *network = nullptr;
    oavc_bridge *bridge = nullptr;
    std::mutex lifecycle_mutex, request_mutex, snapshot_mutex;
    std::shared_ptr<oavc_command> request;
    oavc_snapshot snapshot;
    bool valid = false;
    std::chrono::steady_clock::time_point published;
    std::atomic<bool> stopping{false}, network_closed{false};
    std::mutex close_mutex;
    std::condition_variable close_changed;
    std::thread closing;
};

// Caller holds snapshot_mutex. Unchanged values still need current readback.
static bool current_locked(oavc_adapter *self) {
    return !self->stopping && self->valid &&
        std::chrono::steady_clock::now() - self->published <= std::chrono::milliseconds(self->config.feedback_max_age_ms);
}

static oavc_outcome dispatch(void *context, oavc_token token, const char *) {
    auto *self = static_cast<oavc_adapter *>(context);
    { std::lock_guard<std::mutex> lock(self->snapshot_mutex);
      if (!current_locked(self)) return OAVC_REFUSED; }
    std::shared_ptr<oavc_command> request;
    { std::lock_guard<std::mutex> lock(self->request_mutex); request = self->request; }
    if (!request) return OAVC_FAILED;
    // This shared copy lives through callback return even if completion is inline.
    return self->dispatch(self->config.hardware, token, request.get());
}

static int command(void *context, const oavc_command *request) {
    auto *self = static_cast<oavc_adapter *>(context);
    oavc_token token{};
    {
        std::lock_guard<std::mutex> lock(self->lifecycle_mutex);
        if (self->stopping) return -1;
        { std::lock_guard<std::mutex> request_lock(self->request_mutex);
          self->request = std::make_shared<oavc_command>(*request); }
        if (!oavc_lifecycle_submit(self->lifecycle, "generated-command", &token)) {
            std::lock_guard<std::mutex> request_lock(self->request_mutex);
            self->request.reset();
            return -1;
        }
    }
    oavc_outcome outcome;
    for (;;) {
        { std::lock_guard<std::mutex> lock(self->lifecycle_mutex);
          outcome = oavc_lifecycle_observe(self->lifecycle, token); }
        if (outcome == OAVC_DONE || outcome == OAVC_FAILED || outcome == OAVC_REFUSED) break;
        // The network SDK worker waits; the OEM event loop remains free. A client
        // timeout never completes, cancels or resubmits the accepted operation.
        std::this_thread::sleep_for(1ms);
    }
    { std::lock_guard<std::mutex> lock(self->request_mutex); self->request.reset(); }
    return outcome == OAVC_DONE ? 0 : outcome == OAVC_FAILED ? -6 : -1;
}

static int readback(void *context, oavc_snapshot *out) {
    auto *self = static_cast<oavc_adapter *>(context);
    std::lock_guard<std::mutex> lock(self->snapshot_mutex);
    if (!current_locked(self)) return -1;
    *out = self->snapshot;
    return 0;
}

extern "C" oavc_adapter *oavc_runtime_open(const oavc_adapter_config *config,
    oavc_outcome (*command_dispatch)(void *, oavc_token, const oavc_command *),
    const char *model_hash, char *error, size_t size) {
    if (!config || !command_dispatch || !model_hash || !config->readback || !config->post || !config->feedback_max_age_ms) {
        if (error && size) std::snprintf(error, size, "Supply hardware readback, product dispatch and a positive feedback age deadline.");
        return nullptr;
    }
    std::unique_ptr<oavc_adapter> self;
    try {
        self = std::make_unique<oavc_adapter>();
        self->config = *config;
        self->dispatch = command_dispatch;
        oavc_lifecycle_config lifecycle{self.get(), dispatch, config->hardware, config->post};
        self->lifecycle = oavc_lifecycle_open(&lifecycle);
        if (!self->lifecycle) return nullptr;
        oavc_endpoint_config network = config->network;
        network.context = self.get(); network.command = command; network.readback = readback;
        if (config->bridge_path) self->bridge = oavc_bridge_open(&network, model_hash, config->bridge_path, error, size);
        else self->network = oavc_open(&network, error, size);
        if (!self->network && !self->bridge) { oavc_lifecycle_stop(self->lifecycle); oavc_lifecycle_try_close(self->lifecycle); return nullptr; }
        self->closing = std::thread([owner = self.get()] {
            std::unique_lock<std::mutex> lock(owner->close_mutex);
            owner->close_changed.wait(lock, [owner] { return owner->stopping.load(); });
            lock.unlock();
            if (owner->bridge) oavc_bridge_close(owner->bridge);
            else oavc_close(owner->network);
            owner->network_closed.store(true);
        });
        return self.release();
    } catch (...) {
        if (self) {
            if (self->network) oavc_close(self->network);
            if (self->bridge) oavc_bridge_close(self->bridge);
            if (self->lifecycle) { oavc_lifecycle_stop(self->lifecycle); oavc_lifecycle_try_close(self->lifecycle); }
        }
        if (error && size) std::snprintf(error, size, "Could not start the equipment adapter.");
        return nullptr;
    }
}
extern "C" unsigned oavc_adapter_port(oavc_adapter *self) { return self && self->network ? oavc_port(self->network) : 0; }
extern "C" int oavc_adapter_refresh(oavc_adapter *self) {
    if (!self || self->stopping) return 0;
    const auto observed_at = std::chrono::steady_clock::now();
    oavc_snapshot current;
    bool valid = false;
    try { valid = self->config.readback(self->config.hardware, &current) == 0 && !current.invalid; }
    catch (...) { /* Unavailable feedback is safer than retaining apparent freshness. */ }
    std::lock_guard<std::mutex> lock(self->snapshot_mutex);
    self->valid = valid;
    if (valid) { self->snapshot = std::move(current); self->published = observed_at; }
    return current_locked(self);
}
extern "C" void oavc_adapter_stop(oavc_adapter *self) {
    if (!self) return;
    { std::lock_guard<std::mutex> lock(self->close_mutex);
      if (self->stopping.exchange(true)) return; }
    { std::lock_guard<std::mutex> lock(self->lifecycle_mutex); oavc_lifecycle_stop(self->lifecycle); }
    { std::lock_guard<std::mutex> lock(self->snapshot_mutex); self->valid = false; }
    self->close_changed.notify_one();
}
extern "C" int oavc_adapter_try_close(oavc_adapter *self) {
    if (!self) return 1;
    if (!self->stopping || !self->network_closed) return 0;
    { std::lock_guard<std::mutex> lock(self->lifecycle_mutex);
      if (!oavc_lifecycle_try_close(self->lifecycle)) return 0; }
    self->closing.join();
    delete self;
    return 1;
}
