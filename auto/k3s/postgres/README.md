Following this guide: https://www.digitalocean.com/community/tutorials/how-to-deploy-postgres-to-kubernetes-cluster

# Install

Run the following commands:

```bash
kubectl create namespace postgres
kubectl apply -f configmap.yaml
kubectl apply -f pv.yaml
kubectl apply -f pvc.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

# Backup

This probably requires the password but it wasn't mentioned in the tutorial.

```bash
kubectl exec -it postgres-665b7554dc-cddgq -- pg_dump -U ps_user -d ps_db > db_backup.sql
```

# Restore

```bash
kubectl cp db_backup.sql postgres-665b7554dc-cddgq:/tmp/db_backup.sql
kubectl exec -it postgres-665b7554dc-cddgq -- /bin/bash
psql -U ps_user -d ps_db -f /tmp/db_backup.sql
```
