# Plan: end-to-end Source-to-Pay flow with bots

One guided flow inside the Sourcing window. The steps run in real-world order, and each step unlocks the next.
**Bots write and judge** (Sarvam AI); **code does the maths and the checks**, so numbers are always right. A free **Simulation** mode runs the same flow without Sarvam.

| # | Step | You | Bots | Built? |
|---|---|---|---|---|
| 1 | **Parts** | Choose which parts to re-source and what you want (cheaper / greener / faster / safer) | – | Board exists. Add the "goal per part" |
| 2 | **Suppliers** | Tick which qualified suppliers to invite. "Find new suppliers" adds options | – | Mostly exists (criteria, scouting) |
| 3 | **RFQ** | Press **Send RFQs** | **Buyer bot** writes each RFQ (spec, volume, delivery plant, start date, terms, due date). **Supplier bots** write quotes (price, lead time, capacity per week, ESG, validity, conditions) | New |
| 4 | **Evaluate** | Accept or change the shortlist | **Evaluator bot** ranks the quotes on total cost, ESG, risk and lead time, and on calendar impact (start date, capacity gaps, overlap with the ending contract), with reasons | New. The **calendar** is built here |
| 5 | **Negotiate** | Set the budget per part (sliders) | Buyer bots vs the shortlisted supplier bots (what you tested) | Exists |
| 6 | **Award** | Pick the winners in the table and see the impact | – | Exists |
| 7 | **PO → Pay** | Watch, and resolve failures | **Procurement bot** raises the PO. **Supplier bot** confirms and ships. **Warehouse bot** books the goods receipt. **Supplier bot** sends the invoice. **AP bot** runs the 3-way match and pays | New |
| 8 | **Performance** | View | Scores update from what happened | New (simple) |

## Step 4: calendar
A 60-day grid: the past 30 days (actual) and the next 30 days (forecast).
- Each day shows cars built and sold, and revenue.
- Events on the days they happen:
  - contract ending;
  - new supplier start (ramp-up);
  - short delivery (−x units → −y cars → −€z);
  - late delivery;
  - quality hold;
  - ship sailing.
- On the evaluate step, a toggle shows the forecast **with each shortlisted quote**. For example, "Hanseo starts in 18 days → 3 days without batteries → −420 cars → −€1.2M, unless Voltacell is extended."

## Step 7: PO → payment and 3-way match
- The flow is: PO created, supplier confirms, delivery, goods receipt, invoice, 3-way match, then payment.
- About 1 in 4 runs fails at the match, to stay realistic. The code finds the mismatch, and the **AP bot explains why** in plain words and suggests a fix:
  - **Quantity:** received fewer than invoiced (short delivery);
  - **Price:** invoice price ≠ PO price (old price list);
  - **Quality:** goods rejected at receipt (damaged / failed inspection);
  - **Duplicate:** invoice already paid;
  - **Missing receipt:** invoice arrived before the goods.
- Fixes: ask for a credit note, pay only what was received, hold the payment, or reject the invoice. You click one, and the bot finishes the step.
- The bottom-left **Source-to-Pay tracker** follows the same steps.

## Step 8: supplier performance (simple)
One card per supplier, built from what actually happened, plus a trend arrow:
- on-time delivery %;
- quantity accuracy;
- quality pass %;
- invoice accuracy;
- relationship score.

These scores feed into the next sourcing round: they affect evaluation, and how hard the supplier bots bargain.

## Build phases
- **A (done):** the flow stepper (steps 1–8 in one bar), plus step 3 RFQ bots and quotes.
- **B (done):** step 4 evaluator bot plus the calendar.
- **C (done):** step 7 PO → payment bots, with match failures and explanations.
- **D (done):** step 8 performance, linked back into evaluation and negotiation.

Every bot follows the same rules as the negotiators: private data stays private, every reply is checked in code, every run is saved in SQLite, and each bot gets an eval check (for example "the RFQ includes all required fields", "the explanation names the real mismatch").

**Cost:** a full run with 3 parts is about ₹2–3 of Sarvam credit. Simulation mode is free.
