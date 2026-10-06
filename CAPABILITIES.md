# What the Freshdesk agent can and cannot do

I built this connector so an AI agent (tested with Claude Desktop) can read a Freshdesk helpdesk
such as `yourcompany.freshdesk.com`. This page is the honest version of what that agent can do, what
it can't, and where a human has to stay in charge. Ticket numbers refer to the fictional seeded
data (see [demo/demo.md](demo/demo.md)).

## What it can do

The agent has four read-only tools: `list_tickets`, `get_ticket`, `search_tickets` and
`get_ticket_conversations`. That's enough for questions like:

- **"Which urgent tickets are still open?"** It filters by status and priority and gets back the
  matching tickets with a total count.
- **"Summarise ticket 5."** `get_ticket` shows the first 10 messages and flags that the thread may
  be longer. The agent then reads the full 14-message thread with `get_ticket_conversations`.
- **"What actually went wrong in ticket 4?"** The merchant wrote "is your checkout broken?". Only
  later in the thread does it turn out that *their own* webhook endpoint returned HTTP 500 after
  *their own* deploy. Nothing in the subject, description or tags mentions webhooks, so the agent
  only finds the cause by reading the whole conversation.
- **"Are tickets 6 and 7 the same issue?"** Both come from the same requester about the same order
  (FX-1042), worded differently. The agent can compare them once it has fetched both; there is no
  automatic duplicate detection.

## What it cannot do

- **Change anything.** The connector only sends HTTP `GET`. There is no code path to reply,
  change status, merge, assign or close.
- **Read attachments.** It sees file names only, never contents or download URLs.
- **See requester emails or phone numbers.** This is deliberate data minimisation: the model can't
  leak what it never receives. It sees requester ids and names.
- **Search by words.** Search filters only on status, priority, one tag and created/updated dates.
  There is no free-text search in subjects or bodies.
- **See old tickets by default.** `list_tickets` without `updated_since` covers only tickets created
  in the last 30 days (a Freshdesk default).
- **See a whole thread at once.** `get_ticket` includes at most the first 10 messages; longer
  threads need `get_ticket_conversations`, page by page.

## What it should refuse

The server instructions tell the agent to:

- say when the answer is **not in Freshdesk** (revenue, churn scores, anything from other systems)
  instead of guessing;
- **not infer** emotions, sentiment, intent or churn risk. It may report factual signals (overdue,
  no reply, number of follow-ups) and must say they are not a prediction;
- **not offer write actions** through any other tool, such as a browser. It drafts the text and
  says a human must do it in Freshdesk.

## Human in the loop

A human acts before anything reaches a customer or changes a ticket: **replying, closing, merging
or escalating**. The agent's job is to read, summarise and draft; the person decides and does it in
Freshdesk.

## Failure modes: what the agent sees

| Situation | What the agent gets |
|---|---|
| Rate limited | The client first waits and retries (honouring `Retry-After`, within a 30-second budget). If the wait is too long or retries run out, the agent gets a tool error with the wait time, e.g. `[rate_limited] Freshdesk rate limit reached; retry after 40 seconds.` (rounded up, so it never retries too early). |
| Partial results | `meta.has_more`, `meta.truncated` and plain-language `meta.notes`, e.g. "call again with page=2" or "only the first 300 results are reachable". |
| Data changing between calls | Nothing special. Every call reads live data, so two calls can disagree if a ticket was edited in between. There's no snapshot or warning. |
| Ticket deleted mid-read | A normal result, `found: false` with a message, not an error. |
| API access disabled or wrong key | `[auth_failed]` telling the user to check the API key and **Admin > Agents**. |

## Data boundaries

**Reaches the model:** ticket fields as plain text (`description_text`, `body_text`, with long
text truncated and flagged), status/priority/source as names, tags, dates, requester id and name,
the numeric id of each message's author, attachment *names*, and whether a message came from the
customer or is a private note.

**Deliberately never reaches it:** emails and phone numbers, HTML bodies, attachment contents and
URLs, CC/BCC lists, and the API key. The key is only in a git-ignored `.env`, never in logs,
errors or `repr()`.

## Evaluation findings

1. **A real smoke test caught what mocks missed.** Freshdesk sends rate-limit headers as `"50.0"`,
   and `int("50.0")` fails. My parser silently treated that as "no header", which switched off the
   low-remaining pause. My mocks had used `"50"`, so they agreed with my assumption, not with
   Freshdesk. Fixed; test fixtures now use the real format.
2. **The tests catch that bug.** As a mutation check, I re-introduced it: 3 tests failed.
3. **Prompt instructions are a soft guardrail.** In my Claude Desktop demo, the agent first offered
   searches it can't do and guessed how customers felt. I tightened the server instructions and
   re-tested. The over-promising stopped, but a churn-risk question still got an answer naming a
   merchant. Instructions shape behaviour; they don't enforce it.
4. **Read-only is a property of this connector, not of the agent.** The agent offered to make the
   change through a browser instead. A real deployment must also control the agent's *other*
   tools, not just this one.

## What I'd build next, and why not yet

- **A least-privilege Freshdesk agent for the API key.** Today the key belongs to an admin
  account. A dedicated agent with the narrowest role limits damage if the key leaks. Not done yet
  because it is Freshdesk admin setup rather than code.
- **Write tools behind explicit human approval** (e.g. "draft reply, human confirms"). Left out on
  purpose: the brief asks for reading, and read-only keeps the safety story simple.
- **A 30-question evaluation set run on every change**, so instruction tweaks are measured, not
  eyeballed. Finding 3 above is why this matters; it needs a labelled question set first.
- **Detailed output schemas** generated from the pydantic models. Today tools return plain dicts,
  so the advertised output schema is a generic object. Changing it touches every tool's return
  type, and the input schemas mattered more for correct tool use.
