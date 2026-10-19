# Bland call scripts

Pathway scripts (`cancel.yaml`, `negotiate_internet.yaml`, `price_adjust.yaml`, ...) arrive in M1 (cancel) and M5
(negotiation). Every script must open with an AI-assistant disclosure ("I'm an AI assistant calling on behalf of ..."),
disclose only the fields listed in the approved ActionRequest, and never accept new terms outside pre-approved limits.
