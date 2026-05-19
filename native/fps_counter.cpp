/*
 * fps_counter.cpp
 * DXGI Present-hook FPS counter for Windows.
 *
 * Architecture:
 *   This tool injects a DLL into the target process that hooks
 *   IDXGISwapChain::Present via VMT (virtual method table) patching.
 *   Frame timestamps are written to a named shared memory segment
 *   ("Global\\PCAgent_FPS") which this process reads and reports.
 *
 * Because full DLL injection requires a 32/64-bit matching DLL,
 * this executable is split into two modes:
 *
 *   Mode A  --query  <PID>
 *     Read the shared-memory FPS data written by the hook DLL.
 *     Safe to run from any process — just reads shared memory.
 *
 *   Mode B  --inject <PID>
 *     Inject fps_hook.dll into the target process, which patches
 *     IDXGISwapChain::Present and writes frame data to shared memory.
 *     Requires SeDebugPrivilege (run as Administrator).
 *
 *   Mode C  --listgpu
 *     Enumerate DXGI adapters and outputs — no injection needed.
 *
 *   Mode D  --frametimes <PID> [--samples N]
 *     Read N frame-time samples from shared memory and compute
 *     percentile statistics (50th, 95th, 99th, 1% low, 0.1% low).
 *
 * Shared memory layout  (PCAgent_FPS_<PID>):
 *   [0]      DWORD  magic         = 0xFABFABFA
 *   [4]      DWORD  version       = 1
 *   [8]      DWORD  frame_count   (total frames since hook)
 *   [12]     DWORD  fps_1s        (frames in last 1 second)
 *   [16]     DWORD  fps_5s_avg    (average FPS over last 5 s  * 100)
 *   [20]     QWORD  last_present  (QPC timestamp of last Present call)
 *   [28]     DWORD  ft_head       (ring buffer head index)
 *   [32]     DWORD  ft_count      (entries in ring buffer)
 *   [36]     FLOAT  ft_ring[512]  (frame-time ring buffer, milliseconds)
 *
 * Build (MSVC 64-bit):
 *   cl /EHsc /O2 /W4 fps_counter.cpp /link dxgi.lib user32.lib \
 *      kernel32.lib /out:fps_counter.exe
 *
 * Build (MinGW 64-bit):
 *   g++ -std=c++17 -O2 -o fps_counter.exe fps_counter.cpp \
 *       -ldxgi -luser32 -lkernel32
 *
 * The companion DLL  fps_hook.dll  must be built separately from
 * fps_hook.cpp (included in native/).
 *
 * Output (--query):
 *   JSON: {"pid":<n>,"fps_1s":<n>,"fps_5s_avg":<f>,"frame_count":<n>,
 *          "last_present_ms":<f>}
 *
 * Output (--listgpu):
 *   JSON: {"adapters":[{"index":0,"name":"...","vram_mb":<n>,
 *          "outputs":[{"name":"...","resolution":"WxH","refresh":<n>}]}]}
 *
 * Output (--frametimes):
 *   JSON: {"pid":<n>,"samples":<n>,"avg_ms":<f>,"p50":<f>,"p95":<f>,
 *          "p99":<f>,"low1pct":<f>,"low01pct":<f>,"min_ms":<f>,"max_ms":<f>}
 */

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define INITGUID
#include <windows.h>
#include <dxgi.h>
#include <dxgi1_2.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <string>
#include <vector>
#include <sstream>
#include <stdexcept>

#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "user32.lib")

// ---------------------------------------------------------------------------
// Shared-memory constants (must match fps_hook.cpp)
// ---------------------------------------------------------------------------

static constexpr DWORD  SHM_MAGIC       = 0xFABFABFA;
static constexpr DWORD  SHM_VERSION     = 1;
static constexpr size_t FT_RING_SIZE    = 512;
static constexpr size_t SHM_TOTAL_BYTES =
    4   // magic
  + 4   // version
  + 4   // frame_count
  + 4   // fps_1s
  + 4   // fps_5s_avg  (fixed-point *100)
  + 8   // last_present (QWORD)
  + 4   // ft_head
  + 4   // ft_count
  + sizeof(float) * FT_RING_SIZE;

struct ShmLayout {
    DWORD  magic;
    DWORD  version;
    DWORD  frame_count;
    DWORD  fps_1s;
    DWORD  fps_5s_avg100;   // FPS * 100 for fixed-point
    UINT64 last_present;    // QPC ticks
    DWORD  ft_head;
    DWORD  ft_count;
    float  ft_ring[FT_RING_SIZE];
};

static std::string shm_name(DWORD pid) {
    return "Global\\PCAgent_FPS_" + std::to_string(pid);
}


// ---------------------------------------------------------------------------
// JSON escape helper
// ---------------------------------------------------------------------------

