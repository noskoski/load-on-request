FROM alpine:3.20

RUN apk add --no-cache stress-ng python3 \
    && adduser -D -u 1000 appuser

WORKDIR /app
COPY app.py .

USER appuser

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s \
  CMD wget -qO- http://127.0.0.1:8080/health || exit 1

CMD ["python3", "app.py"]
