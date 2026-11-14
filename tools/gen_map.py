"""Build an offline hiking map for the route corridor.

Inputs : day1.gpx, day2.gpx (repo root)
Outputs: data/map.json   - vector features clipped to the corridor (OSM, ODbL)
         data/relief.jpg - shaded relief from Copernicus GLO-30 DEM
"""
import json, math, re, sys, time, io
import numpy as np, requests
from PIL import Image
from scipy import ndimage
from shapely.geometry import LineString, Polygon, MultiLineString, MultiPolygon, Point, mapping
from shapely.ops import unary_union
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import rasterio
from rasterio.merge import merge

UA = {"User-Agent": "carmel-trip-offline-map/1.0 (github.com/liavcarasso/carmel-trip)"}
LAT0 = 32.69
KX, KY = 111320 * math.cos(math.radians(LAT0)), 110540.0
to_xy = lambda lat, lon: (lon * KX, lat * KY)
to_ll = lambda x, y: (y / KY, x / KX)
BUF = 900.0  # metres each side of the route

def read_gpx(p):
    g = open(p, encoding="utf-8").read()
    return [(float(a), float(b)) for a, b in re.findall(r'<trkpt lat="([\d.]+)" lon="([\d.]+)"', g)]

route = read_gpx("day1.gpx") + read_gpx("day2.gpx")
line = LineString([to_xy(*p) for p in route])
corr = line.buffer(BUF, resolution=8)
corr_s = corr.simplify(30)
minx, miny, maxx, maxy = corr.bounds
S, W = to_ll(minx, miny); N, E = to_ll(maxx, maxy)
S, W, N, E = S - 0.002, W - 0.002, N + 0.002, E + 0.002
print("bbox", S, W, N, E)

# ---------------- OSM ----------------
q = f"""[out:json][timeout:300];
(
 way["highway"]({S},{W},{N},{E});
 way["waterway"~"^(stream|river|ditch|drain|canal)$"]({S},{W},{N},{E});
 way["natural"~"^(wood|scrub|cliff|water|bare_rock)$"]({S},{W},{N},{E});
 way["landuse"~"^(forest|residential|farmland|orchard|cemetery|military|industrial|meadow|vineyard)$"]({S},{W},{N},{E});
 way["building"]({S},{W},{N},{E});
 way["railway"="rail"]({S},{W},{N},{E});
 node["natural"~"^(peak|spring|cave_entrance)$"]({S},{W},{N},{E});
 node["tourism"~"^(viewpoint|picnic_site|camp_site|attraction)$"]({S},{W},{N},{E});
 node["historic"]({S},{W},{N},{E});
 node["place"~"^(city|town|village|hamlet|suburb|neighbourhood|locality)$"]({S},{W},{N},{E});
 node["amenity"~"^(drinking_water|parking|shelter)$"]({S},{W},{N},{E});
 relation["route"="hiking"]({S},{W},{N},{E});
);
out body geom;"""
els = None
for url in ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]:
    for attempt in range(2):
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=400)
            if r.ok:
                els = r.json()["elements"]; print("overpass ok", url, len(els)); break
            print("overpass", url, r.status_code, r.text[:200])
        except Exception as e:
            print("overpass err", url, e)
        time.sleep(10)
    if els is not None:
        break
if els is None:
    sys.exit("overpass failed")

def name_of(t):
    return t.get("name:he") or t.get("name") or ""

def clip_line(coords):
    if len(coords) < 2: return []
    g = LineString([to_xy(la, lo) for la, lo in coords])
    if not g.intersects(corr_s): return []
    g = g.intersection(corr).simplify(2.5)
    parts = [g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))
    out = []
    for p in parts:
        if isinstance(p, LineString) and len(p.coords) >= 2:
            out.append([[round(to_ll(x, y)[0], 6), round(to_ll(x, y)[1], 6)] for x, y in p.coords])
    return out

def clip_poly(coords):
    if len(coords) < 4: return []
    try:
        g = Polygon([to_xy(la, lo) for la, lo in coords])
        if not g.is_valid: g = g.buffer(0)
        if not g.intersects(corr_s): return []
        g = g.intersection(corr).simplify(2.5)
    except Exception:
        return []
    parts = [g] if isinstance(g, Polygon) else list(getattr(g, "geoms", []))
    out = []
    for p in parts:
        if isinstance(p, Polygon) and p.area > 30:
            out.append([[round(to_ll(x, y)[0], 6), round(to_ll(x, y)[1], 6)] for x, y in p.exterior.coords])
    return out

