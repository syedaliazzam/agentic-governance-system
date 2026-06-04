#!/usr/bin/env bash
set -euo pipefail

VALID_ENVS=("dev" "test" "stage" "prod")
FUNCTIONS_DIR="src/functions"
ENV="${1:-}"
FUNCTION="${2:-}"

if [ -z "$ENV" ] || [[ ! " ${VALID_ENVS[*]} " =~ " ${ENV} " ]]; then
  echo "Usage: $0 <environment> [function-name]" >&2
  echo "Valid environments: ${VALID_ENVS[*]}" >&2
  echo "If function-name is omitted, all functions are deployed." >&2
  exit 1
fi

BUCKET="ai-foundry-registry-${ENV}-lambdazip-bucket"

deploy_function() {
  local func_name="$1"
  local zip_file="${func_name}.zip"
  local lambda_name="ai-foundry-registry-${ENV}-${func_name}-lambda"

  if [ ! -f "$zip_file" ]; then
    echo "ERROR: ${zip_file} not found. Run scripts/build.sh ${func_name} first." >&2
    exit 1
  fi

  echo "Uploading ${zip_file} to s3://${BUCKET}..."
  aws s3 cp "$zip_file" "s3://${BUCKET}/${zip_file}"

  echo "Updating Lambda function ${lambda_name}..."
  aws lambda update-function-code \
    --function-name "${lambda_name}" \
    --s3-bucket "${BUCKET}" \
    --s3-key "${zip_file}"

  echo "Deployed: ${func_name}"
}

if [ -n "$FUNCTION" ]; then
  # Deploy a single function
  deploy_function "$FUNCTION"
else
  # Deploy all functions
  echo "Deploying all functions..."
  for func_dir in "${FUNCTIONS_DIR}"/*/; do
    func_name=$(basename "$func_dir")
    deploy_function "$func_name"
  done
fi

echo "Deploy complete."
