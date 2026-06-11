"""
agentcore-registry-ingestion
----------------------------
Scans all AgentCore Runtimes in eu-central-1, filters to A2A and AGUI
protocol agents, and ingests them as registry records in eu-west-1.

Invocation: async (InvocationType=Event) — execution ~40s
"""

import boto3
import json
import os
import time
from collections import Counter

control  = boto3.client("bedrock-agentcore-control", region_name="eu-central-1")
data     = boto3.client(
    "bedrock-agentcore",
    region_name="eu-central-1",
    endpoint_url="https://bedrock-agentcore.eu-central-1.amazonaws.com"
)
registry = boto3.client("bedrock-agentcore-control", region_name="eu-west-1")

REGISTRY_ID = os.environ.get("REGISTRY_ID", "Oa1hXgCtdOnJp6GM")


def get_all_runtimes():
    runtimes = []
    kwargs   = {}
    while True:
        response = control.list_agent_runtimes(**kwargs)
        runtimes.extend(response["agentRuntimes"])
        next_token = response.get("nextToken")
        if not next_token:
            break
        kwargs["nextToken"] = next_token
    return runtimes


def build_rich_custom(detail):
    env      = detail.get("environmentVariables", {})
    protocol = detail.get("protocolConfiguration", {}).get("serverProtocol", "UNKNOWN")
    network  = detail.get("networkConfiguration", {}).get("networkMode", "UNKNOWN")
    auth     = detail.get("authorizerConfiguration", {})

    gateways = {}
    for k, v in env.items():
        if "GATEWAY_URL" in k:
            gateway_name = k.replace("_GATEWAY_URL", "").replace("_", " ").title()
            gateways[gateway_name] = v

    model = env.get("PORTKEY_MODEL_ID", "")
    if model and "/" in model:
        model = model.split("/")[-1]

    auth_type = "Entra ID JWT" if "customJWTAuthorizer" in auth else "IAM"

    container = (
        detail.get("agentRuntimeArtifact", {})
              .get("containerConfiguration", {})
              .get("containerUri", "")
    )
    if container and "/" in container:
        container = container.split("/")[-1]

    content = {
        "agentRuntimeArn": detail["agentRuntimeArn"],
        "protocol":        protocol,
        "networkMode":     network,
        "version":         str(detail.get("agentRuntimeVersion", "1")),
        "status":          detail.get("status", "READY"),
        "createdAt":       str(detail.get("createdAt", "")),
        "lastUpdatedAt":   str(detail.get("lastUpdatedAt", "")),
        "authType":        auth_type,
    }

    if model:
        content["model"] = model
    if gateways:
        content["gateways"] = gateways
    if env.get("OTEL_SERVICE_NAME"):
        content["serviceName"] = env["OTEL_SERVICE_NAME"]
    if env.get("AGENT_OBSERVABILITY_ENABLED"):
        content["observability"] = env["AGENT_OBSERVABILITY_ENABLED"] == "true"
    if container:
        content["container"] = container

    return json.dumps(content)


def build_description(detail):
    env      = detail.get("environmentVariables", {})
    protocol = str(detail.get("protocolConfiguration", {}).get("serverProtocol", "UNKNOWN"))
    network  = str(detail.get("networkConfiguration", {}).get("networkMode", "UNKNOWN"))

    parts   = []
    service = str(env.get("OTEL_SERVICE_NAME", ""))
    if service:
        parts.append("Service: " + service + ".")
    model = str(env.get("PORTKEY_MODEL_ID", ""))
    if model and "/" in model:
        model = model.split("/")[-1]
    if model:
        parts.append("Model: " + model + ".")
    gateways = [
        k.replace("_GATEWAY_URL", "").replace("_", " ").title()
        for k in env.keys() if "GATEWAY_URL" in k
    ]
    if gateways:
        parts.append("Gateways: " + ", ".join(gateways) + ".")
    parts.append("Protocol: " + protocol + ", Network: " + network + ".")
    if env.get("AGENT_OBSERVABILITY_ENABLED") == "true":
        parts.append("Observability enabled.")

    result = " ".join(parts)
    return result[:4096] if result else protocol + " agent"


def build_a2a_card_content(agent_card):
    return json.dumps({
        "name":               agent_card.get("name", ""),
        "description":        agent_card.get("description", ""),
        "version":            agent_card.get("version", "0.0.1"),
        "protocolVersion":    agent_card.get("protocolVersion", "0.3.0"),
        "preferredTransport": agent_card.get("preferredTransport", "JSONRPC"),
        "url":                agent_card.get("url", ""),
        "capabilities":       agent_card.get("capabilities", {}),
        "defaultInputModes":  agent_card.get("defaultInputModes", ["text"]),
        "defaultOutputModes": agent_card.get("defaultOutputModes", ["text"]),
        "skills":             agent_card.get("skills", []),
    })


