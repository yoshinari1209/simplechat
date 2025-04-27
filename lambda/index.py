import json
import os
import re
import time
from urllib import request, error, parse

try:
    COLAB_BASE_URL = os.environ["COLAB_BASE_URL"].rstrip("/")
except KeyError:
    raise RuntimeError("環境変数 COLAB_BASE_URL が設定されていません")
COLAB_API_KEY = os.getenv("COLAB_API_KEY") or None

GENERATE_PATH = "/generate"
HEALTH_PATH   = "/health"

def extract_region_from_arn(arn: str) -> str:
    m = re.search(r"arn:aws:lambda:([^:]+):", arn)
    return m.group(1) if m else "us-east-1"

def _build_request(url: str, payload: dict | None = None) -> request.Request:
    data = json.dumps(payload).encode("utf-8") if payload else None
    req  = request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    if COLAB_API_KEY:
        req.add_header("Authorization", f"Bearer {COLAB_API_KEY}")
    return req

def _call_fastapi(path: str, payload: dict | None = None, timeout: int = 30):
    url = parse.urljoin(COLAB_BASE_URL, path)
    req = _build_request(url, payload)
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def lambda_handler(event, context):
    region = extract_region_from_arn(context.invoked_function_arn)
    print(f"[Lambda][{region}] Event: {json.dumps(event)[:400]}")

    try:
        health = _call_fastapi(HEALTH_PATH, payload=None, timeout=5)
        print(f"[Lambda] /health OK: {health}")
    except Exception as e:
        print(f"[Lambda] /health NG: {e}")

    try:
        body        = json.loads(event["body"])
        user_msg    = body["message"]
        history     = body.get("conversationHistory", [])

        prompt_parts = [f"{m['role'].capitalize()}: {m['content']}" for m in history]
        prompt_parts.append(f"User: {user_msg}\nAssistant:")
        prompt_text = "\n".join(prompt_parts)

        payload = {
            "prompt":         prompt_text,
            "max_new_tokens": 512,
            "temperature":    0.7,
            "top_p":          0.9,
            "do_sample":      True,
        }

        print(f"[Lambda] POST → {COLAB_BASE_URL + GENERATE_PATH}")

        t0 = time.time()
        result = _call_fastapi(GENERATE_PATH, payload, timeout=60)
        elapsed = time.time() - t0

        assistant_reply = result.get("generated_text")
        if not assistant_reply:
            raise ValueError("FastAPI から 'generated_text' が返りませんでした")

        print(
            f"[Lambda] FastAPI resp_time={result['response_time']:.2f}s "
            f"total={elapsed:.2f}s"
        )

        history.append({"role": "assistant", "content": assistant_reply})
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,Authorization",
                "Access-Control-Allow-Methods": "OPTIONS,POST",
            },
            "body": json.dumps(
                {
                    "success": True,
                    "response": assistant_reply,
                    "conversationHistory": history,
                }
            ),
        }

    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        return _error_response(f"HTTPError {e.code}: {body}")

    except error.URLError as e:
        return _error_response(f"URLError: {e.reason}")

    except Exception as e:
        return _error_response(str(e))

def _error_response(message: str):
    print(f"[Lambda][ERROR] {message}")
    return {
        "statusCode": 500,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,POST",
        },
        "body": json.dumps({"success": False, "error": message}),
    }
