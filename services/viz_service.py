"""
Visualization service — generates matplotlib charts as BytesIO objects.
"""

import io
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from matplotlib.gridspec import GridSpec

from utils.logger import setup_logger

logger = setup_logger("viz_service")

# Dark theme palette
DARK_BG = "#1a1a2e"
PANEL_BG = "#16213e"
ACCENT1 = "#0f3460"
ACCENT2 = "#e94560"
ACCENT3 = "#00d4aa"
ACCENT4 = "#f5a623"
TEXT_COLOR = "#e0e0e0"
GRID_COLOR = "#2a2a4a"


def _apply_dark_theme(fig, axes=None):
    fig.patch.set_facecolor(DARK_BG)
    targets = axes if axes else fig.get_axes()
    if not isinstance(targets, (list, tuple)):
        targets = [targets]
    for ax in targets:
        ax.set_facecolor(PANEL_BG)
        ax.tick_params(colors=TEXT_COLOR)
        ax.xaxis.label.set_color(TEXT_COLOR)
        ax.yaxis.label.set_color(TEXT_COLOR)
        ax.title.set_color(TEXT_COLOR)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID_COLOR)
        ax.grid(color=GRID_COLOR, linewidth=0.5, alpha=0.7)


def _to_bytes(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120, facecolor=DARK_BG)
    buf.seek(0)
    plt.close(fig)
    return buf


# ─── CPU History Chart ────────────────────────────────────────────────────────

def cpu_history_chart(history: List[float], title: str = "CPU Usage History") -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(10, 4))
    _apply_dark_theme(fig, ax)

    x = list(range(len(history)))
    ax.fill_between(x, history, alpha=0.3, color=ACCENT2)
    ax.plot(x, history, color=ACCENT2, linewidth=2, label="CPU %")
    ax.axhline(y=np.mean(history), color=ACCENT4, linestyle="--", alpha=0.8,
               label=f"Avg: {np.mean(history):.1f}%")
    ax.set_ylim(0, 100)
    ax.set_xlabel("Time (samples)", color=TEXT_COLOR)
    ax.set_ylabel("Usage (%)", color=TEXT_COLOR)
    ax.set_title(title, color=TEXT_COLOR, fontsize=14, fontweight="bold")
    ax.legend(facecolor=PANEL_BG, labelcolor=TEXT_COLOR)
    fig.tight_layout()
    return _to_bytes(fig)


# ─── RAM Usage Donut ──────────────────────────────────────────────────────────

def ram_donut_chart(used_gb: float, total_gb: float) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(6, 6))
    _apply_dark_theme(fig, ax)

    free_gb = total_gb - used_gb
    pct = (used_gb / total_gb) * 100

    colors = [ACCENT2 if pct > 80 else ACCENT3, GRID_COLOR]
    wedges, _ = ax.pie(
        [used_gb, free_gb],
        colors=colors,
        startangle=90,
        wedgeprops=dict(width=0.55, edgecolor=DARK_BG, linewidth=2),
    )

    ax.text(0, 0.1, f"{pct:.1f}%", ha="center", va="center",
            fontsize=28, fontweight="bold", color=TEXT_COLOR)
    ax.text(0, -0.2, f"{used_gb:.1f}/{total_gb:.1f} GB", ha="center", va="center",
            fontsize=12, color=TEXT_COLOR)
    ax.set_title("RAM Usage", color=TEXT_COLOR, fontsize=14, fontweight="bold")
    ax.axis("equal")
    return _to_bytes(fig)


# ─── Disk Usage Bar ───────────────────────────────────────────────────────────

