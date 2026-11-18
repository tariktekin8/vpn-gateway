#!/bin/sh
# Build and register ~/Applications/VPN Gateway Callback.app, the handler for globalprotectcallback: links.
# Usage: handlers/macos/install.sh [--take-over]   (--take-over: make it the default even when GlobalProtect is installed)
# Run it again when this checkout moves: the app holds the absolute path of data/secrets/web-handler.json.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
app="$HOME/Applications/VPN Gateway Callback.app"
handler_file="$root/data/secrets/web-handler.json"
lsregister="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
gp_app="/Applications/GlobalProtect.app"

take_over=0
for arg in "$@"; do
  case "$arg" in
    --take-over) take_over=1 ;;
    *) echo "usage: $0 [--take-over]" >&2; exit 2 ;;
  esac
done

if ! xcrun --find swiftc >/dev/null 2>&1; then
  echo "swiftc not found. Install the Xcode Command Line Tools first: xcode-select --install" >&2
  exit 1
fi

build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT
staged="$build/VPN Gateway Callback.app"
mkdir -p "$staged/Contents/MacOS"
echo "Building VPN Gateway Callback..."
# Some Command Line Tools releases ship both module.modulemap and bridging.modulemap with the same
# SwiftBridging module, so AppKit fails to import ("redefinition of module 'SwiftBridging'").
# Hide the duplicate with a VFS overlay instead of changing system files.
overlay_args=""
swift_inc="$(dirname "$(dirname "$(xcrun --find swiftc)")")/include/swift"
if [ -f "$swift_inc/module.modulemap" ] && [ -f "$swift_inc/bridging.modulemap" ]; then
  : > "$build/empty.modulemap"
  printf '{"version":0,"roots":[{"name":"%s","type":"directory","contents":[{"name":"module.modulemap","type":"file","external-contents":"%s"}]}]}\n' \
    "$swift_inc" "$build/empty.modulemap" > "$build/overlay.yaml"
  overlay_args="-vfsoverlay $build/overlay.yaml -Xcc -ivfsoverlay -Xcc $build/overlay.yaml"
fi
# $overlay_args is unquoted on purpose: it is several flags. $build comes from mktemp, with no spaces.
xcrun swiftc -O -target "$(uname -m)-apple-macos12.0" $overlay_args -o "$staged/Contents/MacOS/CallbackHandler" \
  "$here/CallbackHandler.swift"
cp "$here/Info.plist" "$staged/Contents/Info.plist"
plutil -replace HandlerFile -string "$handler_file" "$staged/Contents/Info.plist"
codesign --force --sign - "$staged"

mkdir -p "$HOME/Applications"
old_app="$HOME/Applications/VPNHub Callback.app"  # the same app from before the project was renamed
if [ -e "$old_app" ]; then
  "$lsregister" -u "$old_app" >/dev/null 2>&1 || true
  rm -rf "$old_app"
  echo "Removed the old VPNHub Callback app."
fi
if [ -e "$app" ]; then
  "$lsregister" -u "$app" >/dev/null 2>&1 || true
  rm -rf "$app"
fi
mv "$staged" "$app"
"$lsregister" -f "$app"
echo "Installed and registered: $app"
echo "It reads the port and token from: $handler_file (written by ./vpn web while it runs)"

if [ -d "$gp_app" ]; then
  echo
  echo "WARNING: GlobalProtect is installed ($gp_app). The official app owns globalprotectcallback: links."
  echo "Making VPN Gateway Callback the default breaks the official app's browser login until handlers/macos/uninstall.sh is run."
  if [ "$take_over" -eq 1 ]; then
    "$app/Contents/MacOS/CallbackHandler" --set-default
  else
    echo "VPN Gateway Callback was NOT made the default. To take over the links, run: handlers/macos/install.sh --take-over"
  fi
else
  "$app/Contents/MacOS/CallbackHandler" --set-default
fi

echo
echo "The first time a login hands over a link, the browser asks \"Open VPN Gateway Callback?\": allow it."
echo "To remove it: handlers/macos/uninstall.sh"
