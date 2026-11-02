CONTAINER_TOOL  ?= podman
REGISTRY        ?=
VERSION         ?= 0.1.0
ARCH            ?= linux/amd64
PUSH_EXTRA_ARGS ?=

CLUSTER_COUNT  ?= 1
EDGE_NAMESPACE ?= dark-noc-edge
SITE_A         ?= edge-site-01
SITE_B         ?= edge-site-02
RELEASE        ?= cpu-misconfiguration

CLIENT_IMAGE         := $(REGISTRY)/showcase-client:$(VERSION)
MICROSERVICE_A_IMAGE := $(REGISTRY)/showcase-microservice-a:$(VERSION)
MICROSERVICE_B_IMAGE := $(REGISTRY)/showcase-microservice-b:$(VERSION)

.PHONY: check-registry build-images push-images deploy undeploy reset

check-registry:
	@test -n "$(REGISTRY)" || { echo "Set REGISTRY to the image registry you use" >&2; exit 1; }

build-images: check-registry
	$(CONTAINER_TOOL) build -t $(CLIENT_IMAGE) --platform=$(ARCH) -f client/Containerfile client
	$(CONTAINER_TOOL) build -t $(MICROSERVICE_A_IMAGE) --platform=$(ARCH) -f microservice-a/Containerfile microservice-a
	$(CONTAINER_TOOL) build -t $(MICROSERVICE_B_IMAGE) --platform=$(ARCH) -f microservice-b/Containerfile microservice-b

push-images: check-registry
	$(CONTAINER_TOOL) push $(CLIENT_IMAGE) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(MICROSERVICE_A_IMAGE) $(PUSH_EXTRA_ARGS)
	$(CONTAINER_TOOL) push $(MICROSERVICE_B_IMAGE) $(PUSH_EXTRA_ARGS)

deploy:
	REGISTRY="$(REGISTRY)" VERSION="$(VERSION)" CLUSTER_COUNT="$(CLUSTER_COUNT)" EDGE_NAMESPACE="$(EDGE_NAMESPACE)" SITE_A="$(SITE_A)" SITE_B="$(SITE_B)" RELEASE="$(RELEASE)" ./deploy.sh deploy

undeploy:
	REGISTRY="$(REGISTRY)" VERSION="$(VERSION)" CLUSTER_COUNT="$(CLUSTER_COUNT)" EDGE_NAMESPACE="$(EDGE_NAMESPACE)" SITE_A="$(SITE_A)" SITE_B="$(SITE_B)" RELEASE="$(RELEASE)" ./deploy.sh undeploy

reset:
	CLUSTER_COUNT="$(CLUSTER_COUNT)" EDGE_NAMESPACE="$(EDGE_NAMESPACE)" SITE_A="$(SITE_A)" SITE_B="$(SITE_B)" RELEASE="$(RELEASE)" ./deploy.sh reset
