"""Render already validated public GitHub notifications; no network or credentials."""
import html

# Urgency tiers — emergencies first, per Engel.
TIER = {
    "review_requested": ("Awaiting your review", 0),
    "assign":           ("Assigned to you", 0),
    "mention":          ("You were mentioned", 1),
    "author":           ("Your threads", 2),
    "comment":          ("Commented / participating", 2),
    "manual":           ("You subscribed", 3),
    "subscribed":       ("Watching", 3),
    "state_change":     ("State changes", 3),
    "ci_activity":      ("CI activity", 3),
}


def web_url(n):
    """Best-effort html url for a notification subject (API url -> web url)."""
    api = n["subject"].get("url") or ""
    repo = n["repository"]["full_name"]
    if "/pulls/" in api:
        return f"https://github.com/{repo}/pull/{api.rsplit('/',1)[-1]}"
    if "/issues/" in api:
        return f"https://github.com/{repo}/issues/{api.rsplit('/',1)[-1]}"
    if n["subject"]["type"] == "Release":
        return f"https://github.com/{repo}/releases"
    return f"https://github.com/{repo}"


def render(notifs, day):
    # group into tiers
    tiers = {}
    for n in notifs:
        label, rank = TIER.get(n["reason"], (n["reason"].title(), 3))
        tiers.setdefault((rank, label), []).append(n)

    today = day.strftime("%A, %B %-d, %Y")
    urgent_items = [n for (r, _), items in tiers.items() if r == 0 for n in items]
    urgent = len(urgent_items)
    mention_items = [n for (r, l), items in tiers.items() if l == "You were mentioned" for n in items]
    mentions = len(mention_items)
    total = len(notifs)

    def esc(s):
        return html.escape(s or "")

    def repo_short(n):
        return n["repository"]["full_name"].split("/")[-1]

    # --- Weather report: a jokey verdict on the inbox climate ---
    if urgent == 0 and mentions == 0:
        weather = "☀️ Clear skies. Nobody needs you. Suspicious, but enjoy it."
    elif urgent == 0:
        weather = "⛅ Mild. A few mentions drifting through, nothing on fire."
    elif urgent <= 2:
        weather = f"🌦️ Scattered reviews — {urgent} PR(s) tapping their feet, waiting for you."
    else:
        weather = f"⛈️ Storm warning: {urgent} reviews queued. Bring coffee and a raincoat."

    # --- Ticker: ALL CAPS shorthand of the top happenings ---
    tick = []
    if urgent:
        tick.append(f"{urgent} PR(S) AWAIT YOUR VERDICT")
    if mentions:
        tick.append(f"{mentions} FRESH @MENTIONS")
    tick.append(f"{total} PUBLIC NOTIFICATIONS UNREAD")
    top_repo = None
    if notifs:
        from collections import Counter
        top_repo, cnt = Counter(repo_short(n) for n in notifs).most_common(1)[0]
        tick.append(f"{top_repo.upper()} LEADS THE NEWS WITH {cnt} DISPATCHES")
    ticker = "  ✦  ".join(tick)

    # --- Lead story: the top item, written up with a straight face and a wink ---
    if urgent_items:
        lead, kick = urgent_items[0], "Breaking · Awaiting your review"
        pitch = "is waiting for your attention. Check the discussion and current status before taking action."
    elif mention_items:
        lead, kick = mention_items[0], "Breaking · Your name, in lights"
        pitch = "someone typed your handle and hit enter. The internet has, once again, summoned you by name."
    else:
        lead, kick = (notifs[0] if notifs else None), "Slow news day"
        pitch = "the wires are quiet. Historians will record this as the morning nothing happened."

    lead_html = ""
    if lead:
        lead_html = (
            f'<div class="lead"><div class="kicker">{esc(kick)}</div>'
            f'<h2 class="lead-hd"><a href="{web_url(lead)}">{esc(lead["subject"]["title"])}</a></h2>'
            f'<p class="lead-body">In <span class="repo">{esc(lead["repository"]["full_name"])}</span>, '
            f'a {esc(lead["subject"]["type"].lower())} {pitch} '
            f'Last seen stirring on {lead.get("updated_at","")[:10]}. '
            f'Our correspondent advises: click the headline, do the needful, feel the serotonin.</p></div>'
        )

    # --- Body: explicit grid. Urgent + mentions + comments as feature cards;
    #     the long tail ("Watching") becomes a full-width, self-columned "In Brief".
    feature, brief = [], []
    for (rank, label), items in sorted(tiers.items()):
        if rank >= 3:
            brief.append((label, items))
        else:
            feature.append((rank, label, items))

    HEADLINES = {
        "Awaiting your review": "The Jury Is Still Out",
        "Assigned to you": "Tag, You're It",
        "You were mentioned": "Overheard: Your Name",
        "Commented / participating": "The Conversation Continues",
        "Your threads": "Your Own Words, Haunting You",
    }

    def render_items(items):
        out = []
        for n in items:
            out.append(
                f'<li><a href="{web_url(n)}">{esc(n["subject"]["title"])}</a>'
                f'<span class="meta">{esc(n["repository"]["full_name"])} · {n["subject"]["type"]} · {n.get("updated_at","")[:10]}</span></li>'
            )
        return "".join(out)

    feat_html = []
    for rank, label, items in feature:
        cls = "t0" if rank == 0 else ("t1" if rank == 1 else "t2")
        hd = HEADLINES.get(label, label)
        feat_html.append(
            f'<section class="feature {cls}"><div class="eyebrow">{esc(label)} · {len(items)}</div>'
            f'<h3>{esc(hd)}</h3><ul>{render_items(items)}</ul></section>'
        )
    feat_block = f'<div class="grid">{"".join(feat_html)}</div>' if feat_html else ""

    brief_block = ""
    if brief:
        parts = []
        for label, items in brief:
            parts.append(
                f'<div class="brief-head">{esc(label)} · {len(items)}</div>'
                f'<ul class="brieflist">{render_items(items)}</ul>'
            )
        brief_block = (
            '<section class="inbrief"><h3>In Brief — Also On The Wire</h3>'
            '<p class="brief-sub">The subscriptions, the watch-list, the things you nodded at once and can never escape.</p>'
            + "".join(parts) + "</section>"
        )

    # --- Classifieds: pure fun ---
    classifieds = (
        '<section class="classifieds"><h3>Classifieds &amp; Situations Vacant</h3><div class="ads">'
        '<div class="ad"><b>WANTED:</b> One (1) human to press the green button. Bots need not apply. Coffee provided.</div>'
        '<div class="ad"><b>LOST:</b> Inbox Zero, last seen March 2024. Reward offered. No questions asked.</div>'
        f'<div class="ad"><b>FOR SALE:</b> {total} unread notifications, gently used. Will separate. Buyer collects.</div>'
        '<div class="ad"><b>PERSONALS:</b> Reviewer agent, tireless, seeks PRs for long-term commitment. Loves diffs, hates flake.</div>'
        '<div class="ad"><b>WEATHER:</b> ' + weather + '</div>'
        '<div class="ad"><b>HOROSCOPE:</b> A stranger will @-mention you. Do not fear. It is only Tuesday.</div>'
        '</div></section>'
    )

    if not notifs:
        body = '<p class="empty">☀️ No unread public notifications in this edition. The presses stand idle. 🐾</p>'
    else:
        body = lead_html + feat_block + brief_block + classifieds

    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Mention Gazette — {today}</title>
