import os
import re
import base64
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify
import requests

app = Flask(__name__)

# Configure these as environment variables.
CONSUMER_KEY = os.getenv("DARAJA_CONSUMER_KEY", "")
CONSUMER_SECRET = os.getenv("DARAJA_CONSUMER_SECRET", "")
SHORTCODE = os.getenv("DARAJA_SHORTCODE", "")
PASSKEY = os.getenv("DARAJA_PASSKEY", "")
CALLBACK_URL = os.getenv("DARAJA_CALLBACK_URL", "")

BASE_URL = "https://sandbox.safaricom.co.ke"

DONATION_AMOUNT = 100
CURRENCY = "KES"

# DEMO ONLY: in-memory storage is lost on restart and may not
# be shared across serverless instances. Use a persistent database
# for a deployed application.
transactions = {}
transactions_lock = threading.Lock()


def normalize_phone(phone):
    """Normalize Kenyan mobile numbers to 2547XXXXXXXX / 2541XXXXXXXX."""
    digits = re.sub(r"\D", "", phone or "")

    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("7") and len(digits) == 9:
        digits = "254" + digits
    elif digits.startswith("1") and len(digits) == 9:
        digits = "254" + digits

    if not re.fullmatch(r"254[17]\d{8}", digits):
        raise ValueError(
            "Enter a valid Kenyan Safaricom-compatible mobile number."
        )

    return digits


def get_access_token():
    if not CONSUMER_KEY or not CONSUMER_SECRET:
        raise RuntimeError("Daraja credentials are not configured.")

    response = requests.get(
        f"{BASE_URL}/oauth/v1/generate",
        params={"grant_type": "client_credentials"},
        auth=(CONSUMER_KEY, CONSUMER_SECRET),
        timeout=20,
    )
    response.raise_for_status()

    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Daraja did not return an access token.")

    return token


@app.route("/")
def home():
    return render_template("index.html", amount=DONATION_AMOUNT)


@app.route("/api/donate", methods=["POST"])
def donate():
    data = request.get_json(silent=True) or {}

    try:
        phone = normalize_phone(data.get("phone", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # Never accept the donation amount from the browser.
    amount = DONATION_AMOUNT

    if not all([SHORTCODE, PASSKEY, CALLBACK_URL]):
        return jsonify({
            "error": "The Daraja shortcode, passkey, or callback URL "
                     "has not been configured."
        }), 500

    if not CALLBACK_URL.startswith("https://"):
        return jsonify({
            "error": "The callback URL must be a public HTTPS URL."
        }), 500

    try:
        token = get_access_token()

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(
            f"{SHORTCODE}{PASSKEY}{timestamp}".encode("utf-8")
        ).decode("utf-8")

        payload = {
            "BusinessShortCode": SHORTCODE,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": phone,
            "PartyB": SHORTCODE,
            "PhoneNumber": phone,
            "CallBackURL": CALLBACK_URL,
            "AccountReference": "DONATION",
            "TransactionDesc": "KSh 100 donation",
        }

        response = requests.post(
            f"{BASE_URL}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )

        try:
            result = response.json()
        except ValueError:
            result = {}

        if not response.ok or result.get("ResponseCode") != "0":
            app.logger.warning("Daraja STK request was rejected.")
            return jsonify({
                "error": result.get(
                    "errorMessage",
                    result.get("ResponseDescription",
                               "Daraja rejected the STK request.")
                )
            }), 502

        checkout_id = result.get("CheckoutRequestID")
        if not checkout_id:
            return jsonify({
                "error": "Daraja returned no CheckoutRequestID."
            }), 502

        with transactions_lock:
            transactions[checkout_id] = {
                "status": "pending",
                "amount": amount,
                "phone": phone,
                "receipt": None,
            }

        return jsonify({
            "message": "STK Push request accepted. Check your phone.",
            "checkoutRequestId": checkout_id,
        }), 200

    except requests.RequestException:
        app.logger.exception("Daraja network request failed.")
        return jsonify({
            "error": "Could not contact Daraja. Please try again."
        }), 502
    except Exception:
        app.logger.exception("Unexpected donation error.")
        return jsonify({
            "error": "An unexpected error occurred."
        }), 500


@app.route("/api/status/<checkout_id>", methods=["GET"])
def payment_status(checkout_id):
    with transactions_lock:
        transaction = transactions.get(checkout_id)

    if not transaction:
        return jsonify({"error": "Transaction not found."}), 404

    # Do not expose phone numbers or other personal information.
    return jsonify({
        "status": transaction["status"],
        "receipt": transaction["receipt"],
    })


@app.route("/api/mpesa-callback", methods=["POST"])
def mpesa_callback():
    body = request.get_json(silent=True) or {}
    callback = (
        body.get("Body", {})
        .get("stkCallback", {})
    )

    checkout_id = callback.get("CheckoutRequestID")
    result_code = callback.get("ResultCode")

    if not checkout_id or result_code is None:
        app.logger.warning("Received malformed Daraja callback.")
        return jsonify({
            "ResultCode": 1,
            "ResultDesc": "Invalid callback data",
        }), 400

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
            else:
                transaction["status"] = "failed"

    # Log only the result and request identifier, not the full payload.
    app.logger.info(
        "Daraja callback received: checkout_id=%s result_code=%s",
        checkout_id,
        result_code,
    )

    return jsonify({
        "ResultCode": 0,
        "ResultDesc": "Callback received",
    }), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
