# WTD — standalone fleet container
#
#   docker build -t wtd .
#   docker run --rm \
#     -e CLAUDE_CODE_OAUTH_TOKEN \
#     -e ANTHROPIC_API_KEY \
#     -e GITHUB_TOKEN \
#     -v $PWD/wtd.yml:/app/wtd.yml:ro \
#     -v wtd-state:/root/.wtd \
#     wtd fleet loop --apply
#
# The image carries both lanes: the claude CLI (Claude Code OAuth,
# default) and the anthropic SDK (API fallback).

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# Claude Code native binary (the default provider lane).
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && ln -sf /root/.local/bin/claude /usr/local/bin/claude

WORKDIR /app
COPY pyproject.toml README.md ./
COPY wtd ./wtd
RUN pip install --no-cache-dir .

# Fleet state (queue, ledger, capacity) lives in ~/.wtd — mount a volume
# to persist it across container restarts.
VOLUME ["/root/.wtd"]

ENTRYPOINT ["wtd"]
CMD ["fleet", "status"]
