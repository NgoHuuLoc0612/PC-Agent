/*
 * perf_counters.cpp
 * Windows Performance Counters raw reader — compiled as a standalone CLI tool.
 *
 * Build (MSVC):
 *   cl /EHsc /O2 /W4 perf_counters.cpp /link pdh.lib /out:perf_counters.exe
 *
 * Build (MinGW/GCC on Windows):
 *   g++ -std=c++17 -O2 -o perf_counters.exe perf_counters.cpp -lpdh
 *
 * Usage:
 *   perf_counters.exe [--json] [--interval <ms>] [--count <n>]
 *
 * Output (plain text, one line per counter):
 *   CounterName=Value
 *
 * Output (--json):
 *   {"counters": {"CounterName": value, ...}, "timestamp": unix_epoch_ms}
 *
 * Exit codes:
 *   0  success
 *   1  PDH initialisation error
 *   2  counter query error
 *
 * The Python cog (perf_counters.py) shells out to this binary and parses
 * its stdout, so the interface must remain stable.
 */

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <pdh.h>
#include <pdhmsg.h>

#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <string>
#include <vector>
#include <map>
#include <chrono>
#include <stdexcept>
#include <sstream>
#include <iomanip>

#pragma comment(lib, "pdh.lib")

// ---------------------------------------------------------------------------
// Counter definitions
// ---------------------------------------------------------------------------

struct CounterDef {
    const char* label;   // key used in output
    const char* path;    // Windows PDH counter path
};

static constexpr CounterDef COUNTERS[] = {
    // Processor
    {"cpu_total_pct",          "\\Processor(_Total)\\% Processor Time"},
    {"cpu_user_pct",           "\\Processor(_Total)\\% User Time"},
    {"cpu_privileged_pct",     "\\Processor(_Total)\\% Privileged Time"},
    {"cpu_interrupt_pct",      "\\Processor(_Total)\\% Interrupt Time"},
    {"cpu_dpc_pct",            "\\Processor(_Total)\\% DPC Time"},
    {"interrupts_per_sec",     "\\Processor(_Total)\\Interrupts/sec"},
    {"dpcs_per_sec",           "\\Processor(_Total)\\DPCs Queued/sec"},

    // Memory
    {"mem_available_bytes",    "\\Memory\\Available Bytes"},
    {"mem_committed_bytes",    "\\Memory\\Committed Bytes"},
    {"mem_commit_limit",       "\\Memory\\Commit Limit"},
    {"mem_page_faults_sec",    "\\Memory\\Page Faults/sec"},
    {"mem_pages_input_sec",    "\\Memory\\Pages Input/sec"},
    {"mem_pages_output_sec",   "\\Memory\\Pages Output/sec"},
    {"mem_pool_paged_bytes",   "\\Memory\\Pool Paged Bytes"},
    {"mem_pool_nonpaged_bytes","\\Memory\\Pool Nonpaged Bytes"},
    {"mem_cache_bytes",        "\\Memory\\Cache Bytes"},

    // Disk (PhysicalDisk _Total)
    {"disk_read_bytes_sec",    "\\PhysicalDisk(_Total)\\Disk Read Bytes/sec"},
    {"disk_write_bytes_sec",   "\\PhysicalDisk(_Total)\\Disk Write Bytes/sec"},
    {"disk_reads_sec",         "\\PhysicalDisk(_Total)\\Disk Reads/sec"},
    {"disk_writes_sec",        "\\PhysicalDisk(_Total)\\Disk Writes/sec"},
    {"disk_time_pct",          "\\PhysicalDisk(_Total)\\% Disk Time"},
    {"disk_read_time_pct",     "\\PhysicalDisk(_Total)\\% Disk Read Time"},
    {"disk_write_time_pct",    "\\PhysicalDisk(_Total)\\% Disk Write Time"},
    {"disk_queue_length",      "\\PhysicalDisk(_Total)\\Avg. Disk Queue Length"},

    // Network (first interface — typically Ethernet0 or Wi-Fi)
    {"net_bytes_recv_sec",     "\\Network Interface(*)\\Bytes Received/sec"},
    {"net_bytes_sent_sec",     "\\Network Interface(*)\\Bytes Sent/sec"},
    {"net_packets_recv_sec",   "\\Network Interface(*)\\Packets Received/sec"},
    {"net_packets_sent_sec",   "\\Network Interface(*)\\Packets Sent/sec"},
    {"net_errors_recv",        "\\Network Interface(*)\\Packets Received Errors"},

    // System
    {"system_threads",         "\\System\\Threads"},
    {"system_processes",       "\\System\\Processes"},
    {"system_handle_count",    "\\System\\Handle Count"},
    {"context_switches_sec",   "\\System\\Context Switches/sec"},
    {"system_calls_sec",       "\\System\\System Calls/sec"},

    // Cache
    {"cache_copy_reads_sec",   "\\Cache\\Copy Reads/sec"},
    {"cache_data_flushes_sec", "\\Cache\\Data Flushes/sec"},
};

static constexpr int N_COUNTERS = static_cast<int>(sizeof(COUNTERS) / sizeof(COUNTERS[0]));


// ---------------------------------------------------------------------------
// Helper: escape JSON string value
// ---------------------------------------------------------------------------

static std::string json_escape(const std::string& s) {
    std::ostringstream oss;
    for (char c : s) {
        switch (c) {
        case '"':  oss << "\\\""; break;
        case '\\': oss << "\\\\"; break;
        case '\n': oss << "\\n";  break;
        case '\r': oss << "\\r";  break;
        case '\t': oss << "\\t";  break;
        default:   oss << c;      break;
        }
    }
    return oss.str();
}


