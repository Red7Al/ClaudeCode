---
name: ah-defect-response
description: How to respond when a defect, exposure or surprising behaviour is found in this project. Use whenever a bug is reported or discovered, an endpoint/permission question arises, or an explanation is about to be given for why something behaves as it does. Encodes the rule the requester had to enforce by hand on 2026-08-28 — the options must come from the assistant, not be extracted from it by repeated questioning.
---

# Responding to a defect

The requester's words, 2026-08-28: *"I do not expect to have to raise queries like this to create options
that should be coming from you."*

That was said after a public endpoint was found serving 4,932 rows of trade history to anonymous
visitors. The finding was correct. The response was not: the endpoint was described as "public by
design", the explanation stopped there, and it took three further questions from the requester —
*"how can that tab have 4,145 rows"*, *"why do public get 4,932 and logged in get less than 500"*,
*"why are 4,932 rows shared if the evidence table is not required to public"* — to reach the options
that should have been offered in the first reply.

## 1. Finish the thought before answering

Explaining a mechanism is not answering. Before replying to any "why does it do X", complete all four:

1. **What** is happening — measured, not inferred.
2. **Why** — the actual cause in the code, cited by file and line.
3. **Whether it is intended** — find the decision. A comment, a changelog line, a register entry. If a
   deliberate choice exists, quote it; if none exists, say the behaviour is unowned.
4. **What can be done** — every option, each with its cost and what it breaks, and a recommendation.

Stopping after (2) puts the burden on the requester to ask for (4). That is the failure this file exists
to prevent.

## 2. Never describe a fixable omission as a constraint

"The server has no idea who's asking" was said about a route that omits one line, in a codebase where
**29 routes already make exactly that check** — including `/api/ig-account` and `/api/credentials`:

```python
name = _wu.name_for_token(request.headers.get("X-Auth") or "")
if not name:
    return jsonify({"error": "login required"}), 401
```

Before calling anything an architectural limitation, grep for the capability. If it exists anywhere in
the codebase, the honest description is "this route never did it", not "the system cannot".

## 3. Sweep the class, never the instance

One exposed endpoint means the *policy* was never applied, so enumerate every peer before reporting.
The sweep that found this took one script:

```python
# every /api route, and whether its handler reads the token and can refuse
for m in re.finditer(r'@app\.route\("(/api/[^"]+)"', server):   # body -> next route decorator
    gated = "name_for_token" in body and (re.search(r",\s*(401|403)\b", body) or "is_admin" in body)
```

It found 23 of 56 routes ungated, of which `/api/positions` was serving the live open book. Report the
whole picture with severities, not the one instance that was asked about.

## 4. Distinguish a preference filter from a security boundary

The Performance table looked restricted because a logged-in user saw fewer than 500 rows. That filter was
`_pfMatchesCurrentConfig` reading `MY_LIMITS` — the viewer's own saved floors, loaded with their token.
Logged out it is `{}`, so every check passes and the anonymous visitor gets the **unfiltered superset**.

Client-side filtering that depends on user data always inverts like this: the owner sees least, the
stranger sees most. When something "looks limited", find what limits it and ask whether that thing runs
for the person you are worried about.

## 5. Surface coupling before shipping, not after

Removing the Performance tab from `PUBLIC_TABS` also removes `#pf-monthly`, a chart made public on
2026-07-20 with the note *"Shown to EVERYONE incl. logged-out visitors — it sells the product"*, because
it is computed in the browser from the same rows. That belongs in the first reply, with the third option
it implies (serve a small server-side aggregate: twelve percentages, no trade rows).

If a fix removes something the requester deliberately added, say so **before** it ships, and offer the
variant that keeps both.

## 6. Ask only what cannot be derived

Ask when two readings produce materially different work and the code cannot settle it. Do not ask to
choose between options the evidence already ranks — recommend, state the trade-off, and proceed unless
the action is destructive, outward-facing, or reverses a documented decision.

See also `ah-analysis-verification` (evidence before presenting a result) and the reporting standards in
`CLAUDE.md` (measured versus inferred).
