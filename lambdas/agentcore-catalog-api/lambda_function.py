"""
agentcore-catalog-api
---------------------
Catalog and governance API for the AgentCore Registry frontend.
Reads live data from the registry in eu-west-1 and exposes it via
API Gateway (nhrfwhwue4, eu-central-1).

Endpoints:
  GET  /records                   list + filter (?status= ?type= ?protocol= ?search=)
  GET  /records/{id}              single record with full metadata
  PUT  /records/{id}              update description / tags  (DRAFT only)
  POST /records/{id}/submit       DRAFT → PENDING_APPROVAL
  PUT  /records/{id}/status       APPROVED / REJECTED / DEPRECATED
"""

import boto3
import json
import os
import time

registry    = boto3.client("bedrock-agentcore-control", region_name="eu-west-1")
REGISTRY_ID = os.environ.get("REGISTRY_ID")
CORS        = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}


# ── helpers ───────────────────────────────────────────────────────────────────

def ok(body):
    return {"statusCode": 200, "headers": CORS, "body": json.dumps(body, default=str)}


def err(code, message):
    return {"statusCode": code, "headers": CORS, "body": json.dumps({"error": message})}


def get_record(record_id):
    try:
        d = registry.get_registry_record(registryId=REGISTRY_ID, recordId=record_id)
        d.pop("ResponseMetadata", None)
        return d, None
    except Exception as e:
        msg = str(e)
        if "ResourceNotFoundException" in msg or "not found" in msg.lower():
            return None, "Record not found"
        return None, msg


def wait_for_stable(record_id, timeout=15):
    """Wait until record leaves UPDATING state (max timeout seconds)."""
    for _ in range(timeout):
        d, e = get_record(record_id)
        if e or d["status"] != "UPDATING":
            return d, e
        time.sleep(1)
    return get_record(record_id)


def normalize(d):
    """
    Normalise a raw registry record into a consistent shape for the frontend.
    Parses inlineContent according to descriptor type and extracts a metadata object.
    """
    dtype    = d.get("descriptorType", "")
    metadata = {}

    try:
        if dtype == "A2A":
            inline  = (d.get("descriptors", {})
                        .get("a2a", {})
                        .get("agentCard", {})
                        .get("inlineContent", "{}"))
            content = json.loads(inline)
            metadata = {
                "protocol":     "A2A",
                "authType":     "IAM",
                "networkMode":  "PUBLIC",
                "capabilities": content.get("capabilities", {}),
                "skills":       content.get("skills", []),
                "url":          content.get("url", ""),
                "tags":         content.get("tags", {}),
            }

        elif dtype == "MCP":
            inline  = (d.get("descriptors", {})
                        .get("mcp", {})
                        .get("server", {})
                        .get("inlineContent", "{}"))
            content = json.loads(inline)
            desc    = d.get("description", "")
            metadata = {
                "protocol":   "MCP",
                "authType":   "CUSTOM_JWT" if ("JWT" in desc or "Entra" in desc) else "NONE",
                "serverName": content.get("name", ""),
                "version":    content.get("version", ""),
                "tags":       content.get("tags", {}),
            }

        elif dtype == "CUSTOM":
            inline  = (d.get("descriptors", {})
                        .get("custom", {})
                        .get("inlineContent", "{}"))
            content = json.loads(inline)
            metadata = {
                "protocol":    content.get("protocol", content.get("protocolType", "CUSTOM")),
                "authType":    content.get("authType", "N/A"),
                "networkMode": content.get("networkMode", ""),
                "tags":        content.get("tags", {}),
            }
            # Agent fields
            for k in ("agentRuntimeArn", "version", "model", "serviceName",
                      "observability", "gateways", "container"):
                if content.get(k) is not None:
                    metadata[k] = content[k]
            # Gateway fields
            for k in ("gatewayId", "gatewayUrl", "targets",
                      "allowedAudience", "interceptors"):
                if content.get(k) is not None:
                    metadata[k] = content[k]

    except Exception as e:
        print("normalize error: " + str(e))

    return {
        "recordId":       d["recordId"],
        "recordArn":      d.get("recordArn", ""),
        "name":           d["name"],
        "description":    d.get("description", ""),
        "descriptorType": dtype,
        "status":         d["status"],
        "recordVersion":  d.get("recordVersion", ""),
        "createdAt":      str(d.get("createdAt", "")),
        "updatedAt":      str(d.get("updatedAt", "")),
        "metadata":       metadata,
    }


def build_descriptors(d, new_desc=None, new_tags=None):
    """
    Build the descriptors update payload. description and tags are both
    injected into inlineContent — the registry API does not accept
    description as a top-level string on update.
    """
    dtype = d["descriptorType"]

    def patch(content):
        if new_desc is not None:
            content["description"] = new_desc
        if new_tags is not None:
            content["tags"] = new_tags
        return content

    if dtype == "CUSTOM":
        inline  = (d.get("descriptors", {})
                    .get("custom", {})
                    .get("inlineContent", "{}"))
        content = patch(json.loads(inline))
        return {"optionalValue": {"custom": {"optionalValue": {
            "inlineContent": json.dumps(content)
        }}}}

    elif dtype == "MCP":
        inline  = (d.get("descriptors", {})
                    .get("mcp", {})
                    .get("server", {})
                    .get("inlineContent", "{}"))
        content = patch(json.loads(inline))
        return {"optionalValue": {"mcp": {"optionalValue": {"server": {
            "optionalValue": {"inlineContent": json.dumps(content)}
        }}}}}

    elif dtype == "A2A":
        inline  = (d.get("descriptors", {})
                    .get("a2a", {})
                    .get("agentCard", {})
                    .get("inlineContent", "{}"))
        content = patch(json.loads(inline))
        return {"optionalValue": {"a2a": {"optionalValue": {"agentCard": {
            "optionalValue": {"inlineContent": json.dumps(content)}
        }}}}}

    return {}


