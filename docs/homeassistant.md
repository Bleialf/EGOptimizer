# Home Assistant integration

The brain is a service HA **calls**. HA gathers the current state (Victron SoC,
PV, load + Solcast forecast), POSTs it to `/recommend`, and stores the answer
in helpers you can visualize and (Phase 4) act on.

> Replace the example entity ids below with your real ones. Find them in
> **Developer Tools -> States** (filter for `solcast`, `battery_soc`, `pv`...).
> min SoC is **not** sent for enforcement -- Victron handles that. We pass it
> only so the brain's math doesn't count energy below your floor.

## 1. Helpers to hold the recommendation

Create these (Settings -> Devices & Services -> Helpers), or via YAML:

```yaml
input_number:
  # YOUR control knob -- slide it to set how full the battery must stay by morning.
  egopt_target_morning_soc: { name: EGOpt target morning SoC, min: 10, max: 100, step: 5, unit_of_measurement: "%" }
  egopt_feed_kw:            { name: EGOpt feed setpoint, min: 0, max: 20, step: 0.01, unit_of_measurement: kW }
  egopt_eg_budget_kwh:      { name: EGOpt EG budget, min: 0, max: 100, step: 0.01, unit_of_measurement: kWh }
  egopt_projected_morning:  { name: EGOpt projected morning SoC, min: 0, max: 100, step: 0.1, unit_of_measurement: "%" }
input_text:
  egopt_rationale:          { name: EGOpt rationale, max: 255 }
input_boolean:
  egopt_explore:            { name: EGOpt exploring }
```

## 2. The REST call

```yaml
# configuration.yaml
rest_command:
  egopt_recommend:
    url: "http://<BRAIN_HOST>:8787/recommend"
    method: POST
    content_type: "application/json"
    payload: >
      {
        "soc_pct": {{ states('sensor.victron_battery_soc') | float(0) }},
        "capacity_kwh": 15.0,
        "target_morning_soc_pct": {{ states('input_number.egopt_target_morning_soc') | float(50) }},
        "hard_min_soc_pct": {{ states('number.victron_minimum_soc') | float(10) }},
        "load_now_kw": {{ states('sensor.victron_ac_consumption_smoothed') | float(0) / 1000 }},
        "pv_forecast": {{ (state_attr('sensor.solcast_pv_forecast_forecast_today','detailedHourly') or []) | to_json }}
      }
```

The brain reads Solcast's native `detailedHourly` slots directly (it uses the
pessimistic `pv_estimate10` from each and sums only the slots before your
`morning_hour`), so the template just passes the attribute through.

Two inputs matter here:

* **`target_morning_soc_pct`** comes from your slider -- "keep me at >= X% by
  morning." (`hard_min_soc_pct` is just Victron's floor, for the math.)
* **`load_now_kw`** is your *live* house draw. Because this automation re-runs
  every 15 min, the plan adapts as consumption changes. Point it at a
  **smoothed** sensor (e.g. a `statistics` 15-min mean) so a kettle switching on
  doesn't jerk the setpoint. If you can't build the hourly `pv_forecast` list,
  just send `"pv_until_morning_kwh": 0` (overnight PV is usually negligible).

> The `pv_forecast` template above passes Solcast's hourly slots (with the P10
> `pv_estimate10`); the brain sums only the slots before your `morning_hour`. If
> your Solcast version names the attribute differently (`detailedForecast`),
> adjust accordingly.

## 3. Automation: call + store the answer

```yaml
automation:
  - alias: EGOptimizer - recompute feed-in
    trigger:
      - trigger: time_pattern        # every 15 min, matching the meter interval
        minutes: "/15"
      - trigger: event               # let a dashboard button force a recompute
        event_type: egopt_recompute
    action:
      - action: rest_command.egopt_recommend
        response_variable: rec
      - if: "{{ rec['status'] == 200 }}"
        then:
          - action: input_number.set_value
            target: { entity_id: input_number.egopt_feed_kw }
            data: { value: "{{ rec['content']['feed_kw'] }}" }
          - action: input_number.set_value
            target: { entity_id: input_number.egopt_eg_budget_kwh }
            data: { value: "{{ rec['content']['eg_budget_kwh'] }}" }
          - action: input_number.set_value
            target: { entity_id: input_number.egopt_reserve_kwh }
            data: { value: "{{ rec['content']['reserve_kwh'] }}" }
          - action: input_text.set_value
            target: { entity_id: input_text.egopt_rationale }
            data: { value: "{{ rec['content']['rationale'][:255] }}" }
          - action: "input_boolean.turn_{{ 'on' if rec['content']['explore'] else 'off' }}"
            target: { entity_id: input_boolean.egopt_explore }
```

## 4. Visualization dashboard

```yaml
# A view with gauge + reasoning + history.
type: vertical-stack
cards:
  - type: gauge
    name: Feed into EG now
    entity: input_number.egopt_feed_kw
    unit: kW
    min: 0
    max: 5
    severity: { green: 0, yellow: 0.01, red: 4 }
  - type: glance
    entities:
      - entity: input_number.egopt_eg_budget_kwh
        name: EG budget tonight
      - entity: input_number.egopt_reserve_kwh
        name: Reserved for house
      - entity: sensor.victron_battery_soc
        name: Battery SoC
      - entity: input_boolean.egopt_explore
        name: Probing higher?
  - type: markdown
    content: "**Why:** {{ states('input_text.egopt_rationale') }}"
  - type: history-graph
    hours_to_show: 48
    entities:
      - input_number.egopt_feed_kw
      - input_number.egopt_eg_budget_kwh
      - sensor.victron_battery_soc
```

