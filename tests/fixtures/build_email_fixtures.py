#!/usr/bin/env python3
"""Build the synthetic, PII-free email fixture set used by the classifier tests.

Spec 7.4 asks for >= 20 anonymized samples per category. Real anonymized mail is not available
in the repo, so this deterministic builder writes realistic synthetic samples (no real people,
addresses, card numbers, or accounts; the recipient is always alex@example.com). Re-run with
``python3 tests/fixtures/build_email_fixtures.py`` after editing templates; the JSONL is committed.
"""
from __future__ import annotations

import itertools
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "emails", "samples.jsonl")
TO = "alex@example.com"

T: dict[str, list[tuple[str, str, str]]] = {}  # category -> [(from, subject, body)]  with {m},{d},{amt}

T["subscription_receipt"] = [
    ("{m} <billing@{d}>", "Your {m} receipt", "Hi Alex, thanks for your payment. You were charged ${amt} for your monthly {m} plan on Oct 3, 2026. Next billing date: Nov 3, 2026."),
    ("{m} <no-reply@{d}>", "Payment received - {m} Premium", "We received your payment of ${amt}. Plan: {m} Premium (billed monthly). Card ending in 4242 was charged."),
    ("{m} Billing <invoices@{d}>", "Invoice #INV-20481 for your {m} subscription", "Invoice date: September 14, 2026. Amount paid: ${amt} USD. Billing period: Sep 14 - Oct 13, 2026. Thank you for subscribing."),
    ("{m} <account@{d}>", "Thanks for subscribing to {m}", "Your subscription is active. Total charged today: ${amt}/month. You can manage your membership in Account settings."),
    ("{m} <receipts@{d}>", "{m}: your annual plan receipt", "Order summary: {m} annual plan, ${amt} per year. Paid on Aug 21, 2026. This receipt is for your records."),
]
T["trial_start"] = [
    ("{m} <hello@{d}>", "Welcome to your {m} free trial", "Your 7-day free trial has started. After your trial ends on Oct 26, 2026 you'll be charged ${amt}/month unless you cancel."),
    ("{m} <no-reply@{d}>", "Your free trial ends in 3 days", "Heads up: your {m} free trial ends on November 2, 2026. Your plan will then convert to ${amt} per month."),
    ("{m} <team@{d}>", "Your {m} trial is active", "Enjoy 30 days free. Trial end date: Nov 18, 2026. After the trial, the price is ${amt}/mo."),
    ("{m} <billing@{d}>", "Start your 14-day trial of {m}", "Thanks for starting your 14-day free trial. We won't charge you until the trial period is over. Then ${amt}/month."),
    ("{m} <noreply@{d}>", "Reminder: {m} trial ending soon", "Your trial period ends tomorrow. To avoid being charged ${amt}, cancel before Oct 21, 2026."),
]
T["renewal_notice"] = [
    ("{m} <billing@{d}>", "Your {m} membership will renew soon", "Your annual membership will automatically renew on Dec 1, 2026 for ${amt}. No action is needed to keep your benefits."),
    ("{m} <no-reply@{d}>", "Upcoming renewal: {m}", "This is a reminder that your subscription renews on Nov 15, 2026. You will be charged ${amt}."),
    ("{m} <account@{d}>", "Important: {m} price change", "Starting with your next billing cycle on Nov 9, 2026, your plan price will increase to ${amt}/month."),
    ("{m} <notices@{d}>", "Auto-renew notice for your {m} plan", "Auto-renew is on. Your plan renews on January 4, 2027 at ${amt} per year. Turn off auto-renew anytime."),
    ("{m} <support@{d}>", "Your {m} subscription is set to renew", "Your subscription will renew on Oct 30, 2026. Renewal amount: ${amt}. Manage your subscription in settings."),
]
T["order_confirmation"] = [
    ("{m} <order-update@{d}>", "Your {m} order #112-4839201 has been placed", "Thanks for your order, Alex! Order total: ${amt}. Estimated delivery: Oct 24, 2026. Items: Wireless earbuds (1)."),
    ("{m} <orders@{d}>", "Order confirmation", "We received your order 7720193. Order date: October 12, 2026. Subtotal ${amt}. We'll email you when it ships."),
    ("{m} <no-reply@{d}>", "Thanks for shopping with {m}", "Order #W8821-33 confirmed. 1 x Stainless steel water bottle. Total: ${amt}. Arrives by Fri, Oct 23."),
    ("{m} <confirm@{d}>", "We got your order!", "Order number: 5512-9908. Placed on Oct 9, 2026. Order total ${amt}. Pickup or delivery details will follow."),
    ("{m} <orders@{d}>", "Order received: 2 items", "Your order is confirmed. Order # 300-118822. Estimated arrival Oct 28, 2026. Charged ${amt} to card ending 1111."),
]
T["shipping_update"] = [
    ("{m} <shipment-tracking@{d}>", "Your {m} order has shipped", "Good news! Your package is on its way. Tracking number: 1Z999AA10123456784. Expected delivery Oct 22, 2026."),
    ("{m} <no-reply@{d}>", "Out for delivery today", "Your order #7720193 is out for delivery and should arrive by 8 pm today."),
    ("{m} <updates@{d}>", "Shipping update: your package is delayed", "Your shipment is running late. New estimated delivery date: Oct 27, 2026. We're sorry for the delay."),
    ("{m} <ship@{d}>", "Your items are on the way", "Order 5512-9908 shipped via UPS. Track your package with tracking number 9400111899223344556677."),
    ("{m} <orders@{d}>", "Shipment notification for order #300-118822", "Part of your order has shipped. Carrier: FedEx. Estimated arrival: Oct 25, 2026."),
]
T["delivery"] = [
    ("{m} <shipment-tracking@{d}>", "Delivered: your {m} package", "Your package was delivered on Oct 20, 2026 at 2:14 PM. It was left at the front door."),
    ("{m} <no-reply@{d}>", "Your order has been delivered", "Order #7720193 has been delivered. We hope you enjoy your purchase. Something wrong? Start a return."),
    ("{m} <updates@{d}>", "Package delivered", "Your package was delivered to the mailroom on October 19, 2026."),
    ("{m} <ship@{d}>", "Your {m} delivery is complete", "Delivery complete for order 5512-9908. Delivered Oct 18, 2026. Rate your delivery experience."),
    ("{m} <orders@{d}>", "Delivered: 2 items from order #300-118822", "Good news, your items were delivered today. Returns are accepted within 30 days of delivery."),
]
T["refund"] = [
    ("{m} <returns@{d}>", "Your refund has been issued", "We've issued a refund of ${amt} to your original payment method. Please allow 3-5 business days."),
    ("{m} <no-reply@{d}>", "Return received - refund on the way", "We received your return for order #7720193. A refund of ${amt} will be processed within 5 business days."),
    ("{m} <support@{d}>", "Refund processed for your {m} order", "Your refund of ${amt} has been processed and credited to the card ending in 4242."),
    ("{m} <billing@{d}>", "Credit issued to your account", "As requested, we've applied a credit of ${amt} to your account. This will appear on your next statement."),
    ("{m} <care@{d}>", "Price adjustment approved", "Good news! Your price adjustment request was approved. We refunded the difference of ${amt}."),
]
T["travel_booking"] = [
    ("{m} <reservations@{d}>", "Your flight confirmation - ABC123", "Thank you for booking with {m}. Confirmation code: ABC123. Flight 1427 SFO to SEA, Nov 12, 2026, departs 8:05 AM. Total ${amt}."),
    ("{m} <no-reply@{d}>", "Your trip itinerary", "Itinerary for Alex: Flight 885 LAX to JFK on Dec 3, 2026. Record locator: QWE789. Fare paid: ${amt}."),
    ("{m} <reservations@{d}>", "Reservation confirmed: 2 nights", "Your hotel reservation is confirmed. Check-in: Nov 20, 2026. Check-out: Nov 22, 2026. Confirmation #84471920. Total ${amt}. Free cancellation until Nov 18."),
    ("{m} <booking@{d}>", "Booking confirmation #HX-55102", "Your stay is booked. King room, check-in Dec 10, 2026. Total price ${amt}. Cancel for free before Dec 8."),
    ("{m} <travel@{d}>", "You're all set for your trip to Denver", "Confirmation number: ZK4PL2. Departing Oct 30, 2026, flight 220. Seats 14A. Total charged ${amt}."),
]
T["travel_change"] = [
    ("{m} <alerts@{d}>", "Schedule change for your trip ABC123", "There has been a schedule change to your itinerary. Flight 1427 now departs at 11:40 AM instead of 8:05 AM."),
    ("{m} <no-reply@{d}>", "Your flight is delayed", "Flight 885 from LAX to JFK is delayed. New departure time: 6:50 PM. We apologize for the inconvenience."),
    ("{m} <alerts@{d}>", "Flight cancellation notice", "We're sorry, flight 220 on Oct 30, 2026 has been cancelled due to operational issues. You have been rebooked on flight 224."),
    ("{m} <updates@{d}>", "Important change to your itinerary QWE789", "Your itinerary has changed. Please review your new flight times and accept or request a refund."),
    ("{m} <notifications@{d}>", "Delay update for flight 1427", "Your flight is now expected to depart 3 hours late. Meal vouchers are available at the gate."),
]
T["bill"] = [
    ("{m} <billing@{d}>", "Your {m} bill is ready", "Your monthly statement is ready. Amount due: ${amt}. Due date: Nov 5, 2026. Account ending in 4821."),
    ("{m} <no-reply@{d}>", "Your statement is available", "Your October statement for account ending 9912 is now available. Total amount due ${amt} by Nov 12, 2026."),
    ("{m} <ebill@{d}>", "Payment due soon", "Reminder: your payment of ${amt} is due on Oct 28, 2026. AutoPay is not set up for this account."),
    ("{m} <billing@{d}>", "Thanks for your AutoPay payment", "Your AutoPay payment of ${amt} for your internet service was received. Service period: Oct 1 - Oct 31."),
    ("{m} <notices@{d}>", "Your wireless bill for October", "Your bill for 2 lines is ${amt}. View your bill details online. Due Nov 3, 2026."),
]
T["insurance_policy"] = [
    ("{m} <policyservice@{d}>", "Your auto policy renewal documents", "Your auto insurance policy renews on Dec 15, 2026. Renewal premium: ${amt} for 6 months. Your declarations page is attached."),
    ("{m} <no-reply@{d}>", "Your homeowners policy declarations", "Attached are the declarations for your homeowners policy. Coverage A dwelling. Deductible $1,000. Annual premium ${amt}."),
    ("{m} <service@{d}>", "Important information about your renters insurance", "Your renters insurance policy will renew on Jan 2, 2027. Premium ${amt} per year. Review your coverage."),
    ("{m} <policy@{d}>", "Policy change confirmation", "We've updated your auto policy to add a vehicle. Your new premium is ${amt}. Effective Nov 1, 2026."),
    ("{m} <insurance@{d}>", "Your insurance ID cards and policy documents", "Thank you for choosing {m}. Your policy documents and ID cards are ready. Policy period Nov 1, 2026 - May 1, 2027."),
]
T["gift_card"] = [
    ("{m} <giftcards@{d}>", "You've received a {m} gift card", "Alex, you received a ${amt} eGift card! Card ending in 7731. Redeem online or in store."),
    ("{m} <no-reply@{d}>", "Your gift card balance", "Your gift card ending in 1208 has a remaining balance of ${amt}."),
    ("{m} <rewards@{d}>", "Your {m} eGift is ready to use", "Enjoy your ${amt} eGift card. Use it by Dec 31, 2026 for a bonus reward."),
    ("{m} <giftcards@{d}>", "Gift card added to your account", "A ${amt} gift card has been added to your account balance. Applied automatically at checkout."),
    ("{m} <promo@{d}>", "Your promotional gift card expires soon", "Your ${amt} promotional gift card expires on Nov 30, 2026. Use it before it's gone."),
]
T["loyalty_statement"] = [
    ("{m} <rewards@{d}>", "Your {m} points statement", "Your rewards balance: 18,420 points. 2,000 points expire on Dec 31, 2026 unless you have qualifying activity."),
    ("{m} <miles@{d}>", "Your miles are about to expire", "Alex, 12,500 miles in your account will expire on Jan 15, 2027. Earn or redeem miles to keep them active."),
    ("{m} <loyalty@{d}>", "Your monthly member account summary", "Member summary for October: points earned 640, points balance 22,115. Elite status progress: 12 of 25 nights."),
    ("{m} <no-reply@{d}>", "Your loyalty rewards update", "You have $15 in rewards available. Your rewards expire on Nov 30, 2026."),
    ("{m} <rewards@{d}>", "Don't lose your points", "Your points balance of 5,300 will expire in 30 days. Make any purchase to keep your points active."),
]
T["settlement_notice"] = [
    ("Settlement Administrator <info@{d}>", "Notice of proposed class action settlement", "You may be eligible for a payment from a class action settlement with {m}. Submit a claim form by Jan 20, 2027."),
    ("Claims Administrator <noreply@{d}>", "Legal notice: {m} data breach settlement", "A settlement has been reached in a class action lawsuit. Class members may claim up to ${amt}. Claim deadline: Dec 15, 2026."),
    ("Settlement Administrator <claims@{d}>", "Your claim ID for the {m} settlement", "Our records show you may be a class member. Use your claim ID to file online. The court will hold a final approval hearing on Feb 3, 2027."),
    ("Claims Administrator <admin@{d}>", "Settlement payment update", "The settlement in the {m} litigation received final court approval. Payments to claimants are expected in early 2027."),
    ("Class Action Notice <notice@{d}>", "Court-authorized notice regarding {m}", "This is a court-authorized notice. If you purchased {m} products between 2019 and 2024, you could get a payment from a class action settlement."),
]
T["other"] = [
    ("{m} <news@{d}>", "New arrivals you'll love", "Check out this week's new arrivals and staff picks. Free shipping on orders over $35."),
    ("{m} <security@{d}>", "New sign-in to your account", "We noticed a new sign-in to your account from Chrome on Mac. If this was you, no action is needed."),
    ("{m} <community@{d}>", "Your weekly digest", "Here's what happened in your network this week: 3 new followers, 12 post views."),
    ("{m} <team@{d}>", "We've updated our privacy policy", "We're making changes to our privacy policy effective November 15, 2026. Learn more on our website."),
    ("{m} <hello@{d}>", "Tips to get the most out of {m}", "Did you know you can customize your home screen? Here are five tips from our team."),
]

