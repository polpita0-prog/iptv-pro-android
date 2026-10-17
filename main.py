# -*- coding: utf-8 -*-
"""
IPTV Client V6.6 PRO - VIVO TV EDITION (Android TV)
Naprawiono i połączono wszystkie moduły:
1. PEŁNA OBSŁUGA ZDALNEGO POŁĄCZENIA (CLOUD NTFY.SH):
   - Przywrócono wątek nasłuchujący (cloud_listener), wysyłanie statusu (publish_status)
     oraz zdalne wgrywanie/przełączanie list i sterowanie z panelu PC/WWW.
2. INTERFEJS VIVO PLAYER + ZAAWANSOWANY BUFOR RAM (KATEGORIE I STRUMIENIE).
3. PODGLĄD EPG NA ŻYWO ORAZ OPISÓW/PLAKATÓW TMDB PO NAJECHANIU KURSOREM.
4. PRIORYTET POLSKICH KANAŁÓW I NOWOŚCI NA GÓRZE LISTY.
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
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Android TV) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

PL_REGEX = re.compile(
    r"(?:^|[\s\|\[\(\-_:•/])(PL|POL|POLAND|POLSKA|POLSKIE|POLISH|LEKTOR|DUBBING|NAPISY[\s_]*PL|PL[\s_]*SUB|VIP[\s_]*PL)(?:$|[\s\|\]\)\-_:•/])"
    r"|[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]"
    r"|\b(tvp|polsat|tvn|canal\+|c\+|ale\s*kino|kino\s*polska|planete\+|domo\+|kuchnia\+|teletoon\+|minimini\+|ttv|puls|fokus\s*tv|eska|polo\s*tv|trwam|republika|wpolsce|wydarzenia\s*24|stopklatka|zoom\s*tv|wp\s*tv|nowa\s*tv|metro\s*tv|eleven\s*sports|polsat\s*sport|motowizja|stars\s*tv|kino\s*tv|gametoon|active\s*family|romance\s*tv|13\s*ulica|sci\s*fi|paramount|comedy\s*central|national\s*geographic|discovery|animal\s*planet|tlc|id\s*investigation|hbo|cinemax|filmbox|axn|fox|epic\s*drama|bbc\s*brit|bbc\s*earth|bbc\s*first|bbc\s*lifestyle|viasat|history|crime\s*investigation|sundance|red\s*carpet|antena\s*hd|tv\s*okazje|super\s*polsat|tv4|tv6|polsat\s*news|tvn24|tvp\s*info|biznes24|news24|pogoda24|4fun|vox\s*music|disco\s*polo|power\s*tv|nuta\s*tv|mixtape|hip\s*hop\s*tv)\b",
    re.IGNORECASE,
)


def is_polish_text(text: str) -> bool:
    if not text:
        return False
    return bool(PL_REGEX.search(str(text)))


def is_polish_item(item: dict, pl_cat_ids: set = None) -> bool:
    if not isinstance(item, dict):
        return False
    if pl_cat_ids and str(item.get("category_id", "")) in pl_cat_ids:
        return True
    fields = (
        item.get("name"),
        item.get("title"),
        item.get("category_name"),
        item.get("group"),
    )
    return any(is_polish_text(f) for f in fields if f)


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
    pl_cats = []
    other_cats = []
    for c in categories:
        if is_polish_text(c.get("category_name", "")):
            pl_cats.append(c)
        else:
            other_cats.append(c)
    return pl_cats + other_cats


def sort_streams_smart(
    items: list,
    kind: str,
    force_newest_only: bool = False,
    pl_cat_ids: set = None,
) -> list:
    if not items:
        return []

    if kind == "live" and not force_newest_only:
        pl_list = []
        other_list = []
        for it in items:
            if is_polish_item(it, pl_cat_ids):
                pl_list.append(it)
            else:
                other_list.append(it)
        return pl_list + other_list

    def _vod_series_key(it):
        pl_flag = 1 if is_polish_item(it, pl_cat_ids) else 0
        added_ts = get_added_timestamp(it)
        num_id = get_numeric_stream_id(it)
        return (pl_flag, added_ts, num_id)

    return sorted(items, key=_vod_series_key, reverse=True)


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
        username = (qs.get("username") or qs.get("user") or [None])[0]
        password = (qs.get("password") or qs.get("pass") or [None])[0]
        if username and password:
            server = f"{parsed.scheme}://{parsed.netloc}"
            return server, username, password
    except Exception:
        pass
    return None


def clean_media_title(title):
    if not title:
        return ""
    clean = re.sub(r"^(\|?[A-Za-z0-9]{2,5}\|?\s*[:|\-]+\s*)+", "", title)
    clean = re.sub(r"\[.*?\]", "", clean)
    clean = re.sub(r"\s*[Ss]\d{1,2}\s*[Ee]\d{1,3}.*$", "", clean)
    clean = re.sub(r"\s*\(\d{4}\).*$", "", clean)
    clean = re.sub(
        r"\b(FHD|UHD|4K|HD|1080p|720p|HEVC|H265|LEKTOR|DUBBING|NAPISY|PL|EN|FR)\b.*$",
        "",
        clean,
        flags=re.IGNORECASE,
    )
    return clean.strip(" -|_:")


tmdb_memory_cache = {}
epg_memory_cache = {}


def fetch_tmdb_details(title):
    clean_title = clean_media_title(title)
    if not clean_title:
        return None
    if clean_title.lower() in tmdb_memory_cache:
        return tmdb_memory_cache[clean_title.lower()]
    try:
        url = (
            f"https://api.themoviedb.org/3/search/multi"
            f"?api_key={TMDB_API_KEY}&query={requests.utils.quote(clean_title)}&language=pl-PL"
        )
        r = requests.get(url, timeout=4)
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                best = results[0]
                poster_path = best.get("poster_path")
                release = (
                    best.get("release_date") or best.get("first_air_date") or ""
                )
                res = {
                    "poster": (
                        f"https://image.tmdb.org/t/p/w500{poster_path}"
                        if poster_path
                        else None
                    ),
                    "plot": best.get("overview") or "",
                    "rating": (
                        str(round(best.get("vote_average", 0), 1))
                        if best.get("vote_average")
                        else ""
                    ),
                    "year": release[:4] if release else "",
                }
                tmdb_memory_cache[clean_title.lower()] = res
                return res
    except Exception:
        pass
    return None


class XtreamClient:

    def __init__(self, server, username, password):
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.session = make_session()
        self.user_info = {}
        self.pl_cat_ids = {"live": set(), "vod": set(), "series": set()}

    def _api(self, params, timeout=15):
        url = f"{self.server}/player_api.php"
        full_params = {"username": self.username, "password": self.password}
        full_params.update(params)
        r = self.session.get(url, params=full_params, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def login(self):
        data = self._api({})
        self.user_info = data.get("user_info", {})
        if (self.user_info or {}).get("auth") != 1:
            raise ValueError("Nieprawidłowy login lub hasło.")
        return self.user_info

    def get_categories(self, kind):
        action = {
            "live": "get_live_categories",
            "vod": "get_vod_categories",
            "series": "get_series_categories",
        }[kind]
        raw_cats = self._api({"action": action}) or []
        self.pl_cat_ids[kind] = {
            str(c.get("category_id", ""))
            for c in raw_cats
            if is_polish_text(c.get("category_name", ""))
        }
        return sort_categories_pl_first(raw_cats)

    def get_streams(self, kind, category_id=None):
        action = {
            "live": "get_live_streams",
            "vod": "get_vod_streams",
            "series": "get_series",
        }[kind]
        params = {"action": action}
        if category_id and category_id not in ("__ALL__", "__RECENT__", "__CATCHUP__"):
            params["category_id"] = category_id
        raw_streams = self._api(params, timeout=24) or []
        if category_id == "__CATCHUP__":
            raw_streams = [
                s for s in raw_streams if parse_int_safe(s.get("tv_archive"), 0) == 1
            ]
        return sort_streams_smart(
            raw_streams,
            kind,
            force_newest_only=(category_id == "__RECENT__"),
            pl_cat_ids=self.pl_cat_ids.get(kind, set()),
        )

    def get_series_info(self, series_id):
        return self._api({"action": "get_series_info", "series_id": series_id})

    def get_short_epg(self, stream_id):
        if not stream_id:
            return []
        now_ts = time.time()
        cached = epg_memory_cache.get(str(stream_id))
        if cached and (now_ts - cached[0] < 120):
            return cached[1]
        try:
            data = self._api(
                {"action": "get_short_epg", "stream_id": stream_id, "limit": 2},
                timeout=5,
            )
            listings = data.get("epg_listings", []) if isinstance(data, dict) else []
            parsed = []
            for el in listings[:2]:
                title = decode_b64_safe(el.get("title", ""))
                desc = decode_b64_safe(el.get("description", ""))
                start_t = (el.get("start") or "")[11:16]
                end_t = (el.get("end") or "")[11:16]
                if title:
                    parsed.append(f"🕒 {start_t}-{end_t} | {title}\n{desc}".strip())
            epg_memory_cache[str(stream_id)] = (now_ts, parsed)
            return parsed
        except Exception:
            return []

    def build_stream_url(self, kind, stream_id, container_ext="ts", item=None):
        if item and item.get("direct_url"):
            return item["direct_url"]
        if kind == "live":
            return f"{self.server}/live/{self.username}/{self.password}/{stream_id}.{container_ext}"
        if kind == "vod":
            return f"{self.server}/movie/{self.username}/{self.password}/{stream_id}.{container_ext}"
        if kind == "series":
            return f"{self.server}/series/{self.username}/{self.password}/{stream_id}.{container_ext}"
        return ""


class M3UClient:

    def __init__(self, source):
        self.source = source.strip()
        self.server = "Lista M3U"
        self.username = "M3U Playlist"
        self.user_info = {"auth": 1, "exp_date": None}
        self.session = make_session()
        self.categories = {"live": [], "vod": [], "series": []}
        self.streams_by_cat = {"live": {}, "vod": {}, "series": {}}
        self.pl_cat_ids = {"live": set(), "vod": set(), "series": set()}
        self.series_map = {}
        self.direct_urls = {}

    def login(self):
        self.categories = {"live": [], "vod": [], "series": []}
        self.streams_by_cat = {"live": {}, "vod": {}, "series": {}}
        self.pl_cat_ids = {"live": set(), "vod": set(), "series": set()}
        self.series_map = {}
        self.direct_urls = {}
        r = self.session.get(self.source, timeout=25)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        self._parse_m3u(r.text)
        return self.user_info

    def _classify_entry(self, url, group, name):
        lower_url = url.lower()
        lower_group = group.lower()
        ep_match = re.search(r"[Ss](\d{1,2})\s*[Ee](\d{1,3})", name)
        if (
            "/series/" in lower_url
            or ep_match
            or any(k in lower_group for k in ["seriale", "series", "serial"])
        ):
            return "series", ep_match
        if (
            "/movie/" in lower_url
            or lower_url.endswith((".mp4", ".mkv", ".avi"))
            or any(
                k in lower_group
                for k in ["vod", "filmy", "movies", "film", "kino"]
            )
        ):
            return "vod", None
        return "live", None

    def _parse_m3u(self, text):
        cat_sets = {"live": {}, "vod": {}, "series": {}}
        series_groups = {}
        attr_re = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')
        extinf_line = None
        seq_id = 1

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                extinf_line = line
                continue
            if line.startswith("#"):
                continue

            url = line
            attrs = dict(attr_re.findall(extinf_line)) if extinf_line else {}
            name = (
                extinf_line.split(",", 1)[1].strip()
                if (extinf_line and "," in extinf_line)
                else f"Kanał {seq_id}"
            )
            group = (attrs.get("group-title") or "Bez kategorii").strip()
            logo = attrs.get("tvg-logo") or ""
            catchup = 1 if (attrs.get("catchup") or attrs.get("tvg-rec")) else 0
            kind, ep_match = self._classify_entry(url, group, name)

            if group not in cat_sets[kind]:
                cat_id = f"m3u_{kind}_{len(cat_sets[kind]) + 1}"
                cat_sets[kind][group] = cat_id
                self.categories[kind].append(
                    {"category_id": cat_id, "category_name": group}
                )
                if is_polish_text(group):
                    self.pl_cat_ids[kind].add(cat_id)
                self.streams_by_cat[kind][cat_id] = []
            else:
                cat_id = cat_sets[kind][group]

            item_id = f"m3u_item_{seq_id}"
            cur_num = seq_id
            seq_id += 1
            self.direct_urls[item_id] = url

            if kind in ("live", "vod"):
                self.streams_by_cat[kind][cat_id].append({
                    "stream_id": item_id,
                    "category_id": cat_id,
                    "added": str(cur_num),
                    "name": name,
                    "group": group,
                    "stream_icon": logo,
                    "direct_url": url,
                    "tv_archive": catchup,
                    "kind": kind,
                })
            else:
                season_num = str(int(ep_match.group(1))) if ep_match else "1"
                ep_num = int(ep_match.group(2)) if ep_match else 1
                base_title = (
                    name[: ep_match.start()].strip(" -|:") if ep_match else name
                )
                series_key = (cat_id, base_title.lower())
                if series_key not in series_groups:
                    series_id = f"m3u_ser_{cur_num}"
                    series_groups[series_key] = series_id
                    self.streams_by_cat["series"][cat_id].append({
                        "series_id": series_id,
                        "category_id": cat_id,
                        "last_modified": str(cur_num),
                        "name": base_title,
                        "group": group,
                        "cover": logo,
                        "kind": "series",
                    })
                    self.series_map[series_id] = {"episodes": {}}
                else:
                    series_id = series_groups[series_key]
                eps = self.series_map[series_id]["episodes"].setdefault(
                    season_num, []
                )
                eps.append({
                    "id": item_id,
                    "episode_num": ep_num,
                    "title": name,
                    "direct_url": url,
                })
            extinf_line = None

        for k in ("live", "vod", "series"):
            self.categories[k] = sort_categories_pl_first(self.categories[k])

    def get_categories(self, kind):
        return self.categories.get(kind, [])

    def get_streams(self, kind, category_id=None):
        cat_map = self.streams_by_cat.get(kind, {})
        pl_ids = self.pl_cat_ids.get(kind, set())
        if not category_id or category_id in ("__ALL__", "__RECENT__", "__CATCHUP__"):
            res = []
            for v in cat_map.values():
                res.extend(v)
            if category_id == "__CATCHUP__":
                res = [s for s in res if parse_int_safe(s.get("tv_archive"), 0) == 1]
            return sort_streams_smart(
                res,
                kind,
                force_newest_only=(category_id == "__RECENT__"),
                pl_cat_ids=pl_ids,
            )
        raw_list = cat_map.get(category_id, [])
        return sort_streams_smart(raw_list, kind, pl_cat_ids=pl_ids)

    def get_series_info(self, series_id):
        return self.series_map.get(series_id, {"episodes": {}})

    def get_short_epg(self, stream_id):
        return []

    def build_stream_url(self, kind, stream_id, container_ext="ts", item=None):
        if item and item.get("direct_url"):
            return item["direct_url"]
        return self.direct_urls.get(stream_id, "")


def get_item_unique_id(item):
    if not isinstance(item, dict):
        return ""
    val = (
        item.get("stream_id")
        or item.get("series_id")
        or item.get("id")
        or item.get("category_id")
        or ""
    )
    return str(val)


def build_special_top_categories(kind: str, raw_cats: list) -> list:
    top = [
        {
            "category_id": "__ALL__",
            "category_name": "🔥 WSZYSTKIE (🇵🇱 Polskie na górze)",
        }
    ]
    if kind == "live":
        top.append(
            {
                "category_id": "__CATCHUP__",
                "category_name": "⏱ KANAŁY Z ARCHIWUM (CATCH UP)",
            }
        )
    if kind in ("vod", "series"):
        top.append(
            {
                "category_id": "__RECENT__",
                "category_name": "🆕 OSTATNIO DODANE (NOWOŚCI NA GÓRZE)",
            }
        )
    return top + raw_cats


def main(page: ft.Page):
    try:
        page.title = "IPTV V6.6 PRO - VIVO TV EDITION"
        page.theme_mode = ft.ThemeMode.DARK
        page.bgcolor = "#0b1f15"
        page.padding = 0

        stop_event = threading.Event()
        app_state = {"dialog_open": False, "paused_in_bg": False, "preview_seq": 0}
        storage_lock = threading.Lock()
        storage_cache = {}
        pending_writes = {}
        write_timer_active = {"running": False}

        def init_storage_cache():
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
                "last_session": {},
            }
            for k, d in keys_defaults.items():
                try:
                    val = page.client_storage.get(k)
                    storage_cache[k] = val if val is not None else d
                except Exception:
                    storage_cache[k] = d

        init_storage_cache()

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

            def _flush_writer():
                time.sleep(0.25)
                if stop_event.is_set():
                    return
                with storage_lock:
                    items_to_save = list(pending_writes.items())
                    pending_writes.clear()
                    write_timer_active["running"] = False

                for k_save, v_save in items_to_save:
                    if stop_event.is_set():
                        break
                    try:
                        page.client_storage.set(k_save, v_save)
                    except Exception:
                        pass

            threading.Thread(target=_flush_writer, daemon=True).start()

        def safe_ui_update():
            if stop_event.is_set():
                return
            try:
                page.update()
            except Exception:
                pass

        def close_app_cleanly(_e=None):
            stop_event.set()
            try:
                page.window_close()
            except Exception:
                pass
            try:
                page.window.close()
            except Exception:
                pass

        def on_lifecycle_change(e: ft.AppLifecycleStateChangeEvent):
            st_str = str(e.data).lower()
            if "detach" in st_str:
                stop_event.set()
            elif "pause" in st_str or "inactive" in st_str or "hidden" in st_str:
                app_state["paused_in_bg"] = True
            elif "resume" in st_str:
                app_state["paused_in_bg"] = False
                safe_ui_update()

        page.on_app_lifecycle_state_change = on_lifecycle_change
        page.on_disconnect = lambda _: stop_event.set()
        page.on_close = lambda _: stop_event.set()

        device_id = cache_get("device_id")
        if not device_id:
            device_id = f"android-{uuid.uuid4().hex[:6]}"
            cache_set("device_id", device_id)

        device_name = cache_get("device_name", "Android TV Box")
        profiles = list(cache_get("profiles", []))
        processed_ids = set(cache_get("processed_ids", []))
        auto_vlc_saved = cache_get("auto_vlc", True)
        auto_login_saved = cache_get("auto_login", True)
        only_pl_saved = cache_get("only_pl", False)
        saved_vlc_mode = cache_get("vlc_mode", "vlc_clean")
        favorites_items = list(cache_get("favorites_v2", []))

        VLC_MODE_ORDER = ["vlc_clean", "intent", "vlc_full", "http"]
        VLC_MODE_LABELS = {
            "vlc_clean": "🔧 VLC: vlc://",
            "intent": "🔧 VLC: Intent",
            "vlc_full": "🔧 VLC: vlc://http",
            "http": "🔧 VLC: HTTP",
        }
        if saved_vlc_mode not in VLC_MODE_LABELS:
            saved_vlc_mode = "vlc_clean"

        creds = {
            "server": cache_get("server", "http://"),
            "username": cache_get("username", ""),
            "password": cache_get("password", ""),
            "m3u_url": cache_get("m3u_url", ""),
        }

        def has_saved_credentials():
            m3u_ok = bool((creds.get("m3u_url") or "").strip())
            srv = (creds.get("server") or "").strip()
            usr = (creds.get("username") or "").strip()
            pas = creds.get("password") or ""
            return m3u_ok or bool(srv and srv not in ("http://", "https://") and usr and pas)

        def register_or_update_profile(name_hint="", srv="", usr="", pas="", m3u=""):
            nonlocal profiles
            srv_c = (srv or "").strip()
            usr_c = (usr or "").strip()
            m3u_c = (m3u or "").strip()
            mode = "m3u" if (m3u_c and not usr_c) else "xtream"

            if mode == "xtream" and (not srv_c or srv_c in ("http://", "https://") or not usr_c):
                return ""
            if mode == "m3u" and not m3u_c:
                return ""

            existing_idx = -1
            for idx, p in enumerate(profiles):
                if mode == "xtream" and p.get("server") == srv_c and p.get("username") == usr_c:
                    existing_idx = idx
                    break
                if mode == "m3u" and p.get("m3u_url") == m3u_c:
                    existing_idx = idx
                    break

            if existing_idx >= 0:
                prof_name = name_hint or profiles[existing_idx].get("name") or f"Lista {existing_idx + 1}"
                profiles[existing_idx]["name"] = prof_name
                profiles[existing_idx]["password"] = pas
                profiles[existing_idx]["mode"] = mode
            else:
                host_short = (
                    srv_c.replace("http://", "").replace("https://", "")[:22]
                    if mode == "xtream"
                    else "M3U"
                )
                prof_name = name_hint or (f"{usr_c} @ {host_short}" if mode == "xtream" else f"Lista M3U #{len(profiles) + 1}")
                profiles = [p for p in profiles if p.get("name") != prof_name]
                profiles.insert(0, {
                    "name": prof_name,
                    "mode": mode,
                    "server": srv_c,
                    "username": usr_c,
                    "password": pas,
                    "m3u_url": m3u_c,
                    "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                })

            cache_set("profiles", profiles)
            cache_set("active_profile_name", prof_name)
            return prof_name

        if has_saved_credentials() and not profiles:
            register_or_update_profile(
                cache_get("active_profile_name", ""),
                creds["server"],
                creds["username"],
                creds["password"],
                creds["m3u_url"],
            )

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
            "active_profile_name": cache_get("active_profile_name", ""),
            "exp_date_str": "Unlimited",
            "categories": {"live": [], "vod": [], "series": []},
            "categories_cache": {"live": None, "vod": None, "series": None},
            "streams_cache": {},
            "streams": [],
            "episodes": [],
            "filtered": [],
            "search_query": "",
            "only_pl": bool(only_pl_saved),
            "now_playing": "Brak aktywnego strumienia",
            "playing_url": "",
            "current_item": None,
            "is_playing": False,
            "paused": False,
            "volume": 100,
            "muted": False,
            "auto_vlc": bool(auto_vlc_saved),
            "auto_login": bool(auto_login_saved),
            "is_logging_in": False,
            "vlc_mode": saved_vlc_mode,
            "zone": "dashboard",
            "dash_idx": 0,
            "list_idx": 0,
            "right_idx": 0,
            "login_idx": 5 if has_saved_credentials() else 1,
            "page_offset": 0,
        }

        PAGE_SIZE = 50

        # =====================================================================
        # RAPORTOWANIE STATUSU DO CHMURY (NTFY.SH)
        # =====================================================================
        def publish_status():
            if stop_event.is_set() or app_state["paused_in_bg"]:
                return

            def _worker():
                try:
                    local_session = make_session()
                    clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", cache_get("device_id", device_id))
                    status_topic = f"iptv_v4_pro_{clean_id}_status"
                    if state["is_playing"]:
                        st_lbl = "⏸ PAUZA" if state["paused"] else "▶ ODTWARZANIE"
                    else:
                        st_lbl = "⏹ ZATRZYMANO"

                    srv_txt = (
                        state["active_profile_name"]
                        if state["client"]
                        else "Ekran logowania"
                    )
                    prof_summary = []
                    for p in profiles:
                        prof_summary.append({
                            "name": p.get("name", "Lista"),
                            "mode": p.get("mode", "xtream"),
                            "host": (p.get("server") or p.get("m3u_url") or "")[:30],
                            "added_at": p.get("added_at", ""),
                            "active": (
                                state["client"] is not None
                                and p.get("name") == state["active_profile_name"]
                            ),
                        })

                    snapshot = {
                        "type": "player_status",
                        "device_name": cache_get("device_name", device_name),
                        "device_id": clean_id,
                        "ts": int(time.time() * 1000),
                        "time_str": datetime.datetime.now().strftime("%H:%M:%S"),
                        "state_label": st_lbl,
                        "now_playing": state["now_playing"],
                        "active_server": srv_txt,
                        "only_pl": state["only_pl"],
                        "current_tab": (
                            "menu"
                            if state["in_dashboard"]
                            else ("listy" if state["in_profiles"] else ("ulubione" if state["in_fav"] else state["kind"]))
                        ),
                        "volume": state["volume"],
                        "muted": state["muted"],
                        "profiles": prof_summary,
                    }
                    local_session.post(
                        f"https://ntfy.sh/{status_topic}",
                        data=json.dumps(snapshot, ensure_ascii=False).encode("utf-8"),
                        timeout=4,
                    )
                except Exception:
                    pass

            threading.Thread(target=_worker, daemon=True).start()

        def get_current_pl_cat_ids() -> set:
            cl = state.get("client")
            if cl and hasattr(cl, "pl_cat_ids"):
                return cl.pl_cat_ids.get(state["kind"], set())
            return set()

        def make_tv_button(text_str, on_click_fn, key_str=None, height=46, expand=False, item_ref=None):
            txt_ctrl = ft.Text(
                text_str,
                size=14,
                weight=ft.FontWeight.BOLD,
                color="white",
                no_wrap=True,
                expand=True,
            )
            box = ft.Container(
                key=key_str,
                content=ft.Row([txt_ctrl], alignment=ft.MainAxisAlignment.START),
                height=height,
                padding=ft.padding.symmetric(horizontal=12, vertical=6),
                border_radius=8,
                bgcolor="#163828",
                border=ft.border.all(1, "#22543d"),
                alignment=ft.alignment.center_left,
                expand=expand,
                on_click=lambda _: on_click_fn(),
            )
            box.data = {
                "text_ctrl": txt_ctrl,
                "base_text": text_str,
                "action": on_click_fn,
                "item": item_ref,
            }
            return box

        def set_tv_button_text(box, new_text):
            box.data["base_text"] = new_text
            box.data["text_ctrl"].value = new_text

        def toggle_only_pl(_e=None):
            state["only_pl"] = not state["only_pl"]
            cache_set("only_pl", state["only_pl"])
            state["categories_cache"] = {"live": None, "vod": None, "series": None}
            set_tv_button_text(
                btn_only_pl,
                f"🇵🇱 Tylko PL: {'WŁ.' if state['only_pl'] else 'WYŁ.'}",
            )
            state["page_offset"] = 0
            state["list_idx"] = 0
            if not state["in_dashboard"]:
                render_list()
            else:
                safe_ui_update()
            publish_status()

        def cycle_vlc_mode(_e=None):
            cur = state.get("vlc_mode", "vlc_clean")
            idx = (VLC_MODE_ORDER.index(cur) + 1) % len(VLC_MODE_ORDER) if cur in VLC_MODE_ORDER else 0
            new_mode = VLC_MODE_ORDER[idx]
            state["vlc_mode"] = new_mode
            cache_set("vlc_mode", new_mode)
            set_tv_button_text(btn_vlc_mode, VLC_MODE_LABELS[new_mode])
            now_playing_txt.value = f"🔧 Zmieniono tryb odtwarzacza na: {VLC_MODE_LABELS[new_mode]}"
            safe_ui_update()

        def is_favorite(item):
            uid = get_item_unique_id(item)
            if not uid:
                return False
            return any(get_item_unique_id(f) == uid for f in favorites_items)

        def toggle_favorite(_e=None):
            nonlocal favorites_items
            cur_item = state.get("current_item")
            if not cur_item:
                return
            uid = get_item_unique_id(cur_item)
            if not uid:
                return
            if is_favorite(cur_item):
                favorites_items = [f for f in favorites_items if get_item_unique_id(f) != uid]
            else:
                item_copy = dict(cur_item)
                item_copy["kind"] = cur_item.get("kind") or state["kind"]
                favorites_items.insert(0, item_copy)
            cache_set("favorites_v2", favorites_items)
            set_tv_button_text(
                btn_fav_toggle,
                "⭐ Usuń z Ulub." if is_favorite(cur_item) else "⭐ Dodaj do Ulub.",
            )
            render_list()

        def delete_saved_profile(prof_name):
            nonlocal profiles
            profiles = [p for p in profiles if p.get("name") != prof_name]
            cache_set("profiles", profiles)
            refresh_login_button_labels()
            now_playing_txt.value = f"🗑 Usunięto listę: {prof_name}"
            if state["in_profiles"]:
                render_list()
            else:
                safe_ui_update()
            publish_status()

        # Widgety zegara i stopki Vivo
        clock_txt = ft.Text("00:00", size=32, weight=ft.FontWeight.BOLD, color="white")
        date_txt = ft.Text("Saturday, 17 Oct 2026", size=13, color="#86efac")
        footer_playlist_txt = ft.Text("Current Playlist: Brak", size=12, color="#86efac")
        footer_expire_txt = ft.Text(f"Playlist Expire Date: Unlimited  •  ID: {device_id}", size=12, color="#86efac")

        def update_vivo_footer():
            cur_id = cache_get("device_id", device_id)
            footer_playlist_txt.value = f"Current Playlist: {state.get('active_profile_name') or 'Domyślna'}"
            footer_expire_txt.value = f"Playlist Expire Date: {state.get('exp_date_str') or 'Unlimited'}  •  Cloud ID: {cur_id}"

        def update_clock_loop():
            while not stop_event.is_set():
                try:
                    now = datetime.datetime.now()
                    clock_txt.value = now.strftime("%H:%M")
                    date_txt.value = now.strftime("%A, %d %b %Y")
                    safe_ui_update()
                except Exception:
                    pass
                if stop_event.wait(10):
                    break

        threading.Thread(target=update_clock_loop, daemon=True).start()

        # Główne kafelki pulpitu Vivo
        def make_vivo_tile(title, icon_char, on_click_fn, is_large=False):
            t_ctrl = ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color="white", text_align=ft.TextAlign.CENTER)
            i_ctrl = ft.Text(icon_char, size=38, color="#34d399", text_align=ft.TextAlign.CENTER)
            box = ft.Container(
                content=ft.Column([i_ctrl, t_ctrl], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6),
                bgcolor="#112e22",
                border_radius=12,
                border=ft.border.all(1, "#22543d"),
                padding=10,
                expand=is_large,
                height=160 if is_large else 75,
                on_click=lambda _: on_click_fn(),
            )
            box.data = {"title": title, "action": on_click_fn}
            return box

        def open_dashboard():
            state["in_dashboard"] = True
            state["view_mode"] = "dashboard"
            state["zone"] = "dashboard"
            update_vivo_footer()
            dash_view.visible = True
            dash_inner_view.visible = False
            render_dashboard_focus()
            safe_ui_update()
            publish_status()

        def open_section(kind):
            state["in_dashboard"] = False
            state["kind"] = kind
            state["in_fav"] = False
            state["in_profiles"] = False
            state["search_query"] = ""
            state["zone"] = "list"
            dash_view.visible = False
            dash_inner_view.visible = True
            back_to_cats()
            publish_status()

        def open_catchup_section():
            state["in_dashboard"] = False
            state["kind"] = "live"
            state["in_fav"] = False
            state["in_profiles"] = False
            state["search_query"] = ""
            state["zone"] = "list"
            dash_view.visible = False
            dash_inner_view.visible = True
            open_category({
                "category_id": "__CATCHUP__",
                "category_name": "⏱ KANAŁY Z ARCHIWUM (CATCH UP)",
            })
            publish_status()

        def open_favorites_section():
            state["in_dashboard"] = False
            state["in_fav"] = True
            state["in_profiles"] = False
            state["view_mode"] = "streams"
            state["active_cat"] = None
            state["page_offset"] = 0
            state["list_idx"] = 0
            state["zone"] = "list"
            dash_view.visible = False
            dash_inner_view.visible = True
            render_list()
            publish_status()

        def open_profiles_section():
            state["in_dashboard"] = False
            state["in_fav"] = False
            state["in_profiles"] = True
            state["view_mode"] = "profiles"
            state["active_cat"] = None
            state["page_offset"] = 0
            state["list_idx"] = 0
            state["zone"] = "list"
            dash_view.visible = False
            dash_inner_view.visible = True
            render_list()
            publish_status()

        dash_tile_live = make_vivo_tile("Live TV", "📺", lambda: open_section("live"), is_large=True)
        dash_tile_movies = make_vivo_tile("Movies", "🎬", lambda: open_section("vod"), is_large=True)
        dash_tile_series = make_vivo_tile("Series", "🎞", lambda: open_section("series"), is_large=True)
        dash_tile_catchup = make_vivo_tile("Catch Up", "⏱", open_catchup_section, is_large=True)
        dash_tile_fav = make_vivo_tile("Favorites", "⭐", open_favorites_section, is_large=True)
        dash_tile_playlists = make_vivo_tile("Playlists", "📋", open_profiles_section, is_large=True)

        dash_tiles = [
            dash_tile_live,
            dash_tile_movies,
            dash_tile_series,
            dash_tile_catchup,
            dash_tile_fav,
            dash_tile_playlists,
        ]

        def render_dashboard_focus():
            for idx, tile in enumerate(dash_tiles):
                is_foc = (state["zone"] == "dashboard" and state["dash_idx"] == idx)
                if is_foc:
                    tile.bgcolor = "#059669"
                    tile.border = ft.border.all(3, "#fbbf24")
                else:
                    tile.bgcolor = "#112e22"
                    tile.border = ft.border.all(1, "#22543d")
            safe_ui_update()

        # Elementy prawego panelu podglądu (Okładka + EPG + Opis)
        poster_img = ft.Image(
            src="",
            width=150,
            height=215,
            fit=ft.ImageFit.COVER,
            border_radius=8,
            visible=False,
        )
        status_info_title = ft.Text(
            "Wybierz kanał lub film z listy",
            size=17,
            weight=ft.FontWeight.BOLD,
            color="#34d399",
            text_align=ft.TextAlign.LEFT,
        )
        status_info_sub = ft.Text(
            "Najedź kursorem na pozycję, aby zobaczyć EPG, okładkę lub opis TMDB.\nNaciśnij OK, aby uruchomić odtwarzanie.",
            size=13,
            color="#cbd5e1",
            text_align=ft.TextAlign.LEFT,
            max_lines=7,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        video_placeholder = ft.Container(
            content=ft.Row(
                [
                    poster_img,
                    ft.Column(
                        [status_info_title, status_info_sub],
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.START,
                        spacing=8,
                        expand=True,
                    ),
                ],
                alignment=ft.MainAxisAlignment.START,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=16,
            ),
            expand=True,
            bgcolor="#091811",
            border_radius=8,
            border=ft.border.all(1, "#163828"),
            padding=16,
        )

        now_playing_txt = ft.Text(
            "▶ Pilot TV: ⬅️ ➡️ zmiana panelu | ⬆️ ⬇️ góra/dół | OK wybierz | WSTECZ cofnij",
            weight=ft.FontWeight.BOLD,
            size=13,
            color="#fbbf24",
        )
        header_dev_lbl = ft.Text(f"📺 {device_name} [{device_id}]", size=12, color="#34d399", weight=ft.FontWeight.BOLD)
        list_header_lbl = ft.Text("📂 LISTA KATEGORII", size=13, weight=ft.FontWeight.BOLD, color="#34d399")
        list_view = ft.ListView(expand=True, spacing=6, padding=4, auto_scroll=False)
        status_banner = ft.Text(f"🟢 Zdalny Cloud aktywny — Kod ID: {device_id}", color="#34d399", size=13)

        def trigger_focus_preview(item):
            if not isinstance(item, dict) or ("category_name" in item and "stream_id" not in item and "series_id" not in item):
                return
            state["current_item"] = item
            set_tv_button_text(
                btn_fav_toggle,
                "⭐ Usuń z Ulub." if is_favorite(item) else "⭐ Dodaj do Ulub.",
            )
            title = item.get("display_title") or item.get("name") or item.get("title") or "Pozycja"
            icon_url = item.get("stream_icon") or item.get("cover") or ""
            k = item.get("kind") or state["kind"]

            status_info_title.value = f"📺 {title}" if k == "live" else f"🎬 {title}"
            if icon_url and icon_url.startswith("http"):
                poster_img.src = icon_url
                poster_img.visible = True
            else:
                poster_img.visible = False

            app_state["preview_seq"] += 1
            cur_seq = app_state["preview_seq"]

            def _async_preview():
                time.sleep(0.35)
                if cur_seq != app_state["preview_seq"] or stop_event.is_set():
                    return
                if k == "live" and state.get("client"):
                    epg_list = state["client"].get_short_epg(item.get("stream_id"))
                    if cur_seq != app_state["preview_seq"]:
                        return
                    if epg_list:
                        status_info_sub.value = "\n\n".join(epg_list)
                    else:
                        arch = "⏱ Dostępne archiwum Catch Up" if parse_int_safe(item.get("tv_archive"), 0) == 1 else "📡 Transmisja na żywo"
                        status_info_sub.value = f"{arch}\nBrak szczegółowego programu EPG dla tej stacji."
                    safe_ui_update()
                elif k in ("vod", "series"):
                    tmdb = fetch_tmdb_details(title)
                    if cur_seq != app_state["preview_seq"]:
                        return
                    if tmdb:
                        if tmdb.get("poster") and not poster_img.visible:
                            poster_img.src = tmdb["poster"]
                            poster_img.visible = True
                        yr = f"Rok: {tmdb['year']}  |  " if tmdb.get("year") else ""
                        rt = f"Ocena TMDB: ⭐ {tmdb['rating']}/10\n" if tmdb.get("rating") else ""
                        status_info_sub.value = f"{yr}{rt}{tmdb.get('plot') or 'Brak opisu fabuły.'}"
                    else:
                        status_info_sub.value = "Naciśnij OK, aby rozpocząć odtwarzanie lub wybrać odcinek."
                    safe_ui_update()

            threading.Thread(target=_async_preview, daemon=True).start()

        def refresh_current_list(_e=None):
            if not state["client"]:
                return
            state["streams_cache"].clear()
            state["categories_cache"] = {"live": None, "vod": None, "series": None}
            state["categories"] = {"live": [], "vod": [], "series": []}
            state["search_query"] = ""
            now_playing_txt.value = "🔄 Odświeżanie listy i bufora RAM z serwera..."
            list_header_lbl.value = "⏳ Pobieranie najnowszych pozycji..."
            list_view.controls.clear()
            safe_ui_update()

            def _reload_w():
                try:
                    if isinstance(state["client"], M3UClient):
                        state["client"].login()
                    cur_kind = state["kind"]
                    raw_cats = state["client"].get_categories(cur_kind) or []
                    state["categories"][cur_kind] = build_special_top_categories(cur_kind, raw_cats)
                    if not state["in_dashboard"] and state["view_mode"] in ("streams", "episodes") and state.get("active_cat") and not state["in_fav"] and not state["in_profiles"]:
                        act_cat = state["active_cat"]
                        items = state["client"].get_streams(cur_kind, act_cat.get("category_id")) or []
                        for it in items:
                            it["kind"] = cur_kind
                        state["streams"] = items
                        state["streams_cache"][(cur_kind, str(act_cat.get("category_id")))] = items
                        state["view_mode"] = "streams"
                        state["page_offset"] = 0
                        state["list_idx"] = 1 if items else 0
                        render_list()
                    else:
                        open_dashboard()
                except Exception as ex:
                    now_playing_txt.value = f"❌ Błąd: {ex}"
                    safe_ui_update()

            threading.Thread(target=_reload_w, daemon=True).start()

        def open_search_dialog(_e=None):
            app_state["dialog_open"] = True
            search_in = ft.TextField(label="Szukaj na liście (puste = wyczyść)", value=state.get("search_query", ""), autofocus=True, on_submit=lambda _: _apply())

            def _apply(_e=None):
                state["search_query"] = (search_in.value or "").strip()
                state["page_offset"] = 0
                state["list_idx"] = 0
                dlg.open = False
                app_state["dialog_open"] = False
                if state["in_dashboard"]:
                    open_section("live")
                else:
                    render_list()

            dlg = ft.AlertDialog(
                title=ft.Text("🔍 Wyszukiwarka"),
                content=ft.Column([search_in], tight=True),
                actions=[ft.ElevatedButton("Szukaj", on_click=_apply, bgcolor="#059669", color="white")],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        def open_remote_settings_dlg(_e=None):
            nonlocal device_name, device_id
            app_state["dialog_open"] = True
            name_in = ft.TextField(label="Nazwa odbiornika", value=cache_get("device_name", device_name))
            id_in = ft.TextField(label="Unikalny Kod ID (wpisz ten sam w panelu zdalnym)", value=cache_get("device_id", device_id))

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
                dlg.open = False
                app_state["dialog_open"] = False
                safe_ui_update()
                publish_status()

            dlg = ft.AlertDialog(
                title=ft.Text("🌐 Konfiguracja Zdalnego Odbiornika (Cloud)"),
                content=ft.Column([name_in, id_in], tight=True, spacing=10),
                actions=[
                    ft.ElevatedButton(
                        "Anuluj",
                        on_click=lambda _: (setattr(dlg, "open", False), app_state.update({"dialog_open": False}), safe_ui_update()),
                    ),
                    ft.ElevatedButton("💾 Zapisz", on_click=_save, bgcolor="#059669", color="white"),
                ],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        def open_profiles_dialog_from_login(_e=None):
            if not profiles:
                status_banner.value = "ℹ️ Brak zapisanych list. Wpisz dane poniżej lub wyślij listę zdalnie przez Cloud."
                safe_ui_update()
                return

            app_state["dialog_open"] = True
            col_items = []

            def _select_p(prof):
                dlg.open = False
                app_state["dialog_open"] = False
                apply_profile(prof)

            def _delete_p(prof):
                delete_saved_profile(prof.get("name"))
                dlg.open = False
                app_state["dialog_open"] = False
                safe_ui_update()

            for idx, p in enumerate(profiles):
                p_name = p.get("name", f"Lista {idx + 1}")
                p_mode = (p.get("mode") or "xtream").upper()
                p_host = (p.get("server") or p.get("m3u_url") or "")[:30]
                is_act = p_name == state.get("active_profile_name")
                prefix = "🟢 [AKTYWNA] " if is_act else ""

                row = ft.Row(
                    [
                        ft.ElevatedButton(
                            f"{prefix}{idx + 1}. [{p_mode}] {p_name} ({p_host})",
                            on_click=lambda _e, pr=p: _select_p(pr),
                            bgcolor="#059669" if is_act else "#163828",
                            color="white",
                            expand=True,
                        ),
                        ft.ElevatedButton(
                            "🗑",
                            on_click=lambda _e, pr=p: _delete_p(pr),
                            bgcolor="#7f1d1d",
                            color="white",
                        ),
                    ],
                    spacing=6,
                )
                col_items.append(row)

            dlg = ft.AlertDialog(
                title=ft.Text("📋 Zapisane Listy / Serwery"),
                content=ft.Container(
                    content=ft.Column(col_items, tight=True, spacing=8, scroll=ft.ScrollMode.AUTO),
                    width=480,
                    height=300,
                ),
                actions=[
                    ft.ElevatedButton(
                        "Zamknij",
                        on_click=lambda _: (setattr(dlg, "open", False), app_state.update({"dialog_open": False}), safe_ui_update()),
                    )
                ],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        def show_info_dialog(_e=None):
            cur_item = state.get("current_item")
            title = (cur_item.get("name") or cur_item.get("title")) if isinstance(cur_item, dict) else state["now_playing"]
            app_state["dialog_open"] = True
            dlg = ft.AlertDialog(
                title=ft.Text(f"TMDB: {title}"),
                content=ft.Text("⏳ Pobieranie opisu..."),
                actions=[ft.ElevatedButton("Zamknij", on_click=lambda _: (setattr(dlg, "open", False), app_state.update({"dialog_open": False}), safe_ui_update()))],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

            def _w():
                tmdb = fetch_tmdb_details(title)
                txt = f"Rok: {tmdb.get('year')}\nOcena: ⭐ {tmdb.get('rating')}/10\n\n{tmdb.get('plot') or 'Brak opisu.'}" if tmdb else "Brak danych w bazie TMDB."
                dlg.content = ft.Text(txt)
                safe_ui_update()

            threading.Thread(target=_w, daemon=True).start()

        def launch_vlc_intent(raw_url: str, title: str = ""):
            if not raw_url:
                return
            clean_u = raw_url.strip().splitlines()[0].strip()
            mode = state.get("vlc_mode", "vlc_clean")
            no_proto = re.sub(r"^https?://", "", clean_u, flags=re.IGNORECASE)
            scheme = "https" if clean_u.lower().startswith("https://") else "http"

            if mode == "vlc_clean":
                target_url = f"vlc://{no_proto}"
            elif mode == "intent":
                target_url = f"intent://{no_proto}#Intent;scheme={scheme};type=video/*;package=org.videolan.vlc;end"
            elif mode == "vlc_full":
                target_url = f"vlc://{clean_u}"
            else:
                target_url = clean_u

            now_playing_txt.value = f"🚀 VLC ({mode}): {title or state['now_playing']}"
            safe_ui_update()
            threading.Thread(target=lambda: page.launch_url(target_url), daemon=True).start()

        def play_stream_url(url, title, item=None):
            state["playing_url"] = url
            state["now_playing"] = title
            state["current_item"] = item
            state["is_playing"] = True
            state["paused"] = False
            now_playing_txt.value = f"▶ Odtwarzanie: {title}"
            status_info_title.value = f"▶ {title}"
            if state["auto_vlc"]:
                launch_vlc_intent(url, title)
            else:
                safe_ui_update()
            publish_status()

        def stop_playback(_e=None):
            state["is_playing"] = False
            state["paused"] = False
            state["now_playing"] = "Brak aktywnego strumienia"
            now_playing_txt.value = "⏹ Zatrzymano odtwarzanie"
            safe_ui_update()
            publish_status()

        def toggle_play_pause(_e=None):
            state["paused"] = not state["paused"]
            now_playing_txt.value = "⏸ PAUZA" if state["paused"] else f"▶ {state['now_playing']}"
            safe_ui_update()
            publish_status()

        btn_vlc = make_tv_button("🚀 Otwórz VLC", lambda: launch_vlc_intent(state["playing_url"], state["now_playing"]), height=40)
        btn_vlc_mode = make_tv_button(VLC_MODE_LABELS[state["vlc_mode"]], cycle_vlc_mode, height=40)
        btn_only_pl = make_tv_button(f"🇵🇱 Tylko PL: {'WŁ.' if state['only_pl'] else 'WYŁ.'}", toggle_only_pl, height=40)
        btn_refresh_cat = make_tv_button("🔄 Odśwież RAM", refresh_current_list, height=40)
        btn_search = make_tv_button("🔍 Szukaj", open_search_dialog, height=40)
        btn_fav_toggle = make_tv_button("⭐ Ulubione", toggle_favorite, height=40)
        btn_tmdb = make_tv_button("ℹ TMDB", show_info_dialog, height=40)
        btn_cloud_cfg = make_tv_button("🌐 Kod ID", open_remote_settings_dlg, height=40)
        btn_home = make_tv_button("🏠 Menu", open_dashboard, height=40)
        btn_logout = make_tv_button("⇄ Konto", lambda: do_logout(), height=40)

        right_buttons = [
            btn_vlc,
            btn_vlc_mode,
            btn_only_pl,
            btn_refresh_cat,
            btn_search,
            btn_fav_toggle,
            btn_tmdb,
            btn_cloud_cfg,
            btn_home,
            btn_logout,
        ]
        list_item_buttons = []
        list_item_actions = []

        # Ekran Logowania Vivo (z przyciskiem Kodu ID oraz Zapisanych List)
        btn_remote_id = make_tv_button(f"🌐 Kod ID Odbiornika: {device_id} ({device_name})", open_remote_settings_dlg, height=42)
        btn_edit_srv = make_tv_button(f"🌐 Serwer: {creds['server'] or 'ustaw'}", lambda: open_field_editor("server", "Adres serwera (http://...)"), height=42)
        btn_edit_usr = make_tv_button(f"👤 Login: {creds['username'] or 'ustaw'}", lambda: open_field_editor("username", "Nazwa użytkownika"), height=42)
        btn_edit_pas = make_tv_button("🔑 Hasło: " + ("********" if creds["password"] else "ustaw"), lambda: open_field_editor("password", "Hasło", is_pass=True), height=42)
        btn_edit_m3u = make_tv_button(f"📋 Link M3U: {'[Zapisano]' if creds['m3u_url'] else 'opcjonalnie'}", lambda: open_field_editor("m3u_url", "Pełny link M3U"), height=42)
        btn_submit_login = make_tv_button("🚀 ZALOGUJ DO VIVO TV", lambda: do_login(), height=48)
        btn_saved_profiles_login = make_tv_button(f"📋 WYBIERZ ZAPISANĄ LISTĘ ({len(profiles)})", open_profiles_dialog_from_login, height=42)
        btn_exit_login = make_tv_button("🚪 ZAMKNIJ APLIKACJĘ", close_app_cleanly, height=40)

        login_items = [
            btn_remote_id,             # 0
            btn_edit_srv,              # 1
            btn_edit_usr,              # 2
            btn_edit_pas,              # 3
            btn_edit_m3u,              # 4
            btn_submit_login,          # 5
            btn_saved_profiles_login,  # 6
            btn_exit_login,            # 7
        ]

        def refresh_login_button_labels():
            set_tv_button_text(btn_remote_id, f"🌐 Kod ID Odbiornika: {device_id} ({device_name})")
            set_tv_button_text(btn_edit_srv, f"🌐 Serwer: {creds['server'] or 'ustaw'}")
            set_tv_button_text(btn_edit_usr, f"👤 Login: {creds['username'] or 'ustaw'}")
            set_tv_button_text(btn_edit_pas, "🔑 Hasło: " + ("********" if creds["password"] else "ustaw"))
            set_tv_button_text(btn_edit_m3u, f"📋 Link M3U: {'[Zapisano]' if creds['m3u_url'] else 'opcjonalnie'}")
            set_tv_button_text(btn_saved_profiles_login, f"📋 WYBIERZ ZAPISANĄ LISTĘ ({len(profiles)})")

        def open_field_editor(field_key, title_str, is_pass=False):
            app_state["dialog_open"] = True
            tf_in = ft.TextField(label=title_str, value=creds.get(field_key, ""), password=is_pass, autofocus=True, on_submit=lambda _: _save())

            def _save(_=None):
                creds[field_key] = (tf_in.value or "").strip() if field_key != "password" else (tf_in.value or "")
                cache_set(field_key, creds[field_key])
                refresh_login_button_labels()
                dlg.open = False
                app_state["dialog_open"] = False
                update_focus_styles()

            dlg = ft.AlertDialog(
                title=ft.Text(title_str),
                content=ft.Column([tf_in], tight=True),
                actions=[ft.ElevatedButton("Zapisz", on_click=_save, bgcolor="#059669", color="white")],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        login_view = ft.Container(
            content=ft.Column(
                [
                    ft.Text("🟢 VIVO PLAYER IPTV V6.6", size=24, weight=ft.FontWeight.BOLD, color="#34d399"),
                    status_banner,
                    *login_items,
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
                width=540,
            ),
            alignment=ft.alignment.center,
            expand=True,
            visible=True,
            bgcolor="#0b1f15",
            padding=20,
        )

        # Główny interfejs Vivo Dashboard
        dash_view = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Row([ft.Text("🟢", size=24), ft.Text("VIVO Player", size=20, weight=ft.FontWeight.BOLD, color="#34d399")], spacing=8),
                            ft.Column([clock_txt, date_txt], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                            ft.Row(
                                [
                                    ft.IconButton(icon="search", icon_color="#34d399", on_click=open_search_dialog),
                                    ft.IconButton(icon="person", icon_color="#34d399", on_click=do_logout),
                                    ft.IconButton(icon="settings", icon_color="#34d399", on_click=open_remote_settings_dlg),
                                ],
                                spacing=4,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Column(
                        [
                            ft.Row([dash_tile_live, dash_tile_movies, dash_tile_series], spacing=12, expand=True),
                            ft.Row([dash_tile_catchup, dash_tile_fav, dash_tile_playlists], spacing=12, height=85),
                        ],
                        spacing=12,
                        expand=True,
                    ),
                    ft.Row(
                        [
                            ft.Column([footer_playlist_txt, footer_expire_txt], spacing=2),
                            ft.Row(
                                [
                                    ft.IconButton(icon="refresh", icon_color="#34d399", on_click=refresh_current_list),
                                    ft.IconButton(icon="power_settings_new", icon_color="#ef4444", on_click=close_app_cleanly),
                                ],
                                spacing=8,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                ],
                expand=True,
                spacing=15,
            ),
            expand=True,
            visible=False,
            bgcolor="#0b1f15",
            padding=20,
        )

        left_panel = ft.Container(width=440, bgcolor="#0f291d", padding=10, border_radius=10, border=ft.border.all(1, "#22543d"))
        right_panel = ft.Container(expand=True, bgcolor="#0f291d", padding=10, border_radius=10, border=ft.border.all(1, "#22543d"))

        left_panel.content = ft.Column(
            [
                ft.Row(
                    [
                        make_tv_button("🏠 Menu", open_dashboard, height=38, expand=True),
                        make_tv_button("🔄 Odśwież RAM", refresh_current_list, height=38, expand=True),
                    ],
                    spacing=6,
                ),
                ft.Container(content=list_header_lbl, padding=6, bgcolor="#091811", border_radius=6),
                list_view,
            ],
            expand=True,
            spacing=8,
        )

        right_panel.content = ft.Column(
            [
                ft.Row([header_dev_lbl, ft.Row([btn_home, btn_logout], spacing=6)], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                video_placeholder,
                now_playing_txt,
                ft.Row(right_buttons, spacing=6, wrap=True),
            ],
            expand=True,
        )

        dash_inner_view = ft.Container(
            content=ft.Row([left_panel, right_panel], expand=True, spacing=10),
            expand=True,
            visible=False,
            bgcolor="#0b1f15",
            padding=10,
        )

        page.add(login_view, dash_view, dash_inner_view)

        def style_single_list_btn(idx, is_focused, single_update=False):
            if not (0 <= idx < len(list_item_buttons)):
                return
            b = list_item_buttons[idx]
            base_t = b.data["base_text"]
            if is_focused:
                b.bgcolor = "#059669"
                b.border = ft.border.all(3, "#fbbf24")
                b.data["text_ctrl"].color = "#ffffff"
                b.data["text_ctrl"].value = f"▶  {base_t}"
            else:
                b.bgcolor = "#163828"
                b.border = ft.border.all(1, "#22543d")
                b.data["text_ctrl"].color = "#cbd5e1"
                b.data["text_ctrl"].value = base_t
            if single_update:
                try:
                    b.update()
                except Exception:
                    pass

        def scroll_list_to_focus():
            if state["zone"] == "list" and 0 <= state["list_idx"] < len(list_item_buttons):
                try:
                    list_view.scroll_to(key=f"item_{state['list_idx']}", duration=100)
                except Exception:
                    pass
                focused_btn = list_item_buttons[state["list_idx"]]
                if focused_btn.data.get("item"):
                    trigger_focus_preview(focused_btn.data["item"])

        def update_focus_styles(full_refresh=False):
            if login_view.visible:
                for i, item in enumerate(login_items):
                    is_foc = (state["zone"] == "login" and state["login_idx"] == i)
                    item.bgcolor = "#fbbf24" if is_foc else ("#059669" if i == 5 else "#163828")
                    item.border = ft.border.all(3, "white" if is_foc else "#22543d")
                    item.data["text_ctrl"].color = "black" if is_foc else "white"
                safe_ui_update()
                return

            if dash_view.visible:
                render_dashboard_focus()
                return

            if dash_inner_view.visible:
                for i, b in enumerate(right_buttons):
                    is_foc = (state["zone"] == "right" and state["right_idx"] == i)
                    b.bgcolor = "#fbbf24" if is_foc else "#163828"
                    b.border = ft.border.all(3, "white" if is_foc else "#22543d")
                    b.data["text_ctrl"].color = "black" if is_foc else "white"

                for i in range(len(list_item_buttons)):
                    style_single_list_btn(i, state["zone"] == "list" and state["list_idx"] == i, single_update=False)
                safe_ui_update()
                scroll_list_to_focus()

        def change_page(delta, jump_to_end=False):
            total_len = len(state.get("filtered", []))
            if total_len <= 0:
                return
            max_off = ((total_len - 1) // PAGE_SIZE) * PAGE_SIZE
            new_off = max(0, min(state["page_offset"] + delta, max_off))
            if new_off != state["page_offset"]:
                state["page_offset"] = new_off
                render_list(select_last=jump_to_end)

        def render_list(select_last=False):
            list_view.controls.clear()
            list_item_buttons.clear()
            list_item_actions.clear()
            v_mode = state["view_mode"]
            btn_idx = 0
            pl_ids = get_current_pl_cat_ids()

            if state["in_profiles"]:
                list_header_lbl.value = f"📋 ZAPISANE LISTY ({len(profiles)})"
                b_add = make_tv_button("➕ DODAJ / EDYTUJ LISTĘ RĘCZNIE", do_logout, key_str=f"item_{btn_idx}", height=42)
                list_item_buttons.append(b_add)
                list_item_actions.append(do_logout)
                list_view.controls.append(b_add)
                btn_idx += 1

                for idx, prof in enumerate(profiles):
                    p_name = prof.get("name", f"Lista {idx + 1}")
                    is_act = p_name == state.get("active_profile_name")
                    badge = "🟢 [AKTYWNA] " if is_act else "📋 "
                    act_fn = lambda pr=prof: apply_profile(pr)
                    b = make_tv_button(f"{idx + 1}. {badge}{p_name}", act_fn, key_str=f"item_{btn_idx}", height=46)
                    list_item_buttons.append(b)
                    list_item_actions.append(act_fn)
                    list_view.controls.append(b)
                    btn_idx += 1

                    if not is_act or len(profiles) > 1:
                        del_fn = lambda nm=p_name: delete_saved_profile(nm)
                        b_del = make_tv_button(f"   ↳ 🗑 Usuń: {p_name}", del_fn, key_str=f"item_{btn_idx}", height=36)
                        list_item_buttons.append(b_del)
                        list_item_actions.append(del_fn)
                        list_view.controls.append(b_del)
                        btn_idx += 1

                state["list_idx"] = min(state["list_idx"], max(0, len(list_item_buttons) - 1))
                update_focus_styles(full_refresh=True)
                return

            kind_lbl = {"live": "TV NA ŻYWO", "vod": "FILMY VOD", "series": "SERIALE"}[state["kind"]]
            if v_mode == "categories":
                if state["categories_cache"].get(state["kind"]) is not None:
                    raw = state["categories_cache"][state["kind"]]
                else:
                    raw = list(state["categories"].get(state["kind"], []))
                    if state["only_pl"]:
                        filtered_c = [
                            c for c in raw
                            if str(c.get("category_id")) in ("__ALL__", "__RECENT__", "__CATCHUP__")
                            or is_polish_text(c.get("category_name", ""))
                        ]
                        if len(filtered_c) > 2:
                            raw = filtered_c
                    state["categories_cache"][state["kind"]] = raw
                list_header_lbl.value = f"📂 {kind_lbl} (RAM ⚡) ({len(raw)})"
            elif v_mode == "episodes":
                raw = list(state["episodes"])
                list_header_lbl.value = f"🎞 {state['active_series_title']} ({len(raw)})"
            elif state["in_fav"]:
                raw = list(favorites_items)
                if state["only_pl"]:
                    raw = [it for it in raw if is_polish_item(it, pl_ids)]
                list_header_lbl.value = f"⭐ ULUBIONE ({len(raw)})"
            else:
                raw = list(state["streams"])
                act_cat = state.get("active_cat") or {}
                cat_nm = act_cat.get("category_name", "Lista")
                cat_id_str = str(act_cat.get("category_id", ""))
                cat_is_pl = (
                    cat_id_str not in ("__ALL__", "__RECENT__", "__CATCHUP__")
                    and (cat_id_str in pl_ids or is_polish_text(cat_nm))
                )
                if state["only_pl"] and not cat_is_pl:
                    only_pl_s = [it for it in raw if is_polish_item(it, pl_ids)]
                    if only_pl_s:
                        raw = only_pl_s
                list_header_lbl.value = f"📺 {cat_nm} (RAM ⚡) ({len(raw)})"

            q = state.get("search_query", "").strip().lower()
            if q:
                raw = [
                    it for it in raw
                    if q in (it.get("name") or it.get("title") or it.get("category_name") or "").lower()
                ]

            state["filtered"] = raw
            total_items = len(raw)
            start = state["page_offset"]
            end = min(start + PAGE_SIZE, total_items)
            slice_items = raw[start:end]

            if v_mode == "episodes":
                b_back = make_tv_button("↩ WRÓĆ DO SERIALI", back_to_streams, key_str=f"item_{btn_idx}", height=42)
                list_item_buttons.append(b_back)
                list_item_actions.append(back_to_streams)
                list_view.controls.append(b_back)
                btn_idx += 1
            elif v_mode == "streams" and not state["in_fav"]:
                b_back = make_tv_button("↩ WRÓĆ DO KATEGORII", back_to_cats, key_str=f"item_{btn_idx}", height=42)
                list_item_buttons.append(b_back)
                list_item_actions.append(back_to_cats)
                list_view.controls.append(b_back)
                btn_idx += 1

            if start > 0:
                prev_fn = lambda: change_page(-PAGE_SIZE, jump_to_end=True)
                b_prev = make_tv_button(f"⬆️ POPRZEDNIA STRONA ({start - PAGE_SIZE + 1}-{start})", prev_fn, key_str=f"item_{btn_idx}", height=42)
                list_item_buttons.append(b_prev)
                list_item_actions.append(prev_fn)
                list_view.controls.append(b_prev)
                btn_idx += 1

            first_media_btn_idx = btn_idx

            for local_i, item in enumerate(slice_items):
                real_num = start + local_i + 1
                if v_mode == "categories":
                    cn = item.get("category_name", "")
                    action_fn = lambda c=item: open_category(c)
                    lbl = f"{real_num}. 📁 {cn}"
                elif v_mode == "episodes":
                    et = item.get("display_title") or item.get("title") or "Odcinek"
                    action_fn = lambda ep=item: play_episode(ep)
                    lbl = f"{real_num}. 🎞 {et}"
                else:
                    fav = "⭐ " if is_favorite(item) else ""
                    pl = "🇵🇱 " if is_polish_item(item, pl_ids) else ""
                    arch = "⏱ " if parse_int_safe(item.get("tv_archive"), 0) == 1 else ""
                    nm = item.get("name") or item.get("title") or "Pozycja"
                    action_fn = lambda it=item: on_stream_click(it)
                    lbl = f"{real_num}. {fav}{pl}{arch}{nm}"

                b = make_tv_button(lbl, action_fn, key_str=f"item_{btn_idx}", height=46, item_ref=item)
                list_item_buttons.append(b)
                list_item_actions.append(action_fn)
                list_view.controls.append(b)
                btn_idx += 1

            last_media_btn_idx = btn_idx - 1

            if end < total_items:
                next_fn = lambda: change_page(PAGE_SIZE, jump_to_end=False)
                b_next = make_tv_button(f"⬇️ NASTĘPNA STRONA ({end + 1}-{min(end + PAGE_SIZE, total_items)} z {total_items})", next_fn, key_str=f"item_{btn_idx}", height=42)
                list_item_buttons.append(b_next)
                list_item_actions.append(next_fn)
                list_view.controls.append(b_next)
                btn_idx += 1

            if select_last and last_media_btn_idx >= first_media_btn_idx:
                state["list_idx"] = last_media_btn_idx
            elif slice_items:
                state["list_idx"] = first_media_btn_idx
            else:
                state["list_idx"] = 0

            update_focus_styles(full_refresh=True)

        def back_to_streams():
            state["view_mode"] = "streams"
            state["page_offset"] = 0
            render_list()

        def back_to_cats():
            state["view_mode"] = "categories"
            state["active_cat"] = None
            state["page_offset"] = 0
            if not state["categories"].get(state["kind"]):
                list_header_lbl.value = "⏳ Pobieranie kategorii..."
                safe_ui_update()

                def _w():
                    try:
                        cats = state["client"].get_categories(state["kind"]) or []
                        state["categories"][state["kind"]] = build_special_top_categories(state["kind"], cats)
                        state["categories_cache"][state["kind"]] = None
                        render_list()
                    except Exception:
                        pass

                threading.Thread(target=_w, daemon=True).start()
            else:
                render_list()

        def open_category(cat_obj):
            state["view_mode"] = "streams"
            state["active_cat"] = cat_obj
            state["page_offset"] = 0
            state["list_idx"] = 0
            cat_id = str(cat_obj.get("category_id", "__ALL__"))
            cache_key = (state["kind"], cat_id)

            if cache_key in state["streams_cache"]:
                state["streams"] = state["streams_cache"][cache_key]
                render_list()
                return

            list_view.controls.clear()
            list_header_lbl.value = f"⏳ Ładowanie: {cat_obj.get('category_name')}..."
            safe_ui_update()

            def _w():
                try:
                    items = state["client"].get_streams(state["kind"], cat_obj.get("category_id")) or []
                    for it in items:
                        it["kind"] = state["kind"]
                    state["streams"] = items
                    state["streams_cache"][cache_key] = items
                    render_list()
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
                state["active_series_id"] = uid
                state["active_series_title"] = title
                state["page_offset"] = 0
                list_view.controls.clear()
                list_header_lbl.value = f"⏳ Odcinki: {title}..."
                safe_ui_update()

                def _w():
                    try:
                        info = state["client"].get_series_info(item.get("series_id"))
                        eps_map = info.get("episodes") or {}
                        flat_eps = []
                        for s_num in sorted(eps_map.keys(), key=lambda x: int(x) if str(x).isdigit() else 999):
                            for ep in eps_map[s_num]:
                                ep_copy = dict(ep)
                                ep_copy["display_title"] = f"S{int(s_num):02d}E{int(ep.get('episode_num', 1)):02d} - {ep.get('title')}"
                                flat_eps.append(ep_copy)
                        state["episodes"] = flat_eps
                        state["view_mode"] = "episodes"
                        render_list()
                    except Exception:
                        back_to_streams()

                threading.Thread(target=_w, daemon=True).start()
                return
            ext = item.get("container_extension", "ts" if k == "live" else "mp4") or ("ts" if k == "live" else "mp4")
            url = state["client"].build_stream_url(k, item.get("stream_id"), ext, item=item)
            play_stream_url(url, title, item=item)

        def do_login(_e=None, is_auto=False, profile_name_hint=""):
            if state["is_logging_in"]:
                return
            state["is_logging_in"] = True
            status_banner.value = "⏳ Łączenie z serwerem Vivo..."
            safe_ui_update()

            def _w():
                try:
                    m3u = creds["m3u_url"].strip()
                    srv = creds["server"].strip()
                    usr = creds["username"].strip()
                    pas = creds["password"]
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
                    state["exp_date_str"] = (
                        datetime.datetime.fromtimestamp(exp_ts).strftime("%Y-%m-%d")
                        if exp_ts > 0
                        else "Unlimited"
                    )
                    prof_name = register_or_update_profile(
                        profile_name_hint or state.get("active_profile_name", ""),
                        srv,
                        usr,
                        pas,
                        m3u,
                    )
                    state["active_profile_name"] = prof_name or f"{client.username} ({client.server})"
                    cache_set("server", srv)
                    cache_set("username", usr)
                    cache_set("password", pas)
                    cache_set("m3u_url", m3u)
                    refresh_login_button_labels()

                    login_view.visible = False
                    open_dashboard()
                except Exception as ex:
                    status_banner.value = f"❌ Błąd logowania: {ex}"
                    safe_ui_update()
                finally:
                    state["is_logging_in"] = False

            threading.Thread(target=_w, daemon=True).start()

        def do_logout(_e=None):
            stop_playback()
            state["client"] = None
            state["streams_cache"].clear()
            state["categories_cache"] = {"live": None, "vod": None, "series": None}
            dash_view.visible = False
            dash_inner_view.visible = False
            login_view.visible = True
            state["zone"] = "login"
            state["login_idx"] = 5
            refresh_login_button_labels()
            update_focus_styles()
            publish_status()

        def apply_profile(prof):
            mode = prof.get("mode", "xtream")
            name = prof.get("name", "Zdalna Lista")
            state["active_profile_name"] = name
            cache_set("active_profile_name", name)

            if mode == "m3u" or (prof.get("m3u_url") and not prof.get("username")):
                creds["m3u_url"] = prof.get("m3u_url") or prof.get("url") or ""
                creds["server"] = "http://"
                creds["username"] = ""
                creds["password"] = ""
            else:
                creds["server"] = prof.get("server") or prof.get("host") or ""
                creds["username"] = prof.get("username") or prof.get("user") or ""
                creds["password"] = prof.get("password") or prof.get("pass") or ""
                creds["m3u_url"] = ""

            cache_set("server", creds["server"])
            cache_set("username", creds["username"])
            cache_set("password", creds["password"])
            cache_set("m3u_url", creds["m3u_url"])
            refresh_login_button_labels()
            do_login(profile_name_hint=name)

        # =====================================================================
        # ZDALNE STEROWANIE I ODBIERANIE LIST PRZEZ CLOUD (NTFY.SH)
        # =====================================================================
        def handle_remote_command(cmd):
            nonlocal profiles, device_name
            c_type = cmd.get("type") or cmd.get("action") or cmd.get("cmd") or ""

            if c_type in (
                "add_profile",
                "send_list",
                "new_list",
                "add_list",
                "push_list",
            ) or ("server" in cmd and "username" in cmd) or ("m3u_url" in cmd):
                prof_name = (
                    cmd.get("name")
                    or cmd.get("profile_name")
                    or f"Lista {len(profiles) + 1}"
                )
                mode = cmd.get("mode") or (
                    "m3u" if cmd.get("m3u_url") and not cmd.get("username") else "xtream"
                )
                new_prof = {
                    "name": prof_name,
                    "mode": mode,
                    "server": cmd.get("server") or cmd.get("host") or "",
                    "username": cmd.get("username") or cmd.get("user") or "",
                    "password": cmd.get("password") or cmd.get("pass") or "",
                    "m3u_url": cmd.get("m3u_url") or cmd.get("url") or "",
                    "added_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                profiles = [p for p in profiles if p.get("name") != prof_name]
                profiles.insert(0, new_prof)
                cache_set("profiles", profiles)
                refresh_login_button_labels()

                switch_now = cmd.get("switch", cmd.get("auto_switch", cmd.get("activate", True)))
                if switch_now:
                    apply_profile(new_prof)
                else:
                    if state["in_profiles"]:
                        render_list()
                    publish_status()

            elif c_type in (
                "switch_profile",
                "select_profile",
                "activate_profile",
                "switch_list",
                "switch",
            ):
                target = cmd.get("name") or cmd.get("profile_name")
                for p in profiles:
                    if p.get("name") == target:
                        apply_profile(p)
                        break

            elif c_type in (
                "delete_profile",
                "remove_profile",
                "del_profile",
                "delete_list",
                "delete",
            ):
                target = cmd.get("name") or cmd.get("profile_name")
                delete_saved_profile(target)

            elif c_type in ("set_name", "rename", "set_device_name", "save_name"):
                nn = (cmd.get("device_name") or cmd.get("name") or "").strip()
                if nn:
                    device_name = nn
                    cache_set("device_name", nn)
                    cur_id = cache_get("device_id", device_id)
                    header_dev_lbl.value = f"📺 {nn} [{cur_id}]"
                    refresh_login_button_labels()
                    update_vivo_footer()
                    safe_ui_update()
                    publish_status()

            elif c_type in ("remote_control", "control", "player", "key", "command"):
                act = cmd.get("command") or cmd.get("action") or cmd.get("key") or ""
                _exec_player_action(act)
            elif c_type in (
                "play_pause",
                "toggle",
                "stop",
                "mute",
                "vol_up",
                "vol_down",
                "logout",
                "refresh",
                "reload_list",
                "only_pl",
                "toggle_only_pl",
                "ping",
                "status",
            ):
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
            listener_session = make_session()
            while not stop_event.is_set():
                if not app_state["paused_in_bg"]:
                    try:
                        clean_id = re.sub(r"[^a-zA-Z0-9_-]", "", cache_get("device_id", device_id))
                        topics = f"iptv_v4_pro_{clean_id},iptv_v4_pro_{clean_id}_cmd"
                        url = f"https://ntfy.sh/{topics}/json?poll=1&since=30m"
                        r = listener_session.get(url, timeout=4)
                        if r.status_code == 200 and not stop_event.is_set():
                            for line in r.text.splitlines():
                                if not line.strip():
                                    continue
                                event = json.loads(line)
                                msg_id = event.get("id")
                                if not msg_id or msg_id in processed_ids:
                                    continue
                                if event.get("event") != "message":
                                    continue

                                processed_ids.add(msg_id)
                                cache_set("processed_ids", list(processed_ids)[-200:])

                                raw_msg = event.get("message", "")
                                try:
                                    payload = json.loads(raw_msg)
                                except Exception:
                                    continue

                                if not isinstance(payload, dict) or payload.get("type") == "player_status":
                                    continue

                                handle_remote_command(payload)
                    except Exception:
                        pass
                if stop_event.wait(2.5):
                    break

        threading.Thread(target=cloud_listener, daemon=True).start()

        def handle_smart_back():
            if state["view_mode"] == "episodes":
                back_to_streams()
            elif state["view_mode"] == "streams" and not state["in_fav"] and not state["in_profiles"]:
                back_to_cats()
            else:
                open_dashboard()

        def on_key_event(e: ft.KeyboardEvent):
            if app_state["dialog_open"]:
                return
            key = e.key

            if login_view.visible:
                state["zone"] = "login"
                if key == "Arrow Down" and state["login_idx"] < len(login_items) - 1:
                    state["login_idx"] += 1
                elif key == "Arrow Up" and state["login_idx"] > 0:
                    state["login_idx"] -= 1
                elif key in ("Enter", "Select", " "):
                    login_items[state["login_idx"]].data["action"]()
                update_focus_styles()
                return

            if dash_view.visible:
                state["zone"] = "dashboard"
                if key == "Arrow Right" and state["dash_idx"] < len(dash_tiles) - 1:
                    state["dash_idx"] += 1
                elif key == "Arrow Left" and state["dash_idx"] > 0:
                    state["dash_idx"] -= 1
                elif key == "Arrow Down" and state["dash_idx"] < 3:
                    state["dash_idx"] = min(5, state["dash_idx"] + 3)
                elif key == "Arrow Up" and state["dash_idx"] >= 3:
                    state["dash_idx"] -= 3
                elif key in ("Enter", "Select", " "):
                    dash_tiles[state["dash_idx"]].data["action"]()
                render_dashboard_focus()
                return

            if dash_inner_view.visible:
                if key in ("Page Down", "Channel Down"):
                    change_page(PAGE_SIZE, jump_to_end=False)
                    return
                elif key in ("Page Up", "Channel Up"):
                    change_page(-PAGE_SIZE, jump_to_end=False)
                    return

                if key == "Arrow Right":
                    if state["zone"] == "list":
                        state["zone"] = "right"
                        state["right_idx"] = 0
                    elif state["zone"] == "right" and state["right_idx"] < len(right_buttons) - 1:
                        state["right_idx"] += 1
                elif key == "Arrow Left":
                    if state["zone"] == "right":
                        if state["right_idx"] > 0:
                            state["right_idx"] -= 1
                        else:
                            state["zone"] = "list"
                elif key == "Arrow Down":
                    if state["zone"] == "list" and list_item_buttons:
                        if state["list_idx"] < len(list_item_buttons) - 1:
                            state["list_idx"] += 1
                        elif state["page_offset"] + PAGE_SIZE < len(state.get("filtered", [])):
                            change_page(PAGE_SIZE, jump_to_end=False)
                            return
                    elif state["zone"] == "right" and state["right_idx"] < len(right_buttons) - 1:
                        state["right_idx"] += 1
                elif key == "Arrow Up":
                    if state["zone"] == "list" and list_item_buttons:
                        if state["list_idx"] > 0:
                            state["list_idx"] -= 1
                        elif state["page_offset"] >= PAGE_SIZE:
                            change_page(-PAGE_SIZE, jump_to_end=True)
                            return
                    elif state["zone"] == "right" and state["right_idx"] > 0:
                        state["right_idx"] -= 1
                elif key in ("Enter", "Select", " "):
                    if state["zone"] == "list" and list_item_actions and 0 <= state["list_idx"] < len(list_item_actions):
                        list_item_actions[state["list_idx"]]()
                        return
                    elif state["zone"] == "right":
                        right_buttons[state["right_idx"]].data["action"]()
                        return
                elif key in ("Backspace", "Escape"):
                    handle_smart_back()
                    return
                update_focus_styles()

        page.on_keyboard_event = on_key_event
        update_focus_styles()
        publish_status()

        if state["auto_login"] and has_saved_credentials():
            threading.Thread(target=lambda: (time.sleep(0.35), do_login(is_auto=True)), daemon=True).start()

    except Exception as e:
        err_msg = traceback.format_exc()
        page.add(ft.Text(f"Błąd krytyczny: {e}\n{err_msg}", color="red", size=14))
        page.update()


ft.app(target=main)
