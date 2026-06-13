// Cloudflare Pages Function template: Round-robin AI interpretation endpoint
// Copy to functions/api/interpret.js in your Pages project
// Requires [ai] binding in wrangler.toml: binding = "AI"

export async function onRequest(context) {
  const { request, env } = context;
  
  if (request.method === 'OPTIONS') {
    return new Response(null, {
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      }
    });
  }

  if (request.method !== 'POST') {
    return new Response(JSON.stringify({ error: 'POST required' }), { status: 405 });
  }

  try {
    const data = await request.json();
    const prompt = buildPrompt(data);  // Define this for your use case

    // Tier 1: Workers AI (free, 10K/day)
    try {
      const result = await env.AI.run('@cf/meta/llama-3-8b-instruct', {
        messages: [
          { role: 'system', content: 'You are a helpful assistant.' },
          { role: 'user', content: prompt }
        ],
        max_tokens: 500,
        temperature: 0.7,
      });
      if (result?.response) {
        return json({ text: result.response, provider: 'cloudflare-workers-ai' });
      }
    } catch (e) { console.log('Tier 1 failed:', e.message); }

    // Tier 2: External API fallback
    if (env.FALLBACK_API_KEY) {
      try {
        const resp = await fetch(`https://api.example.com/generate?key=${env.FALLBACK_API_KEY}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt })
        });
        const d = await resp.json();
        if (d?.text) return json({ text: d.text, provider: 'fallback-api' });
      } catch (e) { console.log('Tier 2 failed:', e.message); }
    }

    // Tier 3: Template fallback
    return json({ text: templateResponse(data), provider: 'template-fallback' });

  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500 });
  }
}

function json(obj) {
  return new Response(JSON.stringify(obj), {
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
  });
}

// Override these for your use case
function buildPrompt(data) { return JSON.stringify(data); }
function templateResponse(data) { return 'AI interpretation unavailable. Here is a summary of your data...'; }
