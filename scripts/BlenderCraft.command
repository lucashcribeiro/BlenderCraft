#!/usr/bin/env bash
# Opens Blender with the BlenderCraft template.
#
#   BlenderCraft.command [--open] [FILE ...]
#
# A .blend file is opened as the file (the first one, if there are several).
# Any other file (STL, 3MF, OBJ, ...) is imported into a new BlenderCraft
# file: it goes to the template after "--" as "--bc-open FILE". Other
# options starting with "-" are passed on to Blender.
#
# BlenderCraft.app runs a copy of this script with the files the Finder
# hands it. Blender's stdout and stderr are appended to
# ~/Library/Logs/BlenderCraft/blendercraft.log (a header line per launch;
# above 10 MB the file is moved to blendercraft.log.1 first); the extension's
# own log (extension.log) lives in the same folder.
#
# Environment overrides (for tests):
#   BLENDERCRAFT_BLENDER   the Blender binary
#   BLENDERCRAFT_LOG_DIR   the log folder
BLENDER="${BLENDERCRAFT_BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"

args=(--app-template blendercraft)
blend=""
imports=()
while [ $# -gt 0 ]; do
  case "$1" in
    --open)
      if [ $# -lt 2 ]; then
        echo "--open precisa de um arquivo" >&2
        exit 2
      fi
      file="$2"
      shift 2
      ;;
    -*)
      args+=("$1")
      shift
      continue
      ;;
    *)
      file="$1"
      shift
      ;;
  esac
  case "$file" in
    /*) ;;
    *) file="$(pwd -P)/$file" ;;
  esac
  lower="$(printf '%s' "$file" | tr '[:upper:]' '[:lower:]')"
  case "$lower" in
    *.blend)
      if [ -z "$blend" ]; then blend="$file"; fi
      ;;
    *)
      imports+=(--bc-open "$file")
      ;;
  esac
done

if [ -n "$blend" ]; then
  args+=("$blend")
fi
if [ ${#imports[@]} -gt 0 ]; then
  args+=(-- "${imports[@]}")
fi

LOG_DIR="${BLENDERCRAFT_LOG_DIR:-$HOME/Library/Logs/BlenderCraft}"
LOG="$LOG_DIR/blendercraft.log"
MAX_LOG_BYTES=10485760
if mkdir -p "$LOG_DIR" 2>/dev/null && touch "$LOG" 2>/dev/null; then
  if [ "$(wc -c < "$LOG" | tr -d ' ')" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG" "$LOG.1"
  fi
  printf '=== %s BlenderCraft: %s %s ===\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$BLENDER" "${args[*]}" >> "$LOG"
  exec "$BLENDER" "${args[@]}" >> "$LOG" 2>&1
fi
# The log folder is not writable: still start Blender, without a log.
exec "$BLENDER" "${args[@]}" > /dev/null 2>&1
