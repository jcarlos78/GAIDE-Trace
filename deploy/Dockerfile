# GAIDE-Trace server — zero-dependency Python, so the image is just the
# official slim runtime plus two directories.
#
# Build (from the repo root):
#   docker build -t gaide-trace-server -f deploy/Dockerfile .
# Run:
#   docker run -d --name gaide-trace -p 8321:8321 \
#     -v gaide-trace-data:/data gaide-trace-server
# First-run admin key: docker logs gaide-trace

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 trace \
 && mkdir -p /data && chown trace:trace /data
USER trace
WORKDIR /app

COPY --chown=trace server/ /app/server/

ENV GAIDE_TRACE_SERVER_DATA=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8321

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8321/healthz',timeout=3)"

CMD ["python3", "/app/server/gaide_trace_server.py", "serve", "--host", "0.0.0.0", "--port", "8321"]
