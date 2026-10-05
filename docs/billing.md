# ScanGenAI Pro

Freemium (owner decision, 5 October 2026): the app is free within a monthly allowance;
**ScanGenAI Pro** unlocks the paid-only models and raises every allowance. **OpenAI is for
subscribers only, with no free use at all.** Numbers and reasoning: [models.md](models.md).

| | Free | Pro |
| --- | --- | --- |
| Standard model | ✓ | ✓ |
| Gemini, DeepSeek | ✓ (10 answers a month together) | ✓ |
| Claude, OpenAI | – | ✓ |
| Image scans / transcriptions / PDF questions | 100 / 30 / 50 a month | 1,000 / 300 / 500 |

`FREE_CLOUD_MODELS` (default `gemini,deepseek`) names the cloud models a free account may use.
Startup refuses a value that includes `openai`.

| Product ID | Notes |
| --- | --- |
| `scangenai_pro_monthly` | US$4.99, 1 month, auto-renewing |
| `scangenai_pro_yearly` | US$39.99, 1 year, auto-renewing; 7-day free trial for new subscribers |

**Prices** (owner decision, 5 October 2026: "implement the pricing structure you proposed").
They are published on the website and must be entered in both stores exactly as below:

| Plan | Price | Why |
| --- | --- | --- |
| Monthly | US$4.99 | Same as NoAlibi Pro. Nets about $3.49 after a 30% store cut ($4.24 at 15%). |
| Yearly | US$39.99 | A third off twelve months; nets about $28. Offer a 7-day free trial here, as NoAlibi does. |

The most a Pro account can cost in a month is about $1.30: 300 cloud answers all on Claude, the
dearest model ($1.20), plus embeddings. Typical use on Gemini, DeepSeek or OpenAI is a few cents.
So the price is set by what similar apps charge, not by cost, and $4.99 leaves room even for the
heaviest user. One tier only: a second tier would have to be justified by a model that costs
materially more, and none here does.

Both belong to one subscription group, "ScanGenAI Pro". **Prices are yours to set in the stores**;
the app shows whatever the store returns and no price is written in code. The IDs live in
`app/services/billing/plans.py` and the app's `src/constants` (`IAP`);
`tests/test_billing_contract.py` fails if they differ.

## How it is enforced

- The server decides who is on Pro, never the app: an unexpired, unrevoked row in `purchases`
  (verified with the store that sold it) or `users.pro_until` in the future. Reads fail closed.
- `GET /v1/me` tells the app the plan, the models this account may use, the ones that need Pro,
  and each allowance. The app only displays it.
- Asking for a Pro-only model on a free account answers **402** and the app opens the paywall. A
  free allowance that has run out answers 429 with `X-Upgrade-Available: true` and the app offers
  it. A model with no provider key is offered to nobody.
- To give an account Pro without a purchase (support, a reviewer):
  `UPDATE users SET pro_until = now() + interval '30 days' WHERE email = '…';`

## Store verification (ported from NoAlibi, Lost Vowels and Letterbolt)

- **Apple:** the app sends the StoreKit 2 signed transaction. It is trusted only if its
  certificate chain ends at the **pinned** Apple Root CA G3 (`certs/apple-root-ca-g3.pem`, SHA-256
  `63:34:3A:BF…:91:79`) and every signature verifies. A Production server also accepts Sandbox
  (App Review and TestFlight buy in Sandbox).
- **Apple notifications** arrive at `POST /webhooks/apple` (unversioned, no app-version gate):
  renewals extend, `REFUND`/`REVOKE` revoke, `REFUND_REVERSED` reinstates.
- **Google:** the app sends the purchase token; the server reads `subscriptionsv2`. Play pushes
  nothing, so the API process re-reads subscriptions near expiry hourly and sweeps refunds every
  six hours. The app acknowledges a purchase only after the server has recorded it.
- How a subscription row may change (receipts only extend; Google's live answer can shorten;
  after a refund only a newer purchase restores access) is in `app/services/billing/service.py`
  and covered by `tests/test_billing.py`.

## Configuration

`BILLING_PROVIDER` is `off` until the store setup below is done: the paywall says plans are
coming. `dev` accepts fake receipts (`scangenai_pro_yearly:any-id`) for local testing and is
refused in production. `store` needs the Apple and Google values and refuses to start without
them.

## Store setup (owner)

1. **App Store Connect** → the app → Subscriptions: create group "ScanGenAI Pro" with
   `scangenai_pro_monthly` (1 month, US$4.99) and `scangenai_pro_yearly` (1 year, US$39.99) and an
   introductory offer on the yearly one: free, 1 week, new subscribers. Add a review screenshot
   of the paywall to each.
2. App Information: copy the numeric **Apple ID** into `APPLE_APP_ID`. Under App Store Server
   Notifications set the Production and Sandbox URL to `https://<API host>/webhooks/apple`,
   version 2.
3. **Play Console** → Monetize → Subscriptions: create the same two product IDs with a base plan
   each (monthly US$4.99, yearly US$39.99, auto-renewing) and, on the yearly base plan, a
   free-trial offer (7 days, new customers only). Activate them.
4. Google Cloud → create a service account; Play Console → Users and permissions → invite it with
   **View financial data** and **Manage orders and subscriptions**. Put its JSON key, on one line,
   in `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`.
5. Set `BILLING_PROVIDER=store` and `make up`.
6. Mention the auto-renewing subscriptions and link the Terms
   (`https://leonfrontier.com/scangenai/terms`) in the App Store description.

Test with Sandbox (TestFlight) and Play license testers before release.

## Social sign-in setup (owner)

- **Apple (iOS):** enable the *Sign in with Apple* capability for `com.leonfrontier.scangenai` in
  the Apple Developer portal. `app.json` already sets `usesAppleSignIn`. Nothing to configure on
  the server: the token's audience is `IOS_BUNDLE_ID`.
- **Google (Android):** in one Google Cloud project create a **Web** OAuth client and an
  **Android** OAuth client for `com.leonfrontier.scangenai` with the Play app-signing SHA-1 (and
  the EAS preview certificate's, for test builds). Put the *Web* client ID in `.env`
  `GOOGLE_WEB_CLIENT_ID` **and** in the app's `src/constants` `GOOGLE_WEB_CLIENT_ID`; they must
  match. While it is empty the Google button is hidden and the endpoint answers 503.
