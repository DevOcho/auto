#!/bin/bash
# Installing the `auto` command

# Local vars
BLUE='\033[0;36m'
NC='\033[0m'
REPO="devocho/auto"
TEMP_DIR=$(mktemp -d)

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check for required tools
for cmd in curl tar uname; do
    if ! command_exists "$cmd"; then
        echo "Error: $cmd is required but not installed."
        exit 1
    fi
done

# Detect OS and architecture so we pull the right release asset
OS=$(uname -s)
ARCH=$(uname -m)
case "$OS" in
    Linux)
        case "$ARCH" in
            x86_64) ASSET_SUFFIX="linux-x86_64" ;;
            *) echo "Error: Unsupported Linux architecture: $ARCH"; exit 1 ;;
        esac
        ;;
    Darwin)
        case "$ARCH" in
            arm64|aarch64) ASSET_SUFFIX="darwin-arm64" ;;
            *) echo "Error: Unsupported macOS architecture: $ARCH (only Apple Silicon is supported)"; exit 1 ;;
        esac
        ;;
    *)
        echo "Error: Unsupported OS: $OS"
        exit 1
        ;;
esac
echo " - Detected ${OS} / ${ARCH} (asset: ${ASSET_SUFFIX})"

# Pick the shell rc file matching the user's default shell.
# macOS defaults to zsh; most Linux distros default to bash.
case "$(basename "${SHELL:-/bin/bash}")" in
    zsh)  SHELL_RC="$HOME/.zshrc" ;;
    bash) SHELL_RC="$HOME/.bashrc" ;;
    *)    SHELL_RC="$HOME/.profile" ;;
esac

# Create ~/.auto directory
mkdir -p ~/.auto
echo " - Directory ~/.auto created"

# If auto was previously installed we want save the local.yaml file
if [ -f ~/.auto/config/local.yaml ]; then
    echo " - Previous install detected"
    echo "   = Saving local.yaml"
    cp -f ~/.auto/config/local.yaml ${TEMP_DIR}/local.yaml.bak
fi

# Install the exact release pinned by AUTO_VERSION (e.g. AUTO_VERSION=0.7.1), or the latest
AUTO_VERSION="${AUTO_VERSION:-}"
if [ -n "${AUTO_VERSION}" ]; then
    AUTO_VERSION="${AUTO_VERSION#v}"
    if ! printf '%s' "${AUTO_VERSION}" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
        echo "Error: invalid AUTO_VERSION '${AUTO_VERSION}' (expected X.Y.Z)."
        exit 1
    fi
    RELEASE_URL="https://api.github.com/repos/${REPO}/releases/tags/v${AUTO_VERSION}"
    echo " - Downloading auto v${AUTO_VERSION} from GitHub..."
else
    RELEASE_URL="https://api.github.com/repos/${REPO}/releases/latest"
    echo " - Downloading latest release from GitHub..."
fi

# Fetch the release metadata (the HTTP status tells us if a pinned version is missing)
RELEASE_JSON="${TEMP_DIR}/release.json"
HTTP_STATUS=$(curl -sL -w '%{http_code}' -o "${RELEASE_JSON}" "${RELEASE_URL}")
if [ "${HTTP_STATUS}" != "200" ]; then
    case "${HTTP_STATUS}" in
        404) echo "Error: auto v${AUTO_VERSION} not found (no such release)." ;;
        403|429) echo "Error: GitHub API rate limit reached (HTTP ${HTTP_STATUS}); try again later." ;;
        *) echo "Error: failed to fetch release metadata (HTTP ${HTTP_STATUS})." ;;
    esac
    exit 1
fi

ASSET_URL=$(grep "browser_download_url" "${RELEASE_JSON}" \
    | grep "auto-.*${ASSET_SUFFIX}\.tar\.gz" \
    | cut -d '"' -f 4)
if [ -z "${ASSET_URL}" ]; then
    if [ -n "${AUTO_VERSION}" ]; then
        echo "Error: release v${AUTO_VERSION} has no ${ASSET_SUFFIX} asset (release too old or unsupported)."
    else
        echo "Error: No release asset matching ${ASSET_SUFFIX} found."
    fi
    exit 1
fi
if ! curl -sL -o "${TEMP_DIR}/auto-release.tar.gz" "${ASSET_URL}"; then
    echo "Error: Failed to download the release."
    exit 1
fi

# Extract the tar.gz file
echo " - Extracting release..."
tar -xzf "${TEMP_DIR}/auto-release.tar.gz" -C "${TEMP_DIR}"
EXTRACTED_DIR=$(ls -d ${TEMP_DIR}/auto-*/ | head -n 1)
if [ -z "${EXTRACTED_DIR}" ]; then
    echo "Error: Could not find extracted directory."
    exit 1
fi

# Clean existing binary to prevent "file busy" lock issues
echo " - Removing old executable..."
rm -f ~/.auto/auto

# Copy the contents of auto into the new directory
cp -r ${EXTRACTED_DIR}/* ~/.auto/.
printf " - Contents of ${BLUE}auto${NC} installed\n"

# If we saved the local.yaml lets put it back
if [ -f "${TEMP_DIR}/local.yaml.bak" ]; then
    echo " - Restored local.yaml"
    cp -f ${TEMP_DIR}/local.yaml.bak ~/.auto/config/local.yaml
fi

# Clean up temporary directory
rm -rf "${TEMP_DIR}"

# Ensure the auto command is executable
chmod +x ~/.auto/auto
echo " - Ensured auto is executable"

# On macOS, strip the quarantine attribute Gatekeeper applies to downloaded binaries
# so the user doesn't hit a "cannot verify developer" warning on first run.
if [ "$OS" = "Darwin" ] && command_exists xattr; then
    xattr -d com.apple.quarantine ~/.auto/auto 2>/dev/null || true
    echo " - Cleared macOS quarantine attribute"
fi

# Add the line to the shell rc file to make sure it is in our path
if ! [[ `env | grep PATH | grep 'auto'` ]]
then
    echo '';\
    echo "Updating path to include auto folder (${SHELL_RC})";\
    echo '' >> "${SHELL_RC}";\
    echo '# Adding auto to the path' >> "${SHELL_RC}";\
    echo 'export PATH="$PATH:'"$HOME"'/.auto"' >> "${SHELL_RC}";\
    echo "IMPORTANT: Any open terminals will need to be restarted for this to take effect!";\
    echo "           or you can type \"source ${SHELL_RC}\" in the terminal";\
fi

printf "\nYou now have ${BLUE}auto${NC} installed.\n"
printf "You can see what it does by simply typing 'auto' and pressing enter\n"
