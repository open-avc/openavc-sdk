// OpenAVC equipment endpoint, backed by manufacturer hardware callbacks.
#include "endpoint.h"
#include "civetweb.h"
#include "json.hpp"
#include "value_types.hpp"
#include "protocol.h"
#include <openssl/crypto.h>
#include <openssl/rand.h>
#include <atomic>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <stdexcept>
#include <thread>
#include <vector>

using json = nlohmann::json;
using namespace std::chrono_literals;
constexpr uint64_t safe_integer = 9007199254740991ULL;
struct refused : std::runtime_error {
    int status; std::string code;
    refused(int status, std::string code, const char *message)
        : std::runtime_error(message), status(status), code(std::move(code)) {}
};

static json strict_json(const std::string& body) {
    std::vector<std::set<std::string>> keys;
    return json::parse(body, [&](int depth, json::parse_event_t event, json& value) {
        if (depth > 16) throw refused(422, "invalid_json", "JSON nesting limit exceeded.");
        if (event == json::parse_event_t::object_start) keys.emplace_back();
        if (event == json::parse_event_t::object_end) keys.pop_back();
        if (event == json::parse_event_t::key && !keys.back().insert(value.get<std::string>()).second)
            throw refused(422, "invalid_json", "Duplicate JSON field.");
        return true;
    });
}

static void validate(const json& value, const json& rule) {
    const auto type = rule.value("type", "string");
    const bool correct = type == "boolean" ? value.is_boolean() :
        type == "integer" ? value.is_number_integer() :
        type == "number" || type == "float" ? value.is_number() :
        type == "enum" ? value.is_primitive() && !value.is_null() : value.is_string();
    if (!correct) throw refused(422, "invalid_parameter", "Parameter type does not match its description.");
    if (value.is_string()) {
        const auto& text = value.get_ref<const std::string&>();
        if (text.find('\0') != std::string::npos)
            throw refused(422, "invalid_parameter", "Text cannot contain a null character.");
    }
    if (value.is_number() && !std::isfinite(value.get<double>()))
        throw refused(422, "invalid_parameter", "Use finite numbers.");
    if ((rule.contains("min") && value < rule["min"]) || (rule.contains("max") && value > rule["max"]))
        throw refused(422, "invalid_parameter", "Parameter is outside its range.");
    if (rule.contains("values")) {
        const auto& choices = rule["values"];
        const auto found = std::find_if(choices.begin(), choices.end(), [&](const json& choice) {
            return (choice.is_object() ? choice.at("value") : choice) == value;
        });
        if (found == choices.end()) throw refused(422, "invalid_parameter", "Parameter is not an allowed choice.");
    }
}

static std::string fresh_session() {
    unsigned char bytes[16];
    if (RAND_bytes(bytes, sizeof(bytes)) != 1) throw std::runtime_error("Could not obtain session entropy.");
    const char *hex = "0123456789abcdef";
    std::string result;
    for (auto byte : bytes) { result += hex[byte >> 4]; result += hex[byte & 15]; }
    return result;
}

struct job {
    uint64_t id, generation;
    std::string signature;
    oavc_command command;
    json result;
    bool done = false;
};

struct oavc_endpoint {
    oavc_endpoint_config config;
    std::string identity, token, certificate, session = fresh_session();
    json model, observed;
    std::mutex mutex;
    std::condition_variable changed;
    std::deque<std::shared_ptr<job>> queue;
    using request_key = std::pair<std::string, uint64_t>;
    std::map<request_key, std::shared_ptr<job>> results;
    std::deque<request_key> retention_order;
    std::map<std::string, uint64_t> highest;
    uint64_t generation = 0;
    unsigned pending = 0, waiters = 0, port = 0;
    bool available = false, stopping = false, completion_uncertain = false;
    unsigned hardware_pending = 0;
    bool hardware_uncertain = true, confirmation_pending = false;
    uint64_t confirmation_generation = 0;
    std::thread worker, poller;
    mg_context *http = nullptr;

