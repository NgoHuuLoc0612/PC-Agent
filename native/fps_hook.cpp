/*
 * fps_hook.cpp
 * IDXGISwapChain::Present VMT-hook DLL for FPS measurement.
 *
 * This DLL is injected into a DirectX game/app process by fps_counter.exe.
 * On attach it:
 *   1. Creates a named shared memory segment "Global\PCAgent_FPS_<PID>".
 *   2. Creates a dummy D3D11 device + swap chain to obtain a valid
 *      IDXGISwapChain vtable.
 *   3. Patches vtable slot 8 (IDXGISwapChain::Present) with our hook.
 *   4. On each Present() call, records the timestamp and updates
 *      FPS counters in shared memory.
 *
 * Build (MSVC — must match bitness of target process):
 *   cl /EHsc /O2 /W4 /LD fps_hook.cpp /link dxgi.lib d3d11.lib \
 *      user32.lib kernel32.lib /out:fps_hook.dll
 *
 * Build (MinGW):
 *   g++ -std=c++17 -O2 -shared -o fps_hook.dll fps_hook.cpp \
 *       -ldxgi -ld3d11 -luser32 -lkernel32
 *
 * The hook is entirely VMT-based — no detour library dependency.
 * It only patches the vtable of the swap chain created internally
 * for bootstrap; once patched it catches all Present calls in the
 * process via the shared vtable pointer.
 *
 * NOTE: Anti-cheat systems (VAC, EAC, BattleEye) treat DLL injection
 * as cheating.  Use this ONLY on single-player games or your own apps.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <dxgi.h>
#include <d3d11.h>

#include <cstdio>
#include <cstring>
#include <cmath>
#include <algorithm>
#include <atomic>
#include <string>

#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "d3d11.lib")

// ---------------------------------------------------------------------------
// Shared-memory layout  (must stay in sync with fps_counter.cpp)
// ---------------------------------------------------------------------------

static constexpr DWORD  SHM_MAGIC       = 0xFABFABFA;
static constexpr DWORD  SHM_VERSION     = 1;
static constexpr size_t FT_RING_SIZE    = 512;

#pragma pack(push, 1)
struct ShmLayout {
    DWORD  magic;
    DWORD  version;
    DWORD  frame_count;
    DWORD  fps_1s;
    DWORD  fps_5s_avg100;   // FPS * 100
    UINT64 last_present;    // QPC ticks
    DWORD  ft_head;
    DWORD  ft_count;
    float  ft_ring[FT_RING_SIZE];
};
#pragma pack(pop)


// ---------------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------------

static HANDLE       g_shm_handle  = nullptr;
static ShmLayout*   g_shm         = nullptr;
static LARGE_INTEGER g_qpf        = {};

// FPS tracking
static constexpr size_t TS_BUF = 600;
static UINT64 g_ts_ring[TS_BUF] = {};
static std::atomic<int> g_ts_head{0};

// Original Present pointer (set by install_detour via trampoline)
using PresentFn = HRESULT(STDMETHODCALLTYPE*)(IDXGISwapChain*, UINT, UINT);
static PresentFn g_orig_present = nullptr;

static CRITICAL_SECTION g_cs;


// ---------------------------------------------------------------------------
// Shared-memory helpers
// ---------------------------------------------------------------------------

static bool shm_init(DWORD pid) {
    std::string name = "Global\\PCAgent_FPS_" + std::to_string(pid);
    g_shm_handle = CreateFileMappingA(
        INVALID_HANDLE_VALUE, nullptr,
        PAGE_READWRITE, 0, sizeof(ShmLayout),
        name.c_str());

    if (!g_shm_handle) {
        // Fall back to non-global (non-admin sessions)
        name = "PCAgent_FPS_" + std::to_string(pid);
        g_shm_handle = CreateFileMappingA(
            INVALID_HANDLE_VALUE, nullptr,
            PAGE_READWRITE, 0, sizeof(ShmLayout),
            name.c_str());
    }
    if (!g_shm_handle) return false;

    g_shm = reinterpret_cast<ShmLayout*>(
        MapViewOfFile(g_shm_handle, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(ShmLayout)));
    if (!g_shm) { CloseHandle(g_shm_handle); g_shm_handle = nullptr; return false; }

    ZeroMemory(g_shm, sizeof(ShmLayout));
    g_shm->magic   = SHM_MAGIC;
    g_shm->version = SHM_VERSION;
    return true;
}

static void shm_cleanup() {
    if (g_shm)        { UnmapViewOfFile(g_shm); g_shm = nullptr; }
    if (g_shm_handle) { CloseHandle(g_shm_handle); g_shm_handle = nullptr; }
}


// ---------------------------------------------------------------------------
// FPS computation — called on every Present
// ---------------------------------------------------------------------------

static void record_frame() {
    if (!g_shm) return;

    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    UINT64 qpc = static_cast<UINT64>(now.QuadPart);
    double freq = static_cast<double>(g_qpf.QuadPart);

    EnterCriticalSection(&g_cs);

    // Update ring buffer for frame-time
    int head = g_ts_head.load(std::memory_order_relaxed);
    UINT64 prev_qpc = g_ts_ring[(head + TS_BUF - 1) % TS_BUF];
    float ft_ms = (prev_qpc > 0 && freq > 0)
        ? static_cast<float>((qpc - prev_qpc) / freq * 1000.0)
        : 0.0f;

    g_ts_ring[head] = qpc;
    g_ts_head.store((head + 1) % TS_BUF, std::memory_order_relaxed);

    // Push frame-time to shared-memory ring
    if (ft_ms > 0.0f && ft_ms < 10000.0f) {
        DWORD fh = g_shm->ft_head;
        g_shm->ft_ring[fh % FT_RING_SIZE] = ft_ms;
        g_shm->ft_head = (fh + 1) % FT_RING_SIZE;
        if (g_shm->ft_count < static_cast<DWORD>(FT_RING_SIZE))
            ++g_shm->ft_count;
    }

    // Count frames in last 1 second
    UINT64 cutoff_1s  = static_cast<UINT64>(qpc - freq);
    UINT64 cutoff_5s  = static_cast<UINT64>(qpc - 5.0 * freq);
    DWORD cnt_1s = 0, cnt_5s = 0;
    for (size_t i = 0; i < TS_BUF; ++i) {
        UINT64 t = g_ts_ring[i];
        if (t == 0) continue;
        if (t >= cutoff_1s) ++cnt_1s;
        if (t >= cutoff_5s) ++cnt_5s;
    }

    g_shm->fps_1s       = cnt_1s;
    g_shm->fps_5s_avg100 = static_cast<DWORD>(cnt_5s / 5.0 * 100.0);
    g_shm->last_present = qpc;
    ++g_shm->frame_count;

    LeaveCriticalSection(&g_cs);
}


// ---------------------------------------------------------------------------
// Hooked Present
// ---------------------------------------------------------------------------

static HRESULT STDMETHODCALLTYPE hooked_present(
    IDXGISwapChain* chain, UINT sync_interval, UINT flags)
{
    record_frame();
    return g_orig_present(chain, sync_interval, flags);
}


// ---------------------------------------------------------------------------
// Inline detour — patches the actual Present() code in dxgi.dll
// so ALL swap chains in the process are intercepted, not just the dummy.
// ---------------------------------------------------------------------------

static constexpr int PRESENT_SLOT   = 8;   // IDXGISwapChain vtable index
static constexpr int TRAMPOLINE_SZ  = 14;  // 2 x MOV+JMP (abs indirect, x64)

// Trampoline buffer holds the overwritten bytes + jump back
static BYTE g_trampoline[TRAMPOLINE_SZ + 16] = {};
static BYTE g_orig_bytes[TRAMPOLINE_SZ]       = {};
static void* g_present_target = nullptr;     // address we patched

// Write an absolute indirect JMP (FF 25 00000000 <addr>) — 14 bytes, x64
static void write_abs_jmp(BYTE* dst, void* target) {
    dst[0] = 0xFF; dst[1] = 0x25;
    *reinterpret_cast<DWORD*>(dst + 2) = 0;          // RIP-relative offset 0
    *reinterpret_cast<void**>(dst + 6) = target;      // absolute address
}

static bool install_detour(IDXGISwapChain* chain) {
    void** vtable  = *reinterpret_cast<void***>(chain);
    void*  present = vtable[PRESENT_SLOT];
    g_present_target = present;

    // Store original Present pointer (used by hooked_present)
    g_orig_present = reinterpret_cast<PresentFn>(present);

    BYTE* fn = reinterpret_cast<BYTE*>(present);

    // Build trampoline: copy first TRAMPOLINE_SZ bytes then JMP back
    DWORD old{};
    VirtualProtect(fn, TRAMPOLINE_SZ, PAGE_EXECUTE_READWRITE, &old);
    memcpy(g_orig_bytes,   fn, TRAMPOLINE_SZ);
    VirtualProtect(fn, TRAMPOLINE_SZ, old, &old);

    // Allocate executable trampoline
    BYTE* tramp = reinterpret_cast<BYTE*>(
        VirtualAlloc(nullptr, TRAMPOLINE_SZ + 14,
                     MEM_COMMIT | MEM_RESERVE,
                     PAGE_EXECUTE_READWRITE));
    if (!tramp) return false;

    memcpy(tramp, g_orig_bytes, TRAMPOLINE_SZ);
    write_abs_jmp(tramp + TRAMPOLINE_SZ,
                  reinterpret_cast<BYTE*>(present) + TRAMPOLINE_SZ);

    // Redirect g_orig_present through trampoline so hooked_present can call original
    g_orig_present = reinterpret_cast<PresentFn>(tramp);
    memcpy(g_trampoline, tramp, TRAMPOLINE_SZ + 14);  // keep copy for restore

    // Patch target function: first TRAMPOLINE_SZ bytes → JMP hooked_present
    VirtualProtect(fn, TRAMPOLINE_SZ, PAGE_EXECUTE_READWRITE, &old);
    write_abs_jmp(fn, reinterpret_cast<void*>(&hooked_present));
    // zero remaining bytes of patch area as NOPs
    for (int i = 14; i < TRAMPOLINE_SZ; ++i) fn[i] = 0x90;
    VirtualProtect(fn, TRAMPOLINE_SZ, old, &old);

    FlushInstructionCache(GetCurrentProcess(), fn, TRAMPOLINE_SZ);
    return true;
}

static void restore_vtable() {
    if (!g_present_target) return;
    BYTE* fn = reinterpret_cast<BYTE*>(g_present_target);
    DWORD old{};
    VirtualProtect(fn, TRAMPOLINE_SZ, PAGE_EXECUTE_READWRITE, &old);
    memcpy(fn, g_orig_bytes, TRAMPOLINE_SZ);
    VirtualProtect(fn, TRAMPOLINE_SZ, old, &old);
    FlushInstructionCache(GetCurrentProcess(), fn, TRAMPOLINE_SZ);
    g_present_target  = nullptr;
    g_orig_present    = nullptr;
}


// ---------------------------------------------------------------------------
// Bootstrap: create a dummy D3D11 device + swap chain to get vtable
// ---------------------------------------------------------------------------

static IDXGISwapChain* g_dummy_chain = nullptr;
static ID3D11Device*   g_dummy_dev   = nullptr;
static HWND            g_dummy_hwnd  = nullptr;

static HWND create_dummy_window() {
    WNDCLASSEXA wc{sizeof(wc)};
    wc.lpfnWndProc   = DefWindowProcA;
    wc.hInstance     = GetModuleHandleA(nullptr);
    wc.lpszClassName = "PCAgentFPSDummy";
    RegisterClassExA(&wc);
    return CreateWindowExA(0, "PCAgentFPSDummy", "", WS_OVERLAPPED,
                           0, 0, 1, 1, nullptr, nullptr,
                           wc.hInstance, nullptr);
}

static bool bootstrap_hook() {
    g_dummy_hwnd = create_dummy_window();
    if (!g_dummy_hwnd) return false;

    DXGI_SWAP_CHAIN_DESC scd{};
    scd.BufferCount       = 1;
    scd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    scd.BufferDesc.Width  = 1;
    scd.BufferDesc.Height = 1;
    scd.BufferUsage       = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    scd.OutputWindow      = g_dummy_hwnd;
    scd.SampleDesc.Count  = 1;
    scd.Windowed          = TRUE;

    D3D_FEATURE_LEVEL fl = D3D_FEATURE_LEVEL_11_0;
    ID3D11DeviceContext* ctx{};

    HRESULT hr = D3D11CreateDeviceAndSwapChain(
        nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
        &fl, 1, D3D11_SDK_VERSION,
        &scd, &g_dummy_chain, &g_dummy_dev, nullptr, &ctx);

    if (FAILED(hr)) return false;
    if (ctx) ctx->Release();

    // install_detour patches the actual Present() code in dxgi.dll —
    // all swap chains in the process share the same function, so this
    // catches GD's swap chain too without needing a reference to it.
    bool ok = install_detour(g_dummy_chain);

    // Dummy chain no longer needed after we have the function address
    g_dummy_chain->Release(); g_dummy_chain = nullptr;
    g_dummy_dev->Release();   g_dummy_dev   = nullptr;
    DestroyWindow(g_dummy_hwnd); g_dummy_hwnd = nullptr;

    return ok;
}


// ---------------------------------------------------------------------------
// DLL entry point
// ---------------------------------------------------------------------------

static DWORD WINAPI init_thread(LPVOID) {
    // Safe to call D3D/COM here — outside loader lock
    if (!shm_init(GetCurrentProcessId()))
        return 1;
    if (!bootstrap_hook()) {
        shm_cleanup();
        return 1;
    }
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hmod, DWORD reason, LPVOID) {
    switch (reason) {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hmod);
        QueryPerformanceFrequency(&g_qpf);
        InitializeCriticalSection(&g_cs);
        ZeroMemory(g_ts_ring, sizeof(g_ts_ring));
        // Must NOT call D3D/COM from DllMain — loader lock deadlock
        CloseHandle(CreateThread(nullptr, 0, init_thread, nullptr, 0, nullptr));
        break;

    case DLL_PROCESS_DETACH:
        restore_vtable();
        if (g_dummy_chain) { g_dummy_chain->Release(); g_dummy_chain = nullptr; }
        if (g_dummy_dev)   { g_dummy_dev->Release();   g_dummy_dev   = nullptr; }
        if (g_dummy_hwnd)  { DestroyWindow(g_dummy_hwnd); g_dummy_hwnd = nullptr; }
        shm_cleanup();
        DeleteCriticalSection(&g_cs);
        break;
    }
    return TRUE;
}
