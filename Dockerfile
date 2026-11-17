# SCT — Sun Country Tech internal web app
#
# Dockerfile: builds a minimal, zero-dependency container for the app.
#   * python:3.14-slim (stdlib http.server + jinja2 + pure-Python pymysql)
#   * no system library compilation required
#   * runs as a non-root user (sct)
#
# Requires MySQL (8.x) as a separate service or host-side instance.
# Pair this image with the included ``docker-compose.yml`` which also
# provisions a MySQL 8 container (see ``docker-compose.yml``).
#
# Quick start:
#   docker compose up --build -d
#
# Environment variables are read from the host's env or a ``.env`` file
# adjacent to the docker-compose.yml.  See README.md for the full list.
FROM python:3.14-slim

WORKDIR /app

# Install Python deps (pymysql is pure-python; no compile step needed).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application (and migrations + templates)
COPY . .

# Create a non-root user to run the server.
RUN useradd --create-home --shell /bin/bash sct \
    && chown -R sct:sct /app
USER sct

# App listens on 0.0.0.0:8080 (Docker publishes this port).
EXPOSE 8080

# TLS certificate files
COPY cert/ /app/cert/
RUN chown -R sct:sct /app/cert

# Start the app with HTTPS support (self-signed cert for LAN use).
# Use SCT_TLS=1 to enable TLS on port 8443. For production, run behind
# a reverse proxy (nginx/caddy) with a proper certificate.
CMD ["python", "app.py"]
