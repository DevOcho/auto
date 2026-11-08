# Security policy

## Reporting a problem

Please report security problems privately, through GitHub:
[report a vulnerability](https://github.com/DevOcho/auto/security/advisories/new)
(the repo's Security tab). Don't open a public issue for them.

We'll reply within a week, and keep you posted while it's fixed.

## What counts

auto sets up a development environment on your own machine. These are by
design and not vulnerabilities:

- The system pods' credentials (`root` / `password` for the databases,
  `minio` / `minio123` for MinIO) are fixed and the same everywhere, so every
  developer's config works on every cluster.
- The local registry accepts pushes and pulls without authentication.

Docker publishes the cluster's ports (80 and 443, the database ports, the
registry on 12345 and the Kubernetes API on 6550) on all of your machine's
network interfaces, not only to `localhost`, and on Linux these bypass ufw.
On a network you don't trust, stop the cluster with `auto stop`.

Problems we do want to hear about include: anything that exposes a cluster or
its data beyond your machine, the installer or `auto update` installing
something other than an official release, and commands that run with
untrusted input from a repo you didn't intend to trust.

## Supported versions

Fixes go into the latest release. Run `auto update` to get it.
