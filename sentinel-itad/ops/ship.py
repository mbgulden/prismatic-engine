#!/usr/bin/env python3
"""
Shipping Label & Porch Pickup Automation — GRO-25 (archived)
Generates USPS labels via PirateShip API, schedules USPS Carrier Pickup.

Prerequisites (one-time setup by Michael):
  - PirateShip account (free): pirateship.com → Account → API Keys
  - USPS Web Tools User ID: business.usps.com → Web Tools (2-5 day approval)
  - Add PIRATESHIP_API_KEY and USPS_USER_ID to sentinel-itad/.env

Usage:
  # Generate label only
  python3 ops/ship.py --weight 45 --to-zip 90210 --to-addr "123 Main St, Los Angeles CA 90210" --desc "Server chassis"

  # Generate label + schedule porch pickup
  python3 ops/ship.py --weight 45 --to-zip 90210 --to-addr "..." --desc "..." --pickup

  # Dry run — show rates without buying
  python3 ops/ship.py --weight 12 --to-zip 96701 --desc "Test" --dry-run

  # Update listings DB with label info
  python3 ops/ship.py --weight 8 --to-zip 96813 --to-addr "..." --desc "Switch" --sku NETGEAR-001
"""

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, date, timedelta

ENV_PATH = Path(__file__).resolve().parent.parent / '.env'
LISTINGS_PATH = Path(__file__).resolve().parent.parent / 'listings.json'

def load_env():
    """Load .env file from project root."""
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

def get_key(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Missing {name} in .env. Set it up first.")
        sys.exit(1)
    return val

def load_listings():
    if LISTINGS_PATH.exists():
        with open(LISTINGS_PATH) as f:
            return json.load(f)
    return {"listings": [], "shipping_labels": []}

def save_listings(data):
    LISTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LISTINGS_PATH, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"  📝 Listings DB updated: {LISTINGS_PATH}")