static std::string je(const std::string& s) {
    std::string r;
    r.reserve(s.size() + 4);
    for (char c : s) {
        if (c == '"') r += "\\\"";
        else if (c == '\\') r += "\\\\";
        else r += c;
    }
    return r;
}


// ---------------------------------------------------------------------------
// Open (read-only) shared memory from the hook DLL
// ---------------------------------------------------------------------------

static ShmLayout* open_shm(DWORD pid, HANDLE& out_handle) {
    std::string name = shm_name(pid);
    HANDLE h = OpenFileMappingA(FILE_MAP_READ, FALSE, name.c_str());
    if (!h) {
        // Try without "Global\" prefix (non-admin sessions)
        name = "PCAgent_FPS_" + std::to_string(pid);
        h = OpenFileMappingA(FILE_MAP_READ, FALSE, name.c_str());
    }
    if (!h) return nullptr;
    void* ptr = MapViewOfFile(h, FILE_MAP_READ, 0, 0, sizeof(ShmLayout));
    if (!ptr) { CloseHandle(h); return nullptr; }
    out_handle = h;
    return reinterpret_cast<ShmLayout*>(ptr);
}


// ---------------------------------------------------------------------------
// Mode A: --query <PID>
// ---------------------------------------------------------------------------

static int mode_query(DWORD pid) {
    HANDLE h{};
    ShmLayout* shm = open_shm(pid, h);
    if (!shm) {
        std::printf("{\"error\":\"shared memory not found for PID %lu — "
                    "is fps_hook.dll injected?\",\"pid\":%lu}\n", pid, pid);
        return 2;
    }

    if (shm->magic != SHM_MAGIC) {
        std::printf("{\"error\":\"bad magic — wrong version of fps_hook.dll?\","
                    "\"pid\":%lu}\n", pid);
        UnmapViewOfFile(shm);
        CloseHandle(h);
        return 2;
    }

    LARGE_INTEGER qpf;
    QueryPerformanceFrequency(&qpf);
    LARGE_INTEGER qpc;
    QueryPerformanceCounter(&qpc);
    double elapsed_since_present_ms =
        qpf.QuadPart > 0
        ? static_cast<double>(qpc.QuadPart - shm->last_present)
          / qpf.QuadPart * 1000.0
        : -1.0;

    std::printf(
        "{\"pid\":%lu,\"fps_1s\":%lu,\"fps_5s_avg\":%.2f,"
        "\"frame_count\":%lu,\"elapsed_since_present_ms\":%.2f}\n",
        pid,
        shm->fps_1s,
        shm->fps_5s_avg100 / 100.0,
        shm->frame_count,
        elapsed_since_present_ms
    );

    UnmapViewOfFile(shm);
    CloseHandle(h);
    return 0;
}


// ---------------------------------------------------------------------------
// Mode D: --frametimes <PID> [--samples N]
// ---------------------------------------------------------------------------

static double percentile(std::vector<float>& v, double p) {
    if (v.empty()) return 0.0;
    size_t idx = static_cast<size_t>(std::ceil(p / 100.0 * v.size())) - 1;
    idx = std::min(idx, v.size() - 1);
    return static_cast<double>(v[idx]);
}

static int mode_frametimes(DWORD pid, int max_samples) {
    HANDLE h{};
    ShmLayout* shm = open_shm(pid, h);
    if (!shm) {
        std::printf("{\"error\":\"shared memory not found for PID %lu\","
                    "\"pid\":%lu}\n", pid, pid);
        return 2;
    }

    DWORD count = std::min(shm->ft_count, static_cast<DWORD>(FT_RING_SIZE));
    DWORD head  = shm->ft_head % FT_RING_SIZE;

    std::vector<float> ft;
    ft.reserve(count);
    for (DWORD i = 0; i < count; ++i) {
        DWORD idx = (head + FT_RING_SIZE - count + i) % FT_RING_SIZE;
        float v = shm->ft_ring[idx];
        if (v > 0.0f && v < 10000.0f)   // sanity filter
            ft.push_back(v);
    }

    UnmapViewOfFile(shm);
    CloseHandle(h);

    if ((int)ft.size() > max_samples)
        ft.erase(ft.begin(), ft.begin() + (ft.size() - max_samples));

    if (ft.empty()) {
        std::printf("{\"error\":\"no frame-time samples yet\",\"pid\":%lu}\n", pid);
        return 0;
    }

    std::sort(ft.begin(), ft.end());

    double sum = 0;
    for (float f : ft) sum += f;
    double avg = sum / ft.size();

    // 1% low = average of bottom 1% frame times (inverted for FPS)
    size_t low1_n   = std::max<size_t>(1, ft.size() / 100);
    size_t low01_n  = std::max<size_t>(1, ft.size() / 1000);

    double low1_avg  = 0, low01_avg = 0;
    for (size_t i = ft.size() - low1_n;  i < ft.size(); ++i) low1_avg  += ft[i];
    for (size_t i = ft.size() - low01_n; i < ft.size(); ++i) low01_avg += ft[i];
    low1_avg  /= low1_n;
    low01_avg /= low01_n;

    std::printf(
        "{\"pid\":%lu,\"samples\":%zu,"
        "\"avg_ms\":%.3f,\"avg_fps\":%.2f,"
        "\"p50\":%.3f,\"p95\":%.3f,\"p99\":%.3f,"
        "\"low1pct_ms\":%.3f,\"low01pct_ms\":%.3f,"
        "\"low1pct_fps\":%.2f,\"low01pct_fps\":%.2f,"
        "\"min_ms\":%.3f,\"max_ms\":%.3f}\n",
        pid,
        ft.size(),
        avg,
        avg > 0 ? 1000.0 / avg : 0,
        percentile(ft, 50),
        percentile(ft, 95),
        percentile(ft, 99),
        low1_avg,
        low01_avg,
        low1_avg  > 0 ? 1000.0 / low1_avg  : 0,
        low01_avg > 0 ? 1000.0 / low01_avg : 0,
        (double)ft.front(),
        (double)ft.back()
    );
    return 0;
}


