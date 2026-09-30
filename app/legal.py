"""The privacy policy and terms of use, served by the API itself.

Both app stores need these at a public URL: Google Play for any app with
accounts or a microphone, Apple on any screen that sells a subscription. The
API is already public over HTTPS, so it serves them — no separate site to keep
in step. The app links here by default (see AppConfig.termsUrl).

They describe what this codebase actually does with data. Change the code's
data handling, change these in the same commit. Have them reviewed before
relying on them; they are a plain-language starting point, not legal advice.
"""

from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.core.config import Settings, get_settings

router = APIRouter(prefix="/legal", tags=["legal"], include_in_schema=False)

_UPDATED = "1 October 2026"

_STYLE = """
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 720px; margin: 0 auto; padding: 32px 20px 64px; color: #2e2c2d; background: #f7f6fb; }
  h1 { font-size: 28px; margin-bottom: 4px; color: #6a68df; }
  h2 { font-size: 19px; margin-top: 28px; }
  p.meta { color: #8a8892; margin-top: 0; }
  a { color: #6a68df; }
  @media (prefers-color-scheme: dark) { body { background: #151419; color: #e9e8ee; } }
</style>
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)} · backPAC</title>{_STYLE}</head>"
        f"<body><h1>{escape(title)}</h1><p class='meta'>backPAC · Last updated {_UPDATED}</p>"
        f"{body}</body></html>",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    mail = escape(settings.support_email)
    return _page(
        "Privacy Policy",
        f"""
<p>backPAC is a voice travel planner. This page says what we collect, why, who
helps us process it, and how to have it deleted.</p>

<h2>What we collect</h2>
<p><b>Your account.</b> Your email address and the name you sign up with, held
by our sign-in provider, Supabase.</p>
<p><b>Your profile.</b> Anything you add in Edit Profile: a display name, home
city, a short bio and an avatar.</p>
<p><b>Your conversations.</b> When you talk to the assistant, what you say is
transcribed to text. We keep the text of each conversation, the trip results it
showed you, and anything you do with it — saving, favouriting, grouping,
renaming — so you can find and continue it later.</p>
<p><b>Your plan.</b> Whether you have Premium, and until when.</p>

<h2>What we do not collect</h2>
<p>We do not keep recordings of your voice: audio is processed live to produce
the transcript and the assistant's reply, then discarded. We never see or store
your card, UPI or bank details — payments are handled entirely by the App Store
or Google Play. We do not sell your data or use it for advertising.</p>

<h2>Who processes it for us</h2>
<ul>
  <li><b>Supabase</b> — sign-in and our database.</li>
  <li><b>LiveKit</b> — carries the live audio of a call between your phone and our assistant.</li>
  <li><b>Microsoft Azure Speech</b> — turns your speech into text.</li>
  <li><b>Anthropic (Claude)</b> — understands your request and writes the reply.</li>
  <li><b>ElevenLabs</b> — speaks the reply aloud.</li>
  <li><b>RevenueCat</b>, with <b>Apple</b> or <b>Google</b> — processes Premium purchases and tells us your plan.</li>
  <li><b>Travelpayouts / Aviasales</b> — flight prices. We send the route and date you asked about, not who you are.</li>
</ul>
<p>Each processes data only to provide its part of the service.</p>

<h2>Why</h2>
<p>To run the service you asked for: plan trips, remember them for you, and
provide Premium if you buy it. That is the only use.</p>

<h2>Keeping and deleting it</h2>
<p>We keep your data while you have an account. You can delete a single
conversation at any time, and <b>Profile → Settings → Delete account</b> erases
your conversations, groups, bucket list, profile and plan, and closes your
account. Deleting your account does not cancel a store subscription; cancel
that in your App Store or Google Play settings.</p>

<h2>Children</h2>
<p>backPAC is not directed at children under 13, and we do not knowingly collect
their data.</p>

<h2>Contact</h2>
<p>Questions or requests about your data: <a href="mailto:{mail}">{mail}</a>.</p>
""",
    )


@router.get("/terms", response_class=HTMLResponse)
async def terms(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    mail = escape(settings.support_email)
    return _page(
        "Terms of Use",
        f"""
<p>By using backPAC you agree to these terms.</p>

<h2>The service</h2>
<p>backPAC helps you plan trips by conversation. Its suggestions — routes, times,
prices, places to stay and tips — are for planning only. Prices shown are
estimates and can change; the assistant can be wrong. Always check the details
with the provider before you book or travel. Bookings are made with third
parties on their own terms, not with us.</p>

<h2>Your account</h2>
<p>Keep your sign-in details to yourself; you are responsible for activity on
your account. Do not misuse the service: no unlawful, abusive or automated use,
and no attempt to disrupt it or access other people's data.</p>

<h2>Premium subscriptions</h2>
<ul>
  <li>Premium is an auto-renewing subscription, billed through your App Store or
      Google Play account at the price shown before you confirm.</li>
  <li>It renews automatically at the end of each period unless you cancel at least
      24 hours before the period ends. Manage or cancel any time in your store
      account settings.</li>
  <li>Where a free trial is offered, you are charged when it ends unless you cancel
      before then.</li>
  <li>Refunds are handled by Apple or Google under their policies.</li>
  <li>If you buy on a new device or reinstall, use <b>Restore purchases</b>.</li>
</ul>

<h2>Changes and availability</h2>
<p>We may change or stop features, and will update these terms when we do; the
date above shows the latest version. We provide the service "as is" and, to the
extent the law allows, are not liable for losses arising from your use of it or
from decisions made on its suggestions.</p>

<h2>Contact</h2>
<p><a href="mailto:{mail}">{mail}</a></p>
""",
    )
