# Maintainer: doromiert <doromiert@gmail.com>

pkgname=swisstag-git
pkgver=5.0.r0.g1234567
pkgrel=1
pkgdesc="Automated music tagger using Genius and MusicBrainz"
arch=('any')
url="https://github.com/doromiert/swisstag"
license=('GPL-3.0-or-later')

depends=(
    'python'
    'python-mutagen'
    'python-musicbrainzngs'
    'python-levenshtein'
    'python-requests'
    'python-unidecode'
    'python-pillow'
    'python-lyricsgenius' 
    'python-thefuzz'
    'python-syncedlyrics' 
    'python-rapidfuzz'
    'chromaprint' # For fpcalc
)

makedepends=('git')
provides=('swisstag')
conflicts=('swisstag')
source=("git+$url")
sha256sums=('SKIP')

pkgver() {
  cd "swisstag"
  git describe --long --tags 2>/dev/null | sed 's/\([^-]*-g\)/r\1/;s/-/./g' ||
  printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

package() {
  cd "swisstag"
  install -Dm755 swisstag.py "$pkgdir/usr/bin/swisstag"
  install -Dm644 swisstag.1 "$pkgdir/usr/share/man/man1/swisstag.1"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
