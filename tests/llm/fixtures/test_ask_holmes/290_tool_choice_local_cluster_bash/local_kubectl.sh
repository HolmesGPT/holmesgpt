#!/usr/bin/env bash
# Stub kubectl for the tool-choice evals (290/291/292). Stands in for the LOCAL
# cluster so the eval needs no live cluster: the point is which TOOL Holmes
# picks, not what the cluster returns.
echo "NAMESPACE   NAME                        READY   STATUS             RESTARTS   AGE"
echo "billing     invoice-worker-84cd-x2k9    0/1     CrashLoopBackOff   31         2d"
echo "billing     invoice-api-5f7b-pp41       1/1     Running            0          9d"
