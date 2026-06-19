#!/usr/bin/env bash
# Post-deploy script: attaches the Lambda authorizer to all /records routes
# on the external HTTP API managed by Terraform.
#
# Required env vars (sourced from .env.<stage>):
#   HTTP_API_ID    - API Gateway HTTP API ID
#   AUTHORIZER_ID  - existing authorizer ID on the API
#   AWS_REGION     - AWS region
#
# Usage: ./scripts/attach-authorizer.sh

set -euo pipefail

: "${HTTP_API_ID:?HTTP_API_ID is required}"
: "${AUTHORIZER_ID:?AUTHORIZER_ID is required}"
: "${AWS_REGION:?AWS_REGION is required}"

AUTHORIZER_FUNCTION="ai-foundry-registry-${STAGE:-dev}-authorizer-lambda"
AUTHORIZER_ARN=$(aws lambda get-function \
  --function-name "$AUTHORIZER_FUNCTION" \
  --region "$AWS_REGION" \
  --query "Configuration.FunctionArn" \
  --output text)

echo "Authorizer Lambda ARN: $AUTHORIZER_ARN"

# Update the authorizer to point to the current Lambda ARN
aws apigatewayv2 update-authorizer \
  --api-id "$HTTP_API_ID" \
  --authorizer-id "$AUTHORIZER_ID" \
  --authorizer-uri "arn:aws:apigateway:${AWS_REGION}:lambda:path/2015-03-31/functions/${AUTHORIZER_ARN}/invocations" \
  --region "$AWS_REGION" \
  --output text > /dev/null

echo "Updated authorizer $AUTHORIZER_ID -> $AUTHORIZER_FUNCTION"

# Ensure the API Gateway has permission to invoke the authorizer Lambda
aws lambda add-permission \
  --function-name "$AUTHORIZER_FUNCTION" \
  --statement-id "apigateway-invoke-authorizer" \
  --action "lambda:InvokeFunction" \
  --principal "apigateway.amazonaws.com" \
  --source-arn "arn:aws:execute-api:${AWS_REGION}:$(aws sts get-caller-identity --query Account --output text):${HTTP_API_ID}/authorizers/${AUTHORIZER_ID}" \
  --region "$AWS_REGION" \
  --output text > /dev/null 2>&1 || echo "Permission already exists (skipped)"

# Attach authorizer to all /records routes
ROUTE_IDS=$(aws apigatewayv2 get-routes \
  --api-id "$HTTP_API_ID" \
  --region "$AWS_REGION" \
  --query "Items[?contains(RouteKey, '/records')].RouteId" \
  --output text)

for ROUTE_ID in $ROUTE_IDS; do
  ROUTE_KEY=$(aws apigatewayv2 get-route \
    --api-id "$HTTP_API_ID" \
    --route-id "$ROUTE_ID" \
    --region "$AWS_REGION" \
    --query "RouteKey" \
    --output text)

  aws apigatewayv2 update-route \
    --api-id "$HTTP_API_ID" \
    --route-id "$ROUTE_ID" \
    --authorization-type CUSTOM \
    --authorizer-id "$AUTHORIZER_ID" \
    --region "$AWS_REGION" \
    --output text > /dev/null

  echo "Attached authorizer to route: $ROUTE_KEY"
done

echo "Done — authorizer attached to all /records routes"
