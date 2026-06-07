FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/mbgulden/prismatic-engine"
LABEL org.opencontainers.image.description="Prismatic Engine — agent orchestration hub"
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"

WORKDIR /app

# Install the package
COPY pyproject.toml README.md ./
COPY prismatic/ ./prismatic/
COPY config/ ./config/

RUN pip install --no-cache-dir .

# Default config directory
VOLUME /etc/prismatic

# Nudge file share (for FileSignalProvider)
VOLUME /tmp/prismatic

# HTTP signal provider port
EXPOSE 9001

ENTRYPOINT ["prismatic-engine"]
CMD ["serve"]
