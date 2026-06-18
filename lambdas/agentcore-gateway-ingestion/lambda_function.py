"""
agentcore-gateway-ingestion
---------------------------
Scans all AgentCore Gateways in eu-central-1 and ingests them as registry
records in eu-west-1. Tries MCP descriptor first; falls back to CUSTOM for
JWT-protected gateways where the MCP schema cannot be satisfied.

Invocation: sync (InvocationType=RequestResponse) — completes in ~10s
"""

import boto3
import json
import os
import time

control  = boto3.client("bedrock-agentcore-control", region_name="eu-central-1")
registry = boto3.client("bedrock-agentcore-control", region_name="eu-west-1")

REGISTRY_ID = os.environ.get("REGISTRY_ID")


def get_all_gateways():
    gateways = []
    kwargs   = {}
    while True:
        response = control.list_gateways(**kwargs)
        gateways.extend(response.get("items", []))
        if not response.get("nextToken"):
            break
        kwargs["nextToken"] = response["nextToken"]
    return gateways


def build_mcp_content(detail, list_data):
    """
    MCP-compliant inlineContent. description MUST be non-empty or
    schema validation fails.
    """
    description = ""
    if detail and detail.get("description"):
        description = detail["description"]
    elif list_data.get("description"):
        description = list_data["description"]
    else:
        description = "MCP Gateway. Auth: " + list_data.get("authorizerType", "NONE")

    return json.dumps({
        "name":        "syngenta.ai/" + list_data["name"],
        "description": description,
        "version":     "1.0.0",
    })


def build_custom_content(detail, targets, list_data):
    content = {
        "gatewayId":    list_data["gatewayId"],
        "gatewayArn":   detail.get("gatewayArn", "") if detail else "",
        "gatewayUrl":   detail.get("gatewayUrl", "") if detail else "",
        "protocolType": list_data.get("protocolType", "MCP"),
        "authType":     list_data.get("authorizerType", "NONE"),
        "status":       list_data.get("status", "READY"),
        "createdAt":    str(list_data.get("createdAt", "")),
        "updatedAt":    str(list_data.get("updatedAt", "")),
        "targets": [
            {
                "targetId":    t.get("targetId", ""),
                "name":        t.get("name", ""),
                "status":      t.get("status", ""),
                "description": t.get("description", ""),
            }
            for t in targets
        ],
    }

    if detail:
        auth = detail.get("authorizerConfiguration", {})
        jwt  = auth.get("customJWTAuthorizer", {})
        if jwt:
            content["allowedAudience"] = jwt.get("allowedAudience", [])
            content["allowedScopes"]   = jwt.get("allowedScopes", [])
        interceptors = detail.get("interceptorConfigurations", [])
        if interceptors:
            content["interceptors"] = [
                i.get("interceptor", {}).get("lambda", {}).get("arn", "")
                for i in interceptors
            ]

    return json.dumps(content)


def build_description(detail, targets, list_data):
    if detail and detail.get("description"):
        return detail["description"][:4096]
    if list_data.get("description"):
        return list_data["description"][:4096]
    parts        = ["MCP Gateway.", "Auth: " + list_data.get("authorizerType", "NONE") + "."]
    target_names = [t.get("name", "") for t in targets if t.get("status") == "READY"]
    if target_names:
        parts.append("Tools: " + ", ".join(target_names) + ".")
    return " ".join(parts)


def lambda_handler(event, context):
    print("Starting gateway ingestion into registry...")

    gateways = get_all_gateways()
    print("Found " + str(len(gateways)) + " gateways")

    success = []
    failed  = []

    for gw in gateways:
        gw_id = gw["gatewayId"]
        name  = gw["name"]

        # Fetch full details — graceful fallback if access denied
        detail = None
        try:
            detail = control.get_gateway(gatewayIdentifier=gw_id)
            detail.pop("ResponseMetadata", None)
        except Exception as e:
            print("Error: " + str(e))

        # Fetch targets — empty list if access denied
        targets = []
        try:
            targets_resp = control.list_gateway_targets(gatewayIdentifier=gw_id)
            targets      = targets_resp.get("items", [])
        except Exception:
            print("  ListGatewayTargets denied for " + name)

        description = build_description(detail, targets, gw)
        ingested    = False
        record_id   = None
        record_type = None

        # Try MCP descriptor first
        try:
            mcp_content = build_mcp_content(detail, gw)
            response = registry.create_registry_record(
                registryId=REGISTRY_ID,
                name=name,
                descriptorType="MCP",
                descriptors={
                    "mcp": {
                        "server": {
                            "schemaVersion": "2025-12-11",
                            "inlineContent": mcp_content,
                        }
                    }
                },
                recordVersion="1.0",
                description=description,
            )
            record_id   = response["recordArn"].split("/")[-1]
            record_type = "MCP"
            ingested    = True
            print("  Ingested (MCP): " + name + " — " + record_id)
        except Exception as e:
            print("  MCP failed for " + name + " — " + str(e)[:80])

        # Fallback to CUSTOM
        if not ingested:
            try:
                custom_content = build_custom_content(detail, targets, gw)
                response = registry.create_registry_record(
                    registryId=REGISTRY_ID,
                    name=name,
                    descriptorType="CUSTOM",
                    descriptors={"custom": {"inlineContent": custom_content}},
                    recordVersion="1.0",
                    description=description,
                )
                record_id   = response["recordArn"].split("/")[-1]
                record_type = "CUSTOM"
                ingested    = True
                print("  Ingested (CUSTOM): " + name + " — " + record_id)
            except Exception as e:
                print("  FAILED: " + name + " — " + str(e)[:100])
                failed.append({"name": name, "error": str(e)})

        if ingested:
            success.append({
                "name":     name,
                "recordId": record_id,
                "type":     record_type,
                "targets":  len(targets),
                "authType": gw.get("authorizerType", "NONE"),
            })

        time.sleep(0.3)

    print("SUCCESS: " + str(len(success)) + "/" + str(len(gateways)))
    print("FAILED:  " + str(len(failed))  + "/" + str(len(gateways)))

    return {
        "statusCode": 200,
        "body": json.dumps({
            "summary": {
                "total":   len(gateways),
                "success": len(success),
                "failed":  len(failed),
            },
            "success": success,
            "failed":  failed,
        }, indent=2),
    }
