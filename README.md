# `auto`
Easily manage your k3s/k3d local development environment using k8s yaml configs or helm charts.

`auto` sets up a k3s environment utilizing k3d (k3s in docker).  It then uses a
single config file to create your environment.  At DevOcho we have the goal
of a one command start up for a developer joining a project.  `auto` helps us
achieve that goal.

## Todo List

- [ ] Create `auto status` to show where things are
- [ ] Create `auto update` to update to the latest version of auto

## Install `auto`

### Dependencies
You will need a Linux system with the following pre-installed:
- Bash (`auto` uses Bash commands)
- Git
- Python 3
- Docker (both the daemon running and the bash command available as a non-root user)
- K3D (k3d.io)
- kubectl

Optional dependencies:
- Helm (if you plan to use helm charts for deployments)

### Install Commands

NOTE: `auto` is installed for a user and not installed system wide.
You can install it with the following commands:

```bash
git clone git@github.com:DevOcho/auto.git
cd auto
./install_auto.sh
```

The `auto` install will update your `~/.bashrc` file to add itself to your path
environment variable.  For that change to take effect you will need to run
`source ~/.bashrc` in each open terminal or restart your terminals.

If you are using a shell other than Bash, you will want to add the following
to your path:

```bash
# Adding auto to the path
export PATH="$PATH:/home/$USER/.auto"

# Auto Code Directory
export AUTO_CODE=/home/rogue/source/devocho/auto
```

## Quickstart

Once you've installed `auto` you can get up and running with the following steps:

### Edit the `~/.auto/config/local.yaml`` file

The `local.yaml` file tells auto about your desired local environment.
`auto` checks the `[pods]` section to see which portals and services
you want to run in pods in your local k3s cluster.  We assume each pod
is in it's own separate git repository.

Below is an example we created to show you how to setup a pod.

```yaml
pods:
  - repo: git@github.com:DevOcho/portal.git
    branch: main
```

### Setting up your application to run in auto

`auto` assumes a microservices environment (but doesn't specifically require
it).  With that assumption, we need each pod to contain the configs needed
to run it.  Each pod is it's own unique git code repository.  We will look
for the following files/folders in your repo:

```
/Dockerfile
/.auto/config.yaml (explained below)
/.auto/k8s   (if using k8s yaml)
/.auto/helm  (if using helm charts)
```

In your pod you will need a `.auto` folder that contains a `config.yaml``
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

You can see an example of this portal here:
[https://github.com/DevOcho/portal] (https://github.com/DevOcho/portal)

Once you have the config files ready, you can start the cluster and pods with the following command:

```bash
auto start
```

## Usage

You can get basic help by running `auto --help` or by reading the official
docs.
