SERVICE_NAME ?= cashapp2csv
ENVIRONMENT ?= dev

.PHONY: all
all: format lint test

.PHONY: format
format:
	uv run ruff check --select I --fix .
	uv run ruff format .

.PHONY: lint
lint:
	uv run pyright
	uv run ruff check .

#
# Python development and test
#

.PHONY: test
test:
	echo "Yeah, OK, tested"

#
# AWS infrastructure management
#

# Select environment with ENVIRONMENT=dev (default) or ENVIRONMENT=prod
# Example: make plan ENVIRONMENT=prod
AWS_PROFILE ?= $(ENVIRONMENT)

ifeq ($(filter $(ENVIRONMENT),dev prod),)
$(error ENVIRONMENT must be 'dev' or 'prod', got '$(ENVIRONMENT)')
endif

TF_BACKEND_CONFIG := terraform/env/$(ENVIRONMENT).s3.tfbackend
TF_PLAN_FILE      := .tf.plan
TF_VAR_FILE       := terraform/env/$(ENVIRONMENT).tfvars

.PHONY: aws-auth
aws-auth:
	@echo "Checking AWS auth for profile '$(AWS_PROFILE)'"
	@aws sts get-caller-identity --profile $(AWS_PROFILE) >/dev/null 2>&1 || aws sso login --profile $(AWS_PROFILE)
	@aws sts get-caller-identity --profile $(AWS_PROFILE)

# Pass extra terraform init flags via ARGS (e.g. make init ARGS=-reconfigure)
ARGS ?=

.PHONY: init
init: aws-auth
	@echo "Initializing Terraform for ENVIRONMENT='$(ENVIRONMENT)' with backend 'env/$(ENVIRONMENT).s3.tfbackend'"
	cd terraform && AWS_PROFILE=$(AWS_PROFILE) terraform init \
		-reconfigure \
		-backend-config=env/$(ENVIRONMENT).s3.tfbackend \
		-var-file=env/$(ENVIRONMENT).tfvars \
		$(ARGS)

.PHONY: plan
plan: aws-auth
	cd terraform && AWS_PROFILE=$(AWS_PROFILE) terraform plan \
		-var-file=env/$(ENVIRONMENT).tfvars \
		-var service_name=$(SERVICE_NAME) \
		-out=$(TF_PLAN_FILE) \
		$(ARGS)

.PHONY: apply
apply: aws-auth
	cd terraform && test -f $(TF_PLAN_FILE)
	cd terraform && AWS_PROFILE=$(AWS_PROFILE) terraform apply \
		$(ARGS) \
		-auto-approve $(TF_PLAN_FILE)

#
# Docker image management
#

DOCKER_TARGET    := $(if $(filter $(ENVIRONMENT),dev),portal-dev,portal)
LOCAL_IMAGE_TAG  := $(if $(filter $(ENVIRONMENT),dev),dev,latest)
REMOTE_IMAGE_TAG := $(if $(filter $(ENVIRONMENT),dev),dev,latest)

.PHONY: build
build:
	$(eval GIT_COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown))
	docker buildx build --load --provenance=false --sbom=false --target $(DOCKER_TARGET) --tag $(SERVICE_NAME):$(LOCAL_IMAGE_TAG) --tag $(SERVICE_NAME):$(GIT_COMMIT) \
		--build-arg GIT_TAG=$(shell git describe --match 'v*' --tags --abbrev=0 2>/dev/null || echo unknown) \
		--build-arg GIT_COMMIT=$(GIT_COMMIT) \
		.

.PHONY: push
push: aws-auth build
	$(eval AWS_ACCOUNT_ID := $(shell AWS_PROFILE=$(AWS_PROFILE) aws sts get-caller-identity --query Account --output text))
	$(eval AWS_REGION     := $(shell AWS_PROFILE=$(AWS_PROFILE) aws configure get region))
	$(eval ECR_REGISTRY   := $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com)
	$(eval GIT_TAG        := $(shell git describe --match 'v*' --tags --abbrev=0 2>/dev/null || echo unknown))
	AWS_PROFILE=$(AWS_PROFILE) aws ecr get-login-password --region $(AWS_REGION) \
		| docker login --username AWS --password-stdin $(ECR_REGISTRY)
	docker tag $(SERVICE_NAME):$(LOCAL_IMAGE_TAG) $(ECR_REGISTRY)/$(SERVICE_NAME):$(REMOTE_IMAGE_TAG)
	docker push $(ECR_REGISTRY)/$(SERVICE_NAME):$(REMOTE_IMAGE_TAG)
ifeq ($(ENVIRONMENT),prod)
	@TAG_COMMIT=$(shell git rev-parse --short refs/tags/$(GIT_TAG)^{} 2>/dev/null || echo ''); \
	HEAD_COMMIT=$(shell git rev-parse --short HEAD 2>/dev/null || echo ''); \
	if [ -n "$$TAG_COMMIT" ] && [ "$$TAG_COMMIT" = "$$HEAD_COMMIT" ]; then \
		echo "HEAD matches $(GIT_TAG) ($$HEAD_COMMIT) — tagging and pushing $(GIT_TAG)"; \
		docker tag $(SERVICE_NAME):$(LOCAL_IMAGE_TAG) $(ECR_REGISTRY)/$(SERVICE_NAME):$(GIT_TAG); \
		docker push $(ECR_REGISTRY)/$(SERVICE_NAME):$(GIT_TAG); \
	else \
		echo "HEAD ($$HEAD_COMMIT) does not match $(GIT_TAG) ($$TAG_COMMIT) — skipping version tag push"; \
	fi
endif
