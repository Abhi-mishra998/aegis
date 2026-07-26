"""Render SVG figures locally from the trimmed chart data."""
import json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = "/Users/abhishekmishra/mcp-security-controller/acp/docs/testing/2026-07-26/figures"
os.makedirs(OUT, exist_ok=True)

data = json.load(open("/tmp/chart-ms.json"))  # {kind: [ms, ms, ...]}
scale = [
    {"c": 50,   "rps": 51.1, "ok_pct": 1.8,  "p50": 584,   "p95": 2432,  "p99": 3460,  "max": 4796},
    {"c": 100,  "rps": 40.5, "ok_pct": 3.6,  "p50": 1856,  "p95": 5579,  "p99": 6280,  "max": 8225},
    {"c": 250,  "rps": 29.3, "ok_pct": 7.8,  "p50": 6436,  "p95": 12710, "p99": 16257, "max": 17699},
    {"c": 500,  "rps": 33.4, "ok_pct": 0.0,  "p50": 18687, "p95": 25444, "p99": 26080, "max": 28788},
    {"c": 1000, "rps": 47.4, "ok_pct": 0.0,  "p50": 24204, "p95": 28035, "p99": 28517, "max": 32001},
    {"c": 2000, "rps": 66.7, "ok_pct": 0.0,  "p50": 20474, "p95": 20474, "p99": 20474, "max": 20474},
]

classes = [("inject", "prompt-injection (403 block)", "#c0392b"),
           ("pii", "PII (400 block)", "#e67e22"),
           ("cost", "cost-cap (400 block)", "#8e44ad"),
           ("allow", "search_web (200 mixed with 429)", "#27ae60")]

