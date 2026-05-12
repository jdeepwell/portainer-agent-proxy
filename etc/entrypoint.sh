#!/bin/sh
set -eu

SERVER_CERT_DIR=/data/server-certs
SERVER_CERT_PATH="$SERVER_CERT_DIR/proxy.crt"
SERVER_KEY_PATH="$SERVER_CERT_DIR/proxy.key"

mkdir -p /data "$SERVER_CERT_DIR" /data/nginx/conf.d /run
chown root:root /data /data/nginx /data/nginx/conf.d
chmod 755 /data
chmod 755 /data/nginx /data/nginx/conf.d

if [ -f "$SERVER_CERT_PATH" ] && [ -f "$SERVER_KEY_PATH" ]; then
    :
elif [ ! -f "$SERVER_CERT_PATH" ] && [ ! -f "$SERVER_KEY_PATH" ]; then
    tls_san="${PROXY_TLS_SAN:-}"
    if [ -n "$tls_san" ]; then
        tls_san="DNS:portainer_mtls_proxy,DNS:localhost,IP:127.0.0.1,$tls_san"
    else
        tls_san="DNS:portainer_mtls_proxy,DNS:localhost,IP:127.0.0.1"
    fi

    umask 077
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$SERVER_KEY_PATH" \
        -out "$SERVER_CERT_PATH" \
        -days "${PROXY_TLS_DAYS:-3650}" \
        -subj "${PROXY_TLS_SUBJECT:-/CN=portainer_mtls_proxy}" \
        -addext "subjectAltName=$tls_san" >/dev/null 2>&1
    chmod 644 "$SERVER_CERT_PATH"
    chmod 640 "$SERVER_KEY_PATH"
else
    echo "ERROR: inbound TLS requires both $SERVER_CERT_PATH and $SERVER_KEY_PATH" >&2
    exit 1
fi

exec supervisord -c /etc/supervisord.conf
