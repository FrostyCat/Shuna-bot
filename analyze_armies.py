#!/usr/bin/env python3
"""
Army composition analysis using K-means clustering.

Usage:
  python analyze_armies.py              # analyse and print clusters
  python analyze_armies.py --k 15       # custom cluster count
  python analyze_armies.py --save       # label clusters interactively and save army_clusters.json
  python analyze_armies.py --plot       # also show PCA scatter plot
"""

import sys
import os
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA
except ImportError:
    print("Missing dependencies. Run: pip install numpy scikit-learn")
    sys.exit(1)

from db import Session
from models import Attack
from cogs.army import (
    army_to_vector,
    TROOP_DATA, SPELL_DATA, EQUIPMENT_DATA,
    TROOP_IDS, SPELL_IDS, EQUIP_IDS,
)

TROOP_NAMES = [TROOP_DATA[i][0] for i in TROOP_IDS]
SPELL_NAMES = [SPELL_DATA[i][0]  for i in SPELL_IDS]
EQUIP_NAMES = [EQUIPMENT_DATA[i] for i in EQUIP_IDS]

CLUSTERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "army_clusters.json")

TROOP_SLICE = slice(0, len(TROOP_IDS))
SPELL_SLICE = slice(len(TROOP_IDS), len(TROOP_IDS) + len(SPELL_IDS))
EQUIP_SLICE = slice(len(TROOP_IDS) + len(SPELL_IDS), None)


def auto_label(top_troops) -> str:
    if not top_troops:
        return "Unknown"
    t1, _ = top_troops[0]
    t2 = top_troops[1] if len(top_troops) > 1 else None
    if t2 and t2[1] > 0.20:
        return f"{t1} + {t2[0]}"
    return t1


def analyse(X, labels, meta, K) -> list[dict]:
    """Return list of cluster dicts sorted by size (descending)."""
    rows = []
    for k in range(K):
        mask  = labels == k
        idxs  = np.where(mask)[0]
        count = len(idxs)
        if count == 0:
            continue
        avg_vec = X[mask].mean(axis=0)

        troop_avgs = sorted(zip(TROOP_NAMES, avg_vec[TROOP_SLICE] / 0.65), key=lambda x: x[1], reverse=True)
        spell_avgs = sorted(zip(SPELL_NAMES, avg_vec[SPELL_SLICE] / 0.15), key=lambda x: x[1], reverse=True)
        equip_avgs = sorted(zip(EQUIP_NAMES, avg_vec[EQUIP_SLICE] / 0.20), key=lambda x: x[1], reverse=True)

        top_troops = [(n, p) for n, p in troop_avgs if p > 0.02][:5]
        top_spells = [(n, p) for n, p in spell_avgs if p > 0.05][:3]
        top_equips = [(n, p) for n, p in equip_avgs if p > 0.05][:6]

        stars  = [meta[i][0] for i in idxs]
        trophies = [meta[i][1] for i in idxs]
        three_star_pct = sum(1 for s in stars if s == 3) / count * 100

        rows.append({
            "k":           k,
            "count":       count,
            "auto_label":  auto_label(top_troops),
            "top_troops":  top_troops,
            "top_spells":  top_spells,
            "top_equips":  top_equips,
            "avg_stars":   sum(stars) / count,
            "avg_trophies": sum(trophies) / count,
            "three_star_pct": three_star_pct,
            "centroid":    avg_vec.tolist(),
        })

    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows


def print_cluster(r: dict, total: int):
    pct = r["count"] / total * 100
    print(f"\n  [{r['k']:>2}] {r['auto_label']}")
    print(f"       {r['count']:>5} attacks  ({pct:4.1f}%)  |  "
          f"avg {r['avg_stars']:.2f}*  3*: {r['three_star_pct']:.0f}%  "
          f"{r['avg_trophies']:+.1f} trophies")
    print("       Troops:")
    for name, p in r["top_troops"]:
        bar = "█" * int(p * 25)
        print(f"         {name:<26} {bar:<25} {p*100:4.1f}%")
    if r["top_spells"]:
        print("       Spells:    " + "  |  ".join(f"{n} {p*100:.0f}%" for n, p in r["top_spells"]))
    if r["top_equips"]:
        print("       Equipment: " + "  |  ".join(f"{n} {p*100:.0f}%" for n, p in r["top_equips"]))


