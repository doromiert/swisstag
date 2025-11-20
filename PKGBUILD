# Maintainer: doromiert <your@email.com>
pkgname=swisstag-git
pkgver=4.7.r0.g1234567
pkgrel=1
pkgdesc="Automated music tagger using Genius and MusicBrainz"
arch=('any')
url="https://github.com/doromiert/swisstag"
license=('GPL3')
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
  # Man Page Installation
  install -Dm644 swisstag.1 "$pkgdir/usr/share/man/man1/swisstag.1"
  # Install License (optional but good practice)
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
