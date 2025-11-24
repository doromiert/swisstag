#!/usr/bin/env python3
import os
import sys
import json
import argparse
import subprocess
import importlib
import logging
import re
import shutil
import time
import webbrowser
import math
import contextlib
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from functools import wraps
from io import BytesIO

# --- Constants & Configuration ---
APP_NAME = "swisstag"
VERSION = "5.0.1"
# Resolve configuration file location.
# Priority (highest -> lowest):
# 1) SWISSTAG_CONFIG env var (full path to a config file)
# 2) XDG_CONFIG_HOME env var (directory) -> $XDG_CONFIG_HOME/swisstag/config.json
# 3) Default: ~/.config/swisstag/config.json
_env_config = os.environ.get("SWISSTAG_CONFIG")
if _env_config:
    CONFIG_FILE = Path(_env_config)
    CONFIG_DIR = CONFIG_FILE.parent
else:
    _xdg = os.environ.get("XDG_CONFIG_HOME")
    if _xdg:
        CONFIG_DIR = Path(_xdg) / "swisstag"
    else:
        CONFIG_DIR = Path.home() / ".config" / "swisstag"
    CONFIG_FILE = CONFIG_DIR / "config.json"

# --- ANSI Colors ---
class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    RESET = '\033[0m'
    CLR = '\033[2K\r' # Clear line and return

# --- Detailed Help Text ---
DETAILED_HELP_TEXT = {
    "album": """
    --album, -a
    Switch to Album Mode.
    """,
    "lyrics": """
    --lyrics, -l
    Fetch and save song lyrics.
    
    Modes:
        embed : (Default) Embeds lyrics directly into the audio file.
        lrc   : Saves lyrics as a separate .lrc file.
        both  : Performs both embed and lrc actions.
    
    --lyrics-source, -L
    Control where lyrics are fetched from.
        interactive : (Default) Always ask the user to select the source for every song.
        auto        : Genius first, then synced sources. If fail, ask user.
        synced      : Force using syncedlyrics (LRC/Time-synced) sources only.
        genius      : Force using Genius only.
    """,
    "fingerprint": """
    --chromaprint, -p
    Use acoustic fingerprinting (AcoustID) to identify files.
    
    Requires 'fpcalc' (chromaprint) to be installed.
    Useful for identifying files with bad filenames.
    """,
}

HELP_MAP = {
    "-a": "album", "--album": "album",
    "-l": "lyrics", "--lyrics": "lyrics",
    "-L": "lyrics", "--lyrics-source": "lyrics",
    "-p": "fingerprint", "--chromaprint": "fingerprint"
}

# --- Dependency Management ---
REQUIRED_PACKAGES = {
    "mutagen": "mutagen",
    "lyricsgenius": "lyricsgenius",
    "musicbrainzngs": "musicbrainzngs",
    "thefuzz": "thefuzz",
    "requests": "requests",
    "unidecode": "unidecode",
    "PIL": "Pillow",
    "syncedlyrics": "syncedlyrics"
}

def check_dependencies() -> List[str]:
    missing = []
    for import_name, install_name in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(install_name)
    # Check for fpcalc binary for fingerprinting
    if "--chromaprint" in sys.argv or "-p" in sys.argv:
        if not shutil.which("fpcalc"):
             print(f"{Colors.YELLOW}[WARN] 'fpcalc' (chromaprint) not found. Fingerprinting will fail.{Colors.RESET}")
    return missing

def install_dependencies_interactive(missing: List[str]):
    print(f"[{APP_NAME}] Missing dependencies detected: {', '.join(missing)}")
    cmd = [sys.executable, "-m", "pip", "install", *missing]
    in_venv = hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
    if not in_venv:
        cmd.extend(["--user", "--break-system-packages"])
    try:
        subprocess.check_call(cmd)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except subprocess.CalledProcessError:
        sys.exit(1)

_missing_deps = check_dependencies()
if "--install-deps" in sys.argv:
    if _missing_deps: install_dependencies_interactive(_missing_deps)
    else: sys.exit(0)
if _missing_deps:
    print(f"[{APP_NAME}] Critical dependencies missing: {', '.join(_missing_deps)}")
    sys.exit(1)

import requests
import mutagen
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, USLT, COMM, TCON, TPE2, TRCK
from mutagen.mp3 import MP3
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
import musicbrainzngs
import lyricsgenius
import syncedlyrics
from unidecode import unidecode
from thefuzz import fuzz
from PIL import Image

# --- Config & Defaults ---
DEFAULT_CONFIG = {
    "defaults": {
        "rename": False,
        "match_filename": True,
        "feat_handling": "split",
        "lyrics": {"fetch": True, "mode": "embed", "source": "interactive"},
        "cover": {"size": "1920x1920", "keep_resized": True, "extract": {"crop": False, "scale": True}}
    },
    "separators": {"artist": "; ", "genre": "; "},
    "regex": {"featured_artist": r"(?i)[(\[](?:feat|ft|featuring|with)\.?\s+(.*?)[)\]]"},
    "artist_groups": {},
    "api_keys": {
        "genius": "",
        "acoustid": "cSpUJKpD" # Default generic key
    }
}

# --- Helpers ---

class Logger:
    def __init__(self, debug_str: Optional[str]):
        self.modes = set(debug_str.split(',')) if debug_str else set()
        if 'all' in self.modes:
            self.modes = {'network', 'cmd', 'vars', 'config'}
        self.is_dry = 'dry' in self.modes

    def log(self, mode: str, message: str):
        if mode in self.modes or 'all' in self.modes:
            print(f"[{mode.upper()}] {message}")

    def info(self, message: str): pass 
    def warn(self, message: str): print(f"{Colors.YELLOW}[WARN] {message}{Colors.RESET}")
    def error(self, message: str): print(f"{Colors.RED}[ERROR] {message}{Colors.RESET}", file=sys.stderr)

