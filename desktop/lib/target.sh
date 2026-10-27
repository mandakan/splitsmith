# shellcheck shell=bash
# Build targets, shared by the desktop build scripts. Sourced, not run.
#   macos-aarch64  the signed DMG (the only target before Linux shipped)
#   linux-x86_64   the AppImage and .deb
#   windows-x86_64 the NSIS installer, built under Git Bash (MSYS uname)

host_target() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) echo macos-aarch64 ;;
    Linux-x86_64) echo linux-x86_64 ;;
    MINGW64_NT-*-x86_64 | MSYS_NT-*-x86_64) echo windows-x86_64 ;;
    *) echo "unsupported build host $(uname -s)-$(uname -m)" >&2; return 1 ;;
  esac
}

parse_target_flag() {
  case "$1" in
    --mac) echo macos-aarch64 ;;
    --linux) echo linux-x86_64 ;;
    --windows) echo windows-x86_64 ;;
    *) return 1 ;;
  esac
}

# sha256_verify <sha256> <file>: shasum where it exists (macOS, the tool the
# scripts always used there), sha256sum otherwise.
sha256_verify() {
  if command -v shasum >/dev/null; then
    echo "$1  $2" | shasum -a 256 -c - >/dev/null
  else
    echo "$1  $2" | sha256sum -c - >/dev/null
  fi
}
