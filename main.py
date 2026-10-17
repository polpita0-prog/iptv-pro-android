# -*- coding: utf-8 -*-
"""
IPTV Client V5.5 PRO - ANDROID TV EDITION
Nowości w V5.5:
1. PRZEŁĄCZNIK "🇵🇱 Tylko Polskie: WŁ./WYŁ." (ZAPISYWANY W PAMIĘCI APK):
   - Gdy WŁ.: całkowicie ukrywa zagraniczne kategorie na liście kategorii oraz zagraniczne
     kanały/filmy/seriale na listach zbiorczych (Wszystkie / Ostatnio dodane).
   - Gdy WYŁ.: pokazuje pełną listę, ale polskie kategorie i kanały nadal trzyma na samej górze.
2. INTELIGENTNE ROZPOZNAWANIE POLSKICH KATEGORII:
   - Jeśli użytkownik otworzy polską kategorię (np. "PL: Kino Akcji"), wyświetlane są wszystkie
     filmy należące do tej kategorii, nawet gdy pojedynczy film nie ma tagu "PL" w tytule.
3. FILMY I SERIALE - OSTATNIO DODANE NA GÓRZE + PRZYCISK "🔄 ODŚWIEŻ NOWOŚCI".
"""

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

# Wzorce rozpoznające polskie kategorie, kanały, filmy i seriale
PL_REGEX = re.compile(
    r"(?:^|[\s\|\[\(\-_:•/])(PL|POL|POLAND|POLSKA|POLSKIE|POLISH|LEKTOR|DUBBING|NAPISY[\s_]*PL|PL[\s_]*SUB|VIP[\s_]*PL)(?:$|[\s\|\]\)\-_:•/])"
    r"|[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]"
    r"|\b(tvp|polsat|tvn|canal\+|c\+|ale\s*kino|kino\s*polska|planete\+|domo\+|kuchnia\+|teletoon\+|minimini\+|ttv|puls|fokus\s*tv|eska|polo\s*tv|trwam|republika|wpolsce|wydarzenia\s*24|stopklatka|zoom\s*tv|wp\s*tv|nowa\s*tv|metro\s*tv|eleven\s*sports|polsat\s*sport|motowizja|stars\s*tv|kino\s*tv|gametoon|active\s*family|romance\s*tv|13\s*ulica|sci\s*fi|paramount|comedy\s*central|national\s*geographic|discovery|animal\s*planet|tlc|id\s*investigation|hbo|cinemax|filmbox|axn|fox|epic\s*drama|bbc\s*brit|bbc\s*earth|bbc\s*first|bbc\s*cbbc|bbc\s*lifestyle|viasat|history|crime\s*investigation|sundance|red\s*carpet|antena\s*hd|tv\s*okazje|super\s*polsat|tv4|tv6|polsat\s*news|tvn24|tvp\s*info|biznes24|news24|pogoda24|4fun|vox\s*music|disco\s*polo|power\s*tv|nuta\s*tv|mixtape|hip\s*hop\s*tv)\b",
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
        if category_id and category_id not in ("__ALL__", "__RECENT__"):
            params["category_id"] = category_id
        raw_streams = self._api(params, timeout=24) or []
        return sort_streams_smart(
            raw_streams,
            kind,
            force_newest_only=(category_id == "__RECENT__"),
            pl_cat_ids=self.pl_cat_ids.get(kind, set()),
        )

    def get_series_info(self, series_id):
        return self._api({"action": "get_series_info", "series_id": series_id})

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
        return {"auth": 1, "exp_date": "0"}

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
        if not category_id or category_id in ("__ALL__", "__RECENT__"):
            res = []
            for v in cat_map.values():
                res.extend(v)
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
        page.title = "IPTV V5.5 PRO Android TV"
        page.theme_mode = ft.ThemeMode.DARK
        page.bgcolor = "#0b0e17"
        page.padding = 10

        stop_event = threading.Event()
        app_state = {"dialog_open": False, "paused_in_bg": False}
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

        VLC_MODE_LABELS = {
            "vlc_clean": "Tryb: vlc:// (Czysty)",
            "intent": "Tryb: Android Intent",
            "vlc_full": "Tryb: vlc://http://",
            "http": "Tryb: Standard HTTP",
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
            "view_mode": "categories",  # categories | streams | episodes | profiles
            "active_cat": None,
            "active_series_id": "",
            "active_series_title": "",
            "active_profile_name": cache_get("active_profile_name", ""),
            "categories": {"live": [], "vod": [], "series": []},
            "streams_cache": {},  # (kind, cat_id) -> list
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
            "zone": "login",
            "tab_idx": 0,
            "list_idx": 0,
            "right_idx": 0,
            "login_idx": 5 if has_saved_credentials() else 1,
            "page_offset": 0,
            "last_ok_ts": 0.0,
            "last_vlc_ts": 0.0,
            "prev_zone": None,
            "prev_list_idx": -1,
        }

        PAGE_SIZE = 50

        def get_current_pl_cat_ids() -> set:
            cl = state.get("client")
            if cl and hasattr(cl, "pl_cat_ids"):
                return cl.pl_cat_ids.get(state["kind"], set())
            return set()

        # =====================================================================
        # PRZEŁĄCZNIK: 🇵🇱 TYLKO POLSKIE (UKRYWANIE ZAGRANICZNYCH KANAŁÓW I GRUP)
        # =====================================================================
        def toggle_only_pl(_e=None):
            state["only_pl"] = not state["only_pl"]
            cache_set("only_pl", state["only_pl"])
            set_tv_button_text(
                btn_only_pl,
                f"🇵🇱 Tylko Polskie: {'WŁ.' if state['only_pl'] else 'WYŁ.'}",
            )
            state["page_offset"] = 0
            state["list_idx"] = 0
            now_playing_txt.value = (
                "🇵🇱 Filtr 'Tylko Polskie' WŁĄCZONY — zagraniczne kategorie i kanały zostały ukryte."
                if state["only_pl"]
                else "🌍 Filtr 'Tylko Polskie' WYŁĄCZONY — widoczne wszystkie kanały (Polskie na górze)."
            )
            render_list()

        # =====================================================================
        # SZYBKA OBSŁUGA ULUBIONYCH
        # =====================================================================
        def is_favorite(item):
            uid = get_item_unique_id(item)
            if not uid:
                return False
            return any(get_item_unique_id(f) == uid for f in favorites_items)

        def toggle_favorite(_e=None):
            nonlocal favorites_items
            cur_item = state.get("current_item")
            if not cur_item:
                now_playing_txt.value = "⚠️ Wybierz najpierw kanał lub film, aby dodać do ulubionych."
                safe_ui_update()
                return

            uid = get_item_unique_id(cur_item)
            if not uid:
                return

            if is_favorite(cur_item):
                favorites_items = [f for f in favorites_items if get_item_unique_id(f) != uid]
                now_playing_txt.value = f"⭐ Usunięto z ulubionych: {cur_item.get('name') or cur_item.get('title')}"
            else:
                item_copy = dict(cur_item)
                item_copy["kind"] = cur_item.get("kind") or state["kind"]
                favorites_items.insert(0, item_copy)
                now_playing_txt.value = f"⭐ Dodano do ulubionych: {cur_item.get('name') or cur_item.get('title')}"

            cache_set("favorites_v2", favorites_items)
            set_tv_button_text(
                btn_fav_toggle,
                "⭐ Usuń z ulubionych" if is_favorite(cur_item) else "⭐ Dodaj do ulubionych",
            )
            if state["in_fav"]:
                render_list()
            else:
                safe_ui_update()

        def save_last_session(kind=None, cat_obj=None, stream_id=None, stream_name=None, stream_url=None, item_obj=None):
            cur = dict(cache_get("last_session", {}))
            if kind is not None:
                cur["kind"] = kind
            if cat_obj is not None:
                cur["category_id"] = str(cat_obj.get("category_id", ""))
                cur["category_name"] = cat_obj.get("category_name", "Kategoria")
            if stream_id is not None:
                cur["stream_id"] = str(stream_id)
            if stream_name is not None:
                cur["stream_name"] = stream_name
            if stream_url is not None:
                cur["stream_url"] = stream_url
            if item_obj is not None:
                cur["item_obj"] = item_obj
            cache_set("last_session", cur)

        def calc_cursor_for_item(items_list, target_id, target_name="", has_back_btn=True):
            found_raw_idx = -1
            t_id = str(target_id or "")
            t_nm = (target_name or "").strip().lower()

            for idx, it in enumerate(items_list):
                uid = get_item_unique_id(it)
                if t_id and uid == t_id:
                    found_raw_idx = idx
                    break

            if found_raw_idx < 0 and t_nm:
                for idx, it in enumerate(items_list):
                    nm = (
                        it.get("name")
                        or it.get("title")
                        or it.get("category_name")
                        or ""
                    ).strip().lower()
                    if nm == t_nm:
                        found_raw_idx = idx
                        break

            if found_raw_idx < 0:
                return 0, (1 if has_back_btn and items_list else 0), None

            p_offset = (found_raw_idx // PAGE_SIZE) * PAGE_SIZE
            local_pos = found_raw_idx - p_offset
            extra_top_btns = (1 if has_back_btn else 0) + (1 if p_offset > 0 else 0)
            return p_offset, local_pos + extra_top_btns, items_list[found_raw_idx]

        def make_tv_button(text_str, on_click_fn, key_str=None, height=46, expand=False):
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
                bgcolor="#161d30",
                border=ft.border.all(1, "#1e293b"),
                alignment=ft.alignment.center_left,
                expand=expand,
                on_click=lambda _: on_click_fn(),
            )
            box.data = {"text_ctrl": txt_ctrl, "base_text": text_str, "action": on_click_fn}
            return box

        def set_tv_button_text(box, new_text):
            box.data["base_text"] = new_text
            box.data["text_ctrl"].value = new_text

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
                            "listy" if state["in_profiles"] else ("ulubione" if state["in_fav"] else state["kind"])
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

        poster_img = ft.Image(
            src="",
            width=150,
            height=220,
            fit=ft.ImageFit.COVER,
            border_radius=8,
            visible=False,
        )
        status_info_title = ft.Text(
            "Wybierz kanał lub film z listy po lewej stronie",
            size=18,
            weight=ft.FontWeight.BOLD,
            color="#38bdf8",
            text_align=ft.TextAlign.CENTER,
        )
        status_info_sub = ft.Text(
            "🇵🇱 Polskie kanały oraz ostatnio dodane Filmy/Seriale są zawsze ustawiane na górze.\nUżyj przycisku '🇵🇱 Tylko Polskie', aby całkowicie ukryć zagraniczne pozycje.",
            size=13,
            color="#94a3b8",
            text_align=ft.TextAlign.CENTER,
            max_lines=6,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        video_placeholder = ft.Container(
            content=ft.Row(
                [
                    poster_img,
                    ft.Column(
                        [
                            ft.Text("📺", size=44),
                            status_info_title,
                            status_info_sub,
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=8,
                        expand=True,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=16,
            ),
            expand=True,
            bgcolor="#0f172a",
            border_radius=8,
            border=ft.border.all(1, "#1e293b"),
            alignment=ft.alignment.center,
            padding=15,
        )

        now_playing_txt = ft.Text(
            "▶ Pilot TV: ⬅️ ➡️ zmiana panelu | ⬆️ ⬇️ góra/dół | OK zatwierdź | WSTECZ powrót",
            weight=ft.FontWeight.BOLD,
            size=14,
            color="#f59e0b",
        )
        header_dev_lbl = ft.Text(
            f"📺 {device_name} [{device_id}]",
            size=13,
            color="#10b981",
            weight=ft.FontWeight.BOLD,
        )
        list_header_lbl = ft.Text(
            "📂 LISTA KATEGORII",
            size=13,
            weight=ft.FontWeight.BOLD,
            color="#38bdf8",
        )

        list_view = ft.ListView(
            expand=True, spacing=6, padding=4, auto_scroll=False
        )
        status_banner = ft.Text(
            f"🟢 Zdalny Cloud aktywny — Kod ID: {device_id}",
            color="#10b981",
            size=13,
        )

        def update_media_preview_async(title, item=None):
            icon_url = (item or {}).get("stream_icon") or (item or {}).get("cover") or ""
            if icon_url and icon_url.startswith("http"):
                poster_img.src = icon_url
                poster_img.visible = True
            else:
                poster_img.visible = False
            safe_ui_update()

            k = (item or {}).get("kind") or state["kind"]
            if k in ("vod", "series"):
                def _bg_tmdb():
                    tmdb = fetch_tmdb_details(title)
                    if not tmdb or stop_event.is_set():
                        return
                    if tmdb.get("poster"):
                        poster_img.src = tmdb["poster"]
                        poster_img.visible = True
                    plot = tmdb.get("plot") or ""
                    yr = tmdb.get("year") or ""
                    rt = tmdb.get("rating") or ""
                    meta = " • ".join(filter(None, [f"📅 {yr}" if yr else "", f"⭐ {rt}/10" if rt else ""]))
                    if meta or plot:
                        status_info_sub.value = f"{meta}\n\n{plot}" if meta else plot
                    safe_ui_update()

                threading.Thread(target=_bg_tmdb, daemon=True).start()

        def format_cred_label(field_key):
            if field_key == "server":
                val = creds["server"] or "(brak)"
                return f"🌐 Serwer: {val}"
            if field_key == "username":
                val = creds["username"] or "(kliknij OK, aby wpisać)"
                return f"👤 Użytkownik: {val}"
            if field_key == "password":
                val = ("*" * len(creds["password"])) if creds["password"] else "(kliknij OK, aby wpisać)"
                return f"🔑 Hasło: {val}"
            if field_key == "m3u_url":
                val = (creds["m3u_url"][:38] + "...") if len(creds["m3u_url"]) > 38 else (creds["m3u_url"] or "(opcjonalnie link M3U)")
                return f"📋 Lista M3U: {val}"
            return ""

        def refresh_login_button_labels():
            set_tv_button_text(btn_edit_srv, format_cred_label("server"))
            set_tv_button_text(btn_edit_usr, format_cred_label("username"))
            set_tv_button_text(btn_edit_pas, format_cred_label("password"))
            set_tv_button_text(btn_edit_m3u, format_cred_label("m3u_url"))
            set_tv_button_text(btn_saved_profiles_login, f"📋 WYBIERZ ZAPISANĄ LISTĘ ({len(profiles)})")

        def toggle_auto_login(_e=None):
            state["auto_login"] = not state["auto_login"]
            cache_set("auto_login", state["auto_login"])
            set_tv_button_text(
                btn_toggle_autologin,
                f"⚡ Auto-logowanie przy starcie: {'WŁ.' if state['auto_login'] else 'WYŁ.'}",
            )
            update_focus_styles(full_refresh=True)

        def open_profiles_dialog_from_login(_e=None):
            if not profiles:
                status_banner.value = "ℹ️ Brak zapisanych list. Wpisz dane poniżej lub wyślij listę przez Cloud."
                status_banner.color = "#f59e0b"
                safe_ui_update()
                return

            app_state["dialog_open"] = True
            col_items = []

            def _select_p(prof):
                dlg.open = False
                app_state["dialog_open"] = False
                apply_profile(prof)

            def _delete_p(prof):
                nonlocal profiles
                profiles = [p for p in profiles if p.get("name") != prof.get("name")]
                cache_set("profiles", profiles)
                refresh_login_button_labels()
                dlg.open = False
                app_state["dialog_open"] = False
                safe_ui_update()
                publish_status()

            for idx, p in enumerate(profiles):
                p_name = p.get("name", f"Lista {idx + 1}")
                p_mode = (p.get("mode") or "xtream").upper()
                p_host = (p.get("server") or p.get("m3u_url") or "")[:32]
                is_act = p_name == state.get("active_profile_name")
                prefix = "🟢 [AKTYWNA] " if is_act else ""

                row = ft.Row(
                    [
                        ft.ElevatedButton(
                            f"{prefix}{idx + 1}. [{p_mode}] {p_name} ({p_host})",
                            on_click=lambda _e, pr=p: _select_p(pr),
                            bgcolor="#2563eb" if is_act else "#1e293b",
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

        def open_field_editor(field_key, title_str, is_pass=False):
            app_state["dialog_open"] = True
            tf_in = ft.TextField(
                label=title_str,
                value=creds.get(field_key, ""),
                password=is_pass,
                can_reveal_password=is_pass,
                autofocus=True,
                on_submit=lambda _: _save_field(),
            )

            def _save_field(_e=None):
                creds[field_key] = (tf_in.value or "").strip() if field_key != "password" else (tf_in.value or "")
                cache_set(field_key, creds[field_key])
                refresh_login_button_labels()
                dlg.open = False
                app_state["dialog_open"] = False
                update_focus_styles(full_refresh=True)

            def _cancel_field(_e=None):
                dlg.open = False
                app_state["dialog_open"] = False
                update_focus_styles(full_refresh=True)

            dlg = ft.AlertDialog(
                title=ft.Text(f"✏️ Edytuj: {title_str}"),
                content=ft.Column([tf_in], tight=True),
                actions=[
                    ft.ElevatedButton("Anuluj", on_click=_cancel_field),
                    ft.ElevatedButton("💾 Zapisz", on_click=_save_field, bgcolor="#2563eb", color="white"),
                ],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        def open_search_dialog(_e=None):
            app_state["dialog_open"] = True
            search_in = ft.TextField(
                label="Wpisz szukaną nazwę kanału, filmu lub kategorii",
                value=state.get("search_query", ""),
                autofocus=True,
                on_submit=lambda _: _apply_search(),
            )

            def _apply_search(_e=None):
                state["search_query"] = (search_in.value or "").strip()
                state["page_offset"] = 0
                state["list_idx"] = 0
                set_tv_button_text(
                    btn_search,
                    f"🔍 Szukaj: {state['search_query'][:10]}" if state["search_query"] else "🔍 Szukaj",
                )
                dlg.open = False
                app_state["dialog_open"] = False
                render_list()

            def _clear_search(_e=None):
                state["search_query"] = ""
                state["page_offset"] = 0
                state["list_idx"] = 0
                set_tv_button_text(btn_search, "🔍 Szukaj")
                dlg.open = False
                app_state["dialog_open"] = False
                render_list()

            dlg = ft.AlertDialog(
                title=ft.Text("🔍 Filtruj / Szukaj na liście"),
                content=ft.Column([search_in], tight=True),
                actions=[
                    ft.ElevatedButton("Wyczyść filtr", on_click=_clear_search, bgcolor="#7f1d1d", color="white"),
                    ft.ElevatedButton("🔍 Filtruj", on_click=_apply_search, bgcolor="#2563eb", color="white"),
                ],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        def open_remote_settings_dlg(_e=None):
            nonlocal device_name, device_id
            app_state["dialog_open"] = True
            name_in = ft.TextField(
                label="Nazwa własna tego odbiornika",
                value=cache_get("device_name", device_name),
            )
            id_in = ft.TextField(
                label="Unikalny Kod ID",
                value=cache_get("device_id", device_id),
            )

            def _save(_):
                nonlocal device_name, device_id
                nn = name_in.value.strip() or "Android TV"
                ni = re.sub(r"[^a-zA-Z0-9_-]", "", id_in.value.strip()) or device_id
                device_name = nn
                device_id = ni
                cache_set("device_name", nn)
                cache_set("device_id", ni)
                header_dev_lbl.value = f"📺 {nn} [{ni}]"
                status_banner.value = f"🟢 Zdalny Cloud aktywny — Kod ID: {ni}"
                dlg.open = False
                app_state["dialog_open"] = False
                safe_ui_update()
                publish_status()

            dlg = ft.AlertDialog(
                title=ft.Text("🌐 Konfiguracja Zdalnego Odbiornika"),
                content=ft.Column(
                    [
                        ft.Text(
                            "Wpisz dokładnie ten sam Kod ID w aplikacji na komputerze lub w panelu WWW:",
                            size=12,
                        ),
                        name_in,
                        id_in,
                    ],
                    tight=True,
                    spacing=10,
                ),
                actions=[
                    ft.ElevatedButton(
                        "Anuluj",
                        on_click=lambda _: (setattr(dlg, "open", False), app_state.update({"dialog_open": False}), safe_ui_update()),
                    ),
                    ft.ElevatedButton(
                        "💾 Zapisz",
                        on_click=_save,
                        bgcolor="#2563eb",
                        color="white",
                    ),
                ],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        btn_remote_id = make_tv_button("🌐 Zmień Nazwę / Kod ID Odbiornika", open_remote_settings_dlg, height=40)
        btn_edit_srv = make_tv_button(
            format_cred_label("server"),
            lambda: open_field_editor("server", "Adres serwera (http://serwer:8080)"),
            height=42,
        )
        btn_edit_usr = make_tv_button(
            format_cred_label("username"),
            lambda: open_field_editor("username", "Nazwa użytkownika"),
            height=42,
        )
        btn_edit_pas = make_tv_button(
            format_cred_label("password"),
            lambda: open_field_editor("password", "Hasło", is_pass=True),
            height=42,
        )
        btn_edit_m3u = make_tv_button(
            format_cred_label("m3u_url"),
            lambda: open_field_editor("m3u_url", "Link do listy M3U / M3U8"),
            height=42,
        )
        btn_submit_login = make_tv_button("🚀 POŁĄCZ Z SERWEREM", lambda: do_login(), height=46)
        btn_saved_profiles_login = make_tv_button(
            f"📋 WYBIERZ ZAPISANĄ LISTĘ ({len(profiles)})",
            open_profiles_dialog_from_login,
            height=42,
        )
        btn_toggle_autologin = make_tv_button(
            f"⚡ Auto-logowanie przy starcie: {'WŁ.' if state['auto_login'] else 'WYŁ.'}",
            toggle_auto_login,
            height=40,
        )
        btn_exit_app = make_tv_button("🚪 ZAMKNIJ APLIKACJĘ", close_app_cleanly, height=40)

        login_items = [
            btn_remote_id,              # 0
            btn_edit_srv,               # 1
            btn_edit_usr,               # 2
            btn_edit_pas,               # 3
            btn_edit_m3u,               # 4
            btn_submit_login,           # 5
            btn_saved_profiles_login,   # 6
            btn_toggle_autologin,       # 7
            btn_exit_app,               # 8
        ]

        # =====================================================================
        # BEZPIECZNE URUCHAMIANIE VLC
        # =====================================================================
        def launch_vlc_intent(raw_url: str, title: str = ""):
            if not raw_url:
                return

            now = time.time()
            if now - state["last_vlc_ts"] < 1.2:
                return
            state["last_vlc_ts"] = now

            clean_u = raw_url.strip().splitlines()[0].strip()
            mode = state.get("vlc_mode", "vlc_clean")
            no_proto = re.sub(r"^https?://", "", clean_u, flags=re.IGNORECASE)
            scheme = "https" if clean_u.lower().startswith("https://") else "http"

            if mode == "vlc_clean":
                target_url = f"vlc://{no_proto}"
            elif mode == "intent":
                target_url = (
                    f"intent://{no_proto}#Intent;"
                    f"scheme={scheme};"
                    f"type=video/*;"
                    f"package=org.videolan.vlc;"
                    f"end"
                )
            elif mode == "vlc_full":
                target_url = clean_u if clean_u.startswith("vlc://") else f"vlc://{clean_u}"
            else:
                target_url = clean_u

            now_playing_txt.value = f"🚀 VLC ({mode}): {title or state['now_playing']}"
            status_info_title.value = f"▶ {title or state['now_playing']}"
            status_info_sub.value = (
                f"Aktywna lista: {state.get('active_profile_name') or 'Domyślna'}\n"
                f"Przekazano do VLC ({VLC_MODE_LABELS.get(mode, mode)}):\n{target_url[:68]}..."
            )
            safe_ui_update()

            def _delayed_launch():
                time.sleep(0.06)
                try:
                    page.launch_url(target_url)
                except Exception as ex:
                    now_playing_txt.value = f"❌ Błąd uruchomienia: {ex}"
                    safe_ui_update()

            threading.Thread(target=_delayed_launch, daemon=True).start()

        def open_in_android_vlc(_e=None):
            launch_vlc_intent(state["playing_url"], state["now_playing"])

        def toggle_auto_vlc(_e=None):
            state["auto_vlc"] = not state["auto_vlc"]
            cache_set("auto_vlc", state["auto_vlc"])
            set_tv_button_text(
                btn_auto_vlc,
                f"Auto-start: {'WŁ.' if state['auto_vlc'] else 'WYŁ.'}",
            )
            update_focus_styles(full_refresh=True)

        def toggle_vlc_scheme(_e=None):
            modes = ["vlc_clean", "intent", "vlc_full", "http"]
            cur = state.get("vlc_mode", "vlc_clean")
            nxt = modes[(modes.index(cur) + 1) % len(modes)] if cur in modes else "vlc_clean"
            state["vlc_mode"] = nxt
            cache_set("vlc_mode", nxt)
            set_tv_button_text(btn_scheme_vlc, VLC_MODE_LABELS[nxt])
            update_focus_styles(full_refresh=True)

        def play_stream_url(url, title, item=None):
            state["playing_url"] = url
            state["now_playing"] = title
            state["current_item"] = item
            state["is_playing"] = True
            state["paused"] = False
            now_playing_txt.value = f"▶ Wybrano: {title}"
            status_info_title.value = f"▶ {title}"
            set_tv_button_text(
                btn_fav_toggle,
                "⭐ Usuń z ulubionych" if is_favorite(item) else "⭐ Dodaj do ulubionych",
            )
            update_media_preview_async(title, item=item)

            if state["auto_vlc"]:
                launch_vlc_intent(url, title)
            publish_status()

        def stop_playback(_e=None):
            state["is_playing"] = False
            state["paused"] = False
            state["now_playing"] = "Brak aktywnego strumienia"
            now_playing_txt.value = "⏹ Zatrzymano odtwarzanie"
            status_info_title.value = "⏹ Zatrzymano"
            safe_ui_update()
            publish_status()

        def toggle_play_pause(_e=None):
            state["paused"] = not state["paused"]
            now_playing_txt.value = (
                "⏸ PAUZA" if state["paused"] else f"▶ {state['now_playing']}"
            )
            safe_ui_update()
            publish_status()

        def show_info_dialog(_e=None):
            title = state["now_playing"]
            app_state["dialog_open"] = True
            dlg = ft.AlertDialog(
                title=ft.Text(f"Informacje TMDB: {title}"),
                content=ft.Text("⏳ Pobieranie polskiego opisu z TMDB..."),
                actions=[
                    ft.ElevatedButton(
                        "Zamknij",
                        on_click=lambda _: (
                            setattr(dlg, "open", False),
                            app_state.update({"dialog_open": False}),
                            safe_ui_update(),
                        ),
                        bgcolor="#2563eb",
                        color="white",
                    )
                ],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

            def _w():
                tmdb = fetch_tmdb_details(title)
                if tmdb:
                    txt = f"Rok: {tmdb.get('year')}\nOcena: ⭐ {tmdb.get('rating')}/10\n\n{tmdb.get('plot') or 'Brak opisu.'}"
                else:
                    txt = "Brak danych w bazie TMDB dla tej pozycji."
                dlg.content = ft.Text(txt)
                safe_ui_update()

            threading.Thread(target=_w, daemon=True).start()

        # =====================================================================
        # ODŚWIEŻANIE LISTY Z USTAWIENIEM NOWOŚCI I KANAŁÓW PL NA GÓRZE
        # =====================================================================
        def refresh_current_list(_e=None):
            if not state["client"]:
                return

            state["streams_cache"].clear()
            state["categories"] = {"live": [], "vod": [], "series": []}
            state["search_query"] = ""
            set_tv_button_text(btn_search, "🔍 Szukaj")

            now_playing_txt.value = "🔄 Odświeżanie listy z serwera (🇵🇱 PL i 🆕 Nowości na górze)..."
            list_header_lbl.value = "⏳ Pobieranie najnowszych pozycji z serwera..."
            list_view.controls.clear()
            safe_ui_update()

            def _reload_worker():
                try:
                    if isinstance(state["client"], M3UClient):
                        state["client"].login()

                    cur_kind = state["kind"]
                    raw_cats = state["client"].get_categories(cur_kind) or []
                    state["categories"][cur_kind] = build_special_top_categories(cur_kind, raw_cats)

                    if state["view_mode"] in ("streams", "episodes") and state.get("active_cat") and not state["in_fav"] and not state["in_profiles"]:
                        act_cat = state["active_cat"]
                        items = state["client"].get_streams(cur_kind, act_cat.get("category_id")) or []
                        for it in items:
                            it["kind"] = cur_kind
                        cat_id = str(act_cat.get("category_id", "__ALL__"))
                        state["streams_cache"][(cur_kind, cat_id)] = items
                        state["streams"] = items
                        state["view_mode"] = "streams"
                        state["page_offset"] = 0
                        state["list_idx"] = 1 if items else 0
                        state["zone"] = "list"
                        now_playing_txt.value = f"✅ Odświeżono! Nowości i polskie pozycje są na górze ({len(items)} poz.)."
                        render_list()
                    else:
                        if cur_kind in ("vod", "series") and not state["in_fav"] and not state["in_profiles"]:
                            recent_cat = {
                                "category_id": "__RECENT__",
                                "category_name": "🆕 OSTATNIO DODANE (NOWOŚCI NA GÓRZE)",
                            }
                            now_playing_txt.value = "✅ Odświeżono! Najnowsze filmy/seriale ustawiono na górze listy."
                            open_category(recent_cat, force_reload=True)
                        else:
                            state["view_mode"] = "categories"
                            state["page_offset"] = 0
                            state["list_idx"] = 0
                            state["zone"] = "list"
                            now_playing_txt.value = "✅ Odświeżono listę kategorii (🇵🇱 Polskie na górze)."
                            render_list()
                except Exception as ex:
                    now_playing_txt.value = f"❌ Błąd odświeżania: {ex}"
                    safe_ui_update()

            threading.Thread(target=_reload_worker, daemon=True).start()

        # Zakładki u góry
        btn_tab_live = make_tv_button("📺 TV", lambda: switch_tab("live", 0), height=42, expand=True)
        btn_tab_vod = make_tv_button("🎬 Filmy", lambda: switch_tab("vod", 1), height=42, expand=True)
        btn_tab_ser = make_tv_button("🎞 Seriale", lambda: switch_tab("series", 2), height=42, expand=True)
        btn_tab_fav = make_tv_button("⭐ Ulubione", lambda: switch_tab("favorites", 3), height=42, expand=True)
        btn_tab_prof = make_tv_button("📋 Listy", lambda: switch_tab("profiles", 4), height=42, expand=True)
        tab_buttons = [btn_tab_live, btn_tab_vod, btn_tab_ser, btn_tab_fav, btn_tab_prof]

        btn_vlc = make_tv_button("🚀 Otwórz w VLC", open_in_android_vlc, height=42)
        btn_only_pl = make_tv_button(
            f"🇵🇱 Tylko Polskie: {'WŁ.' if state['only_pl'] else 'WYŁ.'}",
            toggle_only_pl,
            height=42,
        )
        btn_refresh_cat = make_tv_button("🔄 Odśwież Nowości", refresh_current_list, height=42)
        btn_search = make_tv_button("🔍 Szukaj", open_search_dialog, height=42)
        btn_fav_toggle = make_tv_button("⭐ Dodaj do ulubionych", toggle_favorite, height=42)
        btn_tmdb = make_tv_button("ℹ Opis TMDB", show_info_dialog, height=42)
        btn_auto_vlc = make_tv_button(
            f"Auto-start: {'WŁ.' if state['auto_vlc'] else 'WYŁ.'}",
            toggle_auto_vlc,
            height=42,
        )
        btn_scheme_vlc = make_tv_button(
            VLC_MODE_LABELS[state["vlc_mode"]],
            toggle_vlc_scheme,
            height=42,
        )
        btn_change_list = make_tv_button("📋 Zmień listę", lambda: switch_tab("profiles", 4), height=42)
        btn_cloud_id = make_tv_button("🌐 Kod ID", open_remote_settings_dlg, height=42)
        btn_logout = make_tv_button("⇄ Logowanie", lambda: do_logout(), height=42)
        btn_quit_dash = make_tv_button("🚪 Wyjdź", close_app_cleanly, height=42)

        right_buttons = [
            btn_vlc,
            btn_only_pl,
            btn_refresh_cat,
            btn_search,
            btn_fav_toggle,
            btn_tmdb,
            btn_auto_vlc,
            btn_scheme_vlc,
            btn_change_list,
            btn_cloud_id,
            btn_logout,
            btn_quit_dash,
        ]
        list_item_buttons = []
        list_item_actions = []

        left_panel = ft.Container(
            width=450,
            bgcolor="#111625",
            padding=10,
            border_radius=10,
            border=ft.border.all(3, "#38bdf8"),
        )
        right_panel = ft.Container(
            expand=True,
            bgcolor="#111625",
            padding=10,
            border_radius=10,
            border=ft.border.all(1, "#1e293b"),
        )

        login_view = ft.Container(expand=True, visible=True)
        dashboard_view = ft.Container(expand=True, visible=False)

        # =====================================================================
        # MIKRO-ODŚWIEŻANIE FOKUSU (b.update() DLA POJEDYNCZEGO KAFELKA)
        # =====================================================================
        def style_single_list_btn(idx, is_focused, single_update=False):
            if not (0 <= idx < len(list_item_buttons)):
                return
            b = list_item_buttons[idx]
            base_t = b.data["base_text"]
            if is_focused:
                b.bgcolor = "#0284c7"
                b.border = ft.border.all(3, "#f59e0b")
                b.data["text_ctrl"].color = "#ffffff"
                b.data["text_ctrl"].size = 15
                b.data["text_ctrl"].value = f"▶  {base_t}"
            else:
                b.bgcolor = "#161d30"
                b.border = ft.border.all(1, "#1e293b")
                b.data["text_ctrl"].color = "#cbd5e1"
                b.data["text_ctrl"].size = 14
                b.data["text_ctrl"].value = base_t
            if single_update:
                try:
                    b.update()
                except Exception:
                    pass

        def update_focus_styles(full_refresh=False):
            if login_view.visible:
                for i, item in enumerate(login_items):
                    is_focused = state["zone"] == "login" and state["login_idx"] == i
                    base_t = item.data["base_text"]
                    if is_focused:
                        item.bgcolor = "#f59e0b"
                        item.border = ft.border.all(3, "#ffffff")
                        item.data["text_ctrl"].color = "black"
                        item.data["text_ctrl"].value = f"▶  {base_t}"
                    else:
                        if i == 0:
                            item.bgcolor = "#0d9488"
                        elif i == 5:
                            item.bgcolor = "#2563eb"
                        elif i == 6:
                            item.bgcolor = "#4f46e5"
                        elif i == 8:
                            item.bgcolor = "#7f1d1d"
                        else:
                            item.bgcolor = "#161d30"
                        item.border = ft.border.all(1, "#334155")
                        item.data["text_ctrl"].color = "white"
                        item.data["text_ctrl"].value = base_t
                safe_ui_update()
                return

            if not dashboard_view.visible:
                return

            if (
                not full_refresh
                and state["zone"] == "list"
                and state["prev_zone"] == "list"
                and 0 <= state["prev_list_idx"] < len(list_item_buttons)
            ):
                style_single_list_btn(state["prev_list_idx"], False, single_update=True)
                style_single_list_btn(state["list_idx"], True, single_update=True)
                state["prev_list_idx"] = state["list_idx"]
                try:
                    list_view.scroll_to(key=f"item_{state['list_idx']}", duration=35)
                except Exception:
                    pass
                return

            left_panel.border = ft.border.all(
                3 if state["zone"] in ("tabs", "list") else 1,
                "#f59e0b" if state["zone"] in ("tabs", "list") else "#1e293b",
            )
            right_panel.border = ft.border.all(
                3 if state["zone"] == "right" else 1,
                "#f59e0b" if state["zone"] == "right" else "#1e293b",
            )

            for i, b in enumerate(tab_buttons):
                is_focused = state["zone"] == "tabs" and state["tab_idx"] == i
                is_current_kind = (
                    (not state["in_fav"] and not state["in_profiles"] and i == 0 and state["kind"] == "live")
                    or (not state["in_fav"] and not state["in_profiles"] and i == 1 and state["kind"] == "vod")
                    or (not state["in_fav"] and not state["in_profiles"] and i == 2 and state["kind"] == "series")
                    or (state["in_fav"] and i == 3)
                    or (state["in_profiles"] and i == 4)
                )
                base_t = b.data["base_text"]
                if is_focused:
                    b.bgcolor = "#f59e0b"
                    b.border = ft.border.all(3, "#ffffff")
                    b.data["text_ctrl"].color = "black"
                    b.data["text_ctrl"].value = f"▶ {base_t}"
                elif is_current_kind:
                    b.bgcolor = "#2563eb"
                    b.border = ft.border.all(2, "#60a5fa")
                    b.data["text_ctrl"].color = "white"
                    b.data["text_ctrl"].value = base_t
                else:
                    b.bgcolor = "#161d30"
                    b.border = ft.border.all(1, "#1e293b")
                    b.data["text_ctrl"].color = "#94a3b8"
                    b.data["text_ctrl"].value = base_t

            for i in range(len(list_item_buttons)):
                style_single_list_btn(i, state["zone"] == "list" and state["list_idx"] == i, single_update=False)

            for i, b in enumerate(right_buttons):
                is_focused = state["zone"] == "right" and state["right_idx"] == i
                base_t = b.data["base_text"]
                if is_focused:
                    b.bgcolor = "#f59e0b"
                    b.border = ft.border.all(3, "#ffffff")
                    b.data["text_ctrl"].color = "black"
                    b.data["text_ctrl"].value = f"▶ {base_t}"
                else:
                    if b == btn_refresh_cat:
                        b.bgcolor = "#0d9488"
                    elif b == btn_only_pl and state["only_pl"]:
                        b.bgcolor = "#15803d"
                    else:
                        b.bgcolor = "#1e293b"
                    b.border = ft.border.all(1, "#334155")
                    b.data["text_ctrl"].color = "white"
                    b.data["text_ctrl"].value = base_t

            state["prev_zone"] = state["zone"]
            state["prev_list_idx"] = state["list_idx"]
            safe_ui_update()

            if state["zone"] == "list" and list_item_buttons:
                idx = max(0, min(state["list_idx"], len(list_item_buttons) - 1))
                try:
                    list_view.scroll_to(key=f"item_{idx}", duration=45)
                except Exception:
                    pass

        def change_page(delta):
            total_len = len(state.get("filtered", []))
            if total_len <= 0:
                return
            new_off = max(0, min(state["page_offset"] + delta, ((total_len - 1) // PAGE_SIZE) * PAGE_SIZE))
            if new_off != state["page_offset"]:
                state["page_offset"] = new_off
                state["list_idx"] = 0
                render_list()

        def delete_saved_profile(prof_name):
            nonlocal profiles
            profiles = [p for p in profiles if p.get("name") != prof_name]
            cache_set("profiles", profiles)
            refresh_login_button_labels()
            now_playing_txt.value = f"🗑 Usunięto listę: {prof_name}"
            render_list()
            publish_status()

        def render_list():
            list_view.controls.clear()
            list_item_buttons.clear()
            list_item_actions.clear()

            v_mode = state["view_mode"]
            btn_idx = 0
            pl_ids = get_current_pl_cat_ids()

            # WIDOK 1: MENEDŻER ZAPISANYCH LIST
            if state["in_profiles"] or v_mode == "profiles":
                list_header_lbl.value = f"📋 ZAPISANE LISTY / SERWERY ({len(profiles)})"

                b_add_new = make_tv_button(
                    "➕ DODAJ / EDYTUJ LISTĘ RĘCZNIE",
                    lambda: do_logout(),
                    key_str=f"item_{btn_idx}",
                    height=44,
                )
                list_item_buttons.append(b_add_new)
                list_item_actions.append(do_logout)
                list_view.controls.append(b_add_new)
                btn_idx += 1

                for idx, prof in enumerate(profiles):
                    p_name = prof.get("name", f"Lista {idx + 1}")
                    p_mode = (prof.get("mode") or "xtream").upper()
                    p_host = (prof.get("server") or prof.get("m3u_url") or "")[:28]
                    is_act = p_name == state.get("active_profile_name")
                    act_badge = "🟢 [AKTYWNA] " if is_act else "📋 "

                    lbl = f"{idx + 1}. {act_badge}[{p_mode}] {p_name} ({p_host})"
                    act_fn = lambda pr=prof: apply_profile(pr)
                    b_prof = make_tv_button(lbl, act_fn, key_str=f"item_{btn_idx}", height=48)
                    list_item_buttons.append(b_prof)
                    list_item_actions.append(act_fn)
                    list_view.controls.append(b_prof)
                    btn_idx += 1

                    if not is_act or len(profiles) > 1:
                        del_lbl = f"   ↳ 🗑 Usuń listę: {p_name}"
                        del_fn = lambda nm=p_name: delete_saved_profile(nm)
                        b_del = make_tv_button(del_lbl, del_fn, key_str=f"item_{btn_idx}", height=38)
                        list_item_buttons.append(b_del)
                        list_item_actions.append(del_fn)
                        list_view.controls.append(b_del)
                        btn_idx += 1

                if state["list_idx"] >= len(list_item_buttons):
                    state["list_idx"] = max(0, len(list_item_buttons) - 1)
                update_focus_styles(full_refresh=True)
                return

            # WIDOK 2: KATEGORIE / KANAŁY / ODCINKI / ULUBIONE
            only_pl_tag = " [🇵🇱 TYLKO PL]" if state["only_pl"] else ""
            if state["in_fav"]:
                kind_lbl = f"⭐ ULUBIONE{only_pl_tag}"
            else:
                kind_lbl = {
                    "live": f"TV NA ŻYWO{only_pl_tag}",
                    "vod": f"FILMY (🆕 Nowości){only_pl_tag}",
                    "series": f"SERIALE (🆕 Nowości){only_pl_tag}",
                }[state["kind"]]

            if v_mode == "categories":
                raw = list(state["categories"].get(state["kind"], []))
                if state["only_pl"]:
                    filtered_cats = [
                        c for c in raw
                        if str(c.get("category_id", "")) in ("__ALL__", "__RECENT__")
                        or is_polish_text(c.get("category_name", ""))
                    ]
                    # Jeśli lista nie ma oznaczonych tagiem PL kategorii, nie zostawiaj pustej listy
                    if len(filtered_cats) > (2 if state["kind"] in ("vod", "series") else 1):
                        raw = filtered_cats
                hdr_base = f"📂 {kind_lbl}"
            elif v_mode == "episodes":
                raw = list(state["episodes"])
                hdr_base = f"🎞 {state['active_series_title']}"
            elif state["in_fav"]:
                raw = list(favorites_items)
                if state["only_pl"]:
                    raw = [it for it in raw if is_polish_item(it, pl_ids)]
                hdr_base = f"⭐ ULUBIONE POZYCJE{only_pl_tag}"
            else:
                raw = list(state["streams"])
                act_cat = state.get("active_cat") or {}
                cat_nm = act_cat.get("category_name", "Lista")
                cat_id_str = str(act_cat.get("category_id", ""))
                cat_is_pl = (
                    cat_id_str not in ("__ALL__", "__RECENT__")
                    and (cat_id_str in pl_ids or is_polish_text(cat_nm))
                )
                # Gdy włączony jest filtr 'Tylko Polskie', a jesteśmy w kategorii ogólnej (Wszystkie / Ostatnio dodane)
                # lub zagranicznej, filtrujemy pojedyncze pozycje do wyłącznie polskich.
                if state["only_pl"] and not cat_is_pl:
                    only_pl_streams = [it for it in raw if is_polish_item(it, pl_ids)]
                    if only_pl_streams:
                        raw = only_pl_streams
                hdr_base = f"📺 {cat_nm}{only_pl_tag}"

            q = state.get("search_query", "").strip().lower()
            if q:
                raw = [
                    it for it in raw
                    if q in (
                        it.get("name")
                        or it.get("title")
                        or it.get("display_title")
                        or it.get("category_name")
                        or ""
                    ).lower()
                ]
                list_header_lbl.value = f"{hdr_base} [🔍 '{q}': {len(raw)}]"
            else:
                list_header_lbl.value = f"{hdr_base} ({len(raw)})"

            state["filtered"] = raw

            if q:
                def _clear_q():
                    state["search_query"] = ""
                    set_tv_button_text(btn_search, "🔍 Szukaj")
                    state["page_offset"] = 0
                    state["list_idx"] = 0
                    render_list()

                b_clr = make_tv_button(
                    f"❌ WYCZYŚĆ FILTR SZUKANIA: '{state['search_query']}'",
                    _clear_q,
                    key_str=f"item_{btn_idx}",
                    height=40,
                )
                list_item_buttons.append(b_clr)
                list_item_actions.append(_clear_q)
                list_view.controls.append(b_clr)
                btn_idx += 1

            if v_mode == "episodes":
                b_back = make_tv_button(
                    "↩ WRÓĆ DO LISTY SERIALI",
                    back_to_streams,
                    key_str=f"item_{btn_idx}",
                    height=44,
                )
                list_item_buttons.append(b_back)
                list_item_actions.append(back_to_streams)
                list_view.controls.append(b_back)
                btn_idx += 1
            elif v_mode == "streams" and not state["in_fav"]:
                b_back = make_tv_button(
                    "↩ WRÓĆ DO KATEGORII",
                    back_to_cats,
                    key_str=f"item_{btn_idx}",
                    height=44,
                )
                list_item_buttons.append(b_back)
                list_item_actions.append(back_to_cats)
                list_view.controls.append(b_back)
                btn_idx += 1

            start = state["page_offset"]
            end = start + PAGE_SIZE
            slice_items = raw[start:end]

            if start > 0:
                b_prev = make_tv_button(
                    f"⬆ POPRZEDNIE ({max(1, start - PAGE_SIZE + 1)} - {start})",
                    lambda: change_page(-PAGE_SIZE),
                    key_str=f"item_{btn_idx}",
                    height=42,
                )
                list_item_buttons.append(b_prev)
                list_item_actions.append(lambda: change_page(-PAGE_SIZE))
                list_view.controls.append(b_prev)
                btn_idx += 1

            for local_i, item in enumerate(slice_items):
                real_num = start + local_i + 1
                if v_mode == "categories":
                    cat_name = item.get("category_name", "Kategoria")
                    cat_id_str = str(item.get("category_id", ""))
                    pl_tag = "🇵🇱 " if (cat_id_str not in ("__ALL__", "__RECENT__") and is_polish_text(cat_name)) else ""
                    action_fn = lambda c=item: open_category(c)
                    label = f"{real_num}. 📁 {pl_tag}{cat_name}"
                elif v_mode == "episodes":
                    ep_title = item.get("display_title") or item.get("title") or "Odcinek"
                    action_fn = lambda ep=item: play_episode(ep)
                    label = f"{real_num}. 🎞 {ep_title}"
                else:
                    k = item.get("kind") or state["kind"]
                    fav_icon = "⭐ " if is_favorite(item) else ""
                    pl_icon = "🇵🇱 " if is_polish_item(item, pl_ids) else ""
                    badge = (
                        "[TV]"
                        if k == "live"
                        else ("[FILM]" if k == "vod" else "[SERIAL]")
                    )
                    nm = item.get("name") or item.get("title") or "Pozycja"
                    action_fn = lambda it=item: on_stream_click(it)
                    label = f"{real_num}. {fav_icon}{pl_icon}{badge} {nm}"

                b_item = make_tv_button(
                    label,
                    action_fn,
                    key_str=f"item_{btn_idx}",
                    height=48,
                )
                list_item_buttons.append(b_item)
                list_item_actions.append(action_fn)
                list_view.controls.append(b_item)
                btn_idx += 1

            if end < len(raw):
                b_next = make_tv_button(
                    f"⬇ NASTĘPNE ({end + 1} - {min(len(raw), end + PAGE_SIZE)}) z {len(raw)}",
                    lambda: change_page(PAGE_SIZE),
                    key_str=f"item_{btn_idx}",
                    height=42,
                )
                list_item_buttons.append(b_next)
                list_item_actions.append(lambda: change_page(PAGE_SIZE))
                list_view.controls.append(b_next)
                btn_idx += 1

            if state["list_idx"] >= len(list_item_buttons):
                state["list_idx"] = max(0, len(list_item_buttons) - 1)

            update_focus_styles(full_refresh=True)

        def back_to_streams():
            state["view_mode"] = "streams"
            p_off, l_idx, _ = calc_cursor_for_item(
                state["streams"],
                state.get("active_series_id", ""),
                state.get("active_series_title", ""),
                has_back_btn=True,
            )
            state["page_offset"] = p_off
            state["list_idx"] = l_idx
            render_list()

        def back_to_cats():
            if state["in_fav"] or state["in_profiles"]:
                switch_tab("live", 0)
                return

            prev_cat = state.get("active_cat")
            prev_cat_id = str((prev_cat or {}).get("category_id", ""))
            prev_cat_nm = (prev_cat or {}).get("category_name", "")

            state["view_mode"] = "categories"
            state["active_cat"] = None

            def _apply_cats_cursor():
                cats_list = state["categories"].get(state["kind"], [])
                if state["only_pl"]:
                    filtered_c = [
                        c for c in cats_list
                        if str(c.get("category_id", "")) in ("__ALL__", "__RECENT__")
                        or is_polish_text(c.get("category_name", ""))
                    ]
                    if len(filtered_c) > (2 if state["kind"] in ("vod", "series") else 1):
                        cats_list = filtered_c

                if prev_cat_id or prev_cat_nm:
                    p_off, l_idx, _ = calc_cursor_for_item(
                        cats_list, prev_cat_id, prev_cat_nm, has_back_btn=False
                    )
                    state["page_offset"] = p_off
                    state["list_idx"] = l_idx
                else:
                    state["page_offset"] = 0
                    state["list_idx"] = 0
                render_list()

            if not state["categories"].get(state["kind"]):
                list_header_lbl.value = "⏳ Pobieranie kategorii (🇵🇱 Polskie na górze)..."
                safe_ui_update()

                def _w():
                    try:
                        cats = state["client"].get_categories(state["kind"]) or []
                        state["categories"][state["kind"]] = build_special_top_categories(
                            state["kind"], cats
                        )
                        _apply_cats_cursor()
                    except Exception:
                        pass

                threading.Thread(target=_w, daemon=True).start()
            else:
                _apply_cats_cursor()

        def open_category(cat_obj, target_stream_id=None, target_stream_name=None, force_reload=False):
            state["view_mode"] = "streams"
            state["active_cat"] = cat_obj
            state["page_offset"] = 0
            state["list_idx"] = 0

            save_last_session(kind=state["kind"], cat_obj=cat_obj)

            cat_id = str(cat_obj.get("category_id", "__ALL__"))
            cache_key = (state["kind"], cat_id)

            if not force_reload and cache_key in state["streams_cache"]:
                items = state["streams_cache"][cache_key]
                state["streams"] = items
                if target_stream_id or target_stream_name:
                    p_off, l_idx, matched = calc_cursor_for_item(
                        items, target_stream_id, target_stream_name, has_back_btn=True
                    )
                    state["page_offset"] = p_off
                    state["list_idx"] = l_idx
                elif items:
                    state["page_offset"] = 0
                    state["list_idx"] = 1
                render_list()
                return

            list_view.controls.clear()
            list_header_lbl.value = f"⏳ Ładowanie i sortowanie: {cat_obj.get('category_name', '')}..."
            safe_ui_update()

            def _w():
                try:
                    items = (
                        state["client"].get_streams(
                            state["kind"], cat_obj.get("category_id")
                        )
                        or []
                    )
                    for it in items:
                        it["kind"] = state["kind"]
                    state["streams"] = items
                    state["streams_cache"][cache_key] = items

                    if target_stream_id or target_stream_name:
                        p_off, l_idx, matched = calc_cursor_for_item(
                            items,
                            target_stream_id,
                            target_stream_name,
                            has_back_btn=True,
                        )
                        state["page_offset"] = p_off
                        state["list_idx"] = l_idx
                        if matched:
                            m_title = matched.get("name") or matched.get("title") or ""
                            now_playing_txt.value = f"⏮ Ostatnio oglądane: {m_title} (naciśnij OK)"
                            status_info_title.value = f"⏮ {m_title}"
                    elif items:
                        state["page_offset"] = 0
                        state["list_idx"] = 1

                    render_list()
                except Exception:
                    pass

            threading.Thread(target=_w, daemon=True).start()

        def play_episode(ep_item):
            ext = ep_item.get("container_extension", "mp4") or "mp4"
            url = state["client"].build_stream_url(
                "series",
                ep_item.get("id"),
                ext,
                item=ep_item,
            )
            ep_title = ep_item.get("display_title") or ep_item.get("title") or "Odcinek"
            save_last_session(stream_url=url)
            play_stream_url(url, ep_title, item=ep_item)

        def on_stream_click(item):
            k = item.get("kind") or state["kind"]
            title = item.get("name") or item.get("title") or "Strumień"
            uid = get_item_unique_id(item)

            save_last_session(
                kind=k,
                cat_obj=state.get("active_cat"),
                stream_id=uid,
                stream_name=title,
                item_obj=item,
            )

            if k == "series":
                state["active_series_id"] = uid
                state["active_series_title"] = title
                update_media_preview_async(title, item=item)
                list_view.controls.clear()
                list_header_lbl.value = f"⏳ Pobieranie odcinków: {title}..."
                safe_ui_update()

                def _w():
                    try:
                        info = state["client"].get_series_info(item.get("series_id"))
                        eps_map = info.get("episodes") or {}
                        flat_eps = []
                        sorted_seasons = sorted(
                            eps_map.keys(),
                            key=lambda x: int(x) if str(x).isdigit() else 999,
                        )
                        for s_num in sorted_seasons:
                            for ep in eps_map[s_num]:
                                ep_num = ep.get("episode_num", "")
                                ep_raw_title = ep.get("title") or f"Odcinek {ep_num}"
                                ep_copy = dict(ep)
                                ep_copy["display_title"] = (
                                    f"S{int(s_num):02d}E{int(ep_num):02d} - {ep_raw_title}"
                                    if str(s_num).isdigit() and str(ep_num).isdigit()
                                    else f"Sezon {s_num} - {ep_raw_title}"
                                )
                                flat_eps.append(ep_copy)

                        state["episodes"] = flat_eps
                        state["view_mode"] = "episodes"
                        state["page_offset"] = 0
                        state["list_idx"] = 1 if flat_eps else 0
                        render_list()
                    except Exception:
                        back_to_streams()

                threading.Thread(target=_w, daemon=True).start()
                return

            ext = item.get(
                "container_extension", "ts" if k == "live" else "mp4"
            ) or ("ts" if k == "live" else "mp4")
            url = state["client"].build_stream_url(
                k, item.get("stream_id"), ext, item=item
            )
            save_last_session(stream_url=url, item_obj=item)
            play_stream_url(url, title, item=item)

        def switch_tab(tab_key, tab_idx=0):
            state["tab_idx"] = tab_idx
            state["page_offset"] = 0
            state["list_idx"] = 0
            state["zone"] = "list"

            if tab_key == "favorites":
                state["in_fav"] = True
                state["in_profiles"] = False
                state["view_mode"] = "streams"
                state["active_cat"] = None
                render_list()
            elif tab_key == "profiles":
                state["in_profiles"] = True
                state["in_fav"] = False
                state["view_mode"] = "profiles"
                state["active_cat"] = None
                render_list()
            else:
                state["in_fav"] = False
                state["in_profiles"] = False
                state["kind"] = tab_key
                back_to_cats()
            publish_status()

        def restore_last_session():
            last = cache_get("last_session", {})
            if not isinstance(last, dict) or not last.get("category_id"):
                switch_tab("live", 0)
                return

            saved_kind = last.get("kind", "live")
            if saved_kind not in ("live", "vod", "series"):
                saved_kind = "live"

            tab_map = {"live": 0, "vod": 1, "series": 2}
            state["kind"] = saved_kind
            state["tab_idx"] = tab_map[saved_kind]
            state["in_fav"] = False
            state["in_profiles"] = False
            state["zone"] = "list"

            saved_cat_id = str(last.get("category_id", ""))
            saved_cat_name = last.get("category_name", "Ostatnia kategoria")
            saved_stream_id = str(last.get("stream_id", ""))
            saved_stream_name = last.get("stream_name", "")
            saved_stream_url = last.get("stream_url", "")
            saved_item = last.get("item_obj")

            if saved_stream_name:
                state["now_playing"] = saved_stream_name
                state["playing_url"] = saved_stream_url
                state["current_item"] = saved_item

            list_view.controls.clear()
            list_header_lbl.value = f"⏳ Przywracanie: {saved_cat_name}..."
            safe_ui_update()

            def _w():
                try:
                    cats = state["client"].get_categories(saved_kind) or []
                    all_cats = build_special_top_categories(saved_kind, cats)
                    state["categories"][saved_kind] = all_cats

                    target_cat = None
                    for c in all_cats:
                        if str(c.get("category_id", "")) == saved_cat_id:
                            target_cat = c
                            break
                    if not target_cat and saved_cat_name:
                        for c in all_cats:
                            if (
                                (c.get("category_name") or "").strip().lower()
                                == saved_cat_name.strip().lower()
                            ):
                                target_cat = c
                                break

                    if not target_cat:
                        state["view_mode"] = "categories"
                        state["page_offset"] = 0
                        state["list_idx"] = 0
                        render_list()
                        return

                    open_category(
                        target_cat,
                        target_stream_id=saved_stream_id,
                        target_stream_name=saved_stream_name,
                    )
                except Exception:
                    switch_tab("live", 0)

            threading.Thread(target=_w, daemon=True).start()

        # =====================================================================
        # OBSŁUGA PILOTA ANDROID TV
        # =====================================================================
        def on_key_event(e: ft.KeyboardEvent):
            if app_state["dialog_open"]:
                return

            key = e.key

            if login_view.visible:
                if key == "Arrow Down":
                    if state["login_idx"] < len(login_items) - 1:
                        state["login_idx"] += 1
                    update_focus_styles(full_refresh=True)
                elif key == "Arrow Up":
                    if state["login_idx"] > 0:
                        state["login_idx"] -= 1
                    update_focus_styles(full_refresh=True)
                elif key in ("Enter", "Select", " "):
                    now = time.time()
                    if now - state["last_ok_ts"] < 0.35:
                        return
                    state["last_ok_ts"] = now

                    idx = state["login_idx"]
                    if 0 <= idx < len(login_items):
                        login_items[idx].data["action"]()
                return

            if not dashboard_view.visible:
                return

            if key in ("Page Down", "Channel Down"):
                change_page(PAGE_SIZE)
                return
            elif key in ("Page Up", "Channel Up"):
                change_page(-PAGE_SIZE)
                return

            if key == "Arrow Right":
                if state["zone"] == "tabs":
                    if state["tab_idx"] < len(tab_buttons) - 1:
                        state["tab_idx"] += 1
                    else:
                        state["zone"] = "right"
                        state["right_idx"] = 0
                elif state["zone"] == "list":
                    state["zone"] = "right"
                    state["right_idx"] = 0
                elif state["zone"] == "right":
                    if state["right_idx"] < len(right_buttons) - 1:
                        state["right_idx"] += 1
                update_focus_styles(full_refresh=True)

            elif key == "Arrow Left":
                if state["zone"] == "right":
                    if state["right_idx"] > 0:
                        state["right_idx"] -= 1
                    else:
                        state["zone"] = "list"
                elif state["zone"] == "tabs":
                    if state["tab_idx"] > 0:
                        state["tab_idx"] -= 1
                update_focus_styles(full_refresh=True)

            elif key == "Arrow Down":
                if state["zone"] == "tabs":
                    state["zone"] = "list"
                    if state["list_idx"] >= len(list_item_buttons):
                        state["list_idx"] = 0
                    update_focus_styles(full_refresh=True)
                elif state["zone"] == "list":
                    if list_item_buttons and state["list_idx"] < len(list_item_buttons) - 1:
                        state["list_idx"] += 1
                        update_focus_styles(full_refresh=False)
                elif state["zone"] == "right":
                    if right_buttons and state["right_idx"] < len(right_buttons) - 1:
                        state["right_idx"] += 1
                        update_focus_styles(full_refresh=True)

            elif key == "Arrow Up":
                if state["zone"] == "list":
                    if state["list_idx"] > 0:
                        state["list_idx"] -= 1
                        update_focus_styles(full_refresh=False)
                    else:
                        state["zone"] = "tabs"
                        update_focus_styles(full_refresh=True)
                elif state["zone"] == "right":
                    if state["right_idx"] > 0:
                        state["right_idx"] -= 1
                        update_focus_styles(full_refresh=True)

            elif key in ("Enter", "Select", " ", "Media Play / Pause"):
                now = time.time()
                if now - state["last_ok_ts"] < 0.35:
                    return
                state["last_ok_ts"] = now

                if state["zone"] == "tabs":
                    if 0 <= state["tab_idx"] < len(tab_buttons):
                        if state["tab_idx"] == 3:
                            switch_tab("favorites", 3)
                        elif state["tab_idx"] == 4:
                            switch_tab("profiles", 4)
                        else:
                            kinds = ["live", "vod", "series"]
                            switch_tab(kinds[state["tab_idx"]], state["tab_idx"])
                elif state["zone"] == "list":
                    if list_item_actions and 0 <= state["list_idx"] < len(list_item_actions):
                        list_item_actions[state["list_idx"]]()
                elif state["zone"] == "right":
                    idx = state["right_idx"]
                    if 0 <= idx < len(right_buttons):
                        right_buttons[idx].data["action"]()

            elif key in ("Backspace", "Escape"):
                if state["view_mode"] == "episodes":
                    back_to_streams()
                elif state["view_mode"] == "streams" and not state["in_fav"] and not state["in_profiles"]:
                    back_to_cats()

        page.on_keyboard_event = on_key_event

        # =====================================================================
        # LOGOWANIE I ZAPISYWANIE WGRANYCH LIST W PAMIĘCI APK
        # =====================================================================
        def do_login(_e=None, is_auto=False, profile_name_hint=""):
            if state["is_logging_in"]:
                return
            state["is_logging_in"] = True

            if is_auto:
                status_banner.value = "⚡ Automatyczne łączenie z zapisanym serwerem..."
            else:
                status_banner.value = "⏳ Łączenie z serwerem..."
            status_banner.color = "#38bdf8"
            safe_ui_update()

            def _w():
                try:
                    m3u_val = creds["m3u_url"].strip()
                    srv_val = creds["server"].strip()
                    usr_val = creds["username"].strip()
                    pas_val = creds["password"]

                    if m3u_val and (not usr_val or not pas_val):
                        ext = extract_xtream_from_m3u_url(m3u_val)
                        if ext:
                            client = XtreamClient(*ext)
                        else:
                            client = M3UClient(m3u_val)
                    else:
                        client = XtreamClient(srv_val, usr_val, pas_val)

                    client.login()
                    state["client"] = client
                    state["categories"] = {"live": [], "vod": [], "series": []}
                    state["streams_cache"].clear()

                    saved_name = register_or_update_profile(
                        profile_name_hint or state.get("active_profile_name", ""),
                        srv_val,
                        usr_val,
                        pas_val,
                        m3u_val,
                    )
                    state["active_profile_name"] = saved_name or f"{client.username} ({client.server})"

                    cache_set("server", srv_val)
                    cache_set("username", usr_val)
                    cache_set("password", pas_val)
                    cache_set("m3u_url", m3u_val)
                    refresh_login_button_labels()

                    header_dev_lbl.value = f"📺 {device_name} [{device_id}]  •  📋 {state['active_profile_name']}"

                    login_view.visible = False
                    dashboard_view.visible = True
                    state["zone"] = "list"

                    restore_last_session()
                    publish_status()
                except Exception as ex:
                    status_banner.value = f"❌ Błąd logowania: {ex}"
                    status_banner.color = "#f87171"
                    state["login_idx"] = 5
                    update_focus_styles(full_refresh=True)
                finally:
                    state["is_logging_in"] = False

            threading.Thread(target=_w, daemon=True).start()

        def do_logout(_e=None):
            stop_playback()
            state["client"] = None
            state["streams_cache"].clear()
            dashboard_view.visible = False
            login_view.visible = True
            state["zone"] = "login"
            state["login_idx"] = 5
            refresh_login_button_labels()
            status_banner.value = "ℹ️ Możesz wybrać inną zapisaną listę lub wpisać nowe dane."
            status_banner.color = "#38bdf8"
            update_focus_styles(full_refresh=True)
            publish_status()

        def apply_profile(prof):
            mode = prof.get("mode", "xtream")
            name = prof.get("name", "Zdalna Lista")
            state["active_profile_name"] = name
            cache_set("active_profile_name", name)
            cache_set("last_session", {})
            state["streams_cache"].clear()

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

            refresh_login_button_labels()
            now_playing_txt.value = f"⏳ Przełączanie na listę: {name}..."
            safe_ui_update()
            do_login(profile_name_hint=name)

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
                    header_dev_lbl.value = f"📺 {nn} [{cur_id}]  •  📋 {state['active_profile_name']}"
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

        login_view.content = ft.Column(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                "▶ IPTV V5.5 PRO ANDROID TV",
                                size=20,
                                weight=ft.FontWeight.BOLD,
                                color="#38bdf8",
                            ),
                            status_banner,
                            ft.Text(
                                "Użyj strzałek ⬆️ ⬇️ na pilocie, aby wybrać pole. Naciśnij OK, aby edytować lub zmienić listę.",
                                size=12,
                                color="#94a3b8",
                            ),
                            btn_remote_id,
                            btn_edit_srv,
                            btn_edit_usr,
                            btn_edit_pas,
                            btn_edit_m3u,
                            btn_submit_login,
                            btn_saved_profiles_login,
                            btn_toggle_autologin,
                            btn_exit_app,
                        ],
                        spacing=5,
                    ),
                    bgcolor="#111625",
                    padding=16,
                    border_radius=12,
                    width=580,
                    border=ft.border.all(2, "#1e293b"),
                )
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )

        left_panel.content = ft.Column(
            [
                ft.Row(tab_buttons, spacing=4),
                ft.Container(
                    content=list_header_lbl,
                    padding=ft.padding.symmetric(horizontal=6, vertical=4),
                    bgcolor="#0f172a",
                    border_radius=6,
                ),
                list_view,
            ],
            expand=True,
            spacing=8,
        )

        right_panel.content = ft.Column(
            [
                ft.Row(
                    [
                        header_dev_lbl,
                        ft.Row([btn_change_list, btn_cloud_id, btn_logout, btn_quit_dash], spacing=6),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                video_placeholder,
                now_playing_txt,
                ft.Row(
                    [
                        btn_vlc,
                        btn_only_pl,
                        btn_refresh_cat,
                        btn_search,
                        btn_fav_toggle,
                        btn_tmdb,
                        btn_auto_vlc,
                        btn_scheme_vlc,
                    ],
                    spacing=6,
                    wrap=True,
                ),
            ],
            expand=True,
        )

        dashboard_view.content = ft.Row(
            [left_panel, right_panel], expand=True, spacing=10
        )
        page.add(login_view, dashboard_view)
        update_focus_styles(full_refresh=True)
        publish_status()

        if state["auto_login"] and has_saved_credentials():
            def _auto_boot_login():
                if stop_event.wait(0.35):
                    return
                if login_view.visible and not state["client"]:
                    do_login(is_auto=True)

            threading.Thread(target=_auto_boot_login, daemon=True).start()

    except Exception as e:
        err_msg = traceback.format_exc()
        page.add(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            "⚠️ Błąd krytyczny podczas startu:",
                            color="red",
                            size=20,
                            weight=ft.FontWeight.BOLD,
                        ),
                        ft.Text(str(e), color="yellow", size=16),
                        ft.Text(err_msg, color="white", size=11),
                    ],
                    scroll=ft.ScrollMode.ALWAYS,
                ),
                padding=20,
                bgcolor="#1e293b",
                expand=True,
            )
        )
        page.update()


ft.app(target=main)
