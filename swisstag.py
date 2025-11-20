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
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from functools import wraps
from io import BytesIO

# --- Constants & Configuration ---
APP_NAME = "swisstag"
VERSION = "4.7"
CONFIG_DIR = Path.home() / ".config" / "swisstag"
CONFIG_FILE = CONFIG_DIR / "config.json"

# --- Detailed Help Text ---
DETAILED_HELP_TEXT = {
    "album": """
    --album, -a
    Switch to Album Mode.
    
    Behavior:
        1. Searches Genius for the album (Interactive Selection).
        2. Fetches the full tracklist.
        3. Matches local files to the official tracklist.
           - Auto-matches by filename similarity.
           - Prompts for manual matching if files remain unmatched.
        4. Tags files with consistent Album, Year, and Track Numbers.
    
    Requires:
        Either '--filesystem infer-dirs' OR '--search artist="..." name="..."'
        to identify which album to fetch.
        
    Example:
        swisstag --album -f infer-dirs
    """,
    "search": """
    --search, -s
    Provide explicit search criteria, bypassing local tag reads.
    
    Syntax:
        key="value" pairs separated by spaces.
        
    Keys:
        name="<Album/Song Name>" : The name of the song or album.
        artist="<Artist Name>"   : The name of the artist.
        url="<Genius URL>"       : A specific Genius URL to use (skips search).
        
    Examples:
        swisstag -s name="Curio" artist="AllttA"
        swisstag song.flac -s url="https://genius.com/Alltta-the-woods-lyrics"
    """,
    "manual_tags": """
    --manual-tags, -t
    Manually override specific tags.
    
    Behavior:
        Applies these tags immediately before saving, overwriting any fetched data.
    
    Syntax:
        tag="value" pairs.
        
    Available Tags:
        title, artist, album, year, genre, track_number
        
    Example:
        swisstag --album -t year="1999" genre="Hip-Hop"
    """,
    "feat_handling": """
    --feat-handling, -F
    Controls how song titles with featured artists are handled.
    
    Modes:
        keep        : Leaves the title and artist tags as-is.
        split       : (Default) Moves 'feat. X' from Title to Artist tag, adds '!' prefix to Title.
        split-clean : Moves 'feat. X' to Artist tag, removes it from Title entirely.
        
    Example:
        swisstag song.mp3 -F split-clean
    """,
    "filesystem": """
    --filesystem, -f
    Perform one or more filesystem operations. Options can be comma-separated.
    
    Options:
        rename          : Renames files to match their clean song titles.
        match-filename  : (Album Mode) Matches fetched tracks to files by filename similarity.
        infer-dirs      : Infers artist/album from directory structure.
        autosort        : Moves tagged files into Artist/Album/ structure.
        
    Example:
        swisstag --album -f rename,autosort
    """,
    "cover_art": """
    --cover-art, -c
    Manage fetching and applying cover art.
    
    Modes:
        auto                : (Default) Automatically searches online.
        file=/path/to.jpg   : Uses a specific local image file.
        extract             : Extracts embedded cover from the file.
        
    Example:
        swisstag song.mp3 -c file=cover.jpg
    """,
    "lyrics": """
    --lyrics, -l
    Fetch and save song lyrics from Genius.com.
    
    Modes:
        embed : (Default) Embeds lyrics directly into the audio file.
        lrc   : Saves lyrics as a separate .lrc file.
        both  : Performs both embed and lrc actions.
        
    Example:
        swisstag song.m4a -l lrc
    """,
    "debug": """
    --debug, -d
    Enable debugging output.
    
    Caution:
        Using --debug by itself performs a DRY RUN (no changes saved).
        Providing options (e.g., -d=network) executes a LIVE RUN.
        
    Options:
        dry     : Show actions without making changes.
        network : Print raw API responses.
        cmd     : Print shell commands (mv, mkdir).
        vars    : Print internal variables.
        all     : Enable all debug info (LIVE RUN).
        
    Example:
        swisstag --album -d=network,vars
    """,
    "config": """
    --config, -C
    Manage local or global configuration files.
    
    Syntax:
        swisstag -C <action> <target>=["<value>"]
    
    Actions:
        get     : Print the current value of a setting.
        set     : Update a setting in the config file.
        
    Example:
        swisstag -C get api_keys.genius
        swisstag -C set defaults.rename=true
    """,
    "set": """
    --set, -S
    Temporarily override a config setting for the current run only.
    
    Syntax:
        swisstag --set key=value
        
    Example:
        swisstag --album --set separators.artist=" & "
    """
}

