# -*- coding: utf-8 -*-
"""
IPTV BP V7.3 PRO - VIVO TV EDITION (Android TV)
"""

import base64
import datetime
import json
import re
import threading
import time
import traceback
from urllib.parse import parse_qs, urlparse
import uuid

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

def is_polish_text(text: str) -> bool:
    return bool(text and PL_REGEX.search(str(text)))

def is_polish_item(item: dict, pl_cat_ids: set = None) -> bool:
    if not isinstance(item, dict): return False
    if pl_cat_ids and str(item.get("category_id", "")) in pl_cat_ids: return True
    for f in (item.get("name"), item.get("title"), item.get("category_name"), item.get("group")):
        if f and is_polish_text(f): return True
    return False

def parse_int_safe(val, default=0) -> int:
    if val is None: return default
    try: return int(float(str(val).strip()))
    except Exception:
        m = re.search(r"(\d+)$", str(val))
        return int(m.group(1)) if m else default

def decode_b64_safe(val: str) -> str:
    if not val: return ""
    try: return base64.b64decode(val).decode("utf-8", errors="ignore").strip()
    except Exception: return str(val).strip()

def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()

def get_added_timestamp(item: dict) -> int:
    if not isinstance(item, dict): return 0
    return parse_int_safe(item.get("added"), 0) or parse_int_safe(item.get("last_modified"), 0) or parse_int_safe(item.get("releaseDate"), 0)

def get_numeric_stream_id(item: dict) -> int:
    if not isinstance(item, dict): return 0
    return parse_int_safe(item.get("stream_id"), 0) or parse_int_safe(item.get("series_id"), 0) or parse_int_safe(item.get("id"), 0)

def sort_categories_pl_first(categories: list) -> list:
    pl_c, oth_c = [], []
    for c in categories:
        (pl_c if is_polish_text(c.get("category_name", "")) else oth_c).append(c)
    return pl_c + oth_c

def sort_streams_smart(items: list, kind: str, force_newest_only: bool = False, pl_cat_ids: set = None) -> list:
    if not items: return []
    if kind == "live" and not force_newest_only:
        pl_l, oth_l = [], []
        for it in items: (pl_l if is_polish_item(it, pl_cat_ids) else oth_l).append(it)
        return pl_l + oth_l
    def _key(it): return (1 if is_polish_item(it, pl_cat_ids) else 0, get_added_timestamp(it), get_numeric_stream_id(it))
    return sorted(items, key=_key, reverse=True)

def make_session():
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s

def extract_xtream_from_m3u_url(m3u_url):
    try:
        parsed = urlparse(m3u_url.strip())
        if not parsed.scheme or not parsed.netloc: return None
        qs = parse_qs(parsed.query)
        u, p = (qs.get("username") or qs.get("user") or [None])[0], (qs.get("password") or qs.get("pass") or [None])[0]
        if u and p: return f"{parsed.scheme}://{parsed.netloc}", u, p
    except Exception: pass
    return None

def clean_media_title(title):
    if not title: return ""
    c = re.sub(r"^(\|?[A-Za-z0-9]{2,5}\|?\s*[:|\-]+\s*)+", "", str(title))
    c = re.sub(r"\[.*?\]", "", c)
    c = re.sub(r"\s*[Ss]\d{1,2}\s*[Ee]\d{1,3}.*$", "", c)
    c = re.sub(r"\s*\(\d{4}\).*$", "", c)
    c = re.sub(r"\s*-\s*\d{4}\s*$", "", c)
    c = re.sub(r"\b(FHD|UHD|4K|HD|1080p|720p|HEVC|H265|LEKTOR|DUBBING|NAPISY|PL|EN|FR|CAM|WEB-DL|BLURAY)\b.*$", "", c, flags=re.IGNORECASE)
    return c.strip(" -|_:")

def get_item_unique_id(item):
    if not isinstance(item, dict): return ""
    return str(item.get("stream_id") or item.get("series_id") or item.get("id") or item.get("category_id") or "")

metadata_memory_cache = {}
epg_memory_cache = {}

