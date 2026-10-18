# -*- coding: utf-8 -*-
"""
IPTV BP V7.5 PRO - VIVO TV EDITION (Android TV)
1. Dodany kafelek "🚪 Zamknij" na głównym ekranie Vivo, w prawym panelu i w Menu Ustawień.
2. Twarde zamykanie procesu Android TV (os._exit(0)).
3. Automatyczny generator ikony systemowej APK (assets/icon.png) zastępujący logo Flet.
4. Płynne przewijanie (9 stałych slotów), blokada pojedynczego WSTECZ i 4 bazy opisów.
"""

import base64
import datetime
import json
import os
import re
import struct
import threading
import time
import traceback
from urllib.parse import parse_qs, urlparse
import uuid
import zlib

import flet as ft
import requests

TMDB_API_KEY = "6e2d327ee657c8671b3eda952cd260a0"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
}

PL_REGEX = re.compile(
    r"(?:^|[\s\|\[\(\-_:•/])(PL|POL|POLAND|POLSKA|POLSKIE|POLISH|LEKTOR|DUBBING|NAPISY[\s_]*PL|PL[\s_]*SUB|VIP[\s_]*PL)(?:$|[\s\|\]\)\-_:•/])"
    r"|[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]"
    r"|\b(tvp|polsat|tvn|canal\+|c\+|ale\s*kino|kino\s*polska|planete\+|domo\+|kuchnia\+|teletoon\+|minimini\+|ttv|puls|fokus\s*tv|eska|polo\s*tv|trwam|republika|wpolsce|wydarzenia\s*24|stopklatka|zoom\s*tv|wp\s*tv|nowa\s*tv|metro\s*tv|eleven\s*sports|polsat\s*sport|motowizja|stars\s*tv|kino\s*tv|gametoon|active\s*family|romance\s*tv|13\s*ulica|sci\s*fi|paramount|comedy\s*central|national\s*geographic|discovery|animal\s*planet|tlc|id\s*investigation|hbo|cinemax|filmbox|axn|fox|epic\s*drama|bbc\s*brit|bbc\s*earth|bbc\s*first|bbc\s*lifestyle|viasat|history|crime\s*investigation|sundance|red\s*carpet|antena\s*hd|tv\s*okazje|super\s*polsat|tv4|tv6|polsat\s*news|tvn24|tvp\s*info|biznes24|news24|pogoda24|4fun|vox\s*music|disco\s*polo|power\s*tv|nuta\s*tv|mixtape|hip\s*hop\s*tv)\b",
    re.IGNORECASE,
)


def auto_generate_app_icon_if_missing():
    """Tworzy plik assets/icon.png oraz assets/icon_android.png przed kompilacją APK."""
    try:
        os.makedirs("assets", exist_ok=True)
        icon_path = os.path.join("assets", "icon.png")
        if os.path.exists(icon_path):
            return
        w, h = 256, 256
        raw_rows = bytearray()
        for y in range(h):
            raw_rows.append(0)
            for x in range(w):
                border = x < 14 or x >= w - 14 or y < 14 or y >= h - 14
                if border:
                    raw_rows.extend((253, 224, 71, 255))  # Złota ramka (#fde047)
                else:
                    # Pomarańczowo-ciemnozielona tarcza IPTV BP
                    in_center = 48 <= x <= 208 and 64 <= y <= 192
                    if in_center:
                        raw_rows.extend((234, 88, 12, 255))  # Pomarańcz (#ea580c)
                    else:
                        raw_rows.extend((15, 41, 29, 255))  # Ciemna zieleń Vivo (#0f291d)

        def _chunk(c_type, data):
            return struct.pack(">I", len(data)) + c_type + data + struct.pack(">I", zlib.crc32(c_type + data) & 0xFFFFFFFF)

        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(raw_rows), 9))
            + _chunk(b"IEND", b"")
        )
        for fname in ("icon.png", "icon_android.png", "icon_ios.png", "icon_web.png"):
            with open(os.path.join("assets", fname), "wb") as f:
                f.write(png_bytes)
    except Exception:
        pass


auto_generate_app_icon_if_missing()


def is_polish_text(text: str) -> bool:
    return bool(text and PL_REGEX.search(str(text)))


def is_polish_item(item: dict, pl_cat_ids: set = None) -> bool:
    if not isinstance(item, dict):
        return False
    if pl_cat_ids and str(item.get("category_id", "")) in pl_cat_ids:
        return True
    for f in (item.get("name"), item.get("title"), item.get("category_name"), item.get("group")):
        if f and is_polish_text(f):
            return True
    return False


def parse_int_safe(val, default=0) -> int:
    if val is None:
        return default
    try:
        return int(float(str(val).strip()))
    except Exception:
        m = re.search(r"(\d+)$", str(val))
        return int(m.group(1)) if m else default


def decode_b64_safe(val: str) -> str:
    if not val:
        return ""
    try:
        return base64.b64decode(val).decode("utf-8", errors="ignore").strip()
    except Exception:
        return str(val).strip()


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def get_added_timestamp(item: dict) -> int:
    if not isinstance(item, dict):
        return 0
    return (
        parse_int_safe(item.get("added"), 0)
        or parse_int_safe(item.get("last_modified"), 0)
        or parse_int_safe(item.get("releaseDate"), 0)
    )


def get_numeric_stream_id(item: dict) -> int:
    if not isinstance(item, dict):
        return 0
    return (
        parse_int_safe(item.get("stream_id"), 0)
        or parse_int_safe(item.get("series_id"), 0)
        or parse_int_safe(item.get("id"), 0)
    )


def sort_categories_pl_first(categories: list) -> list:
    pl_c, oth_c = [], []
    for c in categories:
        (pl_c if is_polish_text(c.get("category_name", "")) else oth_c).append(c)
    return pl_c + oth_c


def sort_streams_smart(items: list, kind: str, force_newest_only: bool = False, pl_cat_ids: set = None) -> list:
    if not items:
        return []
    if kind == "live" and not force_newest_only:
        pl_l, oth_l = [], []
        for it in items:
            (pl_l if is_polish_item(it, pl_cat_ids) else oth_l).append(it)
        return pl_l + oth_l

    def _key(it):
        return (1 if is_polish_item(it, pl_cat_ids) else 0, get_added_timestamp(it), get_numeric_stream_id(it))

    return sorted(items, key=_key, reverse=True)


def make_session():
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s


def extract_xtream_from_m3u_url(m3u_url):
    try:
        parsed = urlparse(m3u_url.strip())
        if not parsed.scheme or not parsed.netloc:
            return None
        qs = parse_qs(parsed.query)
        u = (qs.get("username") or qs.get("user") or [None])[0]
        p = (qs.get("password") or qs.get("pass") or [None])[0]
        if u and p:
            return f"{parsed.scheme}://{parsed.netloc}", u, p
    except Exception:
        pass
    return None


def clean_media_title(title):
    if not title:
        return ""
    c = re.sub(r"^(\|?[A-Za-z0-9]{2,5}\|?\s*[:|\-]+\s*)+", "", str(title))
    c = re.sub(r"\[.*?\]", "", c)
    c = re.sub(r"\s*[Ss]\d{1,2}\s*[Ee]\d{1,3}.*$", "", c)
    c = re.sub(r"\s*\(\d{4}\).*$", "", c)
    c = re.sub(r"\s*-\s*\d{4}\s*$", "", c)
    c = re.sub(
        r"\b(FHD|UHD|4K|HD|1080p|720p|HEVC|H265|LEKTOR|DUBBING|NAPISY|PL|EN|FR|CAM|WEB-DL|BLURAY)\b.*$",
        "",
        c,
        flags=re.IGNORECASE,
    )
    return c.strip(" -|_:")


def get_item_unique_id(item):
    if not isinstance(item, dict):
        return ""
    return str(item.get("stream_id") or item.get("series_id") or item.get("id") or item.get("category_id") or "")


metadata_memory_cache = {}
epg_memory_cache = {}


def fetch_rich_media_details(title: str, item: dict = None, client=None):
    clean_t = clean_media_title(title)
    cache_key = f"{clean_t.lower()}_{get_item_unique_id(item)}"
    if cache_key in metadata_memory_cache:
        return metadata_memory_cache[cache_key]

    poster, plot, rating, year, src = None, "", "", "", ""

    if isinstance(item, dict):
        poster = item.get("stream_icon") or item.get("cover") or None
        plot = (item.get("plot") or item.get("description") or item.get("overview") or "").strip()
        rating = str(item.get("rating") or "").strip()
        year = str(item.get("year") or item.get("releaseDate") or "")[:4].strip()
        if plot:
            src = "Serwer VOD"

    if not plot and client and isinstance(item, dict) and hasattr(client, "get_vod_metadata"):
        try:
            k = item.get("kind", "")
            if k == "vod" and item.get("stream_id"):
                m = client.get_vod_metadata(item.get("stream_id"))
                if m:
                    poster = m.get("poster") or poster
                    plot = m.get("plot") or plot
                    rating = m.get("rating") or rating
                    year = m.get("year") or year
                    if plot:
                        src = "Xtream VOD"
            elif k == "series" and item.get("series_id"):
                m = client.get_series_metadata(item.get("series_id"))
                if m:
                    poster = m.get("poster") or poster
                    plot = m.get("plot") or plot
                    rating = m.get("rating") or rating
                    year = m.get("year") or year
                    if plot:
                        src = "Xtream Series"
        except Exception:
            pass

    if clean_t and (not plot or not poster):
        try:
            u_pl = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={requests.utils.quote(clean_t)}&language=pl-PL"
            r = requests.get(u_pl, timeout=3.5)
            if r.status_code == 200 and r.json().get("results"):
                b = r.json()["results"][0]
                if b.get("poster_path") and not poster:
                    poster = f"https://image.tmdb.org/t/p/w500{b['poster_path']}"
                rel = b.get("release_date") or b.get("first_air_date") or ""
                if rel and not year:
                    year = rel[:4]
                if b.get("vote_average") and not rating:
                    rating = str(round(b.get("vote_average", 0), 1))
                if b.get("overview") and not plot:
                    plot, src = b["overview"].strip(), "TMDB PL"
        except Exception:
            pass

    if clean_t and not plot:
        try:
            w_url = f"https://pl.wikipedia.org/w/api.php?action=query&list=search&srsearch={requests.utils.quote(clean_t + ' film')}&utf8=&format=json&srlimit=2"
            rw = requests.get(w_url, headers=DEFAULT_HEADERS, timeout=3.0)
            if rw.status_code == 200 and rw.json().get("query", {}).get("search"):
                pg_t = rw.json()["query"]["search"][0].get("title", "")
                rs = requests.get(
                    f"https://pl.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(pg_t)}",
                    headers=DEFAULT_HEADERS,
                    timeout=3.0,
                )
                if rs.status_code == 200:
                    sd = rs.json()
                    ext = (sd.get("extract") or "").strip()
                    if len(ext) > 35:
                        plot, src = ext, "Wikipedia PL"
                        th = (sd.get("thumbnail") or {}).get("source")
                        if th and not poster:
                            poster = th
        except Exception:
            pass

    if clean_t and not plot:
        try:
            rt = requests.get(f"https://api.tvmaze.com/singlesearch/shows?q={requests.utils.quote(clean_t)}", timeout=3.0)
            if rt.status_code == 200:
                td = rt.json()
                sm = strip_html(td.get("summary") or "")
                if sm:
                    plot, src = sm, "TVMaze"
                im = (td.get("image") or {}).get("medium")
                if im and not poster:
                    poster = im
                pr = td.get("premiered") or ""
                if pr and not year:
                    year = pr[:4]
                rv = (td.get("rating") or {}).get("average")
                if rv and not rating:
                    rating = str(rv)
        except Exception:
            pass

    if clean_t and not plot:
        try:
            re_en = requests.get(
                f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={requests.utils.quote(clean_t)}&language=en-US",
                timeout=3.0,
            )
            if re_en.status_code == 200 and re_en.json().get("results") and re_en.json()["results"][0].get("overview"):
                plot, src = re_en.json()["results"][0]["overview"].strip(), "TMDB EN"
        except Exception:
            pass

    res = {"poster": poster, "plot": plot, "rating": rating, "year": year, "source": src}
    metadata_memory_cache[cache_key] = res
    return res


