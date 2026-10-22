# Bland call scripts

Pathway scripts (`cancel.yaml`, `negotiate_internet.yaml`, `price_adjust.yaml`, ...) arrive in M1d (cancel) and M5
(negotiation). No script is used for a real call without a confirmed ActionRequest, and the first paid call needs
Charles's approval.

## Honesty policy (Q3, decided 2026-10-22)
`policy.json` is loaded and enforced by `core/call_policy.py`:
- **No proactive AI disclosure** by default (`proactive_ai_disclosure: false`). The opening line says the bot is
  calling on behalf of the user. It does not say the caller is an AI unless this setting is turned on.
- **Truthful if asked, always.** If the other party asks whether they are talking to an AI, a bot, or a recording,
  the answer is yes, it is an AI assistant calling on behalf of the user. The caller never claims to be human and
  never claims to be the user. `if_asked_whether_ai` accepts only `answer_truthfully`. Any other value, or any key
  that would allow denying it, makes the policy fail to load. There is no setting that allows lying.
- Every script is checked with `validate_script`. Text that claims to be human, denies being an AI, or claims to be
  the account holder is rejected.

Scripts also disclose only the fields listed in the approved ActionRequest, and never accept new terms outside
pre-approved limits.
