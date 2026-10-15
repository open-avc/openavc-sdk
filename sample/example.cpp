// Invented product: SDK callbacks enter its own event loop and publish typed state.
#include "commands.h"
#include "adapter.h"
#include <array>
#include <chrono>
#include <csignal>
#include <openssl/rand.h>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <sys/select.h>
#include <unistd.h>

static volatile std::sig_atomic_t stop_requested;
static void request_stop(int) { stop_requested = 1; }

class Product {
public:
    enum class Action { gain, mute, input, increment };
    Product(const char *model, const char *certificate, const char *credential) {
        if (RAND_bytes(reinterpret_cast<unsigned char *>(&generation_), sizeof(generation_)) != 1 || !generation_)
            throw std::runtime_error("Could not create a hardware session identity.");
        gain_.fill(-20);
        oavc_adapter_config config{{1, sizeof(oavc_endpoint_config), std::getenv("OPENAVC_DEVICE_ID") ? std::getenv("OPENAVC_DEVICE_ID") : "acme-native", model,
            credential, certificate, nullptr, nullptr, nullptr}, std::getenv("OPENAVC_PRODUCT_SOCKET"), this, readback, post, 1000};
        char error[256]{};
        adapter_ = oavc_adapter_open(&config, error, sizeof(error));
        if (!adapter_) throw std::runtime_error(error[0] ? error : "Could not open the equipment adapter.");
        oavc_adapter_refresh(adapter_);
    }
    Product(const Product&) = delete;
    Product& operator=(const Product&) = delete;
    ~Product() {
        if (!adapter_) return;
        oavc_adapter_stop(adapter_);
        // This sample owns its event loop. Finish accepted work before hardware
        // members are destroyed, including during exception unwinding.
        while (!oavc_adapter_try_close(adapter_)) {
            pump();
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        physical();
        std::cout << "CLOSED" << std::endl;
    }
    oavc_outcome command(oavc_token token, Action action, int64_t channel, double value) {
        check_thread(); ++callbacks_;
        if (pending_ || channel < 0 || channel > channels_) return OAVC_FAILED;
        Operation operation{token, action, static_cast<unsigned>(channel), value, Clock::now()};
        last_ = token;
        if (delay_ms_) {
            operation.due += std::chrono::milliseconds(delay_ms_);
            delay_ms_ = 0; pending_ = operation;
            return OAVC_PENDING;
        }
        apply(operation);
        return outcome();
    }
    void run(bool daemon) {
        std::cout << "READY " << oavc_adapter_port(adapter_) << std::endl;
        for (;;) {
            if (stop_requested) stop();
            pump();
            if (stopping_) {
                if (oavc_adapter_try_close(adapter_)) { adapter_ = nullptr; break; }
                close_waited_ = true;
            } else if (!pause_feedback_) oavc_adapter_refresh(adapter_);
            if (daemon) { std::this_thread::sleep_for(std::chrono::milliseconds(1)); continue; }
            fd_set input; FD_ZERO(&input); FD_SET(STDIN_FILENO, &input);
            timeval wait{0, 1000};
            if (select(STDIN_FILENO + 1, &input, nullptr, nullptr, &wait) <= 0) continue;
            std::string line;
            if (!std::getline(std::cin, line)) { stop(); continue; }
            std::istringstream text(line);
            std::string action; text >> action;
            if (action == "delay") text >> delay_ms_;
            else if (action == "finish") { if (pending_) pending_->due = Clock::now(); }
            else if (action == "external") { text >> gain_[1]; ++ticks_; ++revision_; }
            else if (action == "channels") {
                unsigned count = 0; text >> count;
                if (count > gain_.size()) throw std::runtime_error("Invalid sample channel count.");
                channels_ = count; ++revision_;
            } else if (action == "generation") { text >> generation_; ++revision_; }
            else if (action == "readfail") text >> read_failed_;
            else if (action == "pause-feedback") text >> pause_feedback_;
            else if (action == "refuse") {
                std::lock_guard<std::mutex> lock(queue_mutex_); text >> refuse_;
            } else if (action == "fail-next") fail_next_ = true;
            else if (action == "late") {
                std::cout << "LATE " << oavc_complete(last_, OAVC_DONE) << std::endl; continue;
            } else if (action == "stop") stop();
            else if (action != "snapshot") throw std::runtime_error("Unknown sample instrument command.");
            if (text.fail()) throw std::runtime_error("Invalid sample instrument value.");
            physical();
        }
        physical();
        std::cout << "CLOSED" << std::endl;
    }
private:
    using Clock = std::chrono::steady_clock;
    struct Operation { oavc_token token; Action action; unsigned channel; double value; Clock::time_point due; };
    const std::thread::id owner_ = std::this_thread::get_id();
    oavc_adapter *adapter_ = nullptr;
    std::mutex queue_mutex_;
    void (*queued_work_)(oavc_token) = nullptr;
    oavc_token queued_token_ = 0, last_ = 0;
    std::optional<Operation> pending_;
    std::array<double, 4> gain_{};
    std::array<bool, 4> mute_{};
    unsigned channels_ = 2, delay_ms_ = 0, writes_ = 0, callbacks_ = 0, wrong_threads_ = 0, reads_ = 0;
    uint64_t counter_ = 0, ticks_ = 0, revision_ = 1, generation_ = 1;
    bool input_ = false, read_failed_ = false, fail_next_ = false, refuse_ = false;
    bool stopping_ = false, close_waited_ = false, pause_feedback_ = false;
    void check_thread() { if (std::this_thread::get_id() != owner_) ++wrong_threads_; }
    void stop() { if (!stopping_) { stopping_ = true; oavc_adapter_stop(adapter_); } }
    oavc_outcome outcome() { const bool failed = fail_next_; fail_next_ = false; return failed ? OAVC_FAILED : OAVC_DONE; }
    void apply(const Operation& work) {
        check_thread();
        switch (work.action) {
        case Action::gain: gain_.at(work.channel - 1) = work.value; break;
        case Action::mute: mute_.at(work.channel - 1) = work.value != 0; break;
        case Action::input: input_ = work.value != 0; break;
        case Action::increment: ++counter_; break;
        }
        ++writes_; ++revision_;
    }
    void pump() {
        void (*work)(oavc_token); oavc_token token;
        { std::lock_guard<std::mutex> lock(queue_mutex_);
          work = queued_work_; token = queued_token_; queued_work_ = nullptr; }
        if (work) work(token);
        if (pending_ && Clock::now() >= pending_->due) {
            const auto done = *pending_;
            apply(done); oavc_complete(done.token, outcome()); pending_.reset();
        }
    }
    static int post(void *context, void (*work)(oavc_token), oavc_token token) {
        auto& product = *static_cast<Product *>(context);
        std::lock_guard<std::mutex> lock(product.queue_mutex_);
        if (product.refuse_ || product.queued_work_) return 0;
        product.queued_work_ = work; product.queued_token_ = token;
        return 1;
    }
    static int readback(void *context, oavc_snapshot *out) {
        auto& product = *static_cast<Product *>(context);
        product.check_thread(); ++product.reads_;
        if (product.read_failed_) return -1;
        oavc_snapshot_revision(out, product.generation_, product.revision_);
        oavc_state_input(out, product.input_ ? OAVC_STATE_INPUT_NETWORK : OAVC_STATE_INPUT_ANALOG);
        oavc_state_fault(out, false);
        oavc_state_counter(out, static_cast<int64_t>(product.counter_));
        oavc_state_external_ticks(out, static_cast<int64_t>(product.ticks_));
        for (unsigned i = 0; i < product.channels_; ++i) {
            oavc_state_channel_gain(out, i + 1, product.gain_[i]);
            oavc_state_channel_mute(out, i + 1, product.mute_[i]);
        }
        return 0;
    }
    void physical() const {
        std::printf("STATE %u %u %u %u %llu %llu %.1f %.1f %d %d %u %d %llu\n",
            writes_, callbacks_, wrong_threads_, channels_, static_cast<unsigned long long>(counter_),
            static_cast<unsigned long long>(ticks_), gain_[0], gain_[1], static_cast<int>(mute_[0]),
            static_cast<int>(input_), reads_, static_cast<int>(close_waited_),
            static_cast<unsigned long long>(pending_ ? pending_->token : 0));
        std::fflush(stdout);
    }
};
oavc_outcome oavc_hw_set_gain(void *context, oavc_token token, int64_t channel, double gain) {
    return static_cast<Product *>(context)->command(token, Product::Action::gain, channel, gain);
}
oavc_outcome oavc_hw_set_mute(void *context, oavc_token token, int64_t channel, int mute) {
    return static_cast<Product *>(context)->command(token, Product::Action::mute, channel, mute);
}
oavc_outcome oavc_hw_select_input(void *context, oavc_token token, oavc_select_input_input input) {
    return static_cast<Product *>(context)->command(token, Product::Action::input, 0, input == OAVC_SELECT_INPUT_INPUT_NETWORK);
}
oavc_outcome oavc_hw_increment(void *context, oavc_token token) {
    return static_cast<Product *>(context)->command(token, Product::Action::increment, 0, 0);
}
int main(int argc, char **argv) {
    const bool daemon = argc == 4 && std::string(argv[3]) == "--daemon";
    if ((argc != 3 && !daemon) || (!std::getenv("OPENAVC_TOKEN") && !std::getenv("OPENAVC_PRODUCT_SOCKET"))) return 2;
    std::signal(SIGTERM, request_stop); std::signal(SIGINT, request_stop);
    try { Product product(argv[1], argv[2], std::getenv("OPENAVC_TOKEN")); product.run(daemon); return 0; }
    catch (const std::exception& error) { std::cerr << error.what() << std::endl; return 2; }
}