class TreeUI:
    def __init__(self, total, album_name=None):
        self.total = total
        self.idx = 0
        self.prefix = "├──"
        self.root_indent = "    "
        self.current_title = ""
        if album_name:
            print(f"{self.root_indent}Retagging album: {Colors.BOLD}{album_name}{Colors.RESET}")

    def next(self, title):
        self.idx += 1
        self.prefix = "└──" if self.idx == self.total else "├──"
        self.current_title = title
        # Print basic title first
        print(f"{self.root_indent}{self.prefix} {title} ({self.idx}/{self.total})")

    def step(self, msg):
        indent = "    " if self.prefix == "└──" else "│   "
        sys.stdout.write(f"{Colors.CLR}{self.root_indent}{indent}└── {msg}")
        sys.stdout.flush()

    def finish(self, status: str = 'success', warnings: List[str] = None):
        indent = "    " if self.prefix == "└──" else "│   "
        
        icon = f"{Colors.GREEN}[✓]{Colors.RESET}"
        
        extra_msg = "Done"
        if status == 'warning': 
            icon = f"{Colors.YELLOW}[!]{Colors.RESET}"
            extra_msg = "Attention Required"
        elif status == 'error': 
            icon = f"{Colors.RED}[✗]{Colors.RESET}"
            extra_msg = "Failed"

        sys.stdout.write(f"{Colors.CLR}{self.root_indent}{indent}└── {icon} {extra_msg}")
        sys.stdout.flush()
        print("") # Newline
        
        if warnings:
            for w in warnings:
                print(f"{self.root_indent}{indent}    {Colors.YELLOW}↳ {w}{Colors.RESET}")

class ConfigManager:
    def __init__(self, cli_overrides: List[str] = None):
        self.data = self._load()
        if cli_overrides: self._apply_overrides(cli_overrides)

    def _load(self) -> Dict:
        if not CONFIG_FILE.exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, 'w') as f: json.dump(DEFAULT_CONFIG, f, indent=4)
            return DEFAULT_CONFIG
        with open(CONFIG_FILE, 'r') as f:
            try: return json.load(f)
            except json.JSONDecodeError: return DEFAULT_CONFIG

    def save(self):
        with open(CONFIG_FILE, 'w') as f: json.dump(self.data, f, indent=4)

    def get(self, path: str, default=None):
        keys = path.split('.')
        val = self.data
        for key in keys:
            if isinstance(val, dict) and key in val: val = val[key]
            else: return default
        return val

    def set(self, path: str, value: Any):
        keys = path.split('.')
        target = self.data
        for key in keys[:-1]: target = target.setdefault(key, {})
        target[keys[-1]] = value

    def _apply_overrides(self, overrides: List[str]):
        for item in overrides:
            if '=' in item:
                k, v = item.split('=', 1)
                if v.lower() == 'true': v = True
                elif v.lower() == 'false': v = False
                elif v.isdigit(): v = int(v)
                self.set(k, v)

