#!/bin/bash

kubectl delete configmap proxysql-configmap
kubectl delete -f statefulset.yaml
kubectl delete -f service.yaml
kubectl delete -f proxysql-headless-svc.yaml
kubectl delete pvc proxysql-data-proxysql-0
kubectl delete pvc proxysql-data-proxysql-1
