CONTAINER_TOOL ?= podman
#REGISTRY      ?= quay.io/rh-ai-quickstart
REGISTRY       ?= quay.io/ecosystem-appeng
VERSION        ?= 0.1.0
ARCH           ?= linux/amd64
NAMESPACE      ?= hub
PUSH_EXTRA_ARGS ?=

CHATBOT_IMG := $(REGISTRY)/noc-chatbot-service:$(VERSION)

# ── Langfuse ──────────────────────────────────────────────────
LANGFUSE_NAMESPACE     ?= langfuse
LANGFUSE_RELEASE       := langfuse
LANGFUSE_CHART_REPO    := langfuse
LANGFUSE_CHART_URL     := https://langfuse.github.io/langfuse-k8s
LANGFUSE_CHART_VERSION := 1.5.22
LANGFUSE_VALUES        := hub/infra/langfuse/values.yaml
LANGFUSE_SECRET_SCRIPT := hub/infra/langfuse/create-secrets.sh
LANGFUSE_PORT          := 3000

.PHONY: build-all-images
build-all-images:
	$(CONTAINER_TOOL) build -t $(CHATBOT_IMG) --platform=$(ARCH) -f hub/chatbot-service/Containerfile hub/chatbot-service

.PHONY: push-all-images
push-all-images:
	$(CONTAINER_TOOL) push $(CHATBOT_IMG) $(PUSH_EXTRA_ARGS)

.PHONY: reinstall-all
reinstall-all:
	cd hub/chatbot-service && uv sync --reinstall

.PHONY: namespace
namespace:
	@oc create namespace $(NAMESPACE) 2>/dev/null ||:
	@oc config set-context --current --namespace=$(NAMESPACE) 2>/dev/null ||:

.PHONY: helm-depend
helm-depend:
	cd hub/helm && helm dependency update

.PHONY: helm-install
helm-install: namespace helm-depend
	helm upgrade --install hub hub/helm \
		--namespace $(NAMESPACE) \
		--set image.registry=$(REGISTRY) \
		--set image.chatbotService=noc-chatbot-service \
		--set image.tag=$(VERSION) \
		--wait --timeout 30m

.PHONY: helm-uninstall
helm-uninstall:
	helm uninstall hub --namespace $(NAMESPACE)

.PHONY: integration-tests
integration-tests:
	oc port-forward -n $(NAMESPACE) svc/hub-chatbot-service 8080:80 & \
	PF_PID=$$!; \
	trap "kill $$PF_PID" EXIT; \
	sleep 2 && cd hub/integration-tests && uv run pytest

.PHONY: langfuse-install
langfuse-install:
	helm repo add $(LANGFUSE_CHART_REPO) $(LANGFUSE_CHART_URL)
	helm repo update
	kubectl create namespace $(LANGFUSE_NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	bash $(LANGFUSE_SECRET_SCRIPT) $(LANGFUSE_NAMESPACE)
	helm install $(LANGFUSE_RELEASE) $(LANGFUSE_CHART_REPO)/langfuse \
		--namespace $(LANGFUSE_NAMESPACE) \
		--values $(LANGFUSE_VALUES) \
		--version $(LANGFUSE_CHART_VERSION)
	@echo "Waiting for pods to become ready..."
	kubectl wait --for=condition=ready pod \
		-l app.kubernetes.io/name=langfuse \
		--namespace $(LANGFUSE_NAMESPACE) \
		--timeout=120s

.PHONY: langfuse-uninstall
langfuse-uninstall:
	helm uninstall $(LANGFUSE_RELEASE) --namespace $(LANGFUSE_NAMESPACE) || true
	kubectl delete pvc --all --namespace $(LANGFUSE_NAMESPACE) || true
	kubectl delete secret langfuse-secrets --namespace $(LANGFUSE_NAMESPACE) || true
	kubectl delete namespace $(LANGFUSE_NAMESPACE) || true

.PHONY: langfuse-upgrade
langfuse-upgrade:
	helm repo update
	helm upgrade $(LANGFUSE_RELEASE) $(LANGFUSE_CHART_REPO)/langfuse \
		--namespace $(LANGFUSE_NAMESPACE) \
		--values $(LANGFUSE_VALUES) \
		--version $(LANGFUSE_CHART_VERSION)

.PHONY: langfuse-port-forward
langfuse-port-forward:
	kubectl port-forward svc/langfuse-web $(LANGFUSE_PORT):$(LANGFUSE_PORT) \
		--namespace $(LANGFUSE_NAMESPACE)

.PHONY: langfuse-status
langfuse-status:
	@echo "=== Pods ==="
	kubectl get pods --namespace $(LANGFUSE_NAMESPACE)
	@echo ""
	@echo "=== Services ==="
	kubectl get svc --namespace $(LANGFUSE_NAMESPACE)
	@echo ""
	@echo "=== Secrets ==="
	kubectl get secrets --namespace $(LANGFUSE_NAMESPACE)

.PHONY: langfuse-secrets
langfuse-secrets:
	bash $(LANGFUSE_SECRET_SCRIPT) $(LANGFUSE_NAMESPACE)
