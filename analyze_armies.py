#!/usr/bin/env python3
"""
Army composition analysis using K-means clustering.
Run from project root: python analyze_armies.py [--k 12] [--plot]
"""

import sys
import os
import argparse
from collections import defaultdict

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
from cogs.army import parse_army_link, TROOP_DATA, SPELL_DATA, EQUIPMENT_DATA

TROOP_IDS    = sorted(TROOP_DATA.keys())
TROOP_NAMES  = [TROOP_DATA[i][0] for i in TROOP_IDS]

SPELL_IDS    = sorted(SPELL_DATA.keys())
SPELL_NAMES  = [SPELL_DATA[i][0] for i in SPELL_IDS]

EQUIP_IDS    = sorted(EQUIPMENT_DATA.keys())
EQUIP_NAMES  = [EQUIPMENT_DATA[i] for i in EQUIP_IDS]


def army_to_vector(code: str) -> np.ndarray | None:
    """
    Convert army code to feature vector.
    Features: troop housing-space proportions (0.65)
              + spell slot proportions (0.15)
              + equipment binary presence (0.20)
    """
    try:
        parsed = parse_army_link(code)
    except Exception:
        return None

    troop_vec = np.zeros(len(TROOP_IDS))
    total_troop = 0
    for qty, name in parsed["troops"]:
        uid = next((k for k, v in TROOP_DATA.items() if v[0] == name), None)
        if uid is None:
            continue
        space = qty * TROOP_DATA[uid][1]
        troop_vec[TROOP_IDS.index(uid)] += space
        total_troop += space

    spell_vec = np.zeros(len(SPELL_IDS))
    total_spell = 0
    for qty, name in parsed["spells"]:
        uid = next((k for k, v in SPELL_DATA.items() if v[0] == name), None)
        if uid is None:
            continue
        slots = qty * SPELL_DATA[uid][1]
        spell_vec[SPELL_IDS.index(uid)] += slots
        total_spell += slots

    equip_vec = np.zeros(len(EQUIP_IDS))
    total_equip = 0
    for hero in parsed["heroes"]:
        for equip_name in hero["equip"]:
            uid = next((k for k, v in EQUIPMENT_DATA.items() if v == equip_name), None)
            if uid is None:
                continue
            equip_vec[EQUIP_IDS.index(uid)] += 1
            total_equip += 1

    if total_troop == 0:
        return None

    troop_norm = troop_vec / total_troop
    spell_norm = spell_vec / total_spell if total_spell > 0 else spell_vec
    equip_norm = equip_vec / total_equip if total_equip > 0 else equip_vec

    return np.concatenate([troop_norm * 0.65, spell_norm * 0.15, equip_norm * 0.20])


