#!/usr/bin/env bash
# yt-dlp through the Apify datacenter proxy, retrying across sticky sessions
# until one lands on an IP YouTube has not flagged. Usage: ytp.sh <yt-dlp args...>
export PATH=$HOME/.local/bin:$HOME/.deno/bin:$PATH
PP=${APIFY_PROXY_PASSWORD:-$(curl -s -m 20 "https://api.apify.com/v2/users/me?token=$APIFY_API_TOKEN" | jq -r .data.proxy.password)}
for i in $(seq 1 ${YTP_TRIES:-15}); do
  S="hs${RANDOM}${i}"
  out=$(yt-dlp --proxy "http://groups-BUYPROXIES94952,session-$S:$PP@proxy.apify.com:8000" --no-warnings "$@" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then printf '%s\n' "$out" | sed "s/$PP/***/g" | grep -vE '^\[download\] +[0-9.]+% of' ; exit 0; fi
  if ! printf '%s' "$out" | grep -qE 'Sign in|403|429|timed out|Unable to download'; then
    printf '%s\n' "$out" | sed "s/$PP/***/g" | tail -5; exit $rc
  fi
done
echo "ytp: all sessions failed" >&2; exit 1
