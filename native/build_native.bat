@echo off
:: ============================================================
:: build_native.bat
:: Compile all PC-Agent native C++ tools
::
:: Run from the native\ directory:
::   cd native
::   build_native.bat
::
:: Requires MSVC (run from a Developer Command Prompt) OR MinGW.
:: ============================================================

setlocal EnableDelayedExpansion

set "MSVC_OK=0"
set "MINGW_OK=0"

:: ── Detect compiler ─────────────────────────────────────────
where cl.exe >nul 2>&1
if %errorlevel% == 0 (
    set "COMPILER=msvc"
    echo [INFO] Found MSVC compiler: cl.exe
    goto :build
)

where g++.exe >nul 2>&1
if %errorlevel% == 0 (
    set "COMPILER=mingw"
    echo [INFO] Found MinGW compiler: g++.exe
    goto :build
)

echo [ERROR] No C++ compiler found.
echo         Install Visual Studio Build Tools (cl.exe)
echo         or MinGW-w64 (g++.exe) and add to PATH.
exit /b 1

:build
echo.
echo ============================================================
echo  Building PC-Agent Native Tools
echo  Compiler: %COMPILER%
echo ============================================================
echo.

:: ── perf_counters.exe ───────────────────────────────────────
echo [1/3] Building perf_counters.exe ...
if "%COMPILER%"=="msvc" (
    cl /EHsc /O2 /W4 /std:c++17 /nologo perf_counters.cpp ^
       /link pdh.lib kernel32.lib /out:perf_counters.exe
) else (
    g++ -std=c++17 -O2 -o perf_counters.exe perf_counters.cpp -lpdh -lkernel32
)
if %errorlevel% neq 0 (
    echo [FAIL] perf_counters.exe build failed.
) else (
    echo [OK]   perf_counters.exe built successfully.
)

echo.

:: ── fps_counter.exe ─────────────────────────────────────────
echo [2/3] Building fps_counter.exe ...
if "%COMPILER%"=="msvc" (
    cl /EHsc /O2 /W4 /std:c++17 /nologo fps_counter.cpp ^
       /link dxgi.lib user32.lib kernel32.lib advapi32.lib /out:fps_counter.exe
) else (
    g++ -std=c++17 -O2 -o fps_counter.exe fps_counter.cpp ^
        -ldxgi -luser32 -lkernel32 -ladvapi32
)
if %errorlevel% neq 0 (
    echo [FAIL] fps_counter.exe build failed.
) else (
    echo [OK]   fps_counter.exe built successfully.
)

echo.

:: ── fps_hook.dll ─────────────────────────────────────────────
echo [2b] Building fps_hook.dll ...
if "%COMPILER%"=="msvc" (
    cl /EHsc /O2 /W4 /std:c++17 /nologo /LD fps_hook.cpp ^
       /link dxgi.lib d3d11.lib user32.lib kernel32.lib /out:fps_hook.dll
) else (
    g++ -std=c++17 -O2 -shared -o fps_hook.dll fps_hook.cpp ^
        -ldxgi -ld3d11 -luser32 -lkernel32
)
if %errorlevel% neq 0 (
    echo [FAIL] fps_hook.dll build failed.
) else (
    echo [OK]   fps_hook.dll built successfully.
)

echo.

:: ── gpu_pipeline.exe ────────────────────────────────────────
echo [3/3] Building gpu_pipeline.exe ...
if "%COMPILER%"=="msvc" (
    cl /EHsc /O2 /W4 /std:c++17 /nologo gpu_pipeline.cpp ^
       /link pdh.lib dxgi.lib kernel32.lib /out:gpu_pipeline.exe
) else (
    g++ -std=c++17 -O2 -o gpu_pipeline.exe gpu_pipeline.cpp ^
        -lpdh -ldxgi -lkernel32
)
if %errorlevel% neq 0 (
    echo [FAIL] gpu_pipeline.exe build failed.
) else (
    echo [OK]   gpu_pipeline.exe built successfully.
)

echo.
echo ============================================================
echo  Done. Place all .exe and .dll files next to main.py or
echo  keep them in the native\ folder (paths are auto-resolved).
echo ============================================================

:: Clean up MSVC intermediate files
if "%COMPILER%"=="msvc" (
    del /Q *.obj 2>nul
    del /Q *.exp 2>nul
    del /Q *.lib 2>nul
)

endlocal
