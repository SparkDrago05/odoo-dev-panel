#!/usr/bin/env bash
# Build a signed APT repository from a folder of .deb files.
#   packaging/apt/build-repo.sh DEB_DIR OUT_DIR
# OUT_DIR gets pool/, dists/stable/ (Packages, Release, InRelease, Release.gpg) and odoo-dev-panel.asc.
# The signing key must be in the gpg keyring and match the fingerprint of packaging/apt/odoo-dev-panel.asc.
# Needs apt-ftparchive (apt-utils) and gpg.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
DEBS=$(realpath "$1")
OUT=$2
SUITE=stable
COMPONENT=main
ARCH=amd64
PUBKEY=$HERE/odoo-dev-panel.asc

FPR=$(gpg --show-keys --with-colons "$PUBKEY" | awk -F: '/^fpr/{print $10; exit}')
gpg --list-secret-keys "$FPR" >/dev/null || { echo "secret key $FPR is not in the keyring" >&2; exit 1; }

rm -rf "$OUT"
POOL=pool/$COMPONENT/o/odoo-dev-panel
BIN=dists/$SUITE/$COMPONENT/binary-$ARCH
mkdir -p "$OUT/$POOL" "$OUT/$BIN"
shopt -s nullglob
debs=("$DEBS"/odoo-dev-panel_*_"$ARCH".deb)
[ ${#debs[@]} -gt 0 ] || { echo "no odoo-dev-panel_*_$ARCH.deb in $DEBS" >&2; exit 1; }
cp "${debs[@]}" "$OUT/$POOL/"

cd "$OUT"
apt-ftparchive packages pool > "$BIN/Packages"
gzip -9nk "$BIN/Packages"
apt-ftparchive \
    -o APT::FTPArchive::Release::Origin="Odoo Dev Panel" \
    -o APT::FTPArchive::Release::Label="Odoo Dev Panel" \
    -o APT::FTPArchive::Release::Suite="$SUITE" \
    -o APT::FTPArchive::Release::Codename="$SUITE" \
    -o APT::FTPArchive::Release::Architectures="$ARCH" \
    -o APT::FTPArchive::Release::Components="$COMPONENT" \
    -o APT::FTPArchive::Release::Description="Odoo Dev Panel packages for Ubuntu" \
    release "dists/$SUITE" > Release.tmp
mv Release.tmp "dists/$SUITE/Release"
gpg --batch --yes --local-user "$FPR" --digest-algo SHA512 --clearsign -o "dists/$SUITE/InRelease" "dists/$SUITE/Release"
gpg --batch --yes --local-user "$FPR" --digest-algo SHA512 --armor --detach-sign -o "dists/$SUITE/Release.gpg" "dists/$SUITE/Release"
cp "$PUBKEY" odoo-dev-panel.asc

echo "APT repository in $OUT: ${#debs[@]} package(s), signed by $FPR"
