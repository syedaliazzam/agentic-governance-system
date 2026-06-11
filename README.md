# AgentCore Registry — Backend

Lambda functions and CI/CD pipeline for the Syngenta AI Foundry AgentCore Registry.

## Repository Structure

```
backend/
├── .gitlab-ci.yml                          # CI/CD pipeline
├── README.md
└── lambdas/
    ├── agentcore-registry-ingestion/       # Ingest A2A + AGUI agents
    │   ├── lambda_function.py
    │   └── requirements.txt
    ├── agentcore-gateway-ingestion/        # Ingest MCP gateways
    │   ├── lambda_function.py
    │   └── requirements.txt
    └── agentcore-catalog-api/              # Catalog + governance API
        ├── lambda_function.py
        └── requirements.txt
```

## Lambda Functions

### agentcore-registry-ingestion
Scans all AgentCore Runtimes in `eu-central-1`, filters to A2A and AGUI protocol agents, and ingests them into the AgentCore Registry in `eu-west-1`.

- **Invocation:** async (`InvocationType=Event`) — takes ~40s
- **Timeout:** 300s | **Memory:** 256 MB
- **IAM Role:** `agentcore-registry-migration-role`

```bash
aws lambda invoke \
  --function-name agentcore-registry-ingestion \
  --invocation-type Event \
  --region eu-central-1 \
  /tmp/response.json

# Tail logs after ~50s
aws logs tail /aws/lambda/agentcore-registry-ingestion \
  --region eu-central-1 --since 5m
```

### agentcore-gateway-ingestion
Scans all AgentCore Gateways in `eu-central-1` and ingests them into the registry. Tries MCP descriptor first; falls back to CUSTOM for JWT-protected gateways.

- **Invocation:** sync (`InvocationType=RequestResponse`) — takes ~10s
- **Timeout:** 120s | **Memory:** 256 MB
- **IAM Role:** `agentcore-registry-migration-role`

```bash
aws lambda invoke \
  --function-name agentcore-gateway-ingestion \
  --invocation-type RequestResponse \
  --region eu-central-1 \
  --payload '{}' \
  /tmp/response.json && cat /tmp/response.json
```

### agentcore-catalog-api
Catalog and governance API for the frontend UI. Reads live data from the registry and exposes it via API Gateway (`nhrfwhwue4`, `eu-central-1`).

- **Base URL:** `https://nhrfwhwue4.execute-api.eu-central-1.amazonaws.com/dev`
- **Timeout:** 30s | **Memory:** 256 MB
- **IAM Role:** `agentcore-mock-api-role`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/records` | List all records (`?status=` `?type=` `?protocol=` `?search=`) |
| GET | `/records/{id}` | Single record with full metadata |
| PUT | `/records/{id}` | Update description / tags (DRAFT only) |
| POST | `/records/{id}/submit` | DRAFT → PENDING_APPROVAL |
| PUT | `/records/{id}/status` | APPROVED / REJECTED / DEPRECATED |

## CI/CD Pipeline

The pipeline runs on every merge request and every push to `main`.

| Stage | Job | Trigger |
|-------|-----|---------|
| lint | Pyflakes all 3 Lambda files | MR + main |
| package | Zip each Lambda | MR + main |
| deploy | Deploy to AWS via `aws-cli` | main only |

### Required GitLab CI/CD Variables

Set these under **Settings → CI/CD → Variables**:

| Variable | Value |
|----------|-------|
| `AWS_ACCESS_KEY_ID` | IAM access key |
| `AWS_SECRET_ACCESS_KEY` | IAM secret key |
| `AWS_DEFAULT_REGION` | `eu-central-1` |
| `REGISTRY_ID` | `Oa1hXgCtdOnJp6GM` |
| `BOTO3_LAYER_ARN` | `arn:aws:lambda:eu-central-1:978502151212:layer:boto3-latest:1` |
| `LAMBDA_ROLE_ARN` | `arn:aws:iam::978502151212:role/agentcore-registry-migration-role` |
| `CATALOG_ROLE_ARN` | `arn:aws:iam::978502151212:role/agentcore-mock-api-role` |

### Branch Protection

- `main` branch is protected — no direct pushes
- All changes go through a merge request
- Pipeline must pass before merge

## Registry

| Property | Value |
|----------|-------|
| Registry ID | `Oa1hXgCtdOnJp6GM` |
| Region | `eu-west-1` |
| Account | `978502151212` |
| Records | 26 (16 agents + 10 gateways) |
