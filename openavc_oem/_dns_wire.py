"""Generated DNS wire helpers from the pinned OpenAVC sources."""
import struct
import socket

DNS_TYPE_A = 1

DNS_TYPE_PTR = 12

DNS_TYPE_TXT = 16

DNS_TYPE_SRV = 33

DNS_CLASS_IN = 1

def encode_dns_name(name: str) -> bytes:
    """Encode a DNS name as wire format labels.

    Example: 'example.local.' -> b'\\x07example\\x05local\\x00'
    """
    result = b""
    # Strip trailing dot if present
    if name.endswith("."):
        name = name[:-1]
    for label in name.split("."):
        encoded = label.encode("utf-8")
        result += struct.pack("B", len(encoded)) + encoded
    result += b"\x00"  # Root label
    return result


def decode_dns_name(
    data: bytes, offset: int, end: int | None = None
) -> tuple[str, int]:
    """Decode a DNS wire format name, handling compression pointers.

    Returns (name_string, new_offset) where new_offset points past the name
    in the original data (after the compression pointer if one was used).

    ``end`` bounds the initial (uncompressed) reading of the name's own
    label bytes. Pass it when the name lives inside a resource record's
    rdata window (``offset + rdlength``) so a short/forged rdlength can't
    let the name bleed into the following record. Compression pointers
    inside the name may still legally jump anywhere earlier in the
    message, so the bound is only enforced until the first pointer is
    followed. Callers decoding a record's own (self-delimiting) owner name
    leave ``end`` unset.
    """
    labels: list[str] = []
    # return_offset tracks where to resume in the original data stream.
    # It's set the first time we encounter a compression pointer.
    return_offset: int | None = None
    # Two separate bounds against malformed packets: pointers followed, and
    # labels read. A name is at most 255 bytes, so at most 127 labels; an
    # IPv6 reverse-address name alone has 34, so labels must not count
    # against the pointer limit.
    max_jumps = 20
    max_labels = 127
    jumps = 0
    visited: set[int] = set()  # Track visited pointer offsets to detect cycles

    while len(labels) < max_labels:
        if offset >= len(data):
            break
        # While still reading the name's own bytes (before any compression
        # pointer), keep them inside the rdata window when one was given.
        if end is not None and return_offset is None and offset >= end:
            break

        length = data[offset]

        if length == 0:
            # End of name
            if return_offset is None:
                return_offset = offset + 1
            break
        elif (length & 0xC0) == 0xC0:
            # Compression pointer (2 bytes)
            if offset + 1 >= len(data):
                break
            if end is not None and return_offset is None and offset + 2 > end:
                break  # Pointer straddles the rdata boundary — reject
            if return_offset is None:
                # Save where to continue reading after this name
                return_offset = offset + 2
            pointer = struct.unpack("!H", data[offset:offset + 2])[0] & 0x3FFF
            if pointer in visited or jumps >= max_jumps:
                break  # Cycle or runaway chain — return what we have
            visited.add(pointer)
            jumps += 1
            offset = pointer
        else:
            # Normal label
            offset += 1
            if offset + length > len(data):
                break
            if end is not None and return_offset is None and offset + length > end:
                break  # Label body spills past the rdata boundary — reject
            label = data[offset:offset + length].decode("utf-8", errors="replace")
            labels.append(label)
            offset += length

    name = ".".join(labels)
    return name, return_offset if return_offset is not None else offset


DEFAULT_TTL = 4500

def encode_txt_rdata(pairs: dict[str, str]) -> bytes:
    """Encode key=value pairs as DNS TXT record rdata.

    Inverse of ``_parse_txt_rdata`` in mdns_scanner.py.
    Each pair becomes a length-prefixed UTF-8 string.
    """
    result = b""
    for key, value in pairs.items():
        entry = f"{key}={value}".encode("utf-8")
        if len(entry) > 255:
            entry = entry[:255]
        result += struct.pack("B", len(entry)) + entry
    if not result:
        # RFC 6763 Section 6.1: empty TXT must have single zero byte
        result = b"\x00"
    return result


def _build_resource_record(
    name: str, rtype: int, rclass: int, ttl: int, rdata: bytes
) -> bytes:
    """Build a single DNS resource record in wire format."""
    name_bytes = encode_dns_name(name)
    return name_bytes + struct.pack("!HHIH", rtype, rclass, ttl, len(rdata)) + rdata


def build_announcement_records(
    instance_name: str,
    service_type: str,
    hostname: str,
    ip: str,
    port: int,
    txt_pairs: dict[str, str],
    ttl: int = DEFAULT_TTL,
) -> list[bytes]:
    """Build the full DNS-SD record set for service announcement.

    Returns a list of 4 resource records (PTR, SRV, TXT, A).
    """
    instance_fqdn = f"{instance_name}.{service_type}"
    hostname_fqdn = f"{hostname}.local."

    # PTR: _openavc._tcp.local. -> <instance>._openavc._tcp.local.
    ptr_rdata = encode_dns_name(instance_fqdn)
    ptr_record = _build_resource_record(
        service_type, DNS_TYPE_PTR, DNS_CLASS_IN, ttl, ptr_rdata
    )

    # SRV: <instance>._openavc._tcp.local. -> <hostname>.local. port <port>
    srv_rdata = struct.pack("!HHH", 0, 0, port) + encode_dns_name(hostname_fqdn)
    srv_record = _build_resource_record(
        instance_fqdn, DNS_TYPE_SRV, DNS_CLASS_IN, ttl, srv_rdata
    )

    # TXT: <instance>._openavc._tcp.local. -> key=value metadata
    txt_rdata = encode_txt_rdata(txt_pairs)
    txt_record = _build_resource_record(
        instance_fqdn, DNS_TYPE_TXT, DNS_CLASS_IN, ttl, txt_rdata
    )

    # A: <hostname>.local. -> IPv4 address
    a_rdata = socket.inet_aton(ip)
    a_record = _build_resource_record(
        hostname_fqdn, DNS_TYPE_A, DNS_CLASS_IN, ttl, a_rdata
    )

    return [ptr_record, srv_record, txt_record, a_record]


def build_dns_response(records: list[bytes]) -> bytes:
    """Build a complete mDNS response packet from resource records.

    Sets QR=1 (response) and AA=1 (authoritative) in flags.
    """
    header = struct.pack(
        "!HHHHHH",
        0,             # Transaction ID (0 for mDNS)
        0x8400,        # Flags: QR=1, AA=1
        0,             # Question count
        len(records),  # Answer count
        0,             # Authority count
        0,             # Additional count
    )
    return header + b"".join(records)


def _parse_query_questions(data: bytes) -> list[tuple[str, int]]:
    """Parse questions from a DNS query packet.

    Returns list of (name, qtype) tuples.
    Only returns results for standard queries (QR bit = 0).
    """
    if len(data) < 12:
        return []

    flags = struct.unpack("!H", data[2:4])[0]
    if flags & 0x8000:  # QR bit set = response, not query
        return []

    qdcount = struct.unpack("!H", data[4:6])[0]
    offset = 12
    questions: list[tuple[str, int]] = []

    for _ in range(qdcount):
        if offset >= len(data):
            break
        try:
            name, offset = decode_dns_name(data, offset)
        except Exception:
            break
        if offset + 4 > len(data):
            break
        qtype, _qclass = struct.unpack("!HH", data[offset : offset + 4])
        offset += 4
        questions.append((name, qtype))

    return questions

