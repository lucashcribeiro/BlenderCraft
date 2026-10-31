#!/usr/bin/env bash
# Builds BlenderCraft.app (an AppleScript applet) in dist/.
#
#   scripts/build_app.sh
#
# The app opens Blender with the BlenderCraft template. Files dropped on it
# or opened with it from the Finder ("Abrir com") are handed to a bundled
# copy of scripts/BlenderCraft.command: a .blend is opened, STL/3MF are
# imported. It claims .stl, .3mf and .blend with rank Alternate, so it never
# becomes the default app for them.
#
# Environment overrides (mostly for tests):
#   BLENDERCRAFT_DIST_DIR  where BlenderCraft.app is written (default: dist/)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
DIST="${BLENDERCRAFT_DIST_DIR:-$REPO/dist}"
APP="$DIST/BlenderCraft.app"
ICON_PNG="$REPO/scripts/assets/BlenderCraft.png"
LAUNCHER="$REPO/scripts/BlenderCraft.command"
BUNDLE_ID="io.github.lucashcribeiro.blendercraft"

for tool in osacompile sips iconutil codesign /usr/libexec/PlistBuddy; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Erro: $tool nao encontrado (este script so roda no macOS)." >&2
    exit 1
  fi
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The applet only finds its bundled launcher and runs it in the background;
# the launcher builds Blender's command line.
cat > "$WORK/BlenderCraft.applescript" <<'APPLESCRIPT'
on launcherPath()
	return (POSIX path of (path to me)) & "Contents/Resources/BlenderCraft.command"
end launcherPath

on run
	do shell script quoted form of launcherPath() & " > /dev/null 2>&1 &"
end run

on open theFiles
	set command to quoted form of launcherPath()
	repeat with theFile in theFiles
		set command to command & " " & quoted form of (POSIX path of theFile)
	end repeat
	do shell script command & " > /dev/null 2>&1 &"
end open
APPLESCRIPT

mkdir -p "$DIST"
rm -rf "$APP"
osacompile -o "$APP" "$WORK/BlenderCraft.applescript"

RES="$APP/Contents/Resources"
PLIST="$APP/Contents/Info.plist"
install -m 755 "$LAUNCHER" "$RES/BlenderCraft.command"

# Icon: every size of the macOS icon set, from the 1024 px PNG.
ICONSET="$WORK/BlenderCraft.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$ICON_PNG" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns -o "$RES/BlenderCraft.icns" "$ICONSET"
# osacompile's own icon: an .icns plus an asset catalog that wins over it.
rm -f "$RES/applet.icns" "$RES/droplet.icns" "$RES/Assets.car"

pb() { /usr/libexec/PlistBuddy -c "$1" "$PLIST"; }
pb "Delete :CFBundleIconName" 2>/dev/null || true
pb "Set :CFBundleIconFile BlenderCraft"
pb "Set :CFBundleName BlenderCraft"
pb "Add :CFBundleDisplayName string BlenderCraft"
pb "Add :CFBundleIdentifier string $BUNDLE_ID" 2>/dev/null || pb "Set :CFBundleIdentifier $BUNDLE_ID"
pb "Delete :CFBundleDocumentTypes" 2>/dev/null || true
pb "Add :CFBundleDocumentTypes array"
add_type() {
  local index="$1" name="$2" ext="$3" role="$4"
  pb "Add :CFBundleDocumentTypes:$index dict"
  pb "Add :CFBundleDocumentTypes:$index:CFBundleTypeName string $name"
  pb "Add :CFBundleDocumentTypes:$index:CFBundleTypeRole string $role"
  pb "Add :CFBundleDocumentTypes:$index:LSHandlerRank string Alternate"
  pb "Add :CFBundleDocumentTypes:$index:CFBundleTypeExtensions array"
  pb "Add :CFBundleDocumentTypes:$index:CFBundleTypeExtensions:0 string $ext"
}
add_type 0 "STL" stl Viewer
add_type 1 "3MF" 3mf Viewer
add_type 2 "Blender File" blend Editor

# Editing the bundle broke osacompile's ad-hoc signature; sign it again.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1

echo "BlenderCraft.app criado em $APP"