HELP_MAP = {
    "-a": "album", "--album": "album",
    "-s": "search", "--search": "search",
    "-t": "manual_tags", "--manual-tags": "manual_tags",
    "-F": "feat_handling", "--feat-handling": "feat_handling",
    "-f": "filesystem", "--filesystem": "filesystem",
    "-c": "cover_art", "--cover-art": "cover_art",
    "-l": "lyrics", "--lyrics": "lyrics",
    "-d": "debug", "--debug": "debug",
    "-C": "config", "--config": "config",
    "-S": "set", "--set": "set"
}

# --- Dependency Management ---
REQUIRED_PACKAGES = {
    "mutagen": "mutagen",
    "lyricsgenius": "lyricsgenius",
    "musicbrainzngs": "musicbrainzngs",
    "thefuzz": "thefuzz",
    "Levenshtein": "python-levenshtein",
    "requests": "requests",
    "unidecode": "unidecode",
    "PIL": "Pillow"
}

def check_dependencies() -> List[str]:
    missing = []
    for import_name, install_name in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(install_name)
    return missing

def install_dependencies_interactive(missing: List[str]):
    print(f"[{APP_NAME}] Missing dependencies detected: {', '.join(missing)}")
    cmd = [sys.executable, "-m", "pip", "install", *missing]
    in_venv = hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
    if not in_venv:
        print(f"[{APP_NAME}] System Python detected. Using user-space install with override...")
        cmd.extend(["--user", "--break-system-packages"])
    try:
        subprocess.check_call(cmd)
        print(f"[{APP_NAME}] Installation successful. Restarting script...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to install dependencies: {e}")
        sys.exit(1)

_missing_deps = check_dependencies()
if "--install-deps" in sys.argv:
    if _missing_deps: install_dependencies_interactive(_missing_deps)
    else: sys.exit(0)
if _missing_deps:
    print(f"[{APP_NAME}] Critical dependencies missing. Run '{sys.argv[0]} --install-deps'")
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
from unidecode import unidecode
from thefuzz import fuzz
from PIL import Image