    explicit oavc_endpoint(const oavc_endpoint_config& c) : config(c), identity(c.identity), token(c.token), certificate(c.certificate_path) {
        std::ifstream file(c.model_path);
        if (!file) throw std::runtime_error("Could not open capability model.");
        model = json::parse(file);
        if (model.dump().size() > OAVC_MAX_MESSAGE_BYTES) throw std::runtime_error("Equipment model exceeds the 1 MiB protocol limit.");
        if (!model.contains("commands") || !model.contains("state_variables"))
            throw std::runtime_error("Capability model is incomplete.");
        static std::once_flag initialized;
        std::call_once(initialized, [] {
            if (!(mg_init_library(MG_FEATURES_TLS) & MG_FEATURES_TLS))
                throw std::runtime_error("HTTP library TLS initialization failed.");
        });
        const char *options[] = {"listening_ports", "127.0.0.1:0s", "ssl_certificate", certificate.c_str(),
            "num_threads", "8", "prespawn_threads", "8", "request_timeout_ms", "2000",
            "max_request_size", "8192", "enable_keep_alive", "no", nullptr};
        http = mg_start(nullptr, this, options);
        if (!http) throw std::runtime_error("Could not start loopback HTTPS endpoint.");
        mg_server_port bound{};
        if (mg_get_server_ports(http, 1, &bound) != 1 || !bound.is_ssl) {
            mg_stop(http); http = nullptr;
            throw std::runtime_error("HTTPS listener did not bind.");
        }
        port = bound.port;
        mg_set_request_handler(http, "/", &request, this);
        try {
            worker = std::thread([this] { work(); });
            poller = std::thread([this] { poll(); });
        } catch (...) { close(); throw; }
    }

    void close() {
        { std::lock_guard<std::mutex> lock(mutex); stopping = true; changed.notify_all(); }
        if (http) { mg_stop(http); http = nullptr; }
        if (worker.joinable()) worker.join();
        if (poller.joinable()) poller.join();
    }
    ~oavc_endpoint() { close(); }

