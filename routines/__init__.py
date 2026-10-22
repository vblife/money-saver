"""Routines from spec 7.3, as plain functions (M1b: R1, R3, R4, R10).

Enabled by default (Charles, 2026-10-22; D31). ``routines/schedule.py`` is the registry, and the ``list_routines``
tool gives the bot what to register as Grok Bot scheduled tasks. Times are in the user's time zone:
R1 daily_email_scan 05:30, R3 reminder_dispatch hourly, R4 trial_guard daily 09:00, R10 action_request_expiry hourly.
Each routine is idempotent and returns the texts the bot should post in the user's own chat. None of them sends to a
third party, calls, cancels, or starts an action run. Cancellations always go through the confirmation gate.
"""
