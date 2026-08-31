"""Reclassification with Anish's narrower ai_infra definition.

ai_infra = what developers BUILD ON: compute/GPUs, model hosting and serving,
vector databases, orchestration frameworks, eval / observability / governance,
AI developer tooling, and the model providers themselves.

ai_saas  = AI-native APPLICATIONS sold as a product to end users or businesses.
           The distinction is who the customer is: a developer wiring it into
           something else (infra) vs someone using the thing itself (saas).

Vertical still wins: Abridge is healthcare, not ai_saas, because the vertical
is what decides which of Anish's bullets should lead.
"""
import csv, io
from collections import Counter, defaultdict

DOMAIN = {
    **{c: "ai_infra" for c in [
        # model providers / APIs - a developer building block
        "Anthropic", "OpenAI", "Cohere", "ElevenLabs", "AssemblyAI",
        "Deepgram", "Speechmatics", "Cartesia",
        # compute, hosting, serving
        "Modal", "Baseten", "Together AI", "Fireworks AI", "Unify",
        # vector databases
        "Pinecone", "Weaviate", "Chroma",
        # orchestration frameworks
        "LangChain", "LlamaIndex", "Rasa", "Vapi",
        # eval, observability, governance
        "Arize AI", "Galileo", "Braintrust", "Comet ML",
        # training-data and eval infrastructure
        "Scale AI",
        # frontier research labs - they build the models others host
        "Imbue", "Thinking Machines Lab", "Reflection AI", "World Labs"]},

    **{c: "ai_saas" for c in [
        "Perplexity", "Character AI", "Suno", "Synthesia", "Runway",
        "Luma AI", "Writer", "Harvey", "Eve", "Sierra", "Decagon",
        "Cresta", "Observe AI", "Parloa", "Cursor (Anysphere)", "Replit",
        "Warp"]},

    **{c: "data_infra" for c in [
        "Databricks", "Snowflake", "Fivetran", "ClickHouse", "Confluent",
        "Hex", "Supabase", "Sigma Computing", "Airbyte", "Astronomer",
        "Atlan", "Dremio", "Grafana Labs", "Hightouch", "Honeycomb", "Imply",
        "Materialize", "Monte Carlo", "Neon", "PlanetScale", "Prefect",
        "SingleStore", "StarTree", "Starburst", "Omni Analytics",
        "Amplitude", "Mixpanel", "PostHog", "Datadog", "MongoDB",
        "Cockroach Labs", "Temporal"]},

    **{c: "healthcare" for c in [
        "Abridge", "Ambience Healthcare", "Chai Discovery", "Cohere Health",
        "Cradle Bio", "Isomorphic Labs", "Nabla", "OpenEvidence",
        "Recursion", "Tennr"]},

    **{c: "fintech" for c in [
        "Ramp", "Plaid", "Betterment", "Alloy", "Stripe", "Affirm", "Brex",
        "Chime", "Coinbase", "Column", "Gemini", "Marqeta", "Mercury",
        "Middesk", "Modern Treasury", "Robinhood", "Sardine", "Unit",
        "Persona", "Pilot", "Rogo", "Checkr"]},

    **{c: "robotics_cv" for c in [
        "Figure AI", "Nuro", "Physical Intelligence", "Waymo", "Wayve",
        "Applied Intuition", "Samsara", "Verkada"]},

    **{c: "product_saas" for c in [
        "Notion", "Vercel", "Linear", "Sentry", "Netlify", "Postman",
        "Railway", "Render", "Figma", "Airtable", "Asana", "Miro",
        "Squarespace", "Canva", "Duolingo", "Discord", "Pinterest",
        "Reddit", "Instacart", "Lyft", "DoorDash", "Drata", "Vanta",
        "Justworks", "Gusto"]},
}

rows = list(csv.DictReader(io.open("config/targets.csv", encoding="utf-8-sig")))
missing = [r["company"] for r in rows if r["company"] not in DOMAIN]

by_tier = defaultdict(Counter)
for r in rows:
    by_tier[DOMAIN.get(r["company"], "UNCLASSIFIED")][r["tier"]] += 1
counts = Counter(DOMAIN.get(r["company"], "UNCLASSIFIED") for r in rows)
prio = Counter(DOMAIN[r["company"]] for r in rows if r["tier"] in ("1", "2"))
ptot = sum(prio.values())

print(f"{len(rows)} targets, {len(missing)} unclassified\n")
print(f"{'domain':<14} {'all':>4} {'t1':>3} {'t2':>3} {'t3':>3} | "
      f"{'T1+2':>5} {'share':>6}")
for d, _ in prio.most_common():
    t = by_tier[d]
    print(f"{d:<14} {counts[d]:>4} {t['1']:>3} {t['2']:>3} {t['3']:>3} | "
          f"{prio[d]:>5} {prio[d]/ptot:>5.0%}  " + "#" * round(prio[d]/ptot*40))
for d in counts:
    if d not in prio:
        t = by_tier[d]
        print(f"{d:<14} {counts[d]:>4} {t['1']:>3} {t['2']:>3} {t['3']:>3} | "
              f"{0:>5}    0%")
if missing:
    print("\nUNCLASSIFIED:", missing)
