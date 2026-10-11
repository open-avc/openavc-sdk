/* Invented single-threaded product. All hardware functions run on its event loop. */
#define _POSIX_C_SOURCE 200809L
#include "commands.h"
#include "adapter.h"
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>

typedef struct {
    pthread_t owner;
    pthread_mutex_t queue_mutex;
    void (*work)(oavc_token);
    oavc_token queued, pending, last;
    int reject_dispatch, read_fail, fail_next, stop, close_waited, pause_feedback;
    unsigned delay_ms, channels, writes, callbacks, wrong_threads, reads;
    int op, channel, input, fault, mute[4];
    double value, gain[4], due;
    uint64_t counter, ticks, revision, generation;
} product;

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static void thread_check(product *p) { if (!pthread_equal(pthread_self(), p->owner)) ++p->wrong_threads; }
static int post(void *context, void (*work)(oavc_token), oavc_token token) {
    product *p = (product *)context;
    pthread_mutex_lock(&p->queue_mutex);
    int accepted = !p->reject_dispatch && !p->work;
    if (accepted) { p->work = work; p->queued = token; }
    pthread_mutex_unlock(&p->queue_mutex);
    return accepted;
}
static void apply(product *p) {
    thread_check(p);
    if (p->op == 1) p->gain[p->channel - 1] = p->value;
    else if (p->op == 2) p->mute[p->channel - 1] = p->value != 0;
    else if (p->op == 3) p->input = (int)p->value;
    else if (p->op == 4) ++p->counter;
    ++p->writes; ++p->revision;
}
static oavc_outcome begin(product *p, oavc_token token, int op, int channel, double value) {
    thread_check(p); ++p->callbacks;
    if (p->pending || channel < 0 || channel > (int)p->channels) return OAVC_FAILED;
    p->op = op; p->channel = channel; p->value = value; p->last = token;
    if (p->delay_ms) {
        p->pending = token; p->due = now() + p->delay_ms / 1000.0; p->delay_ms = 0;
        return OAVC_PENDING;
    }
    apply(p);
    if (p->fail_next) { p->fail_next = 0; return OAVC_FAILED; }
    return OAVC_DONE;
}
oavc_outcome oavc_hw_set_gain(void *c, oavc_token token, int64_t channel, double gain) {
    return begin((product *)c, token, 1, (int)channel, gain);
}
oavc_outcome oavc_hw_set_mute(void *c, oavc_token token, int64_t channel, int mute) {
    return begin((product *)c, token, 2, (int)channel, mute);
}
oavc_outcome oavc_hw_select_input(void *c, oavc_token token, oavc_select_input_input input) {
    return begin((product *)c, token, 3, 0, input == OAVC_SELECT_INPUT_INPUT_NETWORK);
}
oavc_outcome oavc_hw_increment(void *c, oavc_token token) { return begin((product *)c, token, 4, 0, 0); }
static int readback(void *context, oavc_snapshot *out) {
    product *p = (product *)context;
    thread_check(p); ++p->reads;
    if (p->read_fail) return -1;
    oavc_snapshot_revision(out, p->generation, p->revision);
    oavc_state_input(out, p->input ? OAVC_STATE_INPUT_NETWORK : OAVC_STATE_INPUT_ANALOG);
    oavc_state_fault(out, p->fault);
    oavc_state_counter(out, (int64_t)p->counter);
    oavc_state_external_ticks(out, (int64_t)p->ticks);
    for (unsigned i = 0; i < p->channels; ++i) {
        oavc_state_channel_gain(out, i + 1, p->gain[i]);
        oavc_state_channel_mute(out, i + 1, p->mute[i]);
    }
    return 0;
}
static void physical(product *p) {
    printf("STATE %u %u %u %u %llu %llu %.1f %.1f %d %d %u %d %llu\n",
           p->writes, p->callbacks, p->wrong_threads, p->channels,
           (unsigned long long)p->counter, (unsigned long long)p->ticks,
           p->gain[0], p->gain[1], p->mute[0], p->input, p->reads,
           p->close_waited, (unsigned long long)p->pending);
    fflush(stdout);
}
int main(int argc, char **argv) {
    if (argc != 3 || (!getenv("OPENAVC_TOKEN") && !getenv("OPENAVC_PRODUCT_SOCKET"))) return 2;
    product p = {0}; p.owner = pthread_self(); p.channels = 2; p.revision = 1; p.generation = 1;
    for (unsigned i = 0; i < 4; ++i) p.gain[i] = -20;
    pthread_mutex_init(&p.queue_mutex, NULL);
    oavc_adapter_config config = {{1, sizeof(oavc_endpoint_config), getenv("OPENAVC_DEVICE_ID") ? getenv("OPENAVC_DEVICE_ID") : "acme-native", argv[1],
        getenv("OPENAVC_TOKEN"), argv[2], NULL, NULL, NULL}, getenv("OPENAVC_PRODUCT_SOCKET"), &p, readback, post, 1000};
    char error[256] = {0};
    oavc_adapter *adapter = oavc_adapter_open(&config, error, sizeof(error));
    if (!adapter) { fprintf(stderr, "%s\n", error); return 2; }
    oavc_adapter_refresh(adapter);
    printf("READY %u\n", oavc_adapter_port(adapter)); fflush(stdout);
    int closed = 0;
    while (!closed) {
        void (*work)(oavc_token) = NULL; oavc_token token = 0;
        pthread_mutex_lock(&p.queue_mutex);
        work = p.work; token = p.queued; p.work = NULL;
        pthread_mutex_unlock(&p.queue_mutex);
        if (work) work(token);
        if (p.pending && now() >= p.due) {
            apply(&p);
            oavc_complete(p.pending, p.fail_next ? OAVC_FAILED : OAVC_DONE);
            p.fail_next = 0; p.pending = 0;
        }
        if (p.stop) {
            closed = oavc_adapter_try_close(adapter);
            if (!closed) p.close_waited = 1;
        } else if (!p.pause_feedback) oavc_adapter_refresh(adapter);
        if (closed) break;
        fd_set input; FD_ZERO(&input); FD_SET(STDIN_FILENO, &input);
        struct timeval wait = {0, 1000};
        if (select(STDIN_FILENO + 1, &input, NULL, NULL, &wait) <= 0) continue;
        char line[128];
        if (!fgets(line, sizeof(line), stdin)) { oavc_adapter_stop(adapter); p.stop = 1; continue; }
        unsigned number; double gain;
        if (sscanf(line, "delay %u", &number) == 1) p.delay_ms = number;
        else if (sscanf(line, "external %lf", &gain) == 1) { p.gain[1] = gain; ++p.ticks; ++p.revision; }
        else if (sscanf(line, "channels %u", &number) == 1 && number <= 4) { p.channels = number; ++p.revision; }
        else if (sscanf(line, "generation %u", &number) == 1 && number) { p.generation = number; ++p.revision; }
        else if (sscanf(line, "readfail %u", &number) == 1) p.read_fail = number != 0;
        else if (sscanf(line, "pause-feedback %u", &number) == 1) p.pause_feedback = number != 0;
        else if (sscanf(line, "refuse %u", &number) == 1) {
            pthread_mutex_lock(&p.queue_mutex); p.reject_dispatch = number != 0; pthread_mutex_unlock(&p.queue_mutex);
        }
        else if (!strcmp(line, "fail-next\n")) p.fail_next = 1;
        else if (!strcmp(line, "late\n")) { printf("LATE %d\n", oavc_complete(p.last, OAVC_DONE)); fflush(stdout); continue; }
        else if (!strcmp(line, "stop\n")) { oavc_adapter_stop(adapter); p.stop = 1; }
        else if (strcmp(line, "snapshot\n")) return 3;
        physical(&p);
    }
    physical(&p);
    pthread_mutex_destroy(&p.queue_mutex);
    printf("CLOSED\n"); fflush(stdout);
    return p.wrong_threads ? 4 : 0;
}