class XtreamClient:
    def __init__(self, server, username, password):
        self.server, self.username, self.password = server.rstrip("/"), username, password
        self.session, self.user_info = make_session(), {}
        self.pl_cat_ids = {"live": set(), "vod": set(), "series": set()}

    def _api(self, params, timeout=15):
        fp = {"username": self.username, "password": self.password}
        fp.update(params)
        r = self.session.get(f"{self.server}/player_api.php", params=fp, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def login(self):
        last_err = None
        for _ in range(3):
            try:
                self.user_info = self._api({}, timeout=10).get("user_info", {})
                if self.user_info.get("auth") != 1:
                    raise ValueError("Nieprawidłowy login lub hasło.")
                return self.user_info
            except Exception as e:
                last_err = e
                time.sleep(0.4)
        raise last_err

    def get_categories(self, kind):
        act = {"live": "get_live_categories", "vod": "get_vod_categories", "series": "get_series_categories"}[kind]
        raw = self._api({"action": act}) or []
        self.pl_cat_ids[kind] = {str(c.get("category_id", "")) for c in raw if is_polish_text(c.get("category_name", ""))}
        return sort_categories_pl_first(raw)

    def get_streams(self, kind, category_id=None):
        p = {"action": {"live": "get_live_streams", "vod": "get_vod_streams", "series": "get_series"}[kind]}
        if category_id and category_id not in ("__ALL__", "__RECENT__", "__CATCHUP__"):
            p["category_id"] = category_id
        raw = self._api(p, timeout=24) or []
        if category_id == "__CATCHUP__":
            raw = [s for s in raw if parse_int_safe(s.get("tv_archive"), 0) == 1]
        return sort_streams_smart(raw, kind, force_newest_only=(category_id == "__RECENT__"), pl_cat_ids=self.pl_cat_ids.get(kind, set()))

    def get_series_info(self, series_id):
        return self._api({"action": "get_series_info", "series_id": series_id})

    def get_vod_metadata(self, vod_id):
        try:
            i = self._api({"action": "get_vod_info", "vod_id": vod_id}, timeout=3.5).get("info", {})
            return (
                {
                    "plot": (i.get("plot") or i.get("description") or "").strip(),
                    "poster": i.get("movie_image") or i.get("cover_big"),
                    "rating": str(i.get("rating") or "").strip(),
                    "year": str(i.get("releasedate") or i.get("year") or "")[:4].strip(),
                }
                if isinstance(i, dict)
                else None
            )
        except Exception:
            return None

    def get_series_metadata(self, series_id):
        try:
            i = self._api({"action": "get_series_info", "series_id": series_id}, timeout=3.5).get("info", {})
            return (
                {
                    "plot": (i.get("plot") or i.get("description") or "").strip(),
                    "poster": i.get("cover") or i.get("cover_big"),
                    "rating": str(i.get("rating") or "").strip(),
                    "year": str(i.get("releaseDate") or "")[:4].strip(),
                }
                if isinstance(i, dict)
                else None
            )
        except Exception:
            return None

    def get_short_epg(self, stream_id):
        if not stream_id:
            return []
        now_ts = time.time()
        c = epg_memory_cache.get(str(stream_id))
        if c and (now_ts - c[0] < 120):
            return c[1]
        try:
            parsed = []
            listings = self._api({"action": "get_short_epg", "stream_id": stream_id, "limit": 3}, timeout=4).get("epg_listings", [])
            for el in listings[:3]:
                t = decode_b64_safe(el.get("title", ""))
                desc = decode_b64_safe(el.get("description", ""))
                st, et = (el.get("start") or "")[11:16], (el.get("end") or "")[11:16]
                if t:
                    parsed.append((f"🕒 {st} - {et}  •  {t}\n{desc}" if desc else f"🕒 {st} - {et}  •  {t}").strip())
            epg_memory_cache[str(stream_id)] = (now_ts, parsed)
            return parsed
        except Exception:
            return []

    def build_stream_url(self, kind, stream_id, container_ext="ts", item=None):
        if item and item.get("direct_url"):
            return item["direct_url"]
        prefix = "live" if kind == "live" else ("movie" if kind == "vod" else "series")
        return f"{self.server}/{prefix}/{self.username}/{self.password}/{stream_id}.{container_ext}"


class M3UClient:
    def __init__(self, source):
        self.source, self.server, self.username = source.strip(), "Lista M3U", "M3U Playlist"
        self.user_info, self.session = {"auth": 1, "exp_date": None}, make_session()
        self.categories = {"live": [], "vod": [], "series": []}
        self.streams_by_cat = {"live": {}, "vod": {}, "series": {}}
        self.pl_cat_ids = {"live": set(), "vod": set(), "series": set()}
        self.series_map, self.direct_urls = {}, {}

    def login(self):
        self.categories = {"live": [], "vod": [], "series": []}
        self.streams_by_cat = {"live": {}, "vod": {}, "series": {}}
        self.pl_cat_ids = {"live": set(), "vod": set(), "series": set()}
        self.series_map, self.direct_urls = {}, {}
        r = self.session.get(self.source, timeout=25)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        self._parse_m3u(r.text)
        return self.user_info

    def _classify_entry(self, url, group, name):
        lu, lg, ep = url.lower(), group.lower(), re.search(r"[Ss](\d{1,2})\s*[Ee](\d{1,3})", name)
        if "/series/" in lu or ep or any(k in lg for k in ["seriale", "series", "serial"]):
            return "series", ep
        if "/movie/" in lu or lu.endswith((".mp4", ".mkv", ".avi")) or any(k in lg for k in ["vod", "filmy", "movies", "film", "kino"]):
            return "vod", None
        return "live", None

    def _parse_m3u(self, text):
        cat_sets = {"live": {}, "vod": {}, "series": {}}
        series_groups, attr_re, extinf, seq = {}, re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"'), None, 1
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                if line.startswith("#EXTINF"):
                    extinf = line
                continue
            attrs = dict(attr_re.findall(extinf)) if extinf else {}
            name = extinf.split(",", 1)[1].strip() if (extinf and "," in extinf) else f"Kanał {seq}"
            group = (attrs.get("group-title") or "Bez kategorii").strip()
            logo = attrs.get("tvg-logo") or ""
            catchup = 1 if (attrs.get("catchup") or attrs.get("tvg-rec")) else 0
            kind, ep_match = self._classify_entry(line, group, name)

            if group not in cat_sets[kind]:
                cid = f"m3u_{kind}_{len(cat_sets[kind]) + 1}"
                cat_sets[kind][group] = cid
                self.categories[kind].append({"category_id": cid, "category_name": group})
                if is_polish_text(group):
                    self.pl_cat_ids[kind].add(cid)
                self.streams_by_cat[kind][cid] = []
            else:
                cid = cat_sets[kind][group]

            iid = f"m3u_item_{seq}"
            self.direct_urls[iid] = line
            if kind in ("live", "vod"):
                self.streams_by_cat[kind][cid].append(
                    {
                        "stream_id": iid,
                        "category_id": cid,
                        "added": str(seq),
                        "name": name,
                        "group": group,
                        "stream_icon": logo,
                        "direct_url": line,
                        "tv_archive": catchup,
                        "kind": kind,
                    }
                )
            else:
                s_num = str(int(ep_match.group(1))) if ep_match else "1"
                e_num = int(ep_match.group(2)) if ep_match else 1
                base_t = name[: ep_match.start()].strip(" -|:") if ep_match else name
                s_key = (cid, base_t.lower())
                if s_key not in series_groups:
                    sid = f"m3u_ser_{seq}"
                    series_groups[s_key] = sid
                    self.streams_by_cat["series"][cid].append(
                        {
                            "series_id": sid,
                            "category_id": cid,
                            "last_modified": str(seq),
                            "name": base_t,
                            "group": group,
                            "cover": logo,
                            "kind": "series",
                        }
                    )
                    self.series_map[sid] = {"episodes": {}}
                self.series_map[series_groups[s_key]]["episodes"].setdefault(s_num, []).append(
                    {"id": iid, "episode_num": e_num, "title": name, "direct_url": line}
                )
            seq += 1
            extinf = None
        for k in ("live", "vod", "series"):
            self.categories[k] = sort_categories_pl_first(self.categories[k])

    def get_categories(self, kind):
        return self.categories.get(kind, [])

    def get_streams(self, kind, category_id=None):
        cmap, pl_ids = self.streams_by_cat.get(kind, {}), self.pl_cat_ids.get(kind, set())
        if not category_id or category_id in ("__ALL__", "__RECENT__", "__CATCHUP__"):
            res = [s for v in cmap.values() for s in v]
            if category_id == "__CATCHUP__":
                res = [s for s in res if parse_int_safe(s.get("tv_archive"), 0) == 1]
            return sort_streams_smart(res, kind, force_newest_only=(category_id == "__RECENT__"), pl_cat_ids=pl_ids)
        return sort_streams_smart(cmap.get(category_id, []), kind, pl_cat_ids=pl_ids)

    def get_series_info(self, series_id):
        return self.series_map.get(series_id, {"episodes": {}})

    def get_short_epg(self, stream_id):
        return []

    def build_stream_url(self, kind, stream_id, container_ext="ts", item=None):
        return item["direct_url"] if item and item.get("direct_url") else self.direct_urls.get(stream_id, "")


def build_special_top_categories(kind: str, raw_cats: list) -> list:
    top = [{"category_id": "__ALL__", "category_name": "🔥 WSZYSTKIE (🇵🇱 Polskie na górze)"}]
    if kind == "live":
        top.append({"category_id": "__CATCHUP__", "category_name": "⏱ KANAŁY Z ARCHIWUM (CATCH UP)"})
    if kind in ("vod", "series"):
        top.append({"category_id": "__RECENT__", "category_name": "🆕 OSTATNIO DODANE (NOWOŚCI NA GÓRZE)"})
    return top + raw_cats


def make_iptv_bp_badge(size_px=42):
    return ft.Container(
        content=ft.Column(
            [
                ft.Text("IPTV", size=max(10, int(size_px * 0.28)), weight=ft.FontWeight.BOLD, color="white", height=max(12, int(size_px * 0.32))),
                ft.Text("BP", size=max(11, int(size_px * 0.34)), weight=ft.FontWeight.BOLD, color="#fde047", height=max(13, int(size_px * 0.38))),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
        ),
        width=size_px * 1.35,
        height=size_px,
        bgcolor="#ea580c",
        border_radius=10,
        border=ft.border.all(2, "#fed7aa"),
        alignment=ft.alignment.center,
        gradient=ft.LinearGradient(begin=ft.alignment.top_left, end=ft.alignment.bottom_right, colors=["#f97316", "#c2410c"]),
    )


def main(page: ft.Page):
    try:
        page.title, page.theme_mode, page.bgcolor, page.padding = "IPTV BP", ft.ThemeMode.DARK, "#0b1f15", 0
        stop_event = threading.Event()
        app_state = {
            "dialog_open": False,
            "settings_open": False,
            "paused_in_bg": False,
            "preview_seq": 0,
            "last_back_press_ts": 0.0,
            "last_back_guard": 0.0,
            "last_ok_ts": 0.0,
            "storage_ready": False,
        }
        storage_lock = threading.Lock()
        storage_cache = {
            "device_id": "android-tv",
            "device_name": "Android TV Box",
            "profiles": [],
            "processed_ids": [],
            "auto_vlc": True,
            "auto_login": True,
            "only_pl": False,
            "vlc_mode": "vlc_clean",
            "favorites_v2": [],
            "server": "http://",
            "username": "",
            "password": "",
            "m3u_url": "",
            "active_profile_name": "",
        }
        pending_writes, write_timer_active = {}, {"running": False}

        def cache_get(key, default=None):
            val = storage_cache.get(key)
            return val if val is not None else default

        def cache_set(key, val):
            storage_cache[key] = val
            with storage_lock:
                pending_writes[key] = val
                if write_timer_active["running"]:
                    return
                write_timer_active["running"] = True

            def _flush():
                time.sleep(0.3)
                if stop_event.is_set():
                    return
                with storage_lock:
                    items = list(pending_writes.items())
                    pending_writes.clear()
                    write_timer_active["running"] = False
                for k_s, v_s in items:
                    if stop_event.is_set():
                        break
                    try:
                        page.client_storage.set(k_s, v_s)
                    except Exception:
                        pass

            threading.Thread(target=_flush, daemon=True).start()

        def safe_ui_update():
            if not stop_event.is_set():
                try:
                    page.update()
                except Exception:
                    pass

        def close_app_cleanly(_e=None):
            """Natychmiastowe zamknięcie aplikacji na Android TV i Desktopie."""
            stop_event.set()
            for fn in ("window_destroy", "window_close"):
                try:
                    getattr(page, fn)()
                except Exception:
                    pass
            try:
                page.window.close()
            except Exception:
                pass
            try:
                os._exit(0)
            except Exception:
                pass

        def on_lifecycle_change(e: ft.AppLifecycleStateChangeEvent):
            st = str(e.data).lower()
            if "detach" in st:
                stop_event.set()
            elif any(x in st for x in ("pause", "inactive", "hidden")):
                app_state["paused_in_bg"] = True
            elif "resume" in st:
                app_state["paused_in_bg"] = False
                safe_ui_update()

        page.on_app_lifecycle_state_change = on_lifecycle_change
        page.on_disconnect = lambda _: stop_event.set()
        page.on_close = lambda _: stop_event.set()

        device_id, device_name = storage_cache["device_id"], storage_cache["device_name"]
        profiles, processed_ids, favorites_items = [], set(), []

        VLC_MODE_ORDER = ["vlc_clean", "intent", "vlc_full", "http"]
        VLC_MODE_LABELS = {
            "vlc_clean": "🔧 Tryb VLC: vlc:// (Czysty)",
            "intent": "🔧 Tryb VLC: Android Intent",
            "vlc_full": "🔧 Tryb VLC: vlc://http://",
            "http": "🔧 Tryb VLC: Standard HTTP",
        }
        creds = {"server": "http://", "username": "", "password": "", "m3u_url": ""}

        def has_saved_credentials():
            return bool(
                creds["m3u_url"].strip()
                or (
                    creds["server"].strip()
                    and creds["server"].strip() not in ("http://", "https://")
                    and creds["username"].strip()
                    and creds["password"]
                )
            )

        def register_or_update_profile(name_hint="", srv="", usr="", pas="", m3u=""):
            nonlocal profiles
            srv_c, usr_c, m3u_c = (srv or "").strip(), (usr or "").strip(), (m3u or "").strip()
            mode = "m3u" if (m3u_c and not usr_c) else "xtream"
            if (mode == "xtream" and (not srv_c or srv_c in ("http://", "https://") or not usr_c)) or (mode == "m3u" and not m3u_c):
                return ""

            ex_idx = next(
                (
                    i
                    for i, p in enumerate(profiles)
                    if (mode == "xtream" and p.get("server") == srv_c and p.get("username") == usr_c)
                    or (mode == "m3u" and p.get("m3u_url") == m3u_c)
                ),
                -1,
            )
            if ex_idx >= 0:
                p_name = name_hint or profiles[ex_idx].get("name") or f"Lista {ex_idx + 1}"
                profiles[ex_idx].update({"name": p_name, "password": pas, "mode": mode})
            else:
                p_name = name_hint or (
                    f"{usr_c} @ {srv_c.replace('http://', '').replace('https://', '')[:22]}"
                    if mode == "xtream"
                    else f"Lista M3U #{len(profiles) + 1}"
                )
                profiles = [p for p in profiles if p.get("name") != p_name]
                profiles.insert(
                    0,
                    {
                        "name": p_name,
                        "mode": mode,
                        "server": srv_c,
                        "username": usr_c,
                        "password": pas,
                        "m3u_url": m3u_c,
                        "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    },
                )
            cache_set("profiles", profiles)
            cache_set("active_profile_name", p_name)
            return p_name

        VISIBLE_ROWS = 9
        state = {
            "client": None,
            "kind": "live",
            "in_fav": False,
            "in_profiles": False,
            "in_dashboard": True,
            "view_mode": "dashboard",
            "active_cat": None,
            "active_series_id": "",
            "active_series_title": "",
            "active_profile_name": "",
            "exp_date_str": "Unlimited",
            "categories": {"live": [], "vod": [], "series": []},
            "categories_cache": {"live": None, "vod": None, "series": None},
            "streams_cache": {},
            "streams": [],
            "episodes": [],
            "filtered": [],
            "all_built_items": [],
            "viewport_top": 0,
            "search_query": "",
            "only_pl": False,
            "now_playing": "Brak aktywnego strumienia",
            "playing_url": "",
            "current_item": None,
            "is_playing": False,
            "paused": False,
            "volume": 100,
            "muted": False,
            "auto_vlc": True,
            "auto_login": True,
            "is_logging_in": False,
            "vlc_mode": "vlc_clean",
            "zone": "login",
            "dash_idx": 0,
            "list_idx": 0,
            "right_idx": 0,
            "settings_idx": 0,
            "login_idx": 5,
        }

        def publish_status():
            if stop_event.is_set() or app_state["paused_in_bg"] or not app_state["storage_ready"]:
                return

            def _worker():
                try:
                    cid = re.sub(r"[^a-zA-Z0-9_-]", "", cache_get("device_id", device_id))
                    snap = {
                        "type": "player_status",
                        "device_name": cache_get("device_name", device_name),
                        "device_id": cid,
                        "ts": int(time.time() * 1000),
                        "time_str": datetime.datetime.now().strftime("%H:%M:%S"),
                        "state_label": ("⏸ PAUZA" if state["paused"] else "▶ ODTWARZANIE") if state["is_playing"] else "⏹ ZATRZYMANO",
                        "now_playing": state["now_playing"],
                        "active_server": state["active_profile_name"] if state["client"] else "Ekran logowania",
                        "only_pl": state["only_pl"],
                        "current_tab": "menu"
                        if state["in_dashboard"]
                        else ("listy" if state["in_profiles"] else ("ulubione" if state["in_fav"] else state["kind"])),
                        "volume": state["volume"],
                        "muted": state["muted"],
                        "profiles": [
                            {
                                "name": p.get("name", "Lista"),
                                "mode": p.get("mode", "xtream"),
                                "host": (p.get("server") or p.get("m3u_url") or "")[:30],
                                "added_at": p.get("added_at", ""),
                                "active": state["client"] is not None and p.get("name") == state["active_profile_name"],
                            }
                            for p in profiles
                        ],
                    }
                    make_session().post(
                        f"https://ntfy.sh/iptv_v4_pro_{cid}_status",
                        data=json.dumps(snap, ensure_ascii=False).encode("utf-8"),
                        timeout=4,
                    )
                except Exception:
                    pass

            threading.Thread(target=_worker, daemon=True).start()

        def get_current_pl_cat_ids() -> set:
            cl = state.get("client")
            return cl.pl_cat_ids.get(state["kind"], set()) if (cl and hasattr(cl, "pl_cat_ids")) else set()

        def is_favorite(item):
            uid = get_item_unique_id(item)
            return bool(uid and any(get_item_unique_id(f) == uid for f in favorites_items))

        def make_tv_button(text_str, on_click_fn, key_str=None, height=44, expand=False, item_ref=None):
            txt_ctrl = ft.Text(text_str, size=14, weight=ft.FontWeight.BOLD, color="white", no_wrap=True, expand=True)
            box = ft.Container(
                key=key_str,
                content=ft.Row([txt_ctrl], alignment=ft.MainAxisAlignment.START),
                height=height,
                padding=ft.padding.symmetric(horizontal=12, vertical=4),
                border_radius=8,
                bgcolor="#163828",
                border=ft.border.all(1, "#22543d"),
                alignment=ft.alignment.center_left,
                expand=expand,
                on_click=lambda _: on_click_fn(),
            )
            box.data = {"text_ctrl": txt_ctrl, "base_text": text_str, "action": on_click_fn, "item": item_ref}
            return box

        def set_tv_button_text(box, new_text):
            box.data["base_text"], box.data["text_ctrl"].value = new_text, new_text

        clock_txt = ft.Text("00:00", size=28, weight=ft.FontWeight.BOLD, color="white")
        date_txt = ft.Text("", size=12, color="#86efac")
        footer_playlist_txt = ft.Text("Current Playlist: Brak", size=12, color="#86efac")
        footer_expire_txt = ft.Text(f"Expire: Unlimited  •  ID: {device_id}", size=12, color="#86efac")
        dash_back_hint_txt = ft.Text(
            "Pilot: Strzałki = wybór kafelka | OK = wejdź | Kafelek '🚪 Zamknij' lub 2x WSTECZ = wyjście",
            size=12,
            color="#fbbf24",
            weight=ft.FontWeight.BOLD,
        )

        def update_vivo_footer():
            footer_playlist_txt.value = f"Current Playlist: {state.get('active_profile_name') or 'Domyślna'}"
            footer_expire_txt.value = f"Playlist Expire Date: {state.get('exp_date_str') or 'Unlimited'}  •  Cloud ID: {cache_get('device_id', device_id)}"

        def update_clock_loop():
            while not stop_event.is_set():
                try:
                    now = datetime.datetime.now()
                    clock_txt.value, date_txt.value = now.strftime("%H:%M"), now.strftime("%A, %d %b %Y")
                    safe_ui_update()
                except Exception:
                    pass
                if stop_event.wait(10):
                    break

        threading.Thread(target=update_clock_loop, daemon=True).start()

        poster_img = ft.Image(src="", width=180, height=255, fit=ft.ImageFit.COVER, border_radius=10, visible=False)
        info_badge_txt = ft.Text("PODGLĄD POZYCJI", size=11, weight=ft.FontWeight.BOLD, color="#fbbf24")
        status_info_title = ft.Text("Najedź kursorem na kanał, film lub serial", size=19, weight=ft.FontWeight.BOLD, color="#34d399")
        status_info_meta = ft.Text("", size=13, weight=ft.FontWeight.BOLD, color="#fbbf24")
        status_info_sub = ft.Text(
            "Wystarczy podświetlić pozycję na liście po lewej stronie, aby wyświetlić EPG, ocenę oraz opis (Xtream VOD / TMDB / Wikipedia PL / TVMaze).",
            size=14,
            color="#e2e8f0",
            max_lines=9,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        info_box_content = ft.Row(
            [
                poster_img,
                ft.Column(
                    [
                        ft.Container(content=info_badge_txt, bgcolor="#163828", padding=ft.padding.symmetric(horizontal=8, vertical=3), border_radius=6),
                        status_info_title,
                        status_info_meta,
                        ft.Divider(height=8, color="#22543d"),
                        status_info_sub,
                    ],
                    alignment=ft.MainAxisAlignment.START,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                    spacing=6,
                    expand=True,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.START,
            spacing=16,
        )

        settings_panel_col = ft.Column([], spacing=6, expand=True)
        settings_overlay_box = ft.Container(
            content=settings_panel_col,
            expand=True,
            bgcolor="#091811",
            border_radius=12,
            border=ft.border.all(2, "#ea580c"),
            padding=14,
            visible=False,
        )
        video_placeholder = ft.Container(
            content=info_box_content,
            expand=True,
            bgcolor="#091811",
            border_radius=12,
            border=ft.border.all(2, "#22543d"),
            padding=16,
            visible=True,
        )

        now_playing_txt = ft.Text(
            "▶ Pilot: ⬆️ ⬇️ przewijaj przez całe okno | OK = Uruchom | ➡️ Prawy panel | WSTECZ = Cofnij",
            weight=ft.FontWeight.BOLD,
            size=13,
            color="#fbbf24",
        )
        header_dev_lbl = ft.Text(f"📺 {device_name} [{device_id}]", size=12, color="#34d399", weight=ft.FontWeight.BOLD)
        list_header_lbl = ft.Text("📂 LISTA KATEGORII", size=13, weight=ft.FontWeight.BOLD, color="#34d399")
        status_banner = ft.Text("⏳ Inicjalizacja systemu IPTV BP...", color="#34d399", size=13)

        def trigger_focus_preview(item):
            if not isinstance(item, dict):
                return
            if "category_name" in item and "stream_id" not in item and "series_id" not in item:
                poster_img.visible = False
                info_badge_txt.value = "📁 KATEGORIA"
                status_info_title.value = item.get("category_name", "Kategoria")
                status_info_meta.value = f"Sekcja: {state['kind'].upper()}  •  Naciśnij OK, aby otworzyć"
                status_info_sub.value = (
                    "Przewijaj listę strzałkami ⬆️ ⬇️ od samej góry do samego dołu okna.\n"
                    "Po wejściu w kategorię najedź na kanał lub film, aby pobrać opis z 4 baz danych (Xtream, TMDB, Wikipedia PL, TVMaze)."
                )
                return

            state["current_item"] = item
            title = item.get("display_title") or item.get("name") or item.get("title") or "Pozycja"
            icon_url = item.get("stream_icon") or item.get("cover") or ""
            k = item.get("kind") or state["kind"]
            fav_str = "⭐ W ulubionych  •  " if is_favorite(item) else ""
            pl_str = "🇵🇱 PL  •  " if is_polish_item(item, get_current_pl_cat_ids()) else ""

            info_badge_txt.value = "📡 KANAŁ TV NA ŻYWO" if k == "live" else ("🎬 FILM VOD" if k == "vod" else "🎞 SERIAL")
            status_info_title.value, status_info_meta.value = title, f"{fav_str}{pl_str}Naciśnij OK, aby odtworzyć"
            poster_img.src, poster_img.visible = icon_url, bool(icon_url and icon_url.startswith("http"))
            status_info_sub.value = "⏳ Pobieranie informacji o programie / opisu fabuły..."
            app_state["preview_seq"] += 1
            cur_seq = app_state["preview_seq"]

            def _async_preview():
                time.sleep(0.16)
                if cur_seq != app_state["preview_seq"] or stop_event.is_set():
                    return
                if k == "live" and state.get("client"):
                    epg_list = state["client"].get_short_epg(item.get("stream_id"))
                    if cur_seq != app_state["preview_seq"]:
                        return
                    status_info_meta.value = f"{fav_str}{pl_str}{'⏱ Archiwum Catch Up' if parse_int_safe(item.get('tv_archive'), 0) == 1 else '📡 Live TV'}"
                    status_info_sub.value = (
                        "\n\n".join(epg_list)
                        if epg_list
                        else "Brak aktualnych danych EPG dla tego kanału.\nNaciśnij OK, aby uruchomić transmisję."
                    )
                    safe_ui_update()
                elif k in ("vod", "series"):
                    meta = fetch_rich_media_details(title, item=item, client=state.get("client"))
                    if cur_seq != app_state["preview_seq"]:
                        return
                    if meta:
                        if meta.get("poster"):
                            poster_img.src, poster_img.visible = meta["poster"], True
                        yr = f"📅 Rok: {meta['year']}   " if meta.get("year") else ""
                        rt = f"⭐ Ocena: {meta['rating']}   " if meta.get("rating") else ""
                        src_b = f"📚 {meta['source']}" if meta.get("source") else ""
                        status_info_meta.value = f"{fav_str}{pl_str}{yr}{rt}{src_b}".strip()
                        status_info_sub.value = meta.get("plot") or "Brak opisu fabuły w bazach Xtream, TMDB, Wikipedia PL oraz TVMaze."
                    else:
                        status_info_sub.value = "Naciśnij OK na pilocie, aby rozpocząć oglądanie."
                    safe_ui_update()

            threading.Thread(target=_async_preview, daemon=True).start()

        def toggle_only_pl(_e=None):
            state["only_pl"] = not state["only_pl"]
            cache_set("only_pl", state["only_pl"])
            state.update({"categories_cache": {"live": None, "vod": None, "series": None}, "list_idx": 0, "viewport_top": 0})
            now_playing_txt.value = (
                "🇵🇱 Filtr 'Tylko Polskie' WŁĄCZONY"
                if state["only_pl"]
                else "🌍 Filtr 'Tylko Polskie' WYŁĄCZONY (Polskie na górze)"
            )
            build_full_list_data() if not state["in_dashboard"] else safe_ui_update()
            publish_status()

        def cycle_vlc_mode(_e=None):
            cur = state.get("vlc_mode", "vlc_clean")
            idx = (VLC_MODE_ORDER.index(cur) + 1) % len(VLC_MODE_ORDER) if cur in VLC_MODE_ORDER else 0
            new_m = VLC_MODE_ORDER[idx]
            state["vlc_mode"] = new_m
            cache_set("vlc_mode", new_m)
            now_playing_txt.value = f"🔧 {VLC_MODE_LABELS[new_m]}"
            safe_ui_update()

        def toggle_auto_vlc(_e=None):
            state["auto_vlc"] = not state["auto_vlc"]
            cache_set("auto_vlc", state["auto_vlc"])
            now_playing_txt.value = f"🚀 Auto-start VLC po kliknięciu OK: {'WŁ.' if state['auto_vlc'] else 'WYŁ.'}"
            safe_ui_update()

        def toggle_favorite(_e=None):
            nonlocal favorites_items
            cur_item = state.get("current_item")
            if not cur_item:
                now_playing_txt.value = "⚠️ Najpierw najedź na kanał lub film na liście."
                safe_ui_update()
                return
            uid = get_item_unique_id(cur_item)
            if not uid:
                return
            nm = cur_item.get("name") or cur_item.get("title") or "Pozycja"
            if is_favorite(cur_item):
                favorites_items = [f for f in favorites_items if get_item_unique_id(f) != uid]
                now_playing_txt.value = f"⭐ Usunięto z ulubionych: {nm}"
            else:
                cp = dict(cur_item)
                cp["kind"] = cur_item.get("kind") or state["kind"]
                favorites_items.insert(0, cp)
                now_playing_txt.value = f"⭐ Dodano do ulubionych: {nm}"
            cache_set("favorites_v2", favorites_items)
            build_full_list_data(keep_idx=True)

        def stop_playback(_e=None):
            state.update({"is_playing": False, "paused": False, "now_playing": "Brak aktywnego strumienia"})
            now_playing_txt.value = "⏹ Zatrzymano odtwarzanie"
            safe_ui_update()
            publish_status()

        def toggle_play_pause(_e=None):
            state["paused"] = not state["paused"]
            now_playing_txt.value = "⏸ PAUZA" if state["paused"] else f"▶ {state['now_playing']}"
            safe_ui_update()
            publish_status()

        def launch_vlc_intent(raw_url: str, title: str = ""):
            if not raw_url:
                return
            cu, mode = raw_url.strip().splitlines()[0].strip(), state.get("vlc_mode", "vlc_clean")
            no_p = re.sub(r"^https?://", "", cu, flags=re.IGNORECASE)
            sch = "https" if cu.lower().startswith("https://") else "http"
            t_url = (
                f"vlc://{no_p}"
                if mode == "vlc_clean"
                else (
                    f"intent://{no_p}#Intent;scheme={sch};type=video/*;package=org.videolan.vlc;end"
                    if mode == "intent"
                    else (f"vlc://{cu}" if mode == "vlc_full" else cu)
                )
            )
            now_playing_txt.value = f"🚀 Uruchamianie VLC: {title or state['now_playing']}"
            safe_ui_update()
            threading.Thread(target=lambda: page.launch_url(t_url), daemon=True).start()

        def play_stream_url(url, title, item=None):
            state.update({"playing_url": url, "now_playing": title, "current_item": item, "is_playing": True, "paused": False})
            now_playing_txt.value = f"▶ Odtwarzanie: {title}"
            if state["auto_vlc"]:
                launch_vlc_intent(url, title)
            else:
                safe_ui_update()
            publish_status()

        def make_vivo_tile(title, icon_char, on_click_fn, is_large=False, is_exit=False):
            t_ctrl = ft.Text(title, size=16 if is_large else 13, weight=ft.FontWeight.BOLD, color="white", text_align=ft.TextAlign.CENTER)
            i_ctrl = ft.Text(
                icon_char,
                size=34 if is_large else 20,
                color="#ef4444" if is_exit else ("#fb923c" if title in ("Live TV", "Ustawienia") else "#34d399"),
                text_align=ft.TextAlign.CENTER,
            )
            layout = (
                ft.Column([i_ctrl, t_ctrl], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6)
                if is_large
                else ft.Row([i_ctrl, t_ctrl], alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8)
            )
            base_bg = "#3f1111" if is_exit else "#112e22"
            box = ft.Container(
                content=layout,
                bgcolor=base_bg,
                border_radius=12,
                border=ft.border.all(1, "#7f1d1d" if is_exit else "#22543d"),
                padding=8,
                expand=True,
                on_click=lambda _: on_click_fn(),
            )
            box.data = {"title": title, "action": on_click_fn, "base_bg": base_bg}
            return box

        def render_dashboard_focus():
            for idx, tile in enumerate(dash_tiles):
                foc = state["zone"] == "dashboard" and state["dash_idx"] == idx
                tile.bgcolor = "#ea580c" if foc else tile.data.get("base_bg", "#112e22")
                tile.border = ft.border.all(3 if foc else 1, "#fde047" if foc else "#22543d")
            safe_ui_update()

        def open_dashboard():
            app_state["settings_open"], settings_overlay_box.visible, video_placeholder.visible = False, False, True
            state.update({"in_dashboard": True, "view_mode": "dashboard", "zone": "dashboard"})
            update_vivo_footer()
            dash_view.visible, dash_inner_view.visible = True, False
            render_dashboard_focus()
            publish_status()

        def open_section(kind):
            app_state["settings_open"], settings_overlay_box.visible, video_placeholder.visible = False, False, True
            state.update({"in_dashboard": False, "kind": kind, "in_fav": False, "in_profiles": False, "search_query": "", "zone": "list"})
            dash_view.visible, dash_inner_view.visible = False, True
            back_to_cats()
            publish_status()

        def open_catchup_section():
            app_state["settings_open"], settings_overlay_box.visible, video_placeholder.visible = False, False, True
            state.update({"in_dashboard": False, "kind": "live", "in_fav": False, "in_profiles": False, "search_query": "", "zone": "list"})
            dash_view.visible, dash_inner_view.visible = False, True
            open_category({"category_id": "__CATCHUP__", "category_name": "⏱ KANAŁY Z ARCHIWUM (CATCH UP)"})
            publish_status()

        def open_favorites_section():
            app_state["settings_open"], settings_overlay_box.visible, video_placeholder.visible = False, False, True
            state.update(
                {
                    "in_dashboard": False,
                    "in_fav": True,
                    "in_profiles": False,
                    "view_mode": "streams",
                    "active_cat": None,
                    "list_idx": 0,
                    "viewport_top": 0,
                    "zone": "list",
                }
            )
            dash_view.visible, dash_inner_view.visible = False, True
            build_full_list_data()
            publish_status()

        def open_profiles_section():
            app_state["settings_open"], settings_overlay_box.visible, video_placeholder.visible = False, False, True
            state.update(
                {
                    "in_dashboard": False,
                    "in_fav": False,
                    "in_profiles": True,
                    "view_mode": "profiles",
                    "active_cat": None,
                    "list_idx": 0,
                    "viewport_top": 0,
                    "zone": "list",
                }
            )
            dash_view.visible, dash_inner_view.visible = False, True
            build_full_list_data()
            publish_status()

        def open_settings_from_dashboard():
            state.update({"in_dashboard": False, "kind": "live", "in_fav": False, "in_profiles": False})
            dash_view.visible, dash_inner_view.visible = False, True
            back_to_cats()
            open_settings_menu_dlg()

        # 3 duże kafelki na górze (0, 1, 2) + 5 kafelków na dole (3, 4, 5, 6, 7 - w tym ZAMKNIJ)
        dash_tile_live = make_vivo_tile("Live TV", "📺", lambda: open_section("live"), is_large=True)
        dash_tile_movies = make_vivo_tile("Movies", "🎬", lambda: open_section("vod"), is_large=True)
        dash_tile_series = make_vivo_tile("Series", "🎞", lambda: open_section("series"), is_large=True)
        dash_tile_catchup = make_vivo_tile("Catch Up", "⏱", open_catchup_section, is_large=False)
        dash_tile_fav = make_vivo_tile("Ulubione", "⭐", open_favorites_section, is_large=False)
        dash_tile_playlists = make_vivo_tile("Listy", "📋", open_profiles_section, is_large=False)
        dash_tile_settings = make_vivo_tile("Ustawienia", "⚙️", open_settings_from_dashboard, is_large=False)
        dash_tile_exit = make_vivo_tile("Zamknij", "🚪", close_app_cleanly, is_large=False, is_exit=True)

        dash_tiles = [
            dash_tile_live,       # 0
            dash_tile_movies,     # 1
            dash_tile_series,     # 2
            dash_tile_catchup,    # 3
            dash_tile_fav,        # 4
            dash_tile_playlists,  # 5
            dash_tile_settings,   # 6
            dash_tile_exit,       # 7
        ]

        def refresh_current_list(_e=None):
            if not state["client"]:
                return
            state["streams_cache"].clear()
            state["categories_cache"] = {"live": None, "vod": None, "series": None}
            state["categories"] = {"live": [], "vod": [], "series": []}
            state["search_query"] = ""
            now_playing_txt.value = "🔄 Odświeżanie listy i bufora RAM z serwera..."
            list_header_lbl.value = "⏳ Pobieranie najnowszych pozycji..."
            safe_ui_update()

            def _reload_w():
                try:
                    if isinstance(state["client"], M3UClient):
                        state["client"].login()
                    ck = state["kind"]
                    rc = state["client"].get_categories(ck) or []
                    state["categories"][ck] = build_special_top_categories(ck, rc)
                    if (
                        not state["in_dashboard"]
                        and state["view_mode"] in ("streams", "episodes")
                        and state.get("active_cat")
                        and not state["in_fav"]
                        and not state["in_profiles"]
                    ):
                        ac = state["active_cat"]
                        items = state["client"].get_streams(ck, ac.get("category_id")) or []
                        for it in items:
                            it["kind"] = ck
                        state["streams"] = items
                        state["streams_cache"][(ck, str(ac.get("category_id")))] = items
                        state.update({"view_mode": "streams", "list_idx": 1 if items else 0, "viewport_top": 0})
                        build_full_list_data()
                    else:
                        open_dashboard()
                except Exception as ex:
                    now_playing_txt.value = f"❌ Błąd: {ex}"
                    safe_ui_update()

            threading.Thread(target=_reload_w, daemon=True).start()

        def open_search_dialog(_e=None):
            app_state["dialog_open"] = True
            search_in = ft.TextField(
                label="Wpisz szukaną nazwę (puste = wyczyść filtr)",
                value=state.get("search_query", ""),
                autofocus=True,
                on_submit=lambda _: _apply(),
            )

            def _apply(_e=None):
                state.update({"search_query": (search_in.value or "").strip(), "list_idx": 0, "viewport_top": 0})
                dlg.open, app_state["dialog_open"] = False, False
                open_section("live") if state["in_dashboard"] else build_full_list_data()

            dlg = ft.AlertDialog(
                title=ft.Text("🔍 Wyszukiwarka kanałów / filmów"),
                content=ft.Column([search_in], tight=True),
                actions=[ft.ElevatedButton("🔍 Filtruj", on_click=_apply, bgcolor="#ea580c", color="white")],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        def open_remote_settings_dlg(_e=None):
            nonlocal device_name, device_id
            app_state["dialog_open"] = True
            name_in = ft.TextField(label="Nazwa odbiornika", value=cache_get("device_name", device_name))
            id_in = ft.TextField(label="Unikalny Kod ID", value=cache_get("device_id", device_id))

            def _save(_):
                nonlocal device_name, device_id
                device_name = name_in.value.strip() or "Android TV"
                device_id = re.sub(r"[^a-zA-Z0-9_-]", "", id_in.value.strip()) or device_id
                cache_set("device_name", device_name)
                cache_set("device_id", device_id)
                header_dev_lbl.value = f"📺 {device_name} [{device_id}]"
                status_banner.value = f"🟢 Zdalny Cloud aktywny — Kod ID: {device_id}"
                set_tv_button_text(btn_remote_id, f"🌐 Kod ID Odbiornika: {device_id} ({device_name})")
                update_vivo_footer()
                dlg.open, app_state["dialog_open"] = False, False
                safe_ui_update()
                publish_status()

            dlg = ft.AlertDialog(
                title=ft.Text("🌐 Konfiguracja Zdalnego Odbiornika (Cloud)"),
                content=ft.Column([name_in, id_in], tight=True, spacing=10),
                actions=[
                    ft.ElevatedButton("Anuluj", on_click=lambda _: (setattr(dlg, "open", False), app_state.update({"dialog_open": False}), safe_ui_update())),
                    ft.ElevatedButton("💾 Zapisz", on_click=_save, bgcolor="#ea580c", color="white"),
                ],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        def refresh_login_button_labels():
            set_tv_button_text(btn_remote_id, f"🌐 Kod ID Odbiornika: {device_id} ({device_name})")
            set_tv_button_text(btn_edit_srv, f"🌐 Serwer: {creds['server'] or 'ustaw'}")
            set_tv_button_text(btn_edit_usr, f"👤 Login: {creds['username'] or 'ustaw'}")
            set_tv_button_text(btn_edit_pas, "🔑 Hasło: " + ("********" if creds["password"] else "ustaw"))
            set_tv_button_text(btn_edit_m3u, f"📋 Link M3U: {'[Zapisano]' if creds['m3u_url'] else 'opcjonalnie'}")
            set_tv_button_text(btn_saved_profiles_login, f"📋 WYBIERZ ZAPISANĄ LISTĘ ({len(profiles)})")

        def delete_saved_profile(prof_name):
            nonlocal profiles
            profiles = [p for p in profiles if p.get("name") != prof_name]
            cache_set("profiles", profiles)
            refresh_login_button_labels()
            now_playing_txt.value = f"🗑 Usunięto listę: {prof_name}"
            build_full_list_data() if state["in_profiles"] else safe_ui_update()
            publish_status()

        def do_login(_e=None, is_auto=False, profile_name_hint=""):
            if state["is_logging_in"]:
                return
            state["is_logging_in"] = True
            status_banner.value, status_banner.color = "⏳ Łączenie z serwerem IPTV BP...", "#fbbf24"
            safe_ui_update()

            def _w():
                try:
                    m3u, srv, usr, pas = creds["m3u_url"].strip(), creds["server"].strip(), creds["username"].strip(), creds["password"]
                    if m3u and (not usr or not pas):
                        ext = extract_xtream_from_m3u_url(m3u)
                        client = XtreamClient(*ext) if ext else M3UClient(m3u)
                    else:
                        client = XtreamClient(srv, usr, pas)
                    u_info = client.login() or {}
                    state["client"] = client
                    state["categories"] = {"live": [], "vod": [], "series": []}
                    state["categories_cache"] = {"live": None, "vod": None, "series": None}
                    state["streams_cache"].clear()

                    exp_ts = parse_int_safe(u_info.get("exp_date"), 0)
                    state["exp_date_str"] = datetime.datetime.fromtimestamp(exp_ts).strftime("%Y-%m-%d") if exp_ts > 0 else "Unlimited"
                    p_name = register_or_update_profile(profile_name_hint or state.get("active_profile_name", ""), srv, usr, pas, m3u)
                    state["active_profile_name"] = p_name or f"{client.username} ({client.server})"
                    for k_c, v_c in (("server", srv), ("username", usr), ("password", pas), ("m3u_url", m3u)):
                        cache_set(k_c, v_c)
                    refresh_login_button_labels()
                    login_view.visible = False
                    open_dashboard()
                except Exception as ex:
                    status_banner.value = f"❌ Błąd logowania: {ex} (naciśnij OK, aby ponowić)"
                    status_banner.color = "#ef4444"
                    state["login_idx"] = 5
                    update_focus_styles()
                finally:
                    state["is_logging_in"] = False

            threading.Thread(target=_w, daemon=True).start()

        def do_logout(_e=None):
            stop_playback()
            app_state["settings_open"], settings_overlay_box.visible, video_placeholder.visible = False, False, True
            state["client"] = None
            state["streams_cache"].clear()
            state["categories_cache"] = {"live": None, "vod": None, "series": None}
            dash_view.visible, dash_inner_view.visible, login_view.visible = False, False, True
            state.update({"zone": "login", "login_idx": 5})
            refresh_login_button_labels()
            update_focus_styles()
            publish_status()

        def apply_profile(prof):
            mode, name = prof.get("mode", "xtream"), prof.get("name", "Zdalna Lista")
            state["active_profile_name"] = name
            cache_set("active_profile_name", name)
            if mode == "m3u" or (prof.get("m3u_url") and not prof.get("username")):
                creds.update({"m3u_url": prof.get("m3u_url") or prof.get("url") or "", "server": "http://", "username": "", "password": ""})
            else:
                creds.update(
                    {
                        "server": prof.get("server") or prof.get("host") or "",
                        "username": prof.get("username") or prof.get("user") or "",
                        "password": prof.get("password") or prof.get("pass") or "",
                        "m3u_url": "",
                    }
                )
            for k_c in ("server", "username", "password", "m3u_url"):
                cache_set(k_c, creds[k_c])
            refresh_login_button_labels()
            do_login(profile_name_hint=name)

        def open_profiles_dialog_from_login(_e=None):
            if not profiles:
                status_banner.value = "ℹ️ Brak zapisanych list. Wpisz dane poniżej lub wyślij listę przez Cloud."
                safe_ui_update()
                return
            app_state["dialog_open"] = True
            col_items = []

            def _sel(pr):
                dlg.open, app_state["dialog_open"] = False, False
                apply_profile(pr)

            def _del(pr):
                delete_saved_profile(pr.get("name"))
                dlg.open, app_state["dialog_open"] = False, False
                safe_ui_update()

            for idx, p in enumerate(profiles):
                pn, pm, ph = p.get("name", f"Lista {idx + 1}"), (p.get("mode") or "xtream").upper(), (p.get("server") or p.get("m3u_url") or "")[:30]
                act = pn == state.get("active_profile_name")
                col_items.append(
                    ft.Row(
                        [
                            ft.ElevatedButton(
                                f"{'🟢 [AKTYWNA] ' if act else ''}{idx + 1}. [{pm}] {pn} ({ph})",
                                on_click=lambda _e, pr=p: _sel(pr),
                                bgcolor="#ea580c" if act else "#163828",
                                color="white",
                                expand=True,
                            ),
                            ft.ElevatedButton("🗑", on_click=lambda _e, pr=p: _del(pr), bgcolor="#7f1d1d", color="white"),
                        ],
                        spacing=6,
                    )
                )

            dlg = ft.AlertDialog(
                title=ft.Text("📋 Zapisane Listy / Serwery"),
                content=ft.Container(content=ft.Column(col_items, tight=True, spacing=8, scroll=ft.ScrollMode.AUTO), width=480, height=300),
                actions=[ft.ElevatedButton("Zamknij", on_click=lambda _: (setattr(dlg, "open", False), app_state.update({"dialog_open": False}), safe_ui_update()))],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        def open_field_editor(field_key, title_str, is_pass=False):
            app_state["dialog_open"] = True
            tf_in = ft.TextField(label=title_str, value=creds.get(field_key, ""), password=is_pass, autofocus=True, on_submit=lambda _: _save())

            def _save(_=None):
                creds[field_key] = (tf_in.value or "").strip() if field_key != "password" else (tf_in.value or "")
                cache_set(field_key, creds[field_key])
                refresh_login_button_labels()
                dlg.open, app_state["dialog_open"] = False, False
                update_focus_styles()

            dlg = ft.AlertDialog(
                title=ft.Text(title_str),
                content=ft.Column([tf_in], tight=True),
                actions=[ft.ElevatedButton("Zapisz", on_click=_save, bgcolor="#ea580c", color="white")],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        settings_menu_buttons = []

        def close_settings_panel():
            app_state["settings_open"], settings_overlay_box.visible, video_placeholder.visible = False, False, True
            state["zone"] = "list"
            update_focus_styles()

        def open_settings_menu_dlg(_e=None):
            app_state["settings_open"], state["settings_idx"] = True, 0
            settings_menu_buttons.clear()
            settings_panel_col.controls.clear()

            cur_it = state.get("current_item")
            fav_lbl = "⭐ Usuń bieżącą pozycję z Ulubionych" if (cur_it and is_favorite(cur_it)) else "⭐ Dodaj bieżącą pozycję do Ulubionych"

            b_s_fav = make_tv_button(fav_lbl, lambda: (close_settings_panel(), toggle_favorite()), height=36)
            b_s_pl = make_tv_button(
                f"🇵🇱 Filtr 'Tylko Polskie': {'WŁĄCZONY' if state['only_pl'] else 'WYŁĄCZONY'}",
                lambda: (toggle_only_pl(), _rebuild()),
                height=36,
            )
            b_s_vmode = make_tv_button(VLC_MODE_LABELS[state["vlc_mode"]], lambda: (cycle_vlc_mode(), _rebuild()), height=36)
            b_s_autovlc = make_tv_button(
                f"🚀 Auto-start VLC po kliknięciu OK: {'WŁ.' if state['auto_vlc'] else 'WYŁ.'}",
                lambda: (toggle_auto_vlc(), _rebuild()),
                height=36,
            )
            b_s_search = make_tv_button("🔍 Szukaj / Filtruj kanały i filmy", lambda: (close_settings_panel(), open_search_dialog()), height=36)
            b_s_refresh = make_tv_button("🔄 Odśwież listę i wyczyść bufor RAM", lambda: (close_settings_panel(), refresh_current_list()), height=36)
            b_s_cloud = make_tv_button(f"🌐 Zmień Kod ID / Nazwę odbiornika ({device_id})", lambda: (close_settings_panel(), open_remote_settings_dlg()), height=36)
            b_s_logout = make_tv_button("⇄ Zmień konto / Wyloguj do ekranu startowego", lambda: (close_settings_panel(), do_logout()), height=36)
            b_s_exit = make_tv_button("🚪 Zamknij aplikację całkowicie", close_app_cleanly, height=36)
            b_s_close = make_tv_button("↩ Wróć do listy", close_settings_panel, height=36)

            settings_menu_buttons.extend([b_s_fav, b_s_pl, b_s_vmode, b_s_autovlc, b_s_search, b_s_refresh, b_s_cloud, b_s_logout, b_s_exit, b_s_close])
            settings_panel_col.controls.append(
                ft.Text("⚙️ MENU USTAWIEŃ (⬆️ ⬇️ i OK, WSTECZ = zamknij)", size=14, weight=ft.FontWeight.BOLD, color="#fbbf24")
            )
            settings_panel_col.controls.extend(settings_menu_buttons)

            def _rebuild():
                set_tv_button_text(b_s_pl, f"🇵🇱 Filtr 'Tylko Polskie': {'WŁĄCZONY' if state['only_pl'] else 'WYŁĄCZONY'}")
                set_tv_button_text(b_s_vmode, VLC_MODE_LABELS[state["vlc_mode"]])
                set_tv_button_text(b_s_autovlc, f"🚀 Auto-start VLC po kliknięciu OK: {'WŁ.' if state['auto_vlc'] else 'WYŁ.'}")
                style_settings_menu()

            def style_settings_menu():
                for i, b in enumerate(settings_menu_buttons):
                    foc = i == state["settings_idx"]
                    bt = b.data["base_text"]
                    b.bgcolor, b.border = "#ea580c" if foc else "#163828", ft.border.all(3 if foc else 1, "#fde047" if foc else "#22543d")
                    b.data["text_ctrl"].color, b.data["text_ctrl"].value = "white", f"▶  {bt}" if foc else bt
                safe_ui_update()

            app_state["style_settings_fn"] = style_settings_menu
            video_placeholder.visible, settings_overlay_box.visible = False, True
            style_settings_menu()

        # Prawy panel: 4 przyciski (w tym bezpośredni przycisk Zamknij)
        btn_vlc = make_tv_button("🚀 Uruchom w VLC", lambda: launch_vlc_intent(state["playing_url"], state["now_playing"]), height=42, expand=True)
        btn_settings_menu = make_tv_button("⚙️ Ustawienia", open_settings_menu_dlg, height=42, expand=True)
        btn_home = make_tv_button("🏠 Menu Główne", open_dashboard, height=42, expand=True)
        btn_exit_right = make_tv_button("🚪 Zamknij", close_app_cleanly, height=42, expand=True)
        right_buttons = [btn_vlc, btn_settings_menu, btn_home, btn_exit_right]

        btn_remote_id = make_tv_button(f"🌐 Kod ID Odbiornika: {device_id} ({device_name})", open_remote_settings_dlg, height=42)
        btn_edit_srv = make_tv_button("🌐 Serwer: http://", lambda: open_field_editor("server", "Adres serwera (http://...)"), height=42)
        btn_edit_usr = make_tv_button("👤 Login: ustaw", lambda: open_field_editor("username", "Nazwa użytkownika"), height=42)
        btn_edit_pas = make_tv_button("🔑 Hasło: ustaw", lambda: open_field_editor("password", "Hasło", is_pass=True), height=42)
        btn_edit_m3u = make_tv_button("📋 Link M3U: opcjonalnie", lambda: open_field_editor("m3u_url", "Pełny link M3U"), height=42)
        btn_submit_login = make_tv_button("🚀 ZALOGUJ DO IPTV BP", lambda: do_login(), height=48)
        btn_saved_profiles_login = make_tv_button("📋 WYBIERZ ZAPISANĄ LISTĘ (0)", open_profiles_dialog_from_login, height=42)
        btn_exit_login = make_tv_button("🚪 ZAMKNIJ APLIKACJĘ", close_app_cleanly, height=40)
        login_items = [btn_remote_id, btn_edit_srv, btn_edit_usr, btn_edit_pas, btn_edit_m3u, btn_submit_login, btn_saved_profiles_login, btn_exit_login]

        login_view = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [make_iptv_bp_badge(48), ft.Text("IPTV BP  •  V7.5 PRO", size=22, weight=ft.FontWeight.BOLD, color="#fb923c")],
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=12,
                    ),
                    status_banner,
                    *login_items,
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=7,
                width=540,
            ),
            alignment=ft.alignment.center,
            expand=True,
            visible=True,
            bgcolor="#0b1f15",
            padding=16,
        )

        dash_view = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Row([make_iptv_bp_badge(42), ft.Text("IPTV BP Player", size=19, weight=ft.FontWeight.BOLD, color="#34d399")], spacing=10),
                            ft.Column([clock_txt, date_txt], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                            ft.Container(
                                content=ft.Text("🇵🇱 VIVO TV PRO", size=13, weight=ft.FontWeight.BOLD, color="#fb923c"),
                                bgcolor="#112e22",
                                padding=ft.padding.symmetric(horizontal=12, vertical=6),
                                border_radius=8,
                                border=ft.border.all(1, "#22543d"),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Column(
                        [
                            ft.Row([dash_tile_live, dash_tile_movies, dash_tile_series], spacing=12, expand=5),
                            ft.Row([dash_tile_catchup, dash_tile_fav, dash_tile_playlists, dash_tile_settings, dash_tile_exit], spacing=10, expand=2),
                        ],
                        spacing=10,
                        expand=True,
                    ),
                    ft.Row(
                        [ft.Column([footer_playlist_txt, footer_expire_txt], spacing=2), dash_back_hint_txt],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                expand=True,
                spacing=10,
            ),
            expand=True,
            visible=False,
            bgcolor="#0b1f15",
            padding=ft.padding.symmetric(horizontal=22, vertical=14),
        )

        list_slots = []
        for _ in range(VISIBLE_ROWS):
            st_txt = ft.Text("", size=14, weight=ft.FontWeight.BOLD, color="#cbd5e1", no_wrap=True, expand=True)
            s_box = ft.Container(
                content=ft.Row([st_txt], alignment=ft.MainAxisAlignment.START),
                expand=True,
                padding=ft.padding.symmetric(horizontal=12, vertical=4),
                border_radius=8,
                bgcolor="#163828",
                border=ft.border.all(1, "#22543d"),
                alignment=ft.alignment.center_left,
                visible=False,
            )
            s_box.data = {"txt": st_txt, "action": None}
            s_box.on_click = lambda _e, sb=s_box: sb.data["action"]() if sb.data["action"] else None
            list_slots.append(s_box)

        empty_list_lbl = ft.Text("Brak pozycji do wyświetlenia.", color="#94a3b8", size=14, visible=False)
        list_column = ft.Column([empty_list_lbl, *list_slots], spacing=5, expand=True)

        left_panel = ft.Container(
            width=450,
            bgcolor="#0f291d",
            padding=10,
            border_radius=10,
            border=ft.border.all(2, "#ea580c"),
            content=ft.Column(
                [
                    ft.Container(content=list_header_lbl, padding=ft.padding.symmetric(horizontal=10, vertical=6), bgcolor="#091811", border_radius=6),
                    list_column,
                ],
                expand=True,
                spacing=6,
            ),
        )

        right_panel = ft.Container(
            expand=True,
            bgcolor="#0f291d",
            padding=12,
            border_radius=10,
            border=ft.border.all(1, "#22543d"),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Row([make_iptv_bp_badge(32), header_dev_lbl], spacing=10),
                            ft.Text("⬅️ Lista | ➡️ Prawy panel | WSTECZ = Cofnij", size=12, color="#86efac"),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    video_placeholder,
                    settings_overlay_box,
                    now_playing_txt,
                    ft.Row(right_buttons, spacing=8),
                ],
                expand=True,
                spacing=8,
            ),
        )

        dash_inner_view = ft.Container(
            content=ft.Row([left_panel, right_panel], expand=True, spacing=10),
            expand=True,
            visible=False,
            bgcolor="#0b1f15",
            padding=10,
        )

        guard_view_1 = ft.View(route="/g1", controls=[ft.Container(bgcolor="#0b1f15", expand=True)], padding=0, bgcolor="#0b1f15")
        guard_view_2 = ft.View(route="/g2", controls=[ft.Container(bgcolor="#0b1f15", expand=True)], padding=0, bgcolor="#0b1f15")
        main_app_view = ft.View(route="/app", controls=[ft.Stack([login_view, dash_view, dash_inner_view], expand=True)], padding=0, bgcolor="#0b1f15")

        def ensure_view_stack():
            if len(page.views) < 3 or page.views[-1] != main_app_view:
                page.views.clear()
                page.views.extend([guard_view_1, guard_view_2, main_app_view])

        ensure_view_stack()

        def render_viewport():
            items, total = state["all_built_items"], len(state["all_built_items"])
            if total == 0:
                empty_list_lbl.visible = True
                for s in list_slots:
                    s.visible = False
                safe_ui_update()
                return

            empty_list_lbl.visible = False
            state["list_idx"] = max(0, min(state["list_idx"], total - 1))
            cur_idx, v_top = state["list_idx"], state.get("viewport_top", 0)

            if cur_idx < v_top:
                v_top = cur_idx
            elif cur_idx >= v_top + VISIBLE_ROWS:
                v_top = cur_idx - VISIBLE_ROWS + 1
            state["viewport_top"] = v_top = max(0, min(v_top, max(0, total - VISIBLE_ROWS)))

            for slot_i in range(VISIBLE_ROWS):
                item_idx, sb = v_top + slot_i, list_slots[slot_i]
                if item_idx < total:
                    entry, foc = items[item_idx], (state["zone"] == "list" and item_idx == cur_idx)
                    sb.visible = True
                    sb.bgcolor = "#ea580c" if foc else "#163828"
                    sb.border = ft.border.all(3 if foc else 1, "#fde047" if foc else "#22543d")
                    sb.data["txt"].value = f"▶  {entry['label']}" if foc else entry["label"]
                    sb.data["txt"].size = 15 if foc else 14
                    sb.data["txt"].color = "white" if foc else "#cbd5e1"
                    sb.data["action"] = entry["action"]
                else:
                    sb.visible, sb.data["action"] = False, None

            if state["zone"] == "list" and items[cur_idx].get("item") is not None:
                trigger_focus_preview(items[cur_idx]["item"])
            safe_ui_update()

        def update_focus_styles():
            if login_view.visible:
                for i, item in enumerate(login_items):
                    foc = state["zone"] == "login" and state["login_idx"] == i
                    bt = item.data["base_text"]
                    item.bgcolor = "#ea580c" if foc else ("#059669" if i == 5 else ("#7f1d1d" if i == 7 else "#163828"))
                    item.border = ft.border.all(3 if foc else 1, "#fde047" if foc else "#22543d")
                    item.data["text_ctrl"].color = "white"
                    item.data["text_ctrl"].value = f"▶  {bt}" if foc else bt
                safe_ui_update()
                return

            if dash_view.visible:
                render_dashboard_focus()
                return

            if not dash_inner_view.visible:
                return

            left_panel.border = ft.border.all(3 if state["zone"] == "list" else 1, "#ea580c" if state["zone"] == "list" else "#22543d")
            right_panel.border = ft.border.all(3 if state["zone"] == "right" else 1, "#ea580c" if state["zone"] == "right" else "#22543d")

            for i, b in enumerate(right_buttons):
                foc, bt = (state["zone"] == "right" and state["right_idx"] == i), b.data["base_text"]
                b.bgcolor = "#ea580c" if foc else ("#7f1d1d" if i == 3 else "#163828")
                b.border = ft.border.all(3 if foc else 1, "#fde047" if foc else "#22543d")
                b.data["text_ctrl"].color = "white"
                b.data["text_ctrl"].value = f"▶ {bt}" if foc else bt

            render_viewport()

        def build_full_list_data(keep_idx=False):
            prev_idx, built, v_mode, pl_ids = state["list_idx"], [], state["view_mode"], get_current_pl_cat_ids()

            if state["in_profiles"]:
                list_header_lbl.value = f"📋 ZAPISANE LISTY ({len(profiles)})"
                built.append({"label": "➕ DODAJ / EDYTUJ LISTĘ RĘCZNIE", "action": lambda: do_logout(), "item": None})
                for idx, prof in enumerate(profiles):
                    pn = prof.get("name", f"Lista {idx + 1}")
                    act = pn == state.get("active_profile_name")
                    built.append({"label": f"{idx + 1}. {'🟢 [AKTYWNA] ' if act else '📋 '}{pn}", "action": lambda pr=prof: apply_profile(pr), "item": None})
                    if not act or len(profiles) > 1:
                        built.append({"label": f"   ↳ 🗑 Usuń: {pn}", "action": lambda nm=pn: delete_saved_profile(nm), "item": None})
                state["all_built_items"] = built
                state["list_idx"] = min(prev_idx if keep_idx else 0, max(0, len(built) - 1))
                if not keep_idx:
                    state["viewport_top"] = 0
                update_focus_styles()
                return

            kind_lbl = {"live": "TV NA ŻYWO", "vod": "FILMY VOD", "series": "SERIALE"}[state["kind"]]
            if v_mode == "categories":
                if state["categories_cache"].get(state["kind"]) is not None:
                    raw = state["categories_cache"][state["kind"]]
                else:
                    raw = list(state["categories"].get(state["kind"], []))
                    if state["only_pl"]:
                        fc = [
                            c
                            for c in raw
                            if str(c.get("category_id")) in ("__ALL__", "__RECENT__", "__CATCHUP__") or is_polish_text(c.get("category_name", ""))
                        ]
                        if len(fc) > 2:
                            raw = fc
                    state["categories_cache"][state["kind"]] = raw
                list_header_lbl.value = f"📂 {kind_lbl} ({len(raw)} kat.)"
            elif v_mode == "episodes":
                raw = list(state["episodes"])
                list_header_lbl.value = f"🎞 {state['active_series_title']} ({len(raw)} odc.)"
            elif state["in_fav"]:
                raw = list(favorites_items)
                if state["only_pl"]:
                    raw = [it for it in raw if is_polish_item(it, pl_ids)]
                list_header_lbl.value = f"⭐ ULUBIONE ({len(raw)})"
            else:
                raw = list(state["streams"])
                ac = state.get("active_cat") or {}
                cnm, cid_s = ac.get("category_name", "Lista"), str(ac.get("category_id", ""))
                c_pl = cid_s not in ("__ALL__", "__RECENT__", "__CATCHUP__") and (cid_s in pl_ids or is_polish_text(cnm))
                if state["only_pl"] and not c_pl:
                    opl = [it for it in raw if is_polish_item(it, pl_ids)]
                    if opl:
                        raw = opl
                list_header_lbl.value = f"📺 {cnm} ({len(raw)})"

            q = state.get("search_query", "").strip().lower()
            if q:
                raw = [it for it in raw if q in (it.get("name") or it.get("title") or it.get("category_name") or "").lower()]

            state["filtered"] = raw

            if v_mode == "episodes":
                built.append({"label": "↩ WRÓĆ DO SERIALI", "action": back_to_streams, "item": None})
            elif v_mode == "streams" and not state["in_fav"]:
                built.append({"label": "↩ WRÓĆ DO KATEGORII", "action": back_to_cats, "item": None})

            first_media_idx = len(built)

            for i, item in enumerate(raw):
                r_num = i + 1
                if v_mode == "categories":
                    built.append({"label": f"{r_num}. 📁 {item.get('category_name', '')}", "action": lambda c=item: open_category(c), "item": item})
                elif v_mode == "episodes":
                    et = item.get("display_title") or item.get("title") or "Odcinek"
                    built.append({"label": f"{r_num}. 🎞 {et}", "action": lambda ep=item: play_episode(ep), "item": item})
                else:
                    fav = "⭐ " if is_favorite(item) else ""
                    pl = "🇵🇱 " if is_polish_item(item, pl_ids) else ""
                    arch = "⏱ " if parse_int_safe(item.get("tv_archive"), 0) == 1 else ""
                    nm = item.get("name") or item.get("title") or "Pozycja"
                    built.append({"label": f"{r_num}. {fav}{pl}{arch}{nm}", "action": lambda it=item: on_stream_click(it), "item": item})

            state["all_built_items"] = built
            if keep_idx:
                state["list_idx"] = min(prev_idx, max(0, len(built) - 1))
            elif raw:
                state["list_idx"], state["viewport_top"] = first_media_idx, 0
            else:
                state["list_idx"], state["viewport_top"] = 0, 0

            update_focus_styles()

        def back_to_streams():
            state.update({"view_mode": "streams", "viewport_top": 0})
            build_full_list_data()

        def back_to_cats():
            state.update({"view_mode": "categories", "active_cat": None, "viewport_top": 0})
            if not state["categories"].get(state["kind"]):
                list_header_lbl.value = "⏳ Pobieranie kategorii..."
                safe_ui_update()

                def _w():
                    try:
                        cats = state["client"].get_categories(state["kind"]) or []
                        state["categories"][state["kind"]] = build_special_top_categories(state["kind"], cats)
                        state["categories_cache"][state["kind"]] = None
                        build_full_list_data()
                    except Exception:
                        pass

                threading.Thread(target=_w, daemon=True).start()
            else:
                build_full_list_data()

        def open_category(cat_obj):
            state.update({"view_mode": "streams", "active_cat": cat_obj, "list_idx": 0, "viewport_top": 0})
            ckey = (state["kind"], str(cat_obj.get("category_id", "__ALL__")))
            if ckey in state["streams_cache"]:
                state["streams"] = state["streams_cache"][ckey]
                build_full_list_data()
                return
            for s in list_slots:
                s.visible = False
            list_header_lbl.value = f"⏳ Ładowanie: {cat_obj.get('category_name')}..."
            safe_ui_update()

            def _w():
                try:
                    items = state["client"].get_streams(state["kind"], cat_obj.get("category_id")) or []
                    for it in items:
                        it["kind"] = state["kind"]
                    state["streams"] = state["streams_cache"][ckey] = items
                    build_full_list_data()
                except Exception:
                    pass

            threading.Thread(target=_w, daemon=True).start()

        def play_episode(ep_item):
            ext = ep_item.get("container_extension", "mp4") or "mp4"
            url = state["client"].build_stream_url("series", ep_item.get("id"), ext, item=ep_item)
            play_stream_url(url, ep_item.get("display_title") or "Odcinek", item=ep_item)

        def on_stream_click(item):
            k = item.get("kind") or state["kind"]
            title = item.get("name") or item.get("title") or "Strumień"
            uid = get_item_unique_id(item)
            if k == "series":
                state["active_series_id"], state["active_series_title"] = uid, title
                for s in list_slots:
                    s.visible = False
                list_header_lbl.value = f"⏳ Odcinki: {title}..."
                safe_ui_update()

                def _w():
                    try:
                        info = state["client"].get_series_info(item.get("series_id"))
                        eps_map = info.get("episodes") or {}
                        flat_eps = []
                        for s_num in sorted(eps_map.keys(), key=lambda x: int(x) if str(x).isdigit() else 999):
                            for ep in eps_map[s_num]:
                                ep_c = dict(ep)
                                ep_c["display_title"] = f"S{int(s_num):02d}E{int(ep.get('episode_num', 1)):02d} - {ep.get('title')}"
                                flat_eps.append(ep_c)
                        state.update({"episodes": flat_eps, "view_mode": "episodes", "viewport_top": 0})
                        build_full_list_data()
                    except Exception:
                        back_to_streams()

                threading.Thread(target=_w, daemon=True).start()
                return
            ext = item.get("container_extension", "ts" if k == "live" else "mp4") or ("ts" if k == "live" else "mp4")
            url = state["client"].build_stream_url(k, item.get("stream_id"), ext, item=item)
            play_stream_url(url, title, item=item)

        def handle_remote_command(cmd):
            nonlocal profiles, device_name
            c_type = cmd.get("type") or cmd.get("action") or cmd.get("cmd") or ""

            if (
                c_type in ("add_profile", "send_list", "new_list", "add_list", "push_list")
                or ("server" in cmd and "username" in cmd)
                or ("m3u_url" in cmd)
            ):
                p_name = cmd.get("name") or cmd.get("profile_name") or f"Lista {len(profiles) + 1}"
                mode = cmd.get("mode") or ("m3u" if cmd.get("m3u_url") and not cmd.get("username") else "xtream")
                new_p = {
                    "name": p_name,
                    "mode": mode,
                    "server": cmd.get("server") or cmd.get("host") or "",
                    "username": cmd.get("username") or cmd.get("user") or "",
                    "password": cmd.get("password") or cmd.get("pass") or "",
                    "m3u_url": cmd.get("m3u_url") or cmd.get("url") or "",
                    "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                profiles = [p for p in profiles if p.get("name") != p_name]
                profiles.insert(0, new_p)
                cache_set("profiles", profiles)
                refresh_login_button_labels()
                if cmd.get("switch", cmd.get("auto_switch", cmd.get("activate", True))):
                    apply_profile(new_p)
                else:
                    if state["in_profiles"]:
                        build_full_list_data()
                    publish_status()
            elif c_type in ("switch_profile", "select_profile", "activate_profile", "switch_list", "switch"):
                t = cmd.get("name") or cmd.get("profile_name")
                for p in profiles:
                    if p.get("name") == t:
                        apply_profile(p)
                        break
            elif c_type in ("delete_profile", "remove_profile", "del_profile", "delete_list", "delete"):
                delete_saved_profile(cmd.get("name") or cmd.get("profile_name"))
            elif c_type in ("set_name", "rename", "set_device_name", "save_name"):
                nn = (cmd.get("device_name") or cmd.get("name") or "").strip()
                if nn:
                    device_name = nn
                    cache_set("device_name", nn)
                    cid = cache_get("device_id", device_id)
                    header_dev_lbl.value = f"📺 {nn} [{cid}]"
                    refresh_login_button_labels()
                    update_vivo_footer()
                    safe_ui_update()
                    publish_status()
            elif c_type in ("remote_control", "control", "player", "key", "command"):
                _exec_player_action(cmd.get("command") or cmd.get("action") or cmd.get("key") or "")
            else:
                _exec_player_action(c_type)

        def _exec_player_action(act):
            if act in ("play_pause", "toggle", "play", "pause"):
                toggle_play_pause()
            elif act == "stop":
                stop_playback()
            elif act == "mute":
                state["muted"] = not state["muted"]
                publish_status()
            elif act in ("vol_up", "volume_up"):
                state["volume"] = min(100, state["volume"] + 10)
                publish_status()
            elif act in ("vol_down", "volume_down"):
                state["volume"] = max(0, state["volume"] - 10)
                publish_status()
            elif act == "logout":
                do_logout()
            elif act in ("reload_list", "refresh_list"):
                refresh_current_list()
            elif act in ("only_pl", "toggle_only_pl"):
                toggle_only_pl()
            elif act in ("refresh", "ping", "status", "get_status"):
                publish_status()

        def cloud_listener():
            nonlocal processed_ids
            s = make_session()
            while not stop_event.is_set():
                if app_state["storage_ready"] and not app_state["paused_in_bg"]:
                    try:
                        cid = re.sub(r"[^a-zA-Z0-9_-]", "", cache_get("device_id", device_id))
                        url = f"https://ntfy.sh/iptv_v4_pro_{cid},iptv_v4_pro_{cid}_cmd/json?poll=1&since=30m"
                        r = s.get(url, timeout=4)
                        if r.status_code == 200 and not stop_event.is_set():
                            for line in r.text.splitlines():
                                if not line.strip():
                                    continue
                                ev = json.loads(line)
                                mid = ev.get("id")
                                if not mid or mid in processed_ids or ev.get("event") != "message":
                                    continue
                                processed_ids.add(mid)
                                cache_set("processed_ids", list(processed_ids)[-200:])
                                try:
                                    pl = json.loads(ev.get("message", ""))
                                except Exception:
                                    continue
                                if isinstance(pl, dict) and pl.get("type") != "player_status":
                                    handle_remote_command(pl)
                    except Exception:
                        pass
                if stop_event.wait(2.5):
                    break

        threading.Thread(target=cloud_listener, daemon=True).start()

        def handle_universal_back():
            now = time.time()
            if now - app_state["last_back_guard"] < 0.25:
                return
            app_state["last_back_guard"] = now

            if app_state["settings_open"]:
                close_settings_panel()
                return
            if app_state["dialog_open"]:
                return
            if login_view.visible or dash_view.visible:
                if now - app_state["last_back_press_ts"] < 2.5:
                    close_app_cleanly()
                else:
                    app_state["last_back_press_ts"] = now
                    dash_back_hint_txt.value = "⚠️ Naciśnij WSTECZ jeszcze raz (lub kliknij kafelek '🚪 Zamknij'), aby wyjść!"
                    dash_back_hint_txt.color = "#ef4444"
                    status_banner.value = "⚠️ Naciśnij WSTECZ jeszcze raz, aby zamknąć aplikację!"
                    status_banner.color = "#ef4444"
                    safe_ui_update()
                return
            if dash_inner_view.visible:
                if state["view_mode"] == "episodes":
                    back_to_streams()
                elif state["view_mode"] == "streams" and not state["in_fav"] and not state["in_profiles"]:
                    back_to_cats()
                else:
                    open_dashboard()

        page.on_view_pop = lambda _e: (ensure_view_stack(), safe_ui_update(), handle_universal_back())
        try:
            page.window_prevent_close = page.window.prevent_close = True
        except Exception:
            pass

        def on_key_event(e: ft.KeyboardEvent):
            key, total_built = e.key, len(state["all_built_items"])

            if app_state["settings_open"] and settings_menu_buttons:
                if key == "Arrow Down":
                    state["settings_idx"] = (state["settings_idx"] + 1) % len(settings_menu_buttons)
                    app_state["style_settings_fn"]()
                elif key == "Arrow Up":
                    state["settings_idx"] = (state["settings_idx"] - 1) % len(settings_menu_buttons)
                    app_state["style_settings_fn"]()
                elif key in ("Enter", "Select", " "):
                    now = time.time()
                    if now - app_state["last_ok_ts"] < 0.25:
                        return
                    app_state["last_ok_ts"] = now
                    settings_menu_buttons[state["settings_idx"]].data["action"]()
                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                    close_settings_panel()
                return

            if app_state["dialog_open"]:
                return

            if login_view.visible:
                state["zone"] = "login"
                if key == "Arrow Down" and state["login_idx"] < len(login_items) - 1:
                    state["login_idx"] += 1
                elif key == "Arrow Up" and state["login_idx"] > 0:
                    state["login_idx"] -= 1
                elif key in ("Enter", "Select", " "):
                    now = time.time()
                    if now - app_state["last_ok_ts"] < 0.25:
                        return
                    app_state["last_ok_ts"] = now
                    login_items[state["login_idx"]].data["action"]()
                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                    handle_universal_back()
                    return
                update_focus_styles()
                return

            if dash_view.visible:
                state["zone"] = "dashboard"
                if key == "Arrow Right" and state["dash_idx"] < len(dash_tiles) - 1:
                    state["dash_idx"] += 1
                elif key == "Arrow Left" and state["dash_idx"] > 0:
                    state["dash_idx"] -= 1
                elif key == "Arrow Down" and state["dash_idx"] < 3:
                    state["dash_idx"] = min(len(dash_tiles) - 1, state["dash_idx"] + 3)
                elif key == "Arrow Up" and state["dash_idx"] >= 3:
                    state["dash_idx"] = max(0, min(2, state["dash_idx"] - 3))
                elif key in ("Enter", "Select", " "):
                    now = time.time()
                    if now - app_state["last_ok_ts"] < 0.25:
                        return
                    app_state["last_ok_ts"] = now
                    dash_tiles[state["dash_idx"]].data["action"]()
                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                    handle_universal_back()
                    return
                render_dashboard_focus()
                return

            if dash_inner_view.visible:
                if key in ("Page Down", "Channel Down") and total_built > 0:
                    state["list_idx"] = min(total_built - 1, state["list_idx"] + VISIBLE_ROWS)
                    render_viewport()
                elif key in ("Page Up", "Channel Up") and total_built > 0:
                    state["list_idx"] = max(0, state["list_idx"] - VISIBLE_ROWS)
                    render_viewport()
                elif key == "Arrow Right":
                    if state["zone"] == "list":
                        state["zone"], state["right_idx"] = "right", 0
                    elif state["zone"] == "right" and state["right_idx"] < len(right_buttons) - 1:
                        state["right_idx"] += 1
                    update_focus_styles()
                elif key == "Arrow Left":
                    if state["zone"] == "right":
                        state["zone"] = "list"
                        update_focus_styles()
                elif key == "Arrow Down":
                    if state["zone"] == "list" and total_built > 0 and state["list_idx"] < total_built - 1:
                        state["list_idx"] += 1
                        render_viewport()
                    elif state["zone"] == "right" and state["right_idx"] < len(right_buttons) - 1:
                        state["right_idx"] += 1
                        update_focus_styles()
                elif key == "Arrow Up":
                    if state["zone"] == "list" and total_built > 0 and state["list_idx"] > 0:
                        state["list_idx"] -= 1
                        render_viewport()
                    elif state["zone"] == "right" and state["right_idx"] > 0:
                        state["right_idx"] -= 1
                        update_focus_styles()
                elif key in ("Enter", "Select", " "):
                    now = time.time()
                    if now - app_state["last_ok_ts"] < 0.25:
                        return
                    app_state["last_ok_ts"] = now
                    if state["zone"] == "list" and 0 <= state["list_idx"] < total_built:
                        state["all_built_items"][state["list_idx"]]["action"]()
                    elif state["zone"] == "right":
                        right_buttons[state["right_idx"]].data["action"]()
                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                    handle_universal_back()

        page.on_keyboard_event = on_key_event
        update_focus_styles()

        def deferred_cold_boot_loader():
            nonlocal device_id, device_name, profiles, processed_ids, favorites_items
            time.sleep(0.35)
            if stop_event.is_set():
                return

            keys_defaults = {
                "device_id": "",
                "device_name": "Android TV Box",
                "profiles": [],
                "processed_ids": [],
                "auto_vlc": True,
                "auto_login": True,
                "only_pl": False,
                "vlc_mode": "vlc_clean",
                "favorites_v2": [],
                "server": "http://",
                "username": "",
                "password": "",
                "m3u_url": "",
                "active_profile_name": "",
            }
            for k, d in keys_defaults.items():
                try:
                    val = page.client_storage.get(k)
                    storage_cache[k] = val if val is not None else d
                except Exception:
                    storage_cache[k] = d

            device_id = cache_get("device_id")
            if not device_id:
                device_id = f"android-{uuid.uuid4().hex[:6]}"
                cache_set("device_id", device_id)

            device_name = cache_get("device_name", "Android TV Box")
            profiles = list(cache_get("profiles", []))
            processed_ids = set(cache_get("processed_ids", []))
            favorites_items = list(cache_get("favorites_v2", []))

            state["auto_vlc"] = bool(cache_get("auto_vlc", True))
            state["auto_login"] = bool(cache_get("auto_login", True))
            state["only_pl"] = bool(cache_get("only_pl", False))
            saved_v = cache_get("vlc_mode", "vlc_clean")
            state["vlc_mode"] = saved_v if saved_v in VLC_MODE_LABELS else "vlc_clean"
            state["active_profile_name"] = cache_get("active_profile_name", "")

            for k_c in ("server", "username", "password", "m3u_url"):
                creds[k_c] = cache_get(k_c, "http://" if k_c == "server" else "")

            if has_saved_credentials() and not profiles:
                register_or_update_profile(state["active_profile_name"], creds["server"], creds["username"], creds["password"], creds["m3u_url"])

            header_dev_lbl.value = f"📺 {device_name} [{device_id}]"
            status_banner.value = f"🟢 Zdalny Cloud aktywny — Kod ID: {device_id}"
            refresh_login_button_labels()
            update_vivo_footer()
            app_state["storage_ready"] = True
            update_focus_styles()
            publish_status()

            if state["auto_login"] and has_saved_credentials():
                time.sleep(0.2)
                if login_view.visible and not state["client"]:
                    do_login(is_auto=True)

        threading.Thread(target=deferred_cold_boot_loader, daemon=True).start()

    except Exception as e:
        page.add(ft.Text(f"Błąd krytyczny: {e}\n{traceback.format_exc()}", color="red", size=14))
        page.update()


if __name__ == "__main__":
    ft.app(target=main, assets_dir="assets")
