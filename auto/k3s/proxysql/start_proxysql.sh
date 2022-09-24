#!/bin/bash

kubectl create configmap proxysql-configmap --from-file=proxysql.cnf
kubectl create -f statefulset.yaml
kubectl create -f service.yaml
kubectl create -f proxysql-headless-svc.yaml
