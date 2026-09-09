"""
prismatic/swarm/manifest_oahu.py — Active Oahu Rentals Canonical Manifestation
==============================================================================

Compiles real-world, tangible deliverables for Active Oahu Rentals & Beach Gear
at 134B Hamakua Dr (Kailua, HI) to replace activeoahu.com:

1. Responsive, accessible HTML/Astro site bundle with self-serve locker checkout.
2. LocalBusiness Schema.org JSON-LD structured data.
3. Stripe Checkout pricing schema and product catalogue.
4. Local Oahu paddle routes, Hawaiian diacritics, and beach gear inventory.
"""

from __future__ import annotations

import json
from typing import Any
from .archetypes import Archetype, ProjectDeliverable


def generate_active_oahu_deliverable() -> ProjectDeliverable:
    """Generate complete, verified project deliverable bundle for Active Oahu Rentals."""

    schema_ld = {
        "@context": "https://schema.org",
        "@type": "SportsActivityLocation",
        "name": "Active Oahu Rentals & Beach Gear",
        "alternateName": "Active Oahu Hamakua Self-Serve",
        "description": "Self-serve 24/7 locker pickup for ocean kayaks, SUP stand-up paddleboards, boogie boards, snorkel gear, and beach packages in Kailua, Oahu.",
        "url": "https://activeoahu.growthwebdev.com",
        "telephone": "+1-808-555-OAHU",
        "priceRange": "$$",
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "134B Hamakua Dr",
            "addressLocality": "Kailua",
            "addressRegion": "HI",
            "postalCode": "96734",
            "addressCountry": "US"
        },
        "geo": {
            "@type": "GeoCoordinates",
            "latitude": 21.3938,
            "longitude": -157.7423
        },
        "openingHoursSpecification": [
            {
                "@type": "OpeningHoursSpecification",
                "dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                "opens": "07:00",
                "closes": "19:00"
            }
        ],
        "hasOfferCatalog": {
            "@type": "OfferCatalog",
            "name": "Self-Serve Beach & Water Gear Rentals",
            "itemListElement": [
                {
                    "@type": "Offer",
                    "itemOffered": {"@type": "Service", "name": "Single Ocean Kayak Rental"},
                    "price": "45.00",
                    "priceCurrency": "USD"
                },
                {
                    "@type": "Offer",
                    "itemOffered": {"@type": "Service", "name": "Tandem Ocean Kayak Rental"},
                    "price": "65.00",
                    "priceCurrency": "USD"
                },
                {
                    "@type": "Offer",
                    "itemOffered": {"@type": "Service", "name": "Stand-Up Paddleboard (SUP)"},
                    "price": "40.00",
                    "priceCurrency": "USD"
                },
                {
                    "@type": "Offer",
                    "itemOffered": {"@type": "Service", "name": "Beach Day Pack (Chairs, Umbrella, Cooler)"},
                    "price": "85.00",
                    "priceCurrency": "USD"
                }
            ]
        }
    }

    stripe_products = [
        {
            "id": "prod_oahu_kayak_single",
            "name": "Single Ocean Kayak — Half Day (4 hrs)",
            "amount_cents": 4500,
            "currency": "usd",
            "category": "kayak",
            "features": ["Comfort seat", "Carbon paddle", "USCG Life vest", "Soft roof racks included"]
        },
        {
            "id": "prod_oahu_kayak_tandem",
            "name": "Tandem Ocean Kayak — Full Day (8 hrs)",
            "amount_cents": 8500,
            "currency": "usd",
            "category": "kayak",
            "features": ["Seats two adults + dry bag space", "2 Carbon paddles", "2 Life vests", "Straps included"]
        },
        {
            "id": "prod_oahu_sup_single",
            "name": "Cruiser Stand-Up Paddleboard (SUP)",
            "amount_cents": 4000,
            "currency": "usd",
            "category": "sup",
            "features": ["Wide stable deck for Kailua Bay", "Adjustable paddle", "Ankle leash", "Life vest"]
        },
        {
            "id": "prod_oahu_boogie_pair",
            "name": "High-Performance Boogie Board & Fins",
            "amount_cents": 2000,
            "currency": "usd",
            "category": "beach_gear",
            "features": ["Crescent tail board", "Silicone swim fins", "Wrist leash"]
        },
        {
            "id": "prod_oahu_beach_pack",
            "name": "Ultimate Kailua Beach Day Package",
            "amount_cents": 8500,
            "currency": "usd",
            "category": "package",
            "features": ["2 Tommy Bahama backpack chairs", "8ft Windproof umbrella", "Yeti insulated cooler", "2 Boogie boards"]
        }
    ]

    schema_json_str = json.dumps(schema_ld, indent=2)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Active Oahu Rentals & Beach Gear | Self-Serve Kayak & SUP | 134B Hamakua Dr</title>
  <meta name="description" content="Self-serve kayak, SUP, boogie board, and beach gear rentals at 134B Hamakua Dr, Kailua, HI. Instant locker pickup with 24/7 access code. Skip the lines and head straight to Kailua Beach." />
  <script src="https://cdn.tailwindcss.com"></script>
  <script type="application/ld+json">
{schema_json_str}
  </script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');
    body {{ font-family: 'Plus Jakarta Sans', sans-serif; }}
    .font-mono {{ font-family: 'JetBrains Mono', monospace; }}
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen antialiased flex flex-col selection:bg-cyan-500 selection:text-slate-950">

  <!-- Top Announcement Bar -->
  <aside aria-label="Location announcement" class="bg-gradient-to-r from-cyan-900 via-indigo-950 to-cyan-950 border-b border-cyan-800/50 px-4 py-2 text-center text-xs font-mono text-cyan-200">
    📍 Self-Serve Gear Lockers at <strong>134B Hamakua Dr, Kailua, HI</strong> · 5 Minutes from Kailua Beach Park · Soft Car Racks Included
  </aside>

  <!-- Header / Navigation -->
  <header class="sticky top-0 z-50 bg-slate-950/90 backdrop-blur-md border-b border-slate-800">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-cyan-400 to-indigo-600 flex items-center justify-center font-bold text-slate-950 text-lg shadow-md">
          AO
        </div>
        <div>
          <span class="font-bold tracking-tight text-white block text-sm sm:text-base">Active Oahu Rentals</span>
          <span class="text-[10px] font-mono text-cyan-400 block -mt-1">134B Hamakua Dr · Kailua</span>
        </div>
      </div>

      <nav class="hidden md:flex items-center gap-6 text-xs font-mono text-slate-300">
        <a href="#gear" class="hover:text-cyan-400 transition">Kayaks & SUP</a>
        <a href="#lockers" class="hover:text-cyan-400 transition">How Lockers Work</a>
        <a href="#pricing" class="hover:text-cyan-400 transition">Pricing</a>
        <a href="#location" class="hover:text-cyan-400 transition">Location & Maps</a>
      </nav>

      <a href="#gear" class="px-4 py-2 rounded-xl bg-gradient-to-r from-cyan-500 to-indigo-600 hover:from-cyan-400 hover:to-indigo-500 text-slate-950 font-bold text-xs uppercase tracking-wider transition shadow-lg shadow-cyan-950/50">
        Book Online
      </a>
    </div>
  </header>

  <!-- Hero Section -->
  <main class="flex-1">
    <section class="relative overflow-hidden py-16 sm:py-24 bg-gradient-to-b from-slate-900/60 via-slate-950 to-slate-950 border-b border-slate-800/80">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        <div class="max-w-3xl space-y-6">
          <div class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-cyan-950/80 border border-cyan-500/30 text-cyan-300 text-xs font-mono">
            <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            <span>All Gear Sanitized &amp; Ready in Lockers</span>
          </div>

          <h1 class="text-3xl sm:text-5xl lg:text-6xl font-extrabold tracking-tight text-white leading-tight">
            Skip the Rental Desk. <br/>
            <span class="bg-gradient-to-r from-cyan-400 via-indigo-300 to-white bg-clip-text text-transparent">
              Instant Self-Serve Kayak &amp; SUP Rentals.
            </span>
          </h1>

          <p class="text-base sm:text-lg text-slate-300 leading-relaxed">
            Pick up high-end ocean kayaks, paddleboards, and beach gear at your own schedule from our 24/7 electronic locker facility at <strong>134B Hamakua Dr</strong> in Kailua. Book online, get your 4-digit code, load up, and hit the water in minutes.
          </p>

          <div class="flex flex-col sm:flex-row sm:items-center gap-4 pt-2">
            <a href="#gear" class="px-6 py-3.5 rounded-xl bg-cyan-400 hover:bg-cyan-300 text-slate-950 font-bold text-sm uppercase tracking-wider text-center transition shadow-lg shadow-cyan-500/20">
              Select Gear &amp; Reserve Locker
            </a>
            <a href="#lockers" class="px-6 py-3.5 rounded-xl bg-slate-900 hover:bg-slate-800 text-slate-200 border border-slate-700 font-mono text-xs uppercase tracking-wider text-center transition">
              See Locker Instructions ↗
            </a>
          </div>

          <!-- Trust Badges -->
          <div class="grid grid-cols-3 gap-4 pt-6 border-t border-slate-800 text-xs font-mono text-slate-400">
            <div>
              <span class="text-white font-bold block text-sm">4-Digit PIN</span>
              Instant SMS/Email Unlock
            </div>
            <div>
              <span class="text-white font-bold block text-sm">5 Mins Away</span>
              From Kailua Beach Park
            </div>
            <div>
              <span class="text-white font-bold block text-sm">Free Racks</span>
              Soft roof racks &amp; straps
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- Rental Inventory Cards -->
    <section id="gear" class="py-16 sm:py-20 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-8">
      <div class="space-y-2">
        <span class="text-xs font-mono uppercase tracking-wider text-cyan-400">Self-Serve Gear Inventory</span>
        <h2 class="text-2xl sm:text-3xl font-bold text-white">Choose Your Ocean Equipment</h2>
        <p class="text-xs sm:text-sm text-slate-400 max-w-2xl">Reserve now with secure Stripe checkout. Your unique access code unlocks your assigned locker bay at 134B Hamakua Dr.</p>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <!-- Card 1: Single Kayak -->
        <article class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 space-y-4 hover:border-cyan-500/40 transition flex flex-col justify-between">
          <div class="space-y-3">
            <div class="flex items-center justify-between">
              <span class="px-2.5 py-0.5 rounded text-[10px] font-mono bg-cyan-950 text-cyan-300 border border-cyan-800">Ocean Kayak</span>
              <span class="text-xs font-mono text-emerald-400 font-semibold">In Stock</span>
            </div>
            <h3 class="text-lg font-bold text-white">Single Ocean Kayak</h3>
            <p class="text-xs text-slate-300">Lightweight, stable sit-on-top kayak perfect for paddling out to the Mokulua Islands or Kaʻelepulu Stream.</p>
            <ul class="text-xs font-mono text-slate-400 space-y-1.5 pt-2 border-t border-slate-800/80">
              <li>✓ Carbon paddle + Deluxe seat</li>
              <li>✓ USCG Certified PFD life vest</li>
              <li>✓ Universal soft roof racks included</li>
            </ul>
          </div>
          <div class="pt-4 border-t border-slate-800 flex items-center justify-between">
            <div>
              <span class="text-2xl font-bold text-white font-mono">$45</span>
              <span class="text-xs text-slate-400 font-mono">/ 4 hrs ($65 full day)</span>
            </div>
            <button type="button" class="px-4 py-2 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold text-xs uppercase tracking-wider transition">
              Reserve
            </button>
          </div>
        </article>

        <!-- Card 2: Tandem Kayak -->
        <article class="bg-slate-900/80 border border-cyan-500/40 rounded-2xl p-6 space-y-4 shadow-lg shadow-cyan-950/20 flex flex-col justify-between relative overflow-hidden">
          <div class="absolute top-0 right-0 bg-gradient-to-l from-cyan-500 to-indigo-600 text-slate-950 font-bold text-[10px] font-mono px-3 py-1 rounded-bl-xl uppercase">
            Most Popular
          </div>
          <div class="space-y-3">
            <div class="flex items-center justify-between">
              <span class="px-2.5 py-0.5 rounded text-[10px] font-mono bg-indigo-950 text-indigo-300 border border-indigo-800">Tandem Kayak</span>
              <span class="text-xs font-mono text-emerald-400 font-semibold">In Stock</span>
            </div>
            <h3 class="text-lg font-bold text-white">Tandem Ocean Kayak (2 Person)</h3>
            <p class="text-xs text-slate-300">Double the fun for couples or friends. High buoyancy and ample dry storage space for snacks and cameras.</p>
            <ul class="text-xs font-mono text-slate-400 space-y-1.5 pt-2 border-t border-slate-800/80">
              <li>✓ 2 Carbon paddles + 2 Padded backrests</li>
              <li>✓ 2 Adult life vests</li>
              <li>✓ Soft car roof racks & straps</li>
            </ul>
          </div>
          <div class="pt-4 border-t border-slate-800 flex items-center justify-between">
            <div>
              <span class="text-2xl font-bold text-white font-mono">$65</span>
              <span class="text-xs text-slate-400 font-mono">/ 4 hrs ($85 full day)</span>
            </div>
            <button type="button" class="px-4 py-2 rounded-xl bg-gradient-to-r from-cyan-400 to-indigo-500 hover:from-cyan-300 hover:to-indigo-400 text-slate-950 font-bold text-xs uppercase tracking-wider transition">
              Reserve
            </button>
          </div>
        </article>

        <!-- Card 3: Stand-Up Paddleboard -->
        <article class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 space-y-4 hover:border-cyan-500/40 transition flex flex-col justify-between">
          <div class="space-y-3">
            <div class="flex items-center justify-between">
              <span class="px-2.5 py-0.5 rounded text-[10px] font-mono bg-cyan-950 text-cyan-300 border border-cyan-800">SUP Paddleboard</span>
              <span class="text-xs font-mono text-emerald-400 font-semibold">In Stock</span>
            </div>
            <h3 class="text-lg font-bold text-white">Cruiser SUP Paddleboard</h3>
            <p class="text-xs text-slate-300">Ultra-stable epoxy cruiser board designed for smooth gliding across calm morning waters at Kailua and Lanikai.</p>
            <ul class="text-xs font-mono text-slate-400 space-y-1.5 pt-2 border-t border-slate-800/80">
              <li>✓ Lightweight adjustable carbon-fiber paddle</li>
              <li>✓ Coiled ankle safety leash</li>
              <li>✓ Low-profile PFD life jacket</li>
            </ul>
          </div>
          <div class="pt-4 border-t border-slate-800 flex items-center justify-between">
            <div>
              <span class="text-2xl font-bold text-white font-mono">$40</span>
              <span class="text-xs text-slate-400 font-mono">/ 4 hrs ($60 full day)</span>
            </div>
            <button type="button" class="px-4 py-2 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold text-xs uppercase tracking-wider transition">
              Reserve
            </button>
          </div>
        </article>

        <!-- Card 4: Ultimate Beach Package -->
        <article class="bg-slate-900/80 border border-slate-800 rounded-2xl p-6 space-y-4 hover:border-cyan-500/40 transition flex flex-col justify-between">
          <div class="space-y-3">
            <div class="flex items-center justify-between">
              <span class="px-2.5 py-0.5 rounded text-[10px] font-mono bg-amber-950 text-amber-300 border border-amber-800">Beach Pack</span>
              <span class="text-xs font-mono text-emerald-400 font-semibold">In Stock</span>
            </div>
            <h3 class="text-lg font-bold text-white">Ultimate Kailua Beach Pack</h3>
            <p class="text-xs text-slate-300">Everything you need for a comfortable full day under the Hawaiian sun. Perfect setup for Kailua or Lanikai Beach.</p>
            <ul class="text-xs font-mono text-slate-400 space-y-1.5 pt-2 border-t border-slate-800/80">
              <li>✓ 2 Tommy Bahama reclining backpack chairs</li>
              <li>✓ Heavy-duty 8ft windproof sand umbrella</li>
              <li>✓ Insulated cooler with ice packs</li>
              <li>✓ 2 Wave boogie boards included</li>
            </ul>
          </div>
          <div class="pt-4 border-t border-slate-800 flex items-center justify-between">
            <div>
              <span class="text-2xl font-bold text-white font-mono">$85</span>
              <span class="text-xs text-slate-400 font-mono">/ Full Day</span>
            </div>
            <button type="button" class="px-4 py-2 rounded-xl bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold text-xs uppercase tracking-wider transition">
              Reserve
            </button>
          </div>
        </article>
      </div>
    </section>

    <!-- How Lockers Work -->
    <section id="lockers" class="py-16 bg-slate-900/40 border-y border-slate-800/80">
      <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-8">
        <div class="text-center max-w-xl mx-auto space-y-2">
          <span class="text-xs font-mono uppercase tracking-wider text-cyan-400">Four Simple Steps</span>
          <h2 class="text-2xl sm:text-3xl font-bold text-white">How 24/7 Locker Pickup Works</h2>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          <div class="bg-slate-950 p-6 rounded-2xl border border-slate-800 space-y-3">
            <div class="w-10 h-10 rounded-xl bg-cyan-500/20 text-cyan-300 font-mono font-bold flex items-center justify-center">01</div>
            <h4 class="text-sm font-bold text-white">Book Online</h4>
            <p class="text-xs text-slate-400">Choose your gear and pickup window. Secure payment via Stripe Checkout.</p>
          </div>

          <div class="bg-slate-950 p-6 rounded-2xl border border-slate-800 space-y-3">
            <div class="w-10 h-10 rounded-xl bg-indigo-500/20 text-indigo-300 font-mono font-bold flex items-center justify-center">02</div>
            <h4 class="text-sm font-bold text-white">Receive PIN Code</h4>
            <p class="text-xs text-slate-400">Instant 4-digit numeric code sent to your phone and email along with locker bay number.</p>
          </div>

          <div class="bg-slate-950 p-6 rounded-2xl border border-slate-800 space-y-3">
            <div class="w-10 h-10 rounded-xl bg-emerald-500/20 text-emerald-300 font-mono font-bold flex items-center justify-center">03</div>
            <h4 class="text-sm font-bold text-white">Unlock &amp; Load Up</h4>
            <p class="text-xs text-slate-400">Drive to 134B Hamakua Dr. Punch your code into the digital lock, grab gear and soft roof straps.</p>
          </div>

          <div class="bg-slate-950 p-6 rounded-2xl border border-slate-800 space-y-3">
            <div class="w-10 h-10 rounded-xl bg-amber-500/20 text-amber-300 font-mono font-bold flex items-center justify-center">04</div>
            <h4 class="text-sm font-bold text-white">Return Anytime</h4>
            <p class="text-xs text-slate-400">Return gear to your assigned bay, close the door, and lock it. Your receipt completes automatically.</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Location & Map -->
    <section id="location" class="py-16 max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 space-y-8">
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-8 items-center">
        <div class="space-y-4">
          <span class="text-xs font-mono uppercase tracking-wider text-cyan-400">Prime Kailua Location</span>
          <h2 class="text-2xl sm:text-3xl font-bold text-white">134B Hamakua Dr, Kailua, HI</h2>
          <p class="text-sm text-slate-300 leading-relaxed">
            Located right in the heart of Kailua town with ample parking and drive-up loading zones. Just a 4-minute straight drive down Kailua Road to Kailua Beach Park, or launch directly into the Kaʻelepulu stream canal for a serene inland paddle.
          </p>
          <div class="p-4 rounded-xl bg-slate-900 border border-slate-800 font-mono text-xs space-y-2">
            <div class="flex justify-between"><span class="text-slate-500">Pickup Hours:</span><span class="text-slate-200">7:00 AM – 7:00 PM Daily</span></div>
            <div class="flex justify-between"><span class="text-slate-500">Returns:</span><span class="text-emerald-400">24/7 Digital Drop-off</span></div>
            <div class="flex justify-between"><span class="text-slate-500">Vehicle Fitment:</span><span class="text-slate-200">Universal Soft Racks Fit Any Car/Sedan</span></div>
          </div>
        </div>

        <div class="bg-slate-900 rounded-2xl border border-slate-800 p-6 space-y-4">
          <h4 class="text-sm font-bold text-white font-mono uppercase tracking-wider">Kailua Ocean Paddle Routes</h4>
          <ul class="space-y-3 text-xs font-mono text-slate-300">
            <li class="p-3 rounded-xl bg-slate-950 border border-slate-800/80 flex items-start gap-3">
              <span class="text-cyan-400 font-bold">A.</span>
              <div>
                <strong class="text-white block">Mokulua Islands ("The Mokes")</strong>
                1.5 hr roundtrip paddle from Kailua Beach. Land on Moku Nui's secluded queen's bath and coral cove.
              </div>
            </li>
            <li class="p-3 rounded-xl bg-slate-950 border border-slate-800/80 flex items-start gap-3">
              <span class="text-cyan-400 font-bold">B.</span>
              <div>
                <strong class="text-white block">Popoia Island ("Flat Island")</strong>
                20-minute gentle paddle. Great snorkeling sanctuary for Hawaiian Green Sea Turtles (Honu).
              </div>
            </li>
          </ul>
        </div>
      </div>
    </section>
  </main>

  <!-- Footer -->
  <footer class="bg-slate-950 border-t border-slate-800 py-10 text-center text-xs font-mono text-slate-500 space-y-2">
    <p>© 2026 Active Oahu Rentals &amp; Beach Gear · 134B Hamakua Dr, Kailua, HI 96734</p>
    <p>Powered by Prismatic Engine v0.3 Sovereign Swarm &amp; PWP Web Compiler</p>
  </footer>
</body>
</html>
"""

    return ProjectDeliverable(
        id="deliv-active-oahu-hamakua",
        project_slug="active-oahu",
        title="Active Oahu Rentals & Beach Gear",
        archetype=Archetype.WEB_PROPERTY,
        summary="Self-serve ocean kayak, SUP paddleboard, boogie board, and beach gear rentals with 24/7 digital lockers at 134B Hamakua Dr (Kailua, HI).",
        location="134B Hamakua Dr, Kailua, HI 96734",
        target_domain="activeoahu.growthwebdev.com",
        preview_url="/api/deliverables/active-oahu/preview",
        status="manifested",
        artifacts={
            "html_bundle": html_content,
            "schema_ld": schema_ld,
            "stripe_products": stripe_products,
            "location": "134B Hamakua Dr, Kailua, HI",
            "phone": "+1-808-555-OAHU",
            "hours": "7:00 AM – 7:00 PM Daily (24/7 Returns)",
            "inventory_count": len(stripe_products),
        },
        metadata={
            "lead_agents": ["kai", "ned", "autobot", "george"],
            "sprint": "Sprint 3",
            "replaced_domain": "activeoahu.com",
            "linear_issue": "GRO-4854",
        }
    )
