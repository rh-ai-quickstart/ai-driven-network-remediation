CONTAINER_TOOL  ?= podman
REGISTRY        ?= quay.io/rh-ai-quickstart
VERSION         ?= 0.1.0
ARCH            ?= linux/amd64
NAMESPACE       ?= hub
EDGE_NAMESPACE  ?= $(NAMESPACE) # TODO: Change to dark-noc-edge
RELEASE         ?= hub
PUSH_EXTRA_ARGS ?=
ROUTES_ENABLED  ?= true

CHATBOT_IMG        := $(REGISTRY)/noc-chatbot-service:$(VERSION)
INGESTION_IMG      := $(REGISTRY)/noc-ingestion-pipeline:$(VERSION)
AGENT_IMG          := $(REGISTRY)/noc-agent-service:$(VERSION)
MCP_OPENSHIFT_IMG  := $(REGISTRY)/noc-mcp-openshift:$(VERSION)
MCP_LOKISTACK_IMG  := $(REGISTRY)/noc-mcp-lokistack:$(VERSION)
MCP_KAFKA_IMG      := $(REGISTRY)/noc-mcp-kafka:$(VERSION)
MCP_AAP_IMG        := $(REGISTRY)/noc-mcp-aap:$(VERSION)
MCP_SLACK_IMG      := $(REGISTRY)/noc-mcp-slack:$(VERSION)
MCP_SERVICENOW_IMG := $(REGISTRY)/noc-mcp-servicenow:$(VERSION)

MCP_CONTAINERFILE           := hub/mcp-servers/Containerfile
MCP_OPENSHIFT_CONTAINERFILE := hub/mcp-servers/Containerfile.openshift
MCP_CONTEXT                 := hub/mcp-servers

# ── Hub (optional: ENABLE_HUB=true) ──────────────────────────────
ENABLE_HUB             ?= true

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
ENABLE_KAFKA           ?= true
ENABLE_KAFKA_UI        ?= false
KAFKA_RELEASE          := kafka
KAFKA_VALUES           := hub/infra/kafka/values.yaml
KAFKA_PORT             := 9092
KAFKA_HELM_EXTRA_ARGS  ?=

# ── LokiStack (optional: ENABLE_LOKISTACK=true) ─────────────────
ENABLE_LOKISTACK       ?= false
ENABLE_LOKISTACK_TEST  ?= false
LOKISTACK_RELEASE      := lokistack
LOKISTACK_CHART        := hub/infra/lokistack/chart
LOKISTACK_NAMESPACE    ?= $(NAMESPACE)
LOKISTACK_NAME         ?= logging-loki
LOKISTACK_EXTRA        ?=

# ── MinIO ────────────────────────────────────────────────────────
ENABLE_MINIO           ?= true
MINIO_RELEASE          := minio
MINIO_CHART            := hub/infra/minio
MINIO_NAMESPACE        ?= $(NAMESPACE)
MINIO_PORT             ?= 9000
MINIO_HELM_EXTRA_ARGS  ?=

# ── AAP Mock (optional: ENABLE_AAP_MOCK=true) ──────────────────
ENABLE_AAP_MOCK        ?= false
AAP_MOCK_IMG           := $(REGISTRY)/aap-mock:$(VERSION)

# ── ServiceNow Mock ──────────────────────────────────────────────
ENABLE_SERVICENOW_MOCK ?= true
SERVICENOW_MOCK_IMG    := $(REGISTRY)/servicenow-mock:$(VERSION)

ADNR_LLM_ENABLED := $(and $(ADNR_LLM_ID),$(ADNR_LLM_URL),$(ADNR_LLM_TOKEN))

helm_adnr_llm_args = \
	$(if $(ADNR_LLM_ENABLED),--set llama-stack.models.adnr-llm.enabled=true,) \
	$(if $(ADNR_LLM_ENABLED),--set-string llama-stack.models.adnr-llm.id='$(ADNR_LLM_ID)',) \
	$(if $(ADNR_LLM_ENABLED),--set-string llama-stack.models.adnr-llm.url='$(ADNR_LLM_URL)',) \
	$(if $(ADNR_LLM_ENABLED),--set-string llama-stack.models.adnr-llm.apiToken='$(ADNR_LLM_TOKEN)',)

