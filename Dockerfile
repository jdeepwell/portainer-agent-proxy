FROM nginx:alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN set -eux; \
    apk add --no-cache \
        supervisor \
        python3 \
        py3-flask; \
    addgroup -S www-data 2>/dev/null || true; \
    adduser -S -D -H -h /app -s /sbin/nologin -G www-data www-data 2>/dev/null || true; \
    addgroup www-data nginx 2>/dev/null || true; \
    mkdir -p /app/templates /certs /data /nginx/conf.d /run /var/log/supervisor; \
    rm -f /etc/nginx/conf.d/default.conf; \
    ln -sf /nginx/nginx.conf /etc/nginx/nginx.conf; \
    chown -R www-data:www-data /app /data

COPY app/ /app/
COPY etc/entrypoint.sh /etc/entrypoint.sh
COPY etc/supervisord.conf /etc/supervisord.conf
COPY nginx/nginx.conf /nginx/nginx.conf

RUN chmod +x /etc/entrypoint.sh

EXPOSE 9200

CMD ["/etc/entrypoint.sh"]
