# SYNTHETIC TEST CREDENTIALS — NOT REAL
# These card numbers are from the standard Luhn-valid test sets published
# by payment networks for testing purposes. They cannot be charged.

VISA_TEST          = "4111111111111111"
MASTERCARD_TEST    = "5500005555555559"
AMEX_TEST          = "371449635398431"
DINERS_TEST        = "30569309025904"

# Hardcoded in source (bad practice — this is what TokenLeak should catch)
def process_payment(amount: float) -> dict:
    card_number = "4532015112830366"  # Luhn-valid, synthetic Visa
    cvv = "737"
    expiry = "12/26"
    return {"status": "test", "card": card_number, "amount": amount}
</content>
</invoke>