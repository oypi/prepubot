#!/usr/bin/env python3
"""
PrepuBot Match Analysis & Whiff Diagnostic Tool
Reads match_debug.jsonl and provides formatted reports on touches, near-misses,
kickoffs, and exact 3D spatial miss diagnostics.
"""

import os
import sys
import json
from collections import defaultdict


def analyze_matches(log_path=None, limit=15):
    if log_path is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(base_dir, "match_debug.jsonl")

    if not os.path.exists(log_path):
        print(f"[-] No flight recorder log found at: {log_path}")
        print("    Play a few matches with PrepuBot active to generate telemetry data!")
        return

    touches = []
    whiffs = []
    kickoffs = []
    summaries = []

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                etype = entry.get("type")
                if etype == "encounter":
                    if entry.get("result") == "TOUCH":
                        touches.append(entry)
                    elif entry.get("result") == "WHIFF":
                        whiffs.append(entry)
                elif etype == "kickoff":
                    kickoffs.append(entry)
                elif etype == "session_summary":
                    summaries.append(entry)
            except Exception:
                continue

    total_encounters = len(touches) + len(whiffs)
    touch_rate = (len(touches) / total_encounters * 100.0) if total_encounters > 0 else 0.0

    print("================================================================================")
    print("                    PREPUBOT MATCH FLIGHT RECORDER ANALYSIS                     ")
    print("================================================================================")
    print(f" Log File: {log_path}")
    print(f" Total Ball Encounters Tracked: {total_encounters}")
    print(f"   [+] Confirmed Touches:       {len(touches)} ({touch_rate:.1f}%)")
    print(f"   [-] Whiffs / Near-Misses:    {len(whiffs)} ({100.0 - touch_rate:.1f}%)")
    print(f"   [#] Kickoffs Tracked:        {len(kickoffs)}")
    print("================================================================================\n")

    # Kickoff Analysis
    if kickoffs:
        print("--- [ KICKOFF PERFORMANCE ] ---------------------------------------------------")
        first_touches = sum(1 for k in kickoffs if k.get("first_touch"))
        first_touch_rate = (first_touches / len(kickoffs)) * 100.0
        print(f" Total Kickoffs: {len(kickoffs)} | First Touch Win Rate: {first_touch_rate:.1f}%")
        spawn_counts = defaultdict(int)
        for k in kickoffs:
            spawn_counts[k.get("spawn", "UNKNOWN")] += 1
        for spawn, count in spawn_counts.items():
            print(f"   - {spawn:<20}: {count} kickoffs")
        print()

    # Whiff Categorization
    if whiffs:
        print("--- [ WHIFF / MISS DIAGNOSTIC BREAKDOWN ] -------------------------------------")
        diag_counts = defaultdict(int)
        for w in whiffs:
            diag = w.get("diagnostic", "Other")
            # Classify primary reason
            if "ABOVE" in diag:
                diag_counts["Under-jump / Ball too high"] += 1
            elif "BELOW" in diag:
                diag_counts["Over-jump / Ball too low"] += 1
            elif "RIGHT" in diag or "LEFT" in diag:
                diag_counts["Lateral Alignment / Steered off"] += 1
            elif "AHEAD" in diag:
                diag_counts["Late arrival / Flipped early"] += 1
            elif "BEHIND" in diag:
                diag_counts["Overshot ball"] += 1
            else:
                diag_counts["Close graze"] += 1

        for cat, count in sorted(diag_counts.items(), key=lambda x: x[1], reverse=True):
            pct = (count / len(whiffs)) * 100.0
            print(f"   {cat:<35}: {count:3d} ({pct:5.1f}%)")
        print()

        # Detailed Miss Timeline
        print(f"--- [ RECENT WHIFF DETAILS (Last {min(limit, len(whiffs))}) ] -----------------------------------")
        for i, w in enumerate(reversed(whiffs[-limit:]), 1):
            ts = w.get("timestamp", "N/A")
            min_d = w.get("min_dist", 0.0)
            car_spd = w.get("car_spd", 0.0)
            ball_spd = w.get("ball_spd", 0.0)
            diag = w.get("diagnostic", "N/A")
            off = w.get("miss_offset", {})
            dx = off.get("right_left", 0.0)
            dy = off.get("fwd_back", 0.0)
            dz = off.get("up_down", 0.0)
            act = w.get("action_at_closest", {})

            act_flags = []
            if act.get("boost"): act_flags.append("BOOST")
            if act.get("jump"): act_flags.append("JUMP")
            if act.get("pitch", 0) > 0: act_flags.append("PITCH_UP")
            elif act.get("pitch", 0) < 0: act_flags.append("PITCH_DN")
            if act.get("steer", 0) > 0: act_flags.append("STEER_R")
            elif act.get("steer", 0) < 0: act_flags.append("STEER_L")
            act_str = "+".join(act_flags) if act_flags else "COAST"

            print(f" #{i:<2} [{ts}] Closest Dist: {min_d:5.1f} uu | Car: {car_spd:4.0f} uu/s | Ball: {ball_spd:4.0f} uu/s")
            print(f"     Relative Offset (Car Local) : X(R/L)={dx:+5.1f} uu | Y(F/B)={dy:+5.1f} uu | Z(U/D)={dz:+5.1f} uu")
            print(f"     Controller Inputs at Miss   : {act_str}")
            print(f"     Diagnosis                   : {diag}")
            print("-" * 80)
    else:
        print("[+] Outstanding! Zero whiffs recorded in logged encounters.\n")

    print("================================================================================")


if __name__ == "__main__":
    analyze_matches()
