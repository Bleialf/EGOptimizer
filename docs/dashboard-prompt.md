# Gemini prompt — build the EGOptimizer Home Assistant dashboard

Copy everything in the fenced block below into Gemini. It is self-contained:
it describes the integration, every entity, the exact attribute shapes, and the
views to build. Paste the YAML Gemini returns into a new Home Assistant
dashboard (Settings → Dashboards → Add dashboard → Edit → Raw configuration editor).

---

```text
You are an expert Home Assistant Lovelace dashboard author. Build me a complete,
production-quality dashboard (YAML, "raw configuration" format with a top-level
`views:` list) for my custom integration "EGOptimizer".

WHAT EGOPTIMIZER DOES
EGOptimizer decides how much solar energy to feed from my home battery into my
Austrian energy community (EG) each night, without ever risking my own autarky.
A separate "brain" service does the math; this Home Assistant integration shows
its live recommendation and lets me control it. It uses a censored-aware
contextual bandit: it learns per (season|weekday-type|hour) how much the EG
absorbs, and PROBES ("explores") above the known uptake where the ceiling is
still unknown, to learn more. A forward battery simulation finds the overnight
"trough" (lowest SoC) and only feeds energy that's safe above my morning target.

DESIGN REQUIREMENTS
- Dark-theme friendly, looks good on both desktop and mobile (use responsive
  layouts: `grid`, `horizontal-stack`, `vertical-stack`).
- Prefer built-in cards. You MAY use these popular HACS cards if helpful, but
  keep a built-in fallback in a comment: `custom:apexcharts-card`,
  `custom:mushroom-*`. If you use a custom card, add a one-line comment noting it
  must be installed via HACS.
- Use clear section headings and icons. Group related info. No external images.
- IMPORTANT sign convention: `sensor.egoptimizer_feed_setpoint` is in WATTS and
  is NEGATIVE when exporting to the grid (e.g. -800 W = feeding 0.8 kW to the EG).
  When charting/series, show magnitude with a clear "export" label.
- Times in attributes are ISO strings (local, Europe/Vienna). SoC is %.
- Do NOT try to build a file-upload control — uploads and configuration live in
  the integration's own screen. Instead add a Markdown card that links there:
  "Settings → Devices & Services → EGOptimizer → Configure" (upload data /
  settings / delete old data), and reference the train/refresh buttons below.

ENTITIES (use these exact entity_ids; if your HA prefixed some with the device
/area name like `technikraum_`, keep both spellings in a comment so I can fix):

Headline & status (domain sensor):
- sensor.egoptimizer_feed_setpoint   — W, negative = export NOW. Carries all the
  rich attributes (see ATTRIBUTES below): explore, mode, context_observations,
  next_feed_time, feed_plan, soc_forecast, debug.
- sensor.egoptimizer_status          — text: feeding | holding | no_budget
- sensor.egoptimizer_confidence      — text: probing | confident | locked | no_model
- sensor.egoptimizer_reasoning       — plain-language sentence explaining the decision
- sensor.egoptimizer_current_plan    — text plan state; attrs: plan_preview,
  next_feed_time, planned_tonight_kwh, feed_plan, decision

Energy & forecast (domain sensor):
- sensor.egoptimizer_eg_budget_tonight   — kWh safe to feed tonight
- sensor.egoptimizer_planned_tonight     — kWh actually planned
- sensor.egoptimizer_forecast_trough_soc — % lowest projected SoC
- sensor.egoptimizer_trough_time         — ISO time of that low point
- sensor.egoptimizer_pv_takeover_time    — ISO time PV starts covering the house
- sensor.egoptimizer_load_now            — kW, smoothed current house load
- sensor.egoptimizer_base_load           — kW, estimated sustained/overnight load

Controls:
- number.egoptimizer_target_morning_soc        — %, the autarky floor to protect
- number.egoptimizer_exploration_aggressiveness — 0..1, how hard it probes
- select.egoptimizer_learning_mode             — explore | locked
- button.egoptimizer_refresh_recommendation    — recompute now
- button.egoptimizer_train_model               — retrain from data
- update.egoptimizer_update                     — integration update available?

ATTRIBUTES on sensor.egoptimizer_feed_setpoint:
- explore (bool), mode (str), context_observations (int), next_feed_time (ISO|null)
- feed_plan: list of hourly objects, each:
    { "time": ISO, "hour": int, "feed_kwh": float, "capacity_kwh": float, "explore": bool }
  (feed_kwh = planned feed that hour; capacity_kwh = learned EG ceiling;
   explore=true means this hour is a probe above known uptake.)
- soc_forecast: list of { "t": ISO, "soc_pct": float, "pv_kw": float }
- debug: {
    decision: { path, status, confidence, note },
    inputs:   { soc_pct, capacity_kwh, load_kw, load_now_kw, target_morning_soc_pct,
                hard_min_soc_pct, mode, exploration_aggressiveness, plan_until },
    model:    { loaded, bucket_count, has_current_bucket },
    autarky:  { eg_budget_kwh, trough_soc_pct, trough_time, pv_takeover_time, reserve_note },
    context:  { bucket, observations, max_absorbed_kwh, mean_absorbed_kwh,
                best_was_censored, recommended_capacity_kwh }
  }

VIEWS TO BUILD (one dashboard, these tabs):

1) "Now" — at-a-glance:
   - Big gauge or prominent number for the current feed setpoint (show as kW
     export magnitude, e.g. compute {{ (states('sensor.egoptimizer_feed_setpoint')|float(0)/-1000)|round(2) }} kW).
   - Status + Confidence as colored chips (feeding=green, holding=amber,
     no_budget=grey; probing=blue, confident=green, locked=purple).
   - The reasoning sentence (Markdown).
   - Quick tiles: EG budget tonight, planned tonight, trough SoC + trough time,
     PV takeover time, load now, base load.
   - Controls row: target morning SoC (number), exploration aggressiveness
     (number slider), learning mode (select), refresh + train buttons.

2) "Tonight's plan & exploration":
   - An ApexCharts column chart of feed_plan: x = hour (from each item's "time"),
     y = feed_kwh. Use a data_generator over
     `entity.attributes.feed_plan`. Color bars where explore==true differently
     (e.g. orange "probe") vs normal feed (green) — if ApexCharts can't color per
     point easily, render TWO series: one for explore hours, one for non-explore.
   - A second series/line for capacity_kwh (the learned ceiling per hour).
   - A Markdown card that lists, from feed_plan, which hours are PROBES
     (explore=true) vs exploiting known uptake — this is "what we're going to
     explore tonight". Also surface debug.context (current bucket, observations,
     best_was_censored, recommended_capacity_kwh) so I can see why a context is
     being probed or not.

3) "Battery forecast":
   - ApexCharts line chart from soc_forecast: x = t, y = soc_pct (left axis), plus
     pv_kw as a second series (right axis / area). Add a horizontal reference line
     at the target morning SoC (number.egoptimizer_target_morning_soc) and mark
     the trough. Title: "Projected SoC until PV takeover".

4) "Diagnostics":
   - Markdown/entities cards dumping debug.inputs, debug.autarky, debug.model and
     debug.context in a readable table (use templates to pull from
     state_attr('sensor.egoptimizer_feed_setpoint','debug')).
   - Show load_now vs base_load side by side (this is the key tuning signal).
   - History graph (built-in) for feed_setpoint, eg_budget_tonight,
     forecast_trough_soc over 48h.

5) "Data & settings":
   - Markdown card explaining that data upload, connection/battery settings, data
     retention and deletion all live in: Settings → Devices & Services →
     EGOptimizer → Configure. Include the train + refresh buttons and the update
     entity here too.

APEXCHARTS-CARD — STRICT SCHEMA (these caused "Configuration error" before):
- There is NO top-level `xaxis:` key. Format the time axis via
  `apex_config: { xaxis: { labels: { format: HH:mm } } }`.
- `yaxis:` items support ONLY id/min/max/decimals/opposite/apex_config — NOT
  `title`. Omit titles or put them in `apex_config`.
- `header:` supports show/title/show_states only — NO `abilities`.
- Series support type (line/area/column), color, stroke_width, opacity,
  yaxis_id, group_by, data_generator — NOT `stroke_dash_array`, NOT `type:
  scatter`. For a flat reference line (e.g. target SoC) emit a normal `line`
  series whose data_generator returns the constant for every x.
- FORECAST data is in the FUTURE, so the default (now-graph_span … now) window
  hides it. Use `graph_span: 24h` + `span: { start: hour }` to show now → +24h.

JINJA — guard every number, the model returns nulls on the no_budget path:
- Always `{{ (x | float(0)) | round(2) }}`, never `{{ x | round(2) }}`
  (debug.context.* and capacity fields are null when status == no_budget).

OUTPUT
- Return ONLY the complete dashboard YAML, ready to paste into the raw config
  editor (top-level `views:`). Add brief `#` comments above any custom: card
  noting the HACS dependency. Use template sensors/markdown where needed to
  reshape attributes. Make ApexCharts `data_generator` blocks correct JS that map
  the attribute arrays to [x,y] pairs (parse ISO time with new Date(...).getTime()).
```

A corrected, working reference dashboard is committed at `docs/dashboard.yaml`.

---

## Notes for you (not part of the prompt)

- **Entity-id prefixes:** some of your entities came out as
  `sensor.technikraum_egoptimizer_current_plan` and
  `button.technikraum_egoptimizer_*` (HA prefixed them with the device's area).
  After Gemini gives you the YAML, fix any entity_id that doesn't resolve — the
  friendly names all start with "EGOptimizer …" so they're easy to find.
- **Upload/settings are not dashboard cards** — they live in the integration's
  **Configure** dialog (config/options flow). The dashboard links there.
