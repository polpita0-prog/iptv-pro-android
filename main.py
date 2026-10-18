# -*- coding: utf-8 -*-
import base64
import datetime
import json
import os
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
        last_err = None
        for _ in range(2):
            try:
                data = self._api({}, timeout=12)
                self.user_info = data.get("user_info", {})
                if (self.user_info or {}).get("auth") != 1:
                    raise ValueError("Nieprawidłowy login lub hasło.")
                return self.user_info
            except Exception as e:
                last_err = e
                time.sleep(0.3)
        raise last_err

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
                {"action": "get_short_epg", "stream_id": stream_id, "limit": 3},
                timeout=4,
            )
            listings = data.get("epg_listings", []) if isinstance(data, dict) else []
            parsed = []
            for el in listings[:3]:
                title = decode_b64_safe(el.get("title", ""))
                desc = decode_b64_safe(el.get("description", ""))
                start_t = (el.get("start") or "")[11:16]
                end_t = (el.get("end") or "")[11:16]
                if title:
                    line_str = f"🕒 {start_t} - {end_t}  •  {title}"
                    if desc:
                        line_str += f"\n{desc}"
                    parsed.append(line_str.strip())
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


def make_iptv_bp_badge(size_px=42):
    return ft.Container(
        content=ft.Row(
            [
                ft.Text("▣", size=max(12, int(size_px * 0.40)), color="#a1a1aa"),
                ft.Column(
                    [
                        ft.Text(
                            "IPTV",
                            size=max(9, int(size_px * 0.25)),
                            weight=ft.FontWeight.BOLD,
                            color="#f4f4f5",
                            height=max(11, int(size_px * 0.29)),
                        ),
                        ft.Text(
                            "BP",
                            size=max(11, int(size_px * 0.32)),
                            weight=ft.FontWeight.BOLD,
                            color="#a1a1aa",
                            height=max(13, int(size_px * 0.36)),
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.START,
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=5,
        ),
        width=size_px * 1.6,
        height=size_px,
        bgcolor="#18181b",
        border_radius=10,
        border=ft.border.all(1.5, "#52525b"),
        alignment=ft.alignment.center,
    )


def main(page: ft.Page):
    try:
        page.title = "IPTV BP"
        page.theme_mode = ft.ThemeMode.DARK
        page.bgcolor = "#101216"
        page.padding = 0

        stop_event = threading.Event()
        app_state = {
            "dialog_open": False,
            "settings_open": False,
            "search_open": False,
            "in_fullscreen_player": False,
            "osd_list_open": False,
            "paused_in_bg": False,
            "preview_seq": 0,
            "osd_hide_seq": 0,
            "last_back_press_ts": 0.0,
            "last_ok_ts": 0.0,
            "video_ctrl": None,
        }
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
                "auto_vlc": False,
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

        def force_exit_app(_e=None):
            stop_event.set()
            try:
                page.window.prevent_close = False
            except Exception:
                pass
            try:
                page.window_prevent_close = False
            except Exception:
                pass
            try:
                page.window.close()
            except Exception:
                pass
            try:
                page.window_close()
            except Exception:
                pass
            try:
                page.window_destroy()
            except Exception:
                pass

            def _kill():
                time.sleep(0.15)
                os._exit(0)

            threading.Thread(target=_kill, daemon=True).start()

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
        auto_vlc_saved = cache_get("auto_vlc", False)
        auto_login_saved = cache_get("auto_login", True)
        only_pl_saved = cache_get("only_pl", False)
        saved_vlc_mode = cache_get("vlc_mode", "vlc_clean")
        favorites_items = list(cache_get("favorites_v2", []))

        VLC_MODE_ORDER = ["vlc_clean", "intent", "vlc_full", "http"]
        VLC_MODE_LABELS = {
            "vlc_clean": "🔧 Tryb VLC: vlc:// (Czysty)",
            "intent": "🔧 Tryb VLC: Android Intent",
            "vlc_full": "🔧 Tryb VLC: vlc://http://",
            "http": "🔧 Tryb VLC: Standard HTTP",
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
            "all_built_items": [],
            "osd_channels": [],
            "osd_idx": 0,
            "osd_cat_idx": 0,
            "search_query": "",
            "only_pl": bool(only_pl_saved),
            "now_playing": "Brak aktywnego strumienia",
            "playing_url": "",
            "current_item": None,
            "is_playing": False,
            "paused": False,
            "volume": 100,
            "muted": False,
            "external_vlc_only": bool(auto_vlc_saved),
            "auto_login": bool(auto_login_saved),
            "is_logging_in": False,
            "vlc_mode": saved_vlc_mode,
            "zone": "dashboard",
            "dash_idx": 0,
            "dash_top_idx": 0,
            "list_idx": 0,
            "right_idx": 0,
            "settings_idx": 0,
            "search_idx": 0,
            "login_idx": 5 if has_saved_credentials() else 1,
        }

        VISIBLE_ROWS = 8
        OSD_VISIBLE_ROWS = 9

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

        def make_tv_button(text_str, on_click_fn, key_str=None, height=42, expand=False, item_ref=None):
            txt_ctrl = ft.Text(
                text_str,
                size=14,
                weight=ft.FontWeight.BOLD,
                color="#e4e4e7",
                no_wrap=True,
                expand=True,
            )
            box = ft.Container(
                key=key_str,
                content=ft.Row([txt_ctrl], alignment=ft.MainAxisAlignment.START),
                height=height,
                padding=ft.padding.symmetric(horizontal=12, vertical=4),
                border_radius=8,
                bgcolor="#1c1f26",
                border=ft.border.all(1, "#2e323d"),
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

        clock_txt = ft.Text("00:00", size=28, weight=ft.FontWeight.BOLD, color="#f4f4f5")
        date_txt = ft.Text("Sunday, 18 Oct 2026", size=12, color="#a1a1aa")
        footer_playlist_txt = ft.Text("Current Playlist: Brak", size=12, color="#a1a1aa")
        footer_expire_txt = ft.Text(f"Expire: Unlimited  •  ID: {device_id}", size=12, color="#a1a1aa")
        dash_back_hint_txt = ft.Text(
            "Pilot: Strzałki = wybór | OK = zatwierdź | 2x WSTECZ lub 🚪 ZAMKNIJ = wyjdź",
            size=12,
            color="#d4d4d8",
            weight=ft.FontWeight.BOLD,
        )

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

        poster_img = ft.Image(
            src="",
            width=180,
            height=255,
            fit=ft.ImageFit.COVER,
            border_radius=10,
            visible=False,
        )
        info_badge_txt = ft.Text(
            "PODGLĄD POZYCJI",
            size=11,
            weight=ft.FontWeight.BOLD,
            color="#a1a1aa",
        )
        status_info_title = ft.Text(
            "Najedź kursorem na kanał, film lub serial",
            size=19,
            weight=ft.FontWeight.BOLD,
            color="#f4f4f5",
        )
        status_info_meta = ft.Text(
            "",
            size=13,
            weight=ft.FontWeight.BOLD,
            color="#a1a1aa",
        )
        status_info_sub = ft.Text(
            "Najedź na pozycję po lewej stronie, aby wyświetlić opis i program TV (EPG).\nPodczas oglądania kanału naciśnij przycisk OK na pilocie, aby otworzyć półprzezroczystą listę kanałów bez wychodzenia z programu.",
            size=14,
            color="#d4d4d8",
            max_lines=9,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        info_box_content = ft.Row(
            [
                poster_img,
                ft.Column(
                    [
                        ft.Container(
                            content=info_badge_txt,
                            bgcolor="#27272a",
                            padding=ft.padding.symmetric(horizontal=8, vertical=3),
                            border_radius=6,
                        ),
                        status_info_title,
                        status_info_meta,
                        ft.Divider(height=8, color="#2e323d"),
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

        settings_panel_col = ft.Column([], spacing=5, expand=True)
        settings_overlay_box = ft.Container(
            content=settings_panel_col,
            expand=True,
            bgcolor="#14161b",
            border_radius=12,
            border=ft.border.all(2, "#52525b"),
            padding=14,
            visible=False,
        )

        search_panel_col = ft.Column([], spacing=6, expand=True)
        search_overlay_box = ft.Container(
            content=search_panel_col,
            expand=True,
            bgcolor="#14161b",
            border_radius=12,
            border=ft.border.all(2, "#52525b"),
            padding=14,
            visible=False,
        )

        video_placeholder = ft.Container(
            content=info_box_content,
            expand=True,
            bgcolor="#14161b",
            border_radius=12,
            border=ft.border.all(1, "#2e323d"),
            padding=16,
            visible=True,
        )

        now_playing_txt = ft.Text(
            "▶ Pilot: OK = Oglądaj (podczas oglądania OK = półprzezroczysta lista kanałów)",
            weight=ft.FontWeight.BOLD,
            size=13,
            color="#a1a1aa",
        )
        header_dev_lbl = ft.Text(f"📺 {device_name} [{device_id}]", size=12, color="#a1a1aa", weight=ft.FontWeight.BOLD)
        list_header_lbl = ft.Text("📂 LISTA KATEGORII", size=13, weight=ft.FontWeight.BOLD, color="#e4e4e7")
        list_column = ft.Column(spacing=5, expand=True)
        status_banner = ft.Text(f"🟢 Zdalny Cloud aktywny — Kod ID: {device_id}", color="#a1a1aa", size=13)

        # =====================================================================
        # PEŁNOEKRANOWY ODTWARZACZ Z PÓŁPRZEZROCZYSTĄ LISTĄ KANAŁÓW I KATEGORII
        # =====================================================================
        video_stage_container = ft.Container(
            expand=True,
            bgcolor="#000000",
            alignment=ft.alignment.center,
        )

        mini_osd_title = ft.Text("", size=19, weight=ft.FontWeight.BOLD, color="#ffffff")
        mini_osd_epg = ft.Text(
            "OK = Półprzezroczysta lista kanałów  |  ⬆️/⬇️ lub CH+/CH- = Szybka zmiana kanału  |  WSTECZ = Wyjdź",
            size=13,
            color="#d4d4d8",
        )
        mini_osd_bar = ft.Container(
            content=ft.Column([mini_osd_title, mini_osd_epg], spacing=4, tight=True),
            bgcolor="#BF111318",
            padding=ft.padding.symmetric(horizontal=22, vertical=14),
            border_radius=12,
            border=ft.border.all(1, "#52525b"),
            bottom=24,
            left=24,
            right=24,
            visible=True,
        )

        osd_cat_header_txt = ft.Text(
            "📁 Kategoria: Wszystkie",
            size=14,
            weight=ft.FontWeight.BOLD,
            color="#f4f4f5",
            no_wrap=True,
        )
        osd_list_column = ft.Column(spacing=5, expand=True)
        osd_right_title = ft.Text("", size=20, weight=ft.FontWeight.BOLD, color="#ffffff")
        osd_right_epg = ft.Text("", size=14, color="#e4e4e7", max_lines=6, overflow=ft.TextOverflow.ELLIPSIS)

        channel_osd_overlay = ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        width=480,
                        bgcolor="#CC12141A",
                        padding=14,
                        border_radius=12,
                        border=ft.border.all(1.5, "#71717a"),
                        content=ft.Column(
                            [
                                ft.Container(
                                    content=ft.Row(
                                        [
                                            ft.Text("⬅️", size=14, color="#a1a1aa"),
                                            ft.Container(content=osd_cat_header_txt, expand=True, alignment=ft.alignment.center),
                                            ft.Text("➡️", size=14, color="#a1a1aa"),
                                        ],
                                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                    ),
                                    bgcolor="#9927272A",
                                    padding=ft.padding.symmetric(horizontal=10, vertical=6),
                                    border_radius=8,
                                    border=ft.border.all(1, "#52525b"),
                                ),
                                ft.Text(
                                    "⬆️ ⬇️ Wybór kanału  |  ⬅️ ➡️ Zmiana kategorii  |  OK = Przełącz",
                                    size=11,
                                    weight=ft.FontWeight.BOLD,
                                    color="#a1a1aa",
                                ),
                                ft.Divider(height=4, color="#3f3f46"),
                                osd_list_column,
                            ],
                            expand=True,
                            spacing=5,
                        ),
                    ),
                    ft.Container(
                        expand=True,
                        padding=16,
                        alignment=ft.alignment.bottom_left,
                        content=ft.Container(
                            bgcolor="#BF12141A",
                            padding=16,
                            border_radius=12,
                            border=ft.border.all(1, "#52525b"),
                            content=ft.Column(
                                [
                                    osd_right_title,
                                    osd_right_epg,
                                    ft.Divider(height=8, color="#3f3f46"),
                                    ft.Text(
                                        "OK = Przełącz kanał bez wychodzenia   |   WSTECZ = Ukryj listę",
                                        size=12,
                                        color="#a1a1aa",
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                ],
                                tight=True,
                                spacing=6,
                            ),
                        ),
                    ),
                ],
                expand=True,
                spacing=16,
            ),
            expand=True,
            bgcolor="#44000000",
            padding=20,
            visible=False,
        )

        fullscreen_player_view = ft.Container(
            content=ft.Stack(
                [
                    video_stage_container,
                    mini_osd_bar,
                    channel_osd_overlay,
                ],
                expand=True,
            ),
            expand=True,
            visible=False,
            bgcolor="#000000",
        )

        def schedule_mini_osd_autohide():
            app_state["osd_hide_seq"] += 1
            cur_s = app_state["osd_hide_seq"]

            def _hider():
                time.sleep(3.8)
                if cur_s != app_state["osd_hide_seq"] or stop_event.is_set():
                    return
                if app_state["in_fullscreen_player"] and not app_state["osd_list_open"]:
                    mini_osd_bar.visible = False
                    safe_ui_update()

            threading.Thread(target=_hider, daemon=True).start()

        def update_mini_osd_epg_async(item):
            if not isinstance(item, dict):
                return
            k = item.get("kind") or state["kind"]
            if k == "live" and state.get("client"):
                def _w():
                    epg_list = state["client"].get_short_epg(item.get("stream_id"))
                    if epg_list:
                        first_line = epg_list[0].splitlines()[0]
                        mini_osd_epg.value = f"{first_line}   |   OK = Półprzezroczysta lista kanałów"
                        safe_ui_update()

                threading.Thread(target=_w, daemon=True).start()

        def update_osd_channel_epg_async(item):
            if not isinstance(item, dict):
                return
            nm = item.get("name") or item.get("title") or "Kanał"
            osd_right_title.value = f"📺 {nm}"
            osd_right_epg.value = "⏳ Pobieranie programu TV (EPG)..."
            safe_ui_update()

            k = item.get("kind") or state["kind"]
            if k == "live" and state.get("client"):
                def _epg_w():
                    epg_list = state["client"].get_short_epg(item.get("stream_id"))
                    if epg_list:
                        osd_right_epg.value = "\n\n".join(epg_list[:2])
                    else:
                        osd_right_epg.value = "Brak danych EPG dla tego kanału. Naciśnij OK, aby przełączyć."
                    safe_ui_update()

                threading.Thread(target=_epg_w, daemon=True).start()
            else:
                osd_right_epg.value = "Naciśnij OK, aby uruchomić tę pozycję."
                safe_ui_update()

        def render_osd_channel_list():
            act_cat_nm = (state.get("active_cat") or {}).get("category_name") or ("Ulubione" if state["in_fav"] else "Kanały TV")
            osd_cat_header_txt.value = f"📁 {act_cat_nm}"

            channels = state.get("osd_channels", [])
            total = len(channels)
            osd_list_column.controls.clear()
            if total == 0:
                osd_list_column.controls.append(ft.Text("Brak kanałów w tej kategorii.", color="#a1a1aa"))
                safe_ui_update()
                return

            state["osd_idx"] = max(0, min(state["osd_idx"], total - 1))
            cur_idx = state["osd_idx"]
            playing_uid = get_item_unique_id(state.get("current_item"))

            half = OSD_VISIBLE_ROWS // 2
            start_v = max(0, min(cur_idx - half, max(0, total - OSD_VISIBLE_ROWS)))
            end_v = min(total, start_v + OSD_VISIBLE_ROWS)

            for idx in range(start_v, end_v):
                ch = channels[idx]
                is_foc = (idx == cur_idx)
                is_now = (get_item_unique_id(ch) == playing_uid)
                nm = ch.get("name") or ch.get("title") or "Kanał"
                prefix = "▶ " if is_foc else ("🟢 " if is_now else "   ")
                lbl = f"{idx + 1}. {prefix}{nm}"

                row_box = ft.Container(
                    content=ft.Text(
                        lbl,
                        size=15 if is_foc else 14,
                        weight=ft.FontWeight.BOLD,
                        color="#ffffff" if is_foc else ("#a1a1aa" if not is_now else "#f4f4f5"),
                        no_wrap=True,
                    ),
                    height=42,
                    padding=ft.padding.symmetric(horizontal=12, vertical=4),
                    border_radius=8,
                    bgcolor="#D93F3F46" if is_foc else ("#9927272A" if is_now else "#8018181B"),
                    border=ft.border.all(2 if is_foc else 1, "#f4f4f5" if is_foc else "#3f3f46"),
                    alignment=ft.alignment.center_left,
                    on_click=lambda _e, it=ch: select_channel_from_osd(it),
                )
                osd_list_column.controls.append(row_box)

            update_osd_channel_epg_async(channels[cur_idx])
            safe_ui_update()

        def switch_osd_category_relative(step: int):
            if not state.get("client"):
                return
            cur_kind = state["kind"]
            cats = state["categories"].get(cur_kind) or []
            if not cats:
                return
            if state["only_pl"]:
                filtered_c = [
                    c for c in cats
                    if str(c.get("category_id")) in ("__ALL__", "__RECENT__", "__CATCHUP__")
                    or is_polish_text(c.get("category_name", ""))
                ]
                if len(filtered_c) > 2:
                    cats = filtered_c

            cur_cat_id = str((state.get("active_cat") or {}).get("category_id", ""))
            c_idx = 0
            for idx, c in enumerate(cats):
                if str(c.get("category_id", "")) == cur_cat_id:
                    c_idx = idx
                    break

            new_cat = cats[(c_idx + step) % len(cats)]
            state["active_cat"] = new_cat
            cat_id = str(new_cat.get("category_id", "__ALL__"))
            cache_key = (cur_kind, cat_id)

            if cache_key in state["streams_cache"]:
                items = state["streams_cache"][cache_key]
                state["streams"] = items
                state["filtered"] = items
                state["osd_channels"] = items
                state["osd_idx"] = 0
                render_osd_channel_list()
                return

            osd_cat_header_txt.value = f"⏳ Ładowanie: {new_cat.get('category_name', '')}..."
            safe_ui_update()

            def _load_c():
                try:
                    items = state["client"].get_streams(cur_kind, new_cat.get("category_id")) or []
                    for it in items:
                        it["kind"] = cur_kind
                    state["streams"] = items
                    state["filtered"] = items
                    state["streams_cache"][cache_key] = items
                    state["osd_channels"] = items
                    state["osd_idx"] = 0
                    render_osd_channel_list()
                except Exception:
                    pass

            threading.Thread(target=_load_c, daemon=True).start()

        def open_osd_channel_overlay():
            channels = state.get("filtered") or state.get("streams") or favorites_items or []
            state["osd_channels"] = channels
            cur_uid = get_item_unique_id(state.get("current_item"))
            found_i = 0
            for idx, it in enumerate(channels):
                if get_item_unique_id(it) == cur_uid:
                    found_i = idx
                    break
            state["osd_idx"] = found_i
            app_state["osd_list_open"] = True
            mini_osd_bar.visible = False
            channel_osd_overlay.visible = True
            render_osd_channel_list()

        def close_osd_channel_overlay():
            app_state["osd_list_open"] = False
            channel_osd_overlay.visible = False
            mini_osd_bar.visible = True
            safe_ui_update()
            schedule_mini_osd_autohide()

        def select_channel_from_osd(item):
            close_osd_channel_overlay()
            on_stream_click(item)

        def exit_fullscreen_player():
            v_ctrl = app_state.get("video_ctrl")
            if v_ctrl is not None:
                try:
                    v_ctrl.pause()
                except Exception:
                    pass
            app_state["video_ctrl"] = None
            app_state["in_fullscreen_player"] = False
            app_state["osd_list_open"] = False
            channel_osd_overlay.visible = False
            fullscreen_player_view.visible = False
            video_stage_container.content = None
            dash_inner_view.visible = True
            state["zone"] = "list"
            build_full_list_data(keep_idx=True)
            publish_status()

        def is_favorite(item):
            uid = get_item_unique_id(item)
            if not uid:
                return False
            return any(get_item_unique_id(f) == uid for f in favorites_items)

        def trigger_focus_preview(item):
            if not isinstance(item, dict):
                return

            if "category_name" in item and "stream_id" not in item and "series_id" not in item:
                cat_nm = item.get("category_name", "Kategoria")
                poster_img.visible = False
                info_badge_txt.value = "📁 KATEGORIA"
                status_info_title.value = cat_nm
                status_info_meta.value = f"Sekcja: {state['kind'].upper()}  •  Naciśnij OK, aby otworzyć"
                status_info_sub.value = (
                    "Przewijaj listę strzałkami ⬆️ ⬇️ na całej wysokości okna.\n"
                    "Po włączeniu kanału naciśnij przycisk OK na pilocie, aby wyświetlić półprzezroczystą listę kanałów na ekranie wideo."
                )
                return

            state["current_item"] = item
            title = item.get("display_title") or item.get("name") or item.get("title") or "Pozycja"
            icon_url = item.get("stream_icon") or item.get("cover") or ""
            k = item.get("kind") or state["kind"]
            fav_str = "⭐ W ulubionych  •  " if is_favorite(item) else ""
            pl_str = "🇵🇱 PL  •  " if is_polish_item(item, get_current_pl_cat_ids()) else ""

            info_badge_txt.value = (
                "📡 KANAŁ TV NA ŻYWO"
                if k == "live"
                else ("🎬 FILM VOD" if k == "vod" else "🎞 SERIAL")
            )
            status_info_title.value = title
            status_info_meta.value = f"{fav_str}{pl_str}Naciśnij OK, aby odtworzyć"

            if icon_url and icon_url.startswith("http"):
                poster_img.src = icon_url
                poster_img.visible = True
            else:
                poster_img.visible = False

            status_info_sub.value = "⏳ Pobieranie informacji o programie / opisu..."

            app_state["preview_seq"] += 1
            cur_seq = app_state["preview_seq"]

            def _async_preview():
                time.sleep(0.18)
                if cur_seq != app_state["preview_seq"] or stop_event.is_set():
                    return
                if k == "live" and state.get("client"):
                    epg_list = state["client"].get_short_epg(item.get("stream_id"))
                    if cur_seq != app_state["preview_seq"]:
                        return
                    arch_tag = "⏱ Archiwum Catch Up" if parse_int_safe(item.get("tv_archive"), 0) == 1 else "📡 Live TV"
                    status_info_meta.value = f"{fav_str}{pl_str}{arch_tag}"
                    if epg_list:
                        status_info_sub.value = "\n\n".join(epg_list)
                    else:
                        status_info_sub.value = "Brak aktualnych danych EPG dla tego kanału.\nNaciśnij OK, aby uruchomić transmisję."
                    safe_ui_update()
                elif k in ("vod", "series"):
                    tmdb = fetch_tmdb_details(title)
                    if cur_seq != app_state["preview_seq"]:
                        return
                    if tmdb:
                        if tmdb.get("poster"):
                            poster_img.src = tmdb["poster"]
                            poster_img.visible = True
                        yr = f"📅 Rok: {tmdb['year']}   " if tmdb.get("year") else ""
                        rt = f"⭐ Ocena TMDB: {tmdb['rating']}/10" if tmdb.get("rating") else ""
                        status_info_meta.value = f"{fav_str}{pl_str}{yr}{rt}".strip()
                        status_info_sub.value = tmdb.get("plot") or "Brak polskiego opisu fabuły w bazie TMDB."
                    else:
                        status_info_sub.value = "Brak dodatkowego opisu w bazie TMDB.\nNaciśnij OK na pilocie, aby rozpocząć oglądanie."
                    safe_ui_update()

            threading.Thread(target=_async_preview, daemon=True).start()

        def toggle_only_pl(_e=None):
            state["only_pl"] = not state["only_pl"]
            cache_set("only_pl", state["only_pl"])
            state["categories_cache"] = {"live": None, "vod": None, "series": None}
            state["list_idx"] = 0
            now_playing_txt.value = (
                "🇵🇱 Filtr 'Tylko Polskie' WŁĄCZONY"
                if state["only_pl"]
                else "🌍 Filtr 'Tylko Polskie' WYŁĄCZONY (Polskie na górze)"
            )
            if not state["in_dashboard"]:
                build_full_list_data()
            else:
                safe_ui_update()
            publish_status()

        def cycle_vlc_mode(_e=None):
            cur = state.get("vlc_mode", "vlc_clean")
            idx = (VLC_MODE_ORDER.index(cur) + 1) % len(VLC_MODE_ORDER) if cur in VLC_MODE_ORDER else 0
            new_mode = VLC_MODE_ORDER[idx]
            state["vlc_mode"] = new_mode
            cache_set("vlc_mode", new_mode)
            now_playing_txt.value = f"🔧 {VLC_MODE_LABELS[new_mode]}"
            safe_ui_update()

        def toggle_player_engine(_e=None):
            state["external_vlc_only"] = not state["external_vlc_only"]
            cache_set("auto_vlc", state["external_vlc_only"])
            now_playing_txt.value = (
                "🚀 Wybrano: Zewnętrzny odtwarzacz VLC"
                if state["external_vlc_only"]
                else "📺 Wybrano: Wbudowany odtwarzacz z półprzezroczystą listą kanałów (OK)"
            )
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
                item_copy = dict(cur_item)
                item_copy["kind"] = cur_item.get("kind") or state["kind"]
                favorites_items.insert(0, item_copy)
                now_playing_txt.value = f"⭐ Dodano do ulubionych: {nm}"
            cache_set("favorites_v2", favorites_items)
            build_full_list_data(keep_idx=True)

        def stop_playback(_e=None):
            state["is_playing"] = False
            state["paused"] = False
            state["now_playing"] = "Brak aktywnego strumienia"
            if app_state["in_fullscreen_player"]:
                exit_fullscreen_player()
            now_playing_txt.value = "⏹ Zatrzymano odtwarzanie"
            safe_ui_update()
            publish_status()

        def toggle_play_pause(_e=None):
            state["paused"] = not state["paused"]
            v_ctrl = app_state.get("video_ctrl")
            if v_ctrl is not None:
                try:
                    v_ctrl.play_or_pause()
                except Exception:
                    pass
            now_playing_txt.value = "⏸ PAUZA" if state["paused"] else f"▶ {state['now_playing']}"
            safe_ui_update()
            publish_status()

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

            now_playing_txt.value = f"🚀 Uruchamianie VLC: {title or state['now_playing']}"
            safe_ui_update()
            threading.Thread(target=lambda: page.launch_url(target_url), daemon=True).start()

        # Płynne przełączanie kanałów w aktywnym odtwarzaczu bez wychodzenia do menu
        def play_stream_url(url, title, item=None):
            state["playing_url"] = url
            state["now_playing"] = title
            state["current_item"] = item
            state["is_playing"] = True
            state["paused"] = False
            now_playing_txt.value = f"▶ Odtwarzanie: {title}"

            if state["external_vlc_only"]:
                launch_vlc_intent(url, title)
                publish_status()
                return

            existing_vid = app_state.get("video_ctrl")
            if app_state["in_fullscreen_player"] and existing_vid is not None:
                # Podmiana strumienia w locie bez zamykania pełnego ekranu
                switched_in_place = False
                try:
                    existing_vid.playlist_add(ft.VideoMedia(url))
                    existing_vid.playlist_remove(0)
                    existing_vid.jump_to(0)
                    existing_vid.play()
                    switched_in_place = True
                except Exception:
                    switched_in_place = False

                if not switched_in_place:
                    try:
                        new_vid = ft.Video(
                            playlist=[ft.VideoMedia(url)],
                            autoplay=True,
                            show_controls=False,
                            expand=True,
                            fill_color="#000000",
                        )
                        app_state["video_ctrl"] = new_vid
                        video_stage_container.content = new_vid
                    except Exception:
                        pass
            else:
                try:
                    vid_ctrl = ft.Video(
                        playlist=[ft.VideoMedia(url)],
                        autoplay=True,
                        show_controls=False,
                        expand=True,
                        fill_color="#000000",
                    )
                    app_state["video_ctrl"] = vid_ctrl
                    video_stage_container.content = vid_ctrl
                except Exception:
                    app_state["video_ctrl"] = None
                    video_stage_container.content = ft.Column(
                        [
                            ft.Text(f"▶ {title}", size=24, weight=ft.FontWeight.BOLD, color="white"),
                            ft.Text(
                                "Naciśnij OK = Półprzezroczysta lista kanałów  |  ⬆️/⬇️ = Przełącz kanał",
                                size=15,
                                color="#a1a1aa",
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    )

            mini_osd_title.value = f"▶  {title}"
            mini_osd_epg.value = "OK = Półprzezroczysta lista kanałów  |  ⬆️/⬇️ lub CH+/CH- = Szybka zmiana kanału  |  WSTECZ = Wyjdź"
            mini_osd_bar.visible = True
            channel_osd_overlay.visible = False
            app_state["osd_list_open"] = False
            app_state["in_fullscreen_player"] = True

            dash_view.visible = False
            dash_inner_view.visible = False
            fullscreen_player_view.visible = True
            safe_ui_update()
            update_mini_osd_epg_async(item)
            schedule_mini_osd_autohide()
            publish_status()

        def switch_live_channel_relative(step: int):
            streams_list = state.get("osd_channels") or state.get("filtered") or state.get("streams") or favorites_items or []
            if not streams_list or not state.get("client"):
                return
            cur_item = state.get("current_item")
            cur_uid = get_item_unique_id(cur_item) if cur_item else ""
            cur_pos = 0
            for idx, it in enumerate(streams_list):
                if get_item_unique_id(it) == cur_uid:
                    cur_pos = idx
                    break
            new_pos = (cur_pos + step) % len(streams_list)
            next_item = streams_list[new_pos]
            state["osd_idx"] = new_pos

            for b_idx, b_entry in enumerate(state.get("all_built_items", [])):
                if b_entry.get("item") is next_item:
                    state["list_idx"] = b_idx
                    break

            on_stream_click(next_item)

        search_menu_buttons = []
        search_tf_input = ft.TextField(
            label="Wpisz szukaną nazwę (np. TVN, HBO, Batman)...",
            value="",
            bgcolor="#1c1f26",
            border_color="#71717a",
            color="white",
            text_size=15,
        )

        def update_left_search_bar_label():
            q = state.get("search_query", "").strip()
            if q:
                set_tv_button_text(btn_left_top_search, f"🔍 FILTR AKTYWNY: '{q}' (Kliknij OK, aby zmienić)")
            else:
                set_tv_button_text(btn_left_top_search, "🔍 SZUKAJ KANAŁU, FILMU LUB SERIALU...")

        def close_search_panel():
            app_state["search_open"] = False
            search_overlay_box.visible = False
            video_placeholder.visible = True
            state["zone"] = "list"
            update_focus_styles()

        def execute_global_search(target_kind=None):
            q_str = (search_tf_input.value or "").strip()
            state["search_query"] = q_str
            update_left_search_bar_label()
            app_state["search_open"] = False
            search_overlay_box.visible = False
            video_placeholder.visible = True

            if target_kind:
                state["kind"] = target_kind
                state["in_fav"] = False
                state["in_profiles"] = False
                state["zone"] = "list"
                open_category({
                    "category_id": "__ALL__",
                    "category_name": f"🔍 WYNIKI SZUKANIA: '{q_str}' ({target_kind.upper()})",
                })
            else:
                state["zone"] = "list"
                if state["view_mode"] == "categories" and q_str:
                    open_category({
                        "category_id": "__ALL__",
                        "category_name": f"🔍 WYNIKI SZUKANIA: '{q_str}' ({state['kind'].upper()})",
                    })
                else:
                    build_full_list_data()

        def open_search_panel(_e=None):
            if state["in_dashboard"]:
                state["in_dashboard"] = False
                dash_view.visible = False
                dash_inner_view.visible = True
                if not state["all_built_items"]:
                    back_to_cats()

            app_state["settings_open"] = False
            settings_overlay_box.visible = False
            app_state["search_open"] = True
            state["search_idx"] = 0
            search_tf_input.value = state.get("search_query", "")
            search_menu_buttons.clear()
            search_panel_col.controls.clear()

            def _focus_keyboard():
                try:
                    search_tf_input.focus()
                    page.update()
                except Exception:
                    pass

            def _clear_and_close():
                search_tf_input.value = ""
                state["search_query"] = ""
                update_left_search_bar_label()
                close_search_panel()
                build_full_list_data()

            search_tf_input.on_submit = lambda _: execute_global_search(None)

            b_kbd = make_tv_button("⌨️ WPISZ TEKST (Otwórz klawiaturę ekranową)", _focus_keyboard, height=38)
            b_cur = make_tv_button("🔎 Filtruj bieżącą listę po wpisanej frazie", lambda: execute_global_search(None), height=38)
            b_live = make_tv_button("📺 Szukaj we wszystkich Kanałach TV na żywo", lambda: execute_global_search("live"), height=38)
            b_vod = make_tv_button("🎬 Szukaj we wszystkich Filmach VOD", lambda: execute_global_search("vod"), height=38)
            b_ser = make_tv_button("🎞 Szukaj we wszystkich Serialach", lambda: execute_global_search("series"), height=38)
            b_clr = make_tv_button("❌ Wyczyść filtr wyszukiwania (Pokaż wszystko)", _clear_and_close, height=38)
            b_cls = make_tv_button("↩ Zamknij wyszukiwarkę", close_search_panel, height=38)

            search_menu_buttons.extend([b_kbd, b_cur, b_live, b_vod, b_ser, b_clr, b_cls])

            search_panel_col.controls.append(
                ft.Text("🔍 WYSZUKIWARKA IPTV BP (użyj strzałek ⬆️ ⬇️ oraz OK)", size=15, weight=ft.FontWeight.BOLD, color="#e4e4e7")
            )
            search_panel_col.controls.append(search_tf_input)
            search_panel_col.controls.extend(search_menu_buttons)

            def style_search_menu():
                for i, b in enumerate(search_menu_buttons):
                    foc = (i == state["search_idx"])
                    base_t = b.data["base_text"]
                    b.bgcolor = "#3f3f46" if foc else "#1c1f26"
                    b.border = ft.border.all(2.5 if foc else 1, "#f4f4f5" if foc else "#2e323d")
                    b.data["text_ctrl"].color = "white"
                    b.data["text_ctrl"].value = f"▶  {base_t}" if foc else base_t
                safe_ui_update()

            app_state["style_search_fn"] = style_search_menu
            video_placeholder.visible = False
            search_overlay_box.visible = True
            style_search_menu()

        def make_top_pill_btn(icon_emoji, label_str, on_click_fn, is_danger=False):
            txt_c = ft.Text(f"{icon_emoji} {label_str}", size=13, weight=ft.FontWeight.BOLD, color="white")
            box = ft.Container(
                content=txt_c,
                padding=ft.padding.symmetric(horizontal=12, vertical=7),
                border_radius=20,
                bgcolor="#451a1a" if is_danger else "#1c1f26",
                border=ft.border.all(1, "#7f1d1d" if is_danger else "#3f3f46"),
                on_click=lambda _: on_click_fn(),
            )
            box.data = {"text_ctrl": txt_c, "action": on_click_fn, "danger": is_danger}
            return box

        def make_vivo_tile(title, icon_char, on_click_fn, is_large=False, is_danger=False):
            t_ctrl = ft.Text(
                title,
                size=16 if is_large else 13,
                weight=ft.FontWeight.BOLD,
                color="#f4f4f5",
                text_align=ft.TextAlign.CENTER,
            )
            i_ctrl = ft.Text(
                icon_char,
                size=34 if is_large else 19,
                color="#d4d4d8",
                text_align=ft.TextAlign.CENTER,
            )
            content_layout = (
                ft.Column(
                    [i_ctrl, t_ctrl],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=6,
                )
                if is_large
                else ft.Row(
                    [i_ctrl, t_ctrl],
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=6,
                )
            )
            box = ft.Container(
                content=content_layout,
                bgcolor="#3b1818" if is_danger else "#181a20",
                border_radius=12,
                border=ft.border.all(1.5 if is_danger else 1, "#7f1d1d" if is_danger else "#2e323d"),
                padding=8,
                expand=True,
                on_click=lambda _: on_click_fn(),
            )
            box.data = {"title": title, "action": on_click_fn, "danger": is_danger}
            return box

        def open_dashboard():
            app_state["settings_open"] = False
            app_state["search_open"] = False
            app_state["in_fullscreen_player"] = False
            app_state["osd_list_open"] = False
            settings_overlay_box.visible = False
            search_overlay_box.visible = False
            fullscreen_player_view.visible = False
            video_placeholder.visible = True
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
            app_state["settings_open"] = False
            app_state["search_open"] = False
            app_state["in_fullscreen_player"] = False
            settings_overlay_box.visible = False
            search_overlay_box.visible = False
            fullscreen_player_view.visible = False
            video_placeholder.visible = True
            state["in_dashboard"] = False
            state["kind"] = kind
            state["in_fav"] = False
            state["in_profiles"] = False
            state["search_query"] = ""
            update_left_search_bar_label()
            state["zone"] = "list"
            dash_view.visible = False
            dash_inner_view.visible = True
            back_to_cats()
            publish_status()

        def open_catchup_section():
            app_state["settings_open"] = False
            app_state["search_open"] = False
            app_state["in_fullscreen_player"] = False
            settings_overlay_box.visible = False
            search_overlay_box.visible = False
            fullscreen_player_view.visible = False
            video_placeholder.visible = True
            state["in_dashboard"] = False
            state["kind"] = "live"
            state["in_fav"] = False
            state["in_profiles"] = False
            state["search_query"] = ""
            update_left_search_bar_label()
            state["zone"] = "list"
            dash_view.visible = False
            dash_inner_view.visible = True
            open_category({
                "category_id": "__CATCHUP__",
                "category_name": "⏱ KANAŁY Z ARCHIWUM (CATCH UP)",
            })
            publish_status()

        def open_favorites_section():
            app_state["settings_open"] = False
            app_state["search_open"] = False
            app_state["in_fullscreen_player"] = False
            settings_overlay_box.visible = False
            search_overlay_box.visible = False
            fullscreen_player_view.visible = False
            video_placeholder.visible = True
            state["in_dashboard"] = False
            state["in_fav"] = True
            state["in_profiles"] = False
            state["view_mode"] = "streams"
            state["active_cat"] = None
            state["list_idx"] = 0
            state["zone"] = "list"
            dash_view.visible = False
            dash_inner_view.visible = True
            build_full_list_data()
            publish_status()

        def open_profiles_section():
            app_state["settings_open"] = False
            app_state["search_open"] = False
            app_state["in_fullscreen_player"] = False
            settings_overlay_box.visible = False
            search_overlay_box.visible = False
            fullscreen_player_view.visible = False
            video_placeholder.visible = True
            state["in_dashboard"] = False
            state["in_fav"] = False
            state["in_profiles"] = True
            state["view_mode"] = "profiles"
            state["active_cat"] = None
            state["list_idx"] = 0
            state["zone"] = "list"
            dash_view.visible = False
            dash_inner_view.visible = True
            build_full_list_data()
            publish_status()

        def open_settings_from_dashboard():
            state["in_dashboard"] = False
            state["kind"] = "live"
            state["in_fav"] = False
            state["in_profiles"] = False
            dash_view.visible = False
            dash_inner_view.visible = True
            if not state["all_built_items"]:
                back_to_cats()
            open_settings_menu_dlg()

        dash_tile_live = make_vivo_tile("Live TV", "📺", lambda: open_section("live"), is_large=True)
        dash_tile_movies = make_vivo_tile("Movies", "🎬", lambda: open_section("vod"), is_large=True)
        dash_tile_series = make_vivo_tile("Series", "🎞", lambda: open_section("series"), is_large=True)
        dash_tile_search = make_vivo_tile("Szukaj", "🔍", open_search_panel, is_large=False)
        dash_tile_catchup = make_vivo_tile("Catch Up", "⏱", open_catchup_section, is_large=False)
        dash_tile_fav = make_vivo_tile("Ulubione", "⭐", open_favorites_section, is_large=False)
        dash_tile_playlists = make_vivo_tile("Listy", "📋", open_profiles_section, is_large=False)
        dash_tile_settings = make_vivo_tile("Ustawienia", "⚙️", open_settings_from_dashboard, is_large=False)
        dash_tile_exit = make_vivo_tile("ZAMKNIJ", "🚪", force_exit_app, is_large=False, is_danger=True)

        dash_tiles = [
            dash_tile_live,       # 0
            dash_tile_movies,     # 1
            dash_tile_series,     # 2
            dash_tile_search,     # 3
            dash_tile_catchup,    # 4
            dash_tile_fav,        # 5
            dash_tile_playlists,  # 6
            dash_tile_settings,   # 7
            dash_tile_exit,       # 8
        ]

        def refresh_current_list(_e=None):
            if not state["client"]:
                return
            state["streams_cache"].clear()
            state["categories_cache"] = {"live": None, "vod": None, "series": None}
            state["categories"] = {"live": [], "vod": [], "series": []}
            state["search_query"] = ""
            update_left_search_bar_label()
            now_playing_txt.value = "🔄 Odświeżanie listy i bufora RAM z serwera..."
            list_header_lbl.value = "⏳ Pobieranie najnowszych pozycji..."
            list_column.controls.clear()
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
                        state["list_idx"] = 0
                        build_full_list_data()
                    else:
                        open_dashboard()
                except Exception as ex:
                    now_playing_txt.value = f"❌ Błąd: {ex}"
                    safe_ui_update()

            threading.Thread(target=_reload_w, daemon=True).start()

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
                    ft.ElevatedButton("💾 Zapisz", on_click=_save, bgcolor="#3f3f46", color="white"),
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
            if state["in_profiles"]:
                build_full_list_data()
            else:
                safe_ui_update()
            publish_status()

        def do_login(_e=None, is_auto=False, profile_name_hint=""):
            if state["is_logging_in"]:
                return
            state["is_logging_in"] = True
            status_banner.value = "⏳ Łączenie z serwerem IPTV BP..."
            status_banner.color = "#d4d4d8"
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
                    status_banner.value = f"❌ Błąd logowania: {ex} (naciśnij OK, aby ponowić)"
                    status_banner.color = "#ef4444"
                    state["login_idx"] = 5
                    update_focus_styles()
                finally:
                    state["is_logging_in"] = False

            threading.Thread(target=_w, daemon=True).start()

        def do_logout(_e=None):
            stop_playback()
            app_state["settings_open"] = False
            app_state["search_open"] = False
            app_state["in_fullscreen_player"] = False
            settings_overlay_box.visible = False
            search_overlay_box.visible = False
            fullscreen_player_view.visible = False
            video_placeholder.visible = True
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
                            bgcolor="#3f3f46" if is_act else "#1c1f26",
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
                actions=[ft.ElevatedButton("Zapisz", on_click=_save, bgcolor="#3f3f46", color="white")],
                on_dismiss=lambda _: app_state.update({"dialog_open": False}),
            )
            page.overlay.append(dlg)
            dlg.open = True
            safe_ui_update()

        settings_menu_buttons = []

        def close_settings_panel():
            app_state["settings_open"] = False
            settings_overlay_box.visible = False
            video_placeholder.visible = True
            state["zone"] = "list"
            update_focus_styles()

        def open_settings_menu_dlg(_e=None):
            app_state["search_open"] = False
            search_overlay_box.visible = False
            app_state["settings_open"] = True
            state["settings_idx"] = 0
            settings_menu_buttons.clear()
            settings_panel_col.controls.clear()

            def _act_fav():
                close_settings_panel()
                toggle_favorite()

            def _act_only_pl():
                toggle_only_pl()
                _rebuild_labels()

            def _act_vlc_mode():
                cycle_vlc_mode()
                _rebuild_labels()

            def _act_engine():
                toggle_player_engine()
                _rebuild_labels()

            def _act_search():
                close_settings_panel()
                open_search_panel()

            def _act_refresh():
                close_settings_panel()
                refresh_current_list()

            def _act_cloud():
                close_settings_panel()
                open_remote_settings_dlg()

            def _act_logout():
                close_settings_panel()
                do_logout()

            cur_it = state.get("current_item")
            fav_lbl = "⭐ Usuń bieżącą pozycję z Ulubionych" if (cur_it and is_favorite(cur_it)) else "⭐ Dodaj bieżącą pozycję do Ulubionych"
            eng_lbl = "🎬 Odtwarzacz: Zewnętrzny VLC" if state["external_vlc_only"] else "📺 Odtwarzacz: Wbudowany (Półprzezroczysta lista pod OK)"

            b_s_fav = make_tv_button(fav_lbl, _act_fav, height=36)
            b_s_pl = make_tv_button(f"🇵🇱 Filtr 'Tylko Polskie': {'WŁĄCZONY' if state['only_pl'] else 'WYŁĄCZONY'}", _act_only_pl, height=36)
            b_s_eng = make_tv_button(eng_lbl, _act_engine, height=36)
            b_s_vmode = make_tv_button(VLC_MODE_LABELS[state["vlc_mode"]], _act_vlc_mode, height=36)
            b_s_search = make_tv_button("🔍 Otwórz wyszukiwarkę (Lupa)", _act_search, height=36)
            b_s_refresh = make_tv_button("🔄 Odśwież listę i wyczyść bufor RAM", _act_refresh, height=36)
            b_s_cloud = make_tv_button(f"🌐 Zmień Kod ID / Nazwę odbiornika ({device_id})", _act_cloud, height=36)
            b_s_logout = make_tv_button("⇄ Zmień konto / Wyloguj do ekranu startowego", _act_logout, height=36)
            b_s_exit = make_tv_button("🚪 ZAMKNIJ APLIKACJĘ (Wyjdź do Android TV)", force_exit_app, height=36)
            b_s_close = make_tv_button("↩ Zamknij ustawienia (wróć do listy)", close_settings_panel, height=36)

            settings_menu_buttons.extend([
                b_s_fav,
                b_s_pl,
                b_s_eng,
                b_s_vmode,
                b_s_search,
                b_s_refresh,
                b_s_cloud,
                b_s_logout,
                b_s_exit,
                b_s_close,
            ])

            settings_panel_col.controls.append(
                ft.Text("⚙️ MENU USTAWIEŃ (steruj strzałkami ⬆️ ⬇️ i OK, WSTECZ = zamknij)", size=14, weight=ft.FontWeight.BOLD, color="#e4e4e7")
            )
            settings_panel_col.controls.extend(settings_menu_buttons)

            def _rebuild_labels():
                set_tv_button_text(b_s_pl, f"🇵🇱 Filtr 'Tylko Polskie': {'WŁĄCZONY' if state['only_pl'] else 'WYŁĄCZONY'}")
                set_tv_button_text(b_s_vmode, VLC_MODE_LABELS[state["vlc_mode"]])
                set_tv_button_text(
                    b_s_eng,
                    "🎬 Odtwarzacz: Zewnętrzny VLC" if state["external_vlc_only"] else "📺 Odtwarzacz: Wbudowany (Półprzezroczysta lista pod OK)",
                )
                style_settings_menu()

            def style_settings_menu():
                for i, b in enumerate(settings_menu_buttons):
                    foc = (i == state["settings_idx"])
                    base_t = b.data["base_text"]
                    b.bgcolor = "#3f3f46" if foc else ("#451a1a" if b == b_s_exit else "#1c1f26")
                    b.border = ft.border.all(2.5 if foc else 1, "#f4f4f5" if foc else "#2e323d")
                    b.data["text_ctrl"].color = "white"
                    b.data["text_ctrl"].value = f"▶  {base_t}" if foc else base_t
                safe_ui_update()

            app_state["style_settings_fn"] = style_settings_menu
            video_placeholder.visible = False
            settings_overlay_box.visible = True
            style_settings_menu()

        top_btn_lupa = make_top_pill_btn("🔍", "Szukaj", open_search_panel)
        top_btn_user = make_top_pill_btn("👤", "Konto", do_logout)
        top_btn_sett = make_top_pill_btn("⚙️", "Ustawienia", open_settings_from_dashboard)
        top_btn_exit = make_top_pill_btn("🚪", "ZAMKNIJ", force_exit_app, is_danger=True)
        dash_top_buttons = [top_btn_lupa, top_btn_user, top_btn_sett, top_btn_exit]

        def render_dashboard_focus():
            for idx, b_top in enumerate(dash_top_buttons):
                foc_top = (state["zone"] == "dash_top" and state["dash_top_idx"] == idx)
                is_d = b_top.data.get("danger", False)
                b_top.bgcolor = "#3f3f46" if foc_top else ("#451a1a" if is_d else "#1c1f26")
                b_top.border = ft.border.all(2.5 if foc_top else 1, "#f4f4f5" if foc_top else ("#7f1d1d" if is_d else "#3f3f46"))

            for idx, tile in enumerate(dash_tiles):
                is_foc = (state["zone"] == "dashboard" and state["dash_idx"] == idx)
                is_d = tile.data.get("danger", False)
                if is_foc:
                    tile.bgcolor = "#7f1d1d" if is_d else "#2e323d"
                    tile.border = ft.border.all(3, "#f4f4f5")
                else:
                    tile.bgcolor = "#3b1818" if is_d else "#181a20"
                    tile.border = ft.border.all(1.5 if is_d else 1, "#7f1d1d" if is_d else "#2e323d")
            safe_ui_update()

        btn_left_top_search = make_tv_button(
            "🔍 SZUKAJ KANAŁU, FILMU LUB SERIALU...",
            open_search_panel,
            height=42,
        )

        btn_vlc = make_tv_button("🚀 Wymuś VLC", lambda: launch_vlc_intent(state["playing_url"], state["now_playing"]), height=42, expand=True)
        btn_search_right = make_tv_button("🔍 Szukaj", open_search_panel, height=42, expand=True)
        btn_settings_menu = make_tv_button("⚙️ Ustawienia", open_settings_menu_dlg, height=42, expand=True)
        btn_home = make_tv_button("🏠 Menu", open_dashboard, height=42, expand=True)
        btn_exit_right = make_tv_button("🚪 ZAMKNIJ", force_exit_app, height=42, expand=True)

        right_buttons = [btn_vlc, btn_search_right, btn_settings_menu, btn_home, btn_exit_right]

        btn_remote_id = make_tv_button(f"🌐 Kod ID Odbiornika: {device_id} ({device_name})", open_remote_settings_dlg, height=42)
        btn_edit_srv = make_tv_button(f"🌐 Serwer: {creds['server'] or 'ustaw'}", lambda: open_field_editor("server", "Adres serwera (http://...)"), height=42)
        btn_edit_usr = make_tv_button(f"👤 Login: {creds['username'] or 'ustaw'}", lambda: open_field_editor("username", "Nazwa użytkownika"), height=42)
        btn_edit_pas = make_tv_button("🔑 Hasło: " + ("********" if creds["password"] else "ustaw"), lambda: open_field_editor("password", "Hasło", is_pass=True), height=42)
        btn_edit_m3u = make_tv_button(f"📋 Link M3U: {'[Zapisano]' if creds['m3u_url'] else 'opcjonalnie'}", lambda: open_field_editor("m3u_url", "Pełny link M3U"), height=42)
        btn_submit_login = make_tv_button("🚀 ZALOGUJ DO IPTV BP", lambda: do_login(), height=48)
        btn_saved_profiles_login = make_tv_button(f"📋 WYBIERZ ZAPISANĄ LISTĘ ({len(profiles)})", open_profiles_dialog_from_login, height=42)
        btn_exit_login = make_tv_button("🚪 ZAMKNIJ APLIKACJĘ", force_exit_app, height=42)

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

        login_view = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [make_iptv_bp_badge(48), ft.Text("IPTV BP  •  V8.2 DARK", size=22, weight=ft.FontWeight.BOLD, color="#e4e4e7")],
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
            bgcolor="#101216",
            padding=16,
        )

        dash_view = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Row([make_iptv_bp_badge(42), ft.Text("IPTV BP", size=19, weight=ft.FontWeight.BOLD, color="#e4e4e7")], spacing=10),
                            ft.Column([clock_txt, date_txt], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
                            ft.Container(
                                content=ft.Row(dash_top_buttons, spacing=8),
                                bgcolor="#181a20",
                                padding=6,
                                border_radius=26,
                                border=ft.border.all(1, "#2e323d"),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Column(
                        [
                            ft.Row([dash_tile_live, dash_tile_movies, dash_tile_series], spacing=12, expand=5),
                            ft.Row([dash_tile_search, dash_tile_catchup, dash_tile_fav, dash_tile_playlists, dash_tile_settings, dash_tile_exit], spacing=8, expand=2),
                        ],
                        spacing=10,
                        expand=True,
                    ),
                    ft.Row(
                        [
                            ft.Column([footer_playlist_txt, footer_expire_txt], spacing=2),
                            dash_back_hint_txt,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                expand=True,
                spacing=10,
            ),
            expand=True,
            visible=False,
            bgcolor="#101216",
            padding=ft.padding.symmetric(horizontal=22, vertical=14),
        )

        left_panel = ft.Container(
            width=450,
            bgcolor="#16181f",
            padding=10,
            border_radius=10,
            border=ft.border.all(2, "#52525b"),
        )
        right_panel = ft.Container(
            expand=True,
            bgcolor="#16181f",
            padding=12,
            border_radius=10,
            border=ft.border.all(1, "#2e323d"),
        )

        left_panel.content = ft.Column(
            [
                btn_left_top_search,
                ft.Container(
                    content=list_header_lbl,
                    padding=ft.padding.symmetric(horizontal=10, vertical=5),
                    bgcolor="#101216",
                    border_radius=6,
                ),
                list_column,
            ],
            expand=True,
            spacing=5,
        )

        right_panel.content = ft.Column(
            [
                ft.Row(
                    [
                        ft.Row([make_iptv_bp_badge(32), header_dev_lbl], spacing=10),
                        ft.Text("⬅️ Lista | ➡️ Prawy panel", size=12, color="#a1a1aa"),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                video_placeholder,
                settings_overlay_box,
                search_overlay_box,
                now_playing_txt,
                ft.Row(right_buttons, spacing=6),
            ],
            expand=True,
            spacing=6,
        )

        dash_inner_view = ft.Container(
            content=ft.Row([left_panel, right_panel], expand=True, spacing=10),
            expand=True,
            visible=False,
            bgcolor="#101216",
            padding=10,
        )

        guard_view_1 = ft.View(route="/g1", controls=[ft.Container(bgcolor="#101216", expand=True)], padding=0, bgcolor="#101216")
        guard_view_2 = ft.View(route="/g2", controls=[ft.Container(bgcolor="#101216", expand=True)], padding=0, bgcolor="#101216")
        main_app_view = ft.View(
            route="/app",
            controls=[
                ft.Stack(
                    [
                        login_view,
                        dash_view,
                        dash_inner_view,
                        fullscreen_player_view,
                    ],
                    expand=True,
                )
            ],
            padding=0,
            bgcolor="#101216",
        )

        page.views.clear()
        page.views.append(guard_view_1)
        page.views.append(guard_view_2)
        page.views.append(main_app_view)

        def render_viewport():
            items = state["all_built_items"]
            total = len(items)
            list_column.controls.clear()

            is_search_foc = (state["zone"] == "list_search")
            base_s_txt = btn_left_top_search.data["base_text"]
            btn_left_top_search.bgcolor = "#3f3f46" if is_search_foc else "#1c1f26"
            btn_left_top_search.border = ft.border.all(2.5 if is_search_foc else 1, "#f4f4f5" if is_search_foc else "#3f3f46")
            btn_left_top_search.data["text_ctrl"].color = "white"
            btn_left_top_search.data["text_ctrl"].value = f"▶  {base_s_txt}" if is_search_foc else base_s_txt

            if total == 0:
                list_column.controls.append(
                    ft.Text("Brak pozycji. Kliknij 🔍 SZUKAJ powyżej, aby zmienić filtr.", color="#a1a1aa", size=14)
                )
                safe_ui_update()
                return

            state["list_idx"] = max(0, min(state["list_idx"], total - 1))
            cur_idx = state["list_idx"]

            half = VISIBLE_ROWS // 2
            start_vis = max(0, min(cur_idx - half, max(0, total - VISIBLE_ROWS)))
            end_vis = min(total, start_vis + VISIBLE_ROWS)

            for idx in range(start_vis, end_vis):
                entry = items[idx]
                is_foc = (state["zone"] == "list" and idx == cur_idx)
                lbl = f"▶  {entry['label']}" if is_foc else entry["label"]
                btn = ft.Container(
                    content=ft.Row(
                        [
                            ft.Text(
                                lbl,
                                size=15 if is_foc else 14,
                                weight=ft.FontWeight.BOLD,
                                color="white" if is_foc else "#d4d4d8",
                                no_wrap=True,
                                expand=True,
                            )
                        ],
                        alignment=ft.MainAxisAlignment.START,
                    ),
                    expand=True,
                    padding=ft.padding.symmetric(horizontal=12, vertical=4),
                    border_radius=8,
                    bgcolor="#3f3f46" if is_foc else "#1c1f26",
                    border=ft.border.all(2.5 if is_foc else 1, "#f4f4f5" if is_foc else "#2e323d"),
                    alignment=ft.alignment.center_left,
                    on_click=lambda _e, fn=entry["action"]: fn(),
                )
                list_column.controls.append(btn)

            if state["zone"] == "list" and items[cur_idx].get("item") is not None:
                trigger_focus_preview(items[cur_idx]["item"])

            safe_ui_update()

        def update_focus_styles():
            if login_view.visible:
                for i, item in enumerate(login_items):
                    is_foc = (state["zone"] == "login" and state["login_idx"] == i)
                    base_t = item.data["base_text"]
                    item.bgcolor = "#3f3f46" if is_foc else ("#27272a" if i == 5 else ("#451a1a" if i == 7 else "#1c1f26"))
                    item.border = ft.border.all(2.5 if is_foc else 1, "#f4f4f5" if is_foc else "#2e323d")
                    item.data["text_ctrl"].color = "white"
                    item.data["text_ctrl"].value = f"▶  {base_t}" if is_foc else base_t
                safe_ui_update()
                return

            if dash_view.visible:
                render_dashboard_focus()
                return

            if not dash_inner_view.visible:
                return

            left_panel.border = ft.border.all(
                2.5 if state["zone"] in ("list", "list_search") else 1,
                "#a1a1aa" if state["zone"] in ("list", "list_search") else "#2e323d",
            )
            right_panel.border = ft.border.all(
                2.5 if state["zone"] == "right" else 1,
                "#a1a1aa" if state["zone"] == "right" else "#2e323d",
            )

            for i, b in enumerate(right_buttons):
                is_foc = (state["zone"] == "right" and state["right_idx"] == i)
                base_t = b.data["base_text"]
                b.bgcolor = "#3f3f46" if is_foc else ("#451a1a" if b == btn_exit_right else "#1c1f26")
                b.border = ft.border.all(2.5 if is_foc else 1, "#f4f4f5" if is_foc else "#2e323d")
                b.data["text_ctrl"].color = "white"
                b.data["text_ctrl"].value = f"▶ {base_t}" if is_foc else base_t

            render_viewport()

        def build_full_list_data(keep_idx=False):
            prev_idx = state["list_idx"]
            built = []
            v_mode = state["view_mode"]
            pl_ids = get_current_pl_cat_ids()
            update_left_search_bar_label()

            if state["in_profiles"]:
                list_header_lbl.value = f"📋 ZAPISANE LISTY ({len(profiles)})"
                built.append({
                    "label": "➕ DODAJ / EDYTUJ LISTĘ RĘCZNIE",
                    "action": lambda: do_logout(),
                    "item": None,
                })
                for idx, prof in enumerate(profiles):
                    p_name = prof.get("name", f"Lista {idx + 1}")
                    is_act = p_name == state.get("active_profile_name")
                    badge = "🟢 [AKTYWNA] " if is_act else "📋 "
                    built.append({
                        "label": f"{idx + 1}. {badge}{p_name}",
                        "action": lambda pr=prof: apply_profile(pr),
                        "item": None,
                    })
                    if not is_act or len(profiles) > 1:
                        built.append({
                            "label": f"   ↳ 🗑 Usuń: {p_name}",
                            "action": lambda nm=p_name: delete_saved_profile(nm),
                            "item": None,
                        })
                state["all_built_items"] = built
                state["list_idx"] = min(prev_idx if keep_idx else 0, max(0, len(built) - 1))
                update_focus_styles()
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
                hdr_base = f"📂 {kind_lbl}"
            elif v_mode == "episodes":
                raw = list(state["episodes"])
                hdr_base = f"🎞 {state['active_series_title']}"
            elif state["in_fav"]:
                raw = list(favorites_items)
                if state["only_pl"]:
                    raw = [it for it in raw if is_polish_item(it, pl_ids)]
                hdr_base = "⭐ ULUBIONE"
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
                hdr_base = f"📺 {cat_nm}"

            q = state.get("search_query", "").strip().lower()
            if q:
                raw = [
                    it for it in raw
                    if q in (it.get("name") or it.get("title") or it.get("category_name") or "").lower()
                ]
                list_header_lbl.value = f"{hdr_base} [🔍 '{q}': {len(raw)}]"
            else:
                list_header_lbl.value = f"{hdr_base} ({len(raw)})"

            state["filtered"] = raw

            if q:
                def _clear_q():
                    state["search_query"] = ""
                    update_left_search_bar_label()
                    state["list_idx"] = 0
                    build_full_list_data()

                built.append({
                    "label": f"❌ WYCZYŚĆ WYSZUKIWANIE ('{state['search_query']}')",
                    "action": _clear_q,
                    "item": None,
                })

            if v_mode == "episodes":
                built.append({
                    "label": "↩ WRÓĆ DO SERIALI",
                    "action": back_to_streams,
                    "item": None,
                })
            elif v_mode == "streams" and not state["in_fav"]:
                built.append({
                    "label": "↩ WRÓĆ DO KATEGORII",
                    "action": back_to_cats,
                    "item": None,
                })

            first_media_idx = len(built)

            for i, item in enumerate(raw):
                real_num = i + 1
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

                built.append({
                    "label": lbl,
                    "action": action_fn,
                    "item": item,
                })

            state["all_built_items"] = built
            if keep_idx:
                state["list_idx"] = min(prev_idx, max(0, len(built) - 1))
            elif raw:
                state["list_idx"] = first_media_idx
            else:
                state["list_idx"] = 0

            update_focus_styles()

        def back_to_streams():
            state["view_mode"] = "streams"
            build_full_list_data()

        def back_to_cats():
            state["view_mode"] = "categories"
            state["active_cat"] = None
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
            state["view_mode"] = "streams"
            state["active_cat"] = cat_obj
            state["list_idx"] = 0
            cat_id = str(cat_obj.get("category_id", "__ALL__"))
            cache_key = (state["kind"], cat_id)

            if cache_key in state["streams_cache"]:
                state["streams"] = state["streams_cache"][cache_key]
                build_full_list_data()
                return

            list_column.controls.clear()
            list_header_lbl.value = f"⏳ Ładowanie: {cat_obj.get('category_name')}..."
            safe_ui_update()

            def _w():
                try:
                    items = state["client"].get_streams(state["kind"], cat_obj.get("category_id")) or []
                    for it in items:
                        it["kind"] = state["kind"]
                    state["streams"] = items
                    state["streams_cache"][cache_key] = items
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
                state["active_series_id"] = uid
                state["active_series_title"] = title
                list_column.controls.clear()
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
                        build_full_list_data()
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
                "ch_up",
                "ch_down",
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
            elif act in ("ch_up", "next_channel"):
                switch_live_channel_relative(1)
            elif act in ("ch_down", "prev_channel"):
                switch_live_channel_relative(-1)
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

        def check_double_back_to_exit():
            now = time.time()
            if now - app_state["last_back_press_ts"] < 2.2:
                force_exit_app()
            else:
                app_state["last_back_press_ts"] = now
                dash_back_hint_txt.value = "⚠️ Naciśnij WSTECZ jeszcze raz (w ciągu 2 sek.), aby zamknąć IPTV BP!"
                dash_back_hint_txt.color = "#ef4444"
                status_banner.value = "⚠️ Naciśnij WSTECZ jeszcze raz, aby zamknąć aplikację!"
                status_banner.color = "#ef4444"
                now_playing_txt.value = "⚠️ Naciśnij WSTECZ jeszcze raz, aby zamknąć aplikację!"
                safe_ui_update()

        def rebuild_guard_views():
            page.views.clear()
            page.views.append(guard_view_1)
            page.views.append(guard_view_2)
            page.views.append(main_app_view)

        def handle_universal_back():
            rebuild_guard_views()

            if app_state["in_fullscreen_player"]:
                if app_state["osd_list_open"]:
                    close_osd_channel_overlay()
                else:
                    exit_fullscreen_player()
                return

            if app_state["search_open"]:
                close_search_panel()
                return
            if app_state["settings_open"]:
                close_settings_panel()
                return
            if app_state["dialog_open"]:
                return
            if login_view.visible or dash_view.visible:
                check_double_back_to_exit()
                return
            if dash_inner_view.visible:
                if state["view_mode"] == "episodes":
                    back_to_streams()
                elif state["view_mode"] == "streams" and not state["in_fav"] and not state["in_profiles"]:
                    back_to_cats()
                else:
                    open_dashboard()

        def on_android_view_pop(_e):
            handle_universal_back()

        page.on_view_pop = on_android_view_pop
        try:
            page.window_prevent_close = True
            page.window.prevent_close = True
        except Exception:
            pass

        def trigger_current_ok_action():
            now = time.time()
            if now - app_state["last_ok_ts"] < 0.25:
                return
            app_state["last_ok_ts"] = now

            if app_state["in_fullscreen_player"]:
                if not app_state["osd_list_open"]:
                    open_osd_channel_overlay()
                else:
                    channels = state.get("osd_channels", [])
                    if 0 <= state["osd_idx"] < len(channels):
                        select_channel_from_osd(channels[state["osd_idx"]])
                return

            if app_state["search_open"] and search_menu_buttons:
                search_menu_buttons[state["search_idx"]].data["action"]()
                return
            if app_state["settings_open"] and settings_menu_buttons:
                settings_menu_buttons[state["settings_idx"]].data["action"]()
                return
            if app_state["dialog_open"]:
                return

            if login_view.visible:
                login_items[state["login_idx"]].data["action"]()
                return

            if dash_view.visible:
                if state["zone"] == "dash_top":
                    dash_top_buttons[state["dash_top_idx"]].data["action"]()
                else:
                    dash_tiles[state["dash_idx"]].data["action"]()
                return

            if dash_inner_view.visible:
                total_built = len(state["all_built_items"])
                if state["zone"] == "list_search":
                    open_search_panel()
                elif state["zone"] == "list" and 0 <= state["list_idx"] < total_built:
                    state["all_built_items"][state["list_idx"]]["action"]()
                elif state["zone"] == "right":
                    right_buttons[state["right_idx"]].data["action"]()

        def on_key_event(e: ft.KeyboardEvent):
            key = e.key

            # =================================================================
            # STEROWANIE PODCZAS OGLĄDANIA KANAŁU NA PEŁNYM EKRANIE (BEZ WYCHODZENIA)
            # =================================================================
            if app_state["in_fullscreen_player"]:
                if app_state["osd_list_open"]:
                    channels = state.get("osd_channels", [])
                    if key == "Arrow Down" and channels:
                        state["osd_idx"] = (state["osd_idx"] + 1) % len(channels)
                        render_osd_channel_list()
                    elif key == "Arrow Up" and channels:
                        state["osd_idx"] = (state["osd_idx"] - 1) % len(channels)
                        render_osd_channel_list()
                    elif key in ("Page Down", "Channel Down") and channels:
                        state["osd_idx"] = min(len(channels) - 1, state["osd_idx"] + OSD_VISIBLE_ROWS)
                        render_osd_channel_list()
                    elif key in ("Page Up", "Channel Up") and channels:
                        state["osd_idx"] = max(0, state["osd_idx"] - OSD_VISIBLE_ROWS)
                        render_osd_channel_list()
                    elif key == "Arrow Right":
                        # Zmiana kategorii w prawo bez wychodzenia z oglądanego kanału
                        switch_osd_category_relative(1)
                    elif key == "Arrow Left":
                        # Zmiana kategorii w lewo bez wychodzenia z oglądanego kanału
                        switch_osd_category_relative(-1)
                    elif key in ("Enter", "Select", " "):
                        trigger_current_ok_action()
                    elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                        close_osd_channel_overlay()
                else:
                    if key in ("Enter", "Select", " ", "Arrow Left"):
                        open_osd_channel_overlay()
                    elif key in ("Arrow Up", "Channel Up", "Media Track Next", "Page Up"):
                        # Szybkie przełączenie na następny kanał bez otwierania menu
                        switch_live_channel_relative(1)
                    elif key in ("Arrow Down", "Channel Down", "Media Track Previous", "Page Down"):
                        # Szybkie przełączenie na poprzedni kanał bez otwierania menu
                        switch_live_channel_relative(-1)
                    elif key == "Arrow Right":
                        launch_vlc_intent(state["playing_url"], state["now_playing"])
                    elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                        exit_fullscreen_player()
                return

            if app_state["search_open"] and search_menu_buttons:
                if key == "Arrow Down":
                    state["search_idx"] = (state["search_idx"] + 1) % len(search_menu_buttons)
                    app_state["style_search_fn"]()
                elif key == "Arrow Up":
                    state["search_idx"] = (state["search_idx"] - 1) % len(search_menu_buttons)
                    app_state["style_search_fn"]()
                elif key in ("Enter", "Select", " "):
                    trigger_current_ok_action()
                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                    close_search_panel()
                return

            if app_state["settings_open"] and settings_menu_buttons:
                if key == "Arrow Down":
                    state["settings_idx"] = (state["settings_idx"] + 1) % len(settings_menu_buttons)
                    app_state["style_settings_fn"]()
                elif key == "Arrow Up":
                    state["settings_idx"] = (state["settings_idx"] - 1) % len(settings_menu_buttons)
                    app_state["style_settings_fn"]()
                elif key in ("Enter", "Select", " "):
                    trigger_current_ok_action()
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
                    trigger_current_ok_action()
                    return
                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                    handle_universal_back()
                    return
                update_focus_styles()
                return

            if dash_view.visible:
                if state["zone"] == "dash_top":
                    if key == "Arrow Right" and state["dash_top_idx"] < len(dash_top_buttons) - 1:
                        state["dash_top_idx"] += 1
                    elif key == "Arrow Left" and state["dash_top_idx"] > 0:
                        state["dash_top_idx"] -= 1
                    elif key == "Arrow Down":
                        state["zone"] = "dashboard"
                    elif key in ("Enter", "Select", " "):
                        trigger_current_ok_action()
                        return
                    elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                        handle_universal_back()
                        return
                    render_dashboard_focus()
                    return

                state["zone"] = "dashboard"
                if key == "Arrow Right" and state["dash_idx"] < len(dash_tiles) - 1:
                    state["dash_idx"] += 1
                elif key == "Arrow Left" and state["dash_idx"] > 0:
                    state["dash_idx"] -= 1
                elif key == "Arrow Down" and state["dash_idx"] < 3:
                    state["dash_idx"] = min(8, state["dash_idx"] + 3)
                elif key == "Arrow Up":
                    if state["dash_idx"] >= 3:
                        state["dash_idx"] = min(2, max(0, state["dash_idx"] - 3))
                    else:
                        state["zone"] = "dash_top"
                        state["dash_top_idx"] = 0
                elif key in ("Enter", "Select", " "):
                    trigger_current_ok_action()
                    return
                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                    handle_universal_back()
                    return
                render_dashboard_focus()
                return

            if dash_inner_view.visible:
                total_built = len(state["all_built_items"])

                if key in ("Channel Up", "Media Track Next") and state["view_mode"] == "streams" and state["kind"] == "live":
                    switch_live_channel_relative(1)
                    return
                elif key in ("Channel Down", "Media Track Previous") and state["view_mode"] == "streams" and state["kind"] == "live":
                    switch_live_channel_relative(-1)
                    return
                elif key == "Page Down" and total_built > 0:
                    state["zone"] = "list"
                    state["list_idx"] = min(total_built - 1, state["list_idx"] + VISIBLE_ROWS)
                    render_viewport()
                    return
                elif key == "Page Up" and total_built > 0:
                    state["zone"] = "list"
                    state["list_idx"] = max(0, state["list_idx"] - VISIBLE_ROWS)
                    render_viewport()
                    return

                if key == "Arrow Right":
                    if state["zone"] in ("list", "list_search"):
                        state["zone"] = "right"
                        state["right_idx"] = 0
                    elif state["zone"] == "right" and state["right_idx"] < len(right_buttons) - 1:
                        state["right_idx"] += 1
                    update_focus_styles()

                elif key == "Arrow Left":
                    if state["zone"] == "right":
                        state["zone"] = "list"
                        update_focus_styles()

                elif key == "Arrow Down":
                    if state["zone"] == "list_search":
                        state["zone"] = "list"
                        state["list_idx"] = 0
                        render_viewport()
                    elif state["zone"] == "list" and total_built > 0:
                        if state["list_idx"] < total_built - 1:
                            state["list_idx"] += 1
                            render_viewport()
                    elif state["zone"] == "right" and state["right_idx"] < len(right_buttons) - 1:
                        state["right_idx"] += 1
                        update_focus_styles()

                elif key == "Arrow Up":
                    if state["zone"] == "list":
                        if state["list_idx"] > 0:
                            state["list_idx"] -= 1
                            render_viewport()
                        else:
                            state["zone"] = "list_search"
                            render_viewport()
                    elif state["zone"] == "right" and state["right_idx"] > 0:
                        state["right_idx"] -= 1
                        update_focus_styles()

                elif key in ("Enter", "Select", " "):
                    trigger_current_ok_action()

                elif key in ("Backspace", "Escape", "Browser Back", "Go Back"):
                    handle_universal_back()

        page.on_keyboard_event = on_key_event
        update_focus_styles()
        publish_status()

        if state["auto_login"] and has_saved_credentials():
            def _reliable_boot():
                if stop_event.wait(0.25):
                    return
                if login_view.visible and not state["client"]:
                    do_login(is_auto=True)

            threading.Thread(target=_reliable_boot, daemon=True).start()

    except Exception as e:
        err_msg = traceback.format_exc()
        page.add(ft.Text(f"Błąd krytyczny: {e}\n{err_msg}", color="red", size=14))
        page.update()


ft.app(target=main)