def fetch_rich_media_details(title: str, item: dict = None, client=None):
    clean_t = clean_media_title(title)
    cache_key = f"{clean_t.lower()}_{get_item_unique_id(item)}"
    if cache_key in metadata_memory_cache: return metadata_memory_cache[cache_key]

    poster, plot, rating, year, src = None, "", "", "", ""

    if isinstance(item, dict):
        poster = item.get("stream_icon") or item.get("cover") or None
        plot = (item.get("plot") or item.get("description") or item.get("overview") or "").strip()
        rating = str(item.get("rating") or "").strip()
        year = str(item.get("year") or item.get("releaseDate") or "")[:4].strip()
        if plot: src = "Serwer VOD"

    if not plot and client and isinstance(item, dict) and hasattr(client, "get_vod_metadata"):
        try:
            k = item.get("kind", "")
            if k == "vod" and item.get("stream_id"):
                m = client.get_vod_metadata(item.get("stream_id"))
                if m:
                    poster, plot, rating, year = m.get("poster") or poster, m.get("plot") or plot, m.get("rating") or rating, m.get("year") or year
                    if plot: src = "Xtream VOD"
            elif k == "series" and item.get("series_id"):
                m = client.get_series_metadata(item.get("series_id"))
                if m:
                    poster, plot, rating, year = m.get("poster") or poster, m.get("plot") or plot, m.get("rating") or rating, m.get("year") or year
                    if plot: src = "Xtream Series"
        except Exception: pass

    if clean_t and (not plot or not poster):
        try:
            u_pl = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={requests.utils.quote(clean_t)}&language=pl-PL"
            r = requests.get(u_pl, timeout=3.5)
            if r.status_code == 200 and r.json().get("results"):
                b = r.json()["results"][0]
                if b.get("poster_path") and not poster: poster = f"https://image.tmdb.org/t/p/w500{b['poster_path']}"
                rel = b.get("release_date") or b.get("first_air_date") or ""
                if rel and not year: year = rel[:4]
                if b.get("vote_average") and not rating: rating = str(round(b.get("vote_average", 0), 1))
                if b.get("overview") and not plot:
                    plot, src = b["overview"].strip(), "TMDB PL"
        except Exception: pass

    if clean_t and not plot:
        try:
            w_url = f"https://pl.wikipedia.org/w/api.php?action=query&list=search&srsearch={requests.utils.quote(clean_t + ' film')}&utf8=&format=json&srlimit=2"
            rw = requests.get(w_url, headers=DEFAULT_HEADERS, timeout=3.0)
            if rw.status_code == 200 and rw.json().get("query", {}).get("search"):
                pg_t = rw.json()["query"]["search"][0].get("title", "")
                rs = requests.get(f"https://pl.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(pg_t)}", headers=DEFAULT_HEADERS, timeout=3.0)
                if rs.status_code == 200:
                    sd = rs.json()
                    ext = (sd.get("extract") or "").strip()
                    if len(ext) > 35:
                        plot, src = ext, "Wikipedia PL"
                        th = (sd.get("thumbnail") or {}).get("source")
                        if th and not poster: poster = th
        except Exception: pass

    if clean_t and not plot:
        try:
            rt = requests.get(f"https://api.tvmaze.com/singlesearch/shows?q={requests.utils.quote(clean_t)}", timeout=3.0)
            if rt.status_code == 200:
                td = rt.json()
                sm = strip_html(td.get("summary") or "")
                if sm: plot, src = sm, "TVMaze"
                im = (td.get("image") or {}).get("medium")
                if im and not poster: poster = im
                pr = td.get("premiered") or ""
                if pr and not year: year = pr[:4]
                rv = (td.get("rating") or {}).get("average")
                if rv and not rating: rating = str(rv)
        except Exception: pass

    if clean_t and not plot:
        try:
            re_en = requests.get(f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={requests.utils.quote(clean_t)}&language=en-US", timeout=3.0)
            if re_en.status_code == 200 and re_en.json().get("results") and re_en.json()["results"][0].get("overview"):
                plot, src = re_en.json()["results"][0]["overview"].strip(), "TMDB EN"
        except Exception: pass

    res = {"poster": poster, "plot": plot, "rating": rating, "year": year, "source": src}
    metadata_memory_cache[cache_key] = res
    return res

class XtreamClient:
    def __init__(self, server, username, password):
        self.server, self.username, self.password = server.rstrip("/"), username, password
        self.session, self.user_info, self.pl_cat_ids = make_session(), {}, {"live": set(), "vod": set(), "series": set()}

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
                if self.user_info.get("auth") != 1: raise ValueError("Nieprawidłowy login lub hasło.")
                return self.user_info
            except Exception as e:
                last_err = e
                time.sleep(0.4)
        raise last_err

    def get_categories(self, kind):
        raw = self._api({"action": {"live": "get_live_categories", "vod": "get_vod_categories", "series": "get_series_categories"}[kind]}) or []
        self.pl_cat_ids[kind] = {str(c.get("category_id", "")) for c in raw if is_polish_text(c.get("category_name", ""))}
        return sort_categories_pl_first(raw)

    def get_streams(self, kind, category_id=None):
        p = {"action": {"live": "get_live_streams", "vod": "get_vod_streams", "series": "get_series"}[kind]}
        if category_id and category_id not in ("__ALL__", "__RECENT__", "__CATCHUP__"): p["category_id"] = category_id
        raw = self._api(p, timeout=24) or []
        if category_id == "__CATCHUP__": raw = [s for s in raw if parse_int_safe(s.get("tv_archive"), 0) == 1]
        return sort_streams_smart(raw, kind, force_newest_only=(category_id == "__RECENT__"), pl_cat_ids=self.pl_cat_ids.get(kind, set()))

    def get_series_info(self, series_id):
        return self._api({"action": "get_series_info", "series_id": series_id})

    def get_vod_metadata(self, vod_id):
        try:
            i = self._api({"action": "get_vod_info", "vod_id": vod_id}, timeout=3.5).get("info", {})
            return {"plot": (i.get("plot") or i.get("description") or "").strip(), "poster": i.get("movie_image") or i.get("cover_big"), "rating": str(i.get("rating") or "").strip(), "year": str(i.get("releasedate") or i.get("year") or "")[:4].strip()} if isinstance(i, dict) else None
        except Exception: return None

    def get_series_metadata(self, series_id):
        try:
            i = self._api({"action": "get_series_info", "series_id": series_id}, timeout=3.5).get("info", {})
            return {"plot": (i.get("plot") or i.get("description") or "").strip(), "poster": i.get("cover") or i.get("cover_big"), "rating": str(i.get("rating") or "").strip(), "year": str(i.get("releaseDate") or "")[:4].strip()} if isinstance(i, dict) else None
        except Exception: return None

    def get_short_epg(self, stream_id):
        if not stream_id: return []
        now_ts = time.time()
        if c := epg_memory_cache.get(str(stream_id)):
            if now_ts - c[0] < 120: return c[1]
        try:
            parsed = []
            for el in (self._api({"action": "get_short_epg", "stream_id": stream_id, "limit": 3}, timeout=4).get("epg_listings", [])[:3]):
                t, desc, st, et = decode_b64_safe(el.get("title", "")), decode_b64_safe(el.get("description", "")), (el.get("start") or "")[11:16], (el.get("end") or "")[11:16]
                if t: parsed.append((f"🕒 {st} - {et}  •  {t}\n{desc}" if desc else f"🕒 {st} - {et}  •  {t}").strip())
            epg_memory_cache[str(stream_id)] = (now_ts, parsed)
            return parsed
        except Exception: return []

    def build_stream_url(self, kind, stream_id, container_ext="ts", item=None):
        if item and item.get("direct_url"): return item["direct_url"]
        return f"{self.server}/{'live' if kind=='live' else ('movie' if kind=='vod' else 'series')}/{self.username}/{self.password}/{stream_id}.{container_ext}"

class M3UClient:
    def __init__(self, source):
        self.source, self.server, self.username = source.strip(), "Lista M3U", "M3U Playlist"
        self.user_info, self.session = {"auth": 1, "exp_date": None}, make_session()
        self.categories, self.streams_by_cat, self.pl_cat_ids, self.series_map, self.direct_urls = {"live": [], "vod": [], "series": []}, {"live": {}, "vod": {}, "series": {}}, {"live": set(), "vod": set(), "series": set()}, {}, {}

    def login(self):
        self.categories, self.streams_by_cat, self.pl_cat_ids, self.series_map, self.direct_urls = {"live": [], "vod": [], "series": []}, {"live": {}, "vod": {}, "series": {}}, {"live": set(), "vod": set(), "series": set()}, {}, {}
        r = self.session.get(self.source, timeout=25)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        self._parse_m3u(r.text)
        return self.user_info

    def _classify_entry(self, url, group, name):
        lu, lg, ep = url.lower(), group.lower(), re.search(r"[Ss](\d{1,2})\s*[Ee](\d{1,3})", name)
        if "/series/" in lu or ep or any(k in lg for k in ["seriale", "series", "serial"]): return "series", ep
        if "/movie/" in lu or lu.endswith((".mp4", ".mkv", ".avi")) or any(k in lg for k in ["vod", "filmy", "movies", "film", "kino"]): return "vod", None
        return "live", None

    def _parse_m3u(self, text):
        cat_sets, series_groups, attr_re, extinf, seq = {"live": {}, "vod": {}, "series": {}}, {}, re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"'), None, 1
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                if line.startswith("#EXTINF"): extinf = line
                continue
            attrs = dict(attr_re.findall(extinf)) if extinf else {}
            name = extinf.split(",", 1)[1].strip() if (extinf and "," in extinf) else f"Kanał {seq}"
            group, logo, catchup = (attrs.get("group-title") or "Bez kategorii").strip(), attrs.get("tvg-logo") or "", 1 if (attrs.get("catchup") or attrs.get("tvg-rec")) else 0
            kind, ep_match = self._classify_entry(line, group, name)

            if group not in cat_sets[kind]:
                cid = f"m3u_{kind}_{len(cat_sets[kind]) + 1}"
                cat_sets[kind][group] = cid
                self.categories[kind].append({"category_id": cid, "category_name": group})
                if is_polish_text(group): self.pl_cat_ids[kind].add(cid)
                self.streams_by_cat[kind][cid] = []
            else: cid = cat_sets[kind][group]

            iid, self.direct_urls[f"m3u_item_{seq}"] = f"m3u_item_{seq}", line
            if kind in ("live", "vod"):
                self.streams_by_cat[kind][cid].append({"stream_id": iid, "category_id": cid, "added": str(seq), "name": name, "group": group, "stream_icon": logo, "direct_url": line, "tv_archive": catchup, "kind": kind})
            else:
                s_num, e_num, base_t = str(int(ep_match.group(1))) if ep_match else "1", int(ep_match.group(2)) if ep_match else 1, name[: ep_match.start()].strip(" -|:") if ep_match else name
                if (cid, base_t.lower()) not in series_groups:
                    sid = f"m3u_ser_{seq}"
                    series_groups[(cid, base_t.lower())] = sid
                    self.streams_by_cat["series"][cid].append({"series_id": sid, "category_id": cid, "last_modified": str(seq), "name": base_t, "group": group, "cover": logo, "kind": "series"})
                    self.series_map[sid] = {"episodes": {}}
                self.series_map[series_groups[(cid, base_t.lower())]]["episodes"].setdefault(s_num, []).append({"id": iid, "episode_num": e_num, "title": name, "direct_url": line})
            seq += 1
            extinf = None
        for k in ("live", "vod", "series"): self.categories[k] = sort_categories_pl_first(self.categories[k])

    def get_categories(self, kind): return self.categories.get(kind, [])
    def get_streams(self, kind, category_id=None):
        cmap, pl_ids = self.streams_by_cat.get(kind, {}), self.pl_cat_ids.get(kind, set())
        if not category_id or category_id in ("__ALL__", "__RECENT__", "__CATCHUP__"):
            res = [s for v in cmap.values() for s in v]
            if category_id == "__CATCHUP__": res = [s for s in res if parse_int_safe(s.get("tv_archive"), 0) == 1]
            return sort_streams_smart(res, kind, force_newest_only=(category_id == "__RECENT__"), pl_cat_ids=pl_ids)
        return sort_streams_smart(cmap.get(category_id, []), kind, pl_cat_ids=pl_ids)
    def get_series_info(self, series_id): return self.series_map.get(series_id, {"episodes": {}})
    def get_short_epg(self, stream_id): return []
    def build_stream_url(self, kind, stream_id, container_ext="ts", item=None): return item["direct_url"] if item and item.get("direct_url") else self.direct_urls.get(stream_id, "")

def build_special_top_categories(kind: str, raw_cats: list) -> list:
    top = [{"category_id": "__ALL__", "category_name": "🔥 WSZYSTKIE (🇵🇱 Polskie na górze)"}]
    if kind == "live": top.append({"category_id": "__CATCHUP__", "category_name": "⏱ KANAŁY Z ARCHIWUM (CATCH UP)"})
    if kind in ("vod", "series"): top.append({"category_id": "__RECENT__", "category_name": "🆕 OSTATNIO DODANE (NOWOŚCI NA GÓRZE)"})
    return top + raw_cats

def make_iptv_bp_badge(size_px=42):
    return ft.Container(
        content=ft.Column([
            ft.Text("IPTV", size=max(10, int(size_px * 0.28)), weight=ft.FontWeight.BOLD, color="white", height=max(12, int(size_px * 0.32))),
            ft.Text("BP", size=max(11, int(size_px * 0.34)), weight=ft.FontWeight.BOLD, color="#fde047", height=max(13, int(size_px * 0.38))),
        ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
        width=size_px * 1.35, height=size_px, bgcolor="#ea580c", border_radius=10, border=ft.border.all(2, "#fed7aa"), alignment=ft.alignment.center,
        gradient=ft.LinearGradient(begin=ft.alignment.top_left, end=ft.alignment.bottom_right, colors=["#f97316", "#c2410c"]),
    )

def main(page: ft.Page):
    try:
        page.title, page.theme_mode, page.bgcolor, page.padding = "IPTV BP", ft.ThemeMode.DARK, "#0b1f15", 0
        stop_event = threading.Event()
        app_state = {"dialog_open": False, "settings_open": False, "paused_in_bg": False, "preview_seq": 0, "last_back_press_ts": 0.0, "last_back_guard": 0.0, "last_ok_ts": 0.0, "storage_ready": False}
        storage_lock, storage_cache, pending_writes, write_timer_active = threading.Lock(), {"device_id": "android-tv", "device_name": "Android TV Box", "profiles": [], "processed_ids": [], "auto_vlc": True, "auto_login": True, "only_pl": False, "vlc_mode": "vlc_clean", "favorites_v2": [], "server": "http://", "username": "", "password": "", "m3u_url": "", "active_profile_name": ""}, {}, {"running": False}

        def cache_get(key, default=None): return storage_cache.get(key) if storage_cache.get(key) is not None else default
        def cache_set(key, val):
            storage_cache[key] = val
            with storage_lock:
                pending_writes[key] = val
                if write_timer_active["running"]: return
                write_timer_active["running"] = True
            def _flush():
                time.sleep(0.3)
                if stop_event.is_set(): return
                with storage_lock:
                    items = list(pending_writes.items())
                    pending_writes.clear()
                    write_timer_active["running"] = False
                for k_s, v_s in items:
                    if stop_event.is_set(): break
                    try: page.client_storage.set(k_s, v_s)
                    except Exception: pass
            threading.Thread(target=_flush, daemon=True).start()

        def safe_ui_update():
            if not stop_event.is_set():
                try: page.update()
                except Exception: pass

        def close_app_cleanly(_e=None):
            stop_event.set()
            for fn in ("window_destroy", "window_close"):
                try: getattr(page, fn)()
                except Exception: pass
            try: page.window.close()
            except Exception: pass

        def on_lifecycle_change(e: ft.AppLifecycleStateChangeEvent):
            st = str(e.data).lower()
            if "detach" in st: stop_event.set()
            elif any(x in st for x in ("pause", "inactive", "hidden")): app_state["paused_in_bg"] = True
            elif "resume" in st: app_state["paused_in_bg"] = False; safe_ui_update()

        page.on_app_lifecycle_state_change = on_lifecycle_change
        page.on_disconnect = lambda _: stop_event.set()
        page.on_close = lambda _: stop_event.set()

        device_id, device_name, profiles, processed_ids, favorites_items = storage_cache["device_id"], storage_cache["device_name"], [], set(), []
        VLC_MODE_ORDER, VLC_MODE_LABELS = ["vlc_clean", "intent", "vlc_full", "http"], {"vlc_clean": "🔧 Tryb VLC: vlc:// (Czysty)", "intent": "🔧 Tryb VLC: Android Intent", "vlc_full": "🔧 Tryb VLC: vlc://http://", "http": "🔧 Tryb VLC: Standard HTTP"}
        creds = {"server": "http://", "username": "", "password": "", "m3u_url": ""}

        def has_saved_credentials():
            return bool(creds["m3u_url"].strip() or (creds["server"].strip() and creds["server"].strip() not in ("http://", "https://") and creds["username"].strip() and creds["password"]))

        def register_or_update_profile(name_hint="", srv="", usr="", pas="", m3u=""):
            nonlocal profiles
            srv_c, usr_c, m3u_c = (srv or "").strip(), (usr or "").strip(), (m3u or "").strip()
            mode = "m3u" if (m3u_c and not usr_c) else "xtream"
            if (mode == "xtream" and (not srv_c or srv_c in ("http://", "https://") or not usr_c)) or (mode == "m3u" and not m3u_c): return ""

            ex_idx = next((i for i, p in enumerate(profiles) if (mode == "xtream" and p.get("server") == srv_c and p.get("username") == usr_c) or (mode == "m3u" and p.get("m3u_url") == m3u_c)), -1)
            if ex_idx >= 0:
                p_name = name_hint or profiles[ex_idx].get("name") or f"Lista {ex_idx + 1}"
                profiles[ex_idx].update({"name": p_name, "password": pas, "mode": mode})
            else:
                p_name = name_hint or (f"{usr_c} @ {srv_c.replace('http://', '').replace('https://', '')[:22]}" if mode == "xtream" else f"Lista M3U #{len(profiles) + 1}")
                profiles = [p for p in profiles if p.get("name") != p_name]
                profiles.insert(0, {"name": p_name, "mode": mode, "server": srv_c, "username": usr_c, "password": pas, "m3u_url": m3u_c, "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")})
            cache_set("profiles", profiles)
            cache_set("active_profile_name", p_name)
            return p_name

        VISIBLE_ROWS = 9
        state = {"client": None, "kind": "live", "in_fav": False, "in_profiles": False, "in_dashboard": True, "view_mode": "dashboard", "active_cat": None, "active_series_id": "", "active_series_title": "", "active_profile_name": "", "exp_date_str": "Unlimited", "categories": {"live": [], "vod": [], "series": []}, "categories_cache": {"live": None, "vod": None, "series": None}, "streams_cache": {}, "streams": [], "episodes": [], "filtered": [], "all_built_items": [], "viewport_top": 0, "search_query": "", "only_pl": False, "now_playing": "Brak aktywnego strumienia", "playing_url": "", "current_item": None, "is_playing": False, "paused": False, "volume": 100, "muted": False, "auto_vlc": True, "auto_login": True, "is_logging_in": False, "vlc_mode": "vlc_clean", "zone": "login", "dash_idx": 0, "list_idx": 0, "right_idx": 0, "settings_idx": 0, "login_idx": 5}

        def publish_status():
            if stop_event.is_set() or app_state["paused_in_bg"] or not app_state["storage_ready"]: return
            def _worker():
                try:
                    cid = re.sub(r"[^a-zA-Z0-9_-]", "", cache_get("device_id", device_id))
                    make_session().post(f"https://ntfy.sh/iptv_v4_pro_{cid}_status", data=json.dumps({"type": "player_status", "device_name": cache_get("device_name", device_name), "device_id": cid, "ts": int(time.time() * 1000), "time_str": datetime.datetime.now().strftime("%H:%M:%S"), "state_label": ("⏸ PAUZA" if state["paused"] else "▶ ODTWARZANIE") if state["is_playing"] else "⏹ ZATRZYMANO", "now_playing": state["now_playing"], "active_server": state["active_profile_name"] if state["client"] else "Ekran logowania", "only_pl": state["only_pl"], "current_tab": "menu" if state["in_dashboard"] else ("listy" if state["in_profiles"] else ("ulubione" if state["in_fav"] else state["kind"])), "volume": state["volume"], "muted": state["muted"], "profiles": [{"name": p.get("name", "Lista"), "mode": p.get("mode", "xtream"), "host": (p.get("server") or p.get("m3u_url") or "")[:30], "added_at": p.get("added_at", ""), "active": state["client"] is not None and p.get("name") == state["active_profile_name"]} for p in profiles]}, ensure_ascii=False).encode("utf-8"), timeout=4)
                except Exception: pass
            threading.Thread(target=_worker, daemon=True).start()

        def get_current_pl_cat_ids() -> set: return state["client"].pl_cat_ids.get(state["kind"], set()) if (state.get("client") and hasattr(state["client"], "pl_cat_ids")) else set()

        def make_tv_button(text_str, on_click_fn, key_str=None, height=44, expand=False, item_ref=None):
            txt_ctrl = ft.Text(text_str, size=14, weight=ft.FontWeight.BOLD, color="white", no_wrap=True, expand=True)
            box = ft.Container(key=key_str, content=ft.Row([txt_ctrl], alignment=ft.MainAxisAlignment.START), height=height, padding=ft.padding.symmetric(horizontal=12, vertical=4), border_radius=8, bgcolor="#163828", border=ft.border.all(1, "#22543d"), alignment=ft.alignment.center_left, expand=expand, on_click=lambda _: on_click_fn())
            box.data = {"text_ctrl": txt_ctrl, "base_text": text_str, "action": on_click_fn, "item": item_ref}
            return box

        def set_tv_button_text(box, new_text):
            box.data["base_text"], box.data["text_ctrl"].value = new_text, new_text

        clock_txt, date_txt, footer_playlist_txt, footer_expire_txt = ft.Text("00:00", size=28, weight=ft.FontWeight.BOLD, color="white"), ft.Text("", size=12, color="#86efac"), ft.Text("Current Playlist: Brak", size=12, color="#86efac"), ft.Text(f"Expire: Unlimited  •  ID: {device_id}", size=12, color="#86efac")
        dash_back_hint_txt = ft.Text("Pilot: Strzałki = wybór kafelka | OK = wejdź | 2x WSTECZ = zamknij program", size=12, color="#fbbf24", weight=ft.FontWeight.BOLD)

        def update_vivo_footer():
            footer_playlist_txt.value = f"Current Playlist: {state.get('active_profile_name') or 'Domyślna'}"
            footer_expire_txt.value = f"Playlist Expire Date: {state.get('exp_date_str') or 'Unlimited'}  •  Cloud ID: {cache_get('device_id', device_id)}"

        def update_clock_loop():
            while not stop_event.is_set():
                try:
                    now = datetime.datetime.now()
                    clock_txt.value, date_txt.value = now.strftime("%H:%M"), now.strftime("%A, %d %b %Y")
                    safe_ui_update()
                except Exception: pass
                if stop_event.wait(10): break
        threading.Thread(target=update_clock_loop, daemon=True).start()

        poster_img = ft.Image(src="", width=180, height=255, fit=ft.ImageFit.COVER, border_radius=10, visible=False)
        info_badge_txt = ft.Text("PODGLĄD POZYCJI", size=11, weight=ft.FontWeight.BOLD, color="#fbbf24")
        status_info_title = ft.Text("Najedź kursorem na kanał, film lub serial", size=19, weight=ft.FontWeight.BOLD, color="#34d399")
        status_info_meta = ft.Text("", size=13, weight=ft.FontWeight.BOLD, color="#fbbf24")
        status_info_sub = ft.Text("Wystarczy podświetlić pozycję na liście po lewej stronie, aby wyświetlić EPG, ocenę oraz opis.", size=14, color="#e2e8f0", max_lines=9, overflow=ft.TextOverflow.ELLIPSIS)

        info_box_content = ft.Row([poster_img, ft.Column([ft.Container(content=info_badge_txt, bgcolor="#163828", padding=ft.padding.symmetric(horizontal=8, vertical=3), border_radius=6), status_info_title, status_info_meta, ft.Divider(height=8, color="#22543d"), status_info_sub], alignment=ft.MainAxisAlignment.START, horizontal_alignment=ft.CrossAxisAlignment.START, spacing=6, expand=True)], alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.START, spacing=16)

        settings_panel_col = ft.Column([], spacing=6, expand=True)
        settings_overlay_box = ft.Container(content=settings_panel_col, expand=True, bgcolor="#091811", border_radius=12, border=ft.border.all(2, "#ea580c"), padding=14, visible=False)
        video_placeholder = ft.Container(content=info_box_content, expand=True, bgcolor="#091811", border_radius=12, border=ft.border.all(2, "#22543d"), padding=16, visible=True)

        now_playing_txt = ft.Text("▶ Pilot: ⬆️ ⬇️ przewijaj przez całe okno | OK = Uruchom | ➡️ Prawy panel | WSTECZ = Cofnij", weight=ft.FontWeight.BOLD, size=13, color="#fbbf24")
        header_dev_lbl = ft.Text(f"📺 {device_name} [{device_id}]", size=12, color="#34d399", weight=ft.FontWeight.BOLD)
        list_header_lbl = ft.Text("📂 LISTA KATEGORII", size=13, weight=ft.FontWeight.BOLD, color="#34d399")
        status_banner = ft.Text("⏳ Inicjalizacja systemu IPTV BP...", color="#34d399", size=13)

        def trigger_focus_preview(item):
            if not isinstance(item, dict): return
            if "category_name" in item and "stream_id" not in item and "series_id" not in item:
                poster_img.visible = False
                info_badge_txt.value, status_info_title.value, status_info_meta.value, status_info_sub.value = "📁 KATEGORIA", item.get("category_name", "Kategoria"), f"Sekcja: {state['kind'].upper()}  •  Naciśnij OK, aby otworzyć", "Przewijaj listę strzałkami ⬆️ ⬇️ od samej góry do samego dołu okna.\nPo wejściu w kategorię najedź na kanał lub film, aby pobrać opis z baz danych."
                return

            state["current_item"] = item
            title = item.get("display_title") or item.get("name") or item.get("title") or "Pozycja"
            icon_url, k, fav_str, pl_str = item.get("stream_icon") or item.get("cover") or "", item.get("kind") or state["kind"], "⭐ W ulubionych  •  " if (get_item_unique_id(item) and any(get_item_unique_id(f) == get_item_unique_id(item) for f in favorites_items)) else "", "🇵🇱 PL  •  " if is_polish_item(item, get_current_pl_cat_ids()) else ""

            info_badge_txt.value = "📡 KANAŁ TV NA ŻYWO" if k == "live" else ("🎬 FILM VOD" if k == "vod" else "🎞 SERIAL")
            status_info_title.value, status_info_meta.value = title, f"{fav_str}{pl_str}Naciśnij OK, aby odtworzyć"
            poster_img.src, poster_img.visible = icon_url, bool(icon_url and icon_url.startswith("http"))
            status_info_sub.value = "⏳ Pobieranie informacji o programie / opisu fabuły..."
            app_state["preview_seq"] += 1
            cur_seq = app_state["preview_seq"]

            def _async_preview():
                time.sleep(0.16)
                if cur_seq != app_state["preview_seq"] or stop_event.is_set(): return
                if k == "live" and state.get("client"):
                    epg_list = state["client"].get_short_epg(item.get("stream_id"))
                    if cur_seq != app_state["preview_seq"]: return
                    status_info_meta.value = f"{fav_str}{pl_str}{'⏱ Archiwum Catch Up' if parse_int_safe(item.get('tv_archive'), 0) == 1 else '📡 Live TV'}"
                    status_info_sub.value = "\n\n".join(epg_list) if epg_list else "Brak aktualnych danych EPG dla tego kanału.\nNaciśnij OK, aby uruchomić transmisję."
                    safe_ui_update()
                elif k in ("vod", "series"):
                    meta = fetch_rich_media_details(title, item=item, client=state.get("client"))
                    if cur_seq != app_state["preview_seq"]: return
                    if meta:
                        if meta.get("poster"): poster_img.src, poster_img.visible = meta["poster"], True
                        status_info_meta.value = f"{fav_str}{pl_str}{f'📅 Rok: {meta['year']}   ' if meta.get('year') else ''}{f'⭐ Ocena: {meta['rating']}   ' if meta.get('rating') else ''}{f'📚 {meta['source']}' if meta.get('source') else ''}".strip()
                        status_info_sub.value = meta.get("plot") or "Brak opisu fabuły w bazach Xtream, TMDB, Wikipedia PL oraz TVMaze."
                    else: status_info_sub.value = "Naciśnij OK na pilocie, aby rozpocząć oglądanie."
                    safe_ui_update()
            threading.Thread(target=_async_preview, daemon=True).start()

        def toggle_only_pl(_e=None):
            state["only_pl"] = not state["only_pl"]
            cache_set("only_pl", state["only_pl"])
            state.update({"categories_cache": {"live": None, "vod": None, "series": None}, "list_idx": 0, "viewport_top": 0})
            now_playing_txt.value = "🇵🇱 Filtr 'Tylko Polskie' WŁĄCZONY" if state["only_pl"] else "🌍 Filtr 'Tylko Polskie' WYŁĄCZONY (Polskie na górze)"
            build_full_list_data() if not state["in_dashboard"] else safe_ui_update()
            publish_status()

        def launch_vlc_intent(raw_url: str, title: str = ""):
            if not raw_url: return
            cu, mode = raw_url.strip().splitlines()[0].strip(), state.get("vlc_mode", "vlc_clean")
            no_p, sch = re.sub(r"^https?://", "", cu, flags=re.IGNORECASE), "https" if cu.lower().startswith("https://") else "http"
            t_url = f"vlc://{no_p}" if mode == "vlc_clean" else (f"intent://{no_p}#Intent;scheme={sch};type=video/*;package=org.videolan.vlc;end" if mode == "intent" else (f"vlc://{cu}" if mode == "vlc_full" else cu))
            now_playing_txt.value = f"🚀 Uruchamianie VLC: {title or state['now_playing']}"
            safe_ui_update()
            threading.Thread(target=lambda: page.launch_url(t_url), daemon=True).start()

        def open_dashboard():
            app_state["settings_open"], settings_overlay_box.visible, video_placeholder.visible = False, False, True
            state.update({"in_dashboard": True, "view_mode": "dashboard", "zone": "dashboard"})
            update_vivo_footer()
            dash_view.visible, dash_inner_view.visible = True, False
            for idx, tile in enumerate(dash_tiles):
                foc = (state["zone"] == "dashboard" and state["dash_idx"] == idx)
                tile.bgcolor, tile.border = "#ea580c" if foc else "#112e22", ft.border.all(3 if foc else 1, "#fde047" if foc else "#22543d")
            safe_ui_update()
            publish_status()

        def make_vivo_tile(title, icon_char, on_click_fn, is_large=False):
            t_ctrl = ft.Text(title, size=16 if is_large else 13, weight=ft.FontWeight.BOLD, color="white", text_align=ft.TextAlign.CENTER)
            i_ctrl = ft.Text(icon_char, size=34 if is_large else 20, color="#fb923c" if title in ("Live TV", "Ustawienia") else "#34d399", text_align=ft.TextAlign.CENTER)
            layout = ft.Column([i_ctrl, t_ctrl], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6) if is_large else ft.Row([i_ctrl, t_ctrl], alignment=ft.MainAxisAlignment.CENTER, vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=8)
            box = ft.Container(content=layout, bgcolor="#112e22", border_radius=12, border=ft.border.all(1, "#22543d"), padding=8, expand=True, on_click=lambda _: on_click_fn())
            box.data = {"title": title, "action": on_click_fn}
            return box

        dash_tile_live = make_vivo_tile("Live TV", "📺", lambda: (state.update({"in_dashboard": False, "kind": "live", "in_fav": False, "in_profiles": False, "search_query": "", "zone": "list"}), setattr(dash_view, "visible", False), setattr(dash_inner_view, "visible", True), state.update({"view_mode": "categories", "active_cat": None, "viewport_top": 0}), build_full_list_data(), publish_status()), is_large=True)
        dash_tile_movies = make_vivo_tile("Movies", "🎬", lambda: (state.update({"in_dashboard": False, "kind": "vod", "in_fav": False, "in_profiles": False, "search_query": "", "zone": "list"}), setattr(dash_view, "visible", False), setattr(dash_inner_view, "visible", True), state.update({"view_mode": "categories", "active_cat": None, "viewport_top": 0}), build_full_list_data(), publish_status()), is_large=True)
        dash_tile_series = make_vivo_tile("Series", "🎞", lambda: (state.update({"in_dashboard": False, "kind": "series", "in_fav": False, "in_profiles": False, "search_query": "", "zone": "list"}), setattr(dash_view, "visible", False), setattr(dash_inner_view, "visible", True), state.update({"view_mode": "categories", "active_cat": None, "viewport_top": 0}), build_full_list_data(), publish_status()), is_large=True)
        dash_tile_catchup = make_vivo_tile("Catch Up", "⏱", lambda: (state.update({"in_dashboard": False, "kind": "live", "in_fav": False, "in_profiles": False, "search_query": "", "zone": "list"}), setattr(dash_view, "visible", False), setattr(dash_inner_view, "visible", True), open_category({"category_id": "__CATCHUP__", "category_name": "⏱ KANAŁY Z ARCHIWUM (CATCH UP)"}), publish_status()), is_large=False)
        dash_tile_fav = make_vivo_tile("Ulubione", "⭐", lambda: (state.update({"in_dashboard": False, "in_fav": True, "in_profiles": False, "view_mode": "streams", "active_cat": None, "list_idx": 0, "viewport_top": 0, "zone": "list"}), setattr(dash_view, "visible", False), setattr(dash_inner_view, "visible", True), build_full_list_data(), publish_status()), is_large=False)
        dash_tile_playlists = make_vivo_tile("Listy", "📋", lambda: (state.update({"in_dashboard": False, "in_fav": False, "in_profiles": True, "view_mode": "profiles", "active_cat": None, "list_idx": 0, "viewport_top": 0, "zone": "list"}), setattr(dash_view, "visible", False), setattr(dash_inner_view, "visible", True), build_full_list_data(), publish_status()), is_large=False)
        dash_tile_settings = make_vivo_tile("Ustawienia", "⚙️", lambda: (state.update({"in_dashboard": False, "kind": "live", "in_fav": False, "in_profiles": False}), setattr(dash_view, "visible", False), setattr(dash_inner_view, "visible", True), state.update({"view_mode": "categories", "active_cat": None, "viewport_top": 0}), build_full_list_data(), open_settings_menu_dlg()), is_large=False)
        dash_tiles = [dash_tile_live, dash_tile_movies, dash_tile_series, dash_tile_catchup, dash_tile_fav, dash_tile_playlists, dash_tile_settings]

        def open_category(cat_obj):
            state.update({"view_mode": "streams", "active_cat": cat_obj, "list_idx": 0, "viewport_top": 0})
            ckey = (state["kind"], str(cat_obj.get("category_id", "__ALL__")))
            if ckey in state["streams_cache"]:
                state["streams"] = state["streams_cache"][ckey]
                build_full_list_data()
                return
            for s in list_slots: s.visible = False
            list_header_lbl.value = f"⏳ Ładowanie: {cat_obj.get('category_name')}..."
            safe_ui_update()
            def _w():
                try:
                    items = state["client"].get_streams(state["kind"], cat_obj.get("category_id")) or []
                    for it in items: it["kind"] = state["kind"]
                    state["streams"] = state["streams_cache"][ckey] = items
                    build_full_list_data()
                except Exception: pass
            threading.Thread(target=_w, daemon=True).start()

        def open_settings_menu_dlg(_e=None):
            app_state["settings_open"], state["settings_idx"] = True, 0
            settings_menu_buttons.clear()
            settings_panel_col.controls.clear()

            def close_s(): app_state["settings_open"], settings_overlay_box.visible, video_placeholder.visible, state["zone"] = False, False, True, "list"; update_focus_styles()
            def wrap(fn): return lambda: (close_s(), fn())

            b_s_fav = make_tv_button("⭐ Edytuj Ulubione" , lambda: wrap(lambda: toggle_favorite()), height=38)
            b_s_pl = make_tv_button(f"🇵🇱 Filtr 'Tylko Polskie': {'WŁĄCZONY' if state['only_pl'] else 'WYŁĄCZONY'}", lambda: (toggle_only_pl(), style_settings_menu()), height=38)
            b_s_vmode = make_tv_button(VLC_MODE_LABELS[state["vlc_mode"]], lambda: (cycle_vlc_mode(), style_settings_menu()), height=38)
            b_s_close = make_tv_button("↩ Zamknij ustawienia", close_s, height=38)

            settings_menu_buttons.extend([b_s_fav, b_s_pl, b_s_vmode, b_s_close])
            settings_panel_col.controls.append(ft.Text("⚙️ MENU USTAWIEŃ", size=14, weight=ft.FontWeight.BOLD, color="#fbbf24"))
            settings_panel_col.controls.extend(settings_menu_buttons)

            def style_settings_menu():
                set_tv_button_text(b_s_pl, f"🇵🇱 Filtr 'Tylko Polskie': {'WŁĄCZONY' if state['only_pl'] else 'WYŁĄCZONY'}")
                set_tv_button_text(b_s_vmode, VLC_MODE_LABELS[state["vlc_mode"]])
                for i, b in enumerate(settings_menu_buttons):
                    foc = (i == state["settings_idx"])
                    bt = b.data["base_text"]
                    b.bgcolor, b.border = "#ea580c" if foc else "#163828", ft.border.all(3 if foc else 1, "#fde047" if foc else "#22543d")
                    b.data["text_ctrl"].color, b.data["text_ctrl"].value = "white", f"▶  {bt}" if foc else bt
                safe_ui_update()

            app_state["style_settings_fn"] = style_settings_menu
            video_placeholder.visible, settings_overlay_box.visible = False, True
            style_settings_menu()

        btn_vlc = make_tv_button("🚀 Uruchom w VLC", lambda: launch_vlc_intent(state["playing_url"], state["now_playing"]), height=42, expand=True)
        btn_settings_menu = make_tv_button("⚙️ Menu Ustawień", open_settings_menu_dlg, height=42, expand=True)
        btn_home = make_tv_button("🏠 Ekran Główny", open_dashboard, height=42, expand=True)
        right_buttons = [btn_vlc, btn_settings_menu, btn_home]

        btn_submit_login = make_tv_button("🚀 ZALOGUJ DO IPTV BP", lambda: None, height=48)
        login_items = [btn_submit_login]

        login_view = ft.Container(content=ft.Column([ft.Row([make_iptv_bp_badge(48), ft.Text("IPTV BP  •  V7.3 PRO", size=22, weight=ft.FontWeight.BOLD, color="#fb923c")], alignment=ft.MainAxisAlignment.CENTER, spacing=12), status_banner, *login_items], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=7, width=540), alignment=ft.alignment.center, expand=True, visible=True, bgcolor="#0b1f15", padding=16)
        dash_view = ft.Container(content=ft.Column([ft.Row([ft.Row([make_iptv_bp_badge(42), ft.Text("IPTV BP Player", size=19, weight=ft.FontWeight.BOLD, color="#34d399")], spacing=10), ft.Column([clock_txt, date_txt], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), ft.Column([ft.Row([dash_tile_live, dash_tile_movies, dash_tile_series], spacing=12, expand=5), ft.Row([dash_tile_catchup, dash_tile_fav, dash_tile_playlists, dash_tile_settings], spacing=12, expand=2)], spacing=10, expand=True), ft.Row([ft.Column([footer_playlist_txt, footer_expire_txt], spacing=2), dash_back_hint_txt], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)], expand=True, spacing=10), expand=True, visible=False, bgcolor="#0b1f15", padding=ft.padding.symmetric(horizontal=22, vertical=14))

        list_slots = []
        for _ in range(VISIBLE_ROWS):
            st_txt = ft.Text("", size=14, weight=ft.FontWeight.BOLD, color="#cbd5e1", no_wrap=True, expand=True)
            s_box = ft.Container(content=ft.Row([st_txt], alignment=ft.MainAxisAlignment.START), expand=True, padding=ft.padding.symmetric(horizontal=12, vertical=4), border_radius=8, bgcolor="#163828", border=ft.border.all(1, "#22543d"), alignment=ft.alignment.center_left, visible=False)
            s_box.data = {"txt": st_txt, "action": None}
            s_box.on_click = lambda _e, sb=s_box: sb.data["action"]() if sb.data["action"] else None
            list_slots.append(s_box)

        empty_list_lbl = ft.Text("Brak pozycji do wyświetlenia.", color="#94a3b8", size=14, visible=False)
        list_column = ft.Column([empty_list_lbl, *list_slots], spacing=5, expand=True)
        left_panel = ft.Container(width=450, bgcolor="#0f291d", padding=10, border_radius=10, border=ft.border.all(2, "#ea580c"), content=ft.Column([ft.Container(content=list_header_lbl, padding=ft.padding.symmetric(horizontal=10, vertical=6), bgcolor="#091811", border_radius=6), list_column], expand=True, spacing=6))
        right_panel = ft.Container(expand=True, bgcolor="#0f291d", padding=12, border_radius=10, border=ft.border.all(1, "#22543d"), content=ft.Column([ft.Row([ft.Row([make_iptv_bp_badge(32), header_dev_lbl], spacing=10), ft.Text("⬅️ Lista | ➡️ Prawy panel | WSTECZ = Cofnij", size=12, color="#86efac")], alignment=ft.MainAxisAlignment.SPACE_BETWEEN), video_placeholder, settings_overlay_box, now_playing_txt, ft.Row(right_buttons, spacing=8)], expand=True, spacing=8))
        dash_inner_view = ft.Container(content=ft.Row([left_panel, right_panel], expand=True, spacing=10), expand=True, visible=False, bgcolor="#0b1f15", padding=10)

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
                for s in list_slots: s.visible = False
                safe_ui_update()
                return

            empty_list_lbl.visible = False
            state["list_idx"] = max(0, min(state["list_idx"], total - 1))
            cur_idx, v_top = state["list_idx"], state.get("viewport_top", 0)

            if cur_idx < v_top: v_top = cur_idx
            elif cur_idx >= v_top + VISIBLE_ROWS: v_top = cur_idx - VISIBLE_ROWS + 1
            state["viewport_top"] = v_top = max(0, min(v_top, max(0, total - VISIBLE_ROWS)))

            for slot_i in range(VISIBLE_ROWS):
                item_idx, sb = v_top + slot_i, list_slots[slot_i]
                if item_idx < total:
                    entry, foc = items[item_idx], (state["zone"] == "list" and item_idx == cur_idx)
                    sb.visible, sb.bgcolor, sb.border = True, "#ea580c" if foc else "#163828", ft.border.all(3 if foc else 1, "#fde047" if foc else "#22543d")
                    sb.data["txt"].value, sb.data["txt"].size, sb.data["txt"].color = f"▶  {entry['label']}" if foc else entry["label"], 15 if foc else 14, "white" if foc else "#cbd5e1"
                    sb.data["action"] = entry["action"]
                else:
                    sb.visible, sb.data["action"] = False, None

            if state["zone"] == "list" and items[cur_idx].get("item") is not None: trigger_focus_preview(items[cur_idx]["item"])
            safe_ui_update()

        def update_focus_styles():
            if dash_view.visible:
                for idx, tile in enumerate(dash_tiles):
                    foc = (state["zone"] == "dashboard" and state["dash_idx"] == idx)
                    tile.bgcolor, tile.border = "#ea580c" if foc else "#112e22", ft.border.all(3 if foc else 1, "#fde047" if foc else "#22543d")
                safe_ui_update()
                return
            if not dash_inner_view.visible: return
            left_panel.border = ft.border.all(3 if state["zone"] == "list" else 1, "#ea580c" if state["zone"] == "list" else "#22543d")
            right_panel.border = ft.border.all(3 if state["zone"] == "right" else 1, "#ea580c" if state["zone"] == "right" else "#22543d")
            for i, b in enumerate(right_buttons):
                foc, bt = (state["zone"] == "right" and state["right_idx"] == i), b.data["base_text"]
                b.bgcolor, b.border, b.data["text_ctrl"].color, b.data["text_ctrl"].value = "#ea580c" if foc else "#163828", ft.border.all(3 if foc else 1, "#fde047" if foc else "#22543d"), "white", f"▶ {bt}" if foc else bt
            render_viewport()

        def build_full_list_data(keep_idx=False):
            prev_idx, built, v_mode, pl_ids = state["list_idx"], [], state["view_mode"], get_current_pl_cat_ids()
            if state["in_profiles"]:
                list_header_lbl.value = f"📋 ZAPISANE LISTY ({len(profiles)})"
                for idx, prof in enumerate(profiles):
                    pn, act = prof.get("name", f"Lista {idx + 1}"), prof.get("name", "") == state.get("active_profile_name")
                    built.append({"label": f"{idx + 1}. {'🟢 [AKTYWNA] ' if act else '📋 '}{pn}", "action": lambda pr=prof: None, "item": None})
                state["all_built_items"], state["list_idx"] = built, min(prev_idx if keep_idx else 0, max(0, len(built) - 1))
                if not keep_idx: state["viewport_top"] = 0
                update_focus_styles()
                return

            if v_mode == "categories":
                raw = state["categories_cache"][state["kind"]] if state["categories_cache"].get(state["kind"]) is not None else list(state["categories"].get(state["kind"], []))
                list_header_lbl.value = f"📂 KATEGORIE ({len(raw)})"
            else:
                raw = list(state["streams"])
                list_header_lbl.value = f"📺 {state.get('active_cat', {}).get('category_name', 'Lista')} ({len(raw)})"

            for i, item in enumerate(raw):
                if v_mode == "categories": built.append({"label": f"{i + 1}. 📁 {item.get('category_name', '')}", "action": lambda c=item: open_category(c), "item": item})
                else: built.append({"label": f"{i + 1}. {'⭐ ' if is_favorite(item) else ''}{'🇵🇱 ' if is_polish_item(item, pl_ids) else ''}{item.get('name') or item.get('title') or 'Pozycja'}", "action": lambda it=item: None, "item": item})

            state["all_built_items"] = built
            state["list_idx"] = min(prev_idx, max(0, len(built) - 1)) if keep_idx else 0
            if not keep_idx: state["viewport_top"] = 0
            update_focus_styles()

        def handle_universal_back():
            now = time.time()
            if now - app_state["last_back_guard"] < 0.25: return
            app_state["last_back_guard"] = now
            if app_state["settings_open"]: close_settings_panel()
            elif login_view.visible or dash_view.visible:
                if now - app_state["last_back_press_ts"] < 2.5: close_app_cleanly()
                else:
                    app_state["last_back_press_ts"] = now
                    dash_back_hint_txt.value, dash_back_hint_txt.color, status_banner.value, status_banner.color = "⚠️ Naciśnij WSTECZ jeszcze raz, aby zamknąć!", "#ef4444", "⚠️ Naciśnij WSTECZ jeszcze raz, aby zamknąć!", "#ef4444"
                    safe_ui_update()
            elif dash_inner_view.visible: open_dashboard()

        page.on_view_pop = lambda _e: (ensure_view_stack(), safe_ui_update(), handle_universal_back())
        try: page.window_prevent_close = page.window.prevent_close = True
        except Exception: pass

        def on_key_event(e: ft.KeyboardEvent):
            key, total_built = e.key, len(state["all_built_items"])
            if app_state["settings_open"] and settings_menu_buttons:
                if key == "Arrow Down": state["settings_idx"] = (state["settings_idx"] + 1) % len(settings_menu_buttons); app_state["style_settings_fn"]()
                elif key == "Arrow Up": state["settings_idx"] = (state["settings_idx"] - 1) % len(settings_menu_buttons); app_state["style_settings_fn"]()
                elif key in ("Enter", "Select", " "): settings_menu_buttons[state["settings_idx"]].data["action"]()
                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"): close_settings_panel()
                return

            if dash_view.visible:
                state["zone"] = "dashboard"
                if key == "Arrow Right" and state["dash_idx"] < len(dash_tiles) - 1: state["dash_idx"] += 1
                elif key == "Arrow Left" and state["dash_idx"] > 0: state["dash_idx"] -= 1
                elif key == "Arrow Down" and state["dash_idx"] < 3: state["dash_idx"] = min(6, state["dash_idx"] + 3)
                elif key == "Arrow Up" and state["dash_idx"] >= 3: state["dash_idx"] = max(0, state["dash_idx"] - 3)
                elif key in ("Enter", "Select", " "): dash_tiles[state["dash_idx"]].data["action"]()
                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"): handle_universal_back()
                render_dashboard_focus()
                return

            if dash_inner_view.visible:
                if key in ("Page Down", "Channel Down") and total_built > 0: state["list_idx"] = min(total_built - 1, state["list_idx"] + VISIBLE_ROWS); render_viewport()
                elif key in ("Page Up", "Channel Up") and total_built > 0: state["list_idx"] = max(0, state["list_idx"] - VISIBLE_ROWS); render_viewport()
                elif key == "Arrow Right":
                    if state["zone"] == "list": state["zone"], state["right_idx"] = "right", 0
                    elif state["zone"] == "right" and state["right_idx"] < len(right_buttons) - 1: state["right_idx"] += 1
                    update_focus_styles()
                elif key == "Arrow Left":
                    if state["zone"] == "right": state["zone"] = "list"; update_focus_styles()
                elif key == "Arrow Down":
                    if state["zone"] == "list" and total_built > 0 and state["list_idx"] < total_built - 1: state["list_idx"] += 1; render_viewport()
                    elif state["zone"] == "right" and state["right_idx"] < len(right_buttons) - 1: state["right_idx"] += 1; update_focus_styles()
                elif key == "Arrow Up":
                    if state["zone"] == "list" and total_built > 0 and state["list_idx"] > 0: state["list_idx"] -= 1; render_viewport()
                    elif state["zone"] == "right" and state["right_idx"] > 0: state["right_idx"] -= 1; update_focus_styles()
                elif key in ("Enter", "Select", " "):
                    now = time.time()
                    if now - app_state["last_ok_ts"] < 0.25: return
                    app_state["last_ok_ts"] = now
                    if state["zone"] == "list" and 0 <= state["list_idx"] < total_built: state["all_built_items"][state["list_idx"]]["action"]()
                    elif state["zone"] == "right": right_buttons[state["right_idx"]].data["action"]()
                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"): handle_universal_back()

        page.on_keyboard_event = on_key_event
        update_focus_styles()

        def deferred_cold_boot_loader():
            time.sleep(0.35)
            if stop_event.is_set(): return
            header_dev_lbl.value, status_banner.value, app_state["storage_ready"] = f"📺 {device_name} [{device_id}]", f"🟢 Zdalny Cloud aktywny — Kod ID: {device_id}", True
            update_vivo_footer()
            update_focus_styles()
            publish_status()

        threading.Thread(target=deferred_cold_boot_loader, daemon=True).start()

    except Exception as e:
        page.add(ft.Text(f"Błąd krytyczny: {e}\n{traceback.format_exc()}", color="red", size=14))
        page.update()

ft.app(target=main)
