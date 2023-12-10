# Welcome to DevOcho's `auto` CLI

This is the DevOcho `auto` technical documentation.  We are so glad you are here.

As mentioned in the README.md file, `auto` sets up a local k3s environment
utilizing k3d (k3s in docker).  It then uses config files to create
your environment.  At DevOcho we have the goal of a sub 10 minute start up
for a developer joining a project.  `auto` helps us achieve that goal.  We
explain our process a bit more in a moment.  One amazing
benefit of this less than 10 minute start, is that if anything obscure
breaks, the developer is typically able to recover the entire
local development environment in 10 minutes.  This is a huge boost to
productivity.

`auto` is basically a config file parser that runs `bash` commands and `k3d`
commands to create a local K3s / K3d cluster and populate it with the pods
that you need to run your software platform.  We use it at DevOcho to ensure
that our local development setup is as close to our production deployments
as possible.  We also have the goal that any new developer on a team can
start working on that team's project with just one command after they've
cloned the repository.  `auto` makes that much easier.

`auto` comes with a few built in options to start shared services for usage
by the pods.  This includes things like MySQL, MinIO, and ProxySQL.  It's
actually very easy to add additional shared services if your software needs them.
You just need to provide helm charts or K8s YAML files and tell auto to apply
them in the global auto config.


## Installation and Setup

The following section provides the installation and setup steps as well as
the dependencies you need to have pre-installed before you install `auto`.

### Dependencies
You will need a Linux system with the following pre-installed:

- Bash (`auto` uses Bash commands)
- Git
- Python 3
- Docker (both the daemon running and the bash command available as a
  non-root user)
- K3D (k3d.io)
- kubectl

### Install Commands

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

You can verify `auto` is installed with the following command:

```bash
auto --version
```


## Quickstart

We have a quick start option in the README.md file that can get an auto K3s/K3d
cluster running on your computer very quickly.  In this guide we are going to
take a more in-depth step-by-step process to everything.


## Setting up your `auto` environment

Once you've installed `auto` you can get up and running with the following steps:

### Edit the `~/.auto/config/local.yaml`` file

#### The local code folder

You need to edit the `code` folder in the `~/.auto/config/local.yaml` file to
be a location that you want your code to go.  By default this is `~/source`.
If this isn't where you want things then you need to change it.  This is what
I have set for mine:

```bash
# The code folder is where you want us to download all of your pod code repositories
code: /home/rogue/source/devocho
```

#### Adding Your Pods

`auto` checks the `[pods]` section to see which pods you want to run in
your local k3s cluster.  We assume each pod is in it's own separate git
repository.

Below is an example to show you how to setup a pod:

```yaml
pods:
  - repo: git@github.com:DevOcho/portal.git
    branch: main
```

### Setting up your application to run in `auto`

`auto` assumes a microservices environment (but doesn't specifically require
it).  With that assumption, we need each pod to contain the config files needed
to run it.  Since each pod is it's own unique git code repository.  We will look
for the following files/folders in your repo:

```
/Dockerfile
/.auto/config.yaml (explained below)
/.auto/k8s   (if using k8s yaml)
/.auto/helm  (if using helm charts)
```

In your pod you will need an `.auto` folder that contains a `config.yaml``
file that tells auto how you want it to run.  Here is an example of a
web application pod using a helm chart:

```yaml

---
# Portal information
name: portal
desc: Reference Portal
version: 0.0.2

# k8s/k3s commands
command: helm install
command-args: --set ingress.enabled=true

# Database commands
seed-command: seed_db.py
init-command: init_db.py
```

You can see the repository for this example "portal" pod here:
[https://github.com/DevOcho/portal] (https://github.com/DevOcho/portal)

`auto` will pull the DevOcho portal repo that is an NginX pod and
start it for you.

To start the cluster with this a single pod you can just run:

```bash
auto start
```

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
