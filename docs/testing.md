# Testing EGOptimizer End-to-End

A step-by-step walkthrough to deploy the brain + integration and watch it work.

## Step 1: Start the brain (Docker)

```bash
docker volume create egoptimizer_data
docker run -d --name egoptimizer -p 8787:8787 \
  -v egoptimizer_data:/app/data --restart unless-stopped \
  ghcr.io/bleialf/egoptimizer:latest
```

Check it's running and see the startup logs:

```bash
docker logs egoptimizer
```

You should see:
```
=== EGOptimizer v0.4.0 ===
Data: /app/data/store.sqlite
Model: /app/data/model.json
API listening on http://0.0.0.0:8787
Endpoints: POST /recommend /import /train /purge  GET /health /decisions
```

Test the health endpoint:
```bash
curl http://localhost:8787/health
# → {"status": "ok"}
```

## Step 2: Upload your first CSV export

Get your NetzNÖ "Jahreseinspeisung" CSV export from the grid operator portal.

### Via curl (quick test):

```bash
curl -X POST "http://localhost:8787/import?filename=AT0020000000000000000000100487200-Jahreseinspeisung-2026.csv&train=1" \
     --data-binary @/path/to/export.csv
```

Watch the logs:
```bash
docker logs egoptimizer -f
```

You should see:
```
POST /import: AT002...csv (netznoe)
  imported=12345, dropped=5, total=12345
  training model...
  trained: 12345 records, 156 buckets, 42 uncertain
```

## Step 3: Install the Home Assistant integration

1. **HACS → ⋮ → Custom repositories**
   - Add: `https://github.com/Bleialf/EGOptimizer`
   - Category: **Integration**

2. **Download & restart** HA

3. **Settings → Devices & Services → Add Integration → EGOptimizer**
   - Brain URL: `http://<your-docker-host>:8787`
   - Battery capacity: your kWh
   - SoC entity: `sensor.victron_battery_soc` (or yours)
   - House load entity: `sensor.victron_ac_consumption_smoothed` (smoothed; important!)
   - Solcast forecast: `sensor.solcast_pv_forecast_forecast_today`

4. Click **Configure** on the integration:
   - Upload your CSV again (drag it in)
   - Check Settings (should auto-fill from your inputs)
   - Optionally set retention days

Watch HA logs:
```bash
# In HA UI: Settings → System → Logs → Load full logs
```

## Step 4: Test `/recommend` (brain advisory)

The coordinator automatically calls `/recommend` every 15 min. Check the output entities:

**In HA Developer Tools → States**, search for `egoptimizer_`:

```
sensor.egoptimizer_feed_setpoint: 0.42 kW
  status: feeding
  confidence: probing
  eg_budget_tonight: 3.2 kWh
  planned_tonight: 2.1 kWh
  (... + full feed_plan and soc_forecast as attributes)

number.egoptimizer_target_morning_soc: 50 %
select.egoptimizer_learning_mode: explore
```

Force a recompute by calling the service:
```
Developer Tools → Services → call service
Service: egoptimizer.import_csv
Content: nothing (just to test); or call egoptimizer.train to refit
```

Or trigger manually in a template:
```jinja
{{ states('sensor.egoptimizer_feed_setpoint') }}
```

## Step 5: Build the dashboard (optional, before Phase 4)

Add a dashboard card (paste into a new dashboard):

```yaml
type: vertical-stack
cards:
  - type: gauge
    name: Feed into EG now
    entity: sensor.egoptimizer_feed_setpoint
    min: 0
    max: 5
    needle: true
  - type: glance
    entities:
      - { entity: sensor.egoptimizer_status, name: Status }
      - { entity: sensor.egoptimizer_confidence, name: Confidence }
      - { entity: sensor.egoptimizer_eg_budget_tonight, name: Budget }
  - type: entities
    entities:
      - number.egoptimizer_target_morning_soc
      - select.egoptimizer_learning_mode
```

Verify:
- **Gauge shows live feed recommendation** (updates every 15 min)
- **Sliders change the recommendation** when you adjust target-SoC or aggressiveness
- **Mode affects confidence** (toggle explore ⇄ locked)

## Step 6: Phase 4 — import the automation blueprint

Once you trust the numbers:

1. **Settings → Automation & Scenes → Create Automation → Create from blueprint**
2. Search: **EGOptimizer** (or import the YAML file directly)
3. Pick your **Victron grid setpoint entity** (e.g. `number.victron_ess_grid_setpoint`)
4. Set **max export power** (your battery's max discharge, e.g. 3.5 kW)
5. **Save** — the automation is now live

Watch the inverter:
- Brain recommends feed in HA
- Automation maps it to your Victron grid setpoint
- Victron respects the setpoint while enforcing its own minimum-SoC

## Step 7: Monitor (Docker logs + HA)

Every 15 minutes you should see in `docker logs egoptimizer`:

```
POST /recommend: feed=1.23kW status=feeding
POST /recommend: feed=0.00kW status=holding
```

And in HA:
- Entity state changes
- Dashboard gauge moves
- Automation fires (check automation history in HA)

## Troubleshooting

### "Couldn't reach the brain"
- Is Docker running? `docker ps | grep egoptimizer`
- Is the port open? `curl http://<host>:8787/health`
- Firewall? Check your router/Docker network settings.

### No data imported / "model training failed"
- CSV format wrong? Check the filename — meter ID is parsed from it (e.g. `AT002...csv`)
- Watch logs: `docker logs egoptimizer`
- Try uploading a fresh export from your grid operator.

### "Confidence is no_model"
- You need data. Import at least 1–2 weeks of history so the model can bucket observations.
- After upload + train, confidence should shift to "probing" (initial exploration).

### Feed setpoint not changing
- Did you save the automation blueprint? Check **Settings → Automations** to see if it exists.
- Is the Victron entity correct? Verify the entity name in HA States.
- Check automation logs: **Developer Tools → Events** (listen for `automation_triggered`).

## Next steps

- **Monitor for 1–2 weeks** of learning. The model gets smarter with data.
- **Tune `target_morning_soc`** — slide it down to feed more, up to be safer.
- **Watch the confidence** — once it hits "confident" (not probing), the model has discovered the EG's uptake ceiling.
- **Review the feed plan** (attribute on `sensor.egoptimizer_feed_setpoint`) — hour-by-hour allocation is visible in the dashboard.
