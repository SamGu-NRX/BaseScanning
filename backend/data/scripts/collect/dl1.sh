#!/usr/bin/env bash
# dl1.sh VIDEO_ID MAXH [extra yt-dlp args]
v=$1; h=$2; shift 2
export APIFY_PROXY_PASSWORD=$(curl -s -m 20 "https://api.apify.com/v2/users/me?token=$APIFY_API_TOKEN" | jq -r .data.proxy.password)
YTP_TRIES=25 /tmp/hs/tools/ytp.sh -f "bv*[height<=$h][width<=1920]/bv*[width<=$h]/b" --http-chunk-size 5M --no-playlist -o "%(id)s.%(ext)s" "$@" "https://www.youtube.com/watch?v=$v" > $v.log 2>&1
echo "$v rc=$?" >> dl.status
