#!/usr/bin/env bash
# Installs the BlenderCraft app template and assistant extension into the
# user's Blender 5.2 configuration.
#
#   scripts/install.sh          copy the files
#   scripts/install.sh --dev    symlink them, so edits in the repo apply at once
#   scripts/install.sh --app    also build BlenderCraft.app into ~/Applications
#                               (combines with --dev)
#
# Environment overrides (mostly for tests):
#   BLENDERCRAFT_CONFIG_DIR   Blender config dir
#                             (default: ~/Library/Application Support/Blender/5.2)
#   BLENDERCRAFT_BLENDER_APP  Blender.app location (default: /Applications/Blender.app)
#   BLENDERCRAFT_APPS_DIR     where --app installs the app (default: ~/Applications)
#   BLENDERCRAFT_LSREGISTER   lsregister used to register the app with the Finder
set -euo pipefail

MODE=copy
APP=no
for arg in "$@"; do
  case "$arg" in
    --dev) MODE=dev ;;
    --app) APP=yes ;;
    -h|--help)
      sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Opcao desconhecida: $arg (use --dev, --app ou nada)" >&2
      exit 2
      ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
BLENDER_APP="${BLENDERCRAFT_BLENDER_APP:-/Applications/Blender.app}"
CONFIG="${BLENDERCRAFT_CONFIG_DIR:-$HOME/Library/Application Support/Blender/5.2}"

if [ ! -d "$BLENDER_APP" ]; then
  echo "Erro: Blender nao encontrado em $BLENDER_APP. Instale o Blender 5.2 em /Applications e tente de novo." >&2
  exit 1
fi

TEMPLATE_SRC="$REPO/app_template/blendercraft"
EXTENSION_SRC="$REPO/blendercraft_assistant"
TEMPLATE_DST="$CONFIG/scripts/startup/bl_app_templates_user/blendercraft"
EXTENSION_DST="$CONFIG/extensions/user_default/blendercraft_assistant"

install_dir() {
  local src="$1" dst="$2"
  local tmp
  tmp="$(dirname "$dst")/.$(basename "$dst").tmp.$$"
  mkdir -p "$(dirname "$dst")"
  rm -rf -- "$tmp"
  # Build the new install beside the old one, so a failure here leaves the old
  # one in place.
  if [ "$MODE" = dev ]; then
    ln -s "$src" "$tmp"
  else
    cp -R "$src" "$tmp"
    find "$tmp" -name __pycache__ -type d -prune -exec rm -rf {} +
  fi
  # No trailing slash: for a symlink this removes only the link, never the repo.
  rm -rf -- "$dst"
  mv "$tmp" "$dst"
}

install_dir "$TEMPLATE_SRC" "$TEMPLATE_DST"
install_dir "$EXTENSION_SRC" "$EXTENSION_DST"

if [ "$MODE" = dev ]; then
  echo "Instalado em modo dev (links simbolicos para $REPO)."
else
  echo "Instalado (copia de $REPO)."
fi
echo "  template:  $TEMPLATE_DST"
echo "  extensao:  $EXTENSION_DST"

# Same places the extension looks in (claude/process.py: find_claude).
CLAUDE=""
for candidate in "$(command -v claude 2>/dev/null || true)" \
    "$HOME/.local/bin/claude" /opt/homebrew/bin/claude /usr/local/bin/claude; do
  if [ -n "$candidate" ] && [ -x "$candidate" ] && [ -f "$candidate" ]; then
    CLAUDE="$candidate"
    break
  fi
done
if [ -z "$CLAUDE" ]; then
  echo "Aviso: Claude Code (claude) nao foi encontrado. Instale-o e rode 'claude' uma vez no terminal para fazer login." >&2
else
  echo "Claude Code encontrado em $CLAUDE."
fi

if [ "$APP" = yes ]; then
  APPS_DIR="${BLENDERCRAFT_APPS_DIR:-$HOME/Applications}"
  LSREGISTER="${BLENDERCRAFT_LSREGISTER:-/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister}"
  DIST="${BLENDERCRAFT_DIST_DIR:-$REPO/dist}"
  BLENDERCRAFT_DIST_DIR="$DIST" bash "$REPO/scripts/build_app.sh" >/dev/null
  mkdir -p "$APPS_DIR"
  APP_DST="$APPS_DIR/BlenderCraft.app"
  APP_TMP="$APPS_DIR/.BlenderCraft.app.tmp.$$"
  rm -rf -- "$APP_TMP"
  cp -R "$DIST/BlenderCraft.app" "$APP_TMP"
  rm -rf -- "$APP_DST"
  mv "$APP_TMP" "$APP_DST"
  # Tells the Finder about the app, its icon and the files it opens.
  "$LSREGISTER" -f "$APP_DST"
  echo "  app:       $APP_DST"
  echo "Pronto. Abra o BlenderCraft pelo app (Launchpad, Spotlight ou $APPS_DIR)."
else
  echo "Pronto. Abra o BlenderCraft com scripts/BlenderCraft.command (ou instale o app com --app)."
fi
