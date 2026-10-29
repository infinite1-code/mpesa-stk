const registrationForm = document.getElementById("registrationForm");
const phoneInput = document.getElementById("phone");
const amountInput = document.getElementById("amount");
const payButton = document.getElementById("payButton");
const buttonText = document.getElementById("buttonText");
const statusBox = document.getElementById("paymentStatus");

let requestInProgress = false;

const MIN_FEE = 1;
const MAX_FEE = 150000;

function showStatus(message, type = "pending") {
    statusBox.textContent = message;
    statusBox.className = `status ${type}`;
    statusBox.hidden = false;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function checkPaymentStatus(checkoutId, amount) {
    // Poll for up to 90 seconds.
    for (let i = 0; i < 30; i++) {
        await sleep(3000);

        let response;

        try {
            response = await fetch(
                `/api/status/${encodeURIComponent(checkoutId)}`
            );
        } catch {
            continue;
        }

        if (!response.ok) {
            continue;
        }

        let result;

        try {
            result = await response.json();
        } catch {
            continue;
        }

        if (result.status === "success") {
            const receipt = result.receipt
                ? ` M-Pesa receipt: ${result.receipt}.`
                : "";

            showStatus(
                `Payment confirmed! Your registration fee of KSh ${amount.toLocaleString("en-KE")} has been received.${receipt}`,
                "success"
            );

            return;
        }

        if (result.status === "failed") {
            showStatus(
                "The M-Pesa payment was not completed. Check your M-Pesa messages before trying again.",
                "error"
            );

            return;
        }

        if (result.status === "unknown") {
            showStatus(
                "We could not retrieve your payment status. Check your M-Pesa messages before making another payment.",
                "pending"
            );

            return;
        }
    }

    showStatus(
        "Your payment is still awaiting confirmation. Check your M-Pesa messages before trying again.",
        "pending"
    );
}

registrationForm.addEventListener("submit", async function (event) {
    event.preventDefault();

    if (requestInProgress) {
        return;
    }

    const phone = phoneInput.value.trim();
    const amountValue = amountInput.value.trim();
    const amount = Number(amountValue);

    if (!phone) {
        showStatus(
            "Please enter your M-Pesa phone number.",
            "error"
        );

        phoneInput.focus();
        return;
    }

    if (
        amountValue === "" ||
        !Number.isSafeInteger(amount) ||
        amount < MIN_FEE ||
        amount > MAX_FEE
    ) {
        showStatus(
            `Enter a whole-number registration fee between KSh ${MIN_FEE} and KSh ${MAX_FEE.toLocaleString("en-KE")}.`,
            "error"
        );

        amountInput.focus();
        return;
    }

    requestInProgress = true;
    payButton.disabled = true;
    buttonText.textContent = "Initiating Payment...";
    statusBox.hidden = true;

    try {
        // Keep this endpoint because it is defined in the current app.py.
        const response = await fetch("/api/donate", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                phone: phone,
                amount: amount
            })
        });

        let result;

        try {
            result = await response.json();
        } catch {
            result = {};
        }

        if (!response.ok) {
            showStatus(
                result.error ||
                "We could not initiate your registration payment. Please try again.",
                "error"
            );

            return;
        }

        if (!result.checkoutRequestId) {
            showStatus(
                "The payment request could not be verified. Check your M-Pesa messages before trying again.",
                "error"
            );

            return;
        }

        showStatus(
            `An M-Pesa payment prompt has been requested for KSh ${amount.toLocaleString("en-KE")}. Check your phone and follow the instructions. Waiting for confirmation...`,
            "pending"
        );

        await checkPaymentStatus(
            result.checkoutRequestId,
            amount
        );

    } catch (error) {
        showStatus(
            "Unable to contact the website. Check your internet connection and try again.",
            "error"
        );
    } finally {
        requestInProgress = false;
        payButton.disabled = false;
        buttonText.textContent = "Pay Registration Fee";
    }
});