MERCHANTS: dict[str, list[tuple[str, str]]] = {
    "subscription_receipt": [("Netflix", "netflix.com"), ("Spotify", "spotify.com"), ("Dropbox", "dropbox.com"), ("Adobe", "adobe.com")],
    "trial_start": [("Peacock", "peacocktv.com"), ("Calm", "calm.com"), ("Grammarly", "grammarly.com"), ("Paramount+", "paramountplus.com")],
    "renewal_notice": [("Costco", "costco.com"), ("NordVPN", "nordvpn.com"), ("Hulu", "hulu.com"), ("Microsoft 365", "microsoft.com")],
    "order_confirmation": [("Amazon", "amazon.com"), ("Target", "target.com"), ("Best Buy", "bestbuy.com"), ("Walmart", "walmart.com")],
    "shipping_update": [("Amazon", "amazon.com"), ("Target", "target.com"), ("Best Buy", "bestbuy.com"), ("Walmart", "walmart.com")],
    "delivery": [("Amazon", "amazon.com"), ("Target", "target.com"), ("Best Buy", "bestbuy.com"), ("Walmart", "walmart.com")],
    "refund": [("Amazon", "amazon.com"), ("Target", "target.com"), ("Best Buy", "bestbuy.com"), ("Walmart", "walmart.com")],
    "travel_booking": [("Southwest Airlines", "southwest.com"), ("Alaska Airlines", "alaskaair.com"), ("Hilton", "hilton.com"), ("Marriott", "marriott.com")],
    "travel_change": [("Southwest Airlines", "southwest.com"), ("Alaska Airlines", "alaskaair.com"), ("United Airlines", "united.com"), ("Delta", "delta.com")],
    "bill": [("Xfinity", "xfinity.com"), ("Verizon", "verizon.com"), ("AT&T", "att.com"), ("Spectrum", "spectrum.com")],
    "insurance_policy": [("GEICO", "geico.com"), ("State Farm", "statefarm.com"), ("Progressive", "progressive.com"), ("Allstate", "allstate.com")],
    "gift_card": [("Starbucks", "starbucks.com"), ("Target", "target.com"), ("Amazon", "amazon.com"), ("Best Buy", "bestbuy.com")],
    "loyalty_statement": [("Hilton Honors", "hilton.com"), ("Alaska Airlines", "alaskaair.com"), ("Marriott Bonvoy", "marriott.com"), ("Sephora", "sephora.com")],
    "settlement_notice": [("Acme Corp", "acmesettlement.example"), ("Widget Co", "widgetclaims.example"), ("DataCo", "datacosettlement.example"), ("Gadget Inc", "gadgetsettlement.example")],
    "other": [("Target", "target.com"), ("LinkedIn", "linkedin.com"), ("Spotify", "spotify.com"), ("Netflix", "netflix.com")],
}
AMOUNTS = ["15.49", "11.99", "9.99", "54.99", "119.00", "22.50", "7.99", "249.00", "35.75", "64.10"]


def build() -> list[dict]:
    rows = []
    amt_cycle = itertools.cycle(AMOUNTS)
    for category, templates in T.items():
        n = 0
        for (sender, subject, body), (name, domain) in itertools.product(templates, MERCHANTS[category]):
            n += 1
            amt = next(amt_cycle)
            fill = {"m": name, "d": domain, "amt": amt}
            rows.append({
                "id": f"{category}-{n:02d}", "category": category, "to": TO,
                "from": sender.format(**fill), "subject": subject.format(**fill), "body": body.format(**fill),
                "date": "2026-10-1%dT16:00:00Z" % (n % 10), "merchant_name": name,
                "amount": amt if "${amt}" in body else None,
            })
    return rows


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows = build()
    with open(OUT, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} samples to {OUT}")
