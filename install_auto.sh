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

# Download the latest release tar.gz from GitHub
echo " - Downloading latest release from GitHub..."
LATEST_URL="https://api.github.com/repos/${REPO}/releases/latest"
ASSET_URL=$(curl -sL "${LATEST_URL}" \
    | grep "browser_download_url" \
    | grep "auto-.*${ASSET_SUFFIX}\.tar\.gz" \
    | cut -d '"' -f 4)
if [ -z "${ASSET_URL}" ]; then
    echo "Error: No release asset matching ${ASSET_SUFFIX} found."
    exit 1
fi
if ! curl -sL -o "${TEMP_DIR}/auto-latest.tar.gz" "${ASSET_URL}"; then
    echo "Error: Failed to download the latest release."
    exit 1
fi

# Extract the tar.gz file
echo " - Extracting release..."
tar -xzf "${TEMP_DIR}/auto-latest.tar.gz" -C "${TEMP_DIR}"
EXTRACTED_DIR=$(ls -d ${TEMP_DIR}/auto-*/ | head -n 1)
if [ -z "${EXTRACTED_DIR}" ]; then
    echo "Error: Could not find extracted directory."
    exit 1
fi

ls -lah ${TEMP_DIR}

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