def api_retry(max_retries=3, delay=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except (requests.exceptions.RequestException, TimeoutError) as e:
                    retries += 1
                    if retries == max_retries: raise e
                    time.sleep(delay * retries)
            return None
        return wrapper
    return decorator

def get_attr(obj, key, default=None):
    if isinstance(obj, dict): return obj.get(key, default)
    return getattr(obj, key, default)

class TokenWizard:
    @staticmethod
    def run(config_mgr: ConfigManager):
        print("\n=== Genius API Token Setup ===")
        webbrowser.open("https://genius.com/api-clients")
        token = input("\nPaste your 'Client Access Token' here: ").strip()
        if not token: return
        try:
            genius = lyricsgenius.Genius(token, verbose=False)
            res = genius.search_songs("Test", per_page=1)
            if res:
                config_mgr.set("api_keys.genius", token)
                config_mgr.save()
                print(f"Token saved.")
        except Exception as e: print(f"Error: {e}")

class MetadataProvider:
    def __init__(self, config: ConfigManager, logger: Logger):
        self.config = config
        self.logger = logger
        self.genius = None
        self.mb_active = True
        token = config.get("api_keys.genius") or os.environ.get("GENIUS_ACCESS_TOKEN")
        if token:
            self.genius = lyricsgenius.Genius(token, verbose=False)
        else:
            self.logger.warn("No Genius Token found.")
        musicbrainzngs.set_useragent(APP_NAME, VERSION, "user@localhost")
        self.acoustid_key = config.get("api_keys.acoustid", "cSpUJKpD")

    @api_retry()
    def _genius_search_hits(self, title, artist):
        if not self.genius: return {}
        try:
            return self.genius.search_songs(f"{artist} {title}", per_page=5)
        except requests.exceptions.HTTPError as e:
            # If Genius is returning 403 Forbidden, disable the provider and warn the user
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            if status == 403:
                try:
                    self.logger.warn("Genius API returned 403 Forbidde n. Disabling Genius provider.\n" \
                                     "Set a valid token in config or via GENIUS_ACCESS_TOKEN to re-enable.")
                except Exception:
                    pass
                self.genius = None
                return {}
            raise

    @api_retry()
    def search_album_candidates(self, query: str) -> List[Dict]:
        if not self.genius: return []
        self.logger.log("network", f"Searching Genius for albums: {query}")
        try:
            res = self.genius.search_albums(query, per_page=5)
        except requests.exceptions.HTTPError as e:
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            if status == 403:
                try:
                    self.logger.warn("Genius API returned 403 Forbidden while searching albums. Disabling Genius provider.")
                except Exception:
                    pass
                self.genius = None
                return []
            raise
        candidates = []
        if res and 'sections' in res:
            for section in res.get('sections', []):
                if section['type'] == 'album':
                    for hit in section.get('hits', []):
                        result = hit['result']
                        artist_name = get_attr(result.get('artist'), 'name', 'Unknown')
                        candidates.append({
                            "id": result['id'],
                            "title": result.get('name', result.get('title', 'Unknown')),
                            "artist": artist_name,
                            "cover_url": result.get('cover_art_url'),
                            "url": result.get('url')
                        })
        return candidates

    @api_retry()
    def fetch_album_by_id(self, album_id: int) -> Dict:
        if not self.genius: return {}
        self.logger.log("network", f"Fetching Album Details ID: {album_id}")
        try:
            album_raw = self.genius.album(album_id)
        except requests.exceptions.HTTPError as e:
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            if status == 403:
                try:
                    self.logger.warn("Genius API returned 403 Forbidden while fetching album. Disabling Genius provider.")
                except Exception:
                    pass
                self.genius = None
                return {}
            raise
        album_info = album_raw['album'] if (isinstance(album_raw, dict) and 'album' in album_raw) else album_raw
        
        data = {
            "album": get_attr(album_info, 'name'),
            "artist": get_attr(get_attr(album_info, 'artist'), 'name'),
            "cover_url": get_attr(album_info, 'cover_art_url'),
            "tracks": [],
            "year": None, "genre": None
        }
        tracks_raw = self.genius.album_tracks(album_id)
        if tracks_raw and 'tracks' in tracks_raw:
             for t in tracks_raw['tracks']:
                t_song = t.get('song', {})
                data['tracks'].append({
                    "title": t_song.get('title'),
                    "number": t.get('number'),
                    "id": t_song.get('id'),
                    "artist": t_song.get('artist_names')
                })
        
        if self.mb_active and data['album'] and data['artist']:
            try:
                mb_res = musicbrainzngs.search_releases(artist=data['artist'], release=data['album'], limit=1)
                if mb_res['release-list']:
                    rel = mb_res['release-list'][0]
                    if 'date' in rel: data['year'] = rel['date'][:4]
            except Exception: pass
        return data

    @api_retry()
    def _genius_get_song(self, song_id):
        if not self.genius: return None
        try:
            return self.genius.song(song_id)
        except requests.exceptions.HTTPError as e:
            status = getattr(getattr(e, 'response', None), 'status_code', None)
            if status == 403:
                try:
                    self.logger.warn("Genius API returned 403 Forbidden while fetching song. Disabling Genius provider.")
                except Exception:
                    pass
                self.genius = None
                return None
            raise

    def interactive_lyrics_picker(self, title, artist) -> Optional[str]:
        """
        Interactive Wizard to choose lyrics source with Retry Loop.
        """
        # Clear the previous line to make the UI cleaner if called in a loop
        print(f"\n{Colors.BLUE}--- Lyrics Selection: {title} ---{Colors.RESET}")
        
        while True:
            print(f"[s] Skip lyrics for this song")
            print(f"[1] Genius")
            print(f"[2] SyncedLyrics (LRC)")
            print(f"[3] Manual Input")
            
            mode = input("Select source: ").strip().lower()
            
            if mode == 's': return None
            
            lyrics = None
            
            # 1. GENIUS SEARCH
            if mode == '1':
                hits_raw = self._genius_search_hits(title, artist)
                if not hits_raw or 'hits' not in hits_raw:
                    print(f"{Colors.YELLOW}No Genius results found.{Colors.RESET}")
                else:
                    hits = hits_raw['hits']
                    for i, hit in enumerate(hits):
                        res = hit['result']
                        print(f"  [{i+1}] {res['full_title']}")
                    print(f"  [b] Back")
                    
                    sub = input("Select song (1-5): ").strip().lower()
                    if sub != 'b':
                        if sub.isdigit():
                            idx = int(sub) - 1
                            if 0 <= idx < len(hits):
                                sel_id = hits[idx]['result']['id']
                                s = self._genius_get_song(sel_id)
                                if s: lyrics = s.lyrics
                        else:
                             print("Invalid selection.")

            # 2. SYNCEDLYRICS SEARCH
            elif mode == '2':
                print("Searching providers...")
                lyrics = self.get_synced_lyrics(title, artist)
                if not lyrics:
                    print(f"{Colors.YELLOW}No synced lyrics found.{Colors.RESET}")
                else:
                    print(f"Found lyrics ({len(lyrics)} chars).")

            # 3. MANUAL INPUT
            elif mode == '3':
                print("Paste lyrics below (Press Ctrl+D or Ctrl+Z on new line to finish):")
                try:
                    lines = sys.stdin.read()
                    lyrics = lines if lines.strip() else None
                except: pass

            else:
                print("Invalid option.")

            if lyrics:
                return lyrics
            
            # Retry Prompt
            print(f"{Colors.YELLOW}Search failed or cancelled.{Colors.RESET}")
            retry = input("Do you want to retry searching for lyrics? [Y/n]: ").strip().lower()
            if retry == 'n':
                return None
            # Loop continues otherwise

    def get_synced_lyrics(self, title, artist):
        self.logger.log("network", f"Searching syncedlyrics for: {title} - {artist}")
        try:
            # Suppress stdout/stderr from syncedlyrics to hide 401 spam
            with open(os.devnull, 'w') as f, contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
                lyrics = syncedlyrics.search(f"{artist} {title}")
            
            if lyrics:
                self.logger.log("vars", f"Found synced lyrics (len {len(lyrics)})")
                return lyrics
        except Exception as e:
            self.logger.warn(f"Synced lyrics search failed: {e}")
        return None

    def fetch_lyrics_for_track(self, track_id, title=None, artist=None, source_mode="auto") -> str:
        # 1. INTERACTIVE MODE (Explicitly requested OR Default)
        if source_mode == "interactive":
            return self.interactive_lyrics_picker(title, artist)
        
        # 2. SYNCED ONLY
        if source_mode == "synced":
            return self.get_synced_lyrics(title, artist)

        # 3. GENIUS ONLY
        if source_mode == "genius":
             song = self._genius_get_song(track_id)
             if isinstance(song, dict):
                 if 'song' in song: song = song['song']
                 return song.get('lyrics')
             return get_attr(song, 'lyrics')

        # 4. AUTO MODE (Default)
        # Priority: Genius ID -> Genius Search Fallback -> SyncedLyrics -> Interactive Rescue
        
        lyrics = None
        
        # A. Try Genius ID
        self.logger.log("network", f"Fetching Lyrics ID: {track_id}")
        try:
            song = self._genius_get_song(track_id)
            if isinstance(song, dict):
                 if 'song' in song: song = song['song']
                 lyrics = song.get('lyrics')
            else:
                 lyrics = get_attr(song, 'lyrics')
        except: pass

        # B. Try Genius Search Fallback
        if not lyrics and title and artist:
            self.logger.log("network", f"ID failed. Fallback Genius search: {title}")
            try:
                fallback = self.genius.search_song(title, artist, get_full_info=False)
                if fallback: lyrics = fallback.lyrics
            except Exception: pass
        
        # C. Try SyncedLyrics
        if not lyrics and title and artist:
             self.logger.log("network", "Genius empty. Trying syncedlyrics...")
             lyrics = self.get_synced_lyrics(title, artist)
             
        # D. Interactive Rescue (If Auto failed and we are in a terminal)
        if not lyrics and sys.stdin.isatty():
             print(f"\n{Colors.YELLOW}[!] Auto-fetch failed for: {title}{Colors.RESET}")
             # Ask user if they want to manually intervene
             choice = input("    Select source manually? [y/N]: ").strip().lower()
             if choice == 'y':
                 lyrics = self.interactive_lyrics_picker(title, artist)

        if lyrics:
            self.logger.log("vars", f"Lyrics len: {len(lyrics)}")
        else:
            self.logger.log("vars", "No lyrics found.")
        return lyrics

    def fetch_song_data(self, query: Dict, source_mode="auto") -> Dict:
        data = {"title": query.get("name"), "artist": query.get("artist")}
        
        if source_mode == "interactive":
            lyr = self.interactive_lyrics_picker(data['title'], data['artist'])
            data['lyrics'] = lyr
        
        # Standard Genius Metadata Fetch
        if self.genius and data['title'] and data['artist']:
            hits = self._genius_search_hits(data["title"], data["artist"])
            best_hit = None
            best_score = 0
            if hits and isinstance(hits, dict) and 'hits' in hits:
                for hit in hits['hits']:
                    res = hit['result']
                    score = fuzz.token_sort_ratio(res['title'], data['title'])
                    if score > best_score:
                        best_score = score; best_hit = res
            
            if best_hit and best_score >= 70:
                song = self._genius_get_song(best_hit['id'])
                if isinstance(song, dict) and 'song' in song: song = song['song']
                
                # Logic for Single Song Mode Lyrics
                if not data.get('lyrics'): 
                     data["lyrics"] = get_attr(song, 'lyrics')
                     if not data["lyrics"] and source_mode == "auto":
                         data["lyrics"] = self.get_synced_lyrics(data['title'], data['artist'])
                         
                         # Rescue for Single Mode
                         if not data["lyrics"] and sys.stdin.isatty():
                             print(f"\n{Colors.YELLOW}[!] Auto-fetch failed.{Colors.RESET}")
                             if input("    Select source manually? [y/N]: ").lower() == 'y':
                                 data["lyrics"] = self.interactive_lyrics_picker(data['title'], data['artist'])

                data["title"] = get_attr(song, 'title')
                data["artist"] = get_attr(song, 'artist_names')
                if get_attr(song, 'song_art_image_url'):
                    data["cover_url"] = get_attr(song, 'song_art_image_url')
                alb = get_attr(song, 'album')
                if alb: data["album"] = get_attr(alb, 'name')
        return data

    def get_acoustic_fingerprint(self, filepath: Path) -> Optional[Dict]:
        """Runs fpcalc and queries AcoustID."""
        if not shutil.which("fpcalc"): return None
        try:
            self.logger.log("cmd", f"fpcalc -json '{filepath}'")
            res = subprocess.check_output(["fpcalc", "-json", str(filepath)], stderr=subprocess.DEVNULL)
            fp_data = json.loads(res)
            
            duration = fp_data.get("duration")
            fingerprint = fp_data.get("fingerprint")
            
            if duration and fingerprint:
                self.logger.log("network", "Querying AcoustID API...")
                url = "https://api.acoustid.org/v2/lookup"
                params = {
                    "client": self.acoustid_key,
                    "meta": "recordings+releasegroups",
                    "duration": int(duration),
                    "fingerprint": fingerprint
                }
                r = requests.get(url, params=params)
                if r.status_code == 200:
                    resp = r.json()
                    if resp.get("results"):
                        # Get best result
                        result = resp["results"][0]
                        if result.get("recordings"):
                            rec = result["recordings"][0]
                            title = rec.get("title")
                            artists = [a["name"] for a in rec.get("artists", [])]
                            album = None
                            if rec.get("releasegroups"):
                                album = rec["releasegroups"][0].get("title")
                            
                            return {
                                "title": title,
                                "artist": artists[0] if artists else "Unknown",
                                "album": album,
                                "duration": duration
                            }
        except Exception as e:
            self.logger.error(f"Fingerprinting failed: {e}")
        return None

class FileHandler:
    def __init__(self, config: ConfigManager, logger: Logger):
        self.config = config
        self.logger = logger

    def infer_dirs(self, path: Path) -> Dict[str, str]:
        p = path.resolve()
        if p.is_file(): p = p.parent
        parts = p.parts
        if len(parts) >= 2: return {"artist": parts[-2], "album": parts[-1]}
        return {}

    def rename_file(self, filepath: Path, metadata: Dict):
        title = metadata.get("title")
        if not title: return filepath
        ext = filepath.suffix
        clean_title = re.sub(r'[<>:"/\\|?*]', '', title)
        new_path = filepath.parent / f"{clean_title}{ext}"
        self.logger.log("cmd", f"mv '{filepath}' '{new_path}'")
        if not self.logger.is_dry:
            try:
                filepath.rename(new_path)
                return new_path
            except OSError as e:
                self.logger.error(f"Rename failed: {e}")
                return filepath
        return new_path

    def autosort(self, filepath: Path, metadata: Dict) -> Path:
        art_tag = metadata.get("album_artist", metadata.get("artist", "Unknown"))
        if isinstance(art_tag, list): art_tag = art_tag[0]
        artist = re.sub(r'[<>:"/\\|?*]', '', art_tag)
        album = re.sub(r'[<>:"/\\|?*]', '', metadata.get("album", "Unknown Album"))
        dest_dir = filepath.parent.parent / artist / album 
        new_path = dest_dir / filepath.name
        self.logger.log("cmd", f"mkdir -p '{dest_dir}' && mv '{filepath}' '{new_path}'")
        if not self.logger.is_dry:
            dest_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(filepath, new_path)
                return new_path
            except shutil.Error: return new_path
        return new_path

    def save_lrc(self, filepath: Path, lyrics: str):
        lrc_path = filepath.with_suffix('.lrc')
        self.logger.log("cmd", f"Write lyrics to: {lrc_path}")
        if not self.logger.is_dry and lyrics:
            try:
                lrc_path.parent.mkdir(parents=True, exist_ok=True)
                with open(lrc_path, 'w', encoding='utf-8') as f: f.write(lyrics)
            except Exception as e: self.logger.error(f"Failed to save .lrc: {e}")

class Tagger:
    def __init__(self, config: ConfigManager, logger: Logger):
        self.config = config
        self.logger = logger
        self.feat_regex = re.compile(self.config.get("regex.featured_artist"))

    def load_file(self, path: Path):
        try: return mutagen.File(path, easy=False)
        except Exception: return None
    
    def read_existing_metadata(self, path: Path) -> Dict[str, str]:
        audio = self.load_file(path)
        if not audio: return {}
        meta = {}
        
        # Attempt to read common tags based on type
        try:
            # ID3
            if isinstance(audio, MP3) and audio.tags:
                if 'TPE1' in audio.tags: meta['artist'] = str(audio.tags['TPE1'])
                if 'TIT2' in audio.tags: meta['title'] = str(audio.tags['TIT2'])
                if 'TALB' in audio.tags: meta['album'] = str(audio.tags['TALB'])
            # FLAC / Vorbis
            elif isinstance(audio, (FLAC, OggVorbis)):
                if 'artist' in audio: meta['artist'] = audio['artist'][0]
                if 'title' in audio: meta['title'] = audio['title'][0]
                if 'album' in audio: meta['album'] = audio['album'][0]
            # MP4
            elif isinstance(audio, MP4):
                if '\xa9ART' in audio: meta['artist'] = audio['\xa9ART'][0]
                if '\xa9nam' in audio: meta['title'] = audio['\xa9nam'][0]
                if '\xa9alb' in audio: meta['album'] = audio['\xa9alb'][0]
            # Generic Fallback
            elif audio.tags:
                if 'artist' in audio.tags: meta['artist'] = str(audio.tags['artist'][0])
                if 'title' in audio.tags: meta['title'] = str(audio.tags['title'][0])
                if 'album' in audio.tags: meta['album'] = str(audio.tags['album'][0])
        except Exception: pass
            
        return meta

    def get_duration(self, path: Path) -> float:
        try:
            f = mutagen.File(path)
            return f.info.length if f and f.info else 0.0
        except: return 0.0

    def handle_features(self, meta: Dict):
        mode = self.config.get("defaults.feat_handling")
        if mode == "keep": return
        
        def extract_from_string(s):
            found = []
            clean_s = s
            match = self.feat_regex.search(s)
            if match:
                feat_str = match.group(1)
                # Split on commas and ampersands, allowing optional surrounding whitespace
                found = [f.strip() for f in re.split(r"\s*(?:,|&)\s*", feat_str) if f.strip()]
                clean_s = self.feat_regex.sub("", s).strip()
            else:
                # If there's no explicit feat/with token, also handle plain artist lists
                # like: "Artist A, Artist B & Artist C" by splitting on commas and ampersands.
                # This allows international characters (e.g., "Łona, Andrzej & Kacper").
                if re.search(r'[,&]', s):
                    parts = [f.strip() for f in re.split(r"\s*(?:,|&)\s*", s) if f.strip()]
                    if len(parts) > 1:
                        found = parts
                        # Keep the first part as the "clean" primary value
                        clean_s = parts[0]
            return found, clean_s

        # FIX: Guard against NoneType error if artist is missing
        if "artist" not in meta or meta["artist"] is None:
            meta["artist"] = []
        elif isinstance(meta["artist"], str): 
            meta["artist"] = [meta["artist"]]
        # Ensure it's always a list
        if not isinstance(meta["artist"], list):
            meta["artist"] = [str(meta["artist"])]

        if meta.get("title"):
            feats, clean_title = extract_from_string(meta["title"])
            for f in feats:
                if f not in meta["artist"]: meta["artist"].append(f)
            if feats:
                if mode == "split": meta["title"] = f"! {clean_title}"
                elif mode == "split-clean": meta["title"] = clean_title

        current_artists = list(meta["artist"])
        new_artist_list = []
        for art in current_artists:
            feats, clean_art = extract_from_string(art)
            # Further split the cleaned artist string on commas and ampersands
            parts = [p.strip() for p in re.split(r"\s*(?:,|&)\s*", clean_art) if p.strip()]
            for part in parts:
                if part not in new_artist_list: new_artist_list.append(part)
            # Add any featured artists extracted from the string
            for f in feats:
                if f not in new_artist_list: new_artist_list.append(f)
        meta["artist"] = new_artist_list

    def expand_artist_groups(self, meta: Dict):
        groups = self.config.get("artist_groups", {})
        if not groups: return
        group_map = {k.lower(): v for k, v in groups.items()}
        target_keys = ["artist", "album_artist"]
        for key in target_keys:
            if key not in meta or not meta[key]: continue
            current = meta[key]
            if isinstance(current, str): current = [current]
            expanded_list = []
            for artist in current:
                expanded_list.append(artist)
                members = group_map.get(artist.lower())
                if members:
                    if isinstance(members, str): members = [members]
                    expanded_list.extend(members)
            seen = set(); final_list = []
            for x in expanded_list:
                if x not in seen: final_list.append(x); seen.add(x)
            meta[key] = final_list

    def _join_artists(self, artists: Any) -> str:
        sep = self.config.get("separators.artist", "; ")
        if isinstance(artists, list): return sep.join(artists)
        return str(artists)

    def apply_metadata(self, audio, meta: Dict, manual: Dict = None):
        if manual: meta.update(manual)
        self.handle_features(meta)
        self.expand_artist_groups(meta)
        if self.logger.is_dry: return

        if "artist" in meta: meta["artist"] = self._join_artists(meta["artist"])
        if "album_artist" in meta: meta["album_artist"] = self._join_artists(meta["album_artist"])

        if isinstance(audio, MP3): self._tag_id3(audio, meta)
        elif isinstance(audio, FLAC): self._tag_vorbis(audio, meta)
        elif isinstance(audio, MP4): self._tag_mp4(audio, meta)
        
        try: audio.save()
        except Exception as e: self.logger.error(f"Failed to save tags: {e}")

    def _should_embed(self): return self.config.get("defaults.lyrics.mode") in ['embed', 'both']

    def _tag_id3(self, audio, meta):
        if audio.tags is None: audio.add_tags()
        if meta.get('title'): audio.tags.add(TIT2(encoding=3, text=meta['title']))
        if meta.get('album'): audio.tags.add(TALB(encoding=3, text=meta['album']))
        if meta.get('year'): audio.tags.add(TDRC(encoding=3, text=str(meta['year'])))
        if meta.get('genre'): audio.tags.add(TCON(encoding=3, text=meta['genre']))
        if meta.get('track_number'): audio.tags.add(TRCK(encoding=3, text=str(meta['track_number'])))
        if meta.get('lyrics') and self._should_embed():
            audio.tags.add(USLT(encoding=3, lang='eng', desc='desc', text=meta['lyrics']))
        if meta.get('artist'): audio.tags.add(TPE1(encoding=3, text=meta['artist']))
        if meta.get('album_artist'): audio.tags.add(TPE2(encoding=3, text=meta['album_artist']))

    def _tag_vorbis(self, audio, meta):
        if meta.get('title'): audio['title'] = meta['title']
        if meta.get('album'): audio['album'] = meta['album']
        if meta.get('year'): audio['date'] = str(meta['year'])
        if meta.get('genre'): audio['genre'] = meta['genre']
        if meta.get('track_number'): audio['tracknumber'] = str(meta['track_number'])
        if meta.get('lyrics') and self._should_embed(): audio['lyrics'] = meta['lyrics']
        if meta.get('artist'): audio['artist'] = meta['artist']
        if meta.get('album_artist'): audio['albumartist'] = meta['album_artist']

    def _tag_mp4(self, audio, meta):
        if meta.get('title'): audio['\xa9nam'] = meta['title']
        if meta.get('album'): audio['\xa9alb'] = meta['album']
        if meta.get('year'): audio['\xa9day'] = str(meta['year'])
        if meta.get('genre'): audio['\xa9gen'] = meta['genre']
        if meta.get('lyrics') and self._should_embed(): audio['\xa9lyr'] = meta['lyrics']
        if meta.get('artist'): audio['\xa9ART'] = meta['artist']
        if meta.get('album_artist'): audio['aART'] = meta['album_artist']

    def apply_cover(self, audio, image_path: str):
        if self.logger.is_dry or not image_path: return
        try:
            with open(image_path, 'rb') as f: img_data = f.read()
            if isinstance(audio, MP3): audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img_data))
            elif isinstance(audio, FLAC):
                p = Picture(); p.type = 3; p.mime = "image/jpeg"; p.desc = "Cover"; p.data = img_data
                audio.add_picture(p)
            elif isinstance(audio, MP4): audio['covr'] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
        except Exception as e: self.logger.error(f"Cover apply error: {e}")

    def save_cover(self, audio_file_path: Path, image_data: bytes, album_name: str):
        if self.logger.is_dry or not image_data: return
        try:
            album_dir = audio_file_path.parent
            cover_dir = album_dir / "Cover Art"
            cover_dir.mkdir(exist_ok=True)
            cfg_size_str = self.config.get("defaults.cover.size", "1000x1000")
            keep_resized = self.config.get("defaults.cover.keep_resized", True)
            try: max_w, max_h = map(int, cfg_size_str.lower().split('x'))
            except: max_w, max_h = 1000, 1000
            img = Image.open(BytesIO(image_data))
            w, h = img.size
            safe_name = re.sub(r'[<>:"/\\|?*]', '', album_name)
            if w <= max_w and h <= max_h:
                with open(cover_dir / f"{safe_name}.jpg", 'wb') as f: f.write(image_data)
            else:
                if keep_resized:
                    wk, hk = math.floor(w / 1000), math.floor(h / 1000)
                    with open(cover_dir / f"{safe_name} {wk}kx{hk}k.jpg", 'wb') as f: f.write(image_data)
                img.thumbnail((max_w, max_h))
                img.save(cover_dir / f"{safe_name}.jpg", "JPEG")
        except Exception as e: self.logger.error(f"Failed to save cover art to file: {e}")

