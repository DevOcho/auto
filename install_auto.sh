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
for cmd in curl tar; do
    if ! command_exists "$cmd"; then
        echo "Error: $cmd is required but not installed."
        exit 1
    fi
done

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
if ! curl -sL -o "${TEMP_DIR}/auto-latest.tar.gz" \
    $(curl -sL "${LATEST_URL}" | grep "browser_download_url" | grep "auto-.*\.tar\.gz" | cut -d '"' -f 4); then
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

# Add the line to the ~/.bashrc file to make sure it is in our path
if ! [[ `env | grep PATH | grep 'auto'` ]]
then
    echo '';\
    echo "Updating path to include auto folder";\
    echo '' >> ~/.bashrc;\
    echo '# Adding auto to the path' >> ~/.bashrc;\
    echo 'export PATH="$PATH:/home/$USER/.auto"' >> ~/.bashrc;\
    echo 'IMPORTANT: Any open terminals will need to be restarted for this to take effect!';\
    echo '           or you can type "source ~/.bashrc" in the terminal';\

    # Now set an ENV var for the code directory
    #CODE_DIR=$(pwd)
    #echo '';\
    #echo "Setting Auto Code Directory to $CODE_DIR";\
    #echo '' >> ~/.bashrc;\
    #echo '# Auto Code Directory' >> ~/.bashrc;\
    #echo "export AUTO_CODE=$CODE_DIR" >> ~/.bashrc;\
fi

printf "\nYou now have ${BLUE}auto${NC} installed.\n"
printf "You can see what it does by simply typing 'auto' and pressing enter\n"
