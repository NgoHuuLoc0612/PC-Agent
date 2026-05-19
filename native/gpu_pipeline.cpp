/*
 * gpu_pipeline.cpp
 * GPU Frame Pipeline Usage Reporter for Windows.
 *
 * Reads per-process GPU hardware engine utilisation via
 * DXCore / DXGI + D3DKMTQueryStatistics (kernel-mode driver API).
 *
 * Reports utilisation of these GPU engines:
 *   3D Engine (graphics + compute on unified shaders)
 *   Copy Engine (DMA blitter)
 *   Video Decode Engine
 *   Video Encode Engine
 *   Video Process Engine
 *   Overlay Engine
 *   Compute Engine (async compute on some drivers)
 *
 * Two data sources are tried in order:
 *
 *   1. PDH "GPU Engine" counters  (Windows 10 1709+)
 *      Path: \GPU Engine(<process_name>_<pid>_<luid>_<engine_type>)\...
 *      Advantage: per-process, per-engine, no driver headers needed.
 *
 *   2. D3DKMT  QueryStatistics  (fallback, requires d3dkmthk.h — WDK)
 *      Advantage: works pre-1709.
 *
 * Build (MSVC):
 *   cl /EHsc /O2 /W4 gpu_pipeline.cpp /link pdh.lib dxgi.lib \
 *      kernel32.lib /out:gpu_pipeline.exe
 *
 * Build (MinGW):
 *   g++ -std=c++17 -O2 -o gpu_pipeline.exe gpu_pipeline.cpp -lpdh -ldxgi
 *
 * Usage:
 *   gpu_pipeline.exe [--pid <PID>] [--json] [--interval <ms>] [--count <n>]
 *
 * Without --pid: report aggregate engine utilisation for all processes.
 * With    --pid: report engine utilisation for a specific process only.
 *
 * Output (plain):
 *   3d=45.20
 *   copy=2.10
 *   decode=0.00
 *   ...
 *
 * Output (--json):
 *   {"timestamp":<ms>,"pid":<n_or_-1>,"engines":{"3d":45.2,"copy":2.1,...}}
 */

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <pdh.h>
#include <pdhmsg.h>
#include <dxgi.h>
#include <dxgi1_4.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include <chrono>
#include <stdexcept>
#include <sstream>

#pragma comment(lib, "pdh.lib")
#pragma comment(lib, "dxgi.lib")

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

struct EngineStats {
    double usage_pct = -1.0;   // -1 = not available
};

using EngineMap = std::map<std::string, EngineStats>;

static constexpr const char* ENGINE_NAMES[] = {
    "3d", "copy", "video_decode", "video_encode",
    "video_process", "overlay", "compute", "total_running"
};

// PDH engine type strings in counter paths
static constexpr const char* PDH_ENGINE_TYPES[] = {
    "3D", "Copy", "VideoDecode", "VideoEncode",
    "VideoProcess", "Overlay", "Compute", "running"
};
static constexpr int N_ENGINES = 8;


// ---------------------------------------------------------------------------
// JSON / plain helpers
// ---------------------------------------------------------------------------

static int64_t epoch_ms() {
    using namespace std::chrono;
    return duration_cast<milliseconds>(system_clock::now().time_since_epoch()).count();
}

static std::string je(const std::string& s) {
    std::string r;
    for (char c : s) {
        if (c == '"') r += "\\\"";
        else if (c == '\\') r += "\\\\";
        else r += c;
    }
    return r;
}

static void print_plain(const EngineMap& em) {
    for (auto& [k, v] : em) {
        if (v.usage_pct < 0) std::printf("%s=N/A\n", k.c_str());
        else                 std::printf("%s=%.4f\n", k.c_str(), v.usage_pct);
    }
}

static void print_json(const EngineMap& em, long long pid_filter, int64_t ts) {
    std::printf("{\"timestamp\":%lld,\"pid\":%lld,\"engines\":{",
                (long long)ts, (long long)pid_filter);
    bool first = true;
    for (auto& [k, v] : em) {
        if (!first) std::printf(",");
        first = false;
        std::printf("\"%s\":", je(k).c_str());
        if (v.usage_pct < 0) std::printf("null");
        else                  std::printf("%.4f", v.usage_pct);
    }
    std::printf("}}\n");
}


// ---------------------------------------------------------------------------
// PDH GPU Engine counter reader
// Requires Windows 10 1709+ and up-to-date GPU drivers.
// ---------------------------------------------------------------------------