def auto_label(top_troops: list[tuple[str, float]], top_spells: list[tuple[str, float]]) -> str:
    if not top_troops:
        return "Unknown"
    t1_name, t1_pct = top_troops[0]
    t2 = top_troops[1] if len(top_troops) > 1 else None

    if t2 and t2[1] > 0.20:
        return f"{t1_name} + {t2[0]}"
    return t1_name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--k",    type=int, default=12, help="Number of clusters (default: 12)")
    parser.add_argument("--plot", action="store_true",  help="Show PCA scatter plot (requires matplotlib)")
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
        print("Not enough data yet (need at least 100 attacks with army codes).")
        return

    print("Parsing army codes...")
    vectors  = []
    meta     = []  # (stars, trophies, code)
    failed   = 0

    for r in records:
        vec = army_to_vector(r.army_share_code)
        if vec is None:
            failed += 1
            continue
        vectors.append(vec)
        meta.append((r.stars or 0, r.trophies or 0, r.army_share_code))

    print(f"Parsed: {len(vectors):,}  |  Failed: {failed:,}")

    if len(vectors) < K:
        print(f"Not enough valid vectors for K={K}. Lower --k or collect more data.")
        return

    X = np.array(vectors)

    print(f"\nRunning K-means (K={K})...")
    kmeans = KMeans(n_clusters=K, random_state=42, n_init=10, max_iter=300)
    labels = kmeans.fit_predict(X)

    # ── Cluster analysis ──────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print(f"  ARMY CLUSTER ANALYSIS  (K={K}, n={len(vectors):,})")
    print("=" * 65)

    troop_slice = slice(0, len(TROOP_IDS))
    spell_slice = slice(len(TROOP_IDS), len(TROOP_IDS) + len(SPELL_IDS))
    equip_slice = slice(len(TROOP_IDS) + len(SPELL_IDS), None)

    cluster_rows = []
    for k in range(K):
        mask    = labels == k
        idxs    = np.where(mask)[0]
        count   = len(idxs)
        avg_vec = X[mask].mean(axis=0)

        troop_avgs = list(zip(TROOP_NAMES, avg_vec[troop_slice] / 0.65))
        spell_avgs = list(zip(SPELL_NAMES, avg_vec[spell_slice] / 0.15))
        equip_avgs = list(zip(EQUIP_NAMES, avg_vec[equip_slice] / 0.20))

        top_troops = sorted(troop_avgs, key=lambda x: x[1], reverse=True)
        top_troops = [(n, p) for n, p in top_troops if p > 0.02][:5]

        top_spells = sorted(spell_avgs, key=lambda x: x[1], reverse=True)
        top_spells = [(n, p) for n, p in top_spells if p > 0.05][:3]

        top_equips = sorted(equip_avgs, key=lambda x: x[1], reverse=True)
        top_equips = [(n, p) for n, p in top_equips if p > 0.05][:6]

        stars_vals   = [meta[i][0] for i in idxs]
        trophy_vals  = [meta[i][1] for i in idxs]
        avg_stars    = sum(stars_vals)  / count
        avg_trophies = sum(trophy_vals) / count

        label = auto_label(top_troops, top_spells)
        cluster_rows.append((count, k, label, top_troops, top_spells, top_equips, avg_stars, avg_trophies))

    cluster_rows.sort(reverse=True)

    for count, k, label, top_troops, top_spells, top_equips, avg_stars, avg_trophies in cluster_rows:
        pct = count / len(vectors) * 100
        print(f"\n  [{k:>2}] {label}")
        print(f"       {count:>5} attacks  ({pct:4.1f}%)  |  "
              f"avg {avg_stars:.2f}⭐  {avg_trophies:+.1f} trophies")
        print("       Troops:")
        for name, pct_val in top_troops:
            bar = "█" * int(pct_val * 25)
            print(f"         {name:<26} {bar:<25} {pct_val*100:4.1f}%")
        if top_spells:
            print("       Spells:    " + "  |  ".join(f"{n} {p*100:.0f}%" for n, p in top_spells))
        if top_equips:
            print("       Equipment: " + "  |  ".join(f"{n} {p*100:.0f}%" for n, p in top_equips))

    print("\n" + "=" * 65)

    # ── Optional PCA plot ─────────────────────────────────────────────────────
    if args.plot:
        try:
            import matplotlib.pyplot as plt
            import matplotlib.cm as cm

            print("\nGenerating PCA scatter plot...")
            pca = PCA(n_components=2, random_state=42)
            X2  = pca.fit_transform(X)

            colors  = cm.tab20(np.linspace(0, 1, K))
            fig, ax = plt.subplots(figsize=(12, 8))

            for k in range(K):
                mask = labels == k
                _, _, label, *_ = next(r for r in cluster_rows if r[1] == k)
                ax.scatter(X2[mask, 0], X2[mask, 1],
                           color=colors[k], label=label, alpha=0.4, s=10)

            ax.legend(loc="upper right", fontsize=7, markerscale=3)
            ax.set_title(f"Army Clusters — PCA (K={K}, n={len(vectors):,})")
            ax.set_xlabel("PC1")
            ax.set_ylabel("PC2")
            plt.tight_layout()
            plt.savefig("army_clusters.png", dpi=150)
            print("Saved: army_clusters.png")
            plt.show()
        except ImportError:
            print("matplotlib not installed. Run: pip install matplotlib")


if __name__ == "__main__":
    main()
