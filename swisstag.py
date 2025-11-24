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
VERSION = "5.2"
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
    
    Fetches the full tracklist from Genius first, then matches local files to the 
    official tracklist using fuzzy matching. Recommended for processing directories
    containing whole albums.
    """,
    "lyrics": """
    --lyrics, -l
    Fetch and save song lyrics.
    
    Modes:
        embed : (Default) Embeds lyrics directly into the audio file (USLT/LYRICS tag).
        lrc   : Saves lyrics as a separate .lrc file alongside the audio.
        both  : Performs both embed and lrc actions.
        skip  : Explicitly disable lyrics fetching.
    """,
    "lyrics-source": """
    --lyrics-source, -L
    Control where lyrics are fetched from.
    
    Options:
        interactive : (Default) Always ask the user to select the source for every song.
        auto        : Genius first, then synced sources. If fail, ask user.
        synced      : Force using syncedlyrics (LRC/Time-synced) sources only.
        genius      : Force using Genius only.
    """,
    "fingerprint": """
    --chromaprint, -p
    Use acoustic fingerprinting (AcoustID) to identify files.
    
    Requires 'fpcalc' (chromaprint) to be installed.
    Useful for identifying files with bad filenames or missing tags.
    """,
    "search": """
    --search, -s
    Provide manual search criteria (name, artist, url) to bypass local tag reading 
    or directory inference. This is useful for single files with poor original tags 
    or names.
    
    Format: -s KEY=VALUE
    Example: --search artist="Kanye West" name="Yeezus"
    """,
    "set": """
    --set, -S
    Temporarily override configuration values for the current session without saving 
    to the config file.
    
    Format: -S path.to.key=value
    Example: -S defaults.rename=true
    """,
    "manual-tags": """
    --manual-tags, -t
    Manually override specific tags before applying them to the files.
    These values take precedence over fetched metadata.
    
    Format: -t TAG=VALUE
    Supported Tags: title, artist, album, year, genre, track_number
    
    Example: -t title="New Title" genre="Rock"
    """,
    "feat-handling": """
    --feat-handling, -F
    Controls how "feat." artists in song titles are handled during processing.
    
    Modes:
        keep-title  : (Default) Moves artist to Artist tag, but leaves title as-is.
        keep-artist : Removes artist from Title tag, but does NOT move it to Artist tag.
        keep-both   : Warns if features detected, but modifies neither tags.
        split       : Moves artist to Artist tag, adds "!" prefix to title (custom marker).
        split-clean : Moves artist to Artist tag, removes from title entirely.
    """,
    "filesystem": """
    --filesystem, -f
    Comma-separated list of filesystem actions to perform.
    
    Options:
        rename         : Renames files based on metadata (e.g., "Title.mp3").
        match-filename : Matches tracks by filename similarity (Album Mode).
        infer-dirs     : Infers Artist/Album from parent directory names.
        autosort       : Moves files into "Artist/Album/" directory structure.
    """,
    "cover-art": """
    --cover-art, -c
    Manage cover art fetching and embedding.
    
    Modes:
        auto           : Fetches cover art from Genius if available.
        file=/path.jpg : Uses a specific local image file.
        extract        : Extracts embedded cover art from the file (if present).
    """,
    "config": """
    --config, -C
    Manage configuration settings directly from the command line.
    
    Actions:
        get path.to.key       : Print the current value of a config key.
        set path.to.key value : Set a config key to a specific value permanently.
    
    Example: swisstag -C set defaults.rename true
    """,
    "debug": """
    --debug, -d
    Enable debug logging modes to troubleshoot issues.
    
    Modes (comma-separated):
        dry     : Dry run (no changes applied).
        network : Log network requests.
        cmd     : Log shell commands.
        vars    : Log variable states.
        config  : Log config loading.
        all     : Enable all debug modes.
    """,
    "install-deps": """
    --install-deps
    Automatically installs required Python dependencies via pip.
    Useful for first-time setup.
    """,
    "setup-token": """
    --setup-token
    Runs the interactive wizard to set up the Genius API token.
    Opens a browser window to generate a token and saves it to config.
    """
}

HELP_MAP = {
    "-a": "album", "--album": "album",
    "-l": "lyrics", "--lyrics": "lyrics",
    "-L": "lyrics-source", "--lyrics-source": "lyrics-source",
    "-p": "fingerprint", "--chromaprint": "fingerprint",
    "-s": "search", "--search": "search",
    "-S": "set", "--set": "set",
    "-t": "manual-tags", "--manual-tags": "manual-tags",
    "-F": "feat-handling", "--feat-handling": "feat-handling",
    "-f": "filesystem", "--filesystem": "filesystem",
    "-c": "cover-art", "--cover-art": "cover-art",
    "-C": "config", "--config": "config",
    "-d": "debug", "--debug": "debug",
    "--install-deps": "install-deps",
    "--setup-token": "setup-token"
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
        "feat_handling": "keep-title",
        "lyrics": {"fetch": True, "mode": "embed", "source": "interactive"},
        "cover": {"size": "1920x1920", "keep_resized": True, "extract": {"crop": False, "scale": True}}
    },
    "separators": {"artist": "; ", "genre": "; "},
    "regex": {"featured_artist": r"(?i)[(\[](?:feat|ft|featuring|with)\.?\s+(.*?)[)\]]"},
    "artist_groups": {},
    "aliases": {},
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

    def _get_sub_indent(self):
        # Calculate indentation for children messages based on current prefix
        if "├" in self.prefix:
            return self.root_indent + "│   "
        return self.root_indent + "    "

    def step(self, msg):
        indent = self._get_sub_indent()
        sys.stdout.write(f"{Colors.CLR}{indent}└── {msg}")
        sys.stdout.flush()

    def message(self, text, color=None):
        # Permanent message that respects tree structure
        indent = self._get_sub_indent()
        c = color if color else ""
        r = Colors.RESET if color else ""
        sys.stdout.write(f"{Colors.CLR}") # Clear any pending step
        print(f"{indent}│ {c}{text}{r}")

    def ask(self, prompt_text):
        # Input prompt that respects tree structure
        indent = self._get_sub_indent()
        sys.stdout.write(f"{Colors.CLR}") # Clear any pending step
        return input(f"{indent}│ {prompt_text}")

    def finish(self, status: str = 'success', warnings: List[str] = None):
        indent = self._get_sub_indent()
        
        icon = f"{Colors.GREEN}[✓]{Colors.RESET}"
        
        extra_msg = "Done"
        if status == 'warning': 
            icon = f"{Colors.YELLOW}[!]{Colors.RESET}"
            extra_msg = "Attention Required"
        elif status == 'error': 
            icon = f"{Colors.RED}[✗]{Colors.RESET}"
            extra_msg = "Failed"

        sys.stdout.write(f"{Colors.CLR}") # Clear any pending step
        print(f"{indent}└── {icon} {extra_msg}")
        
        if warnings:
            for w in warnings:
                print(f"{indent}    {Colors.YELLOW}↳ {w}{Colors.RESET}")

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

    def interactive_lyrics_picker(self, title, artist, ui: Optional['TreeUI'] = None) -> Optional[str]:
        """
        Interactive Wizard to choose lyrics source with Retry Loop.
        If ui is provided, uses tree-aligned output.
        """
        def _print(msg, color=None):
            if ui: ui.message(msg, color)
            else: print(f"{color if color else ''}{msg}{Colors.RESET if color else ''}")

        def _input(msg):
            if ui: return ui.ask(msg)
            return input(msg)

        # Clear the previous line to make the UI cleaner if called in a loop
        if not ui: print(f"\n{Colors.BLUE}--- Lyrics Selection: {title} ---{Colors.RESET}")
        else: _print(f"Lyrics Selection: {title}", Colors.BLUE)
        
        while True:
            _print("[s] Skip lyrics for this song")
            _print("[1] Genius")
            _print("[2] SyncedLyrics (LRC)")
            _print("[3] Manual Input")
            
            mode = _input("Select source: ").strip().lower()
            
            if mode == 's': return None
            
            lyrics = None
            
            # 1. GENIUS SEARCH
            if mode == '1':
                hits_raw = self._genius_search_hits(title, artist)
                if not hits_raw or 'hits' not in hits_raw:
                    _print("No Genius results found.", Colors.YELLOW)
                else:
                    hits = hits_raw['hits']
                    for i, hit in enumerate(hits):
                        res = hit['result']
                        _print(f"[{i+1}] {res['full_title']}")
                    _print("[b] Back")
                    
                    sub = _input("Select song (1-5): ").strip().lower()
                    if sub != 'b':
                        if sub.isdigit():
                            idx = int(sub) - 1
                            if 0 <= idx < len(hits):
                                sel_id = hits[idx]['result']['id']
                                s = self._genius_get_song(sel_id)
                                if s: 
                                    if isinstance(s, dict) and 'song' in s: s = s['song']
                                    lyrics = get_attr(s, 'lyrics')
                        else:
                             _print("Invalid selection.")

            # 2. SYNCEDLYRICS SEARCH
            elif mode == '2':
                _print("Searching providers...")
                lyrics = self.get_synced_lyrics(title, artist)
                if not lyrics:
                    _print("No synced lyrics found.", Colors.YELLOW)
                else:
                    _print(f"Found lyrics ({len(lyrics)} chars).")

            # 3. MANUAL INPUT
            elif mode == '3':
                _print("Paste lyrics below (Press Ctrl+D or Ctrl+Z on new line to finish):")
                try:
                    lines = sys.stdin.read()
                    lyrics = lines if lines.strip() else None
                except: pass

            else:
                _print("Invalid option.")

            if lyrics:
                return lyrics
            
            # Retry Prompt
            _print("Search failed or cancelled.", Colors.YELLOW)
            retry = _input("Do you want to retry searching for lyrics? [Y/n]: ").strip().lower()
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

    def fetch_lyrics_for_track(self, track_id, title=None, artist=None, source_mode="auto", ui: Optional['TreeUI'] = None) -> str:
        # 1. INTERACTIVE MODE (Explicitly requested OR Default)
        if source_mode == "interactive":
            return self.interactive_lyrics_picker(title, artist, ui=ui)
        
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
             # Use UI if available, else standard print
             msg = f"Auto-fetch failed for: {title}"
             # We pass UI object to be used if available
             if ui: ui.message(msg, Colors.YELLOW)
             else: print(f"\n{Colors.YELLOW}[!] {msg}{Colors.RESET}")
             
             # Ask user if they want to manually intervene
             if ui: choice = ui.ask("Select source manually? [y/N]: ").strip().lower()
             else: choice = input("    Select source manually? [y/N]: ").strip().lower()
             
             if choice == 'y':
                 lyrics = self.interactive_lyrics_picker(title, artist, ui=ui)

        if lyrics:
            self.logger.log("vars", f"Lyrics len: {len(lyrics)}")
        else:
            self.logger.log("vars", "No lyrics found.")
        return lyrics

    def fetch_song_data(self, query: Dict, source_mode="auto") -> Dict:
        data = {"title": query.get("name"), "artist": query.get("artist")}
        
        # --- LOGIC: Always perform full metadata fetch for single file mode ---
        
        # 1. Genius Metadata Fetch
        if self.genius and data['title'] and data['artist']:
            hits = self._genius_search_hits(data["title"], data["artist"])
            best_hit = None
            best_score = 0
            if hits and isinstance(hits, dict) and 'hits' in hits:
                for hit in hits['hits']:
                    res = hit['result']
                    # Calculate score based on fuzzy title match
                    score = fuzz.token_sort_ratio(res['title'], data['title'])
                    if score > best_score:
                        best_score = score; best_hit = res
            
            if best_hit and best_score >= 70:
                song = self._genius_get_song(best_hit['id'])
                if isinstance(song, dict) and 'song' in song: song = song['song']
                
                # Update all tags based on the best Genius result
                data["title"] = get_attr(song, 'title')
                data["artist"] = get_attr(song, 'artist_names')
                if get_attr(song, 'song_art_image_url'):
                    data["cover_url"] = get_attr(song, 'song_art_image_url')
                alb = get_attr(song, 'album')
                if alb: 
                    data["album"] = get_attr(alb, 'name')
                
                # Fetch Lyrics (Only if required by user's config/cli options)
                if self.config.get("defaults.lyrics.fetch", True):
                    data["lyrics"] = self.fetch_lyrics_for_track(
                        best_hit['id'], 
                        title=data["title"], 
                        artist=data["artist"], 
                        source_mode=source_mode
                    )

        # 2. Fallback/Rescue for Lyrics if they are still missing and explicitly requested
        if self.config.get("defaults.lyrics.fetch", True) and not data.get('lyrics') and data['title'] and data['artist']:
             if source_mode == "auto":
                 # Try SyncedLyrics as final auto fallback
                 data["lyrics"] = self.get_synced_lyrics(data['title'], data['artist'])
                 
                 # Rescue for Single Mode if auto failed and we are in a terminal
                 if not data["lyrics"] and sys.stdin.isatty():
                     # No UI here as this is run inside UI loop
                     pass

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

    def apply_aliases(self, meta: Dict):
        aliases = self.config.get("aliases", {})
        if not aliases: return
        # Normalize keys to lower case for matching
        alias_map = {k.lower(): v for k, v in aliases.items()}
        
        target_keys = ["artist", "album_artist"]
        for key in target_keys:
            if key not in meta or not meta[key]: continue
            
            # Ensure list
            current = meta[key]
            if isinstance(current, str): current = [current]
            
            new_list = []
            has_changes = False
            
            for art in current:
                # Check match
                found = alias_map.get(art.strip().lower())
                if found:
                    has_changes = True
                    if isinstance(found, list): new_list.extend(found)
                    else: new_list.append(str(found))
                else:
                    new_list.append(art)
            
            if has_changes:
                # Dedup preserving order
                seen = set()
                final = []
                for x in new_list:
                    if x not in seen:
                        final.append(x)
                        seen.add(x)
                meta[key] = final

    def handle_features(self, meta: Dict, ui: Optional['TreeUI'] = None):
        mode = self.config.get("defaults.feat_handling")
        
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
        if "artist" in meta and meta["artist"] is None:
            meta["artist"] = []
        elif "artist" in meta and isinstance(meta["artist"], str): 
            meta["artist"] = [meta["artist"]]
        # Ensure it's always a list (or empty list if absent/None)
        if "artist" not in meta:
            meta["artist"] = []
        elif not isinstance(meta["artist"], list):
            meta["artist"] = [str(meta["artist"])]

        # Check Title for Features
        if meta.get("title"):
            feats, clean_title = extract_from_string(meta["title"])
            if feats:
                if mode == "keep-both":
                    # Notify
                    msg = f"Features detected in title: '{meta['title']}' -> {feats} (Use -F to fix)"
                    if ui: ui.message(msg, color=Colors.YELLOW)
                    else: print(f"    {Colors.YELLOW}[!] {msg}{Colors.RESET}")
                else:
                    # Update Artist List?
                    if mode in ["split", "split-clean", "keep-title"]:
                        for f in feats:
                            if f not in meta["artist"]: meta["artist"].append(f)
                    
                    # Update Title?
                    if mode == "split":
                        meta["title"] = f"! {clean_title}"
                    elif mode in ["split-clean", "keep-artist"]:
                        meta["title"] = clean_title
                    # keep-title leaves title alone

        if mode == "keep-both": return

        current_artists = list(meta["artist"])
        new_artist_list = []
        for art in current_artists:
            feats, clean_art = extract_from_string(art)
            if clean_art not in new_artist_list: new_artist_list.append(clean_art)
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

    def apply_metadata(self, audio, meta: Dict, manual: Dict = None, ui: Optional['TreeUI'] = None):
        if manual: meta.update(manual)
        
        # Apply Aliases/Replacements FIRST
        self.apply_aliases(meta)
        
        self.handle_features(meta, ui)
        self.expand_artist_groups(meta)
        if self.logger.is_dry: return

        # Ensure artist fields are joined into strings before writing to file tags
        artist_str = self._join_artists(meta.get("artist")) if meta.get("artist") else None
        album_artist_str = self._join_artists(meta.get("album_artist")) if meta.get("album_artist") else None

        if isinstance(audio, MP3): self._tag_id3(audio, meta, artist_str, album_artist_str)
        elif isinstance(audio, FLAC): self._tag_vorbis(audio, meta, artist_str, album_artist_str)
        elif isinstance(audio, MP4): self._tag_mp4(audio, meta, artist_str, album_artist_str)

        # Do NOT call audio.save() here. It's handled in the main processing loop.


    def _should_embed(self): return self.config.get("defaults.lyrics.mode") in ['embed', 'both']

    def _tag_id3(self, audio, meta, artist_str, album_artist_str):
        if audio.tags is None: audio.add_tags()
        if meta.get('title'): audio.tags.add(TIT2(encoding=3, text=meta['title']))
        if meta.get('album'): audio.tags.add(TALB(encoding=3, text=meta['album']))
        if meta.get('year'): audio.tags.add(TDRC(encoding=3, text=str(meta['year'])))
        if meta.get('genre'): audio.tags.add(TCON(encoding=3, text=meta['genre']))
        if meta.get('track_number'): audio.tags.add(TRCK(encoding=3, text=str(meta['track_number'])))
        if meta.get('lyrics') and self._should_embed():
            # Remove any existing USLT tags before adding a new one
            audio.tags.delall('USLT')
            audio.tags.add(USLT(encoding=3, lang='eng', desc='desc', text=meta['lyrics']))
            
        if artist_str: audio.tags.add(TPE1(encoding=3, text=artist_str))
        if album_artist_str: audio.tags.add(TPE2(encoding=3, text=album_artist_str))

    def _tag_vorbis(self, audio, meta, artist_str, album_artist_str):
        if meta.get('title'): audio['title'] = meta['title']
        if meta.get('album'): audio['album'] = meta['album']
        if meta.get('year'): audio['date'] = str(meta['year'])
        if meta.get('genre'): audio['genre'] = meta['genre']
        if meta.get('track_number'): audio['tracknumber'] = str(meta['track_number'])
        if meta.get('lyrics') and self._should_embed(): audio['lyrics'] = meta['lyrics']
        if artist_str: audio['artist'] = artist_str
        if album_artist_str: audio['albumartist'] = album_artist_str

    def _tag_mp4(self, audio, meta, artist_str, album_artist_str):
        if meta.get('title'): audio['\xa9nam'] = meta['title']
        if meta.get('album'): audio['\xa9alb'] = meta['album']
        if meta.get('year'): audio['\xa9day'] = str(meta['year'])
        if meta.get('genre'): audio['\xa9gen'] = meta['genre']
        if meta.get('lyrics') and self._should_embed(): audio['\xa9lyr'] = meta['lyrics']
        if artist_str: audio['\xa9ART'] = artist_str
        if album_artist_str: audio['aART'] = album_artist_str

    def apply_cover(self, audio, image_path: str):
        if self.logger.is_dry or not image_path: return
        try:
            with open(image_path, 'rb') as f: img_data = f.read()
            
            # Clear existing cover art tags before adding a new one
            if isinstance(audio, MP3): 
                 audio.tags.delall('APIC')
                 audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img_data))
            elif isinstance(audio, FLAC):
                # FLAC/Vorbis handles pictures differently (clearing is manual)
                audio.pictures = []
                p = Picture(); p.type = 3; p.mime = "image/jpeg"; p.desc = "Cover"; p.data = img_data
                audio.add_picture(p)
            elif isinstance(audio, MP4): 
                audio['covr'] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]
            
            # Do NOT call audio.save() here. It's handled in the main processing loop.
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
        
        # Determine if help flags are present in sys.argv
        help_present = any(h in sys.argv for h in ["-h", "--help"])
        
        if help_present:
            # 1. Filter out help flags for non-destructive parsing
            # This allows us to check for other flags without triggering help-exit or parsing errors
            filtered_argv = [arg for arg in sys.argv if arg not in ["-h", "--help"]]
            
            # 2. Handle Help
            # We pass the filtered list (excluding script name) to analyze which flags were present
            self.check_extended_help(filtered_argv[1:])
            # Exits inside check_extended_help, but ensure it exits cleanly if needed.
            sys.exit(0) 
            
        # --- Normal Execution Path (No Help Requested) ---
            
        # 1. Actual Parsing: If we reached here, no help was requested.
        try:
             self.args = self._create_parser(add_help=True).parse_args()
        except SystemExit:
             # If argparse catches an error (like missing required arguments), it raises SystemExit.
             # We should rely on argparse to print the error message and exit cleanly.
             sys.exit(2)
        
        # --- Initialization continues if no SystemExit occurred ---
        self.config = ConfigManager(self.args.temp_set)
        
        # 2. Apply CLI overrides for mode/source
        if self.args.lyrics: self.config.set("defaults.lyrics.mode", self.args.lyrics)
        if self.args.lyrics_source: self.config.set("defaults.lyrics.source", self.args.lyrics_source)
        if self.args.feat_handling: self.config.set("defaults.feat_handling", self.args.feat_handling)
        
        # 3. Control Lyrics Fetching Flag
        if self.args.lyrics == "skip":
             self.config.set("defaults.lyrics.fetch", False)
        else:
             self.config.set("defaults.lyrics.fetch", True)


        if self.args.setup_token: TokenWizard.run(self.config); sys.exit(0)
        if self.args.config_action: self.handle_config_action(); sys.exit(0)
        
        self.logger = Logger(self.args.debug)
        self.meta_provider = MetadataProvider(self.config, self.logger)
        self.meta_provider.args = self.args
        self.file_handler = FileHandler(self.config, self.logger)
        self.tagger = Tagger(self.config, self.logger)

    def check_extended_help(self, filtered_args):
        # This function is now only called if -h or --help is detected in sys.argv.
        
        found_topics = []
        for arg in filtered_args:
            clean_arg = arg.split('=')[0]
            if clean_arg in HELP_MAP: 
                topic = HELP_MAP[clean_arg]
                if topic not in found_topics:
                    found_topics.append(topic)
        
        if found_topics:
            # DETAILED HELP MODE
            print(f"\n{APP_NAME} v{VERSION} - Detailed Help\n")
            for topic in found_topics:
                print(DETAILED_HELP_TEXT[topic])
            sys.exit(0) # Explicit exit
        else:
            # GENERAL HELP MODE (No specific topic requested)
            # Create a parser with add_help=True to display the standard help message
            parser = self._create_parser(add_help=True)
            parser.print_help()
            sys.exit(0) # Explicit exit

    def _create_parser(self, add_help=False):
        # Helper function to create the ArgumentParser instance
        parser = argparse.ArgumentParser(description="Swisstag: Automated Music Tagger", add_help=add_help)
        parser.add_argument("inputs", nargs="*", default=["."], help="Files or directories")
        parser.add_argument("--install-deps", action="store_true", help="Install dependencies")
        parser.add_argument("--setup-token", action="store_true", help="Setup Genius Token")
        parser.add_argument("-a", "--album", action="store_true", help="Album Mode")
        parser.add_argument("-s", "--search", nargs="+", help="Manual search")
        parser.add_argument("-t", "--manual-tags", nargs="+", help="Override tags")
        parser.add_argument("-F", "--feat-handling", choices=['split', 'split-clean', 'keep-title', 'keep-artist', 'keep-both'], default="keep-title")
        parser.add_argument("-f", "--filesystem", help="rename, match-filename, infer-dirs, autosort")
        parser.add_argument("-c", "--cover-art", help="auto, file=path, extract")
        parser.add_argument("-l", "--lyrics", help="embed, lrc, both, skip")
        parser.add_argument("-L", "--lyrics-source", choices=['auto', 'interactive', 'synced', 'genius'], help="auto, interactive, synced, genius")
        parser.add_argument("-p", "--chromaprint", action="store_true", help="Use acoustic fingerprinting")
        parser.add_argument("-d", "--debug", nargs="?", const="dry", help="dry, network, cmd, vars, all")
        parser.add_argument("-C", "--config", nargs="+", dest="config_action", help="config ops")
        parser.add_argument("-S", "--set", dest="temp_set", nargs="+", help="temp config")
        parser.add_argument("--about", action="store_true")
        parser.add_argument("-v", "--version", action="store_true")
        
        # Manually add the help flag if add_help=False so we can detect it in sys.argv
        if not add_help:
             parser.add_argument("-h", "--help", action='store_const', const=True, help=argparse.SUPPRESS)
             
        return parser

    def parse_args(self, add_help=False):
        # Deprecated: use _create_parser instead
        return self._create_parser(add_help).parse_args()


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
        for item in items:
            # 1. Split the argument by commas to handle cases like -s tag1=val1,tag2=val2
            sub_items = [s.strip() for s in item.split(',')]
            for sub_item in sub_items:
                if "=" in sub_item: 
                    k, v = sub_item.split("=", 1)
                    # Remove quotes if present
                    if v.startswith('"') and v.endswith('"'): v = v[1:-1]
                    elif v.startswith("'") and v.endswith("'"): v = v[1:-1]
                    res[k] = v
        return res

    def run(self):
        if self.args.about:
            print(f"{APP_NAME} v{VERSION} - advanced mass retagging utility designed by doromiert and coded by gemini")
            return
        if self.args.version: 
            print(f"{APP_NAME} v{VERSION}")
            return
        
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

    def _clean_filename_for_search(self, filename: str) -> str:
        """Removes common noise from filenames before using them as search queries."""
        # 1. Remove Bracketed/Parenthesized content that is not explicitly feature (which is handled later)
        cleaned = re.sub(r'\s*[\(\[].*?(Official Music Video|Lyrics|Audio|Explicit|Remix|Live|\d+kbps).*?[\)\]]', '', filename, flags=re.IGNORECASE)
        # Remove any lingering brackets/IDs
        cleaned = re.sub(r'\s*[\(\[].*?[\)\]]', '', cleaned)
        # Remove leading numbers/hyphens (track numbers)
        cleaned = re.sub(r'^\s*\d+\s*[-.]?\s*', '', cleaned).strip()
        
        return cleaned

    def run_single_mode(self, filepath: Path, fs_opts: List[str]):
        """
        Processes a single file, using the TreeUI structure for cleaner, album-mode-like output.
        """
        # Setup TreeUI for a single item
        ui = TreeUI(total=1)
        ui.next(f"{filepath.name}")

        query = {}
        # 0. Pre-fill from existing tags
        existing = self.tagger.read_existing_metadata(filepath)
        query.update(existing)

        # 1. Fingerprint Check
        if self.args.chromaprint:
            ui.step("Checking acoustic fingerprint...")
            fp_data = self.meta_provider.get_acoustic_fingerprint(filepath)
            if fp_data:
                ui.message(f"Fingerprint matched: {fp_data['title']} - {fp_data['artist']}", Colors.GREEN)
                query['name'] = fp_data['title']
                query['artist'] = fp_data['artist']
                query['album'] = fp_data.get('album')
                query['duration'] = fp_data.get('duration')
                query['mb_id'] = True
            else:
                ui.message("Fingerprint match failed.", Colors.RED)

        if self.args.search: query.update(self.parse_kv(self.args.search))
        if 'infer-dirs' in fs_opts and not query.get('artist'): 
            inferred = self.file_handler.infer_dirs(filepath)
            query.update(inferred)
            if inferred: ui.message(f"Inferred Artist/Album: {inferred.get('artist')} / {inferred.get('album')}")
        
        # Determine the CLEAN search name (Priority: Existing Tag > Cleaned Filename)
        search_name = query.get('name') or query.get('title')
        if not search_name: 
             # Use the cleaned filename stem for the initial search query
             search_name = self._clean_filename_for_search(filepath.stem)
             query['name'] = search_name # Use the clean name for the search API call
             ui.message(f"Cleaned filename for search: {search_name}", Colors.BLUE)
        
        if not query.get('name') and not query.get('artist'):
             ui.finish(status='error', warnings=["Could not determine track name or artist. Cannot search."])
             return

        lyr_src = self.config.get("defaults.lyrics.source", "interactive")
        
        # 2. Fetch ALL available metadata
        ui.step("Searching online for ALL metadata...")
        meta = self.meta_provider.fetch_song_data(query, source_mode=lyr_src)
        
        if query.get('duration'): meta['duration'] = query['duration']

        # 3. Apply/Save Logic 
        audio = self.tagger.load_file(filepath)
        success = False
        warnings = []
        has_lyrics = False

        if not audio:
            warnings.append("Could not load audio file.")
        else:
            manual = self.parse_kv(self.args.manual_tags)
            
            # --- Tag Preparation ---
            # If online search failed, the resulting 'meta' dictionary may be empty.
            # We must ensure Title/Artist are set using the best available info (existing tags or filename).
            if not meta.get('title'):
                # If online fetch didn't yield a title, fall back to the cleanest name we derived.
                meta['title'] = search_name
            # Fallback artist if online failed but local tags or inference gave one
            if not meta.get('artist') and existing.get('artist'):
                meta['artist'] = existing['artist']
            
            # Show planned tags before applying
            display_meta = {k: v for k, v in meta.items() if k not in ['lyrics', 'duration']}
            
            # Use Tagger helper to join artist lists for display consistency
            if 'artist' in display_meta:
                 if isinstance(display_meta['artist'], list):
                    display_meta['artist'] = self.tagger._join_artists(display_meta['artist'])
                 elif isinstance(display_meta['artist'], str) and ',' in display_meta['artist']:
                    # This is for display consistency, the actual tagging handles lists/strings later
                    display_meta['artist'] = display_meta['artist'].replace(',', self.config.get("separators.artist", "; "))

            ui.message(f"Planned tags:", color=Colors.BLUE)
            for k in sorted(display_meta.keys()):
                ui.message(f"  {k}: {display_meta[k]}", color=Colors.BLUE)

            ui.step("Applying tags...")
            self.tagger.apply_metadata(audio, meta, manual)
            
            # Cover Art Logic
            cover_data = None
            cover_path = None
            cover_config = self.args.cover_art or self.config.get("defaults.cover")
            
            if cover_config and cover_config != "extract":
                 if self.args.cover_art == "auto" and meta.get("cover_url"):
                     ui.step("Fetching cover art...")
                     try:
                        r = requests.get(meta["cover_url"])
                        tmp_art = Path("/tmp/swisstag_cover.jpg")
                        cover_data = r.content
                        with open(tmp_art, 'wb') as f: f.write(r.content)
                        cover_path = str(tmp_art)
                        self.tagger.apply_cover(audio, cover_path)
                        ui.message("Embedded cover art.", Colors.GREEN)
                     except Exception: 
                         warnings.append("Failed to fetch/embed cover art.")
                         ui.message("Failed to fetch/embed cover art.", Colors.RED)
                 elif self.args.cover_art and self.args.cover_art.startswith("file="):
                     cover_path = self.args.cover_art.split("=", 1)[1]
                     ui.step(f"Using local cover: {Path(cover_path).name}...")
                     self.tagger.apply_cover(audio, cover_path)
            
            # Save the file immediately after applying tags/cover, but before FS ops
            try:
                if not self.logger.is_dry:
                    audio.save()
                    ui.message("Tags saved to file.", Colors.GREEN)
                else:
                    ui.message("Tags applied (Dry Run).", Colors.BLUE)
                
                success = True
            except Exception as e:
                warnings.append(f"Failed to save tags to file: {e}")
                ui.message(f"ERROR: Failed to save tags to file: {e}", Colors.RED)
                success = False

            if cover_data and meta.get("album"): 
                self.tagger.save_cover(filepath, cover_data, meta["album"])
                ui.message("Saved cover art to file.", Colors.GREEN)
            elif cover_data: 
                self.tagger.save_cover(filepath, cover_data, "Unknown Album")
                ui.message("Saved cover art to file (Unknown Album).", Colors.GREEN)

            # Filesystem Operations (Only if save succeeded)
            if success:
                if 'rename' in fs_opts or self.config.get("defaults.rename"):
                    ui.step("Renaming file...")
                    new_path = self.file_handler.rename_file(filepath, meta)
                    if new_path != filepath:
                        ui.message(f"Renamed to: {new_path.name}", Colors.GREEN)
                    filepath = new_path
                
                if 'autosort' in fs_opts:
                    ui.step("Sorting file...")
                    new_path = self.file_handler.autosort(filepath, meta)
                    if new_path != filepath:
                        ui.message(f"Moved to: {new_path.parent.name} / {new_path.parent.parent.name}", Colors.GREEN)
                    filepath = new_path
                
                # Lyrics Save
                if self.config.get("defaults.lyrics.fetch", True):
                    if meta.get('lyrics'):
                        has_lyrics = True
                        l_mode = self.config.get("defaults.lyrics.mode")
                        if l_mode in ['lrc', 'both']: 
                            ui.step(f"Saving lyrics as .lrc...")
                            self.file_handler.save_lrc(filepath, meta['lyrics'])
                            ui.message("Saved .lrc file.", Colors.GREEN)
                        if l_mode in ['embed', 'both']:
                            ui.message("Embedded lyrics.", Colors.GREEN)
                    else:
                        warnings.append("Lyrics were expected but not found/fetched.")

                # Duration Validation Check (if chromaprint was used)
                if audio and meta.get('duration'):
                     local_dur = audio.info.length
                     if abs(local_dur - int(meta['duration'])) > 10:
                         warnings.append(f"Duration mismatch: Local {int(local_dur)}s vs Remote {meta['duration']}s")

        # 4. Final Status
        status = 'success'
        if not success: status = 'error'
        elif warnings: status = 'warning'
        
        ui.finish(status, warnings)

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
        # Check if cover fetching is requested or not explicitly disabled
        cover_config = self.args.cover_art or self.config.get("defaults.cover")
        if cover_config and cover_config != "extract": # Extract handled later
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
                except Exception: pass

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
            
            # Fetch lyrics only if requested
            has_lyrics = False
            if self.config.get("defaults.lyrics.fetch", True):
                ui.step("Fetching lyrics...")
                lyrics = self.meta_provider.fetch_lyrics_for_track(track['id'], title=track['title'], artist=track['artist'], source_mode=lyr_src, ui=ui)
                if lyrics: file_meta['lyrics'] = lyrics; has_lyrics = True
            
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
                
                # Show planned tags before applying
                try:
                    # Filter out lyrics from display
                    display_meta = {k: v for k, v in file_meta.items() if k != 'lyrics'}
                    
                    if 'artist' in display_meta and isinstance(display_meta['artist'], list):
                        display_meta['artist'] = self.tagger._join_artists(display_meta['artist'])
                    
                    ui.message(f"Planned tags:", color=Colors.BLUE)
                    for k in sorted(display_meta.keys()):
                        ui.message(f"  {k}: {display_meta[k]}", color=Colors.BLUE)
                except Exception:
                    pass

                self.tagger.apply_metadata(audio, file_meta, manual, ui=ui)
                if cover_path: self.tagger.apply_cover(audio, cover_path)
                if cover_data: self.tagger.save_cover(filepath, cover_data, album_meta['album'])
                
                # SAVE FILE
                try:
                    if not self.logger.is_dry:
                        audio.save()
                        ui.message("Tags saved to file.", Colors.GREEN)
                    else:
                        ui.message("Tags applied (Dry Run).", Colors.BLUE)
                    write_success = True
                except Exception as e:
                    warnings.append(f"Failed to save tags to file: {e}")
                    ui.message(f"ERROR: Failed to save tags to file: {e}", Colors.RED)
                    write_success = False

            
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
            elif self.config.get("defaults.lyrics.fetch", True) and not has_lyrics: 
                status = 'warning'
                warnings.append("Missing lyrics")

            ui.finish(status, warnings)

if __name__ == "__main__":
    try:
        app = SwissTag()
        app.run()
    except KeyboardInterrupt:
        print(f"\n{Colors.RED}Aborted by user.{Colors.RESET}")
        sys.exit(130)