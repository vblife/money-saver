# Bland call scripts

Pathway scripts (`cancel.yaml`, `negotiate_internet.yaml`, `price_adjust.yaml`, ...) arrive in M1d (cancel) and M5
(negotiation). No script is used for a real call without a confirmed ActionRequest, and the first paid call needs
Charles's approval.

## Honesty policy (Q3, decided 2026-10-22, D30)
`policy.json` is loaded and enforced by `core/call_policy.py`:
- **Disclose up front, always** (`proactive_ai_disclosure: true`, locked). Every call opens with "Hi, this is an AI
  assistant calling on behalf of <user> about their account." The policy fails to load if this is set to anything
  but `true`, and the action gate rejects a script whose opening lacks the disclosure. This supersedes the earlier
  "no proactive disclosure" choice.
- **Truthful if asked, always.** If the other party asks whether they are talking to an AI, a bot, or a recording,
  the answer is yes, it is an AI assistant calling on behalf of the user. The caller never claims to be human and
  never claims to be the user. `if_asked_whether_ai` accepts only `answer_truthfully`. Any other value, or any key
  that would allow denying it, makes the policy fail to load. There is no setting that allows lying.
- The user's name is saved once with `set_call_identity`, under a strict name allowlist. Scripts are built with
  `build_call_script` (MCP tool `build_call_script`, which uses the saved name): a generated identity block, then
  numbered task steps. Task steps may not talk about identity, names, family, or authorization at all, alone or
  joined together. The action gate checks every `bland_call`
  request on create, amend, and before execution (`validate_call_draft`), with `constraints.call_user_name`, and
  rejects anything else. `validate_script` is a normalized deny-list backstop (curly quotes, full-width letters,
  confusable letters, `A.I.`, English and Chinese). Task steps use Latin or Chinese/Japanese letters only.
- Post-call transcript audits (disclosed up front, never claimed to be human or the user) are planned for M1d.

Scripts also disclose only the fields listed in the approved ActionRequest, and never accept new terms outside
pre-approved limits.
