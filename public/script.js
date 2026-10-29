const form = document.getElementById("donationForm");
const phoneInput = document.getElementById("phone");
const amountInput = document.getElementById("amount");
const donateButton = document.getElementById("donateButton");
const buttonText = document.getElementById("buttonText");
const statusBox = document.getElementById("status");

let requestInProgress = false;

const MIN_DONATION = 1;
const MAX_DONATION = 150000;

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

        const result = await response.json();

        if (result.status === "success") {
            const receipt = result.receipt
                ? ` Receipt number: ${result.receipt}.`
                : "";

            showStatus(
                `Thank you! Your KSh ${amount.toLocaleString("en-KE")} donation was confirmed.${receipt}`,
                "success"
            );

            return;
        }

        if (result.status === "failed") {
            showStatus(
                "The payment was not completed. You can try again.",
                "error"
            );

            return;
        }
    }

    showStatus(
        "The payment is still awaiting confirmation. Check your " +
        "M-Pesa messages before trying again.",
        "pending"
    );
}

form.addEventListener("submit", async function (event) {
    event.preventDefault();

    if (requestInProgress) {
        return;
    }

    const phone = phoneInput.value.trim();
    const amountValue = amountInput.value.trim();
    const amount = Number(amountValue);

    // Validate the phone number field.
    if (!phone) {
        showStatus("Please enter your M-Pesa phone number.", "error");
        phoneInput.focus();
        return;
    }

    // Validate the chosen donation amount.
    if (
        amountValue === "" ||
        !Number.isSafeInteger(amount) ||
        amount < MIN_DONATION ||
        amount > MAX_DONATION
    ) {
        showStatus(
            `Please enter a whole-number donation between KSh ${MIN_DONATION} and KSh ${MAX_DONATION.toLocaleString("en-KE")}.`,
            "error"
        );

        amountInput.focus();
        return;
    }

    requestInProgress = true;
    donateButton.disabled = true;
    buttonText.textContent = "Sending request...";
    statusBox.hidden = true;

    try {
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

        const result = await response.json();

        if (!response.ok) {
            showStatus(
                result.error || "Could not initiate the payment.",
                "error"
            );
            return;
        }

        showStatus(
            `STK Push requested for KSh ${amount.toLocaleString("en-KE")}. ` +
            "Check your phone and follow the M-Pesa instructions. " +
            "Waiting for payment confirmation...",
            "pending"
        );

        await checkPaymentStatus(result.checkoutRequestId, amount);

    } catch (error) {
        showStatus(
            "Unable to contact the website. Check your connection and try again.",
            "error"
        );
    } finally {
        requestInProgress = false;
        donateButton.disabled = false;
        buttonText.textContent = "Donate Now";
    }
});
