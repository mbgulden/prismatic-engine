#!/usr/bin/env python3
"""
eBay Sandbox Test Listing — GRO-705
Creates a test listing in eBay's Sandbox environment using the Inventory API.

Prerequisites (GRO-704: Configure eBay Developer Account):
  - EBAY_APP_ID, EBAY_CERT_ID, EBAY_RUNAME in .env
  - OAuth consent flow completed (GRO-655)
  - ebay_rest SDK installed

Usage:
  python3 ops/ebay_test_listing.py --sandbox
  python3 ops/ebay_test_listing.py --sandbox --sku MELLANOX-001 --price 35.00
"""

import os
import sys
import json
import argparse
from pathlib import Path

# Load .env if present
env_path = Path(__file__).resolve().parent.parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

# --- Config ---
REQUIRED_ENV = ['EBAY_APP_ID', 'EBAY_CERT_ID', 'EBAY_RUNAME']

def check_env():
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        print("   Set these in .env or export them before running.")
        print("   See hardware-flip-protocol skill for eBay Developer setup (GRO-654/GRO-655).")
        sys.exit(1)

def get_ebay_api():
    """Initialize ebay_rest SDK with sandbox credentials."""
    try:
        from ebay_rest import API, Error
    except ImportError:
        print("❌ ebay_rest not installed. Run: pip install ebay_rest")
        sys.exit(1)

    try:
        user_token_path = os.environ.get('EBAY_USER_TOKEN_PATH', '/home/ubuntu/work/sentinel-itad/ebay_user_token.yaml')
        
        # Load user refresh token from YAML file if it exists
        refresh_token = None
        refresh_token_expiry = None
        if os.path.exists(user_token_path):
            try:
                import yaml
                with open(user_token_path, 'r') as tf:
                    token_data = yaml.safe_load(tf)
                    if token_data:
                        refresh_token = token_data.get('refresh_token')
                        refresh_token_expiry = token_data.get('refresh_token_expiry')
            except Exception as e:
                print(f"⚠️  Could not read token file {user_token_path}: {e}")

        user_dict = {
            "email_or_username": "YOUR_EBAY_USERNAME_OR_EMAIL",
            "password": "YOUR_EBAY_PASSWORD",
            "scopes": [
                "https://api.ebay.com/oauth/api_scope",
                "https://api.ebay.com/oauth/api_scope/sell.inventory",
                "https://api.ebay.com/oauth/api_scope/sell.account"
            ]
        }
        if refresh_token:
            user_dict["refresh_token"] = refresh_token
        if refresh_token_expiry:
            user_dict["refresh_token_expiry"] = refresh_token_expiry

        api = API(
            application={
                "app_id": os.environ.get("EBAY_APP_ID"),
                "cert_id": os.environ.get("EBAY_CERT_ID"),
                "dev_id": "",
                "redirect_uri": os.environ.get("EBAY_RUNAME"),
            },
            user=user_dict,
            header={
                "accept_language": "en-US",
                "content_language": "en-US",
                "country": "US",
                "currency": "USD",
                "marketplace_id": "EBAY_US",
            }
        )
        return api
    except Error as e:
        print(f"❌ eBay API init failed: {e}")
        sys.exit(1)

def get_sandbox_policies(api):
    """Fetch or create fulfillment, payment, and return policies for sandbox."""
    return {
        'fulfillment': 'placeholder-fulfillment-policy-id',
        'payment': 'placeholder-payment-policy-id',
        'return': 'placeholder-return-policy-id'
    }

