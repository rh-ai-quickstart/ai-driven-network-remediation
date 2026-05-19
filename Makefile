CONTAINER_TOOL  ?= podman
REGISTRY        ?= quay.io/rh-ai-quickstart
VERSION         ?= 0.1.0
ARCH            ?= linux/amd64
NAMESPACE       ?= hub
RELEASE         ?= hub
PUSH_EXTRA_ARGS ?=
ROUTES_ENABLED  ?= true

CHATBOT_IMG    := $(REGISTRY)/noc-chatbot-service:$(VERSION)
INGESTION_IMG  := $(REGISTRY)/noc-ingestion-pipeline:$(VERSION)

# ── Langfuse (optional: ENABLE_LANGFUSE=true) ───────────────────
ENABLE_LANGFUSE        ?=
LANGFUSE_RELEASE       := langfuse
LANGFUSE_CHART_REPO    := langfuse
LANGFUSE_CHART_URL     := https://langfuse.github.io/langfuse-k8s
LANGFUSE_CHART_VERSION := 1.5.22
LANGFUSE_VALUES        := hub/infra/langfuse/values.yaml
LANGFUSE_SECRET_SCRIPT := hub/infra/langfuse/create-secrets.sh
LANGFUSE_PORT          := 3000

# ── Kafka (optional: ENABLE_KAFKA=true) ─────────────────────────
ENABLE_KAFKA           ?=
ENABLE_KAFKA_UI        ?= true
KAFKA_RELEASE          := kafka
KAFKA_VALUES           := hub/infra/kafka/values.yaml
KAFKA_PORT             := 9092
KAFKA_HELM_EXTRA_ARGS  ?=

.PHONY: build-all-images
build-all-images:
	$(CONTAINER_TOOL) build -t $(CHATBOT_IMG) --platform=$(ARCH) -f hub/chatbot-service/Containerfile hub/chatbot-service
	$(CONTAINER_TOOL) build -t $(INGESTION_IMG) --platform=$(ARCH) -f hub/ingestion-pipeline/Containerfile hub/ingestion-pipeline

.PHONY: push-all-images
push-all-images:
	$(CONTAINER_TOOL) push $(CHATBOT_IMG) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(INGESTION_IMG) $(PUSH_EXTRA_ARGS)

.PHONY: reinstall-all
reinstall-all:
	cd hub/chatbot-service && uv sync --reinstall
	cd hub/ingestion-pipeline && uv sync --reinstall

.PHONY: namespace
namespace:
	@oc create namespace $(NAMESPACE) 2>/dev/null ||:
	@oc config set-context --current --namespace=$(NAMESPACE) 2>/dev/null ||:

.PHONY: helm-depend
helm-depend:
	cd hub/helm && helm dependency update

.PHONY: helm-install
helm-install: namespace helm-depend
	helm upgrade --install $(RELEASE) hub/helm \
		--namespace $(NAMESPACE) \
		--set image.registry=$(REGISTRY) \
		--set image.chatbotService=noc-chatbot-service \
		--set image.ingestionPipeline=noc-ingestion-pipeline \
		--set global.routes.enabled=$(ROUTES_ENABLED) \
		--set image.tag=$(VERSION) \
		$(HELM_EXTRA_ARGS) \
		--wait --timeout 30m
ifeq ($(ENABLE_LANGFUSE),true)
	$(MAKE) _langfuse-deploy
endif
ifeq ($(ENABLE_KAFKA),true)
	$(MAKE) kafka-install
endif

.PHONY: helm-uninstall
helm-uninstall:
	helm uninstall $(RELEASE) --namespace $(NAMESPACE) --ignore-not-found
ifeq ($(ENABLE_LANGFUSE),true)
	helm uninstall $(LANGFUSE_RELEASE) --namespace $(NAMESPACE) --ignore-not-found
	oc delete pvc -l app.kubernetes.io/instance=$(LANGFUSE_RELEASE) --namespace $(NAMESPACE) --ignore-not-found
	oc delete secret langfuse-secrets --namespace $(NAMESPACE) --ignore-not-found
endif
ifeq ($(ENABLE_KAFKA),true)
	$(MAKE) kafka-uninstall
