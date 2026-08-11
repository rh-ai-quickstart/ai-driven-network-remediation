CONTAINER_TOOL  ?= podman
REGISTRY        ?= quay.io/rh-ai-quickstart
VERSION         ?= 0.1.5
ARCH            ?= linux/amd64
NAMESPACE       ?= hub
EDGE_NAMESPACE  ?= $(NAMESPACE)
RELEASE         ?= hub
PUSH_EXTRA_ARGS ?=
ROUTES_ENABLED  ?= true

CHATBOT_IMG        := $(REGISTRY)/noc-chatbot-service:$(VERSION)
INGESTION_IMG      := $(REGISTRY)/noc-ingestion-pipeline:$(VERSION)
AGENT_IMG          := $(REGISTRY)/noc-agent-service:$(VERSION)
RAN_ANOMALY_IMG    := $(REGISTRY)/noc-ran-anomaly-detector:$(VERSION)
RAN_RCA_IMG        := $(REGISTRY)/noc-ran-rca-service:$(VERSION)
RAN_CHATBOT_IMG    := $(REGISTRY)/noc-ran-chatbot-service:$(VERSION)
FRONTEND_IMG       := $(REGISTRY)/noc-frontend:$(VERSION)
MCP_OPENSHIFT_IMG  := $(REGISTRY)/noc-mcp-openshift:$(VERSION)
MCP_LOKISTACK_IMG  := $(REGISTRY)/noc-mcp-lokistack:$(VERSION)
MCP_KAFKA_IMG      := $(REGISTRY)/noc-mcp-kafka:$(VERSION)
MCP_AAP_IMG        := $(REGISTRY)/noc-mcp-aap:$(VERSION)
MCP_SERVICENOW_IMG := $(REGISTRY)/noc-mcp-servicenow:$(VERSION)

MCP_CONTAINERFILE           := hub/mcp-servers/Containerfile
MCP_OPENSHIFT_CONTAINERFILE := hub/mcp-servers/Containerfile.openshift
MCP_CONTEXT                 := hub/mcp-servers

# ran-anomaly-detector depends on the sibling telco-oran package via a local
# uv path source, so its build context must be `hub/`, not its own directory.
RAN_ANOMALY_CONTAINERFILE   := hub/ran-anomaly-detector/Containerfile
RAN_ANOMALY_CONTEXT         := hub

RAN_RCA_CONTAINERFILE       := hub/ran-rca-service/Containerfile
RAN_RCA_CONTEXT             := hub

# agent-service depends on the sibling shared package via a local
# uv path source, so its build context must be `hub/`, not its own directory.
AGENT_CONTAINERFILE         := hub/agent-service/Containerfile
AGENT_CONTEXT               := hub

# ── Feature flags ─────────────────────────────────────────────────
ENABLE_HUB             ?= true
ENABLE_KAFKA           ?= true
ENABLE_KAFKA_UI        ?= false
ENABLE_MINIO           ?= true
ENABLE_LOKISTACK       ?= false
ENABLE_LOKISTACK_TEST  ?= false
ENABLE_AAP_MOCK        ?= true
ENABLE_SERVICENOW_MOCK ?= true
ENABLE_LIGHTSPEED      ?= false
LIGHTSPEED_VERIFY_SSL  ?= false
AUTO_INGEST_ON_STARTUP ?= true
AAP_NAMESPACE          ?= aap
ENABLE_SLACK           ?= false
ENABLE_MULTICLUSTER    ?= false
ENABLE_GITEA           ?= $(if $(filter false,$(ENABLE_AAP_MOCK)),true,false)
GITEA_EXTERNAL         ?= false
GITEA_URL              ?=
GITEA_REPO             ?=
GITEA_TOKEN            ?=
GITEA_ADMIN_USER       ?=
GITEA_ADMIN_PASSWORD   ?=
GITEA_ADMIN_EMAIL      ?=
CLUSTER_PROXY_URL      ?=
RHACM_HUB_TOKEN        ?=
AAP_SECRET_NAME ?=
AAP_TOKEN       ?=

# Token is set inline only when: mock is off, no pre-existing secret, and token is provided
_aap_set_token = $(and $(filter false,$(ENABLE_AAP_MOCK)),$(if $(AAP_SECRET_NAME),,y),$(AAP_TOKEN))

