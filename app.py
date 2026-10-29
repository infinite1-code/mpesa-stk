import os
import re
import base64
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# ============================================================
# DARAJA CONFIGURATION
# ============================================================

DARAJA_ENV = os.getenv("DARAJA_ENV", "production").strip().lower()

if DARAJA_ENV == "production":
    BASE_URL = "https://api.safaricom.co.ke"
elif DARAJA_ENV == "sandbox":
    BASE_URL = "https://sandbox.safaricom.co.ke"
else:
    raise RuntimeError(
        "DARAJA_ENV must be either 'production' or 'sandbox'."
    )

CONSUMER_KEY = os.getenv("DARAJA_CONSUMER_KEY", "").strip()
CONSUMER_SECRET = os.getenv("DARAJA_CONSUMER_SECRET", "").strip()
SHORTCODE = os.getenv("DARAJA_SHORTCODE", "").strip()
PASSKEY = os.getenv("DARAJA_PASSKEY", "").strip()
CALLBACK_URL = os.getenv("DARAJA_CALLBACK_URL", "").strip()

# Set this to:
#   CustomerPayBillOnline  for a supported PayBill integration
#   CustomerBuyGoodsOnline for a supported Buy Goods Till integration
TRANSACTION_TYPE = os.getenv(
    "DARAJA_TRANSACTION_TYPE",
    "CustomerBuyGoodsOnline",
).strip()

# For a Till, set this to the actual Till number when required.
# For a PayBill, this normally matches the business shortcode.
PARTY_B = os.getenv("DARAJA_PARTY_B", "").strip() or SHORTCODE

MIN_DONATION = 1
MAX_DONATION = 150000

# IMPORTANT:
# This in-memory store is only suitable for a local/demo example.
# Vercel serverless instances do not share memory reliably.
# Use a persistent database before relying on payment status in production.
transactions = {}
transactions_lock = threading.Lock()


# ============================================================
# VALIDATION
# ============================================================

def normalize_phone(phone):
    digits = re.sub(r"\D", "", str(phone or ""))

    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("254") and len(digits) == 12:
        pass
    elif digits.startswith("7") and len(digits) == 9:
        digits = "254" + digits
    elif digits.startswith("1") and len(digits) == 9:
        digits = "254" + digits

    if not re.fullmatch(r"254[17]\d{8}", digits):
        raise ValueError(
            "Enter a valid Kenyan mobile number, such as 0712345678."
        )

    return digits


def validate_amount(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Please enter an amount.")

    amount_text = str(value).strip()

    if not re.fullmatch(r"\d+", amount_text):
        raise ValueError("Enter a whole-number amount in KSh.")

    amount = int(amount_text)

    if amount < MIN_DONATION:
        raise ValueError(f"The minimum amount is KSh {MIN_DONATION}.")

    if amount > MAX_DONATION:
        raise ValueError(
            f"The maximum amount is KSh {MAX_DONATION:,}."
        )

    return amount


def validate_configuration():
    missing = []

    if not CONSUMER_KEY:
        missing.append("DARAJA_CONSUMER_KEY")
    if not CONSUMER_SECRET:
        missing.append("DARAJA_CONSUMER_SECRET")
    if not SHORTCODE:
        missing.append("DARAJA_SHORTCODE")
    if not PASSKEY:
        missing.append("DARAJA_PASSKEY")
    if not CALLBACK_URL:
        missing.append("DARAJA_CALLBACK_URL")
    if not PARTY_B:
        missing.append("DARAJA_PARTY_B")

    if missing:
        raise RuntimeError(
            "Missing environment variables: " + ", ".join(missing)
        )

    if not CALLBACK_URL.startswith("https://"):
        raise RuntimeError("The callback URL must use HTTPS.")

    allowed_types = {
        "CustomerPayBillOnline",
        "CustomerBuyGoodsOnline",
    }

    if TRANSACTION_TYPE not in allowed_types:
        raise RuntimeError(
            "DARAJA_TRANSACTION_TYPE must be "
            "CustomerPayBillOnline or CustomerBuyGoodsOnline."
        )


# ============================================================
# DARAJA AUTHENTICATION
# ============================================================

def get_access_token():
    validate_configuration()

    response = requests.get(
        f"{BASE_URL}/oauth/v1/generate",
        params={"grant_type": "client_credentials"},
        auth=(CONSUMER_KEY, CONSUMER_SECRET),
        timeout=20,
    )

    if not response.ok:
        app.logger.error(
            "Daraja OAuth failed: HTTP %s; response=%s",
            response.status_code,
            response.text[:500],
        )
        response.raise_for_status()

    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Daraja OAuth returned an invalid response."
        ) from exc

    token = result.get("access_token")

    if not token:
        app.logger.error(
            "Daraja OAuth response contained no access token."
        )
        raise RuntimeError("Daraja did not return an access token.")

    return token


# ============================================================
# WEBSITE
# ============================================================