# --- Config & Defaults ---
DEFAULT_CONFIG = {
    "defaults": {
        "rename": False,
        "match_filename": True,
        "feat_handling": "split",
        "lyrics": {"fetch": True, "mode": "embed"},
        "cover": {"size": "1920x1920", "keep_resized": True, "extract": {"crop": False, "scale": True}}
    },
    "separators": {"artist": "; ", "genre": "; "},
    "regex": {"featured_artist": r"(?i)[(\[](?:feat|ft|featuring|with)\.?\s+(.*?)[)\]]"},
    "artist_groups": {
        "AllttA": ["20syl", "Mr. J. Medeiros"],
        "Nirvana": ["Kurt Cobain", "Krist Novoselic", "Dave Grohl"],
        "The Rare Occasions": ["Brian McLaughlin", "Luke Imbusch", "Jeremy Cohen"],
        "KIDS SEE GHOSTS": ["Kanye West", "Kid Cudi"],
        "Watch The Throne": ["Kanye West", "Jay-Z"],
        "Sunday Service Choir": ["Kanye West"],
        "¥$": ["Kanye West", "Ty Dolla $ign"],
        "Oddisee": ["Amir Mohamed el Khalifa"]
    },
    "known_artists": [],
    "blacklisted_genres": ["soundtrack"],
    "api_keys": {"genius": ""}
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

    def info(self, message: str): 
        pass 

    def warn(self, message: str): print(f"[WARN] {message}")
    def error(self, message: str): print(f"[ERROR] {message}", file=sys.stderr)

class TreeUI:
    def __init__(self, total, album_name=None):
        self.total = total
        self.idx = 0
        self.prefix = "├──"
        self.root_indent = "   "
        if album_name:
            print(f"{self.root_indent}Retagging album: {album_name}")

    def next(self, title):
        self.idx += 1
        self.prefix = "└──" if self.idx == self.total else "├──"
        print(f"{self.root_indent}{self.prefix} {title} ({self.idx}/{self.total})")

    def step(self, msg):
        indent = "    " if self.prefix == "└──" else "│   "
        sys.stdout.write(f"\033[2K\r{self.root_indent}{indent}└── {msg}")
        sys.stdout.flush()

    def done(self):
        indent = "    " if self.prefix == "└──" else "│   "
        sys.stdout.write(f"\033[2K\r{self.root_indent}{indent}└── Done")
        sys.stdout.flush()
        time.sleep(0.1)
        sys.stdout.write(f"\033[2K\r")
        sys.stdout.flush()

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

# --- Retry Decorator ---
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
    if isinstance(obj, dict):
        return obj.get(key, default)
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

    @api_retry()
    def _genius_search_hits(self, title, artist):
        if not self.genius: return []
        return self.genius.search_songs(f"{artist} {title}", per_page=5)

    @api_retry()
    def search_album_candidates(self, query: str) -> List[Dict]:
        if not self.genius: return []
        self.logger.log("network", f"Searching Genius for albums: {query}")
        res = self.genius.search_albums(query, per_page=5)
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
        album_raw = self.genius.album(album_id)
        if isinstance(album_raw, dict) and 'album' in album_raw:
            album_info = album_raw['album']
        else:
            album_info = album_raw

        data = {
            "album": get_attr(album_info, 'name'),
            "artist": get_attr(get_attr(album_info, 'artist'), 'name'),
            "cover_url": get_attr(album_info, 'cover_art_url'),
            "tracks": [],
            "year": None,
            "genre": None
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
            except Exception as e:
                 self.logger.error(f"MusicBrainz error: {e}")
        return data

    @api_retry()
    def _genius_get_song(self, song_id):
        if not self.genius: return None
        return self.genius.song(song_id)

    @api_retry()
    def _mb_search(self, title, artist):
        return musicbrainzngs.search_recordings(query=f'recording:"{title}" AND artist:"{artist}"', limit=1)

    def fetch_lyrics_for_track(self, track_id) -> str:
        self.logger.log("network", f"Fetching Lyrics for Song ID: {track_id}")
        song = self._genius_get_song(track_id)
        if isinstance(song, dict):
            if 'song' in song: song = song['song']
            return song.get('lyrics')
        return get_attr(song, 'lyrics')

    def fetch_song_data(self, query: Dict) -> Dict:
        data = {"title": query.get("name"), "artist": query.get("artist")}
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
                
                data["lyrics"] = get_attr(song, 'lyrics')
                data["title"] = get_attr(song, 'title')
                data["artist"] = get_attr(song, 'artist_names')
                if get_attr(song, 'song_art_image_url'):
                    data["cover_url"] = get_attr(song, 'song_art_image_url')
                
                alb = get_attr(song, 'album')
                if alb: data["album"] = get_attr(alb, 'name')
        return data

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
        return filepath

    def autosort(self, filepath: Path, metadata: Dict):
        # Use the first artist in the list if available
        art_tag = metadata.get("album_artist", metadata.get("artist", "Unknown"))
        if isinstance(art_tag, list): art_tag = art_tag[0]
        
        artist = re.sub(r'[<>:"/\\|?*]', '', art_tag)
        album = re.sub(r'[<>:"/\\|?*]', '', metadata.get("album", "Unknown Album"))
        dest_dir = filepath.parent.parent / artist / album 
        self.logger.log("cmd", f"mkdir -p '{dest_dir}' && mv '{filepath}' '{dest_dir}'")
        if not self.logger.is_dry:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(filepath, dest_dir / filepath.name)

    def save_lrc(self, filepath: Path, lyrics: str):
        lrc_path = filepath.with_suffix('.lrc')
        self.logger.log("cmd", f"Write lyrics to: {lrc_path}")
        if not self.logger.is_dry and lyrics:
            try:
                with open(lrc_path, 'w', encoding='utf-8') as f:
                    f.write(lyrics)
            except Exception as e:
                self.logger.error(f"Failed to save .lrc: {e}")

class Tagger:
    def __init__(self, config: ConfigManager, logger: Logger):
        self.config = config
        self.logger = logger
        self.feat_regex = re.compile(self.config.get("regex.featured_artist"))

    def load_file(self, path: Path):
        try: return mutagen.File(path, easy=False)
        except Exception: return None

    def handle_features(self, meta: Dict):
        """Extracts featured artists from title/artist and updates meta."""
        mode = self.config.get("defaults.feat_handling")
        if mode == "keep": return

        self.logger.log("vars", f"Feature Regex: {self.feat_regex.pattern}")

        # Helper to process a string for features
        def extract_from_string(s):
            found = []
            clean_s = s
            match = self.feat_regex.search(s)
            if match:
                self.logger.log("vars", f"Regex Matched in: '{s}'")
                feat_str = match.group(1)
                found = [f.strip() for f in re.split(r',|&', feat_str)]
                clean_s = self.feat_regex.sub("", s).strip()
            return found, clean_s

        # Initialize Artist List if missing
        if "artist" not in meta: meta["artist"] = []
        if isinstance(meta["artist"], str): meta["artist"] = [meta["artist"]]

        # 1. Check Title
        if meta.get("title"):
            feats, clean_title = extract_from_string(meta["title"])
            for f in feats:
                if f not in meta["artist"]: meta["artist"].append(f)
            
            if feats:
                if mode == "split": meta["title"] = f"! {clean_title}"
                elif mode == "split-clean": meta["title"] = clean_title

        # 2. Check Artist Fields (Genius sometimes puts features here)
        # We iterate a copy because we might modify the list
        current_artists = list(meta["artist"])
        new_artist_list = []
        
        for art in current_artists:
            feats, clean_art = extract_from_string(art)
            if clean_art not in new_artist_list:
                new_artist_list.append(clean_art)
            for f in feats:
                if f not in new_artist_list:
                    new_artist_list.append(f)
        
        meta["artist"] = new_artist_list
        
        if "artist" in meta and isinstance(meta["artist"], list) and len(meta["artist"]) > 1:
             self.logger.log("vars", f"Found features: {meta['artist'][1:]}")

    def expand_artist_groups(self, meta: Dict):
        """Expands known bands into their individual members."""
        groups = self.config.get("artist_groups", {})
        if not groups: return

        # Create case-insensitive map for lookup
        group_map = {k.lower(): v for k, v in groups.items()}
        target_keys = ["artist", "album_artist"]
        
        for key in target_keys:
            if key not in meta or not meta[key]: continue
            
            # Ensure list
            current = meta[key]
            if isinstance(current, str): current = [current]
            
            expanded_list = []
            for artist in current:
                expanded_list.append(artist)
                
                # Lookup
                members = group_map.get(artist.lower())
                if members:
                    self.logger.log("vars", f"Expanding group '{artist}' -> {members}")
                    if isinstance(members, str): members = [members]
                    expanded_list.extend(members)
            
            # Deduplicate preserving order
            seen = set()
            final_list = []
            for x in expanded_list:
                if x not in seen:
                    final_list.append(x)
                    seen.add(x)
            
            meta[key] = final_list

    def _join_artists(self, artists: Any) -> str:
        """Joins artist list with config separator."""
        sep = self.config.get("separators.artist", "; ")
        self.logger.log("vars", f"Joining artists with separator: '{sep}'")
        if isinstance(artists, list):
            return sep.join(artists)
        return str(artists)

    def apply_metadata(self, audio, meta: Dict, manual: Dict = None):
        if manual: meta.update(manual)
        
        # Process features before applying
        self.handle_features(meta)
        
        # Expand Groups (New)
        self.expand_artist_groups(meta)

        if self.logger.is_dry:
            self.logger.log("dry", f"Would tag: {meta}")
            return

        # Join artists based on config preferences
        if "artist" in meta: 
            meta["artist"] = self._join_artists(meta["artist"])
            self.logger.log("vars", f"Final Artist Tag: {meta['artist']}")
        if "album_artist" in meta: 
            meta["album_artist"] = self._join_artists(meta["album_artist"])

        if isinstance(audio, MP3): self._tag_id3(audio, meta)
        elif isinstance(audio, FLAC): self._tag_vorbis(audio, meta)
        elif isinstance(audio, MP4): self._tag_mp4(audio, meta)
        
        try: audio.save()
        except Exception as e: self.logger.error(f"Failed to save tags: {e}")

    def _should_embed(self):
        return self.config.get("defaults.lyrics.mode") in ['embed', 'both']

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
            if isinstance(audio, MP3):
                audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img_data))
            elif isinstance(audio, FLAC):
                p = Picture(); p.type = 3; p.mime = "image/jpeg"; p.desc = "Cover"; p.data = img_data
                audio.add_picture(p)
            elif isinstance(audio, MP4):
                audio['covr'] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
        except Exception as e: self.logger.error(f"Cover apply error: {e}")

    def save_cover(self, audio_file_path: Path, image_data: bytes, album_name: str):
        """Saves cover art to [Album Folder]/Cover Art/ using size rules."""
        if self.logger.is_dry or not image_data: return
        try:
            # Album folder is parent of audio file
            album_dir = audio_file_path.parent
            cover_dir = album_dir / "Cover Art"
            cover_dir.mkdir(exist_ok=True)
            
            # Check config size
            cfg_size_str = self.config.get("defaults.cover.size", "1000x1000")
            keep_resized = self.config.get("defaults.cover.keep_resized", True)
            try:
                max_w, max_h = map(int, cfg_size_str.lower().split('x'))
            except:
                max_w, max_h = 1000, 1000

            img = Image.open(BytesIO(image_data))
            w, h = img.size
            
            # Use album_name for file name, sanitize it
            safe_name = re.sub(r'[<>:"/\\|?*]', '', album_name)

            # Condition 1: Smaller or Equal
            if w <= max_w and h <= max_h:
                cover_file = cover_dir / f"{safe_name}.jpg"
                with open(cover_file, 'wb') as f:
                    f.write(image_data)
            
            # Condition 2: Larger
            else:
                if keep_resized:
                    # Save Original with special name
                    wk = math.floor(w / 1000)
                    hk = math.floor(h / 1000)
                    orig_name = f"{safe_name} {wk}kx{hk}k.jpg"
                    
                    with open(cover_dir / orig_name, 'wb') as f:
                        f.write(image_data)
                    
                    # Save Resized
                    img.thumbnail((max_w, max_h))
                    img.save(cover_dir / f"{safe_name}.jpg", "JPEG")
                else:
                    img.thumbnail((max_w, max_h))
                    img.save(cover_dir / f"{safe_name}.jpg", "JPEG")

        except Exception as e:
            self.logger.error(f"Failed to save cover art to file: {e}")

