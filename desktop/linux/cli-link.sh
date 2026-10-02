# --- splitsmith: /usr/bin/splitsmith -> the bundled CLI ---------------
# Ours only when it is a link into /opt/Splitsmith. A file or a link
# somewhere else (another install) is left alone on install and removal.
SPLITSMITH_BIN_DIR="${SPLITSMITH_BIN_DIR:-/usr/bin}"
SPLITSMITH_OPT_DIR="${SPLITSMITH_OPT_DIR:-/opt/Splitsmith}"
splitsmith_cli_link() {
  local link="$SPLITSMITH_BIN_DIR/splitsmith" src="$SPLITSMITH_OPT_DIR/resources/python/bin/splitsmith"
  if [ -e "$link" ] || [ -L "$link" ]; then
    case "$(readlink "$link" 2>/dev/null)" in
      "$SPLITSMITH_OPT_DIR"/*) ;;
      *) echo "splitsmith: $link exists and is not ours; leaving it" >&2; return 0 ;;
    esac
  fi
  ln -sfn "$src" "$link"
}
splitsmith_cli_unlink() {
  local link="$SPLITSMITH_BIN_DIR/splitsmith"
  case "$(readlink "$link" 2>/dev/null)" in
    "$SPLITSMITH_OPT_DIR"/*) rm -f "$link" ;;
  esac
}
