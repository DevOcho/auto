# `auto`
Easily manage your k3s/k3d local development environment using k8s yaml configs or helm charts.

`auto` sets up a k3s environment utilizing k3d (k3s in docker).  It then uses a single config file
to create your environment.

## Install `auto`

### Dependencies
You will need a Linux system with the following pre-installed:
- Bash (`auto` uses Bash commands)
- Git
- Python 3
- Docker (both the daemon running and the bash command available as a non-root user)
- K3D (k3d.io)
- Kubectl

Optional dependencies:
- Helm (if you plan to use helm charts for deployments)

### Install Commands

NOTE: `auto` is installed for a user and not installed system wide.
You can install it with the following commands:

```bash
git clone git@github.com:DevOcho/auto.git
cd auto
make && make install
```

The `auto` install will update your `~/.bashrc` file to add itself to your path environment
variable.  For that change to take effect you will need to run `source ~/.bashrc` in each
open terminal or restart your terminals.

If you are using a shell other than Bash, you will want to add the following to your path

```
# Adding auto to the path
export PATH="$PATH:/home/$USER/.auto"

# Auto Code Directory
export AUTO_CODE=/home/rogue/source/devocho/auto
```


## Quickstart

Once you've installed `auto` you can get up and running with the following steps:

### Edit the ~/.auto/config/local.toml file

The `local.toml` file tells auto about your desired local environment.  `auto` checks the `[pods]` section
to see which portals and services you want to run in pods in your local k3s cluster.

Here is an example of a web application pod using a helm chart:

```toml

[pods]

    [pods.portal]
    # helm install --set ingress.enabled=true --description "Portal" portal portal/
    name = "portal"
    desc = "Portal"
    image = "portal:1.0.0"
    repo = "git@github.com:DevOcho/portal.git"
    command = "helm install"
    command_args = "--set ingress.enabled=true"
```

Once you have the config file ready you can run:

```bash
auto start
```

## Setting up your application to run in auto

`auto` assumes a microservices environment (but doesn't specifically require it).  With that assumption, we need
each pod to contain the configs needed to run it.  Each pod needs a git code repository.  We will look for
the following files/folders in your repo.

```
/Dockerfile
/auto/k8s   (if using k8s yaml)
/auto/helm  (if using helm charts)
/migrations (database related items [more on this later])

## Usage

Need to write this but for now just use the `auto -h` inline help docs.
