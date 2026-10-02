# --- splitsmith: /usr/bin/splitsmith, a wrapper for the bundled CLI ----
# A wrapper, not a symlink: run from a terminal the CLI would find no
# ffmpeg next to its interpreter (it lives in resources/bin) and fall back
# to the system one, or to none. The wrapper points SPLITSMITH_FFMPEG /
# SPLITSMITH_FFPROBE at the bundled binaries unless the caller set them.
# Ours is a file carrying the marker line, or a link into /opt/Splitsmith
# (an install from before the wrapper). Anything else (another install) is
# left alone on install and removal.
SPLITSMITH_BIN_DIR="${SPLITSMITH_BIN_DIR:-/usr/bin}"
SPLITSMITH_OPT_DIR="${SPLITSMITH_OPT_DIR:-/opt/Splitsmith}"
SPLITSMITH_CLI_MARKER="# splitsmith-desktop CLI wrapper"
splitsmith_cli_ours() {
  if [ -L "$1" ]; then
    case "$(readlink "$1" 2>/dev/null)" in
      "$SPLITSMITH_OPT_DIR"/*) return 0 ;;
    esac
    return 1
  fi
  [ -f "$1" ] && grep -qxF "$SPLITSMITH_CLI_MARKER" "$1" 2>/dev/null
}
splitsmith_cli_link() {
  local link="$SPLITSMITH_BIN_DIR/splitsmith" res="$SPLITSMITH_OPT_DIR/resources"
  local tmp="$SPLITSMITH_BIN_DIR/.splitsmith.tmp.$$"
  if { [ -e "$link" ] || [ -L "$link" ]; } && ! splitsmith_cli_ours "$link"; then
    echo "splitsmith: $link exists and is not ours; leaving it" >&2
    return 0
  fi
  cat > "$tmp" <<SPLITSMITH_EOF
#!/bin/sh
$SPLITSMITH_CLI_MARKER
: "\${SPLITSMITH_FFMPEG:=$res/bin/ffmpeg}"
: "\${SPLITSMITH_FFPROBE:=$res/bin/ffprobe}"
export SPLITSMITH_FFMPEG SPLITSMITH_FFPROBE
exec "$res/python/bin/splitsmith" "\$@"
SPLITSMITH_EOF
  if chmod 0755 "$tmp" && mv -f "$tmp" "$link"; then
    return 0
  fi
  rm -f "$tmp"
  return 1
}
splitsmith_cli_unlink() {
  local link="$SPLITSMITH_BIN_DIR/splitsmith"
  if splitsmith_cli_ours "$link"; then
    rm -f "$link"
  fi
}
