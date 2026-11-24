#!/bin/bash
# Copies stdin to stdout and hides the value of every "...cookie=value" and "<...cookie>value</...cookie>" (case
# does not matter; "empty" stays).
# The VPN clients print login cookies, and a cookie can log in again without the password.
# A prompt without a line end ("Password:", an MFA code) is passed on after 0.2 s, so it shows at once.
# shortcut: a cookie line split by that 0.2 s pause is not hidden; the clients write such a line in one go.
hide() {
  sed -E -e '/cookie=empty$/I!s/(cookie)=[^ &;]+/\1=<hidden>/gI' \
    -e 's/(<[a-z_:-]*cookie[^>]*>)[^<]+(<\/[a-z_:-]*cookie>)/\1<hidden>\2/gI'
}
while :; do
  line=""
  IFS= read -r -t 0.2 line; rc=$?
  if [ $rc -eq 0 ]; then
    printf '%s\n' "$line" | hide
  elif [ $rc -gt 128 ]; then
    [ -n "$line" ] && printf '%s' "$line"
  else
    [ -n "$line" ] && printf '%s' "$line" | hide
    exit 0
  fi
done