HW = {"motorway": "mw", "trunk": "mw", "primary": "pr", "secondary": "se", "tertiary": "te", "motorway_link": "mw", "trunk_link": "mw",
      "primary_link": "pr", "secondary_link": "se", "tertiary_link": "te", "unclassified": "mi", "residential": "mi", "living_street": "mi",
      "service": "sv", "track": "tr", "path": "pa", "footway": "pa", "steps": "st", "bridleway": "pa", "cycleway": "pa", "pedestrian": "mi"}
out = {k: [] for k in ["roads", "water", "rail", "landuse", "build", "cliff", "trails", "pois"]}
for e in els:
    t = e.get("tags", {})
    if e["type"] == "way":
        geom = [(p["lat"], p["lon"]) for p in e.get("geometry", [])]
        if "highway" in t and t["highway"] in HW:
            for c in clip_line(geom): out["roads"].append([HW[t["highway"]], name_of(t) if t["highway"] in ("primary","secondary","tertiary","trunk","motorway") else "", c])
        elif "waterway" in t:
            for c in clip_line(geom): out["water"].append(c)
        elif t.get("railway") == "rail":
            for c in clip_line(geom): out["rail"].append(c)
        elif t.get("natural") == "cliff":
            for c in clip_line(geom): out["cliff"].append(c)
        elif "building" in t:
            for c in clip_poly(geom): out["build"].append(c)
        elif t.get("landuse") or t.get("natural") in ("wood", "scrub", "water", "bare_rock"):
            k = t.get("landuse") or t.get("natural")
            cls = {"forest": "wd", "wood": "wd", "scrub": "sc", "residential": "rs", "industrial": "rs", "farmland": "fa", "orchard": "or",
                   "vineyard": "or", "meadow": "fa", "cemetery": "cm", "military": "ml", "water": "wa", "bare_rock": "rk"}.get(k)
            if cls and geom and geom[0] == geom[-1]:
                for c in clip_poly(geom): out["landuse"].append([cls, c])
    elif e["type"] == "node":
        n = name_of(t)
        kind = None
        if t.get("natural") == "peak": kind = "peak"
        elif t.get("natural") == "spring": kind = "spring"
        elif t.get("natural") == "cave_entrance": kind = "cave"
        elif t.get("tourism") == "viewpoint": kind = "view"
        elif t.get("tourism") in ("picnic_site",): kind = "picnic"
        elif t.get("tourism") == "camp_site": kind = "camp"
        elif "historic" in t: kind = "hist"
        elif "place" in t: kind = "place_" + t["place"]
        elif t.get("amenity") == "parking": kind = "parking"
        elif t.get("amenity") == "shelter": kind = "shelter"
        if not kind: continue
        if kind.startswith("place_"):
            if not n: continue
        else:
            if not corr.contains(Point(to_xy(e["lat"], e["lon"]))): continue
            if kind in ("hist", "picnic", "parking", "shelter") and not n and kind != "shelter": continue
        out["pois"].append([kind, n, round(e["lat"], 6), round(e["lon"], 6), t.get("ele", "")])
    elif e["type"] == "relation" and t.get("route") == "hiking":
        if t.get("network") == "nwn" or "Israel National Trail" in t.get("name:en", "") or t.get("name") == "שביל ישראל":
            continue
        col = (t.get("osmc:symbol", "").split(":")[0] or t.get("colour", "")).lower()
        col = {"red": "r", "blue": "b", "green": "g", "black": "k", "purple": "p", "orange": "o"}.get(col)
        if not col: continue
        for m in e.get("members", []):
            if m.get("type") == "way" and m.get("geometry"):
                for c in clip_line([(p["lat"], p["lon"]) for p in m["geometry"]]):
                    out["trails"].append([col, c])

