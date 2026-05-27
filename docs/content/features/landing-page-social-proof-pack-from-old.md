# TuxSEO Landing-Page Social Proof Pack (ported from TuxSEO Old)

Owner: Scribe  
Date: 2026-03-11  
Source intent: Rebuild a conversion-ready social-proof layer using verified claims only.

## 1) Social proof snippet library (with confidence variants)

Use the **Strong proof** variant only when evidence is confirmed and permission is cleared.  
Use **Pending validation** variant while evidence is being collected.

---

### SP1 — Hero trust strip: active usage signal
**Placement:** Directly below hero CTA  
**Strong proof copy:**
- "Used by **{{active_projects_count}} active projects** to ship SEO content consistently."

**Pending validation copy:**
- "Used by early SaaS teams building a repeatable SEO workflow."

**Evidence required:**
- Product DB count definition + query snapshot for `active_projects_count` (last 30 days)
- Date stamp for count freshness

---

### SP2 — Hero trust strip: speed-to-draft claim
**Placement:** Hero trust strip (slot 2)

**Strong proof copy:**
- "From keyword brief to publish-ready draft in **{{median_minutes}} minutes** (median)."

**Pending validation copy:**
- "Go from keyword brief to a strong first draft in one focused session."

**Evidence required:**
- Event timestamps: brief created → draft generated
- Median calculation method and sample window

---

### SP3 — Mid-page testimonial: quality of first draft
**Placement:** Testimonial block after "How it works"

**Strong proof copy:**
- "\"TuxSEO gave us drafts worth editing, not rewriting. We publish more consistently now.\" — {{name}}, {{role}} at {{company}}"

**Pending validation copy:**
- "\"The draft quality surprised us — we started from structure, not a blank page.\" — Customer quote pending approval"

**Evidence required:**
- Written customer quote approval
- Name/title/company attribution consent
- Optional source link (case note or customer message)

---

### SP4 — Mini case card: consistency gain
**Placement:** Mini case studies section before pricing

**Strong proof copy:**
- "**Before:** {{before_posts_per_month}} post/month  
  **After:** {{after_posts_per_month}} posts/month in {{time_window}}  
  **Why:** TuxSEO standardized brief → draft → review."

**Pending validation copy:**
- "Teams report improved publishing consistency after adopting a repeatable brief-to-draft workflow."

**Evidence required:**
- Publication logs (CMS timestamps)
- Baseline vs after period definition
- Short method note (what changed operationally)

---

### SP5 — Mini case card: ranking traction
**Placement:** Mini case studies section before pricing

**Strong proof copy:**
- "Tracked pages reached **{{top_10_keywords_count}} top-10 rankings** across {{time_window}} after consistent publishing."

**Pending validation copy:**
- "Customers use TuxSEO to execute keyword-focused publishing that supports ranking traction over time."

**Evidence required:**
- Search Console / SEO tool export (query + page)
- Date range and inclusion criteria
- Confirmation that gains are tied to pages created via TuxSEO workflow

---

### SP6 — Pricing vicinity reassurance: keeps editorial control
**Placement:** Immediately above pricing table or near pricing CTA

**Strong proof copy:**
- "Your team keeps final edit and publish control — TuxSEO handles the heavy first-draft lift."

**Pending validation copy:**
- "Built for teams that want AI speed without giving up editorial control."

**Evidence required:**
- Product flow screenshot(s): review/edit/publish steps
- Confirmation in product docs that publishing requires user action/approval

---

### SP7 — CTA vicinity friction reducer: WordPress workflow proof
**Placement:** Final CTA vicinity (just above or below CTA)

**Strong proof copy:**
- "Connect WordPress, generate a draft, review, and publish from your existing workflow."

**Pending validation copy:**
- "WordPress publishing workflow available (draft-first flow)."

**Evidence required:**
- Integration setup doc + product screenshots
- QA verification that flow works on current release

---

### SP8 — FAQ trust answer: objection handling on generic AI output
**Placement:** FAQ section above final CTA

**Strong proof copy (FAQ answer excerpt):**
- "No — output is grounded in your project context, target keywords, and content brief. Teams use TuxSEO as an expert first draft they can refine quickly."

**Pending validation copy:**
- "We design outputs around your brief and workflow so editing focuses on quality, not starting from zero."

**Evidence required:**
- Prompt/context architecture description
- Before/after editing examples from real usage

---

## 2) Evidence matrix (what exists vs what must be collected)

| Proof ID | Claim type | Current confidence | Evidence status | Source requirement | Owner | Publish gate |
|---|---|---:|---|---|---|---|
| SP1 | Active projects count | Pending | Needs refreshable metric query | Analytics/DB query + definition of "active" + timestamp | Product/Ops | Must verify before numeric publish |
| SP2 | Speed-to-draft median | Pending | Not yet calculated | Event logs + median method + sample size | Product/Eng | Must verify before numeric publish |
| SP3 | Testimonial quote | Pending | Quote exists conceptually, no formal approval attached | Written permission + attribution approval | CS/Founder | Attribution required |
| SP4 | Publishing consistency improvement | Pending | Needs case-level baseline/after data | CMS export + period comparison | CS/Content | Numeric only after validation |
| SP5 | Ranking traction | Pending | Needs SEO export and attribution notes | GSC/export + page mapping | SEO/Ops | Numeric only after validation |
| SP6 | Editorial control claim | Strong | Product workflow supports this | UI screenshots + docs | Product | Safe to publish now |
| SP7 | WordPress workflow claim | Strong (if current integration passes QA) | Setup guide exists; QA check needed for current release | Setup doc + current QA run | Product/Eng | Publish after quick QA pass |
| SP8 | Non-generic output claim | Pending | Conceptual evidence only | Prompt/context architecture + editing examples | Product/Content | Avoid over-claiming until examples are documented |

## 3) Final on-page placement recommendation

### A. Hero (high visibility, low cognitive load)
1. **Primary headline + CTA**
2. **Trust strip (SP1 + SP2)**
   - If metrics not validated, use pending variants.

### B. Mid-page credibility layer
3. **How it works**
4. **Testimonial block (SP3)**
   - Keep to 1–2 short quotes max; include attribution when approved.

### C. Conversion support before pricing decision
5. **Mini case cards (SP4 + SP5)**
   - Show one consistency case and one ranking traction case.
6. **Pricing section with reassurance line (SP6)**
   - Place directly near price to reduce "AI takeover" anxiety.

### D. Final conversion zone
7. **Final CTA with workflow proof (SP7)**
8. **FAQ objection answer (SP8)**
   - Place FAQ directly above final CTA to resolve last-mile hesitation.

## 4) Implementation notes for handoff

- Add every claim to a `proof_registry` doc with:
  - snippet ID
  - live copy in production
  - evidence URL/file
  - last verified date
  - approval owner
- Never publish unverified numbers. Use non-numeric pending variants until evidence is locked.
- For testimonials/logos, require explicit permission records.

## 5) Ready-to-implement deliverables checklist

- [x] 8 social proof snippets provided (>=6 required)
- [x] Each snippet has strong + pending variants
- [x] Each snippet mapped to evidence/source requirements
- [x] Evidence matrix provided (exists vs must collect)
- [x] Final placement plan provided (hero, pricing, CTA vicinity, FAQ)
- [x] Copy is implementation-ready with placeholders where validation is pending
