# AI models: which ones, and what they cost

Owner direction (5 October 2026): pick each provider's model for **cost efficiency**; do not burn
tokens. Every model ID is configuration (`*_MODEL` in `.env`), so a price change is a one-line
edit and `make up`, never a code change. Prices below were read from each provider's own pricing
page on 5 October 2026 and will drift: re-check them before changing anything.

## What is configured

| Choice in the app | `.env` key | Model | Input / output per 1M tokens | Plan |
| --- | --- | --- | --- | --- |
| Standard | `OLLAMA_MODEL` | `llama3.2:3b` on the shared daemon | none (own server) | Free |
| Gemini | `GEMINI_MODEL` | `gemini-3.8-flash` | **free tier**; $0.75 / $3.75 if billing is enabled | Free |
| DeepSeek | `DEEPSEEK_MODEL` | `deepseek-flash` | $0.15 / $0.60 off-peak, double at peak | Free |
| Claude | `CLAUDE_MODEL` | `claude-haiku-4-5` | $1.00 / $5.00 | Pro |
| OpenAI | `OPENAI_MODEL` | `gpt-6-luna` | $0.10 / $0.50 | Pro only (owner decision) |
| (embeddings) | `EMBEDDING_MODEL` | `text-embedding-3-small` | $0.02 | every PDF |

Why these:

- **Gemini — `gemini-3.8-flash`.** The newest model on Google's free tier (the owner asked for the
  latest free one; it replaced `gemini-2.5-flash`). Free-tier request limits are per Google
  project, shared by every user of the app, and only visible in AI Studio — check them there.
  **On the free tier Google may use submitted content to improve its products.** The privacy
  policy says so. Enabling billing on the key ends that and costs about $0.003 an answer.
- **DeepSeek — `deepseek-flash`.** The cheaper of DeepSeek's two current models. The old code
  asked for `deepseek-chat`, a name DeepSeek discontinued on 24 July 2026. DeepSeek charges double
  at peak hours (01:00–04:00 and 06:00–10:00 UTC on weekdays).
- **Claude — `claude-haiku-4-5`.** The least expensive current Claude model. It is still the most
  expensive answer here, which is why it is Pro.
- **OpenAI — `gpt-6-luna`.** The small model of the current generation, about a twelfth of the
  input price of `gpt-5`, which the code used before. (`gpt-5-nano` is cheaper still per token at
  $0.05 / $0.40, if you prefer it.)
- **Embeddings — `text-embedding-3-small`.** A sixth of the price of `-large`. PDFs indexed before
  the change keep working: each stored PDF records the model that indexed it.

## The change that matters most

Each question used to send **100 passages** of the document to the model — about 25,000 input
tokens. It now sends `RAG_TOP_K` (8), about 2,000. Answers are capped at
`CLOUD_MODEL_MAX_OUTPUT_TOKENS` (800), and the instructions sent with every question were cut to a
few lines. Together that is roughly a tenfold cut in tokens per question, on every provider.

Approximate cost of one answer now (about 2,300 tokens in, 400 out):

| Model | Before (25k in) | Now |
| --- | --- | --- |
| Gemini (free tier) | $0 | $0 |
| DeepSeek | ~$0.004 | ~$0.0006 (peak ~$0.0012) |
| OpenAI | ~$0.035 on `gpt-5` | ~$0.0004 on `gpt-6-luna` |
| Claude | — | ~$0.004 |

Raise `RAG_TOP_K` if answers start missing things that are in the document; that is the trade.

## Free allowance: how the numbers were chosen

Per account, per month (`QUOTA_*` free, `PRO_QUOTA_*` Pro; all in `.env`):

| | Free | Pro |
| --- | --- | --- |
| Image scans | 100 | 1,000 |
| Audio transcriptions | 30 | 300 |
| PDF questions (any model) | 50 | 500 |
| Cloud-model answers | **10** | 300 |

- **10 free cloud answers** is enough to try Gemini and DeepSeek properly on two or three
  documents, and costs at most about one US cent per free user per month even if every one goes to
  DeepSeek at peak. The binding limit is not money but Google's shared free-tier request cap: a
  larger free allowance multiplied by many users would exhaust it for everyone.
- **The Standard model does not use the cloud allowance**, so a free user who runs out can keep
  asking (up to 50 PDF questions) at no provider cost beyond embeddings.
- **Audio is the tightest** because transcription is the heaviest work on the shared server.
- **Pro's caps are fair-use limits.** 300 cloud answers all on Claude, the dearest, is about $1.20
  a month — comfortably inside a subscription price in the range the sibling apps charge
  (US$4.99 a month nets about $3.50 after a 30% store cut).

These are starting points. `usage_counters` records real use; revisit after a month of data.

## Not verified

No request was sent to any provider while making these changes (that would spend your credit).
The model IDs and prices come from the providers' documentation; the request shapes are covered by
tests with the SDKs mocked. Before release, send one real question through each model and check
the provider dashboards. Two things to look at in particular: whether `gpt-6-luna` accepts
`max_completion_tokens` as sent, and whether `deepseek-flash` bills hidden reasoning tokens (its
thinking mode has a switch this code does not set, because its documentation could not be read).