    void poll() {
        for (;;) {
            oavc_snapshot snapshot;
            bool valid = false;
            json next;
            try {
                valid = config.readback(config.context, &snapshot) == 0 && !snapshot.invalid && snapshot.generation && snapshot.revision <= safe_integer;
                if (valid) {
                    if (snapshot.state.size() != model["state_variables"].size()) throw std::runtime_error("Incomplete state.");
                    for (const auto& item : snapshot.state.items()) validate(item.value(), model.at("state_variables").at(item.key()));
                    for (const auto& child : snapshot.children) {
                        const auto& definition = model.at("child_entity_types").at(child.at("type").get<std::string>());
                        auto id_rule = definition.value("id_format", json::object());
                        if (!id_rule.contains("type")) id_rule["type"] = "integer";
                        validate(child.at("id"), id_rule);
                        if (child.at("id").is_string()) {
                            const auto id = child.at("id").get<std::string>();
                            if (id.empty() || id.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-") != std::string::npos)
                                throw std::runtime_error("Invalid child ID.");
                        }
                        if (child.at("state").size() != definition.at("state_variables").size()) throw std::runtime_error("Incomplete child.");
                        for (const auto& field : child.at("state").items()) validate(field.value(), definition.at("state_variables").at(field.key()));
                    }
                    next = {{"identity", identity}, {"generation", identity + ":" + std::to_string(snapshot.generation)},
                            {"revision", snapshot.revision}, {"quality", "current"},
                            {"state", snapshot.state}, {"children", snapshot.children}};
                    if (next.dump().size() > OAVC_MAX_MESSAGE_BYTES) throw std::runtime_error("Snapshot exceeds the 1 MiB protocol limit.");
                }
            } catch (...) { valid = false; }
            std::unique_lock<std::mutex> lock(mutex);
            if (stopping) return;
            if (valid) {
                if (confirmation_pending) {
                    completion_uncertain = completion_uncertain || snapshot.generation != confirmation_generation;
                    confirmation_pending = false;
                }
                hardware_pending = snapshot.pending_commands;
                hardware_uncertain = snapshot.completion_uncertain;
                if (generation && generation != snapshot.generation) {
                    session = fresh_session(); highest.clear(); results.clear(); retention_order.clear();
                }
                generation = snapshot.generation;
                next["session"] = session; observed = std::move(next);
            }
            available = valid;
            changed.notify_all();
            changed.wait_for(lock, 20ms, [this] { return stopping; });
            if (stopping) return;
        }
    }

    void work() {
        for (;;) {
            std::unique_lock<std::mutex> lock(mutex);
            changed.wait(lock, [this] { return stopping || !queue.empty(); });
            if (queue.empty() && stopping) return;
            auto current = queue.front(); queue.pop_front();
            bool usable = available && current->generation == generation;
            lock.unlock();
            int code = -4;
            if (usable) {
                try { code = config.command(config.context, &current->command); }
                catch (...) { code = -4; }
            }
            lock.lock();
            const bool uncertain = code == -4 || current->generation != generation;
            current->result = {{"id", current->id}, {"outcome", uncertain ? "uncertain" : code == -6 ? "failed" : code ? "rejected" : "completed"}, {"hardware_status", code}};
            completion_uncertain = completion_uncertain || uncertain;
            if (!available) {
                confirmation_pending = true;
                confirmation_generation = current->generation;
            }
            current->done = true; --pending; changed.notify_all();
        }
    }

    static std::string request_client(mg_connection *connection) {
        const char *header = mg_get_header(connection, "X-OpenAVC-Client");
        const std::string client = header ? header : "";
        if (client.size() != 32 || client.find_first_not_of("0123456789abcdef") != std::string::npos)
            throw refused(422, "invalid_client", "Supply the SDK client identifier.");
        return client;
    }

    json submit(const std::string& client, const json& body) {
        if (!body.is_object() || body.size() != 4 || !body.contains("session") || !body.contains("id") || !body.contains("command") || !body.contains("params"))
            throw refused(422, "invalid_request", "Supply session, id, command and params.");
        if (!body["id"].is_number_integer() || body["id"] < 1 || body["id"] > safe_integer)
            throw refused(422, "invalid_request", "Use a positive safe integer request id.");
        if (!body["command"].is_string() || !body["params"].is_object())
            throw refused(422, "invalid_request", "Use a command name and parameter object.");
        const auto name = body["command"].get<std::string>();
        if (!model["commands"].contains(name)) throw refused(422, "unknown_command", "Choose a described command.");
        auto params = body["params"];
        const auto rules = model["commands"][name].value("params", json::object());
        for (const auto& item : params.items())
            if (!rules.contains(item.key())) throw refused(422, "invalid_parameter", "Parameter is not described by this command.");
        for (const auto& rule : rules.items()) {
            if (!params.contains(rule.key()) || params[rule.key()].is_null()) {
                if (rule.value().value("required", false)) throw refused(422, "invalid_parameter", "A required parameter is missing.");
                params.erase(rule.key());
                if (rule.value().contains("default") && !rule.value()["default"].is_null()) params[rule.key()] = rule.value()["default"];
                else continue;
            }
            auto value_rule = rule.value();
            if (value_rule.value("type", "string") == "child_id") {
                const auto& child = model.at("child_entity_types").at(value_rule.at("child_type").get<std::string>());
                value_rule = child.value("id_format", json::object());
                if (!value_rule.contains("type")) value_rule["type"] = "integer";
            }
            validate(params[rule.key()], value_rule);
        }
        const uint64_t id = body["id"].get<uint64_t>();
        const auto signature = body.dump();
        std::unique_lock<std::mutex> lock(mutex);
        if (stopping) throw refused(503, "stopping", "Endpoint is stopping.");
        if (!available) throw refused(503, "hardware_unavailable", "Hardware readback is unavailable.");
        if (completion_uncertain || hardware_uncertain || confirmation_pending)
            throw refused(503, "completion_unconfirmed", "An earlier hardware command could not be confirmed. Check product diagnostics.");
        if (body["session"] != session) throw refused(409, "session_changed", "Read a fresh description; do not replay old commands.");
        const request_key key{client, id};
        if (results.count(key)) {
            auto previous = results.at(key);
            if (previous->signature != signature) throw refused(409, "request_conflict", "Request id already identifies different work.");
            json response = previous->result; response["duplicate"] = true; return response;
        }
        const auto history = highest.find(client);
        if (history != highest.end() && id <= history->second)
            throw refused(409, "request_expired", "Result expired; inspect readback before new work.");
        // Never evict a client high-water mark within a session: doing so permits old work to run again.
        if (history == highest.end() && highest.size() >= 128)
            throw refused(429, "client_capacity", "Client history capacity is full for this endpoint session.");
        if (pending >= 2) throw refused(429, "busy", "Command capacity is full.");
        auto current = std::make_shared<job>();
        current->id = id; current->generation = generation; current->signature = signature;
        current->command = {name, params}; current->result = {{"id", id}, {"outcome", "pending"}};
        results[key] = current; highest[client] = id; retention_order.push_back(key);
        ++pending; queue.push_back(current);
        while (results.size() > 128) {
            auto oldest = std::find_if(retention_order.begin(), retention_order.end(),
                [&](const request_key& candidate) { return results.at(candidate)->done; });
            if (oldest == retention_order.end()) throw std::runtime_error("Result retention invariant failed.");
            results.erase(*oldest); retention_order.erase(oldest);
        }
        changed.notify_all();
        if (!changed.wait_for(lock, 200ms, [&] { return current->done; }))
            return {{"id", id}, {"outcome", "uncertain"}, {"message", "Hardware has not confirmed completion. Read status and readback; do not replay."}};
        return current->result;
    }

    static int reply(mg_connection *connection, int status, const json& body) {
        auto bytes = body.dump();
        if (bytes.size() > OAVC_MAX_MESSAGE_BYTES) {
            status = 413;
            bytes = R"({"error":"response_limit","message":"Equipment data exceeds the 1 MiB protocol limit."})";
        }
        mg_printf(connection, "HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %zu\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
                  status, mg_get_response_code_text(connection, status), bytes.size());
        mg_write(connection, bytes.data(), bytes.size());
        return status;
    }

    static int request(mg_connection *connection, void *data) {
        auto& self = *static_cast<oavc_endpoint *>(data);
        try { return self.handle(connection); }
        catch (const refused& e) { return reply(connection, e.status, {{"error", e.code}, {"message", e.what()}, {"quality", "unavailable"}}); }
        catch (const json::exception&) { return reply(connection, 422, {{"error", "invalid_json"}}); }
        catch (...) { return reply(connection, 500, {{"error", "internal_error"}}); }
    }

    int handle(mg_connection *connection) {
        const auto *info = mg_get_request_info(connection);
        const std::string path = info->local_uri, method = info->request_method;
        const char *header = mg_get_header(connection, "Authorization");
        const std::string expected = "Bearer " + token;
        if (!header || expected.size() != std::strlen(header) || CRYPTO_memcmp(expected.data(), header, expected.size()))
            throw refused(401, "unauthorized", "Use the configured credential.");
        if (path == "/v1/command" && method == "POST") {
            if (info->content_length < 0 || info->content_length > OAVC_MAX_MESSAGE_BYTES) throw refused(413, "body_limit", "Use a body of at most 1 MiB with Content-Length.");
            std::string body(static_cast<size_t>(info->content_length), '\0');
            size_t offset = 0;
            while (offset < body.size()) {
                auto got = mg_read(connection, body.data() + offset, body.size() - offset);
                if (got <= 0) throw refused(400, "incomplete_body", "Incomplete request body.");
                offset += static_cast<size_t>(got);
            }
            auto result = submit(request_client(connection), strict_json(body));
            auto outcome = result.at("outcome").get<std::string>();
            return reply(connection, outcome == "completed" ? 200 : (outcome == "rejected" || outcome == "failed") ? 409 : 202, result);
        }
        if (method != "GET") throw refused(405, "method_not_allowed", "Use a supported method.");
        std::unique_lock<std::mutex> lock(mutex);
        json body;
        if (path == "/health") body = {{"pending", std::max(pending, hardware_pending)}, {"completion_uncertain", !available || completion_uncertain || hardware_uncertain || confirmation_pending}, {"result_cache", results.size()}, {"quality", available ? "current" : "unavailable"}};
        else if (path.rfind("/v1/commands/", 0) == 0) {
            const auto text = path.substr(13);
            if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos || text.size() > 16)
                throw refused(422, "invalid_request", "Use a safe integer request id.");
            const auto id = std::stoull(text);
            const request_key key{request_client(connection), id};
            if (!results.count(key)) throw refused(404, "unknown_request", "No retained result.");
            body = results.at(key)->result;
        } else if (path == "/v1/snapshot" || path == "/v1/description" || path == "/v1/changes") {
            if (!available) throw refused(503, "hardware_unavailable", "Hardware readback is unavailable.");
            if (path == "/v1/description") {
                body = {{"protocol", OAVC_PROTOCOL}, {"identity", identity}, {"session", session}, {"capabilities", model},
                        {"events", "/v1/changes"}, {"event_transport", "https-long-poll"}, {"channels", json::array()}};
                for (const auto& child : observed["children"]) body["channels"].push_back(child["id"]);
            } else {
                if (path == "/v1/changes") {
                    char revision[32] = {}, gen[128] = {};
                    const std::string query = info->query_string ? info->query_string : "";
                    if (mg_get_var(query.data(), query.size(), "revision", revision, sizeof(revision)) < 0 ||
                        mg_get_var(query.data(), query.size(), "generation", gen, sizeof(gen)) < 0)
                        throw refused(422, "invalid_request", "Supply generation and revision.");
                    if (waiters >= 4) throw refused(429, "subscription_limit", "Four waiting change requests are already active.");
                    ++waiters;
                    changed.wait_for(lock, 250ms, [&] { return stopping || !available || observed["revision"].dump() != revision || observed["generation"] != gen; });
                    --waiters;
                    if (stopping || !available) throw refused(503, "hardware_unavailable", "Readback is unavailable; reacquire a snapshot.");
                }
                body = observed;
            }
        } else throw refused(404, "unknown_path", "Use a supported endpoint and protocol version.");
        lock.unlock();
        return reply(connection, 200, body);
    }
};

