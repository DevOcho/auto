ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

# Prevent interactive prompts from package managers
ENV DEBIAN_FRONTEND=noninteractive

# Install Python 3, pip, and git using whichever package manager the distro provides
RUN \
  if command -v apt-get > /dev/null 2>&1; then \
    apt-get update -qq && \
    apt-get install -y --no-install-recommends python3 python3-pip git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*; \
  elif command -v dnf > /dev/null 2>&1; then \
    dnf install -y python3 python3-pip git && \
    dnf clean all; \
  elif command -v pacman > /dev/null 2>&1; then \
    pacman -Sy --noconfirm python python-pip git; \
  else \
    echo "ERROR: no supported package manager found (apt-get, dnf, pacman)" >&2 && exit 1; \
  fi

WORKDIR /app

# Copy dependency files first so this layer is cached independently of source changes
COPY requirements.txt build_requirements.txt ./

# Install only the dependencies actually needed by the test suite.
# Heavy build/lint/doc tools from requirements.txt (pyinstaller, mkdocs, pre-commit,
# black, codespell, flake8, pylint, pyupgrade, etc.) are skipped to keep the image
# small and avoid arch-specific binary issues inside Docker.
#
# Packages needed at test time:
#   runtime  — click, rich, pyfiglet, dulwich, gitpython, toml, PyYAML, requests,
#              charset-normalizer (from build_requirements.txt)
#   testing  — pytest, pytest-cov, coverage (from requirements.txt)
RUN pip3 install --no-cache-dir --break-system-packages \
      click \
      rich \
      pyfiglet \
      dulwich \
      gitpython \
      "charset_normalizer<=3.4.4" \
      toml \
      PyYAML \
      requests \
      pytest \
      pytest-cov \
      coverage \
  2>/dev/null || \
  pip3 install --no-cache-dir \
      click \
      rich \
      pyfiglet \
      dulwich \
      gitpython \
      "charset_normalizer<=3.4.4" \
      toml \
      PyYAML \
      requests \
      pytest \
      pytest-cov \
      coverage

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

CMD ["python3", "-m", "pytest", "--tb=short", "-v"]
