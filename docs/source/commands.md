# Commands

## General Commands

### auto [--help]

List available commands and show usage options.

### auto start [OPTIONS] [PORTAL]

Start your development environment or start a specific pod.

#### Options for `auto start`

##### --dry-run

Don't actually start anything, just output what you would have done.
This is primarily used for unit testing.

### auto stop [PORTAL]

Stop the entire cluster or stop a single pod.

#### Options for `auto stop`

##### --delete-cluster

This command destroys the cluster.
NOTE: It does not destroy the image repository.

### auto logs PORTAL

View the k3s pod logs for a portal or service.

Portals and Services (e.x. APIs) run in k3s pods.  You can access those via the `kubectl logs -f PORTAL`
command but then you have to first run `kubectl get pods` or have kubectl autocompletion installed for Bash.
This command looks up the pod name and runs the command for you.

### auto restart PORTAL

This command restarts your portal by first issuing a stop

### auto tag PORTAL VERSION

This helper command will make a new version of your pod's docker image, tag it, and upload it to the local
k3s image repository.

## Database Utilities

Auto comes with support for managing local databases that run inside the k3s cluster.  The
following commands are focused on database access and operations.

### auto mysql

Uses a portal to connect you to the MySQL database running inside the cluster.

### auto initdb PORTAL

Load the initial data into the database that your portal needs to function.  For example, if the
portal is a web application, you might populate default data that is loaded in drop down menus.

### auto seed PORTAL

Load test data into your database so you can do development work or run automated tests.

### auto migrate PORTAL

Run database migrations that align with your portal version.

This script will look at the application version in `migrations/[database]/versions.yaml` and then
run the corresponding migration scripts, in order, until the migration number matches the goal
in versions.yaml.

For example:

> auto migrate foo-api
