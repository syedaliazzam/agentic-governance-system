# AgentCore Registry — Backend

Lambda functions, Cognito authorizer, and CI/CD pipeline for the Syngenta AI Foundry AgentCore Registry.

## Repository Structure

```
backend/
├── .gitlab-ci.yml                          # CI/CD pipeline (validate + serverless deploy)
├── .env.dev                                # Non-secret config values for dev (committed)
├── serverless.yml                          # Serverless Framework — defines & deploys all 4 Lambdas
├── README.md
├── scripts/
│   └── attach-authorizer.sh                # Post-deploy: wires Lambda authorizer to API Gateway routes
└── lambdas/
    ├── agentcore-registry-ingestion/       # Ingest A2A + AGUI agents
    │   ├── lambda_function.py
    │   └── requirements.txt
    ├── agentcore-gateway-ingestion/        # Ingest MCP gateways
    │   ├── lambda_function.py
    │   └── requirements.txt
    ├── agentcore-catalog-api/              # Catalog + governance API
    │   ├── lambda_function.py
    │   └── requirements.txt
    └── authorizer/                         # Cognito JWT Lambda authorizer
        ├── lambda_function.py
        └── requirements.txt
```

## Architecture

```
User Request
    ↓
API Gateway (Terraform-managed, externally referenced — <API_GATEWAY_ID>)
    ↓
Lambda Authorizer (validates Cognito JWT, extracts persona)
    ↓
Business Logic Lambda (catalog-api / registry-ingestion / gateway-ingestion)
    ↓
AgentCore Registry (eu-west-1) — using Entra ID JWT for AgentCore runtime metadata, unchanged
```