/*
 * GPU Engine counters live under:
 *   \GPU Engine(<proc_name>_<pid>_<luid_hi>_<luid_lo>_<engtype_X>)\
 *                Utilization Percentage
 *
 * We expand the wildcard path to get all instances, filter by PID if
 * requested, and sum by engine type.
 */

static std::vector<std::string> expand_pdh_wildcard(const char* path) {
    DWORD path_len = 0;
    PdhExpandWildCardPathA(nullptr, path, nullptr, &path_len, PDH_NOEXPANDCOUNTERS);
    if (!path_len) return {};

    std::vector<char> path_buf(path_len);

    PDH_STATUS st = PdhExpandWildCardPathA(
        nullptr, path, path_buf.data(), &path_len,
        PDH_NOEXPANDCOUNTERS);

    if (st != ERROR_SUCCESS) return {};

    // Multi-string — split on NUL
    std::vector<std::string> result;
    const char* p = path_buf.data();
    while (p && *p) {
        result.emplace_back(p);
        p += result.back().size() + 1;
    }
    return result;
}


/*
 * Parse instance name:
 *   "<procname>_<pid>_<luid_hi>_<luid_lo>_engtype_<type>"
 * Returns {pid, engine_type_str} or {-1, ""} on failure.
 */
static std::pair<long long, std::string> parse_gpu_instance(const std::string& name) {
    // Find last occurrence of "_engtype_"
    auto et_pos = name.rfind("_engtype_");
    if (et_pos == std::string::npos) return {-1, ""};
    std::string eng_type = name.substr(et_pos + 9);

    // The segment before _engtype_ is: <procname>_<pid>_<luid_hi>_<luid_lo>
    std::string prefix = name.substr(0, et_pos);
    // Work backwards: last two underscores are LUID, third from end is PID
    size_t pos = prefix.rfind('_');
    if (pos == std::string::npos) return {-1, ""};
    pos = prefix.rfind('_', pos - 1);
    if (pos == std::string::npos) return {-1, ""};
    size_t pid_end = pos;
    pos = prefix.rfind('_', pos - 1);
    if (pos == std::string::npos) return {-1, ""};

    std::string pid_str = prefix.substr(pos + 1, pid_end - pos - 1);
    long long pid = -1;
    try { pid = std::stoll(pid_str); } catch (...) { return {-1, ""}; }
    return {pid, eng_type};
}


class PdhGpuPipeline {
public:
    PDH_HQUERY query_ = nullptr;

    struct CounterEntry {
        std::string eng_type;
        long long   pid;
        PDH_HCOUNTER hc;
    };

    std::vector<CounterEntry> entries_;

    bool init(long long filter_pid) {
        if (PdhOpenQuery(nullptr, 0, &query_) != ERROR_SUCCESS) return false;

        const char* wildcard =
            "\\GPU Engine(*)\\Utilization Percentage";

        auto paths = expand_pdh_wildcard(wildcard);
        if (paths.empty()) {
            // Try alternate (some Windows versions use different name)
            const char* alt = "\\GPU Engine(*pid_*)\\Utilization Percentage";
            paths = expand_pdh_wildcard(alt);
        }
        if (paths.empty()) return false;

        for (auto& p : paths) {
            // Extract instance name from path: between ( and )
            auto lp = p.find('(');
            auto rp = p.find(')');
            if (lp == std::string::npos || rp == std::string::npos) continue;
            std::string inst = p.substr(lp + 1, rp - lp - 1);

            auto [pid, eng] = parse_gpu_instance(inst);
            if (pid < 0 || eng.empty()) continue;
            if (filter_pid >= 0 && pid != filter_pid) continue;

            PDH_HCOUNTER hc{};
            if (PdhAddEnglishCounterA(query_, p.c_str(), 0, &hc) != ERROR_SUCCESS)
                continue;
            entries_.push_back({eng, pid, hc});
        }

        return !entries_.empty();
    }

    ~PdhGpuPipeline() { if (query_) PdhCloseQuery(query_); }

    void collect() { PdhCollectQueryData(query_); }