# Chart 1: histograms
fig, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
for ax, (k, label, color) in zip(axes.flatten(), classes):
    lats = data[k]
    ax.hist(lats, bins=30, color=color, edgecolor="black", linewidth=0.4)
    s = sorted(lats)
    p50, p95 = s[len(s)//2], s[int(len(s)*0.95)]
    ax.set_title(f"{label}\nn={len(lats)}, p50={p50}ms, p95={p95}ms", fontsize=10)
    ax.set_xlabel("latency (ms)")
    ax.set_ylabel("requests")
    ax.grid(True, alpha=0.3)
fig.suptitle("Aegis per-request latency by class · 200 samples each · 2026-07-26",
             fontsize=11, fontweight="bold")
fig.savefig(f"{OUT}/fig-1-latency-histograms.svg", format="svg")
plt.close(fig)
print("fig-1 done")

# Chart 2: CDF
fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
for k, label, color in classes:
    lats = sorted(data[k])
    y = np.arange(1, len(lats)+1) / len(lats)
    ax.plot(lats, y, label=label, color=color, linewidth=2)
ax.set_xlabel("latency (ms) — log scale")
ax.set_ylabel("cumulative fraction")
ax.set_title("Latency CDF by request class · lower + steeper = better")
ax.legend(loc="lower right", fontsize=9)
ax.grid(True, alpha=0.3)
ax.set_xscale("log")
ax.set_xlim(50, 20000)
fig.savefig(f"{OUT}/fig-2-latency-cdf.svg", format="svg")
plt.close(fig)
print("fig-2 done")

# Chart 3: scalability sweep
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
concs = [d["c"] for d in scale]
ax1.plot(concs, [d["p50"] for d in scale], "o-", label="p50", color="#27ae60", linewidth=2, markersize=8)
ax1.plot(concs, [d["p95"] for d in scale], "s-", label="p95", color="#e67e22", linewidth=2, markersize=8)
ax1.plot(concs, [d["p99"] for d in scale], "^-", label="p99", color="#c0392b", linewidth=2, markersize=8)
ax1.set_xlabel("concurrent workers on ONE employee key")
ax1.set_ylabel("latency (ms) — log")
ax1.set_title("Latency vs concurrency · single-key sweep")
ax1.set_xscale("log"); ax1.set_yscale("log")
ax1.legend(loc="upper left", fontsize=9)
ax1.grid(True, which="both", alpha=0.3)

ax2b = ax2.twinx()
ax2.bar(range(len(concs)), [d["rps"] for d in scale], color="#3498db", alpha=0.6)
ax2b.plot(range(len(concs)), [d["ok_pct"] for d in scale], "ro-", linewidth=2, markersize=8)
ax2.set_xticks(range(len(concs))); ax2.set_xticklabels([str(c) for c in concs])
ax2.set_xlabel("concurrent workers")
ax2.set_ylabel("observed RPS (bars)", color="#3498db")
ax2b.set_ylabel("success % (line)", color="#c0392b")
ax2.set_title("Throughput vs success · aggressive rate-limit + quarantine as designed")
ax2.tick_params(axis="y", labelcolor="#3498db")
ax2b.tick_params(axis="y", labelcolor="#c0392b")
ax2b.set_ylim(0, 100)
ax2.grid(True, alpha=0.3)
fig.suptitle("Scalability sweep · 50→2000 workers × 30s each · 2026-07-26",
             fontweight="bold", fontsize=11)
fig.savefig(f"{OUT}/fig-3-scalability.svg", format="svg")
plt.close(fig)
print("fig-3 done")

# Chart 4: chaos timeline
timeline = [
    (0,200,"OK"),(2,200,"OK"),(2.5,200,"OK"),
    (3,503,"kill"),(3.5,503,"kill"),(4,503,"kill"),(4.5,503,"kill"),(5,503,"kill"),
    (5.5,0,"restart"),
    (21,200,"OK"),(22,200,"OK"),(23,200,"OK"),
]
fig, ax = plt.subplots(figsize=(11, 3.5), constrained_layout=True)
cm = {200: "#27ae60", 503: "#c0392b", 0: "#7f8c8d"}
ts = [t for t,_,_ in timeline]
codes = [c for _,c,_ in timeline]
ax.scatter(ts, codes, c=[cm[c] for c in codes], s=200, zorder=3, edgecolor="black", linewidth=0.5)
ax.axvspan(3, 21, alpha=0.15, color="red")
ax.axvline(3, color="red", linestyle="--", alpha=0.5)
ax.axvline(21, color="green", linestyle="--", alpha=0.5)
ax.text(3.3, 400, "docker kill", fontsize=9, color="red")
ax.text(15, 400, "recovery ≈ 16s", fontsize=9, color="green")
ax.set_yticks([0, 200, 429, 503])
ax.set_yticklabels(["network err", "200 OK", "429 rate-lim", "503 fail-closed"])
ax.set_xlabel("seconds since baseline")
ax.set_title("Chaos: kill Decision service · 5/5 fail-CLOSED · 16s recovery",
             fontweight="bold")
ax.grid(True, axis="y", alpha=0.3)
ax.set_xlim(-1, 25); ax.set_ylim(-50, 600)
fig.savefig(f"{OUT}/fig-4-chaos-decision.svg", format="svg")
plt.close(fig)
print("fig-4 done")

# Chart 5: attack matrix
matrix = {
    "prompt injection":      (22, 25),
    "PII (SSN/CC/API-key)":  (22, 22),
    "persona hijack":        (14, 14),
    "unicode obfuscation":   (4,  4),
    "heavy char-obf (leet)": (0,  6),
    "cost / abuse":          (13, 13),
    "cross-tenant":          (12, 12),
    "tool not-in-allowlist": (6,  6),
}
fig, ax = plt.subplots(figsize=(9, 5.5), constrained_layout=True)
labels = list(matrix.keys())
pct = [100*b/t if t else 0 for b, t in matrix.values()]
y = np.arange(len(labels))
ax.barh(y, pct,
    color=["#27ae60" if p == 100 else ("#f39c12" if p > 70 else "#c0392b") for p in pct],
    edgecolor="black", linewidth=0.5)
for i, ((b, t), p) in enumerate(zip(matrix.values(), pct)):
    ax.text(min(p+1, 95), i, f"  {b}/{t} = {p:.1f}%", va="center", fontsize=10, fontweight="bold")
ax.set_yticks(y); ax.set_yticklabels(labels)
ax.set_xlim(0, 115)
ax.set_xlabel("percent blocked at Aegis")
ax.set_title("Attack coverage matrix · focused (23) + broad (100) · post-fix",
             fontweight="bold")
ax.axvline(88.7, color="black", linestyle="--", alpha=0.5)
ax.text(88.7, -0.5, "overall recall 0.887", fontsize=9)
ax.invert_yaxis()
ax.grid(True, axis="x", alpha=0.3)
fig.savefig(f"{OUT}/fig-5-attack-matrix.svg", format="svg")
plt.close(fig)
print("fig-5 done")

# Chart 6: CPU time-series (from /tmp/rstats.txt)
rstats_path = "/tmp/rstats.txt"
if os.path.exists(rstats_path):
    import re
    ticks = []
    svcs = {"acp_gateway": [], "acp_decision": [], "acp_audit": [], "acp_policy": [], "acp_behavior": []}
    for line in open(rstats_path):
        m = re.match(r"tick=(\d+) (.*)", line.strip())
        if not m: continue
        ticks.append(int(m.group(1)))
        found = {s: 0.0 for s in svcs}
        for pair in m.group(2).split():
            parts = pair.split(":")
            if len(parts) >= 3 and parts[0] in svcs:
                try: found[parts[0]] = float(parts[1].rstrip("%"))
                except ValueError: pass
        for s in svcs:
            svcs[s].append(found[s])
    fig, ax = plt.subplots(figsize=(11, 4.5), constrained_layout=True)
    for name, series in svcs.items():
        ax.plot(ticks, series, "-o", label=name.replace("acp_", ""), linewidth=1.5, markersize=4)
    ax.set_xlabel("time (s from start)")
    ax.set_ylabel("CPU %")
    ax.set_title("Per-container CPU % during 5-worker load · 2026-07-26", fontweight="bold")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.savefig(f"{OUT}/fig-6-cpu-timeseries.svg", format="svg")
    plt.close(fig)
    print("fig-6 done")

print(f"figures at {OUT}")
