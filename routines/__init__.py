"""Routines from spec 7.3, as plain functions (M1b: R1, R3, R4, R10).

Only the code is here. Provisioning the schedule (Grok Bot routines) is deploy configuration and stays held back
until Charles approves it (AGENTS.md). Planned times, in the user's time zone:
R1 daily_email_scan 05:30, R3 reminder_dispatch hourly, R4 trial_guard daily 09:00, R10 action_request_expiry hourly.
Each routine is idempotent and returns the texts the bot should post in the user's own chat. None of them sends,
calls, or cancels anything.
"""
