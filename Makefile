# ──────────────────────────────────────────────────────────────
# Project Makefile
# Usage: make help
# ──────────────────────────────────────────────────────────────

CONTAINER_TOOL ?= podman
#REGISTRY      ?= quay.io/rh-ai-quickstart
REGISTRY       ?= quay.io/ecosystem-appeng
VERSION        ?= 0.1.0
ARCH           ?= linux/amd64
NAMESPACE      ?= hub
PUSH_EXTRA_ARGS ?=

CHATBOT_IMG := $(REGISTRY)/noc-chatbot-service:$(VERSION)

# ── Langfuse ──────────────────────────────────────────────────
LANGFUSE_NAMESPACE     := tgolan-langfuse
LANGFUSE_RELEASE       := langfuse
LANGFUSE_CHART_REPO    := langfuse
LANGFUSE_CHART_URL     := https://langfuse.github.io/langfuse-k8s
LANGFUSE_CHART_VERSION := 1.5.22
LANGFUSE_VALUES        := hub/infra/langfuse/values.yaml
LANGFUSE_SECRET_SCRIPT := hub/infra/langfuse/create-secrets.sh
LANGFUSE_PORT          := 3000

.DEFAULT_GOAL := help
.PHONY: help build-all-images push-all-images reinstall-all namespace \
        helm-depend helm-install helm-uninstall integration-tests \
        langfuse-install langfuse-uninstall langfuse-upgrade \
        langfuse-port-forward langfuse-status langfuse-secrets

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-25s\033[0m %s\n", $$1, $$2}'

# ── Build & Push ─────────────────────────────────────────────

build-all-images: ## Build all container images
	$(CONTAINER_TOOL) build -t $(CHATBOT_IMG) --platform=$(ARCH) -f hub/chatbot-service/Containerfile hub/chatbot-service

push-all-images: ## Push all container images
	$(CONTAINER_TOOL) push $(CHATBOT_IMG) $(PUSH_EXTRA_ARGS)

reinstall-all: ## Reinstall all dependencies
	cd hub/chatbot-service && uv sync --reinstall

# ── App Deploy ───────────────────────────────────────────────

namespace: ## Create and switch to app namespace
	@oc create namespace $(NAMESPACE) 2>/dev/null ||:
	@oc config set-context --current --namespace=$(NAMESPACE) 2>/dev/null ||:

helm-depend: ## Update Helm chart dependencies
	cd hub/helm && helm dependency update

helm-install: namespace helm-depend ## Install/upgrade the app via Helm
	helm upgrade --install hub hub/helm \
		--namespace $(NAMESPACE) \
		--set image.registry=$(REGISTRY) \
		--set image.chatbotService=noc-chatbot-service \
		--set image.tag=$(VERSION) \
		--wait --timeout 30m

helm-uninstall: ## Uninstall the app Helm release
	helm uninstall hub --namespace $(NAMESPACE)

integration-tests: ## Run integration tests (port-forward + pytest)
	oc port-forward -n $(NAMESPACE) svc/hub-chatbot-service 8080:80 & \
	PF_PID=$$!; \
	trap "kill $$PF_PID" EXIT; \
	sleep 2 && cd hub/integration-tests && uv run pytest

# ── Langfuse targets ─────────────────────────────────────────

langfuse-install: ## Deploy Langfuse (repo, namespace, secrets, helm install, wait)
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

langfuse-uninstall: ## Teardown Langfuse (helm, PVCs, secrets, namespace)
	helm uninstall $(LANGFUSE_RELEASE) --namespace $(LANGFUSE_NAMESPACE) || true
	kubectl delete pvc --all --namespace $(LANGFUSE_NAMESPACE) || true
	kubectl delete secret langfuse-secrets --namespace $(LANGFUSE_NAMESPACE) || true
	kubectl delete namespace $(LANGFUSE_NAMESPACE) || true

langfuse-upgrade: ## Upgrade Langfuse Helm release
	helm repo update
	helm upgrade $(LANGFUSE_RELEASE) $(LANGFUSE_CHART_REPO)/langfuse \
		--namespace $(LANGFUSE_NAMESPACE) \
		--values $(LANGFUSE_VALUES) \
		--version $(LANGFUSE_CHART_VERSION)

langfuse-port-forward: ## Forward localhost:3000 -> langfuse-web
	kubectl port-forward svc/langfuse-web $(LANGFUSE_PORT):$(LANGFUSE_PORT) \
		--namespace $(LANGFUSE_NAMESPACE)

langfuse-status: ## Show Langfuse pods, services, and secrets
	@echo "=== Pods ==="
	kubectl get pods --namespace $(LANGFUSE_NAMESPACE)
	@echo ""
	@echo "=== Services ==="
	kubectl get svc --namespace $(LANGFUSE_NAMESPACE)
	@echo ""
	@echo "=== Secrets ==="
	kubectl get secrets --namespace $(LANGFUSE_NAMESPACE)

langfuse-secrets: ## Regenerate langfuse-secrets (rotate credentials)
	bash $(LANGFUSE_SECRET_SCRIPT) $(LANGFUSE_NAMESPACE)