def interactive_label(rows: list[dict], total: int) -> list[dict]:
    print("\n" + "=" * 65)
    print("  LABELLING MODE — press Enter to accept auto-label")
    print("=" * 65)
    for r in rows:
        print_cluster(r, total)
        suggested = r["auto_label"]
        user_input = input(f"\n  Label (Enter = \"{suggested}\"): ").strip()
        r["label"] = user_input if user_input else suggested
        print(f"  -> Saved as: {r['label']}")
    return rows


def save_clusters(rows: list[dict]):
    data = {
        "k": len(rows),
        "troop_ids": TROOP_IDS,
        "spell_ids": SPELL_IDS,
        "equip_ids": EQUIP_IDS,
        "clusters": [{"label": r["label"], "centroid": r["centroid"]} for r in rows],
    }
    with open(CLUSTERS_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nSaved {len(rows)} clusters to {CLUSTERS_PATH}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--k",    type=int, default=12)
    parser.add_argument("--save", action="store_true", help="Label clusters and save army_clusters.json")
    parser.add_argument("--plot", action="store_true", help="Show PCA scatter plot")
    args = parser.parse_args()
    K = args.k

    print("Loading army data from database...")
    session = Session()
    records = (
        session.query(Attack)
        .filter(Attack.army_share_code.isnot(None), Attack.is_attack == True)
        .all()
    )
    session.close()
    print(f"Found {len(records):,} attacks with army data")

    if len(records) < 100:
        print("Not enough data (need at least 100 attacks with army codes).")
        return

    print("Parsing army codes...")
    vectors, meta, failed = [], [], 0
    for r in records:
        vec = army_to_vector(r.army_share_code)
        if vec is None:
            failed += 1
            continue
        vectors.append(vec)
        meta.append((r.stars or 0, r.trophies or 0))

    print(f"Parsed: {len(vectors):,}  |  Failed: {failed:,}")

    if len(vectors) < K:
        print(f"Not enough valid vectors for K={K}.")
        return

    X = np.array(vectors)
    print(f"\nRunning K-means (K={K})...")
    kmeans = KMeans(n_clusters=K, random_state=42, n_init=10, max_iter=300)
    labels = kmeans.fit_predict(X)

    rows = analyse(X, labels, meta, K)
    total = len(vectors)

    print("\n" + "=" * 65)
    print(f"  ARMY CLUSTER ANALYSIS  (K={K}, n={total:,})")
    print("=" * 65)
    for r in rows:
        print_cluster(r, total)
    print("\n" + "=" * 65)

    if args.save:
        rows = interactive_label(rows, total)
        save_clusters(rows)
        print("\nRestart the bot to load the new clusters.")

    if args.plot:
        try:
            import matplotlib.pyplot as plt
            import matplotlib.cm as cm
            print("\nGenerating PCA scatter plot...")
            pca = PCA(n_components=2, random_state=42)
            X2  = pca.fit_transform(X)
            colors = cm.tab20(np.linspace(0, 1, K))
            fig, ax = plt.subplots(figsize=(12, 8))
            for r in rows:
                k    = r["k"]
                lbl  = r.get("label", r["auto_label"])
                mask = labels == k
                ax.scatter(X2[mask, 0], X2[mask, 1], color=colors[k % 20],
                           label=lbl, alpha=0.4, s=10)
            ax.legend(loc="upper right", fontsize=7, markerscale=3)
            ax.set_title(f"Army Clusters — PCA (K={K}, n={total:,})")
            ax.set_xlabel("PC1")
            ax.set_ylabel("PC2")
            plt.tight_layout()
            plt.savefig("army_clusters_pca.png", dpi=150)
            print("Saved: army_clusters_pca.png")
        except ImportError:
            print("matplotlib not installed.")


if __name__ == "__main__":
    main()
