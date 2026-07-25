---
id: finding-generation
version: 2.3.1
role: user
models: [claude-opus-5, claude-sonnet-5]
token_budget: 6000
temperature: 0.1
variables:
  - name: clause
    type: object
    required: true
  - name: playbook_positions
    type: array
    required: true
  - name: precedents
    type: array
    required: false
  - name: severity_profile
    type: string
    default: balanced
evaluated_by: evaluations/suites/golden.yaml#finding-generation
---

Assess one clause against the retrieved playbook positions.

<clause type="{{ clause.type }}" anchor="{{ clause.anchor }}">
{{ clause.text }}
</clause>

<playbook_positions>
{% for p in playbook_positions %}
### {{ p.id }} — {{ p.title }} (relevance {{ "%.2f"|format(p.score) }})
Preferred: {{ p.preferred }}
Acceptable fallback: {{ p.fallback | default("none") }}
Unacceptable: {{ p.unacceptable | default("not specified") }}
{% endfor %}
</playbook_positions>

{% if precedents %}
<precedents>
{% for pr in precedents %}
- {{ pr.id }} ({{ pr.matter }}): {{ pr.language }}
{% endfor %}
</precedents>
{% endif %}

Severity profile: **{{ severity_profile }}**
{% if severity_profile == "conservative" %}
Under this profile, promote any divergence affecting liability, indemnity, data
protection or termination from `medium` to `high`.
{% endif %}

Return JSON only:

```json
{
  "anchor": "{{ clause.anchor }}",
  "position_id": "<playbook position id, or UNMATCHED>",
  "severity": "high|medium|low|review-required|info",
  "divergence": "<what differs from the playbook, <= 60 words>",
  "evidence": "<verbatim quotation from the clause, <= 50 words>",
  "suggested_wording": "<fallback language, or null if the clause conforms>",
  "citations": ["<anchor>", "<playbook position id>"]
}
```

Constraints:

- `evidence` must appear verbatim in the clause above. A downstream grounding
  check verifies this by exact string match; a mismatch discards the finding.
- Every id in `citations` must appear in this prompt. Do not cite anything else.
- If the clause conforms to the preferred position, emit `severity: info` with
  `suggested_wording: null`. Conforming clauses are recorded for coverage.
- Do not recommend actions, negotiation strategy or commercial trade-offs.
