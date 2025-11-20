# Swisstag
Swisstag is an automated music tagging utility for Linux. It fetches metadata, lyrics, and cover art from Genius and MusicBrainz, handling complex artist credits, feature splitting, and directory organization automatically.

It is designed to be "set and forget"—once configured, it can process entire albums, rename files, and sort your library with a single command.

<img width="499" height="492" alt="image" src="https://github.com/user-attachments/assets/8854db0e-2fcf-4294-969c-0f4e2eb801c4" />

## Features
- **Smart Album Mode:** Fetches the official tracklist from Genius and fuzzy-matches your local files to it, fixing track numbers and ordering even if your files are named inconsistently.
- **Feature Artist Handling:* Automatically extracts "feat. X" from song titles and moves them to the Artist tag (or keeps them, based on your preference).
- **Band Expansion:** Automatically expands known groups (e.g., "Kids See Ghosts") into individual artists ("Kanye West", "Kid Cudi") to keep your library searchable.
- **Filesystem Operations:** Renames files, creates directory structures (Artist/Album/Title.mp3), and downloads cover art.
- **Lyrics Fetching:** Embeds lyrics directly into your audio files or saves them as .lrc files.Interactive Matching: If automatic matching fails, an interactive CLI UI lets you manually assign tracks.

## Installation
### Arch Linux (AUR)
You can install swisstag using your preferred AUR helper:yay -S swisstag-git
### NixOS / Nix Flakes
Swisstag is available as a Nix Flake. You can run it directly: `nix run github:yourusername/swisstag` or add it to your flake.nix.
### Manual Installation
Swisstag is a single-file Python script. You can download it and let it self-manage dependencies: 
``` wget https://raw.githubusercontent.com/doromiert/swisstag/main/swisstag.py`
chmod +x swisstag.py
./swisstag.py --install-deps
sudo mv swisstag.py /usr/local/bin/swisstag```

## First Run: token setup
Swisstag requires a Genius API Client Access Token to fetch lyrics and tracklists.
Run the setup wizard: `swisstag --setup-token`. It will open the Genius API page. Sign in, create a new API Client (use any dummy URL), and paste the Client Access Token into the terminal.

## Usage
### Basic Usage
**Process a single file:** 
`swisstag "song.mp3"`
**Process an entire album (Recommended):** Navigate to the album directory and run
`swisstag -a -f infer-dirs` 

- -a / --album: Activates Album Mode (fetches tracklist first).
- -f infer-dirs: Infers Artist and Album names from the parent folders (e.g., Music/Artist/Album/).

### Common Flags
| Flag | Description |
| :--- | :--- |
| -a, --album | Album Mode. Fetches full tracklist and matches local files. |
| -f, --filesystem | Comma-separated list of actions: `rename` (clean filename), `autosort` (move to folder), `infer-dirs` (guess metadata from path). |
| -c, --cover-art | `auto` fetches from Genius. `file=cover.jpg` uses local image. |
| -l, --lyrics | `embed` (default), `lrc` (save .lrc file), or `both`. | 
| -F, --feat-handling | `split` (default), `keep` (do nothing), `split-clean` (move feat to artist tag). | -s, --search | Manually specify metadata: `-s artist="Kanye West",album="Yeezus"`.| 

### Examples
1. The "Fix Everything" Command: Renames files, sorts them into folders, fetches cover art, and splits features. `swisstag -a -f infer-dirs,rename,autosort -c auto -F split-clean`
2. Manual Search for an Obscure Album: If infer-dirs gets it wrong, tell it exactly what to look for: `swisstag -a -s artist="The Rare Occasions" album="Into The Shallows"`
3. Download Lyrics Only (Single File): `swisstag song.mp3 -l lrc`

### Configuration
You can permanently change default settings by editing `~/.config/swisstag/config.json` or using the CLI. Example: Set default cover art size to 3000x3000 `swisstag --config set defaults.cover.size 3000x3000`
Example: Add a new Band/Group to auto-expand (Requires editing config.json manually) 
```
"artist_groups": {
    "Kids See Ghosts": ["Kanye West", "Kid Cudi"],
    "Nirvana": ["Kurt Cobain", "Krist Novoselic", "Dave Grohl"]
}
```
## Dependencies
- mutagen (Tagging)
- lyricsgenius (API)
- musicbrainzngs (Year/Genre data)
- thefuzz & python-levenshtein (Fuzzy string matching)
- requests & unidecode (Utilities)
- Pillow (Image processing)
- chromaprint (song fingerprinting)
- rapidfuzz
- syncedlyrics

## License
GPL-3