<style>
 :root{{--ink:#211c15;--muted:#6b6357;--rule:#c9bca7;--rule2:#847a6d;--paper:#f4efe3;--accent:#8a3a1c;--urgent:#9a1f14;
   --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;--mono:ui-monospace,"Courier New",monospace}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:#e9e2d2;color:var(--ink);font-family:var(--serif);line-height:1.44;
   background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.04'/%3E%3C/svg%3E")}}
 .paper{{max-width:1000px;margin:22px auto;background:var(--paper);padding:26px 42px 60px;border:1px solid var(--rule2);box-shadow:0 2px 18px rgba(0,0,0,.13)}}
 .ticker{{font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;color:var(--paper);background:var(--ink);
   padding:6px 12px;margin:-26px -42px 14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
 .flag{{text-align:center;border-bottom:1px solid var(--ink);padding-bottom:6px}}
 .flag .est{{font-family:var(--mono);font-size:10px;letter-spacing:.28em;text-transform:uppercase;color:var(--muted)}}
 .flag h1{{font-family:"Old English Text MT","UnifrakturCook",var(--serif);font-size:60px;font-weight:700;margin:2px 0;line-height:1}}
 .flag .motto{{font-style:italic;font-size:13px;color:var(--muted);margin:0 0 6px}}
 .rule-heavy{{border:0;border-top:3px double var(--ink);margin:0}}
 .dateline{{font-family:var(--mono);text-transform:uppercase;letter-spacing:.09em;font-size:10.5px;color:var(--muted);
   display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-bottom:1px solid var(--ink);margin-bottom:16px;flex-wrap:wrap}}
 .lead{{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:18px;text-align:center}}
 .kicker{{font-family:var(--mono);text-transform:uppercase;letter-spacing:.16em;font-size:10px;color:var(--urgent)}}
 .lead-hd{{font-size:34px;line-height:1.04;margin:4px 0 10px;font-weight:700;letter-spacing:-.01em}}
 .lead-hd a{{color:var(--ink);text-decoration:none}} .lead-hd a:hover{{color:var(--accent)}}
 .lead-body{{font-size:15.5px;max-width:62ch;margin:0 auto;text-align:left}}
 .lead-body::first-letter{{float:left;font-size:54px;line-height:.78;padding:6px 9px 0 0;font-weight:700;color:var(--accent);font-family:var(--serif)}}
 .lead-body .repo{{font-variant:small-caps;letter-spacing:.03em;font-weight:600}}
 .grid{{column-count:3;column-gap:24px;column-rule:1px solid var(--rule)}}
 @media(max-width:820px){{.grid{{column-count:2}}}}
 @media(max-width:560px){{.grid{{column-count:1}} .paper{{padding:22px 18px}} .ticker{{margin:-22px -18px 12px}} .flag h1{{font-size:42px}}}}
 .feature{{break-inside:avoid;padding:0 0 14px;margin:0 0 4px}}
 .eyebrow{{font-family:var(--mono);text-transform:uppercase;letter-spacing:.1em;font-size:9px;color:var(--muted);margin-top:4px}}
 .feature h3{{font-size:19px;line-height:1.08;margin:2px 0 8px;border-bottom:2px solid var(--ink);padding-bottom:6px}}
 .feature.t0 h3{{color:var(--urgent)}} .feature.t1 h3{{color:var(--accent)}}
 ul{{list-style:none;margin:0;padding:0}}
 li{{padding:6px 0;border-bottom:1px dotted var(--rule)}} li:last-child{{border:0}}
 li a{{color:var(--ink);text-decoration:none;font-size:14px;font-weight:600;line-height:1.25;display:block}} li a:hover{{color:var(--accent)}}
 .meta{{display:block;font-family:var(--mono);font-size:9px;color:var(--muted);margin-top:2px;letter-spacing:.01em}}
 .inbrief{{margin-top:22px;border-top:3px double var(--ink);padding-top:12px}}
 .inbrief h3{{font-size:20px;margin:0 0 2px}}
 .brief-sub{{font-style:italic;color:var(--muted);font-size:13px;margin:0 0 12px}}
 .brief-head{{font-family:var(--mono);text-transform:uppercase;letter-spacing:.08em;font-size:10px;color:var(--muted);
   border-bottom:1px solid var(--ink);padding-bottom:3px;margin:14px 0 6px}}
 .brieflist{{column-count:3;column-gap:24px;column-rule:1px solid var(--rule)}}
 @media(max-width:820px){{.brieflist{{column-count:2}}}} @media(max-width:560px){{.brieflist{{column-count:1}}}}
 .brieflist li{{break-inside:avoid}} .brieflist li a{{font-size:13px;font-weight:500}}
 .classifieds{{margin-top:22px;border-top:3px double var(--ink);padding-top:12px}}
 .classifieds h3{{font-size:20px;margin:0 0 10px;text-align:center;font-variant:small-caps;letter-spacing:.04em}}
 .ads{{column-count:2;column-gap:24px}} @media(max-width:560px){{.ads{{column-count:1}}}}
 .ad{{break-inside:avoid;border:1px solid var(--rule2);padding:8px 11px;margin:0 0 10px;font-size:13px;background:rgba(255,255,255,.35)}}
 .ad b{{font-family:var(--mono);font-size:10px;letter-spacing:.06em;color:var(--urgent)}}
 .empty{{text-align:center;font-size:20px;color:var(--muted);padding:40px}}
 .colophon{{text-align:center;font-family:var(--mono);font-size:9.5px;color:var(--muted);
   border-top:1px solid var(--ink);margin-top:22px;padding-top:10px;letter-spacing:.05em}}
</style></head><body><div class="paper">
 <div class="ticker">{esc(ticker)}</div>
 <header class="flag">
   <div class="est">Vol. I · No. {day.strftime('%j')} · Morning Edition</div>
   <h1>The Mention Gazette</h1>
   <p class="motto">"All the pings fit to print" — printed fresh each dawn for enyst, by one small cat</p>
 </header>
 <hr class="rule-heavy">
 <div class="dateline"><span>{today}</span><span>By The Notification Desk</span><span>Price: one ☕</span></div>
 {body}
 <div class="colophon">Set by smol paws 🐾 in the small hours · public repositories only · GitHub notifications · all jokes from the cat</div>
</div></body></html>"""

    return doc
