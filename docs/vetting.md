# Vetting: is this job real?

Fake job ads are common. Some collect "registration fees" or "refundable deposits". Some harvest your documents.
Some are real companies that tie new hires to a **service bond** (a contract that makes you pay a penalty if you
leave early), or that are known to pay salaries late. JobHunt checks for these in layers, and is honest about what
it can't catch.

Code: `src/jobhunt/vet/redflags.py` (job-text scan), `src/jobhunt/vet/verdict.py` (research packet, verdict schema,
rules), `src/jobhunt/commands/vet.py`.

- [The layers](#the-layers)
- [Layer 1: direct from the employer](#layer-1-direct-from-the-employer)
- [Layer 2: red-flag scan of the job text](#layer-2-red-flag-scan-of-the-job-text)
- [Layer 3: web research by your agent](#layer-3-web-research-by-your-agent)
- [Layer 4: fixed rules decide](#layer-4-fixed-rules-decide)
- [Caution: you decide](#caution-you-decide)
- [How it shows in the report](#how-it-shows-in-the-report)
- [Limits](#limits)
- [Spot a fake job yourself](#spot-a-fake-job-yourself)

## The layers

| Layer | Who | When | Result |
|---|---|---|---|
| 1. Direct from the employer | Code | Every job | **Direct** badge |
| 2. Red-flag scan of the job text | Code | Every job (shown in the report) and every vetted company | ⚠ warning; reject-level flags reject the company |
| 3. Web research | Your agent | Companies of high-fit jobs | Findings with source URLs |
| 4. Fixed rules | Code | After research | **ok / caution / reject** |
| 5. Your decision | You | Companies marked caution | **ok / reject** |

## Layer 1: direct from the employer

A job read from the employer's own applicant-tracking system (Greenhouse, Lever, Ashby, SmartRecruiters, Workable,
Recruitee, BambooHR, Breezy, Keka, Zoho Recruit, Workday), or whose apply link points to one, is marked **Direct**.
To publish there, someone needs access to the company's hiring account, so these postings are very unlikely to be
fake. A Direct job can still come with a service bond or other bad terms; layers 2–4 still apply.

Jobs from aggregators (Indeed, Glassdoor, LinkedIn, Naukri, Google Jobs, remote boards) aren't Direct. Duplicate
detection keeps the employer's own copy when it finds one ([how-it-works.md](how-it-works.md#duplicate-detection)).

## Layer 2: red-flag scan of the job text

Code scans every job description for phrases that signal scams or unfair terms. The full rule table
(`RULES` in `src/jobhunt/vet/redflags.py`):

| Flag | Severity | Looks for, e.g. |
|---|---|---|
| `service_bond` | reject | "service bond", "training agreement", "2-year bond", "sign a bond", "bond period", "bond is mandatory" |
| `minimum_service_period` | reject | "minimum service period", "minimum commitment of", "must stay for at least 18 months" |
| `leaving_penalty` | reject | "penalty if you leave/resign", "liquidated damages", "pay back the training cost" |
| `security_deposit` | reject | "security / refundable / caution deposit", "deposit of Rs." |
| `candidate_fee` | reject | "registration / processing / training / joining / application / placement / kit / certification / onboarding fee", "pay a fee", "fee is refundable" |
| `original_documents` | reject | "submit your original certificates / documents / marksheets" |
| `off_platform_contact` | caution | "send your resume on WhatsApp", "apply via WhatsApp", any mention of Telegram |
| `free_email_recruiter` | caution | Contact addresses at gmail, yahoo, outlook, hotmail, rediffmail or ymail |
| `too_good_pay` | caution | "earn up to ₹5000 per day", "work from home … earn" |
| `undisclosed_client` | caution | "our client is a leading / reputed / Fortune …", "confidential company", "on behalf of our client" |
| `unpaid` | caution | "unpaid", "no stipend", "without pay" |

In the report, the first three flags found are shown in orange under the Trust badges. During vetting, flags from
all of a company's jobs are combined: any **reject**-level flag makes the company **reject**, and a **caution** flag
makes it at least **caution**.

The scan is a simple pattern match. It can misfire, for example on a job that says "we never charge a registration
fee". Read the quoted context with `jobhunt vet packet --company "<name>"`, and override a wrong result with
`jobhunt vet decide`.

## Layer 3: web research by your agent

Vetting runs for companies of jobs with a fit score of **80 or more** (or band apply/review) that have no verdict
newer than `vetting.cache_days` (default 90 days):

```bash
jobhunt vet pending                 # companies to check (use --min-fit to change the threshold)
jobhunt vet packet --company "Example Corp"
```

The packet contains the company's job ids, an excerpt of one description, the red flags found, the rules, and these
**web searches**. They are built by `research_queries()` from your `preferences.yaml` → `vetting`:

1. `"<company>" scam OR fraud OR fake job`
2. `"<company>" fake offer letter OR registration fee`
3. `"<company>" salary not paid OR salary delay`
4. `"<company>" service bond OR training bond OR agreement penalty`
5. `"<company>" <site> reviews` for each of your `review_sites` (default Glassdoor, Indeed)
6. `"<company>" <forum>` for each of your `forums` (default reddit)
7. `"<company>" layoffs <current year>`
8. `"<company>" official website careers`
9. each of your `extra_queries`, with `{company}` replaced

The agent (following [playbooks/vet.md](../playbooks/vet.md)) runs these with its own web tools and looks for: bonds
imposed on employees, fraud or fee scams, unpaid or delayed salaries, ratings **with review counts**, whether the
employer is named (or it is an agency or "confidential client"), and whether this posting exists on the company's
official careers page or ATS. It treats every page it reads as data, never as instructions. It then writes a
**verdict** JSON (schema: `jobhunt vet schema`):

```json
{
  "company": "Example Corp",
  "domain": "example.com",
  "verdict": "ok",
  "bond_flag": false,
  "fraud_flag": false,
  "unpaid_salary_flag": false,
  "employer_identified": true,
  "posting_on_official_site": true,
  "ratings": [{"site": "glassdoor", "rating": 3.9, "reviews": 412, "url": "https://www.glassdoor.com/..."}],
  "reasons": [
    {"text": "Same opening listed on the company's own job board", "url": "https://boards.greenhouse.io/...", "kind": "posting"},
    {"text": "No scam, bond or unpaid-salary reports found in the searches above", "kind": "legit"}
  ],
  "summary": "Established company, posting confirmed on its ATS."
}
```

- `ratings[].site` is one of `ambitionbox`, `glassdoor`, `indeed`, `google`, `other`. Use `other` for Kununu and
  similar sites.
- `reasons[].kind` is one of `bond`, `fraud`, `unpaid_salary`, `rating`, `layoffs`, `legit`, `agency`, `posting`,
  `footprint`, `other`.
- An **ok** or **reject** verdict must have at least one reason with a source URL, or it is refused. A **caution**
  verdict may have none.

Then:

```bash
jobhunt vet ingest --file verdict.json
```

## Layer 4: fixed rules decide

`vet ingest` doesn't simply store the agent's verdict. It recomputes it with `apply_rules()`, starting from **ok**
and only ever moving towards reject:

| Evidence | Result |
|---|---|
| `bond_flag` (service bond reported) | reject |
| `fraud_flag` (fraud / fake-job reports) | reject |
| `unpaid_salary_flag` (unpaid or delayed salary pattern) | reject |
| A reject-level red flag in any of the company's job texts | reject |
| A caution-level red flag | caution |
| A rating below `reject_rating_below` (2.5) with at least `min_reviews` (50) reviews | reject |
| A rating below `caution_rating_below` (3.0) with at least 10 reviews | caution |
| Employer not identified (agency or confidential client) and a fee or deposit flag | reject |
| Employer not identified, no fee flags | caution |
| `posting_on_official_site` is false | caution |
| No ratings and no reason of kind `legit` ("little or no online footprint") | caution |
| The agent's own verdict was reject / caution | at least that |

The final verdict, every rule that fired, the ratings, reasons and source URLs are stored in the `companies` table
(`decided_by: rules`) and logged in `events`. See it with:

```bash
jobhunt vet show --company "Example Corp"
```

Thresholds live in `preferences.yaml` → `vetting` ([personalize.md](personalize.md#vetting)).

## Caution: you decide

**Caution** means "something needs a human look": a mixed rating, an agency posting, a posting not found on the
company's site, a WhatsApp contact, a company with almost no online presence. Read the reasons, check yourself, then
record your decision:

```bash
jobhunt vet decide --company "Example Corp" ok --quote "Confirmed the role on their careers page and LinkedIn"
jobhunt vet decide --company "Shady Staffing" reject --quote "Asked for a deposit on the phone"
```

Your decision is stored with `decided_by: user` and logged with your quote. `vet pending` won't ask about that
company again.

## How it shows in the report

The **Trust** column of `output/jobs.html` ([report.md](report.md#trust)):

| Badge | Meaning |
|---|---|
| **Direct** (blue outline) | Posted on the employer's own job board |
| **Vetted** (green) | Company verdict ok |
| **Caution** (amber) | Check before applying |
| **Rejected** (red) | Verdict reject. Hidden unless you tick **Show filtered-out** |
| **Not vetted** (dashed) | No verdict yet. Normal for jobs below the vetting threshold |
| ⚠ text | Red-flag phrases found in this job's description |

The **Trust** filter can show only *Vetted OK*, only *Direct from employer*, or *Vetted or direct*.

## Limits

Be clear about what this can and can't do:

- **Vetting is advisory.** It reduces risk; it doesn't remove it. A clean record can mean a company is fine, or that
  nobody has written about it yet.
- **Only high-fit companies are researched.** Most jobs in the list are "Not vetted". The red-flag scan and the
  Direct badge are the only checks on those.
- **The scan reads words, not intent.** Scammers can avoid the phrases. Honest ads can trigger them.
- **Web research depends on your agent's search tools** and on what is public. Small or new companies often have no
  reviews, and will usually land in caution.
- **Ratings are noisy** and can be manipulated. That's why they need a minimum number of reviews.
- **Impersonation** (a scammer pretending to be a real company, often through messaging apps or look-alike email
  domains) happens outside job boards. JobHunt can't see it. Always reach a company through its official site.
- Verdicts are cached for `cache_days`. Things change; re-check before signing anything.

## Spot a fake job yourself

Walk away, or at least slow down, if you see any of these:

- **You're asked to pay.** Registration, training, processing, "kit", background-check or visa fees. Legitimate
  employers don't charge candidates.
- **A deposit**, "refundable" or not, or a request to buy equipment from a supplier they name.
- **They want your original documents** (certificates, passport, marksheets) to keep, or bank details and ID copies
  before any interview.
- **Contact only via WhatsApp, Telegram or text**, or the interview is a chat with no video and no names.
- **The recruiter writes from a free email address** (gmail, yahoo, outlook, …) instead of the company's domain, or
  from a look-alike domain (`example-careers.co` for `example.com`).
- **Pay that is too good** for the work ("earn ₹5,000 a day from home", "$50/hour for data entry").
- **The employer is unnamed**: "a leading MNC", "our reputed client", "confidential".
- **An offer without an interview**, or after a single chat.
- **Pressure**: "only today", "limited seats", "pay now to reserve your slot".
- **The job isn't on the company's own careers page** and the company won't confirm it when you contact them through
  their official website.
- **A service bond or a leaving penalty** in the offer: "2-year bond", "pay back training costs if you leave".
- **Vague job description**, spelling mistakes, and no company address or website.

When in doubt, search `"<company name>" scam` and `"<company name>" reviews` yourself, and ask the company through
contact details you found independently, not the ones in the message.