// ---------------------------------------------------------------------------
// PDH query wrapper
// ---------------------------------------------------------------------------

class PdhQuery {
public:
    PdhQuery() {
        PDH_STATUS st = PdhOpenQuery(nullptr, 0, &query_);
        if (st != ERROR_SUCCESS)
            throw std::runtime_error("PdhOpenQuery failed: " + std::to_string(st));
    }

    ~PdhQuery() {
        if (query_) PdhCloseQuery(query_);
    }

    // Returns true if counter was added successfully.
    bool add_counter(const char* path, const char* label) {
        PDH_HCOUNTER hc{};
        PDH_STATUS st = PdhAddEnglishCounterA(query_, path, 0, &hc);
        if (st != ERROR_SUCCESS) {
            // Wildcard paths (e.g. *) may fail; try with expanded name
            return false;
        }
        counters_.push_back({std::string(label), hc, false});
        return true;
    }

    // Wildcard-expand a path, add first matching instance.
    // e.g. "\\Network Interface(*)\\Bytes Received/sec"
    bool add_wildcard_first(const char* wildcard_path, const char* label) {
        DWORD patha_sz = 0;
        PdhExpandWildCardPathA(nullptr, wildcard_path, nullptr, &patha_sz, PDH_NOEXPANDCOUNTERS);
        if (patha_sz == 0) return false;

        std::vector<char> path_buf(patha_sz);

        PDH_STATUS st = PdhExpandWildCardPathA(
            nullptr, wildcard_path,
            path_buf.data(), &patha_sz,
            PDH_NOEXPANDCOUNTERS);

        if (st != ERROR_SUCCESS || path_buf.empty()) return false;

        // path_buf is a multi-string; take the first non-empty entry
        std::string first_path(path_buf.data());
        if (first_path.empty()) return false;

        PDH_HCOUNTER hc{};
        st = PdhAddEnglishCounterA(query_, first_path.c_str(), 0, &hc);
        if (st != ERROR_SUCCESS) return false;

        counters_.push_back({std::string(label), hc, true});
        return true;
    }

    // Collect one sample (PDH requires two samples for rate counters).
    void collect() {
        PdhCollectQueryData(query_);
    }

    // Retrieve all counter values. Call collect() twice before this.
    std::map<std::string, double> values() const {
        std::map<std::string, double> out;
        PDH_FMT_COUNTERVALUE val{};
        for (auto& entry : counters_) {
            PDH_STATUS st = PdhGetFormattedCounterValue(
                entry.hc, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, nullptr, &val);
            if (st == ERROR_SUCCESS && val.CStatus == PDH_CSTATUS_VALID_DATA)
                out[entry.label] = val.doubleValue;
            else
                out[entry.label] = -1.0;  // sentinel: not available
        }
        return out;
    }

private:
    struct Entry {
        std::string label;
        PDH_HCOUNTER hc;
        bool is_wildcard_expanded;
    };

    PDH_HQUERY query_{};
    std::vector<Entry> counters_;
};


// ---------------------------------------------------------------------------
// Output formatters
// ---------------------------------------------------------------------------

static int64_t epoch_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

static void print_plain(const std::map<std::string, double>& vals) {
    for (auto& [k, v] : vals) {
        if (v < 0.0)
            std::printf("%s=N/A\n", k.c_str());
        else
            std::printf("%s=%.4f\n", k.c_str(), v);
    }
}

static void print_json(const std::map<std::string, double>& vals, int64_t ts) {
    std::printf("{\"timestamp\":%lld,\"counters\":{", (long long)ts);
    bool first = true;
    for (auto& [k, v] : vals) {
        if (!first) std::printf(",");
        first = false;
        std::printf("\"%s\":", json_escape(k).c_str());
        if (v < 0.0) std::printf("null");
        else         std::printf("%.4f", v);
    }
    std::printf("}}\n");
}


// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(int argc, char* argv[]) {
    // Parse arguments
    bool use_json  = false;
    int  interval  = 1000;   // ms between samples (for multi-shot)
    int  count     = 1;      // number of samples to emit

    for (int i = 1; i < argc; ++i) {
        std::string arg(argv[i]);
        if (arg == "--json")              use_json = true;
        else if (arg == "--interval" && i+1 < argc) interval = std::atoi(argv[++i]);
        else if (arg == "--count"    && i+1 < argc) count    = std::atoi(argv[++i]);
    }

    // Build query
    PdhQuery q;
    for (int i = 0; i < N_COUNTERS; ++i) {
        const char* path  = COUNTERS[i].path;
        const char* label = COUNTERS[i].label;

        // Wildcard paths contain '*'
        if (std::string(path).find('*') != std::string::npos)
            q.add_wildcard_first(path, label);
        else
            q.add_counter(path, label);
    }

    // PDH rate counters need two samples; first is always zero — discard it.
    q.collect();
    Sleep(static_cast<DWORD>(interval > 500 ? interval : 500));   // warm-up wait

    for (int sample = 0; sample < count; ++sample) {
        q.collect();
        int64_t ts = epoch_ms();
        auto vals  = q.values();

        if (use_json) print_json(vals, ts);
        else          print_plain(vals);

        if (sample + 1 < count)
            Sleep(static_cast<DWORD>(interval));
    }

    return 0;
}
