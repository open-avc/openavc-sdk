#include "lifecycle.h"
#include <condition_variable>
#include <cstring>
#include <deque>
#include <limits>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>

struct owner;
struct operation {
    std::shared_ptr<owner> parent;
    oavc_token token{};
    std::string argument;
    bool invoked = false, terminal = false;
};
struct owner {
    oavc_lifecycle_config config{};
    std::mutex mutex;
    std::condition_variable changed;
    std::shared_ptr<operation> active;
    std::deque<oavc_token> queue;
    std::thread worker;
    bool stopping = false, worker_stop = false;
    unsigned callbacks = 0;
    oavc_token last_token{};
    oavc_outcome last_outcome = OAVC_EXPIRED;
};
struct oavc_lifecycle_adapter { std::shared_ptr<owner> state; };
static std::mutex registry_mutex;
static std::unordered_map<oavc_token, std::shared_ptr<operation>> registry;
static oavc_token next_token = 1;

static std::shared_ptr<operation> lookup(oavc_token token) {
    std::lock_guard<std::mutex> lock(registry_mutex);
    auto found = registry.find(token);
    return found == registry.end() ? nullptr : found->second;
}
/* Called with the owner's lock; lookup never holds registry while taking it. */
static void reap(owner& state, const std::shared_ptr<operation>& job) {
    if (!job->terminal || state.callbacks) return;
    { std::lock_guard<std::mutex> lock(registry_mutex); registry.erase(job->token); }
    if (state.active == job) state.active.reset();
}
static void finish(owner& state, const std::shared_ptr<operation>& job, oavc_outcome outcome) {
    job->terminal = true;
    state.last_outcome = outcome;
    reap(state, job);
}
static void invoke(oavc_token token) {
    auto job = lookup(token);
    if (!job) return;
    auto state = job->parent;
    {
        std::lock_guard<std::mutex> lock(state->mutex);
        if (job->terminal || job->invoked) return;
        if (state->stopping) { finish(*state, job, OAVC_REFUSED); return; }
        job->invoked = true;
        ++state->callbacks;
        state->last_outcome = OAVC_PENDING;
    }
    oavc_outcome outcome = OAVC_UNCERTAIN;
    try { outcome = state->config.command(state->config.hardware_context, token, job->argument.c_str()); }
    catch (...) { /* A thrown callback cannot establish absence of hardware work. */ }
    std::lock_guard<std::mutex> lock(state->mutex);
    --state->callbacks;
    if (!job->terminal) {
        if (outcome == OAVC_DONE || outcome == OAVC_FAILED || outcome == OAVC_REFUSED) finish(*state, job, outcome);
        else state->last_outcome = outcome == OAVC_PENDING ? OAVC_PENDING : OAVC_UNCERTAIN;
    }
    reap(*state, job);
    state->changed.notify_all();
}
extern "C" oavc_lifecycle_adapter *oavc_lifecycle_open(const oavc_lifecycle_config *config) {
    if (!config || !config->command) return nullptr;
    try {
        auto state = std::make_shared<owner>();
        state->config = *config;
        auto adapter = std::make_unique<oavc_lifecycle_adapter>();
        adapter->state = state;
        if (!config->post) state->worker = std::thread([state] {
            for (;;) {
                std::unique_lock<std::mutex> lock(state->mutex);
                state->changed.wait(lock, [&] { return state->worker_stop || !state->queue.empty(); });
                if (state->worker_stop) return;
                auto token = state->queue.front(); state->queue.pop_front();
                lock.unlock();
                invoke(token);
            }
        });
        return adapter.release();
    } catch (...) { return nullptr; }
}
extern "C" int oavc_lifecycle_submit(oavc_lifecycle_adapter *adapter, const char *argument, oavc_token *out) {
    if (!adapter || !argument || !out || std::strlen(argument) > 128) return 0;
    auto state = adapter->state;
    std::shared_ptr<operation> job;
    try {
        std::lock_guard<std::mutex> lock(state->mutex);
        if (state->stopping || state->active) return 0;
        job = std::make_shared<operation>();
        job->parent = state; job->argument = argument;
        {
            std::lock_guard<std::mutex> registry_lock(registry_mutex);
            if (next_token == std::numeric_limits<oavc_token>::max()) return 0;
            job->token = next_token++;
            registry.emplace(job->token, job);
        }
        state->active = job;
        state->last_token = *out = job->token;
        state->last_outcome = OAVC_QUEUED;
        if (!state->config.post) {
            state->queue.push_back(job->token);
            state->changed.notify_one();
            return 1;
        }
    } catch (...) {
        if (job && job->token) {
            std::lock_guard<std::mutex> lock(state->mutex);
            finish(*state, job, OAVC_REFUSED);
        }
        return 0;
    }
    int accepted = 0;
    try { accepted = state->config.post(state->config.dispatch_context, invoke, job->token); }
    catch (...) { /* post must not throw across the C API. */ }
    std::lock_guard<std::mutex> lock(state->mutex);
    if (!accepted && !job->invoked) {
        finish(*state, job, OAVC_REFUSED);
        return 0;
    }
    // An invalid dispatcher that ran then refused cannot mean no hardware effect.
    if (!accepted && !job->terminal) state->last_outcome = OAVC_UNCERTAIN;
    return 1;
}
extern "C" oavc_outcome oavc_lifecycle_observe(oavc_lifecycle_adapter *adapter, oavc_token token) {
    if (!adapter) return OAVC_EXPIRED;
    std::lock_guard<std::mutex> lock(adapter->state->mutex);
    return token == adapter->state->last_token ? adapter->state->last_outcome : OAVC_EXPIRED;
}
extern "C" int oavc_complete(oavc_token token, oavc_outcome outcome) {
    if (outcome != OAVC_DONE && outcome != OAVC_FAILED) return 0;
    auto job = lookup(token);
    if (!job) return 0;
    auto state = job->parent;
    std::lock_guard<std::mutex> lock(state->mutex);
    if (!job->invoked || job->terminal) return 0;
    finish(*state, job, outcome);
    state->changed.notify_all();
    return 1;
}
extern "C" void oavc_lifecycle_stop(oavc_lifecycle_adapter *adapter) {
    if (!adapter) return;
    auto state = adapter->state;
    std::lock_guard<std::mutex> lock(state->mutex);
    state->stopping = true;
    auto job = state->active;
    if (job && !job->invoked) finish(*state, job, OAVC_REFUSED);
    state->queue.clear();
}
extern "C" int oavc_lifecycle_try_close(oavc_lifecycle_adapter *adapter) {
    if (!adapter) return 1;
    auto state = adapter->state;
    {
        std::lock_guard<std::mutex> lock(state->mutex);
        if (!state->stopping || state->active || state->callbacks) return 0;
        state->worker_stop = true;
        state->changed.notify_all();
    }
    if (state->worker.joinable()) state->worker.join();
    delete adapter;
    return 1;
}
