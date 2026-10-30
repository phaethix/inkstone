#!/usr/bin/env bash
# Load KEY=value lines from a dotenv file without executing it.
#
# `source .env` runs command substitutions. This parser exports literal values
# and leaves variables that are already set alone, matching web/server.py.

inkstone_load_dotenv() {
  local file="${1:-}"
  local line key val
  [ -n "$file" ] && [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      ''|\#*) continue ;;
    esac
    case "$line" in
      export\ *) line="${line#export }" ;;
    esac
    case "$line" in
      *=*) ;;
      *) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      continue
    fi
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    if [[ "$val" == \"*\" && "$val" == *\" ]]; then
      val="${val:1:$((${#val} - 2))}"
    elif [[ "$val" == \'*\' && "$val" == *\' ]]; then
      val="${val:1:$((${#val} - 2))}"
    fi
    if [ -z "${!key+x}" ]; then
      printf -v "$key" '%s' "$val"
      export "$key"
    fi
  done < "$file"
}