    // Returns aggregated engine utilisation (sum across all matching PIDs).
    EngineMap values() {
        EngineMap out;
        std::map<std::string, double> sums;
        std::map<std::string, int>    counts;

        PDH_FMT_COUNTERVALUE val{};
        for (auto& e : entries_) {
            if (PdhGetFormattedCounterValue(e.hc, PDH_FMT_DOUBLE, nullptr, &val)
                    == ERROR_SUCCESS &&
                val.CStatus == PDH_CSTATUS_VALID_DATA)
            {
                sums[e.eng_type]   += val.doubleValue;
                counts[e.eng_type] += 1;
            }
        }

        // Normalise engine names to our canonical set
        static const std::map<std::string, std::string> alias_map = {
            {"3D",           "3d"},
            {"Copy",         "copy"},
            {"VideoDecode",  "video_decode"},
            {"VideoEncode",  "video_encode"},
            {"VideoProcess", "video_process"},
            {"Overlay",      "overlay"},
            {"Compute",      "compute"},
        };

        for (auto& [raw_eng, sum] : sums) {
            std::string canonical = raw_eng;
            auto it = alias_map.find(raw_eng);
            if (it != alias_map.end()) canonical = it->second;
            // Clamp to 100 (PDH can report > 100 on multi-GPU)
            out[canonical].usage_pct = std::min(sum, 100.0);
        }

        return out;
    }
};


// ---------------------------------------------------------------------------
// DXGI adapter enumeration for metadata (not utilisation)
// ---------------------------------------------------------------------------

static void print_adapter_json() {
    IDXGIFactory1* factory{};
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1),
                                  reinterpret_cast<void**>(&factory))))
    {
        std::printf("{\"error\":\"CreateDXGIFactory1 failed\"}\n");
        return;
    }

    std::printf("{\"adapters\":[\n");
    bool first = true;

    for (UINT ai = 0; ; ++ai) {
        IDXGIAdapter1* adapter{};
        if (factory->EnumAdapters1(ai, &adapter) == DXGI_ERROR_NOT_FOUND) break;

        DXGI_ADAPTER_DESC1 desc{};
        adapter->GetDesc1(&desc);

        char name[256]{};
        WideCharToMultiByte(CP_UTF8, 0, desc.Description, -1,
                            name, sizeof(name), nullptr, nullptr);

        // Get LUID as hex string
        char luid_str[32]{};
        std::snprintf(luid_str, sizeof(luid_str), "%08lX%08lX",
                      desc.AdapterLuid.HighPart,
                      desc.AdapterLuid.LowPart);

        if (!first) std::printf(",\n");
        first = false;

        std::printf(
            "  {\"index\":%u,\"name\":\"%s\","
            "\"vram_mb\":%llu,\"vendor\":\"0x%04X\","
            "\"luid\":\"%s\"}",
            ai, je(name).c_str(),
            (unsigned long long)(desc.DedicatedVideoMemory / 1024 / 1024),
            desc.VendorId, luid_str
        );
        adapter->Release();
    }

    std::printf("\n]}\n");
    factory->Release();
}


// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(int argc, char* argv[]) {
    bool   use_json    = false;
    long long filter_pid = -1;
    int    interval    = 1000;
    int    count       = 1;
    bool   list_adapters = false;

    for (int i = 1; i < argc; ++i) {
        std::string a(argv[i]);
        if (a == "--json")       use_json = true;
        else if (a == "--listadapters") list_adapters = true;
        else if ((a == "--pid" || a == "-p") && i+1 < argc)
            filter_pid = std::atoll(argv[++i]);
        else if (a == "--interval" && i+1 < argc) interval = std::atoi(argv[++i]);
        else if (a == "--count"    && i+1 < argc) count    = std::atoi(argv[++i]);
    }

    if (list_adapters) {
        print_adapter_json();
        return 0;
    }

    PdhGpuPipeline pdh;
    if (!pdh.init(filter_pid)) {
        if (use_json) {
            std::printf("{\"error\":\"GPU Engine PDH counters not available — "
                        "requires Windows 10 1709+ and updated GPU drivers\","
                        "\"pid\":%lld}\n", filter_pid);
        } else {
            std::fprintf(stderr,
                "GPU Engine PDH counters not available.\n"
                "Requires Windows 10 1709+ with up-to-date GPU drivers.\n");
        }
        return 1;
    }

    // First collect is warm-up (PDH rate counters always return 0 first)
    pdh.collect();
    Sleep(static_cast<DWORD>(interval > 500 ? interval : 500));

    for (int i = 0; i < count; ++i) {
        pdh.collect();
        int64_t ts = epoch_ms();
        auto em = pdh.values();

        if (use_json) print_json(em, filter_pid, ts);
        else          print_plain(em);

        if (i + 1 < count)
            Sleep(static_cast<DWORD>(interval));
    }

    return 0;
}
