#              _
#    __ _ _  _| |_ ___
#   / _` | || |  _/ _ \
#   \__,_|\_,_|\__\___/
#

# Let's build some stuff
BLUE=\033[0;36m
NC=\033[0m

init:
	@printf "Making ${BLUE}auto${NC}\n"
	@printf "Installing pip3 requirements..."
	@pip3 -q install -r requirements.txt
	@printf " done\n"
	@pre-commit install


# This install script should be safe to run multiple time
install:
    pyinstaller --onefile auto/auto.py -n auto
    
install: build
    cp dist/auto ~/.auto/