class SwissTag:
    def __init__(self):
        self.check_extended_help()
        self.args = self.parse_args()
        self.config = ConfigManager(self.args.temp_set)
        
        if self.args.lyrics: self.config.set("defaults.lyrics.mode", self.args.lyrics)
        if self.args.lyrics_source: self.config.set("defaults.lyrics.source", self.args.lyrics_source)
        if self.args.feat_handling: self.config.set("defaults.feat_handling", self.args.feat_handling)
        
        if self.args.setup_token: TokenWizard.run(self.config); sys.exit(0)
        if self.args.config_action: self.handle_config_action(); sys.exit(0)
        
        self.logger = Logger(self.args.debug)
        self.meta_provider = MetadataProvider(self.config, self.logger)
        self.file_handler = FileHandler(self.config, self.logger)
        self.tagger = Tagger(self.config, self.logger)

    def check_extended_help(self):
        if "-h" in sys.argv or "--help" in sys.argv:
            other_args = [a for a in sys.argv if a not in ["-h", "--help", sys.argv[0]]]
            if not other_args: return 
            found_topics = []
            for arg in sys.argv:
                if arg in ["-h", "--help", sys.argv[0]]: continue
                clean_arg = arg.split('=')[0]
                if clean_arg in HELP_MAP: found_topics.append(HELP_MAP[clean_arg])
            if found_topics:
                print(f"\n{APP_NAME} v{VERSION} - Detailed Help\n")
                seen = set()
                for topic in found_topics:
                    if topic not in seen: print(DETAILED_HELP_TEXT[topic]); seen.add(topic)
                sys.exit(0)

    def parse_args(self):
        parser = argparse.ArgumentParser(description="Swisstag: Automated Music Tagger")
        parser.add_argument("inputs", nargs="*", default=["."], help="Files or directories")
        parser.add_argument("--install-deps", action="store_true", help="Install dependencies")
        parser.add_argument("--setup-token", action="store_true", help="Setup Genius Token")
        parser.add_argument("-a", "--album", action="store_true", help="Album Mode")
        parser.add_argument("-s", "--search", nargs="+", help="Manual search")
        parser.add_argument("-t", "--manual-tags", nargs="+", help="Override tags")
        parser.add_argument("-F", "--feat-handling", choices=['keep', 'split', 'split-clean'], default="split")
        parser.add_argument("-f", "--filesystem", help="rename, match-filename, infer-dirs, autosort")
        parser.add_argument("-c", "--cover-art", help="auto, file=path, extract")
        parser.add_argument("-l", "--lyrics", help="embed, lrc, both")
        parser.add_argument("-L", "--lyrics-source", choices=['auto', 'interactive', 'synced', 'genius'], help="auto, interactive, synced, genius")
        parser.add_argument("-p", "--chromaprint", action="store_true", help="Use acoustic fingerprinting")
        parser.add_argument("-d", "--debug", nargs="?", const="dry", help="dry, network, cmd, vars, all")
        parser.add_argument("-C", "--config", nargs="+", dest="config_action", help="config ops")
        parser.add_argument("-S", "--set", dest="temp_set", nargs="+", help="temp config")
        parser.add_argument("--about", action="store_true")
        parser.add_argument("-v", "--version", action="store_true")
        return parser.parse_args()

    def handle_config_action(self):
        action = self.args.config_action[0]
        if action == "get" and len(self.args.config_action) > 1: print(self.config.get(self.args.config_action[1]))
        elif action == "set" and len(self.args.config_action) > 2:
            self.config.set(self.args.config_action[1], self.args.config_action[2])
            self.config.save()
            print(f"Set {self.args.config_action[1]} to {self.args.config_action[2]}")

    def parse_kv(self, items: List[str]) -> Dict[str, str]:
        if not items: return {}
        res = {}
        for i in items:
            if "=" in i: k, v = i.split("=", 1); res[k] = v
        return res

    def run(self):
        if self.args.about or self.args.version: print(f"{APP_NAME} v{VERSION}"); return
        
        fs_opts = self.args.filesystem.split(',') if self.args.filesystem else []

        # Gather targets
        queue = []
        for i in self.args.inputs:
            path = Path(i).resolve()
            if not path.exists():
                print(f"{Colors.YELLOW}[WARN] Path not found: {path}{Colors.RESET}")
                continue
            
            if self.args.album:
                if path.is_dir():
                    queue.append(('album', path))
                else:
                    print(f"{Colors.YELLOW}[WARN] Album mode ignores file: {path.name}{Colors.RESET}")
            else:
                if path.is_dir():
                    # Expand directory for single mode
                    files = sorted([f for f in path.iterdir() if f.is_file() and f.suffix.lower() in ['.mp3', '.flac', '.m4a', '.ogg']])
                    for f in files: queue.append(('single', f))
                else:
                    queue.append(('single', path))

        if not queue:
            print("No valid targets found.")
            return

        # FIX: Prevent -s usage with batch (multiple files)
        if self.args.search and len(queue) > 1:
            print(f"{Colors.RED}[ERROR] Manual search (-s) cannot be used with batch processing (multiple files).{Colors.RESET}")
            print(f"You have selected {len(queue)} targets. Applying the same metadata to all would be destructive.")
            print("Please target a single file or remove the '-s' argument.")
            return

        for mode, path in queue:
            if mode == 'album':
                self.run_album_mode(path, fs_opts)
            elif mode == 'single':
                self.run_single_mode(path, fs_opts)

    def run_single_mode(self, filepath: Path, fs_opts: List[str]):
        self.logger.info(f"Processing: {filepath.name}")
        query = {}
        
        # 0. Pre-fill from existing tags
        existing = self.tagger.read_existing_metadata(filepath)
        query.update(existing)

        # Fingerprint Check
        if self.args.chromaprint:
            fp_data = self.meta_provider.get_acoustic_fingerprint(filepath)
            if fp_data:
                print(f"{Colors.GREEN}Fingerprint matched:{Colors.RESET} {fp_data['title']} - {fp_data['artist']}")
                query['name'] = fp_data['title']
                query['artist'] = fp_data['artist']
                query['album'] = fp_data.get('album')
                query['mb_id'] = True # Marker
            else:
                print(f"{Colors.RED}Fingerprint match failed.{Colors.RESET}")

        if self.args.search: query.update(self.parse_kv(self.args.search))
        if 'infer-dirs' in fs_opts and not query.get('artist'): query.update(self.file_handler.infer_dirs(filepath))
        
        # Ensure we have a name/title to search with
        if not query.get('name') and not query.get('title'): 
             query['name'] = re.sub(r"\[.*?\]|^\d+\s*-\s*", "", filepath.stem).strip()
        elif query.get('title') and not query.get('name'):
             query['name'] = query['title']

        lyr_src = self.config.get("defaults.lyrics.source", "interactive")
        meta = self.meta_provider.fetch_song_data(query, source_mode=lyr_src)
        
        self._process_and_apply(filepath, meta, fs_opts)

    def manual_match_interface(self, files, tracks):
        matched = []
        available_tracks = list(tracks)
        print("\n=== Manual Matching Required ===")
        for f in files:
            print(f"\nFile: {f.name}")
            print("Available Tracks:")
            for i, t in enumerate(available_tracks): print(f"  [{i+1}] {t['number']}. {t['title']} ({t['artist']})")
            while True:
                choice = input(f"Select track # for '{f.name}' (or 's' to skip): ").strip().lower()
                if choice == 's': break
                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(available_tracks):
                        selected = available_tracks.pop(idx)
                        matched.append((f, selected))
                        break
        return matched

    def run_album_mode(self, directory: Path, fs_opts: List[str]):
        query = {}
        if self.args.search: query = self.parse_kv(self.args.search)
        if 'infer-dirs' in fs_opts: query.update(self.file_handler.infer_dirs(directory))
        
        if not query.get('artist') or not query.get('album'):
            if 'infer-dirs' not in fs_opts: return self.logger.warn("Use -f infer-dirs or -s artist=.. album=..")
            else: return self.logger.warn(f"Could not infer artist/album from {directory}")

        search_q = f"{query.get('artist')} {query.get('album')}"
        print(f"\n    Searching for album: '{search_q}'...")
        
        candidates = self.meta_provider.search_album_candidates(search_q)
        if not candidates: return print("    No matching albums found on Genius.")

        selected_id = None
        if len(candidates) == 1:
            c = candidates[0]
            print(f"\n    Found 1 match: {c['title']} by {c['artist']}")
            choice = input("    Is this correct? [Y/n] ").lower().strip()
            if choice in ['', 'y', 'yes']: selected_id = c['id']
            else: return
        else:
            print(f"\n    Found {len(candidates)} matches:")
            for i, c in enumerate(candidates, 1): print(f"    [{i}] {c['title']} - {c['artist']}")
            choice = input(f"\n    Select an album (1-{len(candidates)}, n to abort): ").lower().strip()
            if choice.isdigit() and 1 <= int(choice) <= len(candidates): selected_id = candidates[int(choice)-1]['id']
            else: return

        selected_title = "Unknown"
        for c in candidates:
             if c['id'] == selected_id: selected_title = f"{c['title']} by {c['artist']}"; break
        print("") 
        
        album_meta = self.meta_provider.fetch_album_by_id(selected_id)
        if not album_meta.get('tracks'): return self.logger.error("No tracks found.")

        local_files = sorted([f for f in directory.iterdir() if f.suffix.lower() in ['.mp3', '.flac', '.m4a', '.ogg']])
        matched_pairs = []
        
        if len(local_files) == len(album_meta['tracks']) and not 'match-filename' in fs_opts:
            for i, f in enumerate(local_files): matched_pairs.append((f, album_meta['tracks'][i]))
        else:
            for f in local_files:
                clean_name = re.sub(r"^\d+\s*[-.]?\s*", "", f.stem)
                best_track = None; best_score = 0
                for t in album_meta['tracks']:
                    score = fuzz.token_sort_ratio(clean_name, t['title'])
                    if score > best_score: best_score = score; best_track = t
                if best_score > 60: matched_pairs.append((f, best_track))

        matched_files_set = {f for f, t in matched_pairs}
        unmatched_files = [f for f in local_files if f not in matched_files_set]
        matched_track_ids = {t['id'] for f, t in matched_pairs}
        available_tracks = [t for t in album_meta['tracks'] if t['id'] not in matched_track_ids]
        
        if unmatched_files and available_tracks:
            matched_pairs.extend(self.manual_match_interface(unmatched_files, available_tracks))
            print("") 

        matched_track_ids = {t['id'] for f, t in matched_pairs}
        missing_tracks = [t for t in album_meta['tracks'] if t['id'] not in matched_track_ids]
        if missing_tracks:
            print(f"\n{Colors.RED}{Colors.BOLD}=== MISSING TRACKS DETECTED ==={Colors.RESET}")
            for t in missing_tracks: print(f"{Colors.RED}  {t['number']}. {t['title']}{Colors.RESET}")
            missing_file = directory / "missing.txt"
            with open(missing_file, "w") as f:
                f.write(f"Missing tracks for album: {album_meta['album']}\n\n")
                for t in missing_tracks: f.write(f"{t['number']}. {t['title']} - {t['artist']}\n")
            print(f"{Colors.BOLD}Created list at: {missing_file}{Colors.RESET}\n")

        cover_path = None; cover_data = None
        if self.args.cover_art == "auto" and album_meta.get("cover_url"):
            try:
                r = requests.get(album_meta["cover_url"])
                cover_path = "/tmp/swisstag_cover.jpg"
                cover_data = r.content 
                with open(cover_path, 'wb') as f: f.write(r.content)
            except Exception: pass
        elif self.args.cover_art and self.args.cover_art.startswith("file="):
            cover_path = self.args.cover_art.split("=", 1)[1]
            try:
                with open(cover_path, 'rb') as f: cover_data = f.read()
            except: pass

        inferred_artist = query.get('artist') if 'infer-dirs' in fs_opts else None
        ui = TreeUI(len(matched_pairs), album_name=selected_title)
        lyr_src = self.config.get("defaults.lyrics.source", "interactive")

        for filepath, track in matched_pairs:
            ui.next(f"{filepath.name}")
            genius_track_artist = track['artist']
            genius_album_artist = album_meta['artist']
            t_artist_list = []; a_artist_list = []
            if inferred_artist: t_artist_list.append(inferred_artist); a_artist_list.append(inferred_artist)
            if genius_track_artist and genius_track_artist != inferred_artist: t_artist_list.append(genius_track_artist)
            if genius_album_artist and genius_album_artist != inferred_artist: a_artist_list.append(genius_album_artist)
            if not t_artist_list: t_artist_list = ["Unknown"]
            if not a_artist_list: a_artist_list = ["Unknown"]

            file_meta = {
                "title": track['title'], "track_number": track['number'],
                "artist": t_artist_list, "album": album_meta['album'],
                "album_artist": a_artist_list, "year": album_meta['year'], "genre": album_meta['genre']
            }
            
            ui.step("Fetching lyrics...")
            lyrics = self.meta_provider.fetch_lyrics_for_track(track['id'], title=track['title'], artist=track['artist'], source_mode=lyr_src)
            has_lyrics = bool(lyrics)
            if lyrics: file_meta['lyrics'] = lyrics
            
            ui.step("Applying tags...")
            audio = self.tagger.load_file(filepath)
            
            # Duration Validation
            warnings = []
            if audio:
                local_dur = audio.info.length
                # We assume user manually verifies duration for now or we could fetch it from MB if available
                # but since we mostly have Genius data here, we might skip precise check unless we had -p data
                pass

            write_success = False
            if audio:
                manual = self.parse_kv(self.args.manual_tags)
                self.tagger.apply_metadata(audio, file_meta, manual)
                if cover_path: self.tagger.apply_cover(audio, cover_path)
                if cover_data: self.tagger.save_cover(filepath, cover_data, album_meta['album'])
                write_success = True
            
            if 'rename' in fs_opts or self.config.get("defaults.rename"):
                ui.step("Renaming...")
                filepath = self.file_handler.rename_file(filepath, file_meta)
            
            if 'autosort' in fs_opts:
                ui.step("Sorting...")
                filepath = self.file_handler.autosort(filepath, file_meta)
            
            if self.args.lyrics and file_meta.get('lyrics'):
                l_mode = self.config.get("defaults.lyrics.mode")
                if l_mode in ['lrc', 'both']: self.file_handler.save_lrc(filepath, file_meta['lyrics'])
            
            status = 'success'
            if not write_success: status = 'error'
            elif self.args.lyrics and not has_lyrics: 
                status = 'warning'
                warnings.append("Missing lyrics")

            ui.finish(status, warnings)

    def _process_and_apply(self, filepath, meta, fs_opts):
        audio = self.tagger.load_file(filepath)
        success = True
        has_lyrics = False
        warnings = []

        if audio:
            manual = self.parse_kv(self.args.manual_tags)
            self.tagger.apply_metadata(audio, meta, manual)
            cover_data = None
            if self.args.cover_art == "auto" and meta.get("cover_url"):
                 try:
                    r = requests.get(meta["cover_url"])
                    tmp_art = Path("/tmp/swisstag_cover.jpg")
                    cover_data = r.content
                    with open(tmp_art, 'wb') as f: f.write(r.content)
                    self.tagger.apply_cover(audio, str(tmp_art))
                 except Exception: pass

            if cover_data: self.tagger.save_cover(filepath, cover_data, meta.get("album", "Unknown Album"))

            if 'rename' in fs_opts or self.config.get("defaults.rename"):
                filepath = self.file_handler.rename_file(filepath, meta)
            
            if 'autosort' in fs_opts:
                filepath = self.file_handler.autosort(filepath, meta)
            
            if meta.get('lyrics'):
                has_lyrics = True
                l_mode = self.config.get("defaults.lyrics.mode")
                if l_mode in ['lrc', 'both']: self.file_handler.save_lrc(filepath, meta['lyrics'])
        else:
            success = False

        if success:
            if self.args.lyrics and not has_lyrics:
                print(f"{Colors.YELLOW}[!] Finished {filepath.name} (Missing Lyrics){Colors.RESET}")
            else:
                print(f"{Colors.GREEN}[✓] Finished {filepath.name}{Colors.RESET}")
            
            # Duration Validation Check for Single File
            if audio and meta.get('duration'): # If we got duration from chromaprint
                 local_dur = audio.info.length
                 if abs(local_dur - int(meta['duration'])) > 10:
                     print(f"    {Colors.YELLOW}↳ Duration mismatch: Local {int(local_dur)}s vs Remote {meta['duration']}s{Colors.RESET}")

        else:
            print(f"{Colors.RED}[✗] Failed {filepath.name}{Colors.RESET}")

if __name__ == "__main__":
    try:
        app = SwissTag()
        app.run()
    except KeyboardInterrupt:
        print(f"\n{Colors.RED}Aborted by user.{Colors.RESET}")
        sys.exit(130)