def create_test_offer(api, sku, title, price, description, category_id='175704'):
    """Create a sandbox inventory item and offer.
    
    Args:
        api: ebay_rest API instance
        sku: Unique SKU for the item
        title: Listing title
        price: Buy It Now price (float)
        description: Item description
        category_id: eBay category ID (default: 175704 = Network Interface Cards)
    """
    from ebay_rest import Error

    # Step 1: Create inventory item
    inventory_data = {
        'sku': sku,
        'product': {
            'title': title,
            'description': description,
            'aspects': {
                'Brand': ['Mellanox'],
                'Type': ['Network Interface Card'],
                'Model': ['ConnectX-3'],
                'Data Transfer Rate': ['40 Gbps'],
                'Number of Ports': ['1'],
                'Condition': ['Used']
            },
            'imageUrls': ['https://via.placeholder.com/500']
        },
        'condition': 'USED_GOOD'
    }

    try:
        result = api.sell_inventory_create_or_replace_inventory_item(
            body=inventory_data,
            content_language='en-US',
            content_type='application/json',
            sku=sku
        )
        print(f"  ✅ Inventory item created: {sku}")
    except Error as e:
        print(f"  ❌ Inventory item creation failed: {e}")
        return None

    # Step 2: Create offer
    offer_data = {
        'sku': sku,
        'marketplaceId': 'EBAY_US',
        'format': 'FIXED_PRICE',
        'availableQuantity': 1,
        'categoryId': category_id,
        'listingDescription': description,
        'listingPolicies': {
            'fulfillmentPolicyId': 'placeholder-policy-id',
            'paymentPolicyId': 'placeholder-policy-id',
            'returnPolicyId': 'placeholder-policy-id'
        },
        'pricingSummary': {
            'price': {
                'value': str(price),
                'currency': 'USD'
            }
        },
        'listingDuration': 'GTC'
    }

    try:
        result = api.sell_inventory_create_offer(
            body=offer_data,
            content_language='en-US',
            content_type='application/json'
        )
        offer_id = result.get('offerId', 'unknown') if isinstance(result, dict) else getattr(result, 'offer_id', 'unknown')
        print(f"  ✅ Offer created: {offer_id}")
        return offer_id
    except Error as e:
        print(f"  ❌ Offer creation failed: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description='eBay Sandbox Test Listing')
    parser.add_argument('--sandbox', action='store_true', default=True,
                       help='Use eBay sandbox (default: True)')
    parser.add_argument('--sku', default='TEST-MELLANOX-CX3-001',
                       help='SKU for test item')
    parser.add_argument('--price', type=float, default=35.00,
                       help='Buy It Now price')
    parser.add_argument('--title', default='Mellanox ConnectX-3 40GbE Single Port Network Adapter MCX354A',
                       help='Listing title')
    args = parser.parse_args()

    print("🔍 eBay Sandbox Test Listing — Sentinel ITAD")
    print(f"   SKU: {args.sku}")
    print(f"   Price: ${args.price:.2f}")
    print()

    check_env()

    print("🔗 Connecting to eBay Sandbox API...")
    api = get_ebay_api()
    print("  ✅ Connected")

    description = """Mellanox ConnectX-3 40GbE single-port network adapter.

Pulled from working HP DL380 Gen9 server. Tested and functional.

Specs:
- Model: MCX354A-FCBT (or similar ConnectX-3 variant)
- Speed: 40 Gigabit Ethernet (QSFP+)
- Interface: PCIe 3.0 x8
- Single QSFP+ port
- Full-height bracket included

Perfect for homelab upgrades, high-speed NAS connections, or learning RDMA/RoCE.

Note: Requires QSFP+ cable or transceiver (not included). Ships in anti-static bag with bracket."""

    print("📦 Creating test inventory item + offer...")
    offer_id = create_test_offer(api, args.sku, args.title, args.price, description)

    if offer_id:
        print()
        print("✅ SUCCESS: Sandbox test listing created")
        print(f"   Offer ID: {offer_id}")
        print(f"   SKU: {args.sku}")
        print(f"   Price: ${args.price:.2f}")
        print()
        print("Next: Verify in eBay Sandbox seller hub → sandbox.ebay.com")
        print("Once sandbox works, repeat with production credentials for real listing (GRO-706).")
    else:
        print()
        print("⚠️  Test listing creation failed. Check:")
        print("   1. eBay Developer account configured? (GRO-704)")
        print("   2. OAuth consent flow completed? (GRO-655)")
        print("   3. Sandbox API credentials correct in .env?")
        sys.exit(1)


if __name__ == '__main__':
    main()
