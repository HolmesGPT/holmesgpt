devops-runbooks/cluster/certificate-issues.md at main · Robusta-dev/devops-runbooks · GitHub

[Skip to content](#start-of-content)

You signed in with another tab or window. Reload to refresh your session.
You signed out in another tab or window. Reload to refresh your session.
You switched accounts on another tab or window. Reload to refresh your session.
Dismiss alert

{{ message }}

[Robusta-dev](/Robusta-dev)
/
**[devops-runbooks](/Robusta-dev/devops-runbooks)**
Public

* [Notifications](/login?return_to=%2FRobusta-dev%2Fdevops-runbooks) You must be signed in to change notification settings
* [Fork
  0](/login?return_to=%2FRobusta-dev%2Fdevops-runbooks)
* [Star
   0](/login?return_to=%2FRobusta-dev%2Fdevops-runbooks)

/

certificate-issues.md
=====================

Copy path

Latest commit
-------------

History
-------

[History](/Robusta-dev/devops-runbooks/commits/main/cluster/certificate-issues.md)

215 lines (164 loc) · 6.76 KB

/

certificate-issues.md
=====================

File metadata and controls
--------------------------

215 lines (164 loc) · 6.76 KB

[Raw](https://github.com/Robusta-dev/devops-runbooks/raw/refs/heads/main/cluster/certificate-issues.md)

Troubleshooting Certificate Issues
==================================

Symptoms
--------

* Error messages containing "x509: certificate has expired" or "certificate signed by unknown authority"
* API server connection failures
* `kubectl` commands failing with TLS or certificate errors
* Kubelet showing NotReady with certificate errors in logs
* Components unable to communicate with each other
* etcd reporting peer certificate errors
* Webhook calls failing due to certificate validation errors
* New nodes unable to join the cluster
* Control plane component restarts due to certificate errors

Possible Causes
---------------

1. **Expired Certificates**: Kubernetes certificates have validity periods and expire
2. **Mismatched CA**: Components using certificates signed by different CAs
3. **Incorrect Names**: Subject Alternative Names (SANs) not matching hostnames or IPs
4. **Missing Certificates**: Required certificates missing or not in expected locations
5. **Permission Issues**: Certificate files have incorrect permissions
6. **Clock Skew**: System time significantly different between nodes
7. **Certificate Rotation Failure**: Automated rotation failed to replace certificates
8. **Custom CA Issues**: Custom CA configuration problems
9. **Webhook Certificate Issues**: Admission webhooks with invalid certificates
10. **Kubeconfig Issues**: Invalid certificate data in kubeconfig files

Diagnosis Steps
---------------

### 1. Check Certificate Expiration

```
# For kubeadm clusters, check expiration of all certificates
sudo kubeadm certs check-expiration

# Manually check a specific certificate
sudo openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -dates
```

### 2. Check Certificate Content and SANs

```
# Check certificate Subject and Subject Alternative Names
sudo openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -text | grep -A1 Subject
sudo openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -text | grep -A1 "Subject Alternative Name"
```

### 3. Verify Certificate Chain

```
# Verify certificate is signed by the expected CA
sudo openssl verify -CAfile /etc/kubernetes/pki/ca.crt /etc/kubernetes/pki/apiserver.crt
```

### 4. Check for Certificate Errors in Logs

```
# Check kube-apiserver logs
sudo crictl logs $(sudo crictl ps -a --name kube-apiserver -q)

# Check kubelet logs
sudo journalctl -u kubelet | grep -i certificate

# Check etcd logs
sudo crictl logs $(sudo crictl ps -a --name etcd -q)
```

### 5. Check System Time

```
# Check system time and NTP status
date
timedatectl status
```

### 6. Check Certificate Permissions

```
# Check permissions on certificate files
ls -la /etc/kubernetes/pki/
```

Resolution Steps
----------------

### 1. Renew Expired Certificates

For kubeadm clusters:

```
# Renew all certificates
sudo kubeadm certs renew all

# Renew a specific certificate
sudo kubeadm certs renew apiserver
sudo kubeadm certs renew apiserver-kubelet-client
sudo kubeadm certs renew front-proxy-client
sudo kubeadm certs renew etcd-server
# etc.
```

For non-kubeadm clusters, use your certificate management system or manually regenerate certificates.

### 2. Restart Components After Certificate Renewal

```
# For static pods (in kubeadm), move manifest away and back
sudo mv /etc/kubernetes/manifests/kube-apiserver.yaml /tmp/
sudo mv /etc/kubernetes/manifests/kube-controller-manager.yaml /tmp/
sudo mv /etc/kubernetes/manifests/kube-scheduler.yaml /tmp/
sudo mv /etc/kubernetes/manifests/etcd.yaml /tmp/
sleep 5
sudo mv /tmp/kube-apiserver.yaml /etc/kubernetes/manifests/
sudo mv /tmp/kube-controller-manager.yaml /etc/kubernetes/manifests/
sudo mv /tmp/kube-scheduler.yaml /etc/kubernetes/manifests/
sudo mv /tmp/etcd.yaml /etc/kubernetes/manifests/

# Restart kubelet after certificate renewal
sudo systemctl restart kubelet
```

### 3. Fix Kubelet Client Certificate

If kubelet client certificate has issues:

```
# Backup old certificate and generate new one
sudo mv /var/lib/kubelet/pki/kubelet-client-current.pem /var/lib/kubelet/pki/kubelet-client-current.pem.old
sudo kubeadm init phase kubeconfig kubelet

# Restart kubelet
sudo systemctl restart kubelet
```

### 4. Fix Node Time Synchronization

```
# Install NTP
sudo apt-get update && sudo apt-get install -y ntp  # Debian/Ubuntu
sudo yum install -y ntp  # RHEL/CentOS

# Configure NTP
sudo systemctl enable ntp
sudo systemctl start ntp

# Or use chronyd
sudo systemctl enable chronyd
sudo systemctl start chronyd

# Manually set time if needed
sudo date -s "$(wget -qSO- --max-redirect=0 google.com 2>&1 | grep Date: | cut -d' ' -f5-8)Z"
```

### 5. Fix Certificate Permissions

```
# Set correct permissions for certificate files
sudo chmod 600 /etc/kubernetes/pki/*.key
sudo chmod 644 /etc/kubernetes/pki/*.crt
sudo chown root:root /etc/kubernetes/pki/*
```

### 6. Fix Webhook Certificate Issues

For webhook certificate issues:

```
# Update the caBundle field in the webhook configuration
CA_BUNDLE=$(kubectl config view --raw --minify --flatten -o jsonpath='{.clusters[].cluster.certificate-authority-data}')
kubectl patch mutatingwebhookconfiguration <webhook-name> \
  --type='json' -p="[{'op': 'replace', 'path': '/webhooks/0/clientConfig/caBundle', 'value':'${CA_BUNDLE}'}]"
```

### 7. Regenerate kubeconfig Files

If kubeconfig files have certificate issues:

```
# Backup old config
sudo cp /etc/kubernetes/admin.conf /etc/kubernetes/admin.conf.backup

# Regenerate kubeconfig files with kubeadm
sudo kubeadm init phase kubeconfig all
```

### 8. Full Certificate Recovery (Last Resort)

In the worst case, you may need to completely regenerate all certificates:

```
# Backup existing PKI
sudo cp -r /etc/kubernetes/pki /etc/kubernetes/pki.backup

# Remove problematic certificates
sudo rm /etc/kubernetes/pki/apiserver*
sudo rm /etc/kubernetes/pki/etcd/*

# Regenerate certificates
sudo kubeadm init phase certs all
```

Prevention
----------

1. **Certificate Monitoring**: Set up monitoring for certificate expiration dates
2. **Automated Renewal**: Implement automated certificate renewal
3. **Certificate Management**: Use a certificate management solution like cert-manager
4. **Regular Testing**: Periodically test certificate renewal processes
5. **Documentation**: Document certificate locations, renewal procedures, and expiration dates
6. **Time Synchronization**: Ensure all nodes have synchronized time
7. **Backup Certificates**: Maintain secure backups of certificates and private keys
8. **Longer Validity Periods**: Consider using longer validity periods for certificates
9. **Root CA Protection**: Securely store root CA private keys
10. **Certificate Rotation Strategy**: Plan for regular certificate rotation

Related Runbooks
----------------

* [API Server Unavailable](/Robusta-dev/devops-runbooks/blob/main/cluster/api-server-unavailable.md)
* [Node Not Ready](/Robusta-dev/devops-runbooks/blob/main/cluster/node-not-ready.md)
* [etcd Issues](/Robusta-dev/devops-runbooks/blob/main/cluster/etcd-issues.md)
* [Kubelet Issues](/Robusta-dev/devops-runbooks/blob/main/cluster/kubelet-issues.md)
* [Admission Controller Issues](/Robusta-dev/devops-runbooks/blob/main/cluster/admission-controller-issues.md)

You can’t perform that action at this time.
