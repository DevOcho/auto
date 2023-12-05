# Welcome to DevOcho's Auto CLI

This is the DevOcho `auto` technical documentation.  We are so glad you are here.

`auto` is basically a config file parser that runs `bash` commands and `k3d` commands
to create a local K3s / K3d cluster and populate it with the pods that you need to
run your software platform.  We use it at DevOcho to ensure that our local development
setup is as close to our production deployments as possible.  We also have the goal that
any new developer on a team can start working on that teams project with just one command
after they've cloned the repository.  `auto` makes that much easier.

`auto` comes with a few built in options to start shared services for the pods.  This
includes things like MySQL, MinIO and ProxySQL.  It's actually very easy to add additional
shared services if your software needs them.


## Installation

NOTE: `auto` is installed for a user and not installed system wide.
You can install it with the following commands:

```bash
git clone git@github.com:DevOcho/auto.git && cd auto
make && make install
source ~/.bashrc
```

Note: The `auto` install will update your `~/.bashrc` file to add itself to your path environment
variable.  For that change to take effect you will need to run `source ~/.bashrc` in each
open terminal or restart your terminals.


## Quickstart

Verify `auto` is installed.

```bash
auto --version
```

You need to edit the `code` folder in the `~/.auto/config/local.yaml` file to be a location
that you want your code to go.  By default this is `~/source`.  If this isn't where you want
things then you need to change it.  This is what I have set for mine:

```bash
# The code folder is where you want us to download all of your pod code repositories
code: /home/rogue/source/devocho
```

To start a cluster with a simple "Hello, World" pod you can just run:

```bash
auto start
```

It will pull the DevOcho portal repo that is a Python Flask reference application and
start it for you.

## Configure your pod(s)

Auto is configured by the local.yaml file located in `~/.auto/config/local.yaml`.  You can add your
pod in that file to have auto include it.  Pods are simply git repositories.  We need your git URL
and the branch name you want to clone.  Here is an example:

```yaml
# Each repo listed here will be run as a pod in k3s
pods:
  - repo: git@github.com:DevOcho/portal.git
    branch: main
```

`auto` will clone this repo into your "code" folder as defined in the `~/.auto/config/local.yaml` file.
It will also map the pod working directory in K3s to work in that code folder so you can do
your development there.  For this reason we recommend you execute your container in a development
mode so it restarts when you make code changes.

Inside your pod repository you will need to create an `.auto` folder that will need a config file called `config.yaml` and anything else your pod needs to get started (e.x. k8s yaml or helm charts).

Here is an example of a `config.yaml` file that uses helm to install/start the pod:

```yaml
---
# Portal information
name: training
desc: Training Portal
version: 1.0.0

# k8s/k3s commands (auto mostly builds the helm command for us)
command: helm install
command-args: --set ingress.enabled=true

# Database commands
init-command: init_db.py
seed-command: seed_db.py

system-pods:

  # We need a MySQL database
  - name: mysql
    databases:
      - name: dev_training
```
