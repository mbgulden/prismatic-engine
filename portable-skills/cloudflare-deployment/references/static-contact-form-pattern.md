# Static Contact Form — formsubmit.co Pattern

## When to Use
- Need a contact form on a static site (no backend, no serverless functions)
- Want emails to go directly to a personal inbox
- Don't want to manage a form backend or database

## Pattern

formsubmit.co handles the form submission pipeline for free (50 submissions/month). The form POSTs to their endpoint, which emails the submission to your configured address.

### Form HTML

```html
<form 
  action="https://formsubmit.co/YOUR_EMAIL@gmail.com" 
  method="POST" 
>
  <!-- Anti-spam: disable CAPTCHA for clean UX -->
  <input type="hidden" name="_captcha" value="false">
  <!-- Custom subject line -->
  <input type="hidden" name="_subject" value="Site Name — New inquiry from website">
  <!-- Clean email template -->
  <input type="hidden" name="_template" value="box">
  <!-- Redirect after submission -->
  <input type="hidden" name="_next" value="https://yoursite.com/contact/thanks">

  <input type="text" name="name" required placeholder="Your Name">
  <input type="email" name="email" required placeholder="your@email.com">
  <input type="text" name="company" placeholder="Company (optional)">
  <textarea name="message" required placeholder="What are you looking to build?"></textarea>
  
  <button type="submit">SEND</button>
</form>
```

### Thank-You Page

Create a static `/contact/thanks/` page for the `_next` redirect. This avoids the default formsubmit.co thank-you page and keeps the user on your domain.

## Key Configuration

| Field | Purpose |
|---|---|
| `_captcha` | Set to `false` — CAPTCHA adds friction. Accept the spam tradeoff for a cleaner UX on a low-traffic consulting site. |
| `_subject` | Custom subject line in the email. Include site name for filtering. |
| `_template` | `box` produces a clean, readable email. Alternatives: `plain`, `table`. |
| `_next` | URL to redirect after successful submission. Must be a full URL. |

## Email Routing Upgrade

For professional domains, pair formsubmit.co with Cloudflare Email Routing:
1. Enable Email Routing in Cloudflare dashboard for the domain
2. Create `contact@yourdomain.com` → forward to personal Gmail
3. Update form action to `https://formsubmit.co/contact@yourdomain.com`

This keeps your personal email hidden while maintaining the simple formsubmit.co pipeline.

## Pitfalls

- **First submission triggers email verification:** formsubmit.co sends a confirmation email to the destination address on first use. Click the link to activate. Subsequent submissions flow through without verification.
- **50 free submissions/month:** For higher volume, upgrade or switch to a serverless function.
- **`_next` must be a full URL:** `https://yoursite.com/contact/thanks` not `/contact/thanks`.
- **CAPTCHA on = spam off but UX degraded:** For a consulting site with low traffic, the spam tradeoff is worth the cleaner experience. If spam becomes an issue, turn CAPTCHA on.