// ---------------------------------------------------------------------------
// Mode B: --inject <PID>  (requires SeDebugPrivilege + fps_hook.dll)
// ---------------------------------------------------------------------------

static bool enable_sedebug() {
    HANDLE tok{};
    if (!OpenProcessToken(GetCurrentProcess(),
                          TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, &tok))
        return false;
    TOKEN_PRIVILEGES tp{};
    tp.PrivilegeCount = 1;
    LookupPrivilegeValueA(nullptr, SE_DEBUG_NAME, &tp.Privileges[0].Luid);
    tp.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED;
    AdjustTokenPrivileges(tok, FALSE, &tp, sizeof(tp), nullptr, nullptr);
    CloseHandle(tok);
    return GetLastError() == ERROR_SUCCESS;
}

static int mode_inject(DWORD pid) {
    // Locate fps_hook.dll next to this executable
    char self_path[MAX_PATH]{};
    GetModuleFileNameA(nullptr, self_path, MAX_PATH);
    std::string dir(self_path);
    auto slash = dir.rfind('\\');
    if (slash != std::string::npos) dir = dir.substr(0, slash);
    std::string dll_path = dir + "\\fps_hook.dll";

    if (GetFileAttributesA(dll_path.c_str()) == INVALID_FILE_ATTRIBUTES) {
        std::printf("{\"error\":\"fps_hook.dll not found at %s\","
                    "\"hint\":\"Build fps_hook.cpp as a DLL first\"}\n",
                    je(dll_path).c_str());
        return 1;
    }

    enable_sedebug();

    HANDLE proc = OpenProcess(
        PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION |
        PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION,
        FALSE, pid);
    if (!proc) {
        DWORD err = GetLastError();
        std::printf("{\"error\":\"OpenProcess failed\",\"pid\":%lu,"
                    "\"win32_error\":%lu}\n", pid, err);
        return 1;
    }

    // Allocate path string in remote process
    size_t path_len = dll_path.size() + 1;
    LPVOID remote_str = VirtualAllocEx(proc, nullptr, path_len,
                                       MEM_COMMIT | MEM_RESERVE,
                                       PAGE_READWRITE);
    if (!remote_str) {
        CloseHandle(proc);
        std::printf("{\"error\":\"VirtualAllocEx failed\",\"pid\":%lu}\n", pid);
        return 1;
    }

    WriteProcessMemory(proc, remote_str, dll_path.c_str(), path_len, nullptr);

    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    FARPROC load_lib = GetProcAddress(k32, "LoadLibraryA");

    HANDLE thr = CreateRemoteThread(proc, nullptr, 0,
        reinterpret_cast<LPTHREAD_START_ROUTINE>(load_lib),
        remote_str, 0, nullptr);

    if (!thr) {
        VirtualFreeEx(proc, remote_str, 0, MEM_RELEASE);
        CloseHandle(proc);
        std::printf("{\"error\":\"CreateRemoteThread failed\",\"pid\":%lu}\n", pid);
        return 1;
    }

    WaitForSingleObject(thr, 10000);
    DWORD exit_code{};
    GetExitCodeThread(thr, &exit_code);
    CloseHandle(thr);
    VirtualFreeEx(proc, remote_str, 0, MEM_RELEASE);
    CloseHandle(proc);

    if (exit_code == 0) {
        std::printf("{\"error\":\"LoadLibraryA returned NULL — "
                    "DLL load failed in target process\",\"pid\":%lu}\n", pid);
        return 1;
    }

    // Give init_thread inside DLL time to finish D3D bootstrap + shm init
    Sleep(1500);

    std::printf("{\"success\":true,\"pid\":%lu,"
                "\"dll\":\"%s\",\"module_base\":\"0x%lX\"}\n",
                pid, je(dll_path).c_str(), exit_code);
    return 0;
}


