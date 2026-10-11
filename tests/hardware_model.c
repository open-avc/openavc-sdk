/* A small product used to verify generated C callbacks over the real endpoint. */
#include "commands.h"
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    pthread_mutex_t mutex;
    int64_t writes;
    double gain;
    int mute_present, mute;
    char label[128];
} hardware;

oavc_outcome oavc_hw_configure(void *context, oavc_token operation, const char *output,
                              int64_t input, oavc_configure_gain_optional gain,
                              oavc_configure_label_optional label,
                              oavc_configure_mute_optional mute,
                              oavc_configure_mode_optional mode) {
    (void)operation;
    hardware *h = context;
    if (strcmp(output, "Main-L") || input != 2 || !gain.present ||
        (mode.present && mode.value != OAVC_CONFIGURE_MODE_ANALOG &&
         mode.value != OAVC_CONFIGURE_MODE_VALUE_3 && mode.value != OAVC_CONFIGURE_MODE_USB_C)) return OAVC_REFUSED;
    pthread_mutex_lock(&h->mutex);
    h->gain = gain.value;
    h->mute_present = mute.present;
    h->mute = mute.present ? mute.value : 0;
    if (label.present) snprintf(h->label, sizeof(h->label), "%s", label.value);
    ++h->writes;
    pthread_mutex_unlock(&h->mutex);
    return OAVC_DONE;
}

static int readback(void *context, oavc_snapshot *snapshot) {
    hardware *h = context;
    pthread_mutex_lock(&h->mutex);
    oavc_snapshot_revision(snapshot, 1, (uint64_t)h->writes + 1);
    oavc_state_writes(snapshot, h->writes);
    oavc_state_mute_present(snapshot, h->mute_present);
    oavc_state_mute(snapshot, h->mute);
    oavc_state_label(snapshot, h->label);
    oavc_state_revision(snapshot, 42);
    oavc_state_quality(snapshot, "manufacturer-value");
    oavc_state_reading(snapshot, 1.25);
    oavc_state_selection(snapshot, OAVC_STATE_SELECTION_VALUE_3);
    char data[40001];
    memset(data, 'x', sizeof(data) - 1); data[sizeof(data) - 1] = 0;
    oavc_state_data(snapshot, data);
    oavc_state_output_gain(snapshot, "Main-L", h->gain);
    oavc_state_input_active(snapshot, 2, 1);
    for (unsigned index = 0; index < 128; ++index) {
        char channel[32];
        snprintf(channel, sizeof(channel), "Output-%u", index);
        oavc_state_output_gain(snapshot, channel, -20);
    }
    pthread_mutex_unlock(&h->mutex);
    return 0;
}

static int command(void *context, const oavc_command *request) {
    return oavc_dispatch(context, 0, request) == OAVC_DONE ? 0 : -1;
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    hardware h = {0};
    pthread_mutex_init(&h.mutex, NULL);
    oavc_endpoint_config config = {1, sizeof(oavc_endpoint_config), "acme-test-unit", argv[1],
                         getenv("OPENAVC_TEST_TOKEN"), argv[2], &h, readback, command};
    char error[256] = {0};
    oavc_endpoint *endpoint = oavc_open(&config, error, sizeof(error));
    if (!endpoint) { fprintf(stderr, "%s\n", error); return 2; }
    printf("READY %u\n", oavc_port(endpoint)); fflush(stdout);
    (void)getchar();
    oavc_close(endpoint);
    pthread_mutex_destroy(&h.mutex);
    return 0;
}
