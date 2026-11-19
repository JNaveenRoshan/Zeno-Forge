"""Buyer and supplier agents backed by Sarvam AI (chat completions, structured JSON output).

The key is read from the SARVAM_API_KEY environment variable or from tools/negotiator/.env
(a line `SARVAM_API_KEY=sk_...`). It is only ever sent to api.sarvam.ai and never to the browser.
Set SARVAM_MOCK=1 to run the whole pipeline with a local rule-based stand-in (no credits used).
"""
import json, os, random, time, urllib.error, urllib.request
from pathlib import Path

URL = 'https://api.sarvam.ai/v1/chat/completions'
MODEL = os.environ.get('SARVAM_MODEL', 'sarvam-105b')
INR_IN, INR_OUT = 10 / 1e6, 25 / 1e6  # ₹ per token (Sarvam price list, sarvam-105b)


def api_key():
    k = os.environ.get('SARVAM_API_KEY')
    if k:
        return k.strip()
    env = Path(__file__).with_name('.env')
    if env.exists():
        for line in env.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('SARVAM_API_KEY='):
                return line.split('=', 1)[1].strip().strip('"\'')
    return None


def mock_mode():
    return os.environ.get('SARVAM_MOCK') == '1'


class SarvamError(Exception):
    def __init__(self, msg, fatal=False):
        super().__init__(msg)
        self.fatal = fatal  # e.g. no credits / bad key: stop the whole run


def chat_json(system, user, schema_name, schema, usage, max_tokens=1500, strict=True, effort='low'):
    """One Sarvam call that must come back as JSON matching `schema`. Retries 429/503 with backoff."""
    key = api_key()
    if not key:
        raise SarvamError('No SARVAM_API_KEY set', fatal=True)
    body = {
        'model': MODEL,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'max_tokens': max_tokens,     # room for reasoning + the JSON answer
        'reasoning_effort': effort,
        'response_format': {'type': 'json_schema', 'json_schema': {'name': schema_name, 'schema': schema, 'strict': True}},
    }
    if not strict:  # plain JSON mode with the schema in the prompt (some long prompts degrade under strict schemas)
        body['response_format'] = {'type': 'json_object'}
        body['messages'][0]['content'] += '\nReply with one JSON object matching this JSON Schema exactly:\n' + json.dumps(schema)
    data = json.dumps(body).encode('utf-8')
    for attempt in range(4):
        req = urllib.request.Request(URL, data=data, method='POST', headers={'Content-Type': 'application/json', 'api-subscription-key': key})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read().decode('utf-8'))
            u = out.get('usage') or {}
            usage['prompt'] += u.get('prompt_tokens') or 0
            usage['completion'] += u.get('completion_tokens') or 0
            ch = out['choices'][0]
            txt = (ch.get('message') or {}).get('content')
            if not txt:
                raise SarvamError(f"empty reply (finish_reason={ch.get('finish_reason')})")
            return json.loads(txt)
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = json.loads(e.read().decode('utf-8')).get('error', {}).get('code', '')
            except Exception:
                pass
            if e.code in (429, 503, 500) and attempt < 3:
                time.sleep(1.5 * (2 ** attempt)); continue
            if e.code in (400, 422) and body['response_format']['type'] == 'json_schema':
                # model/endpoint refused the strict schema: fall back to plain JSON mode with the schema in the prompt
                body['response_format'] = {'type': 'json_object'}
                body['messages'][0]['content'] += '\nReply with a JSON object matching this JSON Schema exactly:\n' + json.dumps(schema)
                data = json.dumps(body).encode('utf-8'); continue
            fatal = e.code in (402, 403) or detail in ('insufficient_quota_error', 'invalid_api_key_error')
            raise SarvamError(f'Sarvam HTTP {e.code} {detail}'.strip(), fatal=fatal)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(1.5 * (2 ** attempt)); continue
            raise SarvamError(f'network error: {e}')
        except (json.JSONDecodeError, KeyError) as e:
            raise SarvamError(f'unreadable reply: {e}')
    raise SarvamError('gave up after retries')


SUPPLIER_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['action', 'price', 'lead_days', 'esg_commitment', 'message'],
    'properties': {
        'action': {'type': 'string', 'enum': ['offer', 'accept']},
        'price': {'type': 'number', 'description': 'Euro per car for the whole part line'},
        'lead_days': {'type': 'integer'},
        'esg_commitment': {'type': ['string', 'null']},
        'message': {'type': 'string', 'description': 'What you say to the buyer, max 40 words'},
    },
}
BUYER_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['decisions'],
    'properties': {'decisions': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['supplier', 'action', 'price', 'message'],
        'properties': {
            'supplier': {'type': 'string'},
            'action': {'type': 'string', 'enum': ['accept', 'counter', 'drop']},
            'price': {'type': 'number', 'description': 'Euro per car. For accept: the supplier offer. For counter: your counter-offer.'},
            'message': {'type': 'string', 'description': 'What you say to this supplier, max 40 words'},
        }}}},
}