helm_mcp_image_args = \
	--set mcp-servers.mcp-servers.noc-openshift.image.repository=$(REGISTRY)/noc-mcp-openshift \
	--set mcp-servers.mcp-servers.noc-openshift.image.tag=$(VERSION) \
	--set mcp-servers.mcp-servers.noc-lokistack.image.repository=$(REGISTRY)/noc-mcp-lokistack \
	--set mcp-servers.mcp-servers.noc-lokistack.image.tag=$(VERSION) \
	--set mcp-servers.mcp-servers.noc-kafka.image.repository=$(REGISTRY)/noc-mcp-kafka \
	--set mcp-servers.mcp-servers.noc-kafka.image.tag=$(VERSION) \
	--set mcp-servers.mcp-servers.noc-aap.image.repository=$(REGISTRY)/noc-mcp-aap \
	--set mcp-servers.mcp-servers.noc-aap.image.tag=$(VERSION) \
	--set mcp-servers.mcp-servers.noc-slack.image.repository=$(REGISTRY)/noc-mcp-slack \
	--set mcp-servers.mcp-servers.noc-slack.image.tag=$(VERSION) \
	--set mcp-servers.mcp-servers.noc-servicenow.image.repository=$(REGISTRY)/noc-mcp-servicenow \
	--set mcp-servers.mcp-servers.noc-servicenow.image.tag=$(VERSION)

