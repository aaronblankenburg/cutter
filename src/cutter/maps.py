"""Maps for intros: an overview map (country outlines from Natural Earth, georeferenced onto a base map) and a route
image (stage images composed in map coordinates, with place names).

overview(config): draws the outlines of a TopoJSON (e.g. Natural Earth 1:10m "countries-10m.json" from world-atlas,
  public domain) in Web Mercator, georeferenced onto the base map: water on the base map is black, land transparent;
  starting from two known points the scale and offset are refined by a small grid search for the best water overlap.
  The overview frames one country and is written at 1/factor of the base-map scale, so that it cross-fades
  seamlessly into the base map during the zoom (FilmCraft step). Place names are not drawn into the image; they
  become text layers (meta.labels in picture coordinates of the start view).
route(config): composes stage images (e.g. one per episode, earlier ones faint) at their offsets on the base map and
  adds labels.
All parameters come from a JSON config (see docs/formats.md#maps). Requires numpy and Pillow.
"""
import json
import math
import pathlib

from .util import CutterError, MISSING, dump_json, read_json

FONT_DIR = "/usr/share/fonts/truetype/liberation"


def _font(style, size, fonts=None):
    from PIL import ImageFont
    path = (fonts or {}).get(style) or f"{FONT_DIR}/LiberationSansNarrow-{style}.ttf"
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _p(base, x):
    p = pathlib.Path(x)
    return p if p.is_absolute() else base / p