def fetch_rates(api_key, from_zip, to_zip, weight_lbs, length=24, width=24, height=12):
    """Query PirateShip API for available shipping rates."""
    import urllib.request
    payload = {
        "from_zip": from_zip,
        "to_zip": to_zip,
        "parcel": {
            "weight": weight_lbs,
            "length": length,
            "width": width,
            "height": height
        }
    }
    req = urllib.request.Request(
        "https://api.pirateship.com/v1/rates",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def buy_label(api_key, rate_id, from_addr, to_addr):
    """Purchase a shipping label via PirateShip API."""
    import urllib.request
    payload = {
        "rate_id": rate_id,
        "from_address": from_addr,
        "to_address": to_addr
    }
    req = urllib.request.Request(
        "https://api.pirateship.com/v1/labels",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def schedule_usps_pickup(usps_user_id, from_addr, package_count=1):
    """
    Schedule a USPS Carrier Pickup via USPS Web Tools API.
    USPS pickup is FREE and happens during regular mail delivery.
    Schedule before midnight CT for next-day pickup.
    """
    import urllib.request
    from xml.etree import ElementTree as ET
    
    today = date.today()
    pickup_date = today + timedelta(days=1)  # Next day
    if today.weekday() == 5:  # Saturday → Monday
        pickup_date = today + timedelta(days=2)
    elif today.weekday() == 6:  # Sunday → Monday
        pickup_date = today + timedelta(days=1)
    
    # Build USPS Carrier Pickup XML request
    xml_request = f"""<?xml version="1.0"?>
<CarrierPickupScheduleRequest USERID="{usps_user_id}">
  <FirstName>{from_addr.get('name', '').split()[0]}</FirstName>
  <LastName>{" ".join(from_addr.get('name', '').split()[1:])}</LastName>
  <FirmName/>
  <SuiteOrApt>{from_addr.get('street2', '')}</SuiteOrApt>
  <Address2>{from_addr.get('street1', '')}</Address2>
  <Urbanization/>
  <City>{from_addr.get('city', '')}</City>
  <State>{from_addr.get('state', '')}</State>
  <ZIP5>{from_addr.get('zip', '')[:5]}</ZIP5>
  <ZIP4>{from_addr.get('zip', '')[5:] if len(from_addr.get('zip', '')) > 5 else ''}</ZIP4>
  <Phone>{from_addr.get('phone', '8085551234')}</Phone>
  <Package>
    <ServiceType>PRIORITY</ServiceType>
    <Count>{package_count}</Count>
  </Package>
  <EstimatedWeight>1</EstimatedWeight>
  <PackageLocation>Front Door</PackageLocation>
</CarrierPickupScheduleRequest>"""
    
    url = f"https://secure.shippingapis.com/ShippingAPI.dll?API=CarrierPickupSchedule&XML={urllib.request.quote(xml_request)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode()

def main():
    load_env()
    
    parser = argparse.ArgumentParser(description="Generate shipping labels & schedule porch pickup")
    parser.add_argument('--weight', type=float, required=True, help='Package weight in lbs')
    parser.add_argument('--to-zip', required=True, help='Destination ZIP code')
    parser.add_argument('--to-addr', help='Destination full address (if buying label)')
    parser.add_argument('--to-name', default='Buyer', help='Recipient name')
    parser.add_argument('--desc', default='Hardware', help='Item description')
    parser.add_argument('--sku', help='SKU from listings DB (to track)')
    parser.add_argument('--length', type=int, default=24, help='Package length (inches)')
    parser.add_argument('--width', type=int, default=24, help='Package width (inches)')
    parser.add_argument('--height', type=int, default=12, help='Package height (inches)')
    parser.add_argument('--pickup', action='store_true', help='Schedule USPS porch pickup')
    parser.add_argument('--dry-run', action='store_true', help='Show rates without buying')
    parser.add_argument('--from-zip', default='96701', help='Your ZIP (default: 96701, Aiea HI)')
    
    args = parser.parse_args()
    
    print(f"📦 Shipping Label Generator")
    print(f"  Item: {args.desc}")
    print(f"  Weight: {args.weight} lbs ({args.length}x{args.width}x{args.height})")
    print(f"  From: {args.from_zip} → To: {args.to_zip}")
    
    if args.dry_run:
        print(f"\n🔍 DRY RUN — Fetching rates...")
        api_key = get_key('PIRATESHIP_API_KEY')
        rates = fetch_rates(api_key, args.from_zip, args.to_zip, args.weight,
                            args.length, args.width, args.height)
        print(f"\n  {'Service':<35} {'Rate':>8} {'Est. Days'}")
        print(f"  {'-'*35} {'-'*8} {'-'*9}")
        for r in rates.get('rates', []):
            print(f"  {r.get('service', 'Unknown'):<35} ${r.get('amount', 0):>6.2f}  {r.get('days', '?')}")
        print(f"\n✅ Dry run complete. No label purchased.")
        return
    
    if not args.to_addr:
        print("❌ --to-addr is required to purchase a label")
        sys.exit(1)
    
    # Parse from address (Michael's address)
    from_addr = {
        "name": "Michael Gulden",
        "street1": "98-1100 Moanalua Loop",  # Oahu address placeholder
        "city": "Aiea",
        "state": "HI",
        "zip": args.from_zip,
        "country": "US",
        "phone": "8085551234"  # Update in .env as FROM_PHONE
    }
    
    # Parse destination address
    addr_parts = [p.strip() for p in args.to_addr.split(',')]
    to_name = args.to_name
    to_street1 = addr_parts[0] if len(addr_parts) > 0 else args.to_addr
    to_city_state_zip = addr_parts[1] if len(addr_parts) > 1 else ""
    to_city = to_city_state_zip.split()[0] if to_city_state_zip else ""
    to_state = to_city_state_zip.split()[1] if len(to_city_state_zip.split()) > 1 else ""
    to_zip = args.to_zip
    
    to_addr = {
        "name": to_name,
        "street1": to_street1,
        "city": to_city or "Unknown",
        "state": to_state or "HI",
        "zip": to_zip,
        "country": "US"
    }
    
    print(f"  To: {to_name}, {to_street1}, {to_city}, {to_state} {to_zip}")
    
    # Get rates and pick cheapest
    print(f"\n🔍 Fetching shipping rates...")
    api_key = get_key('PIRATESHIP_API_KEY')
    rates = fetch_rates(api_key, args.from_zip, args.to_zip, args.weight,
                        args.length, args.width, args.height)
    
    if not rates.get('rates'):
        print(f"❌ No rates returned. Check PirateShip API key and parcel dimensions.")
        sys.exit(1)
    
    # Pick cheapest rate
    best = min(rates['rates'], key=lambda r: r.get('amount', 9999))
    print(f"  Best rate: {best.get('service')} — ${best.get('amount', 0):.2f} ({best.get('days', '?')} days)")
    
    # Buy the label
    print(f"\n🏷️  Purchasing label...")
    label = buy_label(api_key, best['id'], from_addr, to_addr)
    
    label_url = label.get('label_url', '')
    tracking = label.get('tracking_number', 'N/A')
    cost = label.get('amount', best.get('amount', 0))
    
    print(f"  ✅ Label purchased!")
    print(f"  Tracking: {tracking}")
    print(f"  Cost: ${cost:.2f}")
    print(f"  Label URL: {label_url}")
    print(f"  Download: {label_url}")
    
    # Save to listings DB
    listings = load_listings()
    label_entry = {
        "date": datetime.now().isoformat(),
        "sku": args.sku or "UNKNOWN",
        "description": args.desc,
        "weight_lbs": args.weight,
        "service": best.get('service'),
        "cost": cost,
        "tracking": tracking,
        "label_url": label_url,
        "from_zip": args.from_zip,
        "to_zip": args.to_zip,
        "pickup_scheduled": args.pickup
    }
    listings.setdefault("shipping_labels", []).append(label_entry)
    save_listings(listings)
    
    # Schedule pickup if requested
    if args.pickup:
        print(f"\n📬 Scheduling USPS porch pickup...")
        usps_user_id = os.environ.get('USPS_USER_ID')
        if not usps_user_id:
            print(f"  ⚠️  USPS_USER_ID not set. Skipping pickup scheduling.")
            print(f"  Manually schedule at: https://tools.usps.com/schedule-pickup.htm")
        else:
            try:
                result = schedule_usps_pickup(usps_user_id, from_addr)
                print(f"  ✅ Pickup scheduled!")
                print(f"  📬 Packages will be picked up from your porch during regular mail delivery")
            except Exception as e:
                print(f"  ❌ Pickup scheduling failed: {e}")
                print(f"  Manually schedule at: https://tools.usps.com/schedule-pickup.htm")
    
    print(f"\n✅ Done. Tracking: {tracking}")

if __name__ == '__main__':
    main()