endif

.PHONY: _langfuse-deploy
_langfuse-deploy:
	helm repo add $(LANGFUSE_CHART_REPO) $(LANGFUSE_CHART_URL) || true
	helm repo update
	bash $(LANGFUSE_SECRET_SCRIPT) $(NAMESPACE)
	helm upgrade --install $(LANGFUSE_RELEASE) $(LANGFUSE_CHART_REPO)/langfuse \
		--namespace $(NAMESPACE) \
		--values $(LANGFUSE_VALUES) \
		--version $(LANGFUSE_CHART_VERSION) \
		--wait --timeout 10m

.PHONY: integration-tests
integration-tests:
	oc port-forward -n $(NAMESPACE) svc/hub-chatbot-service 8080:80 & \
	PF1_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/hub-ingestion-pipeline 8000:8000 & \
	PF2_PID=$$!; \
	trap "kill $$PF1_PID $$PF2_PID" EXIT; \
	sleep 2 && cd hub/integration-tests && uv run pytest

# ── Langfuse day-2 targets ───────────────────────────────────────

.PHONY: langfuse-upgrade
langfuse-upgrade:
	helm repo update
	helm upgrade $(LANGFUSE_RELEASE) $(LANGFUSE_CHART_REPO)/langfuse \
		--namespace $(NAMESPACE) \
		--values $(LANGFUSE_VALUES) \
		--version $(LANGFUSE_CHART_VERSION)

.PHONY: langfuse-port-forward
langfuse-port-forward:
	oc port-forward svc/langfuse-web $(LANGFUSE_PORT):$(LANGFUSE_PORT) \
		--namespace $(NAMESPACE)

# ── Kafka targets ────────────────────────────────────────────────
# Before production: review hub/infra/kafka/README.md and search for "TODO: PRODUCTION:" in values.yaml

.PHONY: kafka-install
kafka-install:
	helm upgrade --install $(KAFKA_RELEASE) hub/infra/kafka \
		--namespace $(NAMESPACE) \
		--values $(KAFKA_VALUES) \
		--set kafkaUI.enabled=$(ENABLE_KAFKA_UI) \
		--set kafka.externalRoute.enabled=$(ROUTES_ENABLED) \
		--set kafkaUI.route.enabled=$(ROUTES_ENABLED) \
		$(KAFKA_HELM_EXTRA_ARGS)

.PHONY: kafka-uninstall
kafka-uninstall:
	helm uninstall $(KAFKA_RELEASE) --namespace $(NAMESPACE) --ignore-not-found
	oc delete secret kafka-tls kafka-client-tls --namespace $(NAMESPACE) --ignore-not-found
	oc delete job kafka-create-topics --namespace $(NAMESPACE) --ignore-not-found
	oc delete pvc -l app=kafka --namespace $(NAMESPACE) --ignore-not-found

.PHONY: kafka-port-forward
kafka-port-forward:
	oc port-forward svc/kafka $(KAFKA_PORT):$(KAFKA_PORT) \
		--namespace $(NAMESPACE)

.PHONY: kafka-client-cert
kafka-client-cert:
	@oc get secret kafka-client-tls -n $(NAMESPACE) -o jsonpath='{.data.ca\.crt}' | base64 -d > ca.crt
	@oc get secret kafka-client-tls -n $(NAMESPACE) -o jsonpath='{.data.client\.crt}' | base64 -d > client.crt
	@oc get secret kafka-client-tls -n $(NAMESPACE) -o jsonpath='{.data.client\.key}' | base64 -d > client.key
	@echo "Extracted: ca.crt, client.crt, client.key"

.PHONY: langfuse-status
langfuse-status:
	@echo "=== Pods ==="
	oc get pods -l app.kubernetes.io/instance=$(LANGFUSE_RELEASE) --namespace $(NAMESPACE)
	@echo ""
	@echo "=== Services ==="
	oc get svc -l app.kubernetes.io/instance=$(LANGFUSE_RELEASE) --namespace $(NAMESPACE)
	@echo ""
	@echo "=== Secrets ==="
	oc get secret langfuse-secrets --namespace $(NAMESPACE) 2>/dev/null || echo "(none)"