def lambda_handler(event, context):
    import botocore
    print("boto3: " + boto3.__version__ + " | botocore: " + botocore.__version__)
    print("Starting runtime ingestion into registry...")

    runtimes = get_all_runtimes()
    print("Found " + str(len(runtimes)) + " total runtimes")

    target_runtimes = []
    skipped         = []

    for runtime in runtimes:
        detail   = control.get_agent_runtime(agentRuntimeId=runtime["agentRuntimeId"])
        protocol = detail.get("protocolConfiguration", {}).get("serverProtocol", "UNKNOWN")
        if protocol in ("A2A", "AGUI"):
            target_runtimes.append((runtime, detail, protocol))
        else:
            skipped.append({"name": runtime["agentRuntimeName"], "protocol": protocol})

    print("Targeting " + str(len(target_runtimes)) + " runtimes (A2A + AGUI)")
    print("Skipping  " + str(len(skipped)) + " runtimes")

    success = []
    failed  = []

    for runtime, detail, protocol in target_runtimes:
        name        = runtime["agentRuntimeName"]
        runtime_arn = detail["agentRuntimeArn"]
        record_id   = None
        record_type = None
        error       = None

        if protocol == "A2A":
            # Try IAM agent card first
            try:
                resp       = data.get_agent_card(agentRuntimeArn=runtime_arn)
                agent_card = resp["agentCard"]
                card_content = build_a2a_card_content(agent_card)
                description  = agent_card.get("description", "")[:4096]

                response = registry.create_registry_record(
                    registryId=REGISTRY_ID,
                    name=name,
                    descriptorType="A2A",
                    descriptors={
                        "a2a": {
                            "agentCard": {
                                "schemaVersion": "0.3",
                                "inlineContent": card_content,
                            }
                        }
                    },
                    recordVersion=agent_card.get("version", "1.0"),
                    description=description,
                )
                record_id   = response["recordArn"].split("/")[-1]
                record_type = "A2A (IAM)"
                print("  Ingested as A2A (IAM): " + name + " — " + record_id)

            except Exception as e:
                print("  A2A card failed for " + name + ": " + str(e)[:120] + ", falling back to CUSTOM")
                try:
                    response = registry.create_registry_record(
                        registryId=REGISTRY_ID,
                        name=name,
                        descriptorType="CUSTOM",
                        descriptors={"custom": {"inlineContent": build_rich_custom(detail)}},
                        recordVersion=str(detail.get("agentRuntimeVersion", "1")),
                        description=build_description(detail),
                    )
                    record_id   = response["recordArn"].split("/")[-1]
                    record_type = "CUSTOM (A2A fallback)"
                    print("  Ingested as CUSTOM fallback: " + name + " — " + record_id)
                except Exception as e2:
                    error = str(e2)
                    print("  FAILED: " + name + " — " + str(e2)[:120])

        elif protocol == "AGUI":
            try:
                response = registry.create_registry_record(
                    registryId=REGISTRY_ID,
                    name=name,
                    descriptorType="CUSTOM",
                    descriptors={"custom": {"inlineContent": build_rich_custom(detail)}},
                    recordVersion=str(detail.get("agentRuntimeVersion", "1")),
                    description=build_description(detail),
                )
                record_id   = response["recordArn"].split("/")[-1]
                record_type = "CUSTOM (AGUI)"
                print("  Ingested as CUSTOM (AGUI): " + name + " — " + record_id)
            except Exception as e:
                error = str(e)
                print("  FAILED: " + name + " — " + str(e)[:120])

        if record_id:
            success.append({"name": name, "type": record_type, "recordId": record_id})
        else:
            failed.append({"name": name, "error": error})

        time.sleep(0.5)

    types = Counter(r["type"] for r in success)
    print("SUCCESS: " + str(len(success)) + "/" + str(len(target_runtimes)))
    print("FAILED:  " + str(len(failed))  + "/" + str(len(target_runtimes)))
    print("SKIPPED: " + str(len(skipped)))
    print("Breakdown: " + str(dict(types)))

    result = {
        "summary": {
            "total_runtimes": len(runtimes),
            "targeted":       len(target_runtimes),
            "ingested":       len(success),
            "failed":         len(failed),
            "skipped":        len(skipped),
            "breakdown":      dict(types),
        },
        "success": success,
        "failed":  failed,
        "skipped": skipped,
    }
    print(json.dumps(result, indent=2))
    return {"statusCode": 200, "body": json.dumps(result)}
