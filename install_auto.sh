#!/bin/bash
# Installing the `auto` command

mkdir -p ~/.auto
echo "Directory ~/.auto created"

# If auto was previously installed we want save the local.toml file
if [ -f ~/.auto/config/local.toml ]; then
    echo "Saving local.toml"
    cp -f ~/.auto/config/local.toml /tmp/local.toml.bak
fi

# Copy the contents of auto into the new directory
cp -r auto/* ~/.auto/.
echo "Contents of auto installed"

# If we saved the local.toml let's put it back
if [ -f "/tmp/local.toml.bak" ]; then
    echo "Restored local.toml"
    cp -f /tmp/local.toml.bak ~/.auto/config/local.toml
    rm -f /tmp/local.toml.bak
fi

# Remove the python extension
mv ~/.auto/auto.py ~/.auto/auto
echo "Removed python extension"

# Add the line to the ~/.bashrc file to make sure it's in our path
if ! [[ `env | grep PATH | grep 'auto'` ]]
then
    echo '';\
    echo "Updating path to include auto folder";\
    echo '' >> ~/.bashrc;\
    echo '# Adding auto to the path' >> ~/.bashrc;\
    echo 'export PATH="$PATH:/home/$USER/.auto"' >> ~/.bashrc;\
    echo 'IMPORTANT: Any open terminals will need to be restarted for this to take effect!';\
    echo '           or you can type "source ~/.bashrc" in the terminal';\

    # Now let's set an ENV var for the code directory
    CODE_DIR=$(pwd)
    echo '';\
    echo "Setting Auto Code Directory to $CODE_DIR";\
    echo '' >> ~/.bashrc;\
    echo '# Auto Code Directory' >> ~/.bashrc;\
    echo "export AUTO_CODE=$CODE_DIR" >> ~/.bashrc;\
fi