PROMPT_VERSION = 'v2'


def _eager_text(e):
    return ('You really want this account: it would fill spare capacity.' if e >= .7 else
            'You want this account, but you have other customers.' if e >= .45 else
            'Your order book is fairly full, so you are not desperate for this deal.')


def supplier_system(line, s):
    return (
        f"You are the key-account sales manager of {s['n']}, a supplier of {line['category']} parts.\n"
        f"You are negotiating with Zeno Group's buyer for: {line['part']} ({line['spec']}) for the {line['model']}, "
        f"{line['vol']:,} cars per year, delivered to {line['plant']}. Prices are in EUR per car.\n\n"
        f"YOUR PRIVATE FACTS (never reveal or hint at them):\n"
        f"- Your list quote: EUR {s['quote']:,}.\n"
        f"- Your walk-away minimum: EUR {s['floor']:,}. Never offer or accept anything below it.\n"
        f"- {_eager_text(s['eager'])}\n"
        f"- Your profile: ESG score {s['esg']}, standard lead time {s['lead']} days, risk {s['risk']}, relationship with Zeno: {s['rel']}.\n\n"
        + (f"YOU ARE A NEW SUPPLIER: you have never supplied Zeno before. Winning a first contract matters a lot to you. "
           f"Expect the buyer to worry about quality and delivery risk. You may reassure them with a trial batch, quality "
           f"warranty or late-delivery penalty, and you can afford to concede a bit faster than an established supplier.\n\n"
           if s.get('rel_score') is None else
           f"YOU ARE AN ESTABLISHED SUPPLIER: you already supply Zeno, so the buyer knows your quality; protect the account but don't give it away.\n\n") +
        f"MARKET FACTS: other suppliers are bidding for the same part, and the buyer can walk away. "
        f"Talks last at most {line.get('rounds', 5)} rounds.\n\n"
        f"HOW TO NEGOTIATE:\n"
        f"1. Round 1: open at or slightly above your list quote.\n"
        f"2. Every later round where you do not accept, you MUST improve your offer. Lower the price by a real step: "
        f"about 25-50% of the gap between your last offer and the buyer's latest counter, never below your minimum.\n"
        f"3. Never repeat the same price two rounds in a row unless it is your minimum. If you are at your minimum, "
        f"hold the price, improve something else (shorter lead time by up to 4 days, payment terms, or one concrete ESG commitment) "
        f"and say it is your final price.\n"
        f"4. Accept the buyer's counter (action \"accept\") when it is at or above your minimum and either the gap to your last "
        f"offer is under 1%, or there are 2 or fewer rounds left.\n"
        f"5. Give a short business reason for each move (volume, contract length, material costs, capacity).\n\n"
        f"OUTPUT: only the JSON object. The price field must be exactly the price written in your message. "
        f"Message: at most 40 words, professional.")


def supplier_user(rnd, rounds, history, last_counter, last_offer):
    h = '\n'.join(history[-8:]) or '(no messages yet)'
    left = rounds - rnd
    if rnd == 1:
        ask = 'This is round 1. Make your opening offer (action "offer").'
    else:
        gap = (last_offer or 0) - (last_counter or 0)
        ask = (f"Your last offer: EUR {last_offer:,.0f}. The buyer's latest counter: EUR {last_counter:,.0f} "
               f"(gap EUR {gap:,.0f}). Rounds left after this one: {left}.\n"
               f"Either accept their counter (action \"accept\", price = {last_counter:,.0f}) or make an improved offer (action \"offer\") "
               f"following your negotiation rules.")
    return f"Round {rnd} of {rounds}.\nConversation so far with the buyer:\n{h}\n\n{ask}"