static void publish(oavc_snapshot *s, const char *type, oavc_child_id child_id, const char *key, const json& value) {
    try {
        if (!key || !*key) throw std::runtime_error("Invalid field.");
        if (!type) s->state[key] = value;
        else {
            if (child_id.is_string && !child_id.text) throw std::runtime_error("Missing child ID.");
            const json id = child_id.is_string ? json(child_id.text) : json(child_id.number);
            if (!*type) throw std::runtime_error("Invalid child.");
            auto child = std::find_if(s->children.begin(), s->children.end(), [&](const json& c) { return c["type"] == type && c["id"] == id; });
            if (child == s->children.end()) {
                s->children.push_back({{"type", type}, {"id", id}, {"state", json::object()}});
                child = std::prev(s->children.end());
            }
            (*child)["state"][key] = value;
        }
    } catch (...) { s->invalid = true; }
}

extern "C" {
oavc_endpoint *oavc_open(const oavc_endpoint_config *config, char *error, size_t size) {
    try {
        if (!config || config->abi != 1 || config->size != sizeof(oavc_endpoint_config) || !config->identity || !config->token ||
            !config->model_path || !config->certificate_path || !config->readback || !config->command || std::strlen(config->token) < 32)
            throw std::runtime_error("Supply ABI 1 configuration, callbacks, model, TLS certificate and a strong credential.");
        return new oavc_endpoint(*config);
    } catch (const std::exception& e) { if (error && size) std::snprintf(error, size, "%s", e.what()); return nullptr; }
}
unsigned oavc_port(oavc_endpoint *endpoint) { return endpoint ? endpoint->port : 0; }
void oavc_close(oavc_endpoint *endpoint) { delete endpoint; }
void oavc_snapshot_revision(oavc_snapshot *s, uint64_t generation, uint64_t revision) { s->generation = generation; s->revision = revision; }
void oavc_snapshot_invalidate(oavc_snapshot *s) { s->invalid = true; }
void oavc_state_number(oavc_snapshot *s, const char *type, oavc_child_id id, const char *key, double value) {
    if (!std::isfinite(value)) { s->invalid = true; return; } publish(s, type, id, key, value);
}
void oavc_state_integer(oavc_snapshot *s, const char *type, oavc_child_id id, const char *key, uint64_t value) {
    if (value > safe_integer) { s->invalid = true; return; } publish(s, type, id, key, value);
}
void oavc_state_signed_integer(oavc_snapshot *s, const char *type, oavc_child_id id, const char *key, int64_t value) {
    if (value < -static_cast<int64_t>(safe_integer) || value > static_cast<int64_t>(safe_integer)) { s->invalid = true; return; }
    publish(s, type, id, key, value);
}
void oavc_state_boolean(oavc_snapshot *s, const char *type, oavc_child_id id, const char *key, int value) { publish(s, type, id, key, value != 0); }
void oavc_state_string(oavc_snapshot *s, const char *type, oavc_child_id id, const char *key, const char *value) {
    if (!value) { s->invalid = true; return; }
    try {
        const json text = value;
        (void)text.dump(); // Reject malformed UTF-8 before a snapshot becomes current.
        publish(s, type, id, key, text);
    } catch (...) { s->invalid = true; }
}
const char *oavc_command_name(const oavc_command *c) { return c->name.c_str(); }
int oavc_parameter_present(const oavc_command *c, const char *key) {
    return c && key && c->params.contains(key) && !c->params.at(key).is_null();
}
int oavc_parameter_equals_json(const oavc_command *c, const char *key, const char *value) {
    try { return c && key && value && c->params.at(key) == json::parse(value); }
    catch (...) { return 0; }
}
int oavc_parameter_number(const oavc_command *c, const char *key, double *out) {
    try { if (!out || !c->params.at(key).is_number()) return -1; *out = c->params.at(key).get<double>(); return 0; } catch (...) { return -1; }
}
int oavc_parameter_integer(const oavc_command *c, const char *key, int64_t *out) {
    try { if (!out || !c->params.at(key).is_number_integer()) return -1; *out = c->params.at(key).get<int64_t>(); return 0; } catch (...) { return -1; }
}
int oavc_parameter_boolean(const oavc_command *c, const char *key, int *out) {
    try { if (!out || !c->params.at(key).is_boolean()) return -1; *out = c->params.at(key).get<bool>(); return 0; } catch (...) { return -1; }
}
int oavc_parameter_string(const oavc_command *c, const char *key, const char **out) {
    try {
        if (!out || !c->params.at(key).is_string()) return -1;
        const auto& value = c->params.at(key).get_ref<const std::string&>();
        if (value.find('\0') != std::string::npos) return -1;
        *out = value.c_str();
        return 0;
    } catch (...) { return -1; }
}
}
