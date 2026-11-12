ARG BASE_IMAGE=ubuntu:24.04
FROM ghcr.io/astral-sh/uv:0.13.7 AS uv
FROM ${BASE_IMAGE}

# Prevent interactive prompts from package managers
ENV DEBIAN_FRONTEND=noninteractive

# Python 3 and git from whichever package manager the distro provides. uv
# installs the packages, so pip isn't needed.
RUN \
  if command -v apt-get > /dev/null 2>&1; then \
    apt-get update -qq && \
    apt-get install -y --no-install-recommends python3 git ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*; \
  elif command -v dnf > /dev/null 2>&1; then \
    dnf install -y python3 git && \
    dnf clean all; \
  elif command -v pacman > /dev/null 2>&1; then \
    pacman -Sy --noconfirm python git; \
  else \
    echo "ERROR: no supported package manager found (apt-get, dnf, pacman)" >&2 && exit 1; \
  fi

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# Test with the distro's own Python, not the 3.12 in .python-version that the
# release binary is built with: that is the point of running on each distro.
ENV UV_PYTHON=python3 \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PROJECT_ENVIRONMENT=/opt/venv

# The locked dependencies first, so this layer is cached apart from the source
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen

# Copy the source tree
COPY . .

# Provide a minimal valid local.yaml so config.py can load at import time
# without prompting for input or failing on a missing code directory.
RUN mkdir -p /root/.auto/config /tmp/auto-code && \
    printf 'code: /tmp/auto-code\nhttps: false\npods: []\nsystem-pods: []\n' \
      > /root/.auto/config/local.yaml

# autocli lives under auto/, so add that directory to PYTHONPATH so that
# `from autocli import ...` resolves correctly when pytest runs from the repo root.
ENV PYTHONPATH=/app/auto

CMD ["uv", "run", "--frozen", "pytest", "--tb=short", "-v"]