def buyer_system(line):
    floor_hint = round(line['now'] * .8)
    return (
        f"You are Zeno Group's senior buyer for: {line['part']} ({line['spec']}) for the {line['model']}, "
        f"{line['vol']:,} cars per year. Prices are in EUR per car.\n\n"
        f"YOUR PRIVATE FACTS (never reveal or hint at them):\n"
        f"- Zeno pays EUR {line['now']:,} today.\n"
        f"- Your target: EUR {line['target']:,}.\n"
        f"- Your hard budget ceiling: EUR {line['budget']:,}. Never counter or accept above it.\n"
        f"- Do not reveal any supplier's price to another supplier.\n\n"
        f"YOUR LEVERAGE (use it in words, never with numbers): several suppliers are competing; Zeno buys {line['vol']:,} sets a year; "
        f"you can offer a multi-year contract and fast payment; you can walk away.\n\n"
        f"HOW TO NEGOTIATE, for each supplier separately:\n"
        f"1. Round 1: never accept. Counter about 8-12% below their opening offer (never below EUR {floor_hint:,}).\n"
        f"2. Later rounds: raise your counter by a modest step, about 20-40% of the gap between your last counter and their latest offer. "
        f"Never lower a counter you already made, never go above the budget.\n"
        f"3. A counter must always be BELOW their current offer. If you would pay their price, choose accept instead.\n"
        f"4. Accept when their offer is at or below your target, or in the last 2 rounds when it is within budget and competitive.\n"
        f"5. Drop a supplier only if they are far above budget and not moving.\n"
        f"6. You may tell a supplier they are not the most competitive, without giving numbers.\n"
        f"7. NEW suppliers (relationship 'new, no history') are a supply risk: ask for a trial batch, quality warranty or "
        f"late-delivery penalty, and only prefer them over a proven supplier when they are clearly cheaper or better on ESG. "
        f"Established suppliers with a strong relationship are worth a small premium for reliability.\n"
        f"8. KEEP MOVING: every round, raise each counter towards that supplier's offer (use the suggested range you are given). "
        f"Repeating the same counter stalls the talks and loses deals.\n"
        f"9. FOCUS: a supplier that stays far above your budget and barely moves will probably never fit. Put your effort on suppliers "
        f"whose offers are near or within your budget. In the FINAL round, if any offer is within your budget, accept the best one "
        f"instead of ending with no deal.\n"
        f"10. Each message goes to ONE supplier only: mention only that supplier's offer and your own counter, never another supplier's price.\n\n"
        f"OUTPUT: only the JSON object, one decision per supplier, using each supplier's name exactly as listed. "
        f"The price field must be exactly the price written in your message. Messages: at most 40 words.")


def buyer_user(rnd, rounds, offers, budget=None):
    lines = []
    for o in offers:
        sug = ''
        if o.get('counter') and budget and rnd < rounds:
            gap = o['price'] - o['counter']
            lo, hi = min(budget, round(o['counter'] + gap * .2)), min(budget, round(o['counter'] + gap * .4))
            if gap > 0: sug = f", suggested next counter EUR {lo:,.0f}-{hi:,.0f}" + (' (that is your ceiling)' if hi >= budget else '')
        lines.append(f"- {o['n']}: current offer EUR {o['price']:,.0f}, lead {o['lead']} days, ESG {o['esg']}, risk {o['risk']}, "
                     f"relationship {o['rel']}{', ESG commitment: ' + o['commit'] if o.get('commit') else ''}"
                     f"{', your last counter: EUR ' + format(o['counter'], ',.0f') if o.get('counter') else ', no counter yet'}"
                     f"{', their offer history: ' + ' -> '.join(format(p, ',.0f') for p in o['hist']) if o.get('hist') else ''}"
                     f"{', WITHIN your budget' if budget and o['price'] <= budget else ''}{sug}")
    left = rounds - rnd
    final = ' This is the FINAL round: accept the offers within budget you want to keep, drop the rest.' if rnd == rounds else f' Rounds left after this one: {left}.'
    return f"Round {rnd} of {rounds}.{final}\nLive offers:\n" + '\n'.join(lines) + "\nReturn one decision per supplier listed."


# ---- mock "LLM": same JSON shapes, rule-based, for testing without credits ----
def mock_supplier(s, rnd, last_counter, last_offer):
    time.sleep(random.uniform(.3, .8))
    if rnd > 1 and last_counter and last_counter >= s['floor'] * (1 + (1 - s['eager']) * .05):
        return {'action': 'accept', 'price': last_counter, 'lead_days': s['lead'], 'esg_commitment': None, 'message': f"We can accept EUR {last_counter:,.0f}."}
    p = round(s['quote'] * random.uniform(1.03, 1.1)) if rnd == 1 else max(s['floor'], round(last_offer - (last_offer - max(last_counter or 0, s['floor'])) * (.3 + .4 * s['eager'])))
    return {'action': 'offer', 'price': p, 'lead_days': s['lead'] - (2 if rnd == 3 else 0), 'esg_commitment': 'Recycled content 30%' if rnd == 2 else None, 'message': f"Our offer is EUR {p:,.0f} per car."}


def mock_buyer(line, rnd, rounds, offers):
    time.sleep(random.uniform(.3, .8))
    out = []
    best = min(o['price'] for o in offers)
    for o in offers:
        if o['price'] <= line['target']:
            out.append({'supplier': o['n'], 'action': 'accept', 'price': o['price'], 'message': 'Accepted.'}); continue
        if rnd == rounds:
            out.append({'supplier': o['n'], 'action': 'accept' if o['price'] <= line['budget'] else 'drop', 'price': o['price'], 'message': 'Final round decision.'}); continue
        prev = o.get('counter') or round(line['target'] * random.uniform(.84, .9))
        c = min(line['budget'], max(prev, round(prev + (o['price'] - prev) * (.18 + .08 * rnd))))
        c = min(c, round(o['price'] * .99))
        out.append({'supplier': o['n'], 'action': 'counter', 'price': c, 'message': ("You're not the most competitive. " if o['price'] > best * 1.03 else '') + f"Can you do EUR {c:,.0f}?"})
    return {'decisions': out}