The API Gateway, Cognito User Pool, and IAM roles are managed in a separate **Terraform** repo (`ai-foundry-registry-iac`). The four Lambda functions in this repo are deployed via the **Serverless Framework**, which references the externally-created HTTP API by ID rather than owning it. This split was a deliberate POC tradeoff — see [Known Limitations](#known-limitations--phase-2) below.

## Lambda Functions

### agentcore-registry-ingestion
Scans all AgentCore Runtimes in `eu-central-1`, filters to A2A and AGUI protocol agents, and ingests them into the AgentCore Registry in `eu-west-1`.

- **Deployed name:** `ai-foundry-registry-{stage}-registry-ingestion-lambda`
- **Trigger:** `POST /ingest/registry` (no authorizer — internal trigger)
- **Timeout:** 300s | **Memory:** 256 MB | **VPC:** Yes
- **IAM Role:** `<ingestion-lambda-role>`

```bash
aws lambda invoke \
  --function-name ai-foundry-registry-dev-registry-ingestion-lambda \
  --invocation-type Event \
  --region eu-central-1 \
  /tmp/response.json

# Tail logs after ~50s (this Lambda runs ~40s, so use async invoke + tail, not sync invoke)
aws logs tail /aws/lambda/ai-foundry-registry-dev-registry-ingestion-lambda \
  --region eu-central-1 --since 5m
```

### agentcore-gateway-ingestion
Scans all AgentCore Gateways in `eu-central-1` and ingests them into the registry. Tries MCP descriptor type first (requires non-empty `description`, schema version `2025-12-11`); falls back to CUSTOM descriptor for JWT-protected gateways where URL sync to the registry can't authenticate.

- **Deployed name:** `ai-foundry-registry-{stage}-gateway-ingestion-lambda`
- **Trigger:** `POST /ingest/gateways` (no authorizer — internal trigger)
- **Timeout:** 120s | **Memory:** 256 MB | **VPC:** Yes
- **IAM Role:** `<ingestion-lambda-role>`

```bash
aws lambda invoke \
  --function-name ai-foundry-registry-dev-gateway-ingestion-lambda \
  --invocation-type RequestResponse \
  --region eu-central-1 \
  --payload '{}' \
  /tmp/response.json && cat /tmp/response.json
```

### agentcore-catalog-api
Catalog and governance API for the frontend UI. Reads live data from the registry and exposes it via the shared HTTP API Gateway (`<API_GATEWAY_ID>`, `eu-central-1`).

- **Deployed name:** `ai-foundry-registry-{stage}-catalog-api-lambda`
- **Base URL:** `https://<API_GATEWAY_ID>.execute-api.eu-central-1.amazonaws.com/api`
- **Timeout:** 30s | **Memory:** 256 MB | **VPC:** Yes
- **IAM Role:** `<catalog-api-lambda-role>`
- **Authorizer:** Lambda authorizer (CUSTOM) on all 5 routes below

| Method | Path | Description |
|--------|------|-------------|
| GET | `/records` | List all records visible to the caller's persona (`?status=` `?type=` `?protocol=` `?search=`) |
| GET | `/records/{id}` | Single record with full metadata |
| PUT | `/records/{id}` | Update description / tags / additionalMetadata — **DRAFT or REJECTED only** |
| POST | `/records/{id}/submit` | DRAFT or REJECTED → PENDING_APPROVAL |
| PUT | `/records/{id}/status` | APPROVED / REJECTED / DEPRECATED (admin actions) |

**Payload format compatibility:** the handler reads both HTTP API payload format 2.0 (`rawPath`, `requestContext.http.method`) and REST API format 1.0 (`path`, `httpMethod`), so it runs correctly regardless of which API Gateway type invokes it. It also strips a leading `api` path segment, since the named API Gateway stage (`api`) appears as the first segment of `rawPath`.

**Persona-based visibility:** the handler reads `event.requestContext.authorizer.lambda.group` (set by the Lambda authorizer) and enforces which statuses each persona can see, **regardless of what status filter the frontend sends**:

| Persona | Visible statuses |
|---|---|
| `consumers` | `APPROVED` only |
| `publishers` | `DRAFT`, `REJECTED`, `PENDING_APPROVAL`, `APPROVED` |
| `admins` | `DRAFT`, `REJECTED`, `PENDING_APPROVAL`, `APPROVED`, `DEPRECATED` |

If a status filter is provided in the query string, it is intersected with the persona's allowed set — a `consumers` request for `?status=DRAFT` returns an empty list rather than leaking draft records.

**Editable fields:** only `description`, `tags`, and `additionalMetadata` can be updated via `PUT /records/{id}`. All other fields (name, protocol, authType, gatewayUrl, model, etc.) are read-only and sourced from the AgentCore Runtime/Gateway itself.

### authorizer
Lambda authorizer (`REQUEST` type, payload format 2.0) that validates the Cognito Bearer JWT on incoming requests and identifies the caller's persona.

- **Deployed name:** `ai-foundry-registry-{stage}-authorizer-lambda`
- **Timeout:** 10s | **Memory:** 256 MB | **VPC:** No — only calls Cognito's public JWKS endpoint over the internet, so it is explicitly excluded from the provider-level VPC config via `vpc: ~` in `serverless.yml`
- **IAM Role:** `<authorizer-lambda-role>`
- **Dependency:** `python-jose[cryptography]`, bundled as a Lambda layer (`python-jose`)

What it does:
1. Reads `Authorization: Bearer <token>` from request headers
2. Verifies the JWT signature against the Cognito User Pool's JWKS keys (cached per cold start)
3. Validates issuer, expiry, and audience/client_id (depending on `token_use`)
4. Extracts the user's group from `cognito:groups` — priority `admins` > `publishers` > `consumers`, defaulting to `consumers` if no recognised group is present
5. Returns an IAM Allow/Deny policy to API Gateway, with `{sub, group}` passed as context to the downstream Lambda via `event.requestContext.authorizer.lambda`

This authorizer is fully independent of the Entra ID JWT logic used inside the ingestion Lambdas for AgentCore runtime metadata access — that logic is untouched.

**Cognito configuration (dev):**

Pool ID, App Client ID, and region are stored in `.env.dev` as `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID`, and `COGNITO_REGION`. Configured groups: `publishers`, `admins`, `consumers`. Auth flows enabled on the app client: `ALLOW_USER_PASSWORD_AUTH`, `ALLOW_USER_SRP_AUTH`, `ALLOW_REFRESH_TOKEN_AUTH`.

## Authorizer Route Wiring — `scripts/attach-authorizer.sh`

The Serverless Framework **cannot** manage authorizers on an externally-referenced HTTP API (it only supports authorizer config when it owns the API Gateway resource itself). Since this API Gateway is created in Terraform and only referenced here, attaching the authorizer to routes has to happen as a **post-deploy step**, run automatically by the pipeline after every `serverless deploy`:

```bash
./scripts/attach-authorizer.sh
```

It:
1. Looks up the current authorizer Lambda's ARN and updates the existing API Gateway authorizer (`AUTHORIZER_ID`) to point to it — necessary because every Serverless deploy creates a new Lambda version/ARN
2. Grants API Gateway permission to invoke the authorizer Lambda (idempotent — skips if the permission already exists)
3. Attaches `AuthorizationType: CUSTOM` with that authorizer to every route containing `/records` (i.e. all 5 catalog API routes)

Required environment variables (already in `.env.dev`): `HTTP_API_ID`, `AUTHORIZER_ID`, `AWS_REGION`. The `/ingest/registry` and `/ingest/gateways` routes are intentionally left without an authorizer since they're internal triggers, not user-facing.

## CI/CD Pipeline

The pipeline (`.gitlab-ci.yml`) has two stages and uses GitLab OIDC to assume an AWS role — no static AWS credentials are stored in GitLab.

| Stage | Job | Runs on |
|-------|-----|---------|
| validate | `validate` (placeholder for unit tests) | `feature/*` branches, MRs |
| validate | `lint:python-lambdas` (pyflakes on all 4 Lambda files) | `feature/*` branches, MRs |
| deploy | `deploy_dev` | tag matching `dev/v*` — **manual trigger** |
| deploy | `deploy_prod` | tag matching `release/v*` — **manual trigger** |

**Deploys never run automatically on a branch push or merge** — only on a matching tag, and even then `deploy_dev`/`deploy_prod` require clicking the manual play button in the GitLab UI. This is intentional: validation runs on every push, but nothing reaches AWS without an explicit, deliberate trigger.

The deploy job runs `serverless deploy --stage <env>` followed by `./scripts/attach-authorizer.sh`. It also checks for and cleans up any stack stuck in `ROLLBACK_COMPLETE` before attempting a fresh deploy.

### Tagging convention

```bash
# Deploy to dev
git tag -a dev/v0.1.0 -m "description"
git push origin dev/v0.1.0
# then click ▶ on deploy_dev in the GitLab pipeline UI

# Deploy to prod
git tag -a release/v1.0.0 -m "description"
git push origin release/v1.0.0
# then click ▶ on deploy_prod in the GitLab pipeline UI
```

Branch names must use the `feature/` prefix (not `feat/`) for the validate stage to run.

### Required GitLab CI/CD Variables

Set under **Settings → CI/CD → Variables**:

| Variable | Value | Notes |
|----------|-------|-------|
| `AWS_ROLE_ARN_DEV` | `arn:aws:iam::<ACCOUNT_ID>:role/GalaxyAI-TerraformRole` | Already in `.gitlab-ci.yml` |
| `AWS_ROLE_ARN_PROD` | _(to be set)_ | Currently empty |

All other config (registry ID, role ARNs, layer ARNs, Cognito IDs, VPC subnet/security group IDs, authorizer/API Gateway IDs) lives in `.env.dev` — these are not secrets and are committed directly to the repo at Syngenta's instruction, since they're non-sensitive identifiers (no credentials).

### Branch Protection

- `main` is protected — no direct pushes
- All changes go through a merge request
- Pipeline must pass before merge

## Known Limitations / Phase 2

- **Dual IaC stack:** API Gateway, Cognito, and IAM roles are Terraform-managed; Lambdas are Serverless-managed. This split caused the authorizer-route-attachment problem solved by `scripts/attach-authorizer.sh`. Recommended cleanup: consolidate ownership of the API Gateway into one tool.
- **Galaxy role is interim:** cross-account IAM access (for scanning AgentCore Runtimes/Gateways in other Syngenta accounts) currently uses the `Galaxy` role as a stopgap, pending a properly scoped cross-account trust policy.
- **Record versioning, manual record input, skills as a record type, runtime deployment from registry, and event notifications** are not yet implemented — see Phase 2 recommendations shared with the team.
- **Tagging is not natively supported by AgentCore Registry** (`tag_resource` returns `NotFoundException` on record ARNs). Tags and `additionalMetadata` are instead stored as fields inside each record's `inlineContent` and surfaced through the API — fully additive, so native tagging support could be adopted later without rework.

## Registry

| Property | Value |
|----------|-------|
| Registry region | `eu-west-1` |
| Lambda region | `eu-central-1` |
| API Gateway | HTTP API, stage `api` (ID in `.env.dev` as `HTTP_API_ID`) |

Registry ID, AWS account ID, and IAM role ARNs are stored in `.env.dev` rather than hardcoded here.