# ---------------- DEM ----------------
tiles = []
for lon0 in (34, 35):
    name = f"Copernicus_DSM_COG_10_N32_00_E0{lon0}_00_DEM"
    url = f"https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif"
    r = requests.get(url, headers=UA, timeout=300); r.raise_for_status()
    open(f"/tmp/{name}.tif", "wb").write(r.content); tiles.append(rasterio.open(f"/tmp/{name}.tif"))
    print("dem", name, len(r.content))
mosaic, tr = merge(tiles, bounds=(W, S, E, N))
dem = mosaic[0].astype(np.float32)
dem[dem < -100] = 0
res_lon, res_lat = tr.a, -tr.e
print("dem shape", dem.shape, res_lon, res_lat)
# upsample to ~10 m and smooth
fy = (res_lat * KY) / 10.0; fx = (res_lon * KX) / 10.0
up = ndimage.zoom(dem, (fy, fx), order=3)
up = ndimage.gaussian_filter(up, 1.6)
H, Wd = up.shape
dx = (E - W) / Wd * KX; dy = (N - S) / H * KY
gy, gx = np.gradient(up, dy, dx)
slope = np.arctan(np.hypot(gx, gy) * 1.6)
aspect = np.arctan2(-gy, gx)  # rows go south
az, alt = math.radians(315), math.radians(42)
hs = np.cos(alt) * np.cos(slope) + np.sin(alt) * np.sin(slope) * np.cos(az - aspect)
hs = np.clip(hs, 0, 1)
stops = np.array([0, 60, 150, 300, 450, 560])
cols = np.array([[236, 240, 222], [229, 236, 210], [222, 230, 200], [214, 220, 190], [208, 210, 182], [202, 202, 176]], dtype=np.float32)
tint = np.stack([np.interp(up, stops, cols[:, i]) for i in range(3)], -1)
f = (0.62 + 0.52 * hs)[..., None]
rgb = np.clip(tint * f, 0, 255)
sea = up <= 0.6
rgb[sea] = [190, 220, 238]
img = Image.fromarray(rgb.astype(np.uint8))
import os
os.makedirs("data", exist_ok=True)
img.save("data/relief.jpg", quality=82, optimize=True, progressive=True)
print("relief", img.size, os.path.getsize("data/relief.jpg"))

# contours on 20 m grid (smoothed) clipped to corridor
c_dem = ndimage.gaussian_filter(ndimage.zoom(dem, ((res_lat * KY) / 20.0, (res_lon * KX) / 20.0), order=3), 1.0)
ch, cw = c_dem.shape
lons = np.linspace(W, E, cw); lats = np.linspace(N, S, ch)
levels = list(range(10, 560, 10))
fig = plt.figure(); cs = plt.contour(lons, lats, c_dem, levels=levels)
cont = []
labels = []
for lev, segs in zip(cs.levels, cs.allsegs):
    for seg in segs:
        if len(seg) < 3: continue
        g = LineString([to_xy(la, lo) for lo, la in seg])
        if not g.intersects(corr_s): continue
        g = g.intersection(corr).simplify(4)
        parts = [g] if isinstance(g, LineString) else list(getattr(g, "geoms", []))
        for p in parts:
            if not isinstance(p, LineString) or p.length < 60: continue
            cc = [[round(to_ll(x, y)[0], 5), round(to_ll(x, y)[1], 5)] for x, y in p.coords]
            cont.append([int(lev), cc])
            if int(lev) % 50 == 0 and p.length > 400:
                for d in np.arange(200, p.length, 900):
                    a = p.interpolate(d); b = p.interpolate(min(p.length, d + 20))
                    ang = math.degrees(math.atan2(-(b.y - a.y), b.x - a.x))
                    if ang > 90: ang -= 180
                    if ang < -90: ang += 180
                    la, lo = to_ll(a.x, a.y)
                    labels.append([round(la, 5), round(lo, 5), int(lev), round(ang)])
plt.close(fig)
out["contours"] = cont
out["clabels"] = labels
out["relief"] = {"src": "relief.jpg", "bounds": [[S, W], [N, E]]}
out["corr"] = [[round(to_ll(x, y)[0], 5), round(to_ll(x, y)[1], 5)] for x, y in corr.simplify(20).exterior.coords]
json.dump(out, open("data/map.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print({k: len(v) for k, v in out.items() if isinstance(v, list)}, os.path.getsize("data/map.json"))
