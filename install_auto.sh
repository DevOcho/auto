#!/bin/bash
# Installing the `auto` command

# Local vars
BLUE='\033[0;36m'
NC='\033[0m'

mkdir -p ~/.auto
echo " - Directory ~/.auto created"

# If auto was previously installed we want save the local.yaml file
if [ -f ~/.auto/config/local.yaml ]; then
    echo " - Previous install detected"
    echo "   = Saving local.yaml"
    cp -f ~/.auto/config/local.yaml /tmp/local.yaml.bak
fi

# Copy the contents of auto into the new directory
cp -r auto/* ~/.auto/.
printf " - Contents of ${BLUE}auto${NC} installed\n"

# If we saved the local.yaml lets put it back
if [ -f "/tmp/local.yaml.bak" ]; then
    echo " - Restored local.yaml"
    cp -f /tmp/local.yaml.bak ~/.auto/config/local.yaml
    rm -f /tmp/local.yaml.bak
fi

# Remove the python extension
mv ~/.auto/auto.py ~/.auto/auto
echo " - Removed python extension"

# Add the line to the ~/.bashrc file to make sure it is in our path
if ! [[ "$PATH" =~ "$HOME/.auto" ]]; then
    echo 'Adding auto to PATH'
    echo -e "\n# auto CLI path\nexport PATH=\"\$PATH:$HOME/.auto\"" >> ~/.bashrc
    echo 'Restart terminals or run: source ~/.bashrc'
fi

printf "\nYou now have ${BLUE}auto${NC} installed.\n"
printf "You can see what it does by simply typing 'auto' and pressing enter\n"
