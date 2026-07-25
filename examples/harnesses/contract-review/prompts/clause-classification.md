---
id: clause-classification
version: 2.2.0
role: user
models: [claude-opus-5, claude-sonnet-5]
token_budget: 3000
temperature: 0
variables:
  - name: clause_text
    type: string
    required: true
  - name: clause_anchor
    type: string
    required: true
  - name: taxonomy
    type: array
    required: true
    description: "Clause types from the ACME clause dictionary v9."
evaluated_by: evaluations/suites/golden.yaml#clause-classification
---

Classify the following contract clause.

<clause anchor="{{ clause_anchor }}">
{{ clause_text }}
</clause>

<allowed_types>
{% for t in taxonomy %}
- `{{ t.id }}` — {{ t.definition }}
{% endfor %}
</allowed_types>

Return JSON only:

```json
{
  "anchor": "{{ clause_anchor }}",
  "type": "<one id from allowed_types, or UNMATCHED>",
  "confidence": 0.0,
  "secondary_types": [],
  "rationale": "<= 40 words, referencing the operative words of the clause"
}
```

Rules:

- Choose `UNMATCHED` when no allowed type fits. A wrong type is more harmful than
  an honest `UNMATCHED`, because downstream retrieval will fetch the wrong
  playbook position.
- A clause may carry secondary types (for example a limitation of liability
  clause that also contains an indemnity carve-out). List them; do not merge them.
- `confidence` below 0.6 routes the clause to human review; calibrate honestly.
- Base the rationale on operative words present in the clause, not on the heading.
  Headings in third-party paper are frequently misleading.