@app.route("/")
def home():
    return render_template("index.html")


# ============================================================
# INITIATE STK PUSH
# ============================================================

@app.route("/api/donate", methods=["POST"])
def donate():
    data = request.get_json(silent=True) or {}

    try:
        phone = normalize_phone(data.get("phone", ""))
        amount = validate_amount(data.get("amount"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        validate_configuration()
        token = get_access_token()

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

        password_source = f"{SHORTCODE}{PASSKEY}{timestamp}"
        password = base64.b64encode(
            password_source.encode("utf-8")
        ).decode("utf-8")

        payload = {
            "BusinessShortCode": SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": TRANSACTION_TYPE,
            "Amount": amount,
            "PartyA": phone,
            "PartyB": PARTY_B,
            "PhoneNumber": phone,
            "CallBackURL": CALLBACK_URL,
            "AccountReference": "ANNNAFULA",
            "TransactionDesc": f"Registration payment KSh {amount}",
        }

        response = requests.post(
            f"{BASE_URL}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

        try:
            result = response.json()
        except ValueError:
            result = {}

        if not response.ok or result.get("ResponseCode") != "0":
            # Log the response for troubleshooting. Do not log credentials.
            app.logger.error(
                "Daraja STK rejected: HTTP %s; response=%s",
                response.status_code,
                response.text[:1000],
            )

            message = (
                result.get("errorMessage")
                or result.get("ResponseDescription")
                or "Daraja rejected the STK request. Check server logs."
            )

            return jsonify({"error": message}), 502

        checkout_id = result.get("CheckoutRequestID")
        merchant_id = result.get("MerchantRequestID")

        if not checkout_id:
            app.logger.error(
                "Daraja returned success without CheckoutRequestID."
            )
            return jsonify({
                "error": "Daraja returned no checkout request ID."
            }), 502

        # Demo-only storage. Replace with a persistent database for production.
        with transactions_lock:
            transactions[checkout_id] = {
                "status": "pending",
                "amount": amount,
                "receipt": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        app.logger.info(
            "STK request accepted: checkout_id=%s",
            checkout_id,
        )

        return jsonify({
            "message": "STK Push requested. Check your phone.",
            "checkoutRequestId": checkout_id,
            "merchantRequestId": merchant_id,
            "amount": amount,
        }), 200

    except requests.Timeout:
        app.logger.exception("Daraja request timed out.")
        return jsonify({
            "error": "Daraja timed out. Check payment status before retrying."
        }), 504

    except requests.RequestException:
        app.logger.exception("Could not connect to Daraja.")
        return jsonify({
            "error": "Could not connect to Daraja. Check server logs."
        }), 502

    except RuntimeError as exc:
        app.logger.error("Daraja configuration error: %s", str(exc))
        return jsonify({
            "error": "Daraja configuration is incomplete or invalid."
        }), 500

    except Exception:
        app.logger.exception("Unexpected STK Push error.")
        return jsonify({
            "error": "An unexpected server error occurred."
        }), 500


# ============================================================
# PAYMENT STATUS
# ============================================================

@app.route("/api/status/<checkout_id>", methods=["GET"])
def payment_status(checkout_id):
    with transactions_lock:
        transaction = transactions.get(checkout_id)

        if not transaction:
            return jsonify({
                "error": (
                    "Transaction status is unavailable. "
                    "Check your M-Pesa message before retrying."
                ),
                "status": "unknown",
            }), 404

        return jsonify({
            "status": transaction["status"],
            "receipt": transaction["receipt"],
            "amount": transaction["amount"],
        })


# ============================================================
# DARAJA CALLBACK
# ============================================================

@app.route("/api/mpesa-callback", methods=["POST"])
def mpesa_callback():
    body = request.get_json(silent=True) or {}

    callback = body.get("Body", {}).get("stkCallback", {})

    checkout_id = callback.get("CheckoutRequestID")
    result_code = callback.get("ResultCode")

    if not checkout_id or result_code is None:
        app.logger.warning("Received malformed Daraja callback.")
        return jsonify({
            "ResultCode": 0,
            "ResultDesc": "Callback received",
        }), 200

    receipt = None

    if result_code == 0:
        items = (
            callback.get("CallbackMetadata", {})
            .get("Item", [])
        )

        for item in items:
            if item.get("Name") == "MpesaReceiptNumber":
                receipt = item.get("Value")
                break

    with transactions_lock:
        transaction = transactions.get(checkout_id)

        if transaction:
            if result_code == 0 and receipt:
                transaction["status"] = "success"
                transaction["receipt"] = receipt
            elif result_code != 0:
                transaction["status"] = "failed"
            else:
                transaction["status"] = "pending"

    app.logger.info(
        "Daraja callback received: checkout_id=%s result_code=%s",
        checkout_id,
        result_code,
    )

    return jsonify({
        "ResultCode": 0,
        "ResultDesc": "Callback received",
    }), 200


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False,
    )
