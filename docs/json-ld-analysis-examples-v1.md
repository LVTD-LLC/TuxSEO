# JSON-LD Analysis Examples (v1)

These payloads are examples for UI consumption.

## 1) Missing (suggested starter available)

```json
{
  "state": "missing",
  "status_label": "Missing (suggested starter available)",
  "html_input_available": true,
  "is_scorable": true,
  "detected_script_blocks": 0,
  "valid_items": 0,
  "total_items": 0,
  "parse_errors": [],
  "items": [],
  "starter_suggestion": {
    "template_type": "WebPage",
    "json_ld": {
      "@context": "https://schema.org",
      "@type": "WebPage",
      "name": "Pricing",
      "description": "Replace with page description",
      "url": "https://example.com/pricing"
    }
  }
}
```

## 2) Detected but issues

```json
{
  "state": "issues",
  "status_label": "Detected but issues",
  "html_input_available": true,
  "is_scorable": true,
  "detected_script_blocks": 1,
  "valid_items": 0,
  "total_items": 1,
  "parse_errors": [],
  "items": [
    {
      "block_index": 1,
      "item_index": 1,
      "type": "Article",
      "context": "https://not-schema.example",
      "issues": [
        "@context should usually reference schema.org",
        "Missing required field for Article: author",
        "Missing required field for Article: datePublished"
      ],
      "is_valid": false
    }
  ]
}
```

## 3) Detected & looks okay

```json
{
  "state": "ok",
  "status_label": "Detected & looks okay",
  "html_input_available": true,
  "is_scorable": true,
  "detected_script_blocks": 1,
  "valid_items": 1,
  "total_items": 1,
  "parse_errors": [],
  "items": [
    {
      "block_index": 1,
      "item_index": 1,
      "type": "WebPage",
      "context": "https://schema.org",
      "issues": [],
      "is_valid": true
    }
  ],
  "starter_suggestion": null
}
```

## 4) HTML unavailable (guidance shown, score not impacted)

```json
{
  "state": "missing",
  "status_label": "Missing (suggested starter available)",
  "html_input_available": false,
  "is_scorable": false,
  "detected_script_blocks": 0,
  "notes": [
    "HTML input not available, so JSON-LD did not affect SEO score."
  ]
}
```

> v1 note: this is guidance-only validation (baseline checks), not strict schema compliance certification.