def overview(cfg, base=pathlib.Path(".")):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    topo_p, map_p = _p(base, cfg["topojson"]), _p(base, cfg["base_map"])
    for p in (topo_p, map_p):
        if not p.exists():
            raise CutterError(f"Not found: {p}", MISSING)
    factor = cfg.get("factor", 2)
    topo = read_json(topo_p)
    sx, sy = topo["transform"]["scale"]; tx, ty = topo["transform"]["translate"]
    arcs = []
    for a in topo["arcs"]:
        x = y = 0; pts = []
        for dx, dy in a:
            x += dx; y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idx):
        out = []
        for i in idx:
            p = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(p if not out else p[1:])
        return out

    def polygons(g):
        if g["type"] == "Polygon":
            return [[ring(r) for r in g["arcs"]]]
        if g["type"] == "MultiPolygon":
            return [[ring(r) for r in poly] for poly in g["arcs"]]
        return []
    obj = topo["objects"][cfg.get("object", "countries")]["geometries"]
    countries = {g["properties"]["name"]: polygons(g) for g in obj if "arcs" in g}
    country = cfg["country"]
    if country not in countries:
        raise CutterError(f"Country {country!r} is not in {topo_p.name}", MISSING)
    usage = {}
    for g in obj:
        flat = []

        def collect(x):
            for y in x:
                if isinstance(y, list):
                    collect(y)
                else:
                    flat.append(y if y >= 0 else ~y)
        collect(g.get("arcs", []))
        for i in set(flat):
            usage.setdefault(i, set()).add(g["properties"]["name"])
    border_arcs = [(i, n) for i, n in usage.items() if len(n) >= 2]

    merc = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    def map_px(lon, lat, P):
        k, x0, y0 = P
        return x0 + k * math.radians(lon), y0 - k * merc(lat)

    # start values from two known points (longitude/latitude -> base-map pixels)
    (A, pA), (Bp, pB) = [(tuple(q["lonlat"]), tuple(q["px"])) for q in cfg["georef_start"]["points"][:2]]
    k0 = (pB[0] - pA[0]) / math.radians(Bp[0] - A[0])
    P0 = (k0, pA[0] - k0 * math.radians(A[0]), pA[1] + k0 * merc(A[1]))

    # refine: water on the base map (black, opaque) against water from the outlines
    base_map = Image.open(map_p).convert("RGBA")
    W0, H0 = base_map.size
    D = cfg.get("downsample", 8)
    small = np.array(base_map.resize((W0 // D, H0 // D), Image.BOX)).astype(float)
    valid = small[..., 3] > 250
    water_map = (small[..., :3].mean(-1) < 40) & valid
    near = set(cfg.get("neighbours") or [country])

    def land_mask(P, W, H, d, only=None):
        m = Image.new("L", (W, H), 0); dr = ImageDraw.Draw(m)
        for name, polys in countries.items():
            if only and name not in only:
                continue
            for poly in polys:
                for j, r in enumerate(poly):
                    dr.polygon([tuple(c / d for c in map_px(lon, lat, P)) for lon, lat in r], fill=0 if j else 255)
        return np.array(m) > 127

    def fit(P):
        water_outline = ~land_mask(P, W0 // D, H0 // D, D, near)
        a = water_map & valid; b = water_outline & valid
        return (a & b).sum() / max(1, (a | b).sum())
    best = (fit(P0), P0)
    for step in (0.02, 0.006, 0.002):            # relative scale, offset in pixels
        for _ in range(3):
            k, x0, y0 = best[1]
            for dk in (-step, 0, step):
                for dx in (-60 * step / 0.02, 0, 60 * step / 0.02):
                    for dy in (-60 * step / 0.02, 0, 60 * step / 0.02):
                        kn = k * (1 + dk)
                        xt, yt = map_px(*A, (k, x0, y0))      # scale around the first point
                        Pn = (kn, xt - kn * math.radians(A[0]) + dx, yt + kn * merc(A[1]) + dy)
                        g = fit(Pn)
                        if g > best[0]:
                            best = (g, Pn)
    P = best[1]

    # start view: the whole country in the picture
    pts = [map_px(lon, lat, P) for poly in countries[country] for lon, lat in poly[0]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
    s0 = min(1920 * 0.9 / (bx1 - bx0), 1080 * 0.9 / (by1 - by0))
    C0 = ((bx0 + bx1) / 2, (by0 + by1) / 2 - 0.015 * (by1 - by0))
    RX, RY = 960 / s0 * 1.04, 540 / s0 * 1.04
    X0, Y0, X1, Y1 = C0[0] - RX, C0[1] - RY, C0[0] + RX, C0[1] + RY
    W, H = int(math.ceil((X1 - X0) / factor)), int(math.ceil((Y1 - Y0) / factor))
    OX, OY = X0, Y0
    ov = lambda lon, lat: tuple((v - o) / factor for v, o in zip(map_px(lon, lat, P), (OX, OY)))
    SS = 2
    colors = {"country": (255, 255, 255), "others": (226, 226, 222), "water": (0, 0, 0),
              "border_country": (70, 70, 70), "border_other": (150, 150, 150), **{k: tuple(v) for k, v in cfg.get("colors", {}).items()}}
    img = Image.new("RGB", (W * SS, H * SS), colors["water"])
    dr = ImageDraw.Draw(img)
    poly_pts = lambda r: [(x * SS, y * SS) for x, y in (ov(lon, lat) for lon, lat in r)]
    for name, polys in countries.items():
        fill = colors["country"] if name == country else colors["others"]
        for poly in polys:
            for j, r in enumerate(poly):
                dr.polygon(poly_pts(r), fill=colors["water"] if j else fill)

    def dashed(pts, color, width, pattern=None):
        if not pattern:
            dr.line(pts, fill=color, width=width, joint="curve"); return
        on, off = pattern; rest = 0.0; draw = True
        for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
            L = math.hypot(xb - xa, yb - ya); pos = 0.0
            while pos < L:
                st = min(L - pos, (on if draw else off) - rest)
                if draw:
                    f0, f1 = pos / L, (pos + st) / L
                    dr.line([(xa + (xb - xa) * f0, ya + (yb - ya) * f0), (xa + (xb - xa) * f1, ya + (yb - ya) * f1)], fill=color, width=width)
                pos += st; rest += st
                if rest >= (on if draw else off) - 1e-9:
                    rest = 0.0; draw = not draw
    for i, names in border_arcs:
        pts_ = poly_pts(arcs[i])
        if country in names:
            dashed(pts_, colors["border_country"], 7 * SS, (20 * SS, 11 * SS))
        else:
            dashed(pts_, colors["border_other"], 4 * SS, (12 * SS, 10 * SS))
    img = img.resize((W, H), Image.LANCZOS).convert("RGBA")

    route = cfg.get("route_image")
    if route:
        rimg = Image.open(_p(base, route["file"])).convert("RGBA")
        RO = route["origin"]
        a = np.array(rimg).astype(float)
        red = (a[..., 0] > 150) & (a[..., 1] < 120)          # only the lines, not the place names
        alpha = np.where(red, a[..., 3], 0) / 255.0
        strong = alpha > 0.75
        for mask, opacity in ((alpha * (~strong), 0.62), (alpha * strong, 1.0)):
            m = Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8))
            m = m.resize((round(m.width / factor), round(m.height / factor)), Image.BOX).filter(ImageFilter.MaxFilter(3))
            m = Image.fromarray((np.clip(np.array(m).astype(float) * 2.2, 0, 255) * opacity).astype(np.uint8))
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            col = Image.new("RGBA", m.size, tuple(route.get("color", (225, 30, 30))) + (255,)); col.putalpha(m)
            lay.paste(col, (round((RO[0] - OX) / factor), round((RO[1] - OY) / factor)), col)
            img.alpha_composite(lay)

    def in_view(lon, lat):
        x, y = map_px(lon, lat, P); return [round(960 + (x - C0[0]) * s0, 1), round(540 + (y - C0[1]) * s0, 1)]
    labels = []
    for L in cfg.get("labels", []):
        d = {"text": L["text"], "pos": in_view(*L["lonlat"]), "size": L.get("size", 36), "color": L.get("color", "#3a3a3a"),
             "style": L.get("style", "Bold"), "tracking": L.get("tracking", 0)}
        if L.get("right"):
            d["right"] = True
        labels.append(d)
    point = in_view(*cfg["point"]["lonlat"]) if cfg.get("point") else None
    out = _p(base, cfg["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    meta = {"georeference": {"k_px_per_rad": P[0], "x0": P[1], "y0": P[2], "fit_iou_water": round(best[0], 4)},
            "factor": factor, "origin": [OX, OY], "size": [W, H], "center": [OX + W * factor / 2, OY + H * factor / 2],
            "country_bbox": [round(bx0), round(by0), round(bx1), round(by1)], "start_scale": round(s0, 4),
            "start_center": [round(C0[0]), round(C0[1])], "labels": labels, "point": point}
    if cfg.get("meta"):
        dump_json(_p(base, cfg["meta"]), meta, indent=1)
    return {"output": str(out), "size": [W, H], "fit": round(best[0], 4), "start_scale": round(s0, 4), "meta": meta,
            "text": f"Overview map: {out} ({W}x{H}), water fit {best[0]:.4f} (start {fit(P0):.4f}), start scale {s0:.4f}"}


def route(cfg, base=pathlib.Path(".")):
    import numpy as np
    from PIL import Image, ImageDraw
    OX, OY = cfg["origin"]; W, H = cfg["size"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bbox = {}
    for st in cfg["stages"]:
        p = _p(base, st["file"])
        if not p.exists():
            raise CutterError(f"Not found: {p}", MISSING)
        e = Image.open(p).convert("RGBA")
        a = np.array(e)
        if st.get("alpha", 1.0) != 1.0:
            a[..., 3] = (a[..., 3].astype(float) * st["alpha"]).astype(np.uint8)
        dx, dy = st["offset"]
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        lay.paste(Image.fromarray(a), (int(round(dx - OX)), int(round(dy - OY))))
        img.alpha_composite(lay)
        bb = e.getchannel("A").getbbox()
        if bb:
            bbox[p.name] = [bb[0] + dx, bb[1] + dy, bb[2] + dx, bb[3] + dy]
    for L in cfg.get("labels", []):
        font = _font("Italic" if L.get("italic") else "Bold", L.get("size", 44), cfg.get("fonts"))
        tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(tmp)
        x, y = L["pos"][0] - OX, L["pos"][1] - OY
        size = L.get("size", 44)
        d.text((x, y), L["text"], font=font, anchor=L.get("anchor", "mm"), fill=(40, 40, 40, 255),
               stroke_width=max(2, size // 12), stroke_fill=(255, 255, 255, 235))
        if L.get("angle"):
            from PIL import Image as _I
            tmp = tmp.rotate(L["angle"], resample=_I.BICUBIC, center=(x, y))
        img.alpha_composite(tmp)
    out = _p(base, cfg["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, optimize=True)
    meta = {"origin": [OX, OY], "size": [W, H], "center": [OX + W / 2, OY + H / 2],
            "stage_bbox": {k: [round(v, 1) for v in b] for k, b in bbox.items()}}
    if cfg.get("meta"):
        dump_json(_p(base, cfg["meta"]), meta, indent=1)
    return {"output": str(out), "size": [W, H], "meta": meta, "text": f"Route image: {out} ({W}x{H}), {len(bbox)} stages"}
