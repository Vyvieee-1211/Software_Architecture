"""Validate responses; arbitrary HTTP 4xx must never count as success."""


def classify(operation, status, body):
    if operation == "buy" and status == 409 and isinstance(body, dict) and body.get("error") == "SoldOut":
        return "sold_out", True
    if status != (201 if operation == "buy" else 200):
        return ("transport_error" if not status else f"http_{status}"), False
    if operation == "login":
        valid = isinstance(body, dict) and isinstance(body.get("access_token"), str) and bool(body["access_token"])
    elif operation == "detail":
        valid = (isinstance(body, dict) and isinstance(body.get("id"), int)
                 and isinstance(body.get("name"), str) and bool(body["name"]))
    elif operation in ("buy", "cancel"):
        valid = (isinstance(body, dict) and isinstance(body.get("id"), int)
                 and isinstance(body.get("quantity"), int) and body["quantity"] > 0
                 and body.get("status") == ("confirmed" if operation == "buy" else "cancelled"))
    elif operation in ("concerts", "tickets", "orders"):
        valid = isinstance(body, list) and all(isinstance(row, dict) and isinstance(row.get("id"), int) for row in body)
        if valid and operation == "tickets":
            valid = all(isinstance(row.get("remaining"), int) and row["remaining"] >= 0 for row in body)
        if valid and operation == "orders":
            valid = all(row.get("status") in ("confirmed", "cancelled") for row in body)
    else:
        valid = False
    return ("ok", True) if valid else ("invalid_response", False)
