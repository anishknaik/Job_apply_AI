#!/usr/bin/env python3
"""PHASE5_PLAN 3.3 - the three unknowns, verified live rather than read.

  1. Does gemini-2.5-flash resolve on Vertex, and in which region?
  2. Does implicit caching fire on THE PROFILE PREFIX (not synthetic padding)?
  3. Does thinking_config actually disable thinking? 3.2 wants it off, and
     thinking output is a separate, more expensive SKU.

Uses the real master_profile.md as system_instruction, because "does caching
fire" is only a useful answer about the bytes production actually sends.
"""
from __future__ import annotations
import argparse, sys

sys.path.insert(0, ".")

SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string"}, "n": {"type": "integer"}},
    "required": ["verdict", "n"],
}


def usage_of(r):
    u = getattr(r, "usage_metadata", None)
    if u is None:
        return {}
    out = {}
    for k in ("prompt_token_count", "candidates_token_count",
              "cached_content_token_count", "thoughts_token_count",
              "total_token_count"):
        v = getattr(u, k, None)
        if isinstance(v, int):
            out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="gen-lang-client-0077852111")
    ap.add_argument("--credentials", default="config/service_account.json")
    ap.add_argument("--calls", type=int, default=5)
    args = ap.parse_args()

    from google import genai
    from google.genai import types
    from google.oauth2 import service_account
    from jobpipe import scoring

    profile = scoring.load_profile()
    print(f"profile prefix: {len(profile):,} chars (~{len(profile)//4:,} tokens)\n")

    creds = service_account.Credentials.from_service_account_file(
        args.credentials,
        scopes=["https://www.googleapis.com/auth/cloud-platform"])

    print("1. MODEL RESOLUTION")
    resolves = []
    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        for loc in ("global", "us-central1"):
            client = genai.Client(vertexai=True, project=args.project,
                                  location=loc, credentials=creds)
            try:
                client.models.generate_content(
                    model=model, contents="Reply with ok.",
                    config=types.GenerateContentConfig(
                        temperature=0, max_output_tokens=8))
                print(f"   {model:<24} {loc:<12} OK")
                resolves.append((model, loc))
            except Exception as exc:
                code = "404" if "404" in str(exc) else str(exc)[:60]
                print(f"   {model:<24} {loc:<12} FAIL {code}")

    if not any(m == "gemini-2.5-flash" for m, _ in resolves):
        print("\n   gemini-2.5-flash does not resolve. Track A blocked.")
        return 1

    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        if not any(m == model for m, _ in resolves):
            continue
        mloc = next(l for m, l in resolves if m == model)
        client = genai.Client(vertexai=True, project=args.project,
                              location=mloc, credentials=creds)
        print(f"\n2. IMPLICIT CACHING on the profile prefix - {model} @ {mloc}")
        cfg = dict(system_instruction=profile, temperature=0,
                   response_mime_type="application/json",
                   response_schema=SCHEMA)
        try:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            client.models.generate_content(
                model=model, contents="Return verdict=ok and n=0.",
                config=types.GenerateContentConfig(**cfg))
            print("   thinking_budget=0 accepted")
        except Exception as exc:
            cfg.pop("thinking_config", None)
            print(f"   thinking_budget=0 REJECTED ({str(exc)[:70]}) - "
                  f"falling back to default")

        print(f"   {'call':>5}{'prompt':>9}{'cached':>9}{'thoughts':>10}"
              f"{'out':>7}")
        for i in range(args.calls):
            try:
                r = client.models.generate_content(
                    model=model, contents=f"Return verdict=ok and n={i}.",
                    config=types.GenerateContentConfig(**cfg))
                u = usage_of(r)
                print(f"   {i+1:>5}{u.get('prompt_token_count', 0):>9}"
                      f"{u.get('cached_content_token_count', 0):>9}"
                      f"{u.get('thoughts_token_count', 0):>10}"
                      f"{u.get('candidates_token_count', 0):>7}")
            except Exception as exc:
                print(f"   {i+1:>5}  ERROR {str(exc)[:60]}")
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
