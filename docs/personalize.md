# Personalise JobHunt

JobHunt knows nothing about you until you tell it. Two files in `profile/` decide which jobs are fetched, which
are dropped, and how the rest are ranked:

| File | Holds | Example |
|---|---|---|
| `profile/preferences.yaml` | **What you want** | "Backend or AI roles, in Bengaluru first, remote OK across Asia, at least ₹12 L a year" |
| `profile/resume.yaml` | **Facts about you** | "Python, hands-on. Built a RAG assistant with FastAPI at Acme." |

Nothing personal is hard-coded in the program. If the results look wrong, the fix is almost always in these two
files.

**On this page**

- [Three ways to fill your profile](#three-ways-to-fill-your-profile)
- [preferences.yaml, field by field](#preferencesyaml-field-by-field)
- [Example profiles for four people](#example-profiles-for-four-people)
- [Writing good search queries and title keywords](#writing-good-search-queries-and-title-keywords)
- [resume.yaml, field by field](#resumeyaml-field-by-field)
- [Writing honest resume facts](#writing-honest-resume-facts)
- [Checking your profile](#checking-your-profile)
- [Common validation errors and fixes](#common-validation-errors-and-fixes)
- [Tuning after your first run](#tuning-after-your-first-run)

---

## Three ways to fill your profile

Start with `jobhunt init`. It copies the example profile (the fictional **Sam Example**, an AI engineer in
Bengaluru) into `profile/`. Then pick one of these:

### a) The browser form

1. Run `jobhunt profile form`. It writes `output/profile_form.html`, prefilled with your current profile (or the
   example if you have none), and opens it in your browser. It is a single offline page. Add `--no-open` to only
   write the file, or `--example` to start from the example profile.
2. Fill it in. The status button at the top shows any problems the form finds (click it to jump to the list).
   **Start over** discards your changes; **Copy as JSON** copies the result instead of downloading it.
3. Click **Download profile**. Your browser saves `jobhunt-profile.json`.
4. Install it:

   ```bash
   jobhunt profile import ~/Downloads/jobhunt-profile.json
   ```

   Your old `preferences.yaml` and `resume.yaml` are first copied to `profile/preferences.<date>.bak.yaml` and
   `profile/resume.<date>.bak.yaml`. If the file has a problem, nothing is written and you see what to fix.

`jobhunt profile import` also accepts a single YAML file that starts with `kind: preferences` or `kind: resume`.

### b) Ask your coding agent

Say *"set up my JobHunt profile from my resume"* and point it at your resume file. The agent follows
[playbooks/setup.md](../playbooks/setup.md):

1. It reads your resume. Any agent can get the plain text with:

   ```bash
   jobhunt profile extract-text path/to/resume.pdf     # also .docx, .md, .txt, .tex
   ```

2. It drafts `resume.yaml` from that text **only**, with honest skill levels and evidence levels.
3. It asks you about the things a resume doesn't say: target titles, country and cities, remote regions, salary
   floor, how senior a job you'd take, languages, companies to avoid.
4. It writes `preferences.yaml` and runs `jobhunt profile validate` until the profile is valid.

Review both files afterwards. You know your career better than any agent does.

### c) Edit the YAML by hand

Open `profile/preferences.yaml` and `profile/resume.yaml` in any text editor. Every field has a comment. YAML tips:

- Indent with **spaces**, never tabs.
- Lists can be written `[a, b, c]` or as lines starting with `- `.
- Put text that contains `:` or `#` in quotes: `summary: "Backend: Python, Go"`.
- Run `jobhunt profile validate` after every edit.

---

## preferences.yaml, field by field

The schema lives in `src/jobhunt/profile.py`. Defaults below are what you get when you leave a field out. Fields
marked **required** have no default. Unknown fields (for example a typo like `title_keyword:`) are rejected, so
typos don't fail silently.

### Top level

| Field | Default | What it does |
|---|---|---|
| `kind` | `preferences` | Marks the file type. Leave it. |
| `schema_version` | `1` | Leave it. |
| `target_titles` | **required** (at least one) | Titles you'd happily accept, e.g. `[Data Analyst, BI Analyst]`. Job titles are compared to these (fuzzy match). They also go to the agent when it judges fit. |
| `core_skills` | `[]` | Skills that define you. When a job mentions one, it counts **double** in the keyword score. Each must exist in `resume.yaml` → `skills` (by name). Keep it to 3–6. |
| `languages` | `[English]` | Spoken languages. Jobs that require fluency in another language are skipped. |

### `candidate`

| Field | Default | What it does |
|---|---|---|
| `years_experience` | `0` | Full-time professional experience in years (0–60). Internships count roughly half. Used to compare with the years a job asks for. |
| `work_authorization` | `""` | Free text, e.g. `"US citizen"` or `"EU Blue Card holder; needs sponsorship outside the EU"`. Passed to the agent. |
| `notice_period` | `""` | Free text, e.g. `"2 weeks"`. Passed to the agent. |
| `summary` | `""` | One line about you. Shown to the agent when it judges fit. |

### `title_keywords`

These words decide which job titles count as relevant. They are your most important search filter.

| Field | Default | What it does |
|---|---|---|
| `strong` | `[]` | A title containing one of these is relevant **on its own**, e.g. `machine learning`, `data analyst`, `frontend`. |
| `related` | `[]` | Relevant **only together with a role word**, e.g. `python` matches "Python Developer" but not "Python Instructor"... unless "instructor" is a role word. |
| `exclude` | `[]` | A title containing one of these is **never** shown, e.g. `intern`, `sales`, `recruiter`. |
| `role_words` | `engineer, engineering, developer, scientist, architect, programmer, sde, swe, consultant, specialist, analyst, designer, researcher, associate` | Words that make a `related` keyword count. |

Matching is whole-word and case-insensitive: `ai` matches "AI Engineer" but not "Maintenance".

Where each list is used:

- **Company job boards and remote boards** keep a title when it contains any `strong` **or** `related` word and no
  `exclude` word. (At this stage `related` words don't need a role word.)
- **Indeed, Glassdoor, LinkedIn, Naukri and SerpApi** already search by your queries, so only `exclude` applies
  there.
- **The report** shows a title when it has a `strong` word, or a `related` word plus a role word, and no `exclude`
  word. Jobs you marked as applied are always shown.
- **The keyword score** gives more title points to `strong` matches than to `related` + role-word matches.

At least one `strong` or `related` word is required. Without one, company boards would return every job.

### `experience`

| Field | Default | What it does |
|---|---|---|
| `max_required_years` | `3` | Jobs that ask for **more** years than this are skipped. Set it a little above your real experience; job ads often ask for more than they need. |
| `skip_title_words` | `staff, principal, director, head, vp, vice president, chief, cto, manager, distinguished, fellow, architect` | A title containing one of these is skipped as too senior. "Associate (Solutions) Architect" is let through. |
| `cap_title_words` | `senior, sr, lead` | A title with one of these is kept only if the job asks for at most `max_required_years`. Otherwise it is capped at the **maybe** band. |

If you are senior yourself, shorten these lists. A staff engineer would remove `staff` and `principal` from
`skip_title_words` and set `max_required_years: 15`. A people manager would remove `manager`.

### `location`

| Field | Default | What it does |
|---|---|---|
| `home_country` | `IN` | Two-letter ISO code: `IN`, `US`, `GB` (or `UK`), `DE`, `CA`, `AU`, `SG`, `AE`, `NL`, `PH`, `BR`, … The full list is `COUNTRIES` in `src/jobhunt/geo.py`. |
| `tier1` | `[]` | First-choice cities. Jobs there get location tier 1. |
| `tier2` | `[]` | Second-choice cities (tier 2). Anywhere else in your country is tier 3. Abroad is tier 4. |
| `extra_cities` | `[]` | Cities in your country that the built-in list doesn't know, so they are recognised as "home". |
| `remote_ok_scopes` | depends on country | Remote regions that include you. See below. |
| `abroad` | `relocation` | What to do with jobs in other countries. See below. |

**The built-in city list is small:** major tech-hiring cities only. India has a detailed table with aliases
(Bangalore → Bengaluru, Gurgaon → Gurugram, …). For other countries, if your city isn't recognised, add it to
`tier1`, `tier2` or `extra_cities`. Cities you list are learned automatically.

**`remote_ok_scopes`**: a remote job is kept when the region it's open to is one of these tokens.

| Token | Means |
|---|---|
| Your country code | `IN`, `US`, `UK` (for GB), `DE`, `PH`, `CA`, `AU`, `SG`, … |
| `WORLDWIDE` | "Anywhere", "worldwide", "global" |
| `APAC` | Asia-Pacific |
| `ASIA` | Asia (not "Asia-Pacific") |
| `EMEA` | Europe, Middle East and Africa |
| `EU` | Europe / EU / CET time zones |
| `LATAM` | Latin America |
| `ME` | Middle East / GCC |

If you leave it out, you get your country, its regions, and `WORLDWIDE`. Run `jobhunt profile show` to see the
result. Defaults include:

| Home country | Default `remote_ok_scopes` |
|---|---|
| `IN` | `IN, APAC, ASIA, WORLDWIDE` |
| `US` | `US, WORLDWIDE` |
| `GB` | `UK, EMEA, WORLDWIDE` (add `EU` only if you have EU work rights) |
| `DE` | `DE, EMEA, EU, WORLDWIDE` |
| `PH` | `PH, APAC, ASIA, WORLDWIDE` |
| `BR` | `BR, LATAM, WORLDWIDE` |
| `AE` | `AE, EMEA, ME, WORLDWIDE` |

Remove a region if those jobs usually need a time zone you can't work. Remote jobs whose region can't be detected
are kept but flagged ("remote, region unknown") and capped at the **review** band, so you check them.

**`abroad`**:

| Value | Onsite/hybrid jobs abroad | Remote jobs abroad |
|---|---|---|
| `relocation` (default) | Kept **only** if the ad offers relocation or visa sponsorship | Kept if open to your remote scopes |
| `remote_only` | Skipped | Kept if open to your remote scopes |
| `skip` | Skipped | Skipped — only jobs based in your home country (onsite, hybrid or remote) remain |

Ads that say "no visa sponsorship" are skipped for onsite jobs abroad. For remote jobs they are kept, but the
score is capped at 40.


### `salary`

| Field | Default | What it does |
|---|---|---|
| `currency` | `USD` | Three-letter code of your floor: `INR`, `USD`, `EUR`, `GBP`, `PHP`, … |
| `min_per_year` | none | Your floor per year. Jobs that **state** less are flagged. Jobs that don't state pay are never penalised. |
| `below_min_action` | `review` | `review`: keep but cap at the review band. `skip`: drop. |

Monthly and hourly pay is converted to yearly (×12, ×2000), and other currencies are converted with built-in
approximate rates. Treat the comparison as rough. Set the floor a bit below your real minimum so you don't lose
borderline jobs.

### `search`

| Field | Default | What it does |
|---|---|---|
| `queries` | **required** | Search phrases by tier: `{1: [...], 2: [...], 3: [...]}`. Tiers 1 and 2 run every time. Add tier 3 with `jobhunt search run --tiers 1,2,3`. Needs tier 1 or 2. |
| `locations` | `[]` | Where Indeed, Glassdoor, LinkedIn, Naukri and SerpApi search, e.g. `[Berlin, Hamburg]`. |
| `remote_locations` | `[Remote]` | Extra searches for remote jobs. `Remote` searches remote jobs in your home country's edition of each site. |
| `sources` | all defaults | Which sources run. Leave it out to use all default sources (see [sources.md](sources.md)). If you set it, it **replaces** the default list. |
| `disabled_sources` | `[]` | Sources to switch off, e.g. `[jobspy_linkedin, jobspy_naukri]`. |
| `budgets` | per-source defaults | Maximum calls per run for each source, e.g. `{jobspy_indeed: 12}`. Values must be whole numbers. See [sources.md](sources.md#budgets-and-rate-limits). |
| `posted_within_days` | `7` | Indeed, Glassdoor, LinkedIn and Naukri only return jobs posted within this many days (1–60). |
| `results_per_call` | `30` | Maximum jobs per search call (1–100). For company-board sources it is the maximum per board (with a safety cap of 3,000 per source per run). |
| `max_boards` | `300` | How many watchlist boards are polled per run (0–5000). |

Search sources look in every pair of query × location (`locations` + `remote_locations`), spread out so a small
budget still reaches every query.

### `exclusions`

| Field | Default | What it does |
|---|---|---|
| `companies` | `[]` | Company names you never want to see. Matching is exact, case-insensitive. Write the name as job ads show it. |

### `vetting`

Settings for checking companies. See [vetting.md](vetting.md).

| Field | Default | What it does |
|---|---|---|
| `reject_rating_below` | `2.5` | A review-site rating below this, with at least `min_reviews` reviews, rejects the company. |
| `caution_rating_below` | `3.0` | A rating below this (with at least 10 reviews) means caution. |
| `min_reviews` | `50` | Reviews needed before a low rating can reject. |
| `cache_days` | `90` | How long a verdict is reused before the company is checked again. |
| `review_sites` | `[Glassdoor, Indeed]` | Review sites the agent searches, e.g. `[AmbitionBox, Glassdoor]` for India or `[Kununu, Glassdoor]` for Germany. |
| `forums` | `[reddit]` | Communities the agent searches, e.g. `[reddit cscareerquestions]`. |
| `extra_queries` | `[]` | More web searches; `{company}` is replaced, e.g. `"{company} layoffs"`. |

### `report`

| Field | Default | What it does |
|---|---|---|
| `title` | `My job matches` | Heading of `output/jobs.html`. |
| `posted_within_days` | `30` | The report hides jobs older than this (1–365). Jobs without a posting date use the date JobHunt first saw them. |

---

## Example profiles for four people

All four are fictional. Each snippet shows only the fields that differ from the example profile. The
`target_titles`, `candidate`, `core_skills` and other fields still need filling in.

### 1. India-based AI engineer (the example profile)

Sam lives in Bengaluru, would move to Hyderabad or Pune, and takes remote work open to India or Asia.

```yaml
location:
  home_country: IN
  tier1: [Bengaluru]
  tier2: [Hyderabad, Pune]
  remote_ok_scopes: [IN, APAC, ASIA, WORLDWIDE]
  abroad: relocation
salary:
  currency: INR
  min_per_year: 1200000          # ₹12 L
title_keywords:
  strong: [ai, ml, machine learning, llm, genai, generative, nlp, rag, agentic, mlops]
  related: [python, backend, software, platform, data, api]
  exclude: [intern, internship, sales, recruiter, marketing, hr]
search:
  queries:
    1: [LLM engineer, RAG engineer, Generative AI engineer]
    2: [AI engineer, Machine learning engineer, Python backend developer]
  locations: [Bengaluru, Hyderabad, Pune]
vetting:
  review_sites: [AmbitionBox, Glassdoor]
  forums: [reddit developersIndia]
```

India-based profiles also get Naukri and the India pages of the Y Combinator job board.

### 2. US-based data analyst

Alex is in Austin, Texas, open to Dallas or Houston, and to US-only remote work. No relocation abroad.

```yaml
location:
  home_country: US
  tier1: [Austin]
  tier2: [Dallas, Houston]
  remote_ok_scopes: [US, WORLDWIDE]
  abroad: skip
salary:
  currency: USD
  min_per_year: 75000
  below_min_action: skip
experience:
  max_required_years: 4
title_keywords:
  strong: [data analyst, business intelligence, bi analyst, analytics, reporting analyst]
  related: [data, sql, insights, product]
  exclude: [intern, sales, scientist, engineer, director, manager]
search:
  queries:
    1: [Data analyst, Business intelligence analyst]
    2: [SQL analyst, Analytics analyst, Product analyst]
  locations: ["Austin, TX", "Dallas, TX", "Houston, TX"]
  disabled_sources: [wwr, remoteok]   # programming-focused boards
vetting:
  review_sites: [Glassdoor, Indeed]
  forums: [reddit dataanalysis]
```

Here `engineer` and `scientist` are in `exclude` because Alex doesn't want data-engineering roles. `analyst` is
already a default role word, so `data` + "Analyst" counts as relevant.

### 3. Germany-based frontend developer

Jonas lives in Berlin, would consider Hamburg or Munich, speaks English and German, and is fine with remote work
across Europe.

```yaml
languages: [English, German]
location:
  home_country: DE
  tier1: [Berlin]
  tier2: [Hamburg, Munich]
  remote_ok_scopes: [DE, EU, EMEA, WORLDWIDE]
  abroad: remote_only
salary:
  currency: EUR
  min_per_year: 55000
title_keywords:
  strong: [frontend, front end, front-end, react, vue, angular]
  related: [javascript, typescript, web, ui, full stack, fullstack]
  exclude: [intern, werkstudent, praktikum, sales]
search:
  queries:
    1: [Frontend Developer, React Developer]
    2: [Frontend Engineer, TypeScript Developer, Frontend Entwickler]
  locations: [Berlin, Hamburg, Munich]
vetting:
  review_sites: [Kununu, Glassdoor]
  forums: [reddit cscareerquestionsEU]
```

Listing `German` in `languages` matters. Without it, ads that say "fluent German required" are skipped.

### 4. Remote-only designer in the Philippines

Maria lives in Cebu and only wants remote work, from employers anywhere that hire in the Philippines or Asia.

```yaml
location:
  home_country: PH
  tier1: [Cebu]
  tier2: []
  remote_ok_scopes: [PH, APAC, ASIA, WORLDWIDE]
  abroad: remote_only
salary:
  currency: USD                  # remote roles usually state USD
  min_per_year: 18000
title_keywords:
  strong: [ux, ui, product designer, ux designer, ui designer, interaction designer]
  related: [design, figma, visual, web]
  exclude: [intern, graphic, interior, fashion, sales]
search:
  queries:
    1: [UX designer, Product designer]
    2: [UI designer, UX UI designer]
  locations: []                  # no city searches
  remote_locations: [Remote]
  disabled_sources: [wwr, remoteok]   # programming-focused feeds
```

There is no "remote only at home" switch. Onsite jobs in your own country are still kept. In the report, set
**Work type** to *Remote* (the filter is remembered). Also note that the "skill gaps" vocabulary in the keyword
score is tech-focused, so for design roles the agent's judgment matters more than H.

---

## Writing good search queries and title keywords

**Search queries** (`search.queries`) are sent to Indeed, Glassdoor, LinkedIn, Naukri, Himalayas, Remotive and
SerpApi, exactly like typing into their search box.

- Use **short phrases a job title would contain**: `Data analyst`, not `entry level data analyst jobs SQL Tableau`.
- Put your best 2–3 phrases in **tier 1** and close variations in **tier 2**. Keep broad or speculative phrases in
  **tier 3** and run them only now and then (`--tiers 1,2,3`).
- Each query × location pair costs one call on budget-limited sources. Six queries × four locations is 24 pairs,
  more than Indeed's default budget of 6, so some pairs wait for later runs. Fewer, better queries win.
- Use the language employers in your country use (`Frontend Entwickler`, `Développeur front-end`).
- Try `jobhunt search run --query "Product designer" --sources himalayas` to test one phrase on one source.

**Title keywords** (`title_keywords`) filter what comes back, especially from company boards that list every
opening.

- `strong`: words that alone mean "my field": `machine learning`, `data analyst`, `ux`.
- `related`: words that need a role word next to them: `python`, `data`, `web`. "Python Developer" passes;
  "Python Course Instructor" doesn't (in the report).
- `exclude`: be generous: `intern`, `sales`, `recruiter`, `teacher`, and roles near yours that you don't want.
- Short words match whole words only: `ai` won't match "Maintenance", and `ml` won't match "HTML".
- Avoid very generic `strong` words such as `engineer` or `manager`. They would let thousands of unrelated titles
  through from big company boards.

---

## resume.yaml, field by field

The schema lives in `src/jobhunt/resume.py`. Unknown fields are rejected.

### Top level

| Field | Default | What it is |
|---|---|---|
| `kind` | `resume` | Leave it. |
| `schema_version` | `1` | Leave it. |
| `name` | `""` | Your name (only used in your local files). |
| `headline` | `""` | E.g. `Backend & applied-AI engineer`. |
| `summary` | `""` | Two or three lines. Used if `candidate.summary` in preferences is empty. |
| `skills` | `[]` | Your skill inventory. Required in practice: scoring needs it. |
| `experience` | `[]` | Jobs. |
| `projects` | `[]` | Personal, academic or open-source projects. |
| `education` | `[]` | Degrees. |
| `certifications` | `[]` | Certifications. |

### `skills` (each item)

| Field | Default | What it is |
|---|---|---|
| `name` | **required** | Canonical name, e.g. `PostgreSQL`, `Figma`, `Kubernetes`. |
| `level` | `working` | `hands_on`: used it for real work, could start tomorrow. `working`: used it, would need some ramp-up. `exposure`: courses, tutorials, tinkering. |
| `aliases` | `[]` | Other spellings job ads use: `[Postgres, psql]`, `[k8s]`, `[JS, JavaScript]`. |
| `category` | `""` | Free text for your own grouping: `language`, `cloud`, `design`, … |

Skill names and aliases are how JobHunt spots your skills in job ads, so add the spellings you see in ads.

### Entries: `experience`, `projects`, `education`, `certifications`

| Field | Default | What it is |
|---|---|---|
| `id` | generated | Stable id, e.g. `EXP_ACME`. Generated from the organisation (or title) if left out: `EXP_…`, `PRJ_…`, `EDU_…`, `CERT_…`. |
| `title` | **required** | Role, project name, degree or certification. |
| `org` | `""` | Employer, school or issuer. |
| `start`, `end` | none | Free text: `2024-07`, `"2024"`, `present`. |
| `location` | `""` | Free text. |
| `bullets` | `[]` | What you did. See below. |

### Bullets (each item)

| Field | Default | What it is |
|---|---|---|
| `id` | generated | `<entry id>.B1`, `.B2`, … in order. |
| `text` | **required** (3+ characters) | One concrete thing you did. |
| `skills` | `[]` | Skills this bullet proves. Each must be a name or alias from `skills`. |
| `evidence` | `implemented` | `implemented`: you built it. `contributed`: a team effort you took part in. `explored`: a prototype, study or experiment. |

A bullet can also be a plain string (`- Built the billing API.`). It then gets default values and no skills.

---

## Writing honest resume facts

When your agent judges a job, it must point to **evidence** for every requirement it marks as met. Evidence is a
bullet id (`EXP_ACME.B2`), an entry id (`EDU_BTECH` for a degree requirement) or a skill (`SKILL:Python`). Code
then checks that evidence:

- A requirement marked met or partial with **no valid evidence** becomes **unmet**.
- A requirement marked met using **only `exposure`-level skills** becomes **partial**.

So the score can only be as good as the facts in `resume.yaml`. Some guidelines:

- **Only write what is true and what you could defend in an interview.** Inflated levels give inflated scores, and
  you end up applying to jobs you'll fail at.
- **Be honest with levels.** A weekend tutorial is `exposure`. Something you used daily at work is `hands_on`.
- **One achievement per bullet, with the skills it used.** "Built X with A and B, which did Y" beats "Responsible
  for various tasks".
- **Use `contributed`** when a team did it and you played a part. The agent sees this label.
- **Keep numbers real.** "Answers from 4,000 help articles" is fine if true.
- **Give explicit ids** if two entries would get the same generated id (two jobs at the same company, for
  example): `id: EXP_ACME_2022`. Ids should stay stable so old judgments still make sense.
- **Projects count.** Personal projects with honest `evidence: explored` are valid evidence for "familiarity with
  X" requirements.

The example in `profile.example/resume.yaml` shows all of this.

---

## Checking your profile

```bash
jobhunt profile validate      # schema + consistency checks; exit code 1 if anything is wrong
jobhunt profile show          # what search and scoring will use, with defaults resolved
jobhunt profile skills        # your skill inventory: name, level, aliases
jobhunt doctor                # environment + profile in one go
```

`profile show` is the quickest way to answer "why is it searching there?". It prints your home country, tiers,
the remote scopes in effect, queries, search locations, experience limits, salary floor, core skills, languages,
and how many skills, entries and bullets your resume has.

---

## Common validation errors and fixes

| Message | Cause | Fix |
|---|---|---|
| `preferences.yaml: … not found. Run jobhunt init …` | No profile yet | `jobhunt init` |
| `not valid YAML: …` | Tabs, bad indentation, an unquoted `:` | Indent with spaces; quote text containing `:` or `#` |
| `target_titles: List should have at least 1 item` | Empty `target_titles` | Add at least one title |
| `search: Field required` | No `search:` section | Add `search:` with `queries:` |
| `search.queries.tier1.[key]: Input should be a valid integer` | Tier keys must be numbers | Write `1:`, `2:`, `3:`, not `tier1:` |
| `search.queries needs at least tier 1 or 2` | Only tier 3 queries | Add tier 1 or 2 |
| `location.home_country: String should have at most 2 characters` | `India`, `IND`, `USA` | Use the ISO-2 code: `IN`, `US` |
| `note: location.home_country XX is not in the built-in country list` | Your country isn't in `geo.py` yet (a note, not an error) | Add your cities to `tier1`/`tier2`/`extra_cities`, and consider [adding the country](adding-a-source.md#adding-a-country) |
| `location.abroad: Input should be 'skip', 'remote_only' or 'relocation'` | Typo | Use one of the three values |
| `salary.below_min_action: Input should be 'review' or 'skip'` | Typo | Use one of the two values |
| `title_keyword: Extra inputs are not permitted` | Misspelt field name (here, missing `s`) | Fix the name; compare with `profile.example/` |
| `search.budgets.jobspy_indeed: Input should be a valid integer` | Call budgets are whole numbers (only `wwr_feeds` and `yc_jobs_paths` take lists) | Use a number |
| `title_keywords.strong (or related) is empty` | No relevance keywords | Add some, see [above](#title_keywords) |
| `core_skills not in resume.yaml skills: X` | A core skill missing from your inventory | Add `X` to `resume.yaml` → `skills`, or remove it from `core_skills` |
| `skills.0.level: Input should be 'hands_on', 'working' or 'exposure'` | E.g. `level: expert` | Use one of the three levels |
| `…bullets.0.text: String should have at least 3 characters` | Empty or tiny bullet | Write the bullet or remove it |
| `bullet EXP_X.B2 lists skill 'Y' that is not in skills` | Bullet skill not in inventory | Add `Y` (or an alias) to `skills` |
| `duplicate ids [...]` | Two entries produce the same id | Give one an explicit `id:` |
| `skills listed twice: [...]` | Same skill name twice | Merge them; use `aliases` for other spellings |
| `skills is empty` / `no experience/project bullets` | Nothing to score against | Fill them in |

---

## Tuning after your first run

Run a search, build the report, then adjust. Small changes, one at a time, work best.

**Too many irrelevant jobs?**

- Add the unwanted words to `title_keywords.exclude` (`support`, `qa`, `sales`, …).
- Move broad words from `strong` to `related`, so they need a role word.
- Remove broad queries or move them to tier 3.
- Lower `experience.max_required_years`, or add words to `skip_title_words`.
- Remove remote scopes you can't really work in (for example `WORLDWIDE` if most of those jobs need US hours).
- Add companies you never want to `exclusions.companies`.
- In the report, raise the **Match** filter and tick **Hide** on noise.

**Too few jobs?**

- Run `jobhunt search health`. A source with `n=0` or an error may be blocked, out of budget or unsupported for your
  country. See [troubleshooting.md](troubleshooting.md).
- Add related keywords and synonyms (`frontend` *and* `front end` *and* `front-end`).
- Add queries, or run tier 3: `jobhunt search run --tiers 1,2,3`.
- Add cities to `search.locations`, and to `tier2` or `extra_cities` so they count as home.
- Add remote scopes (`WORLDWIDE`, your region).
- Raise `search.posted_within_days` (search sources) and `report.posted_within_days` (what the report shows).
- Raise `search.results_per_call` (jobs per search call, and per company board).
- Grow your company watchlist: `jobhunt companies import-seeds`, `companies probe`, `companies discover`. See
  [sources.md](sources.md#the-company-watchlist).
- Check `jobhunt companies list`. If it is empty, company-board sources have nothing to poll.

**Scores feel off?**

- Check that `resume.yaml` uses the spellings job ads use (add `aliases`).
- Check `candidate.years_experience` and `core_skills`.
- Remember that without an agent the **Match** column is only the keyword estimate H. Scores marked **AI-judged** are judged by your agent.
  See [scoring.md](scoring.md).

After editing the profile, re-score existing jobs with `jobhunt match run --rescore` and rebuild the report.