def disk_usage_chart(disks: List[Dict]) -> io.BytesIO:
    """disks: [{'mount': 'C:', 'used': 120, 'total': 500, 'unit': 'GB'}]"""
    n = len(disks)
    fig, ax = plt.subplots(figsize=(10, max(3, n * 1.2)))
    _apply_dark_theme(fig, ax)

    labels = [d["mount"] for d in disks]
    used = [d["used"] for d in disks]
    totals = [d["total"] for d in disks]
    pcts = [u / t * 100 if t > 0 else 0 for u, t in zip(used, totals)]
    free = [t - u for t, u in zip(totals, used)]

    y = np.arange(n)
    ax.barh(y, totals, height=0.6, color=GRID_COLOR, label="Free")
    bar_colors = [ACCENT2 if p > 85 else ACCENT4 if p > 70 else ACCENT3 for p in pcts]
    ax.barh(y, used, height=0.6, color=bar_colors)

    for i, (p, u, t) in enumerate(zip(pcts, used, totals)):
        ax.text(t + t * 0.01, i, f"{p:.1f}%  ({u:.1f}/{t:.1f} GB)",
                va="center", color=TEXT_COLOR, fontsize=9)

    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlabel("GB", color=TEXT_COLOR)
    ax.set_title("Disk Usage", color=TEXT_COLOR, fontsize=14, fontweight="bold")
    ax.set_xlim(0, max(totals) * 1.3 if totals else 100)
    fig.tight_layout()
    return _to_bytes(fig)


# ─── Process CPU/MEM Treemap ──────────────────────────────────────────────────

def process_bar_chart(processes: List[Dict], metric: str = "cpu") -> io.BytesIO:
    """processes: [{'name': ..., 'cpu': ..., 'mem': ...}]"""
    top = sorted(processes, key=lambda x: x.get(metric, 0), reverse=True)[:15]
    fig, ax = plt.subplots(figsize=(10, 6))
    _apply_dark_theme(fig, ax)

    names = [p["name"][:20] for p in top]
    values = [p.get(metric, 0) for p in top]
    colors = plt.cm.RdYlGn_r(np.linspace(0.1, 0.9, len(names)))

    bars = ax.barh(names, values, color=colors)
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                f"{val:.1f}", va="center", ha="left", color=TEXT_COLOR, fontsize=8)

    ax.set_xlabel(f"{'CPU %' if metric == 'cpu' else 'Memory MB'}", color=TEXT_COLOR)
    ax.set_title(f"Top Processes by {'CPU' if metric == 'cpu' else 'Memory'}", color=TEXT_COLOR,
                 fontsize=14, fontweight="bold")
    fig.tight_layout()
    return _to_bytes(fig)


# ─── System Dashboard (multi-panel) ──────────────────────────────────────────

def system_dashboard(
    cpu_hist: List[float],
    ram_used: float,
    ram_total: float,
    disk_data: List[Dict],
    net_sent: List[float],
    net_recv: List[float],
) -> io.BytesIO:
    fig = plt.figure(figsize=(14, 10))
    fig.patch.set_facecolor(DARK_BG)
    gs = GridSpec(3, 2, figure=fig, hspace=0.45, wspace=0.35)

    # CPU History
    ax_cpu = fig.add_subplot(gs[0, :])
    _apply_dark_theme(fig, ax_cpu)
    x = list(range(len(cpu_hist)))
    ax_cpu.fill_between(x, cpu_hist, alpha=0.35, color=ACCENT2)
    ax_cpu.plot(x, cpu_hist, color=ACCENT2, linewidth=2)
    ax_cpu.set_ylim(0, 100)
    ax_cpu.set_title("CPU Usage (%)", color=TEXT_COLOR, fontsize=12, fontweight="bold")
    ax_cpu.axhline(np.mean(cpu_hist), color=ACCENT4, linestyle="--", alpha=0.7)

    # RAM Donut
    ax_ram = fig.add_subplot(gs[1, 0])
    _apply_dark_theme(fig, ax_ram)
    free_gb = ram_total - ram_used
    pct = (ram_used / ram_total * 100) if ram_total else 0
    colors = [ACCENT2 if pct > 80 else ACCENT3, GRID_COLOR]
    ax_ram.pie([ram_used, free_gb], colors=colors, startangle=90,
               wedgeprops=dict(width=0.5, edgecolor=DARK_BG, linewidth=2))
    ax_ram.text(0, 0, f"{pct:.0f}%", ha="center", va="center",
                fontsize=20, fontweight="bold", color=TEXT_COLOR)
    ax_ram.set_title("RAM Usage", color=TEXT_COLOR, fontsize=12, fontweight="bold")
    ax_ram.axis("equal")

    # Disk bars
    ax_disk = fig.add_subplot(gs[1, 1])
    _apply_dark_theme(fig, ax_disk)
    if disk_data:
        labels = [d["mount"] for d in disk_data[:5]]
        pcts = [d["used"] / d["total"] * 100 if d["total"] else 0 for d in disk_data[:5]]
        bar_colors = [ACCENT2 if p > 85 else ACCENT4 if p > 70 else ACCENT3 for p in pcts]
        ax_disk.barh(labels, pcts, color=bar_colors, height=0.5)
        ax_disk.set_xlim(0, 100)
        ax_disk.set_xlabel("%", color=TEXT_COLOR)
    ax_disk.set_title("Disk Usage %", color=TEXT_COLOR, fontsize=12, fontweight="bold")

    # Network
    ax_net = fig.add_subplot(gs[2, :])
    _apply_dark_theme(fig, ax_net)
    xn = list(range(len(net_sent)))
    ax_net.fill_between(xn, net_sent, alpha=0.3, color=ACCENT3, label="Sent KB/s")
    ax_net.plot(xn, net_sent, color=ACCENT3, linewidth=1.5)
    ax_net.fill_between(xn, net_recv, alpha=0.3, color=ACCENT4, label="Recv KB/s")
    ax_net.plot(xn, net_recv, color=ACCENT4, linewidth=1.5)
    ax_net.legend(facecolor=PANEL_BG, labelcolor=TEXT_COLOR, fontsize=9)
    ax_net.set_title("Network I/O (KB/s)", color=TEXT_COLOR, fontsize=12, fontweight="bold")

    fig.suptitle("System Dashboard", color=TEXT_COLOR, fontsize=16, fontweight="bold", y=1.01)
    return _to_bytes(fig)