# ── handler ───────────────────────────────────────────────────────────────────

def handler(event, context):
    path   = event.get("path", "/")
    method = event.get("httpMethod", "GET")
    params = event.get("queryStringParameters") or {}
    body   = {}

    if event.get("body"):
        try:
            body = json.loads(event["body"])
        except Exception:
            return err(400, "Invalid JSON body")

    parts     = [p for p in path.strip("/").split("/") if p]
    record_id = parts[1] if len(parts) >= 2 else None

    print("REQUEST: " + method + " " + path)

    # ── GET /records ─────────────────────────────────────────────────────────
    if method == "GET" and path.rstrip("/") == "/records":
        try:
            raw = []
            kw  = {"registryId": REGISTRY_ID}
            while True:
                resp = registry.list_registry_records(**kw)
                raw.extend(resp["registryRecords"])
                if not resp.get("nextToken"):
                    break
                kw["nextToken"] = resp["nextToken"]

            records = []
            for r in raw:
                d, e = get_record(r["recordId"])
                if d:
                    records.append(normalize(d))

            if params.get("status"):
                # Support comma-separated values e.g. ?status=DRAFT,REJECTED
                statuses = [s.strip() for s in params["status"].split(",")]
                records  = [r for r in records if r["status"] in statuses]
            if params.get("type"):
                records = [r for r in records if r["descriptorType"] == params["type"]]
            if params.get("protocol"):
                p = params["protocol"].upper()
                records = [r for r in records
                           if r.get("metadata", {}).get("protocol", "").upper() == p]
            if params.get("search"):
                s = params["search"].lower()
                records = [r for r in records
                           if s in r["name"].lower()
                           or s in r.get("description", "").lower()]

            return ok({"records": records, "total": len(records)})

        except Exception as e:
            print("GET /records error: " + str(e))
            return err(500, str(e))

    # ── GET /records/{id} ────────────────────────────────────────────────────
    if method == "GET" and len(parts) == 2 and parts[0] == "records":
        d, e = get_record(record_id)
        if e:
            return err(404, e)
        return ok(normalize(d))

    # ── PUT /records/{id} — update description / tags ────────────────────────
    if method == "PUT" and len(parts) == 2 and parts[0] == "records":
        d, e = get_record(record_id)
        if e:
            return err(404, e)
        if d["status"] not in ("DRAFT",):
            return err(409, "Only DRAFT records can be updated. Current status: " + d["status"])

        new_desc = body.get("description")
        new_tags = body.get("tags")
        if new_desc is None and new_tags is None:
            return err(400, "Provide at least one of: description, tags")

        try:
            descriptors = build_descriptors(d, new_desc=new_desc, new_tags=new_tags)
            registry.update_registry_record(
                registryId=REGISTRY_ID,
                recordId=record_id,
                descriptors=descriptors,
            )
            stable, _ = wait_for_stable(record_id)
            return ok({"message": "Record updated", "record": normalize(stable)})
        except Exception as e:
            print("PUT /records/{id} error: " + str(e))
            return err(500, str(e))

    # ── POST /records/{id}/submit ─────────────────────────────────────────────
    if (method == "POST" and len(parts) == 3
            and parts[0] == "records" and parts[2] == "submit"):
        d, e = get_record(record_id)
        if e:
            return err(404, e)
        if d["status"] == "UPDATING":
            d, e = wait_for_stable(record_id)
            if e:
                return err(500, e)
        if d["status"] != "DRAFT":
            return err(409, "Only DRAFT records can be submitted. Current status: " + d["status"])
        try:
            registry.submit_registry_record_for_approval(
                registryId=REGISTRY_ID, recordId=record_id)
            updated, _ = get_record(record_id)
            return ok({"message": "Record submitted for approval", "record": normalize(updated)})
        except Exception as e:
            print("POST /submit error: " + str(e))
            return err(500, str(e))

    # ── PUT /records/{id}/status — approve / reject / deprecate ──────────────
    if (method == "PUT" and len(parts) == 3
            and parts[0] == "records" and parts[2] == "status"):
        new_status = body.get("status", "").upper()
        if new_status not in ("APPROVED", "REJECTED", "DEPRECATED"):
            return err(400, "status must be one of: APPROVED, REJECTED, DEPRECATED")

        d, e = get_record(record_id)
        if e:
            return err(404, e)

        current = d["status"]
        valid   = {
            "APPROVED":   ["PENDING_APPROVAL"],
            "REJECTED":   ["PENDING_APPROVAL"],
            "DEPRECATED": ["APPROVED"],
        }
        if current not in valid.get(new_status, []):
            return err(409,
                "Cannot move from " + current + " to " + new_status +
                ". Record must be in: " + str(valid[new_status]))

        try:
            # statusReason is required by the AWS API for all transitions
            reason = body.get("reason") or new_status
            registry.update_registry_record_status(
                registryId=REGISTRY_ID,
                recordId=record_id,
                status=new_status,
                statusReason=reason,
            )
            updated, _ = get_record(record_id)
            return ok({
                "message": "Record status updated to " + new_status,
                "record":  normalize(updated),
            })
        except Exception as e:
            print("PUT /status error: " + str(e))
            return err(500, str(e))

    return err(404, "Endpoint not found: " + method + " " + path)
