# eBay Integration Setup Status
## For Phase 2 (after Michael does one-time setup)

## Prerequisites (Michael — ~15 min)
- [ ] 1. Sign up at developer.ebay.com (uses existing eBay account)
- [ ] 2. Create an App → get Sandbox App ID + Cert ID
- [ ] 3. Register a Redirect URI (RuName)
- [ ] 4. Enable scopes: sell.inventory + sell.account
- [ ] 5. Run OAuth consent → click Grant in browser
- [ ] 6. Generate first token (~18 month validity)

## Once setup complete (Fred handles):
- [ ] Install ebay_rest: pip install ebay_rest
- [ ] Add EBAY_APP_ID, EBAY_CERT_ID, EBAY_RUNAME to sentinel-itad/.env
- [ ] Run: python3 ops/ebay_test_listing.py --sandbox
- [ ] Verify sandbox listing appears in eBay sandbox
- [ ] Run: python3 ops/ebay_test_listing.py --sku HP-DL380-001 --price 650.00
- [ ] Verify production listing live

## PirateShip Setup (Michael — ~5 min)
- [ ] 1. Sign up at pirateship.com → Account → API Keys
- [ ] 2. Add key to sentinel-itad/.env as PIRATESHIP_API_KEY
- [ ] 3. Test: python3 ops/ship.py --dry-run --weight 45
