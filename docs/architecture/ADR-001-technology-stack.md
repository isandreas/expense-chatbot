# ADR-001: Technology Stack for Expense Chatbot

**Date:** 2026-06-02  
**Status:** Proposed  
**Deciders:** Personal project

---

## Context

Building a personal Telegram bot that uses an LLM to parse, categorize, and store expense messages. Requirements:

- Single user (personal use)
- Minimal operational overhead
- Cost-effective or free

---

## Decision Drivers

- Zero or near-zero monthly cost
- No server management
- Fast response times via webhook
- Easy expense data visibility

---

## Options Considered

### Option A: Fully Free Stack

- **Runtime:** Cloudflare Workers (free 100k req/day)
- **LLM:** Google Gemini 2.0 Flash (free tier: 15 RPM)
- **Storage:** Google Sheets via Sheets API
- **State:** Cloudflare KV

**Pros:** Zero cost, no credit card for LLM, Sheets = visual dashboard  
**Cons:** Sheets API has quirks, Gemini free tier rate-limited

### Option B: Minimum Cost Stack (~$1–5/month)

- **Runtime:** Vercel Serverless (free hobby tier)
- **LLM:** OpenRouter with DeepSeek or Gemini Flash
- **Storage:** Supabase Postgres (free 500MB)
- **State:** Supabase

**Pros:** Real database, pay-per-use LLM, more scalable  
**Cons:** Small monthly cost once LLM usage grows

---

## Decision

**Start with Option A.** Migrate storage to Option B (Supabase) if Sheets becomes limiting.

**LLM Priority:**

1. Gemini 2.0 Flash (free tier, sufficient for personal use)
2. Fallback: DeepSeek via OpenRouter (~$0.001/msg) if free tier exhausted

---

## Consequences

- No monthly bill during normal personal use
- Must handle Gemini rate limit gracefully (queue or retry)
- Google Sheets requires OAuth2 setup (one-time)
- Cloudflare Workers has 1MB bundle size limit (manageable)

---

## Upgrade Path

```
Phase 1 (Free):    Cloudflare Workers + Gemini Free + Google Sheets
Phase 2 (~$3/mo):  Vercel + OpenRouter + Supabase
Phase 3 (~$10/mo): Add analytics, multi-user, receipt OCR
```
