# Context

## What This Repo Is

`clubhouse-autopilot` began as a cafe ops pipeline and is moving toward a
business-specific operating intelligence system.

The system combines:

- observed business data
  - Square: sales, timing, item mix, workload
  - Deputy: roster and labor detail
  - Xero: bills, costs, financial truth, payroll truth where available
- declared operating knowledge
  - recipes
  - ordering schedules
  - delivery days
  - staffing constraints
  - workflow rules
  - storage rules

The product direction is:

`observe -> explain -> recommend -> learn -> retain`

## Core Product Idea

Autopilot should help operators answer:

- what should I roster 2-4 weeks ahead?
- what stock should I order and when?
- what items should I raise, bundle, or remove?
- what operational bottlenecks are hurting margin?
- what changed after I acted?

Tomorrow planning still exists, but it is tactical. The strategic value is in
retained business memory and profitability guidance.

## Key Architectural Surfaces

### 1. Pipeline Spine

Main flow:

- ingest Square data
- sync Deputy rosters
- sync Xero costs and financial facts
- compute daily profitability
- generate predictions
- generate intelligence outputs

### 2. Knowledge Layer

Structured operating knowledge is stored as rules and recipes.

Examples:

- `delivery_schedule`
- `ordering_schedule`
- `recipe_definition`
- `staffing_constraint`
- `storage_rule`
- emerging parser types such as `purchase_profile` and `workflow_rule`

Chat is the input interface.
Structured storage is the source of truth.

### 3. Chat Layer

Chat has three jobs:

1. grounded answering from current repo state and stored data
2. capturing structured business rules
3. asking targeted curiosity questions when key business logic is missing

### 4. Curiosity / Explanation Layer

The system should identify:

- missing product recipes
- missing delivery or ordering schedules
- unclear stock consumption drivers
- unexplained Xero purchases
- unexplained labor/wage spikes

Then it should ask one high-value question that improves future
recommendations.

## Important Principles

### Groundedness over fluency

The assistant must not invent business logic or act as though a rule exists if
it is not stored and retrievable.

### Determinism where possible

Structured parsing and rule storage should be deterministic.

### Reliability before feature expansion

If rule persistence, data freshness, or chat grounding are unreliable, fix those
before building broader intelligence features.

### Small, verifiable tasks

ACP agents should work from one narrow task at a time with explicit
verification.