You'll see: the live feed setpoint, how much is going to the EG vs reserved for
your house, whether the system is currently *exploring* (probing above known
uptake), and the plain-language reason for the current decision.

## 5. Phase 4: Drive the Victron ESS grid setpoint

Once you trust the numbers, an automation maps the recommendation to Victron's
actual grid setpoint (not advisory anymore — it's live on your inverter).

### Using the HACS integration (recommended)

The integration already creates `sensor.egoptimizer_feed_setpoint`. A Home
Assistant **blueprint** wires it to your Victron and adds safeguards:

1. **Settings → Automation & Scenes → Create Automation → Create from blueprint**
2. Search for **EGOptimizer** (or import [docs/automation-blueprint.yaml](automation-blueprint.yaml) directly)
3. Fill in your **Victron grid setpoint entity** and **max export power cap**
4. Done — the automation runs every 15 min and drives your inverter

**Safety guarantees:**
- Victron's minimum-SoC floor always wins (the brain never commands below it)
- Max export cap clamps to your battery's max discharge
- Mode guard: only feeds when the model is trained ("explore" / "locked")
- Advisory by default: nothing happens until you import and save the blueprint

### Manual automation (without blueprint)

If you prefer YAML, add this to your `configuration.yaml`:

```yaml
automation:
  - alias: EGOptimizer - drive Victron grid setpoint
    trigger:
      - platform: state
        entity_id: sensor.egoptimizer_feed_setpoint
    action:
      - service: number.set_value
        target:
          entity_id: number.victron_ess_grid_setpoint  # <- your entity
        data:
          # Victron grid setpoint is negative watts for exporting
          value: "{{ (states('sensor.egoptimizer_feed_setpoint') | float(0) * -1000) | round(0) }}"
```

(Multiply by -1000: Victron uses negative watts for feeding to grid.)

---

# Visualizing with the HACS integration

When installed via HACS, the integration creates these entities (no helpers
needed):

| Entity | What |
|--------|------|
| `sensor.egoptimizer_feed_setpoint` | **the headline kW to feed right now** |
| `sensor.egoptimizer_status` | feeding / holding / no_budget |
| `sensor.egoptimizer_confidence` | probing / confident / locked / no_model |
| `sensor.egoptimizer_eg_budget_tonight`, `..._planned_tonight` | energy (kWh) |
| `sensor.egoptimizer_forecast_trough_soc`, `..._trough_time`, `..._pv_takeover_time` | autarky |
| `sensor.egoptimizer_reasoning` | plain-language why |
| `number.egoptimizer_target_morning_soc` | your morning-SoC slider |
| `select.egoptimizer_learning_mode` | explore ⇄ locked switch |

The full **feed plan**, **SoC forecast**, and **debug trace** ride as attributes
on `sensor.egoptimizer_feed_setpoint`.

## Dashboard

```yaml
type: vertical-stack
cards:
  - type: gauge
    name: Feed into EG now
    entity: sensor.egoptimizer_feed_setpoint
    min: 0
    max: 5
    needle: true
    severity: { green: 0.01, yellow: 0, red: 4 }
  - type: glance
    entities:
      - { entity: sensor.egoptimizer_status, name: Status }
      - { entity: sensor.egoptimizer_confidence, name: Confidence }
      - { entity: sensor.egoptimizer_eg_budget_tonight, name: Budget }
      - { entity: sensor.egoptimizer_planned_tonight, name: Planned }
      - { entity: sensor.egoptimizer_forecast_trough_soc, name: Trough SoC }
  - type: entities
    entities:
      - number.egoptimizer_target_morning_soc
      - select.egoptimizer_learning_mode
  - type: markdown
    content: "**Why:** {{ state_attr('sensor.egoptimizer_feed_setpoint','debug') }}"
```

## Charts (ApexCharts card — install via HACS Frontend)

**Predicted overnight SoC curve** (with your morning target as a reference):

```yaml
type: custom:apexcharts-card
header: { show: true, title: Forecast battery SoC }
series:
  - entity: sensor.egoptimizer_feed_setpoint
    name: SoC %
    data_generator: |
      return (entity.attributes.soc_forecast || [])
        .map(p => [new Date(p.t).getTime(), p.soc_pct]);
```

**The hour-by-hour feed plan** (probe hours stand out as the taller bars):

```yaml
type: custom:apexcharts-card
chart_type: column
header: { show: true, title: Tonight's feed plan (kWh/h) }
series:
  - entity: sensor.egoptimizer_feed_setpoint
    name: Feed kWh
    data_generator: |
      return (entity.attributes.feed_plan || [])
        .map(p => [new Date(p.time).getTime(), p.feed_kwh]);
```

Because everything is an attribute on one sensor, you can also dump the raw
`debug` object in a Markdown card while tuning -- it shows the budget math, the
trough, and the learned stats for the current context bucket.
```
