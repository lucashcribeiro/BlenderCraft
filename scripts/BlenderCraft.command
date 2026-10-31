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
# hands it. BLENDERCRAFT_BLENDER overrides the Blender binary (for tests).
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
exec "$BLENDER" "${args[@]}"
