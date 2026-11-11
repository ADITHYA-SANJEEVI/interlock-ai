"""Snapshot and verify every route and its OpenAPI contract without database access."""

import argparse
import hashlib
import json
import runpy
from pathlib import Path

from fastapi.routing import APIRoute
from interlock import api, authority, broker, reference, returns, seller

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "docs/evidence/pass7/api-contracts.json"


def inventory():
    groups = runpy.run_path(str(ROOT / "tests/pass6_matrix.py"))["ROUTES"]
    services = {}
    for name, app in (("api", api.app), ("seller", seller.app),
                      ("broker", broker.app), ("reference", reference.app)):
        schema = app.openapi()
        routes = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in sorted(route.methods):
                key = method + " " + route.path
                category = next(group for group, paths in groups[name].items() if key in paths)
                operation = schema["paths"][route.path][method.lower()]
                pagination = "none; exact resource or mutation result"
                if method == "GET" and route.path in {"/reconciliation/runs", "/reconciliation/findings"}:
                    pagination = "UUID cursor (exclusive id > cursor); limit 1..50, default 20; id ASC"
                elif method == "GET" and route.path == "/purchases":
                    pagination = "newest 50, created_at DESC/id DESC; no continuation token"
                elif method == "GET" and route.path in {"/authority", "/dispatch", "/returns"}:
                    pagination = "bounded snapshot; collections <=100; excess produces 429; no continuation"
                elif method == "GET" and route.path.startswith("/economic/"):
                    pagination = "exact operation; collections <=100; excess produces 429"
                body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
                response = {status: value.get("content", {}).get("application/json", {}).get("schema")
                            for status, value in operation["responses"].items() if status.startswith("2")}
                families = None
                if name == "api" and route.path in {"/authority/commands/{family}", "/returns/commands/{family}"}:
                    commands = authority.COMMANDS if route.path.startswith("/authority") else returns.COMMANDS
                    families = {family: model.__name__ for family, model in commands.items()}
                auth = category
                if name == "seller" and route.path.startswith(("/workspace/", "/truth/workspace/")):
                    auth = "SELLER USER TOKEN (Authorization: Bearer); tenant and beneficiary bound"
                elif "webhooks" in route.path:
                    auth = "AUTHENTICATED EVIDENCE CHANNEL; raw-body signature, no browser identity"
                routes.append({"method": method, "route": route.path, "auth_class": auth,
                               "boundary_class": category, "request_model": body,
                               "request_families": families, "response_model": response,
                               "parameters": operation.get("parameters", []), "pagination": pagination,
                               "classification": "read" if method == "GET" else "mutation",
                               "ui_consumable": name == "api" and "webhooks" not in route.path
                               or name == "seller" and "/workspace/" in route.path})
        serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))
        services[name] = {"openapi_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
                          "routes": sorted(routes, key=lambda x: (x["route"], x["method"])),
                          "schemas": schema.get("components", {}).get("schemas", {})}
    return {"migration_head": "0020_pass6_boundaries", "services": services}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Explicitly generate a reviewed release snapshot")
    args = parser.parse_args()
    actual = inventory()
    if args.write:
        DEST.parent.mkdir(parents=True, exist_ok=True)
        DEST.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    else:
        assert actual == json.loads(DEST.read_text()), "API/schema drift requires separate backend justification"
    print(json.dumps({"verified": not args.write,
                      "routes": {name: len(value["routes"]) for name, value in actual["services"].items()}}))


if __name__ == "__main__":
    main()
