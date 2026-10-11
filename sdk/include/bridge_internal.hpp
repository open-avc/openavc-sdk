// Both ends are supplied by the SDK; manufacturers do not implement this format.
#ifndef OPENAVC_BRIDGE_INTERNAL_HPP
#define OPENAVC_BRIDGE_INTERNAL_HPP
#include "value_types.hpp"
#include "protocol.h"
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <poll.h>
#include <unistd.h>
#include <openssl/sha.h>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fstream>
#include <stdexcept>
using bridge_json = nlohmann::json;
struct bridge_fd {
    int value;
    ~bridge_fd() { if (value >= 0) ::close(value); }
};
inline std::string bridge_model_hash(const char *path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error("Cannot read the equipment model.");
    std::string bytes((std::istreambuf_iterator<char>(input)), {});
    if (bytes.empty() || bytes.size() > OAVC_MAX_MESSAGE_BYTES) throw std::runtime_error("Invalid equipment model size.");
    unsigned char digest[SHA256_DIGEST_LENGTH];
    SHA256(reinterpret_cast<const unsigned char *>(bytes.data()), bytes.size(), digest);
    static const char hex[] = "0123456789abcdef";
    std::string result;
    for (auto byte : digest) { result += hex[byte >> 4]; result += hex[byte & 15]; }
    return result;
}
inline sockaddr_un bridge_address(const std::string& path) {
    sockaddr_un result{}; result.sun_family = AF_UNIX;
    if (path.empty() || path.size() >= sizeof(result.sun_path)) throw std::runtime_error("Invalid local socket path.");
    std::memcpy(result.sun_path, path.c_str(), path.size() + 1);
    return result;
}
inline void bridge_peer(int fd) {
    ucred peer{}; socklen_t size = sizeof(peer);
    if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &peer, &size) || peer.uid != geteuid())
        throw std::runtime_error("Local process user does not match the product.");
}
inline void bridge_bytes(int fd, void *data, size_t size, bool send,
                         std::chrono::steady_clock::time_point deadline) {
    size_t offset = 0;
    while (offset < size) {
        const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now()).count();
        if (left <= 0) throw std::runtime_error("Local reply deadline expired.");
        pollfd ready{fd, static_cast<short>(send ? POLLOUT : POLLIN), 0};
        const int code = poll(&ready, 1, static_cast<int>(left));
        if (code < 0 && errno == EINTR) continue;
        if (code <= 0 || !(ready.revents & ready.events)) throw std::runtime_error("Local connection ended.");
        auto *bytes = static_cast<char *>(data) + offset;
        const auto count = send ? ::send(fd, bytes, size - offset, MSG_NOSIGNAL | MSG_DONTWAIT)
                                : ::recv(fd, bytes, size - offset, MSG_DONTWAIT);
        if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (count <= 0) throw std::runtime_error("Local connection ended.");
        offset += static_cast<size_t>(count);
    }
}
inline bridge_json bridge_read(int fd) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
    uint32_t length = 0;
    bridge_bytes(fd, &length, sizeof(length), false, deadline); length = ntohl(length);
    if (!length || length > OAVC_MAX_MESSAGE_BYTES) throw std::runtime_error("Invalid local frame size.");
    std::string bytes(length, '\0'); bridge_bytes(fd, bytes.data(), length, false, deadline);
    return bridge_json::parse(bytes);
}
inline void bridge_write(int fd, const bridge_json& value) {
    std::string bytes = value.dump();
    if (bytes.empty() || bytes.size() > OAVC_MAX_MESSAGE_BYTES) throw std::runtime_error("Invalid local frame size.");
    uint32_t length = htonl(static_cast<uint32_t>(bytes.size()));
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
    bridge_bytes(fd, &length, sizeof(length), true, deadline);
    bridge_bytes(fd, bytes.data(), bytes.size(), true, deadline);
}
inline bridge_json bridge_exchange(const std::string& path, const bridge_json& request) {
    bridge_fd fd{::socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0)};
    const auto address = bridge_address(path);
    if (fd.value < 0 || ::connect(fd.value, reinterpret_cast<const sockaddr *>(&address), sizeof(address)))
        throw std::runtime_error("Product integration is unavailable.");
    bridge_peer(fd.value); bridge_write(fd.value, request); return bridge_read(fd.value);
}
#endif