class SwissTag:
    def __init__(self):
        self.check_extended_help()
        self.args = self.parse_args()
        self.config = ConfigManager(self.args.temp_set)

        if self.args.lyrics: self.config.set("defaults.lyrics.mode", self.args.lyrics)
        if self.args.feat_handling: self.config.set("defaults.feat_handling", self.args.feat_handling)
        
        if self.args.setup_token: TokenWizard.run(self.config); sys.exit(0)
        if self.args.config_action: self.handle_config_action(); sys.exit(0)

        self.logger = Logger(self.args.debug)
        self.meta_provider = MetadataProvider(self.config, self.logger)
        self.file_handler = FileHandler(self.config, self.logger)
        self.tagger = Tagger(self.config, self.logger)

    def check_extended_help(self):
        if "-h" in sys.argv or "--help" in sys.argv:
            # Ensure other flags are present before intercepting
            other_args = [a for a in sys.argv if a not in ["-h", "--help", sys.argv[0]]]
            if not other_args:
                 return 
                 
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
        parser.add_argument("file", nargs="?", default=".", help="File or directory")
        parser.add_argument("--install-deps", action="store_true", help="Install dependencies")
        parser.add_argument("--setup-token", action="store_true", help="Setup Genius Token")
        parser.add_argument("-a", "--album", action="store_true", help="Album Mode")
        parser.add_argument("-s", "--search", nargs="+", help="Manual search")
        parser.add_argument("-t", "--manual-tags", nargs="+", help="Override tags")
        parser.add_argument("-F", "--feat-handling", choices=['keep', 'split', 'split-clean'], default="split")
        parser.add_argument("-f", "--filesystem", help="rename, match-filename, infer-dirs, autosort")
        parser.add_argument("-c", "--cover-art", help="auto, file=path, extract")
        parser.add_argument("-l", "--lyrics", help="embed, lrc, both")
        parser.add_argument("-d", "--debug", nargs="?", const="dry", help="dry, network, cmd, vars, all")
        parser.add_argument("-C", "--config", nargs="+", dest="config_action", help="config ops")
        parser.add_argument("-S", "--set", dest="temp_set", nargs="+", help="temp config")
        parser.add_argument("--about", action="store_true")
        return parser.parse_args()

    def handle_config_action(self):
        action = self.args.config_action[0]
        if action == "get" and len(self.args.config_action) > 1:
            print(self.config.get(self.args.config_action[1]))
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
        if self.args.about: print(f"{APP_NAME} v{VERSION}"); return
        target = Path(self.args.file).resolve()
        fs_opts = self.args.filesystem.split(',') if self.args.filesystem else []

        if self.args.album:
            if not target.is_dir(): print("[ERROR] Album mode requires directory."); sys.exit(1)
            self.run_album_mode(target, fs_opts)
        else:
            if target.is_dir(): print("[ERROR] Use -a for directories."); sys.exit(1)
            self.run_single_mode(target, fs_opts)

    def run_single_mode(self, filepath: Path, fs_opts: List[str]):
        self.logger.info(f"Processing: {filepath.name}")
        query = {}
        if self.args.search: query = self.parse_kv(self.args.search)
        if 'infer-dirs' in fs_opts: query.update(self.file_handler.infer_dirs(filepath))
        
        if not query.get('name'):
            query['name'] = re.sub(r"\[.*?\]|^\d+\s*-\s*", "", filepath.stem).strip()

        meta = self.meta_provider.fetch_song_data(query)
        self._process_and_apply(filepath, meta, fs_opts)

    def manual_match_interface(self, files, tracks):
        """Interactive manual matching logic."""
        matched = []
        available_tracks = list(tracks)
        
        print("\n=== Manual Matching Required ===")
        print("Some files could not be matched to the tracklist automatically.")
        
        for f in files:
            print(f"\nFile: {f.name}")
            print("Available Tracks:")
            for i, t in enumerate(available_tracks):
                print(f"  [{i+1}] {t['number']}. {t['title']} ({t['artist']})")
            
            while True:
                choice = input(f"Select track # for '{f.name}' (or 's' to skip): ").strip().lower()
                if choice == 's':
                    print("Skipping file.")
                    break
                
                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(available_tracks):
                        selected = available_tracks.pop(idx)
                        matched.append((f, selected))
                        print(f"Matched to: {selected['title']}")
                        break
                print("Invalid selection.")
        return matched

    def run_album_mode(self, directory: Path, fs_opts: List[str]):
        query = {}
        if self.args.search: query = self.parse_kv(self.args.search)
        if 'infer-dirs' in fs_opts: query.update(self.file_handler.infer_dirs(directory))
        
        if not query.get('artist') or not query.get('album'):
            if 'infer-dirs' not in fs_opts:
                self.logger.warn("Cannot identify album. Use -f infer-dirs or -s artist=.. album=..")
                return
            else:
                self.logger.warn(f"Could not infer artist/album from {directory}")
                return

        search_q = f"{query.get('artist')} {query.get('album')}"
        print(f"\n   Searching for album: '{search_q}'...")
        
        candidates = self.meta_provider.search_album_candidates(search_q)
        if not candidates:
            print("   No matching albums found on Genius.")
            return

        selected_id = None
        if len(candidates) == 1:
            c = candidates[0]
            print(f"\n   Found 1 match: {c['title']} by {c['artist']}")
            choice = input("   Is this correct? [Y/n] ").lower().strip()
            if choice in ['', 'y', 'yes']: selected_id = c['id']
            else: print("   Aborting."); return
        else:
            print(f"\n   Found {len(candidates)} matches:")
            for i, c in enumerate(candidates, 1): print(f"   [{i}] {c['title']} - {c['artist']}")
            choice = input(f"\n   Select an album (1-{len(candidates)}, n to abort): ").lower().strip()
            if choice.isdigit() and 1 <= int(choice) <= len(candidates): selected_id = candidates[int(choice)-1]['id']
            else: print("   Aborting."); return

        selected_title = "Unknown Album"
        for c in candidates:
             if c['id'] == selected_id:
                 selected_title = f"{c['title']} by {c['artist']}"
                 break

        print("") 
        
        album_meta = self.meta_provider.fetch_album_by_id(selected_id)
        if not album_meta.get('tracks'):
            self.logger.error("No tracks found for this album.")
            return

        local_files = sorted([f for f in directory.iterdir() if f.suffix.lower() in ['.mp3', '.flac', '.m4a', '.ogg']])
        matched_pairs = []
        
        if len(local_files) == len(album_meta['tracks']) and not 'match-filename' in fs_opts:
            for i, f in enumerate(local_files):
                matched_pairs.append((f, album_meta['tracks'][i]))
        else:
            for f in local_files:
                clean_name = re.sub(r"^\d+\s*[-.]?\s*", "", f.stem)
                best_track = None
                best_score = 0
                for t in album_meta['tracks']:
                    score = fuzz.token_sort_ratio(clean_name, t['title'])
                    if score > best_score:
                        best_score = score; best_track = t
                if best_score > 60:
                    matched_pairs.append((f, best_track))
                    self.logger.log("vars", f"Matched '{f.name}' -> '{best_track['title']}'")
                else:
                    pass

        matched_files_set = {f for f, t in matched_pairs}
        unmatched_files = [f for f in local_files if f not in matched_files_set]
        matched_track_ids = {t['id'] for f, t in matched_pairs}
        available_tracks = [t for t in album_meta['tracks'] if t['id'] not in matched_track_ids]
        
        if unmatched_files and available_tracks:
            manual_pairs = self.manual_match_interface(unmatched_files, available_tracks)
            matched_pairs.extend(manual_pairs)
            print("") 

        cover_path = None
        cover_data = None
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

        for filepath, track in matched_pairs:
            ui.next(f"{filepath.name}")

            genius_track_artist = track['artist']
            genius_album_artist = album_meta['artist']

            t_artist_list = []
            if inferred_artist: t_artist_list.append(inferred_artist)
            if genius_track_artist and genius_track_artist != inferred_artist:
                t_artist_list.append(genius_track_artist)
            if not t_artist_list: t_artist_list = ["Unknown"]

            a_artist_list = []
            if inferred_artist: a_artist_list.append(inferred_artist)
            if genius_album_artist and genius_album_artist != inferred_artist:
                a_artist_list.append(genius_album_artist)
            if not a_artist_list: a_artist_list = ["Unknown"]

            file_meta = {
                "title": track['title'],
                "track_number": track['number'],
                "artist": t_artist_list,
                "album": album_meta['album'],
                "album_artist": a_artist_list,
                "year": album_meta['year'],
                "genre": album_meta['genre']
            }
            
            ui.step("Fetching lyrics...")
            lyrics = self.meta_provider.fetch_lyrics_for_track(track['id'])
            if lyrics: file_meta['lyrics'] = lyrics
            
            ui.step("Applying tags...")
            audio = self.tagger.load_file(filepath)
            if audio:
                manual = self.parse_kv(self.args.manual_tags)
                self.tagger.apply_metadata(audio, file_meta, manual)
                if cover_path: 
                    self.tagger.apply_cover(audio, cover_path)
                    if cover_data:
                        self.tagger.save_cover(filepath, cover_data, album_meta['album'])
            
            if 'rename' in fs_opts or self.config.get("defaults.rename"):
                ui.step("Renaming...")
                filepath = self.file_handler.rename_file(filepath, file_meta)
            
            if 'autosort' in fs_opts:
                ui.step("Sorting...")
                self.file_handler.autosort(filepath, file_meta)
            
            l_mode = self.config.get("defaults.lyrics.mode")
            if l_mode in ['lrc', 'both'] and file_meta.get('lyrics'):
                self.file_handler.save_lrc(filepath, file_meta['lyrics'])
            
            ui.done()

    def _process_and_apply(self, filepath, meta, fs_opts):
        audio = self.tagger.load_file(filepath)
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

            if cover_data:
                 self.tagger.save_cover(filepath, cover_data, meta.get("album", "Unknown Album"))

            l_mode = self.config.get("defaults.lyrics.mode")
            if l_mode in ['lrc', 'both'] and meta.get('lyrics'):
                self.file_handler.save_lrc(filepath, meta['lyrics'])

            if 'rename' in fs_opts or self.config.get("defaults.rename"):
                self.file_handler.rename_file(filepath, meta)
            
            if 'autosort' in fs_opts:
                self.file_handler.autosort(filepath, meta)

if __name__ == "__main__": app = SwissTag(); app.run()