helm_aap_mock_args = \
	$(if $(filter true,$(ENABLE_AAP_MOCK)),--set mcp-servers.mcp-servers.noc-aap.env.AAP_URL=http://aap-mock.$(NAMESPACE).svc:8080,) \
	$(if $(filter true,$(ENABLE_AAP_MOCK)),--set mcp-servers.mcp-servers.noc-aap.env.AAP_VERIFY_SSL=false,)

helm_servicenow_mock_args = \
	$(if $(filter true,$(ENABLE_SERVICENOW_MOCK)),--set mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_URL=http://servicenow-mock.$(NAMESPACE).svc:8080,) \
	$(if $(filter true,$(ENABLE_SERVICENOW_MOCK)),--set mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_MODE=mock,) \
	$(if $(filter true,$(ENABLE_SERVICENOW_MOCK)),--set-string mcpSecrets.servicenow.apiKey=demo-api-key-2026,)

.PHONY: build-all-images
build-all-images: build-chatbot-image build-agent-image build-mcp-images

.PHONY: build-chatbot-image
build-chatbot-image:
	$(CONTAINER_TOOL) build -t $(CHATBOT_IMG) --platform=$(ARCH) -f hub/chatbot-service/Containerfile hub/chatbot-service
	$(CONTAINER_TOOL) build -t $(INGESTION_IMG) --platform=$(ARCH) -f hub/ingestion-pipeline/Containerfile hub/ingestion-pipeline

.PHONY: build-agent-image
build-agent-image:
	$(CONTAINER_TOOL) build -t $(AGENT_IMG) --platform=$(ARCH) -f hub/agent-service/Containerfile hub/agent-service

.PHONY: build-mcp-images
build-mcp-images:
	$(CONTAINER_TOOL) build -t $(MCP_OPENSHIFT_IMG)  --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-openshift  --build-arg MODULE_NAME=mcp_openshift  -f $(MCP_OPENSHIFT_CONTAINERFILE) $(MCP_CONTEXT)
	$(CONTAINER_TOOL) build -t $(MCP_LOKISTACK_IMG)  --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-lokistack  --build-arg MODULE_NAME=mcp_lokistack  -f $(MCP_CONTAINERFILE) $(MCP_CONTEXT)
	$(CONTAINER_TOOL) build -t $(MCP_KAFKA_IMG)      --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-kafka      --build-arg MODULE_NAME=mcp_kafka      -f $(MCP_CONTAINERFILE) $(MCP_CONTEXT)
	$(CONTAINER_TOOL) build -t $(MCP_AAP_IMG)        --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-aap        --build-arg MODULE_NAME=mcp_aap        -f $(MCP_CONTAINERFILE) $(MCP_CONTEXT)
	$(CONTAINER_TOOL) build -t $(MCP_SLACK_IMG)      --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-slack      --build-arg MODULE_NAME=mcp_slack      -f $(MCP_CONTAINERFILE) $(MCP_CONTEXT)
	$(CONTAINER_TOOL) build -t $(MCP_SERVICENOW_IMG) --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-servicenow --build-arg MODULE_NAME=mcp_servicenow -f $(MCP_CONTAINERFILE) $(MCP_CONTEXT)

.PHONY: push-all-images
push-all-images:
	$(CONTAINER_TOOL) push $(CHATBOT_IMG) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(INGESTION_IMG) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(AGENT_IMG) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(MCP_OPENSHIFT_IMG) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(MCP_LOKISTACK_IMG) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(MCP_KAFKA_IMG) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(MCP_AAP_IMG) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(MCP_SLACK_IMG) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(MCP_SERVICENOW_IMG) $(PUSH_EXTRA_ARGS)

.PHONY: build-push-aap-mock
build-push-aap-mock:
	$(CONTAINER_TOOL) build -t $(AAP_MOCK_IMG) --platform=$(ARCH) -f hub/infra/aap-mock/Containerfile hub/infra/aap-mock
	$(CONTAINER_TOOL) push $(AAP_MOCK_IMG) $(PUSH_EXTRA_ARGS)

.PHONY: deploy-aap-mock
deploy-aap-mock: namespace
	oc apply -n $(NAMESPACE) -f hub/infra/aap-mock/k8s.yaml
	oc set image -n $(NAMESPACE) deployment/aap-mock aap-mock=$(AAP_MOCK_IMG)
	oc rollout status -n $(NAMESPACE) deployment/aap-mock --timeout=60s

.PHONY: build-push-servicenow-mock
build-push-servicenow-mock:
	$(CONTAINER_TOOL) build -t $(SERVICENOW_MOCK_IMG) --platform=$(ARCH) -f hub/infra/servicenow-mock/Containerfile hub/infra/servicenow-mock
	$(CONTAINER_TOOL) push $(SERVICENOW_MOCK_IMG) $(PUSH_EXTRA_ARGS)

.PHONY: deploy-servicenow-mock
deploy-servicenow-mock: namespace
	oc apply -n $(NAMESPACE) -f hub/infra/servicenow-mock/k8s.yaml
	oc set image -n $(NAMESPACE) deployment/servicenow-mock servicenow-mock=$(SERVICENOW_MOCK_IMG)
	oc rollout status -n $(NAMESPACE) deployment/servicenow-mock --timeout=60s

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
ifeq ($(ENABLE_MINIO),true)
	$(MAKE) minio-install
endif
ifeq ($(ENABLE_AAP_MOCK),true)
	$(MAKE) deploy-aap-mock
endif
ifeq ($(ENABLE_LOKISTACK),true)
	$(MAKE) lokistack-install
endif
ifeq ($(ENABLE_SERVICENOW_MOCK),true)
	$(MAKE) deploy-servicenow-mock
endif
ifeq ($(ENABLE_HUB),true)
	@oc get secret noc-openshift-edge-kubeconfig -n $(NAMESPACE) > /dev/null 2>&1 || \
		hub/mcp-servers/mcp-openshift/deploy/setup-edge-rbac.sh $(EDGE_NAMESPACE) $(NAMESPACE)
	helm upgrade --install $(RELEASE) hub/helm \
		--namespace $(NAMESPACE) \
		--set image.registry=$(REGISTRY) \
		--set image.chatbotService=noc-chatbot-service \
		--set image.ingestionPipeline=noc-ingestion-pipeline \
		--set image.agentService=noc-agent-service \
		--set global.routes.enabled=$(ROUTES_ENABLED) \
		--set image.tag=$(VERSION) \
		$(helm_mcp_image_args) \
		--set-string mcp-servers.mcp-servers.noc-openshift.env.DEFAULT_NAMESPACE='$(EDGE_NAMESPACE)' \
		--set mcp-servers.mcp-servers.noc-lokistack.enabled=$(ENABLE_LOKISTACK) \
		--set-string lokistack.name='$(LOKISTACK_NAME)' \
		--set-string lokistack.namespace='$(LOKISTACK_NAMESPACE)' \
		$(helm_adnr_llm_args) \
		$(helm_aap_mock_args) \
		$(helm_servicenow_mock_args) \
		$(HELM_EXTRA_ARGS) \
		--wait --timeout 30m
else
	@echo "ENABLE_HUB is not true — skipping hub chart deployment"
endif
ifeq ($(ENABLE_LANGFUSE),true)
	$(MAKE) _langfuse-deploy
endif
ifeq ($(ENABLE_KAFKA),true)
	$(MAKE) kafka-install
endif

.PHONY: helm-uninstall
helm-uninstall:
ifeq ($(ENABLE_HUB),true)
	helm uninstall $(RELEASE) --namespace $(NAMESPACE) --ignore-not-found
	oc delete pvc pg-data-pgvector-0 --namespace $(NAMESPACE) --ignore-not-found
ifeq ($(ENABLE_LOKISTACK),true)
	$(MAKE) lokistack-uninstall
endif
ifeq ($(ENABLE_MINIO),true)
	$(MAKE) minio-uninstall
endif
ifeq ($(ENABLE_LANGFUSE),true)
	helm uninstall $(LANGFUSE_RELEASE) --namespace $(NAMESPACE) || true
	oc delete pvc -l app.kubernetes.io/instance=$(LANGFUSE_RELEASE) --namespace $(NAMESPACE) || true
	oc delete secret langfuse-secrets --namespace $(NAMESPACE) || true
	helm uninstall $(LANGFUSE_RELEASE) --namespace $(NAMESPACE) --ignore-not-found
	oc delete pvc -l app.kubernetes.io/instance=$(LANGFUSE_RELEASE) --namespace $(NAMESPACE) --ignore-not-found
	oc delete secret langfuse-secrets --namespace $(NAMESPACE) --ignore-not-found
endif
ifeq ($(ENABLE_KAFKA),true)
	$(MAKE) kafka-uninstall
endif
ifeq ($(ENABLE_AAP_MOCK),true)
	oc delete -n $(NAMESPACE) -f hub/infra/aap-mock/k8s.yaml --ignore-not-found
endif
ifeq ($(ENABLE_SERVICENOW_MOCK),true)
	oc delete -n $(NAMESPACE) -f hub/infra/servicenow-mock/k8s.yaml --ignore-not-found
endif
endif

.PHONY: edge-rbac-teardown
edge-rbac-teardown:
	sed 's/EDGE_NAMESPACE_PLACEHOLDER/$(EDGE_NAMESPACE)/g' hub/mcp-servers/mcp-openshift/deploy/edge-rbac.yaml \
		| oc delete -n $(EDGE_NAMESPACE) --ignore-not-found -f -
	oc delete secret noc-openshift-edge-kubeconfig -n $(NAMESPACE) --ignore-not-found

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

.PHONY: _check-loki-operator
_check-loki-operator:
	@oc get csv -A 2>/dev/null | grep -q "loki-operator" || \
		{ echo ""; \
		  echo "ERROR: Loki Operator is not installed on this cluster."; \
		  echo ""; \
		  echo "The LokiStack requires the Loki Operator to be installed first."; \
		  echo ""; \
		  echo "To install the Loki Operator:"; \
		  echo "  1. In the OpenShift web console, navigate to:"; \
		  echo "     Operators → OperatorHub"; \
		  echo "  2. Search for 'Loki Operator'"; \
		  echo "  3. Select 'Loki Operator' (provided by Red Hat)"; \
		  echo "  4. Click 'Install' and follow the installation wizard"; \
		  echo "  5. Choose installation mode (all namespaces recommended)"; \
		  echo "  6. Wait for the operator to become ready"; \
		  echo ""; \
		  exit 1; }

.PHONY: _check-minio
_check-minio:
	@oc get statefulset $(MINIO_RELEASE) -n $(MINIO_NAMESPACE) -o jsonpath='{.status.readyReplicas}' 2>/dev/null | grep -qE '^[1-9]' || \
		{ echo "ERROR: MinIO is not running in namespace '$(MINIO_NAMESPACE)'. Run 'make minio-install' first."; exit 1; }

.PHONY: lokistack-install
lokistack-install: _check-loki-operator _check-minio
	helm upgrade --install $(LOKISTACK_RELEASE) $(LOKISTACK_CHART) \
		--namespace $(LOKISTACK_NAMESPACE) \
		--set-string lokistack.name='$(LOKISTACK_NAME)' \
		--set testLogGenerator.enabled=$(ENABLE_LOKISTACK_TEST) \
		$(LOKISTACK_EXTRA) \
		--wait --timeout 15m

.PHONY: lokistack-uninstall
lokistack-uninstall:
	helm uninstall $(LOKISTACK_RELEASE) --namespace $(LOKISTACK_NAMESPACE) --ignore-not-found
	oc delete pvc -n $(LOKISTACK_NAMESPACE) -l app.kubernetes.io/instance=$(LOKISTACK_NAME) --ignore-not-found
	oc exec -n $(LOKISTACK_NAMESPACE) statefulset/minio -- sh -c \
		'mc alias set local http://localhost:$(MINIO_PORT) $$MINIO_ROOT_USER $$MINIO_ROOT_PASSWORD && mc rb --force local/loki' || true

.PHONY: lokistack-status
lokistack-status:
	@echo "=== LokiStack ==="
	oc get lokistack -n $(LOKISTACK_NAMESPACE) 2>/dev/null || echo "(none)"
	@echo ""
	@echo "=== Loki Bucket Job ==="
	oc get jobs minio-bucket-create -n $(LOKISTACK_NAMESPACE) 2>/dev/null || echo "(none)"
	@echo ""
	@echo "=== Grafana ==="
	oc get pods -l app=grafana -n $(LOKISTACK_NAMESPACE)
	@echo ""
	@echo "=== Grafana Route ==="
	oc get route grafana -n $(LOKISTACK_NAMESPACE) -o jsonpath='{.spec.host}' 2>/dev/null && echo "" || echo "(none)"

.PHONY: minio-install
minio-install: namespace
	helm upgrade --install $(MINIO_RELEASE) $(MINIO_CHART) \
		--namespace $(MINIO_NAMESPACE) \
		--set global.routes.enabled=$(ROUTES_ENABLED) \
		$(MINIO_HELM_EXTRA_ARGS) \
		--wait --timeout 10m

.PHONY: minio-uninstall
minio-uninstall:
	@echo "Uninstalling hub MinIO (this will affect all services using it)..."
	helm uninstall $(MINIO_RELEASE) --namespace $(MINIO_NAMESPACE) --ignore-not-found
	oc delete pvc minio-data-$(MINIO_RELEASE)-0 --namespace $(MINIO_NAMESPACE) --ignore-not-found

.PHONY: unit-tests
unit-tests:
	cd hub/agent-service && uv run pytest
	cd hub/mcp-servers/mcp-openshift && uv sync --group dev && uv run pytest
	cd hub/mcp-servers/mcp-aap && uv sync --group dev && AAP_USERNAME=test AAP_PASSWORD=test uv run pytest
	cd hub/mcp-servers/mcp-kafka && uv sync --group dev && uv run pytest
	cd hub/mcp-servers/mcp-servicenow && uv sync --group dev && SERVICENOW_API_KEY=test uv run pytest
	cd hub/infra/servicenow-mock && uv sync --group dev && uv run pytest

.PHONY: integration-tests
integration-tests:
ifeq ($(ENABLE_HUB),true)
	oc port-forward -n $(NAMESPACE) svc/hub-chatbot-service 8080:80 & \
	PF1_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/hub-ingestion-pipeline 8000:8000 & \
	PF2_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/mcp-noc-openshift 8001:8000 & \
	PF3_PID=$$!; \
	PF4_PID=""; \
	if [ "$(ENABLE_LOKISTACK)" = "true" ]; then \
		oc port-forward -n $(NAMESPACE) svc/mcp-noc-lokistack 8002:8000 & \
		PF4_PID=$$!; \
	fi; \
	oc port-forward -n $(NAMESPACE) svc/mcp-noc-kafka 8003:8000 & \
	PF5_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/mcp-noc-aap 8004:8000 & \
	PF6_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/mcp-noc-slack 8005:8000 & \
	PF7_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/mcp-noc-servicenow 8006:8000 & \
	PF8_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/hub-agent-service 8007:8001 & \
	PF9_PID=$$!; \
	trap "kill $$PF1_PID $$PF2_PID $$PF3_PID $$PF4_PID $$PF5_PID $$PF6_PID $$PF7_PID $$PF8_PID $$PF9_PID" EXIT; \
	sleep 2 && cd hub/integration-tests && \
	AGENT_SERVICE_URL=http://localhost:8007 ENABLE_LOKISTACK=$(ENABLE_LOKISTACK) EDGE_NAMESPACE=$(EDGE_NAMESPACE) uv run pytest
else
	@echo "ENABLE_HUB is not true — skipping hub integration tests"
endif

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
		$(KAFKA_HELM_EXTRA_ARGS) \
		--wait --timeout 10m

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
