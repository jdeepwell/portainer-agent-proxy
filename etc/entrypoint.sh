#!/bin/sh
set -eu

mkdir -p /data /run /nginx/conf.d
chown www-data:www-data /data
chmod 755 /data

if [ -f /data/mappings.json ]; then
    chown www-data:www-data /data/mappings.json
fi

exec supervisord -c /etc/supervisord.conf
