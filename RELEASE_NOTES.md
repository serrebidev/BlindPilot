# BlindPilot 0.27.5

Muse Code now reports when Meta refuses a Muse Spark request with HTTP 402. The backend reaches the live Spark service, but that response means the signed-in Meta account does not have Spark inference access yet.

BlindPilot stops the turn promptly and explains that an account with Muse Spark access is required, instead of waiting silently through Muse's internal retries.
