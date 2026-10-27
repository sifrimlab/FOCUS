"""Deterministic categorical data colors shared by AnnData and the alignment API."""
import colorsys

UNASSIGNED_COLOR = "#92979f"


def label_order(labels):
    def key(label):
        try:
            return (0, int(label), label)
        except ValueError:
            return (1, 0, label)
    return sorted(set(map(str, labels)) - {"unassigned"}, key=key) + (["unassigned"] if "unassigned" in labels else [])


def cluster_colors(labels):
    result = {}
    used = {UNASSIGNED_COLOR}
    for i, label in enumerate(label_order(labels)):
        if label == "unassigned":
            result[label] = UNASSIGNED_COLOR
            continue
        # Golden-angle hues with alternating lightness. Never cycle a fixed palette.
        j = i
        while True:
            rgb = colorsys.hls_to_rgb((j * 0.618033988749895) % 1, (0.46, 0.63, 0.38)[j % 3], 0.65)
            color = "#" + "".join(f"{round(255 * c):02x}" for c in rgb)
            if color not in used:
                break
            j += 1
        used.add(color)
        result[label] = color
    return result
