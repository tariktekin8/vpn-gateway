#!/bin/sh
# Unregister and delete ~/Applications/VPN Gateway Callback.app (only that exact path).
set -eu

app="$HOME/Applications/VPN Gateway Callback.app"
lsregister="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

if [ -d "$app" ]; then
  "$lsregister" -u "$app" || true
  rm -rf "$app"
  echo "Removed: $app"
else
  echo "Not installed: $app"
fi
if [ -d "/Applications/GlobalProtect.app" ]; then
  echo "macOS falls back to GlobalProtect for globalprotectcallback: links."
else
  echo "No other app handles globalprotectcallback: links now; paste them into the VPN Gateway page."
fi
