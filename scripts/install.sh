#!/usr/bin/env bash
# Installs the BlenderCraft app template and assistant extension into the
# user's Blender 5.2 configuration.
#
#   scripts/install.sh          copy the files
#   scripts/install.sh --dev    symlink them, so edits in the repo apply at once
#
# Environment overrides (mostly for tests):
#   BLENDERCRAFT_CONFIG_DIR   Blender config dir
#                             (default: ~/Library/Application Support/Blender/5.2)
#   BLENDERCRAFT_BLENDER_APP  Blender.app location (default: /Applications/Blender.app)
set -euo pipefail

MODE=copy
for arg in "$@"; do
  case "$arg" in
    --dev) MODE=dev ;;
    -h|--help)
      sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Opcao desconhecida: $arg (use --dev ou nada)" >&2
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

echo "Pronto. Abra o BlenderCraft com scripts/BlenderCraft.command."