# ─── Network Speed History ────────────────────────────────────────────────────

def network_chart(sent: List[float], recv: List[float]) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(10, 4))
    _apply_dark_theme(fig, ax)
    x = list(range(len(sent)))
    ax.fill_between(x, sent, alpha=0.3, color=ACCENT3, label="Upload KB/s")
    ax.plot(x, sent, color=ACCENT3, linewidth=2)
    ax.fill_between(x, recv, alpha=0.3, color=ACCENT4, label="Download KB/s")
    ax.plot(x, recv, color=ACCENT4, linewidth=2)
    ax.set_title("Network Throughput", color=TEXT_COLOR, fontsize=14, fontweight="bold")
    ax.legend(facecolor=PANEL_BG, labelcolor=TEXT_COLOR)
    ax.set_xlabel("Samples", color=TEXT_COLOR)
    ax.set_ylabel("KB/s", color=TEXT_COLOR)
    fig.tight_layout()
    return _to_bytes(fig)


# ─── Temperature Gauges ───────────────────────────────────────────────────────

def temperature_chart(sensors: Dict[str, float]) -> io.BytesIO:
    n = len(sensors)
    if not n:
        n = 1
        sensors = {"No Data": 0.0}
    fig, axes = plt.subplots(1, n, figsize=(4 * n, 4))
    fig.patch.set_facecolor(DARK_BG)
    if n == 1:
        axes = [axes]

    for ax, (name, temp) in zip(axes, sensors.items()):
        _apply_dark_theme(fig, ax)
        color = ACCENT2 if temp > 80 else ACCENT4 if temp > 65 else ACCENT3
        theta = np.linspace(0, np.pi, 200)
        ax.plot(np.cos(theta), np.sin(theta), color=GRID_COLOR, linewidth=8)
        filled = int(temp / 100 * 200)
        ax.plot(np.cos(theta[:filled]), np.sin(theta[:filled]), color=color, linewidth=8)
        ax.text(0, -0.3, f"{temp:.1f}°C", ha="center", va="center",
                fontsize=20, fontweight="bold", color=TEXT_COLOR)
        ax.text(0, -0.6, name[:15], ha="center", va="center", fontsize=9, color=TEXT_COLOR)
        ax.set_xlim(-1.3, 1.3)
        ax.set_ylim(-0.8, 1.2)
        ax.axis("off")

    fig.suptitle("Temperature Sensors", color=TEXT_COLOR, fontsize=14, fontweight="bold")
    fig.tight_layout()
    return _to_bytes(fig)
