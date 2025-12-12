kubectl apply -f instrumentation.yaml -n newrelic

kubectl create namespace app-117

kubectl -n app-117 create secret generic checkout-src \
  --from-file=server.js=checkout/server.js \
  --from-file=package.json=checkout/package.json

kubectl -n app-117 create secret generic inventory-src \
  --from-file=server.js=inventory/server.js \
  --from-file=package.json=inventory/package.json

kubectl -n app-117 create secret generic risk-src \
  --from-file=app.py=risk/app.py \
  --from-file=requirements.txt=risk/requirements.txt

kubectl apply -f db.yaml

kubectl apply -f manifest.yaml

# Wait for all deployments in app-117 namespace to be ready
echo "Waiting for deployments to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment --all -n app-117

# Wait for postgres statefulset to be ready
echo "Waiting for postgres statefulset to be ready..."
kubectl wait --for=condition=ready --timeout=300s pod -l app=postgres -n app-117

echo "All resources are ready!"