// ---------------------------------------------------------------------------
// Mode C: --listgpu
// ---------------------------------------------------------------------------

static int mode_listgpu() {
    IDXGIFactory1* factory{};
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1),
                                    reinterpret_cast<void**>(&factory));
    if (FAILED(hr)) {
        std::printf("{\"error\":\"CreateDXGIFactory1 failed\","
                    "\"hresult\":\"0x%08lX\"}\n", hr);
        return 1;
    }

    std::printf("{\"adapters\":[\n");
    bool first_adapter = true;

    for (UINT ai = 0; ; ++ai) {
        IDXGIAdapter1* adapter{};
        if (factory->EnumAdapters1(ai, &adapter) == DXGI_ERROR_NOT_FOUND) break;

        DXGI_ADAPTER_DESC1 desc{};
        adapter->GetDesc1(&desc);

        // Convert wide string name
        char name_buf[256]{};
        WideCharToMultiByte(CP_UTF8, 0, desc.Description, -1,
                            name_buf, sizeof(name_buf), nullptr, nullptr);

        UINT64 vram_mb = desc.DedicatedVideoMemory / (1024 * 1024);
        UINT64 sram_mb = desc.SharedSystemMemory    / (1024 * 1024);

        if (!first_adapter) std::printf(",\n");
        first_adapter = false;

        std::printf(
            "  {\"index\":%u,\"name\":\"%s\","
            "\"vram_mb\":%llu,\"shared_mb\":%llu,"
            "\"vendor_id\":\"0x%04X\",\"device_id\":\"0x%04X\","
            "\"outputs\":[\n",
            ai, je(name_buf).c_str(), vram_mb, sram_mb,
            desc.VendorId, desc.DeviceId
        );

        bool first_output = true;
        for (UINT oi = 0; ; ++oi) {
            IDXGIOutput* output{};
            if (adapter->EnumOutputs(oi, &output) == DXGI_ERROR_NOT_FOUND) break;

            DXGI_OUTPUT_DESC odesc{};
            output->GetDesc(&odesc);

            char oname[256]{};
            WideCharToMultiByte(CP_UTF8, 0, odesc.DeviceName, -1,
                                oname, sizeof(oname), nullptr, nullptr);

            UINT mode_count = 0;
            output->GetDisplayModeList(DXGI_FORMAT_R8G8B8A8_UNORM, 0,
                                       &mode_count, nullptr);
            UINT best_w = 0, best_h = 0, best_r = 0;
            if (mode_count > 0) {
                std::vector<DXGI_MODE_DESC> modes(mode_count);
                output->GetDisplayModeList(DXGI_FORMAT_R8G8B8A8_UNORM, 0,
                                           &mode_count, modes.data());
                for (auto& m : modes) {
                    UINT r = m.RefreshRate.Numerator / std::max(1u, m.RefreshRate.Denominator);
                    if (m.Width >= best_w && m.Height >= best_h) {
                        best_w = m.Width; best_h = m.Height; best_r = r;
                    }
                }
            }

            if (!first_output) std::printf(",\n");
            first_output = false;
            std::printf(
                "    {\"index\":%u,\"name\":\"%s\","
                "\"max_resolution\":\"%ux%u\",\"max_refresh\":%u}",
                oi, je(oname).c_str(), best_w, best_h, best_r
            );
            output->Release();
        }

        std::printf("\n  ]}");
        adapter->Release();
    }

    std::printf("\n]}\n");
    factory->Release();
    return 0;
}


// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::fprintf(stderr,
            "Usage:\n"
            "  fps_counter.exe --query    <PID>\n"
            "  fps_counter.exe --inject   <PID>\n"
            "  fps_counter.exe --frametimes <PID> [--samples N]\n"
            "  fps_counter.exe --listgpu\n"
        );
        return 1;
    }

    std::string mode(argv[1]);

    if (mode == "--listgpu") {
        return mode_listgpu();
    }

    if (argc < 3) {
        std::fprintf(stderr, "Expected PID argument after %s\n", argv[1]);
        return 1;
    }
    DWORD pid = static_cast<DWORD>(std::atol(argv[2]));

    if (mode == "--query")      return mode_query(pid);
    if (mode == "--inject")     return mode_inject(pid);

    if (mode == "--frametimes") {
        int samples = 512;
        for (int i = 3; i < argc - 1; ++i) {
            if (std::string(argv[i]) == "--samples")
                samples = std::atoi(argv[i+1]);
        }
        return mode_frametimes(pid, samples);
    }

    std::fprintf(stderr, "Unknown mode: %s\n", argv[1]);
    return 1;
}