ifeq ($(ENABLE_AAP_MOCK),false)
ifndef AAP_SECRET_NAME
ifndef AAP_TOKEN
$(error ENABLE_AAP_MOCK=false requires AAP_TOKEN or AAP_SECRET_NAME. \
Create an OAuth2 token in AAP and pass AAP_TOKEN=<token>, \
or provide an existing K8s secret name via AAP_SECRET_NAME=<name>.)
endif
endif
endif

SLACK_BOT_TOKEN        ?=
SLACK_CHANNEL          ?= \#ai-driven-network
SERVICENOW_INSTANCE_URL ?=
SERVICENOW_CREATE_RESOLVED ?= false

# ── Langfuse (optional: ENABLE_LANGFUSE=true) ───────────────────
ENABLE_LANGFUSE        ?=
LANGFUSE_RELEASE       := langfuse
LANGFUSE_CHART_REPO    := langfuse
LANGFUSE_CHART_URL     := https://langfuse.github.io/langfuse-k8s
LANGFUSE_CHART_VERSION := 1.5.22
LANGFUSE_VALUES        := hub/infra/langfuse/values.yaml
LANGFUSE_SECRET_SCRIPT := hub/infra/langfuse/create-secrets.sh
LANGFUSE_PORT          := 3000

# ── Legacy references (kept for standalone dev targets) ───────────
KAFKA_PORT             := 9092
LOKISTACK_NAME         ?= logging-loki
LOKISTACK_NAMESPACE    ?= $(NAMESPACE)
MINIO_PORT             ?= 9000

# ── AAP / ServiceNow Mock images ──────────────────────────────────
AAP_MOCK_IMG           := $(REGISTRY)/noc-aap-mock:$(VERSION)
SERVICENOW_MOCK_IMG    := $(REGISTRY)/noc-servicenow-mock:$(VERSION)

CORE_BUILD_PUSH_IMAGES := \
	$(CHATBOT_IMG) \
	$(INGESTION_IMG) \
	$(AGENT_IMG) \
	$(RAN_ANOMALY_IMG) \
	$(RAN_RCA_IMG) \
	$(RAN_CHATBOT_IMG) \
	$(FRONTEND_IMG) \
	$(MCP_OPENSHIFT_IMG) \
	$(MCP_LOKISTACK_IMG) \
	$(MCP_KAFKA_IMG) \
	$(MCP_AAP_IMG) \
	$(MCP_SERVICENOW_IMG)

EXTRA_BUILD_PUSH_IMAGES := \
	$(AAP_MOCK_IMG) \
	$(SERVICENOW_MOCK_IMG)

ALL_BUILD_PUSH_IMAGES := \
	$(CORE_BUILD_PUSH_IMAGES) \
	$(EXTRA_BUILD_PUSH_IMAGES)

ADNR_LLM_ENABLED := $(and $(ADNR_LLM_ID),$(ADNR_LLM_URL),$(ADNR_LLM_TOKEN))

.PHONY: version
version:
	@echo $(VERSION)

# ══════════════════════════════════════════════════════════════════════
# Helm argument builders
# ══════════════════════════════════════════════════════════════════════

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
	--set mcp-servers.mcp-servers.noc-servicenow.image.repository=$(REGISTRY)/noc-mcp-servicenow \
	--set mcp-servers.mcp-servers.noc-servicenow.image.tag=$(VERSION)

ifeq ($(ENABLE_MULTICLUSTER),true)
ifndef CLUSTER_PROXY_URL
$(error ENABLE_MULTICLUSTER=true requires CLUSTER_PROXY_URL. \
Run: oc get route -n multicluster-engine cluster-proxy-addon-user -o jsonpath='{.spec.host}')
endif
ifndef RHACM_HUB_TOKEN
$(error ENABLE_MULTICLUSTER=true requires RHACM_HUB_TOKEN. \
Run: oc create token aap-integration-serviceaccount -n aap --duration=8760h)
endif
endif

