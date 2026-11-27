FROM nginx:1.28-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /usr/local/bin/dev-gateway
RUN chmod 0755 /usr/local/bin/dev-gateway
ENTRYPOINT ["/usr/local/bin/dev-gateway"]
