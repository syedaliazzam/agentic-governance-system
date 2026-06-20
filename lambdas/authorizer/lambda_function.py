"""
authorizer
----------
Lambda Authorizer for API Gateway HTTP API (payload format 2.0).

Validates the Bearer JWT issued by the AgentCore Registry Cognito User Pool,
extracts the caller's group (publishers / admins / consumers) and sub claim,
and returns a simple IAM policy (Allow/Deny) along with the user's identity
as context, which API Gateway forwards to downstream Lambdas via
event["requestContext"]["authorizer"]["lambda"].

This authorizer is independent of, and does not replace, the existing
Entra ID JWT logic used inside each Lambda for AgentCore runtime metadata
access — that logic is untouched.

Env vars:
  COGNITO_USER_POOL_ID   e.g. eu-central-1_fm2gxLmKH
  COGNITO_APP_CLIENT_ID  e.g. 7snlt923hi80fl0b3400bsgtg0
  COGNITO_REGION         e.g. eu-central-1
"""

import json
import os
import time
import urllib.request

# python-jose is the standard library for verifying Cognito JWTs against JWKS.
# Bundle it via a Lambda layer (same pattern as the boto3-latest layer).
from jose import jwk, jwt
from jose.utils import base64url_decode

USER_POOL_ID  = os.environ.get("COGNITO_USER_POOL_ID", "")
APP_CLIENT_ID = os.environ.get("COGNITO_APP_CLIENT_ID", "")
REGION        = os.environ.get("COGNITO_REGION", "eu-central-1")

ISSUER   = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"

# Module-level cache so the JWKS keys are only fetched once per cold start,
# not on every invocation.
_jwks_cache = None


def get_jwks():
    global _jwks_cache
    if _jwks_cache is None:
        with urllib.request.urlopen(JWKS_URL, timeout=5) as resp:
            _jwks_cache = json.loads(resp.read())["keys"]
    return _jwks_cache


def get_signing_key(token):
    headers = jwt.get_unverified_header(token)
    kid     = headers.get("kid")
    keys    = get_jwks()
    for key in keys:
        if key["kid"] == kid:
            return key
    raise Exception("Signing key not found for kid: " + str(kid))


def verify_token(token):
    """
    Verify signature, expiry, issuer, and audience (token_use=id requires
    'aud'; token_use=access requires 'client_id'). Returns decoded claims
    on success, raises on any failure.
    """
    key_data    = get_signing_key(token)
    public_key  = jwk.construct(key_data)

    message, encoded_sig = token.rsplit(".", 1)
    decoded_sig = base64url_decode(encoded_sig.encode("utf-8"))

    if not public_key.verify(message.encode("utf-8"), decoded_sig):
        raise Exception("Signature verification failed")

    claims = jwt.get_unverified_claims(token)

    if time.time() > claims.get("exp", 0):
        raise Exception("Token expired")

    if claims.get("iss") != ISSUER:
        raise Exception("Invalid issuer")

    token_use = claims.get("token_use")
    if token_use == "id":
        if claims.get("aud") != APP_CLIENT_ID:
            raise Exception("Invalid audience")
    elif token_use == "access":
        if claims.get("client_id") != APP_CLIENT_ID:
            raise Exception("Invalid client_id")
    else:
        raise Exception("Unexpected token_use: " + str(token_use))

    return claims


def extract_group(claims):
    """
    Cognito groups appear as cognito:groups (a list) on both id and access
    tokens when the app client is configured to include them. Returns the
    first recognised group, or 'consumers' as the most restrictive default.
    """
    groups = claims.get("cognito:groups", [])
    for g in ("admins", "publishers", "consumers"):
        if g in groups:
            return g
    return "consumers"


def build_policy(principal_id, effect, resource, context=None):
    policy = {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource,
                }
            ],
        },
    }
    if context:
        policy["context"] = context
    return policy


def handler(event, context):
    """
    HTTP API Lambda authorizer (simple response format disabled — using the
    IAM policy response format so it is also REST-API compatible if needed).
    event["headers"]["authorization"] holds the Bearer token for HTTP APIs.
    """
    print("AUTHORIZER REQUEST: " + json.dumps({
        "routeArn": event.get("routeArn"),
        "rawPath":  event.get("rawPath"),
    }))

    headers    = event.get("headers", {}) or {}
    auth_header = headers.get("authorization") or headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        print("DENY: missing or malformed Authorization header")
        return build_policy("anonymous", "Deny", event.get("routeArn", "*"))

    token = auth_header[len("Bearer "):].strip()

    try:
        claims = verify_token(token)
    except Exception as e:
        print("DENY: token verification failed — " + str(e))
        return build_policy("anonymous", "Deny", event.get("routeArn", "*"))

    sub   = claims.get("sub", "unknown")
    group = extract_group(claims)

    print("ALLOW: sub=" + sub + " group=" + group)

    # Context values must be flat strings — API Gateway forwards these as
    # event["requestContext"]["authorizer"]["lambda"] to downstream Lambdas.
    return build_policy(
        sub,
        "Allow",
        event.get("routeArn", "*"),
        context={
            "sub":   sub,
            "group": group,
        },
    )