helm_mock_args = \
	--set aapMock.enabled=$(ENABLE_AAP_MOCK) \
	--set aapMock.image.repository=$(REGISTRY)/noc-aap-mock \
	--set aapMock.image.tag=$(VERSION) \
	--set servicenowMock.enabled=$(ENABLE_SERVICENOW_MOCK) \
	--set servicenowMock.image.repository=$(REGISTRY)/noc-servicenow-mock \
	--set servicenowMock.image.tag=$(VERSION) \
	$(if $(filter true,$(ENABLE_AAP_MOCK)),--set mcp-servers.mcp-servers.noc-aap.env.AAP_URL=http://aap-mock.$(NAMESPACE).svc:8080,) \
	$(if $(filter true,$(ENABLE_AAP_MOCK)),--set mcp-servers.mcp-servers.noc-aap.env.AAP_VERIFY_SSL=false,) \
	$(if $(filter true,$(ENABLE_AAP_MOCK)),--set mcp-servers.mcp-servers.noc-aap.env.GITEA_URL=http://aap-mock.$(NAMESPACE).svc:8080,) \
	$(if $(filter true,$(ENABLE_AAP_MOCK)),--set-string mcpSecrets.aap.token=mock,) \
	$(if $(AAP_SECRET_NAME),--set mcpSecrets.aap.create=false,) \
	$(if $(AAP_SECRET_NAME),--set-string mcpSecrets.aap.existingSecretName='$(AAP_SECRET_NAME)',) \
	$(if $(AAP_SECRET_NAME),--set-string mcp-servers.mcp-servers.noc-aap.envSecrets.AAP_TOKEN.name='$(AAP_SECRET_NAME)',) \
	$(if $(_aap_set_token),--set-string mcpSecrets.aap.token='$(AAP_TOKEN)',) \
	$(if $(filter true,$(ENABLE_SERVICENOW_MOCK)),--set mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_URL=http://servicenow-mock.$(NAMESPACE).svc:8080,) \
	$(if $(filter true,$(ENABLE_SERVICENOW_MOCK)),--set mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_MODE=mock,) \
	$(if $(filter true,$(ENABLE_SERVICENOW_MOCK)),--set-string mcpSecrets.servicenow.apiKey=demo-api-key-2026,) \
	$(if $(filter false,$(ENABLE_AAP_MOCK)),--set aapCredential.enabled=true,) \
	$(if $(filter false,$(ENABLE_AAP_MOCK)),--set-string aapCredential.saNamespace='$(EDGE_NAMESPACE)',)

helm_gitea_args = \
	--set gitea.gitea.external=$(GITEA_EXTERNAL) \
	$(if $(GITEA_ADMIN_USER),--set-string gitea.gitea.adminUser='$(GITEA_ADMIN_USER)' --set-string mcp-servers.mcp-servers.noc-aap.env.GITEA_OWNER='$(GITEA_ADMIN_USER)',) \
	$(if $(GITEA_ADMIN_PASSWORD),--set-string gitea.gitea.adminPassword='$(GITEA_ADMIN_PASSWORD)',) \
	$(if $(GITEA_ADMIN_EMAIL),--set-string gitea.gitea.adminEmail='$(GITEA_ADMIN_EMAIL)',) \
	$(if $(GITEA_URL),--set-string mcp-servers.mcp-servers.noc-aap.env.GITEA_URL='$(GITEA_URL)',) \
	$(if $(GITEA_REPO),--set-string mcp-servers.mcp-servers.noc-aap.env.GITEA_REPO='$(GITEA_REPO)',) \
	$(if $(filter true,$(GITEA_EXTERNAL)),$(if $(GITEA_TOKEN),--set mcpSecrets.gitea.create=true,),) \
	$(if $(GITEA_TOKEN),--set-string mcpSecrets.gitea.token='$(GITEA_TOKEN)',) \
	$(if $(filter true,$(ENABLE_SERVICENOW_MOCK)),--set mcp-servers.mcp-servers.noc-servicenow.env.SERVICENOW_URL=http://servicenow-mock.$(NAMESPACE).svc:8080,)

helm_lokistack_args = \
	--set lokistack.enabled=$(ENABLE_LOKISTACK) \
	--set mcp-servers.mcp-servers.noc-lokistack.enabled=$(ENABLE_LOKISTACK) \
	--set-string lokistack.name='$(LOKISTACK_NAME)' \
	--set-string lokistack.namespace='$(LOKISTACK_NAMESPACE)' \
	$(if $(filter true,$(ENABLE_LOKISTACK)),--set-string llama-stack.mcp-servers.noc-lokistack.uri=http://mcp-noc-lokistack:8000/mcp,)

ifeq ($(ENABLE_LIGHTSPEED),true)
ifndef LIGHTSPEED_URL
LIGHTSPEED_URL = $(shell oc get svc -A --no-headers 2>/dev/null | \
	awk '/lightspeed-chatbot-api/{ns=$$1; name=$$2; split($$6,p,"/"); printf "https://%s.%s.svc:%s", name, ns, p[1]; exit}')
ifeq ($(LIGHTSPEED_URL),)
$(error ENABLE_LIGHTSPEED=true but no Lightspeed service found and LIGHTSPEED_URL not set. Set LIGHTSPEED_URL explicitly or install the AAP operator with Lightspeed enabled.)
endif
endif
ifndef LIGHTSPEED_TOKEN
LIGHTSPEED_TOKEN := $(shell oc get secret aap-lightspeed-chatbot-api-key -n $(AAP_NAMESPACE) -o jsonpath='{.data.api_key}' 2>/dev/null | base64 -d 2>/dev/null)
endif
endif

helm_lightspeed_args = \
	$(if $(filter true,$(ENABLE_LIGHTSPEED)),--set-string agentService.lightspeed.url='$(LIGHTSPEED_URL)',) \
	$(if $(filter true,$(ENABLE_LIGHTSPEED)),--set-string agentService.lightspeed.token='$(LIGHTSPEED_TOKEN)',) \
	$(if $(filter true,$(ENABLE_LIGHTSPEED)),--set-string agentService.lightspeed.verifySSL='$(LIGHTSPEED_VERIFY_SSL)',)

helm_slack_args = \
	--set agentService.slack.enabled=$(ENABLE_SLACK) \
	$(if $(SLACK_BOT_TOKEN),--set-string agentService.slack.botToken='$(SLACK_BOT_TOKEN)',) \
	$(if $(filter true,$(ENABLE_SLACK)),--set-string agentService.slack.channel='$(SLACK_CHANNEL)',) \
	$(if $(SERVICENOW_INSTANCE_URL),--set-string agentService.servicenowInstanceUrl='$(SERVICENOW_INSTANCE_URL)',) \
	--set-string agentService.servicenowCreateResolved='$(SERVICENOW_CREATE_RESOLVED)'

helm_multicluster_args = \
	$(if $(filter true,$(ENABLE_MULTICLUSTER)),--set aapCredential.multicluster.enabled=true,) \
	$(if $(filter true,$(ENABLE_MULTICLUSTER)),--set-string aapCredential.multicluster.clusterProxyUrl='$(CLUSTER_PROXY_URL)',) \
	$(if $(filter true,$(ENABLE_MULTICLUSTER)),--set-string aapCredential.multicluster.hubToken='$(RHACM_HUB_TOKEN)',)

helm_infra_args = \
	--set kafka.enabled=$(ENABLE_KAFKA) \
	--set kafka.kafkaUI.enabled=$(ENABLE_KAFKA_UI) \
	--set kafka.kafka.externalRoute.enabled=$(ROUTES_ENABLED) \
	--set minio.enabled=$(ENABLE_MINIO) \
	--set minio.route.enabled=$(ROUTES_ENABLED) \
	--set gitea.enabled=$(ENABLE_GITEA)

helm_all_args = \
	--set image.registry=$(REGISTRY) \
	--set image.chatbotService=noc-chatbot-service \
	--set image.ingestionPipeline=noc-ingestion-pipeline \
	--set image.agentService=noc-agent-service \
	--set image.ranAnomalyDetector=noc-ran-anomaly-detector \
	--set image.ranRcaService=noc-ran-rca-service \
	--set image.ranChatbotService=noc-ran-chatbot-service \
	--set image.frontend=noc-frontend \
	--set image.tag=$(VERSION) \
	--set global.routes.enabled=$(ROUTES_ENABLED) \
	--set edgeRbac.enabled=$(ROUTES_ENABLED) \
	--set-string edgeRbac.edgeNamespace='$(EDGE_NAMESPACE)' \
	--set-string mcp-servers.mcp-servers.noc-openshift.env.DEFAULT_NAMESPACE='$(EDGE_NAMESPACE)' \
	--set ingestionPipeline.autoIngestOnStartup=$(AUTO_INGEST_ON_STARTUP) \
	$(helm_infra_args) \
	$(helm_lokistack_args) \
	$(helm_mcp_image_args) \
	$(helm_mock_args) \
	$(helm_gitea_args) \
	$(helm_adnr_llm_args) \
	$(helm_autorag_args) \
	$(helm_lightspeed_args) \
	$(helm_slack_args) \
	$(helm_multicluster_args) \
	$(HELM_EXTRA_ARGS)

# ══════════════════════════════════════════════════════════════════════
# Main deployment targets
# ══════════════════════════════════════════════════════════════════════

.PHONY: helm-install
helm-install: namespace helm-depend
ifeq ($(ENABLE_AAP_MOCK),false)
	$(MAKE) _check-aap-operator
endif
ifeq ($(ENABLE_LIGHTSPEED),true)
	$(MAKE) _check-lightspeed-operator
endif
ifeq ($(ENABLE_HUB),true)
	$(MAKE) check-adnr-llm-config
	helm upgrade --install $(RELEASE) hub/helm \
		--namespace $(NAMESPACE) \
		$(helm_all_args) \
		--wait --timeout 10m
else
	@echo "ENABLE_HUB is not true — skipping hub chart deployment"
endif
ifeq ($(ENABLE_LANGFUSE),true)
	$(MAKE) _langfuse-deploy
endif

.PHONY: helm-uninstall
helm-uninstall:
ifeq ($(ENABLE_HUB),true)
	helm uninstall $(RELEASE) --namespace $(NAMESPACE) --ignore-not-found
	oc delete pvc pg-data-pgvector-0 --namespace $(NAMESPACE) --ignore-not-found
	oc delete pvc -l app=kafka --namespace $(NAMESPACE) --ignore-not-found
	oc delete pvc minio-data-minio-0 --namespace $(NAMESPACE) --ignore-not-found
	oc delete pvc data-gitea-0 --namespace $(NAMESPACE) --ignore-not-found

ifeq ($(ENABLE_LANGFUSE),true)
	helm uninstall $(LANGFUSE_RELEASE) --namespace $(NAMESPACE) --ignore-not-found
	oc delete pvc -l app.kubernetes.io/instance=$(LANGFUSE_RELEASE) --namespace $(NAMESPACE) --ignore-not-found
	oc delete secret langfuse-secrets --namespace $(NAMESPACE) --ignore-not-found
endif
endif
	$(MAKE) edge-rbac-teardown
	oc delete namespace $(EDGE_NAMESPACE) --ignore-not-found
	oc delete namespace $(NAMESPACE) --ignore-not-found

.PHONY: namespace
namespace:
	@oc create namespace $(NAMESPACE) 2>/dev/null ||:
	@oc config set-context --current --namespace=$(NAMESPACE) 2>/dev/null ||:

.PHONY: helm-depend
helm-depend:
	cd hub/helm && helm dependency update

.PHONY: check-adnr-llm-config
check-adnr-llm-config:
	@missing=""; \
	[ -n "$(ADNR_LLM_ID)" ] || missing="$$missing ADNR_LLM_ID"; \
	[ -n "$(ADNR_LLM_URL)" ] || missing="$$missing ADNR_LLM_URL"; \
	[ -n "$(ADNR_LLM_TOKEN)" ] || missing="$$missing ADNR_LLM_TOKEN"; \
	if [ -n "$$missing" ]; then \
		echo "ERROR: Missing required ADNR LLM configuration:$$missing"; \
		echo "Set ADNR_LLM_ID, ADNR_LLM_URL, and ADNR_LLM_TOKEN before running 'make helm-install'."; \
		echo "See .env.example and docs/manual-deploy.md for the expected values."; \
		exit 1; \
	fi

.PHONY: _require-aap-operator
_require-aap-operator:
	@oc get csv -A 2>/dev/null | grep -q "aap-operator" || \
		{ echo "ERROR: AAP Operator is not installed on this cluster. See README.md for installation instructions."; \
		  exit 1; }

.PHONY: _check-aap-operator
_check-aap-operator: _require-aap-operator
	@oc get svc aap -n $(AAP_NAMESPACE) --no-headers 2>/dev/null | grep -q "aap" || \
		{ echo "ERROR: AAP Operator found but no controller service in namespace '$(AAP_NAMESPACE)'. See README.md for setup."; \
		  exit 1; }

.PHONY: _check-lightspeed-operator
_check-lightspeed-operator: _require-aap-operator
	@oc get svc -A --no-headers 2>/dev/null | grep -q "lightspeed-chatbot-api" || \
		{ echo ""; \
		  echo "ERROR: AAP Operator found but no lightspeed-chatbot-api service detected."; \
		  echo "Ensure Lightspeed is enabled in the AnsibleAutomationPlatform CR (spec.lightspeed.disabled: false)."; \
		  echo ""; \
		  exit 1; }

.PHONY: edge-rbac-teardown
edge-rbac-teardown:
	sed 's/EDGE_NAMESPACE_PLACEHOLDER/$(EDGE_NAMESPACE)/g' hub/mcp-servers/mcp-openshift/deploy/edge-rbac.yaml \
		| oc delete -n $(EDGE_NAMESPACE) --ignore-not-found -f -
	oc delete secret noc-openshift-edge-kubeconfig -n $(NAMESPACE) --ignore-not-found

# ══════════════════════════════════════════════════════════════════════
# Container image targets
# ══════════════════════════════════════════════════════════════════════

.PHONY: build-all-images
build-all-images: build-chatbot-image build-agent-image build-ran-anomaly-image build-ran-rca-image build-ran-chatbot-image build-frontend-image build-mcp-images

.PHONY: build-chatbot-image
build-chatbot-image:
	$(CONTAINER_TOOL) build -t $(CHATBOT_IMG) --platform=$(ARCH) -f hub/chatbot-service/Containerfile hub/chatbot-service
	$(CONTAINER_TOOL) build -t $(INGESTION_IMG) --platform=$(ARCH) -f hub/ingestion-pipeline/Containerfile hub/ingestion-pipeline

.PHONY: build-agent-image
build-agent-image:
	$(CONTAINER_TOOL) build -t $(AGENT_IMG) --platform=$(ARCH) -f $(AGENT_CONTAINERFILE) $(AGENT_CONTEXT)

.PHONY: build-ran-anomaly-image
build-ran-anomaly-image:
	$(CONTAINER_TOOL) build -t $(RAN_ANOMALY_IMG) --platform=$(ARCH) -f $(RAN_ANOMALY_CONTAINERFILE) $(RAN_ANOMALY_CONTEXT)

.PHONY: build-ran-rca-image
build-ran-rca-image:
	$(CONTAINER_TOOL) build -t $(RAN_RCA_IMG) --platform=$(ARCH) -f $(RAN_RCA_CONTAINERFILE) $(RAN_RCA_CONTEXT)

.PHONY: build-ran-chatbot-image
build-ran-chatbot-image:
	$(CONTAINER_TOOL) build -t $(RAN_CHATBOT_IMG) --platform=$(ARCH) -f hub/ran-chatbot-service/Containerfile hub/ran-chatbot-service

.PHONY: build-frontend-image
build-frontend-image:
	$(CONTAINER_TOOL) build -t $(FRONTEND_IMG) --platform=$(ARCH) -f hub/frontend/Containerfile hub/frontend

.PHONY: build-mcp-images
build-mcp-images:
	$(CONTAINER_TOOL) build -t $(MCP_OPENSHIFT_IMG)  --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-openshift  --build-arg MODULE_NAME=mcp_openshift  -f $(MCP_OPENSHIFT_CONTAINERFILE) $(MCP_CONTEXT)
	$(CONTAINER_TOOL) build -t $(MCP_LOKISTACK_IMG)  --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-lokistack  --build-arg MODULE_NAME=mcp_lokistack  -f $(MCP_CONTAINERFILE) $(MCP_CONTEXT)
	$(CONTAINER_TOOL) build -t $(MCP_KAFKA_IMG)      --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-kafka      --build-arg MODULE_NAME=mcp_kafka      -f $(MCP_CONTAINERFILE) $(MCP_CONTEXT)
	$(CONTAINER_TOOL) build -t $(MCP_AAP_IMG)        --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-aap        --build-arg MODULE_NAME=mcp_aap        -f $(MCP_CONTAINERFILE) $(MCP_CONTEXT)
	$(CONTAINER_TOOL) build -t $(MCP_SERVICENOW_IMG) --platform=$(ARCH) --build-arg SERVICE_NAME=mcp-servicenow --build-arg MODULE_NAME=mcp_servicenow -f $(MCP_CONTAINERFILE) $(MCP_CONTEXT)

.PHONY: push-all-images
push-all-images:
	@for image in $(CORE_BUILD_PUSH_IMAGES); do \
		$(CONTAINER_TOOL) push $$image $(PUSH_EXTRA_ARGS); \
	done

.PHONY: print-all-images
print-all-images:
	@printf '%s\n' $(CORE_BUILD_PUSH_IMAGES)

.PHONY: build-push-aap-mock
build-push-aap-mock:
	$(CONTAINER_TOOL) build -t $(AAP_MOCK_IMG) --platform=$(ARCH) -f hub/infra/aap-mock/Containerfile hub/infra/aap-mock
	$(CONTAINER_TOOL) push $(AAP_MOCK_IMG) $(PUSH_EXTRA_ARGS)

.PHONY: build-push-servicenow-mock
build-push-servicenow-mock:
	$(CONTAINER_TOOL) build -t $(SERVICENOW_MOCK_IMG) --platform=$(ARCH) -f hub/infra/servicenow-mock/Containerfile hub/infra/servicenow-mock
	$(CONTAINER_TOOL) push $(SERVICENOW_MOCK_IMG) $(PUSH_EXTRA_ARGS)

.PHONY: reinstall-all
reinstall-all:
	cd hub/chatbot-service && uv sync --reinstall
	cd hub/ingestion-pipeline && uv sync --reinstall

# ══════════════════════════════════════════════════════════════════════
# Edge workload
# ══════════════════════════════════════════════════════════════════════

EDGE_WORKLOAD_IMAGE ?= registry.k8s.io/pause:3.10

.PHONY: deploy-edge-workload
deploy-edge-workload:
	oc create namespace $(EDGE_NAMESPACE) 2>/dev/null ||:
	oc create deployment edge-worker --image=$(EDGE_WORKLOAD_IMAGE) --replicas=1 -n $(EDGE_NAMESPACE) 2>/dev/null \
		|| echo "edge-worker deployment already exists, skipping"
	oc wait --for=condition=available deployment/edge-worker -n $(EDGE_NAMESPACE) --timeout=60s

# ══════════════════════════════════════════════════════════════════════
# Langfuse (separate release — independent of hub chart)
# ══════════════════════════════════════════════════════════════════════

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

# ══════════════════════════════════════════════════════════════════════
# Dev convenience targets (standalone component install)
# ══════════════════════════════════════════════════════════════════════

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

.PHONY: autorag-status
autorag-status:
	@echo "=== LlamaStackDistribution ==="
	oc get llamastackdistribution --namespace $(NAMESPACE) 2>/dev/null || echo "(none)"
	@echo ""
	@echo "=== Llama Stack Pod ==="
	oc get pods -l app.kubernetes.io/managed-by=llamastack-operator --namespace $(NAMESPACE) 2>/dev/null || echo "(none)"

# ══════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════

.PHONY: unit-tests
unit-tests:
	cd hub/shared && uv sync --group dev && uv run pytest
	cd hub/chatbot-service && uv sync --group dev && uv run pytest tests/ -o "addopts="
	cd hub/agent-service && uv sync --group dev && uv run pytest
	cd hub/mcp-servers/mcp-openshift && uv sync --group dev && uv run pytest
	cd hub/mcp-servers/mcp-lokistack && uv sync --group dev && uv run pytest
	cd hub/mcp-servers/mcp-aap && uv sync --group dev && AAP_TOKEN=test uv run pytest
	cd hub/ingestion-pipeline && uv sync --group dev && uv run pytest
	cd hub/mcp-servers/mcp-kafka && uv sync --group dev && uv run pytest
	cd hub/mcp-servers/mcp-servicenow && uv sync --group dev && uv run pytest
	cd hub/infra/servicenow-mock && uv sync --group dev && uv run pytest
	cd hub/telco-oran && uv sync --group dev && uv run pytest
	cd hub/ran-anomaly-detector && uv sync --group dev && uv run pytest
	cd hub/ran-rca-service && uv sync --group dev && uv run pytest
	cd hub/ran-chatbot-service && uv sync --group dev && uv run pytest

.PHONY: integration-tests
integration-tests:
ifeq ($(ENABLE_HUB),true)
	oc port-forward -n $(NAMESPACE) svc/hub-chatbot-service 8080:80 & \
	PF1_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/hub-ingestion-pipeline 8000:8000 & \
	PF2_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/mcp-noc-openshift 8001:8000 & \
	PF3_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/llamastack-service 8321:8321 & \
	PF10_PID=$$!; \
	PF4_PID=""; \
	if [ "$(ENABLE_LOKISTACK)" = "true" ]; then \
		oc port-forward -n $(NAMESPACE) svc/mcp-noc-lokistack 8002:8000 & \
		PF4_PID=$$!; \
	fi; \
	oc port-forward -n $(NAMESPACE) svc/mcp-noc-kafka 8003:8000 & \
	PF5_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/mcp-noc-aap 8004:8000 & \
	PF6_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/mcp-noc-servicenow 8006:8000 & \
	PF8_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/hub-agent-service 8007:8001 & \
	PF9_PID=$$!; \
	oc port-forward -n $(NAMESPACE) svc/hub-ran-chatbot-service 8008:8003 & \
	PF11_PID=$$!; \
	trap "kill $$PF1_PID $$PF2_PID $$PF3_PID $$PF4_PID $$PF5_PID $$PF6_PID $$PF8_PID $$PF9_PID $$PF10_PID $$PF11_PID" EXIT; \
	sleep 2 && cd hub/integration-tests && \
	AGENT_SERVICE_URL=http://localhost:8007 LLAMASTACK_URL=http://localhost:8321 RAN_CHATBOT_SERVICE_URL=http://localhost:8008 ENABLE_LOKISTACK=$(ENABLE_LOKISTACK) EDGE_NAMESPACE=$(EDGE_NAMESPACE) uv run pytest
else
	@echo "ENABLE_HUB is not true — skipping hub integration tests"
endif

# ══════════════════════════════════════════════════════════════════════
# ServiceNow PDI Bootstrap
# ══════════════════════════════════════════════════════════════════════

SERVICENOW_BOOTSTRAP_DIR := scripts/servicenow-bootstrap

.PHONY: deps-servicenow-bootstrap
deps-servicenow-bootstrap:
	cd $(SERVICENOW_BOOTSTRAP_DIR) && uv sync

.PHONY: servicenow-wake-install
servicenow-wake-install:
	cd $(SERVICENOW_BOOTSTRAP_DIR) && uv sync --group wake && uv run playwright install chromium

.PHONY: servicenow-wake
servicenow-wake:
	cd $(SERVICENOW_BOOTSTRAP_DIR) && uv sync --group wake && uv run python -m servicenow_bootstrap.wake_up_pdi

.PHONY: servicenow-bootstrap
servicenow-bootstrap: deps-servicenow-bootstrap
	cd $(SERVICENOW_BOOTSTRAP_DIR) && uv run python -m servicenow_bootstrap.orchestrator --config config.json

.PHONY: servicenow-bootstrap-validate
servicenow-bootstrap-validate: deps-servicenow-bootstrap
	cd $(SERVICENOW_BOOTSTRAP_DIR) && uv run python -m servicenow_bootstrap.setup_validations

.PHONY: servicenow-bootstrap-create-user
servicenow-bootstrap-create-user: deps-servicenow-bootstrap
	cd $(SERVICENOW_BOOTSTRAP_DIR) && uv run python -m servicenow_bootstrap.create_noc_agent_user --config config.json

.PHONY: servicenow-bootstrap-create-api-key
servicenow-bootstrap-create-api-key: deps-servicenow-bootstrap
	cd $(SERVICENOW_BOOTSTRAP_DIR) && uv run python -m servicenow_bootstrap.create_noc_agent_api_key --config config.json

.PHONY: servicenow-bootstrap-create-data
servicenow-bootstrap-create-data: deps-servicenow-bootstrap
	cd $(SERVICENOW_BOOTSTRAP_DIR) && uv run python -m servicenow_bootstrap.create_incident_test_data --config config.json

.PHONY: test-servicenow-bootstrap
test-servicenow-bootstrap: deps-servicenow-bootstrap
	cd $(SERVICENOW_BOOTSTRAP_DIR) && uv run pytest
