# lambda/index.py
import json
import os
import re
import time
from urllib import request, error, parse

# ───────────────────────────────────────────────
# 環境変数
#   ・COLAB_BASE_URL : 例 "https://xxx-xxxx.ngrok-free.app"
#   ・COLAB_API_KEY  : Bearer 認証が必要な場合のみ
# ───────────────────────────────────────────────
COLAB_BASE_URL = os.environ["COLAB_BASE_URL"].rstrip("/")          # 必須
#COLAB_API_KEY  = os.getenv("COLAB_API_KEY")                        # 任意
GENERATE_PATH  = "/generate"                                       # FastAPI 側エンドポイント

# ───────────────────────────────────────────────
# Lambda ARN からリージョンを抜き取る（ログ用）
# ───────────────────────────────────────────────
def extract_region_from_arn(arn: str) -> str:
    m = re.search(r"arn:aws:lambda:([^:]+):", arn)
    return m.group(1) if m else "us-east-1"

# ───────────────────────────────────────────────
# Lambda ハンドラ
# ───────────────────────────────────────────────
def lambda_handler(event, context):
    try:
        region = extract_region_from_arn(context.invoked_function_arn)
        print(f"[Lambda][{region}] Event: {json.dumps(event)[:400]}")

        # ───────── フロントから受け取った内容 ─────────
        body        = json.loads(event["body"])
        user_msg    = body["message"]
        history     = body.get("conversationHistory", [])

        # ───────── 会話履歴を 1 本のプロンプトへ ─────────
        prompt_parts = [
            f"{m['role'].capitalize()}: {m['content']}" for m in history
        ]
        prompt_parts.append(f"User: {user_msg}\nAssistant:")
        prompt_text = "\n".join(prompt_parts)

        # ───────── FastAPI へ送るペイロード ─────────
        payload = {
            "prompt":           prompt_text,
            "max_new_tokens":   512,
            "temperature":      0.7,
            "top_p":            0.9,
            "do_sample":        True
        }
        data = json.dumps(payload).encode("utf-8")

        # ───────── HTTP リクエスト作成 ─────────
        url = parse.urljoin(COLAB_BASE_URL, GENERATE_PATH)
        req = request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if COLAB_API_KEY:
            req.add_header("Authorization", f"Bearer {COLAB_API_KEY}")

        print(f"[Lambda] POST → {url}")

        # ───────── 送信 & 受信 ─────────
        start = time.time()
        with request.urlopen(req, timeout=60) as resp:
            resp_body = resp.read().decode("utf-8")
        elapsed = time.time() - start

        result = json.loads(resp_body)
        assistant_reply = result.get("generated_text")
        if not assistant_reply:
            raise ValueError("FastAPI から 'generated_text' が返りませんでした。")

        print(f"[Lambda] FastAPI resp_time={result['response_time']:.2f}s  total={elapsed:.2f}s")

        # ───────── 履歴更新 & 正常レスポンス ─────────
        history.append({"role": "assistant", "content": assistant_reply})
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,Authorization",
                "Access-Control-Allow-Methods": "OPTIONS,POST"
            },
            "body": json.dumps({
                "success": True,
                "response": assistant_reply,
                "conversationHistory": history
            })
        }

    # ───────── 例外ハンドリング ─────────
    except error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        err_msg  = f"HTTPError {e.code}: {err_body}"
        print(f"[Lambda][ERROR] {err_msg}")
        return _error_response(err_msg)

    except error.URLError as e:
        err_msg = f"URLError: {e.reason}"
        print(f"[Lambda][ERROR] {err_msg}")
        return _error_response(err_msg)

    except Exception as e:
        err_msg = str(e)
        print(f"[Lambda][ERROR] {err_msg}")
        return _error_response(err_msg)


# ───────────────────────────────────────────────
# 汎用エラーレスポンス
# ───────────────────────────────────────────────
def _error_response(message: str):
    return {
        "statusCode": 500,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,POST"
        },
        "body": json.dumps({"success": False, "error": message})
    }

